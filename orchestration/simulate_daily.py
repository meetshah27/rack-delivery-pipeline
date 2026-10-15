"""First step of the daily pipeline: generate the next day of source data, as a real feed would.

Continues the simulator from its state in S3 (sim/_state.parquet) up to the target ingest date
(default: yesterday, UTC) and uploads the day's landing files, the reference files and the
ground truth. Catches up at most MAX_CATCH_UP_DAYS missed days. A rerun for a date already
generated changes nothing: the simulator is deterministic, and the state is uploaded last.
"""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from simulator.config import load_config
from simulator.generator import STATE_FILE, read_state, run_daily

MAX_CATCH_UP_DAYS = 7
STATE_KEY = f"sim/{STATE_FILE}"
GROUND_TRUTH_KEY = "ground_truth/racks.parquet"


def simulate(s3: Any, bucket: str, target: date, workdir: Path) -> dict[str, Any]:
    out = workdir / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / STATE_FILE).write_bytes(s3.get_object(Bucket=bucket, Key=STATE_KEY)["Body"].read())
    _, last_date, _, _ = read_state(out)

    days = [last_date + timedelta(days=n) for n in range(1, (target - last_date).days + 1)]
    if len(days) > MAX_CATCH_UP_DAYS:
        raise RuntimeError(
            f"simulator state ends {last_date}, {len(days)} days behind {target}; "
            "regenerate history locally (make sim) and make upload"
        )
    if not days:
        return {"ingest_date": target.isoformat(), "simulated": [], "ground_truth_key": None}

    cfg = load_config()
    for day in days:
        run_daily(cfg, day, out)

    uploads = [p for p in (out / "reference").glob("*.csv")]
    for day in days:
        partition = f"ingest_date={day.isoformat()}"
        uploads += (out / "landing" / "rack_events" / partition).glob("*.jsonl.gz")
        uploads += (out / "landing" / "vendor_contracts" / partition).glob("*.csv")
    for path in uploads:
        s3.put_object(Bucket=bucket, Key=path.relative_to(out).as_posix(), Body=path.read_bytes())
    s3.put_object(
        Bucket=bucket,
        Key=GROUND_TRUTH_KEY,
        Body=(out / "_ground_truth" / "racks.parquet").read_bytes(),
    )
    s3.put_object(Bucket=bucket, Key=STATE_KEY, Body=(out / STATE_FILE).read_bytes())
    return {
        "ingest_date": target.isoformat(),
        "simulated": [day.isoformat() for day in days],
        "ground_truth_key": GROUND_TRUTH_KEY,
    }


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    import boto3

    if event.get("ingest_date"):
        target = date.fromisoformat(event["ingest_date"])
    else:
        target = datetime.now(UTC).date() - timedelta(days=1)
    with tempfile.TemporaryDirectory() as workdir:
        return simulate(boto3.client("s3"), os.environ["BUCKET"], target, Path(workdir))
