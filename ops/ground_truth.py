"""Print how well fct_rack_delivery recovers the simulator's true milestones.

A rack matches on a milestone when the fact and the ground truth agree exactly (both null
counts as agreement). The dbt test assert_ground_truth_production_match fails the build below
99% on production_at.

    python -m ops.ground_truth --target local
"""

from __future__ import annotations

import argparse

from ops.dbt_query import query

MILESTONES = ("ordered_at", "shipped_at", "received_at", "cabled_at", "production_at")

SQL = """
select
    count(*) as racks,
    {% for m in milestones %}
    sum(case when f.{{ m }} = t.{{ m }} or (f.{{ m }} is null and t.{{ m }} is null)
        then 1 else 0 end) as {{ m }}_match{{ "," if not loop.last }}
    {% endfor %}
from {{ source('ground_truth', 'racks') }} as t
left join {{ ref('fct_rack_delivery') }} as f
    on t.rack_id = f.rack_id
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default="local")
    args = parser.parse_args()
    sql = "{% set milestones = " + repr(list(MILESTONES)) + " %}" + SQL
    racks, *matches = query(sql, args.target)[0]
    print(f"ground truth racks: {int(racks):,}")
    for milestone, matched in zip(MILESTONES, matches, strict=True):
        print(f"{milestone:<15} match rate {int(matched) / int(racks):.4%} ({int(matched):,})")


if __name__ == "__main__":
    main()
