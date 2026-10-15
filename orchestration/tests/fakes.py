"""In-memory stand-ins for S3, the Redshift Data API and CloudWatch.

FakeRedshiftDataClient runs SQL on DuckDB and turns COPY ... FORMAT AS PARQUET [MANIFEST]
into a by-position insert from the Parquet objects in FakeS3, the same way Redshift maps
Parquet columns to a table. Batches run in one transaction, like the Data API.
"""

from __future__ import annotations

import hashlib
import io
import itertools
import json
import re
import tempfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb

SQL_DIR = Path(__file__).resolve().parents[2] / "infra" / "sql"
COPY = re.compile(
    r"copy (\S+) from '(s3://[^']+)' iam_role '[^']+' format as parquet( manifest)?", re.I
)


class NoSuchKey(Exception):
    pass


class FakeS3:
    exceptions = SimpleNamespace(NoSuchKey=NoSuchKey)

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, Bucket: str, Key: str, Body: bytes) -> dict[str, Any]:
        self.objects[Key] = bytes(Body)
        return {}

    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:
        if Key not in self.objects:
            raise NoSuchKey(Key)
        return {"Body": io.BytesIO(self.objects[Key])}

    def list_objects_v2(
        self, Bucket: str, Prefix: str, ContinuationToken: str | None = None
    ) -> dict[str, Any]:
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        start = int(ContinuationToken or 0)
        page = keys[start : start + 1000]
        truncated = start + 1000 < len(keys)
        return {
            "Contents": [
                {"Key": k, "ETag": f'"{hashlib.md5(self.objects[k]).hexdigest()}"'} for k in page
            ],
            "IsTruncated": truncated,
            **({"NextContinuationToken": str(start + 1000)} if truncated else {}),
        }

    def upload_tree(self, root: Path, subdir: str) -> None:
        for path in (root / subdir).rglob("*"):
            if path.is_file():
                self.put_object("bucket", path.relative_to(root).as_posix(), path.read_bytes())


class FakeCloudWatch:
    def __init__(self) -> None:
        self.metrics: list[dict[str, Any]] = []

    def put_metric_data(self, Namespace: str, MetricData: list[dict[str, Any]]) -> None:
        self.metrics += [{**m, "Namespace": Namespace} for m in MetricData]


def _field(value: Any) -> dict[str, Any]:
    if value is None:
        return {"isNull": True}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"longValue": value}
    if isinstance(value, float | Decimal):
        return {"doubleValue": float(value)}
    if isinstance(value, date | datetime):
        return {"stringValue": value.isoformat()}
    return {"stringValue": str(value)}


class FakeRedshiftDataClient:
    def __init__(self, s3: FakeS3) -> None:
        self.s3 = s3
        self.con = duckdb.connect()
        self.tmp = Path(tempfile.mkdtemp())
        self.results: dict[str, dict[str, Any]] = {}
        self.ids = itertools.count()
        for statement in (SQL_DIR / "001_schemas_and_tables.sql").read_text().split(";"):
            if statement.strip():
                self.con.execute(statement)

    def _parquet_paths(self, url: str, manifest: bool) -> list[str]:
        keys = [url.removeprefix("s3://bucket/")]
        if manifest:
            entries = json.loads(self.s3.objects[keys[0]])["entries"]
            keys = [e["url"].removeprefix("s3://bucket/") for e in entries]
        paths = []
        for key in keys:
            path = self.tmp / hashlib.md5(key.encode()).hexdigest()
            path.write_bytes(self.s3.objects[key])
            paths.append(path.as_posix())
        return paths

    def _run(self, sql: str) -> duckdb.DuckDBPyConnection:
        copy = COPY.search(sql)
        if copy:
            paths = self._parquet_paths(copy.group(2), bool(copy.group(3)))
            return self.con.execute(
                f"insert into {copy.group(1)} select * from read_parquet({paths})"
            )
        return self.con.execute(sql.replace("getdate()", "now()::timestamp"))

    def _record(self, run: Any) -> dict[str, str]:
        statement_id = str(next(self.ids))
        try:
            result = run()
            self.results[statement_id] = {"Status": "FINISHED", **result}
        except duckdb.Error as error:
            self.results[statement_id] = {"Status": "FAILED", "Error": str(error)}
        return {"Id": statement_id}

    def execute_statement(self, Sql: str, **_: Any) -> dict[str, str]:
        def run() -> dict[str, Any]:
            cursor = self._run(Sql)
            if cursor.description is None:
                return {"HasResultSet": False}
            return {
                "HasResultSet": True,
                "names": [d[0] for d in cursor.description],
                "rows": cursor.fetchall(),
            }

        return self._record(run)

    def batch_execute_statement(self, Sqls: list[str], **_: Any) -> dict[str, str]:
        def run() -> dict[str, Any]:
            self.con.execute("begin transaction")
            try:
                for sql in Sqls:
                    self._run(sql)
            except duckdb.Error:
                self.con.execute("rollback")
                raise
            self.con.execute("commit")
            return {"HasResultSet": False}

        return self._record(run)

    def describe_statement(self, Id: str) -> dict[str, Any]:
        result = self.results[Id]
        return {
            "Id": Id,
            "Status": result["Status"],
            "Error": result.get("Error", ""),
            "HasResultSet": result.get("HasResultSet", False),
        }

    def get_statement_result(self, Id: str, NextToken: str | None = None) -> dict[str, Any]:
        result = self.results[Id]
        return {
            "ColumnMetadata": [{"name": n} for n in result["names"]],
            "Records": [[_field(v) for v in row] for row in result["rows"]],
        }

    def cancel_statement(self, Id: str) -> None:
        pass
