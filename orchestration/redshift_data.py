"""Redshift Data API: run SQL, wait for it, and read results.

Used by the Lambda handlers (as their IAM role, which Redshift Serverless maps to the database
user IAMR:<role>) and by ops scripts (with the namespace admin secret, for migrations).
"""

from __future__ import annotations

import os
import time
from typing import Any

Row = dict[str, Any]

_DONE = {"FINISHED"}
_FAILED = {"FAILED", "ABORTED"}


class RedshiftDataError(RuntimeError):
    pass


class RedshiftData:
    def __init__(
        self,
        client: Any,
        workgroup: str,
        database: str,
        secret_arn: str | None = None,
        timeout_seconds: float = 600.0,
    ) -> None:
        self.client = client
        self.workgroup = workgroup
        self.database = database
        self.secret_arn = secret_arn
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_env(cls, client: Any) -> RedshiftData:
        return cls(client, os.environ["REDSHIFT_WORKGROUP"], os.environ["REDSHIFT_DATABASE"])

    def _target(self) -> dict[str, str]:
        target = {"WorkgroupName": self.workgroup, "Database": self.database}
        if self.secret_arn:
            target["SecretArn"] = self.secret_arn
        return target

    def _wait(self, statement_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout_seconds
        delay = 0.25
        while True:
            desc: dict[str, Any] = self.client.describe_statement(Id=statement_id)
            if desc["Status"] in _DONE:
                return desc
            if desc["Status"] in _FAILED:
                raise RedshiftDataError(f"{desc['Status']}: {desc.get('Error', '')}")
            if time.monotonic() > deadline:
                self.client.cancel_statement(Id=statement_id)
                raise RedshiftDataError(f"statement {statement_id} timed out")
            time.sleep(delay)
            delay = min(delay * 2, 5.0)

    def execute(self, sql: str) -> dict[str, Any]:
        """Run one statement and wait for it."""
        response = self.client.execute_statement(Sql=sql, **self._target())
        return self._wait(response["Id"])

    def batch(self, statements: list[str]) -> dict[str, Any]:
        """Run statements in order in a single transaction (all or nothing)."""
        response = self.client.batch_execute_statement(Sqls=statements, **self._target())
        return self._wait(response["Id"])

    def query(self, sql: str) -> list[Row]:
        """Rows of a SELECT as dicts keyed by column name."""
        desc = self.execute(sql)
        if not desc.get("HasResultSet"):
            return []
        rows: list[Row] = []
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"Id": desc["Id"]}
            if token:
                kwargs["NextToken"] = token
            page = self.client.get_statement_result(**kwargs)
            names = [column["name"] for column in page["ColumnMetadata"]]
            for record in page["Records"]:
                rows.append(dict(zip(names, (_value(field) for field in record), strict=True)))
            token = page.get("NextToken")
            if not token:
                return rows


def _value(field: dict[str, Any]) -> Any:
    if field.get("isNull"):
        return None
    for key in ("stringValue", "longValue", "doubleValue", "booleanValue"):
        if key in field:
            return field[key]
    raise RedshiftDataError(f"unsupported field {field}")


def quote(text: str) -> str:
    """A SQL string literal (statements in a batch cannot take parameters)."""
    return "'" + text.replace("'", "''") + "'"
