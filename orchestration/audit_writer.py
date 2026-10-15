"""Last step of every run, success or failure: one row in ops.pipeline_run_audit, plus the
RackPipeline CloudWatch metrics.

Inputs come from the earlier steps' outputs, dbt's run_results.json (uploaded by CodeBuild to
runs/<run_id>/dbt/), and Redshift: quarantined and very late rows for the run's ingest window,
and charged compute seconds from SYS_SERVERLESS_USAGE, priced with the RPU-hour price in SSM.
Writing is idempotent: a rerun replaces the run's row.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

from orchestration.redshift_data import RedshiftData, quote

NAMESPACE = "RackPipeline"
LATE_DAYS = 30


def dbt_summary(s3: Any, bucket: str, run_id: str) -> dict[str, int | None]:
    """Test outcomes and fct_rack_status_event rows from dbt's run_results.json, if present."""
    try:
        body = s3.get_object(Bucket=bucket, Key=f"runs/{run_id}/dbt/run_results.json")["Body"]
    except s3.exceptions.NoSuchKey:
        return {"passed": None, "warned": None, "failed": None, "fct_event_rows": None}
    results = json.loads(body.read())["results"]
    tests = [r for r in results if r["unique_id"].startswith("test.")]
    fct = [r for r in results if r["unique_id"].endswith(".fct_rack_status_event")]
    rows = fct[0].get("adapter_response", {}).get("rows_affected") if fct else None
    return {
        "passed": sum(r["status"] == "pass" for r in tests),
        "warned": sum(r["status"] == "warn" for r in tests),
        "failed": sum(r["status"] in ("fail", "error") for r in tests),
        "fct_event_rows": rows,
    }


def _sql(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, str):
        return quote(value)
    return str(value)


def write_audit(
    s3: Any,
    rd: RedshiftData,
    cloudwatch: Any,
    bucket: str,
    rpu_hour_usd: float,
    event: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    # `state` is the whole Step Functions state: a failed run may stop before land or copy.
    run_id = event["run_id"]
    started_at = datetime.fromisoformat(event["started_at"].replace("Z", "+00:00"))
    state = event.get("state") or {}
    land = state.get("land") or {}
    copied = state.get("copy") or {}
    error = state.get("error") or {}
    date_min, date_max = land.get("ingest_date_min"), land.get("ingest_date_max")
    dbt = dbt_summary(s3, bucket, run_id)

    quarantined = late = None
    if date_min and date_max:
        window = f"_ingest_date between {quote(date_min)} and {quote(date_max)}"
        quarantined = rd.query(
            f"select count(*) as n from ops.quarantine_rack_events where {window}"
        )
        late = rd.query(
            "select count(*) as n from intermediate.int_rack_events_validated "
            f"where {window} and late_by_days > {LATE_DAYS}"
        )
    start, end = started_at.strftime("%Y-%m-%d %H:%M:%S"), now.strftime("%Y-%m-%d %H:%M:%S")
    usage = rd.query(
        "select coalesce(sum(charged_seconds), 0) as charged from sys_serverless_usage "
        f"where start_time >= {quote(start)} and end_time <= {quote(end)}"
    )
    charged = int(usage[0]["charged"])

    row: dict[str, Any] = {
        "run_id": run_id,
        "started_at": start,
        "finished_at": end,
        "status": event["status"],
        "ingest_date_min": date_min,
        "ingest_date_max": date_max,
        "rows_landed": land.get("rows_landed"),
        "rows_loaded_raw": copied.get("rows_loaded_raw"),
        "rows_quarantined": int(quarantined[0]["n"]) if quarantined else None,
        "rows_fct_event_merged": dbt["fct_event_rows"],
        "late_event_count": int(late[0]["n"]) if late else None,
        "dbt_tests_passed": dbt["passed"],
        "dbt_tests_warned": dbt["warned"],
        "dbt_tests_failed": dbt["failed"],
        "redshift_charged_seconds": charged,
        "est_cost_usd": round(charged / 3600 * rpu_hour_usd, 4),
        "error": (f"{error.get('Error', '')}: {error.get('Cause', '')}"[:1000]) if error else None,
    }
    columns = ", ".join(row)
    rd.batch(
        [
            f"delete from ops.pipeline_run_audit where run_id = {quote(run_id)}",
            f"insert into ops.pipeline_run_audit ({columns}) values "
            f"({', '.join(_sql(v) for v in row.values())})",
        ]
    )

    landed = row["rows_landed"] or 0
    metrics = {
        "RowsLoaded": (row["rows_loaded_raw"] or 0, "Count"),
        "RowsQuarantined": (row["rows_quarantined"] or 0, "Count"),
        "QuarantineRate": ((row["rows_quarantined"] or 0) / landed if landed else 0.0, "None"),
        "RunDurationSeconds": ((now - started_at).total_seconds(), "Seconds"),
        "EstCostUsd": (row["est_cost_usd"], "None"),
    }
    cloudwatch.put_metric_data(
        Namespace=NAMESPACE,
        MetricData=[
            {"MetricName": name, "Value": float(value), "Unit": unit, "Timestamp": now}
            for name, (value, unit) in metrics.items()
        ],
    )
    return row


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    import boto3

    price = boto3.client("ssm").get_parameter(Name=os.environ["RPU_PRICE_PARAMETER"])
    return write_audit(
        boto3.client("s3"),
        RedshiftData.from_env(boto3.client("redshift-data")),
        boto3.client("cloudwatch"),
        os.environ["BUCKET"],
        float(price["Parameter"]["Value"]),
        event,
        datetime.now(UTC),
    )
