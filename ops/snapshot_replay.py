"""Replay vendor contract history into the SCD2 snapshot.

A dbt snapshot records the state it sees when it runs. The daily pipeline runs it once a day,
so history accumulates naturally; a backfill loads 24 months of contract files at once and
would capture only the latest state. This replays the snapshot once per date on which any
vendor's SLA or tier changed (other dates would be no-ops), oldest first.

    python -m ops.snapshot_replay --target local
"""

from __future__ import annotations

import argparse

from ops.dbt_query import query, run_dbt

CHANGE_DATES_SQL = """
with contracts as (
    select
        vendor_code,
        _ingest_date,
        sla_days_order_to_production as sla,
        contract_tier as tier,
        lag(sla_days_order_to_production) over (
            partition by vendor_code order by _ingest_date
        ) as prev_sla,
        lag(contract_tier) over (partition by vendor_code order by _ingest_date) as prev_tier
    from {{ ref('stg_vendor_contracts') }}
)
select distinct _ingest_date
from contracts
where prev_sla is null or sla <> prev_sla or tier <> prev_tier
order by _ingest_date
"""


def replay(target: str) -> list[str]:
    dates = [str(row[0]) for row in query(CHANGE_DATES_SQL, target)]
    for day in dates:
        print(f"snapshot as of {day}")
        run_dbt(["snapshot", "--quiet"], target, {"contracts_as_of": day})
    return dates


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default="local")
    args = parser.parse_args()
    dates = replay(args.target)
    print(f"replayed {len(dates)} contract change dates")


if __name__ == "__main__":
    main()
