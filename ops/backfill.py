"""Backfill both facts for an ingest-date range and print row counts and checksums.

Rebuilds every rack with an event ingested in [start, end] (dbt vars start_date/end_date
override the incremental lookback). Run it twice: identical output proves the backfill is
idempotent.

    python -m ops.backfill --start 2025-06-01 --end 2025-06-30 --target local
"""

from __future__ import annotations

import argparse
from datetime import date

from ops.dbt_query import query, run_dbt

FACTS = ("fct_rack_status_event", "fct_rack_delivery")

# Key columns only: nothing that depends on the run (run id) or on wall-clock time
# (days_in_current_status, is_overdue_open).
CHECKSUM_COLUMNS = {
    "fct_rack_status_event": [
        "event_sk",
        "vendor_sk",
        "status",
        "prev_status",
        "event_at",
        "hours_since_prev_event",
        "is_regression",
    ],
    "fct_rack_delivery": [
        "rack_sk",
        "vendor_sk",
        "ordered_at",
        "shipped_at",
        "received_at",
        "cabled_at",
        "production_at",
        "end_to_end_hours",
        "current_status",
        "sla_due_at",
        "is_sla_breached",
        "regression_count",
    ],
}


def fact_fingerprint(fact: str, target: str) -> tuple[int, int]:
    """(row count, md5 checksum of the key columns)."""
    key = " || '|' || ".join(
        f"coalesce(cast({c} as {{{{ dbt.type_string() }}}}), '~')" for c in CHECKSUM_COLUMNS[fact]
    )
    sql = f"""
        {{% set key %}}{key}{{% endset %}}
        select
            count(*),
            sum({{{{ md5_bigint(key) }}}})
        from {{{{ ref('{fact}') }}}}
    """
    rows, checksum = query(sql, target)[0]
    return int(rows), int(checksum)


def backfill(start: date, end: date, target: str) -> dict[str, tuple[int, int]]:
    if target == "local":
        run_dbt(
            ["build", "--select", *FACTS, "--quiet"],
            target,
            {"start_date": start.isoformat(), "end_date": end.isoformat()},
        )
    else:
        # On Redshift dbt only runs on CodeBuild, so one identity owns every dbt table.
        from ops.pipeline import dbt

        dbt("daily", start, end)
    return {fact: fact_fingerprint(fact, target) for fact in FACTS}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--target", default="local")
    args = parser.parse_args()
    for fact, (rows, checksum) in backfill(args.start, args.end, args.target).items():
        print(f"{fact:<24} rows={rows:<10,} md5_checksum={checksum}")


if __name__ == "__main__":
    main()
