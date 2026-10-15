"""Run pieces of the deployed pipeline from the operator's machine.

    python -m ops.pipeline load-raw                 # land_to_raw + copy_to_redshift Lambdas
    python -m ops.pipeline dbt --mode bootstrap     # first cloud build: SCD2 replay, then build
    python -m ops.pipeline dbt --start 2025-06-01 --end 2025-06-30   # backfill a window
    python -m ops.pipeline run                      # one state machine execution, waited on
    python -m ops.pipeline schedule on|off          # the daily EventBridge schedule

dbt always runs on CodeBuild, so a single identity owns every dbt table.
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from datetime import date
from typing import Any

from ops.aws import session, tf_outputs


def _invoke(function: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = (
        session()
        .client("lambda")
        .invoke(FunctionName=function, Payload=json.dumps(payload).encode())
    )
    result: dict[str, Any] = json.loads(response["Payload"].read())
    if response.get("FunctionError"):
        raise SystemExit(f"{function} failed: {json.dumps(result, indent=2)}")
    return result


def load_raw() -> None:
    functions = tf_outputs()["function_names"]
    run_id = f"load-{uuid.uuid4().hex[:8]}"
    landed = _invoke(functions["land_to_raw"], {"run_id": run_id})
    print(f"land_to_raw: {landed['files']} files, {landed['rows_landed']:,} event rows")
    copied = _invoke(
        functions["copy_to_redshift"],
        {
            "run_id": run_id,
            "files_key": landed["files_key"],
            "ground_truth_key": "ground_truth/racks.parquet",
        },
    )
    print(f"copy_to_redshift: {copied['rows_loaded_raw']:,} rows into raw.rack_events")


def dbt(mode: str, start: date | None, end: date | None) -> None:
    codebuild = session().client("codebuild")
    env = {"MODE": mode, "RUN_ID": f"manual-{uuid.uuid4().hex[:8]}"}
    if start and end:
        env |= {"START_DATE": start.isoformat(), "END_DATE": end.isoformat()}
    build = codebuild.start_build(
        projectName=tf_outputs()["codebuild_project"],
        environmentVariablesOverride=[
            {"name": k, "value": v, "type": "PLAINTEXT"} for k, v in env.items()
        ],
    )["build"]
    print(f"CodeBuild {build['id']} ({mode}); logs: {build.get('logs', {}).get('deepLink', '')}")
    while True:
        build = codebuild.batch_get_builds(ids=[build["id"]])["builds"][0]
        if build["buildStatus"] != "IN_PROGRESS":
            break
        time.sleep(15)
    if build["buildStatus"] != "SUCCEEDED":
        raise SystemExit(f"dbt build {build['buildStatus']}")
    print("dbt build SUCCEEDED")


def run() -> None:
    sfn = session().client("stepfunctions")
    execution = sfn.start_execution(stateMachineArn=tf_outputs()["state_machine_arn"])
    print(f"execution {execution['executionArn']}")
    while True:
        status = sfn.describe_execution(executionArn=execution["executionArn"])["status"]
        if status != "RUNNING":
            break
        time.sleep(15)
    print(f"execution {status}")
    if status != "SUCCEEDED":
        raise SystemExit(1)


def schedule(state: str) -> None:
    scheduler = session().client("scheduler")
    name = tf_outputs()["schedule_name"]
    current = scheduler.get_schedule(Name=name)
    keep = ("ScheduleExpression", "ScheduleExpressionTimezone", "FlexibleTimeWindow", "Target")
    scheduler.update_schedule(Name=name, State=state, **{k: current[k] for k in keep})
    print(f"schedule {name}: {state}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("load-raw")
    dbt_parser = sub.add_parser("dbt")
    dbt_parser.add_argument("--mode", choices=["daily", "bootstrap"], default="daily")
    dbt_parser.add_argument("--start", type=date.fromisoformat)
    dbt_parser.add_argument("--end", type=date.fromisoformat)
    sub.add_parser("run")
    schedule_parser = sub.add_parser("schedule")
    schedule_parser.add_argument("state", choices=["on", "off"])
    args = parser.parse_args()

    if args.command == "load-raw":
        load_raw()
    elif args.command == "dbt":
        dbt(args.mode, args.start, args.end)
    elif args.command == "run":
        run()
    else:
        schedule("ENABLED" if args.state == "on" else "DISABLED")


if __name__ == "__main__":
    main()
