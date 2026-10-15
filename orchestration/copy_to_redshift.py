"""Load the Parquet that land_to_raw wrote into raw.* with COPY, and record the file manifest.

For each raw table, a COPY manifest lists exactly the files converted in this run, so loading
the same ingest date again copies nothing new. Each table's COPY and its ops.file_manifest rows
run in one transaction: a file is marked loaded only if its rows are in. Reference tables
(sites, rack_types) are full snapshots and are replaced; the event and contract tables are
append-only.

The simulator's ground truth (dev only, for the ground-truth dbt test) is reloaded when the
event names one.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from typing import Any

from orchestration.redshift_data import RedshiftData, quote

REPLACED_TABLES = {"sites", "rack_types"}


def copy_statements(
    bucket: str, table: str, files: list[dict[str, Any]], manifest_key: str, role_arn: str
) -> list[str]:
    statements = [f"delete from raw.{table}"] if table in REPLACED_TABLES else []
    statements.append(
        f"copy raw.{table} from {quote(f's3://{bucket}/{manifest_key}')} "
        f"iam_role {quote(role_arn)} format as parquet manifest"
    )
    values = ",\n".join(
        "({}, {}, {}, {}, {}, getdate())".format(
            quote(f["source_file"]),
            quote(f["md5"]),
            int(f["row_count"]),
            quote(f["ingest_date"]) if f["ingest_date"] else "null",
            quote(f["run_id"]),
        )
        for f in files
    )
    statements.append(
        "insert into ops.file_manifest "
        "(source_file, md5, row_count, _ingest_date, run_id, loaded_at) values\n" + values
    )
    return statements


def copy(
    s3: Any,
    rd: RedshiftData,
    bucket: str,
    role_arn: str,
    run_id: str,
    files_key: str,
    ground_truth_key: str | None,
) -> dict[str, Any]:
    files: list[dict[str, Any]] = json.loads(
        s3.get_object(Bucket=bucket, Key=files_key)["Body"].read()
    )
    by_table: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for f in files:
        by_table[f["table"]].append({**f, "run_id": run_id})

    for table, table_files in sorted(by_table.items()):
        manifest = {
            "entries": [
                {
                    "url": f"s3://{bucket}/{f['raw_key']}",
                    "mandatory": True,
                    "meta": {"content_length": f["content_length"]},
                }
                for f in table_files
            ]
        }
        manifest_key = f"runs/{run_id}/copy_{table}.manifest"
        s3.put_object(Bucket=bucket, Key=manifest_key, Body=json.dumps(manifest).encode())
        rd.batch(copy_statements(bucket, table, table_files, manifest_key, role_arn))

    if ground_truth_key:
        rd.batch(
            [
                "delete from ground_truth.racks",
                f"copy ground_truth.racks from {quote(f's3://{bucket}/{ground_truth_key}')} "
                f"iam_role {quote(role_arn)} format as parquet",
            ]
        )

    loaded = rd.query(f"select count(*) as n from raw.rack_events where _run_id = {quote(run_id)}")
    return {"run_id": run_id, "tables": sorted(by_table), "rows_loaded_raw": int(loaded[0]["n"])}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    import boto3

    rd = RedshiftData.from_env(boto3.client("redshift-data"))
    return copy(
        boto3.client("s3"),
        rd,
        os.environ["BUCKET"],
        os.environ["COPY_ROLE_ARN"],
        event["run_id"],
        event["files_key"],
        event.get("ground_truth_key"),
    )
