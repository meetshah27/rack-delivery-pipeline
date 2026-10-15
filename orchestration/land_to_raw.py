"""Landing -> raw conversion: JSON Lines / CSV files into typed Parquet with load metadata.

`rack_events_to_table` is the conversion the Phase 3 `land_to_raw` Lambda wraps. The CLI is the
local-track stand-in for that Lambda: it converts every landed file under ./out/landing into
./out/raw, which the dbt `local` target reads with read_parquet.

    python -m orchestration.land_to_raw --out ./out
"""

from __future__ import annotations

import argparse
import io
import shutil
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.csv as pcsv
import pyarrow.json as pjson
import pyarrow.parquet as pq

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


def _partition_date(path: Path) -> date:
    return date.fromisoformat(path.parent.name.removeprefix("ingest_date="))


def convert_local(out: Path) -> dict[str, int]:
    """Convert every landed file under out/landing into out/raw. Returns rows per raw table."""
    landing, raw = out / "landing", out / "raw"
    shutil.rmtree(raw, ignore_errors=True)  # raw is fully derived from landing; no stale days
    counts = {"rack_events": 0, "vendor_contracts": 0, "sites": 0, "rack_types": 0}

    for src in sorted((landing / "rack_events").glob("ingest_date=*/*.jsonl.gz")):
        day = _partition_date(src)
        loaded_at = datetime.combine(day + timedelta(days=1), LOCAL_LOAD_TIME, UTC)
        table = rack_events_to_table(
            src.read_bytes(), day, LOCAL_RUN_ID, loaded_at, src.relative_to(out).as_posix()
        )
        _write(table, raw / "rack_events" / src.parent.name / "part-0000.parquet")
        counts["rack_events"] += table.num_rows

    for src in sorted((landing / "vendor_contracts").glob("ingest_date=*/*.csv")):
        table = csv_to_table(src.read_bytes(), _partition_date(src))
        _write(table, raw / "vendor_contracts" / src.parent.name / "part-0000.parquet")
        counts["vendor_contracts"] += table.num_rows

    for name in ("sites", "rack_types"):
        table = csv_to_table((out / "reference" / f"{name}.csv").read_bytes(), None)
        _write(table, raw / name / "part-0000.parquet")
        counts[name] = table.num_rows
    return counts


def _write(table: pa.Table, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)


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
