"""Landing -> raw conversion: JSON Lines / CSV files into typed Parquet with load metadata.

Lambda (`handler`): converts every landed file in S3 that ops.file_manifest has not recorded
(by key and md5) and writes the Parquet to the raw/ prefix. It does not record the manifest:
copy_to_redshift does that in the same transaction as the COPY, so a file is only ever marked
loaded once its rows are in raw.*.

CLI: the local-track stand-in. Converts everything under ./out into ./out/raw, which the dbt
`local` target reads with read_parquet.

    python -m orchestration.land_to_raw --out ./out
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.csv as pcsv
import pyarrow.json as pjson
import pyarrow.parquet as pq

from orchestration.redshift_data import RedshiftData

# Payload v2 (schema drift) renames rack_type_code to rack_model; raw keeps both columns and
# staging maps them back, so drift never breaks the load.
RACK_EVENT_FIELDS = pa.schema(
    [
        ("event_id", pa.string()),
        ("rack_id", pa.string()),
        ("site_code", pa.string()),
        ("vendor_code", pa.string()),
        ("rack_type_code", pa.string()),
        ("rack_model", pa.string()),
        ("status", pa.string()),
        ("event_at", pa.string()),
        ("recorded_by", pa.string()),
        ("payload_version", pa.int16()),
    ]
)
LOAD_FIELDS = [
    ("_ingest_date", pa.date32()),
    ("_run_id", pa.string()),
    ("_loaded_at", pa.timestamp("us")),
    ("_source_file", pa.string()),
]
RAW_RACK_EVENTS = pa.schema(list(RACK_EVENT_FIELDS) + [pa.field(n, t) for n, t in LOAD_FIELDS])

LOCAL_RUN_ID = "local-backfill"
# Local backfill pretends each ingest date was loaded by the next morning's 06:00 UTC run, as
# the daily schedule does. Keeps raw reproducible, preserves load order for staging's
# latest-_loaded_at dedupe, and gives the future-dated check a realistic load time.
LOCAL_LOAD_TIME = time(6, 0)


def _with_load_metadata(
    table: pa.Table, ingest_date: date, run_id: str, loaded_at: datetime, source_file: str
) -> pa.Table:
    n = table.num_rows
    values = {
        "_ingest_date": pa.array([ingest_date] * n, pa.date32()),
        "_run_id": pa.array([run_id] * n, pa.string()),
        "_loaded_at": pa.array([loaded_at.replace(tzinfo=None)] * n, pa.timestamp("us")),
        "_source_file": pa.array([source_file] * n, pa.string()),
    }
    for name, _ in LOAD_FIELDS:
        table = table.append_column(name, values[name])
    return table


def rack_events_to_table(
    jsonl_gz: bytes, ingest_date: date, run_id: str, loaded_at: datetime, source_file: str
) -> pa.Table:
    """Parse one landed rack_events file into the raw.rack_events schema."""
    if not jsonl_gz:
        events = RACK_EVENT_FIELDS.empty_table()
    else:
        events = pjson.read_json(
            pa.input_stream(pa.BufferReader(jsonl_gz), compression="gzip"),
            parse_options=pjson.ParseOptions(
                explicit_schema=RACK_EVENT_FIELDS, unexpected_field_behavior="error"
            ),
        )
    return _with_load_metadata(events, ingest_date, run_id, loaded_at, source_file).cast(
        RAW_RACK_EVENTS
    )


def csv_to_table(data: bytes, ingest_date: date | None) -> pa.Table:
    """Reference and contract CSVs, read with every column as a string (staging types them)."""
    header = data.split(b"\n", 1)[0].decode().split(",")
    table = pcsv.read_csv(
        io.BytesIO(data),
        convert_options=pcsv.ConvertOptions(column_types={c: pa.string() for c in header}),
    )
    if ingest_date is not None:
        table = table.append_column(
            "_ingest_date", pa.array([ingest_date] * table.num_rows, pa.date32())
        )
    return table


@dataclass(frozen=True)
class SourceFile:
    """A landed file and where its Parquet goes. Keys are relative to the bucket (or ./out)."""

    key: str
    table: str
    ingest_date: date | None
    raw_key: str


def classify(key: str) -> SourceFile | None:
    """The raw table a landed file feeds, or None for anything that is not a source file."""
    parts = key.split("/")
    if (
        len(parts) == 4
        and parts[0] == "landing"
        and parts[1] in ("rack_events", "vendor_contracts")
        and parts[2].startswith("ingest_date=")
    ):
        day = date.fromisoformat(parts[2].removeprefix("ingest_date="))
        return SourceFile(key, parts[1], day, f"raw/{parts[1]}/{parts[2]}/part-0000.parquet")
    if len(parts) == 2 and parts[0] == "reference" and parts[1] in ("sites.csv", "rack_types.csv"):
        table = parts[1].removesuffix(".csv")
        return SourceFile(key, table, None, f"raw/{table}/part-0000.parquet")
    return None


def convert(src: SourceFile, data: bytes, run_id: str, loaded_at: datetime) -> pa.Table:
    if src.table == "rack_events":
        assert src.ingest_date is not None
        return rack_events_to_table(data, src.ingest_date, run_id, loaded_at, src.key)
    return csv_to_table(data, src.ingest_date)


def parquet_bytes(table: pa.Table) -> bytes:
    buffer = io.BytesIO()
    pq.write_table(table, buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------- Lambda


def list_objects(s3: Any, bucket: str, prefix: str) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
    while True:
        page = s3.list_objects_v2(**kwargs)
        objects += page.get("Contents", [])
        if not page.get("IsTruncated"):
            return objects
        kwargs["ContinuationToken"] = page["NextContinuationToken"]


def land(s3: Any, rd: RedshiftData, bucket: str, run_id: str, now: datetime) -> dict[str, Any]:
    """Convert pending landed files to raw Parquet; list them in runs/<run_id>/land_to_raw.json."""
    loaded = {
        (row["source_file"], row["md5"])
        for row in rd.query("select source_file, md5 from ops.file_manifest")
    }
    files: list[dict[str, Any]] = []
    for prefix in ("landing/", "reference/"):
        for obj in list_objects(s3, bucket, prefix):
            src = classify(obj["Key"])
            if src is None:
                continue
            md5 = obj["ETag"].strip('"')
            data: bytes | None = None
            if "-" in md5:  # multipart upload: the ETag is not the md5
                data = s3.get_object(Bucket=bucket, Key=src.key)["Body"].read()
                md5 = hashlib.md5(data).hexdigest()
            if (src.key, md5) in loaded:
                continue
            if data is None:
                data = s3.get_object(Bucket=bucket, Key=src.key)["Body"].read()
            table = convert(src, data, run_id, now)
            body = parquet_bytes(table)
            s3.put_object(Bucket=bucket, Key=src.raw_key, Body=body)
            files.append(
                {
                    "table": src.table,
                    "source_file": src.key,
                    "md5": md5,
                    "raw_key": src.raw_key,
                    "content_length": len(body),
                    "row_count": table.num_rows,
                    "ingest_date": src.ingest_date.isoformat() if src.ingest_date else None,
                }
            )

    # The file list goes to S3, not the Step Functions payload (256 KB limit on a backfill).
    files_key = f"runs/{run_id}/land_to_raw.json"
    s3.put_object(Bucket=bucket, Key=files_key, Body=json.dumps(files).encode())
    event_files = [f for f in files if f["table"] == "rack_events"]
    dates = sorted(f["ingest_date"] for f in event_files)
    return {
        "run_id": run_id,
        "files_key": files_key,
        "files": len(files),
        "rows_landed": sum(f["row_count"] for f in event_files),
        "ingest_date_min": dates[0] if dates else None,
        "ingest_date_max": dates[-1] if dates else None,
    }


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    import boto3

    rd = RedshiftData.from_env(boto3.client("redshift-data"))
    return land(boto3.client("s3"), rd, os.environ["BUCKET"], event["run_id"], datetime.now(UTC))


# ---------------------------------------------------------------- local track


def convert_local(out: Path) -> dict[str, int]:
    """Convert every landed file under out/ into out/raw. Returns rows per raw table."""
    shutil.rmtree(out / "raw", ignore_errors=True)  # raw is fully derived from landing
    counts = {"rack_events": 0, "vendor_contracts": 0, "sites": 0, "rack_types": 0}
    for path in sorted([*(out / "landing").rglob("*"), *(out / "reference").glob("*")]):
        src = classify(path.relative_to(out).as_posix())
        if src is None:
            continue
        loaded_at = datetime.combine(
            (src.ingest_date or date.min) + timedelta(days=1), LOCAL_LOAD_TIME, UTC
        )
        table = convert(src, path.read_bytes(), LOCAL_RUN_ID, loaded_at)
        target = out / src.raw_key
        target.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, target)
        counts[src.table] += table.num_rows
    return counts


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--out", type=Path, default=Path("out"))
    args = parser.parse_args(argv)
    started = datetime.now(UTC)
    counts = convert_local(args.out)
    elapsed = (datetime.now(UTC) - started) / timedelta(seconds=1)
    print(", ".join(f"raw.{k}: {v:,}" for k, v in counts.items()) + f" ({elapsed:.1f}s)")


if __name__ == "__main__":
    main()
