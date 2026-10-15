"""Redshift setup and checks for the dev environment.

    python -m ops.redshift_admin migrate      # users, schemas, raw/ops tables, grants
    python -m ops.redshift_admin verify-raw   # raw.rack_events per date vs landed lines in ./out

migrate runs as the namespace admin (Secrets Manager). It creates the database users the IAM
roles map to before anything connects as them, then applies each infra/sql file once, in
order, recording it in ops.schema_migrations. Run it right after `make tf-apply`.
"""

from __future__ import annotations

import argparse
import gzip
from pathlib import Path
from string import Template

from ops.aws import ROOT, operator_db_user, redshift, tf_outputs
from orchestration.redshift_data import RedshiftData, quote

SQL_DIR = ROOT / "infra" / "sql"


def statements(sql: str) -> list[str]:
    """Split a migration file into statements (the files contain no ';' inside literals)."""
    body = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    return [s.strip() for s in body.split(";") if s.strip()]


def create_users(rd: RedshiftData, users: dict[str, str], quicksight_password: str) -> None:
    existing = {row["usename"] for row in rd.query("select usename from pg_user")}
    for key, name in users.items():
        if name in existing:
            continue
        if key == "quicksight_user":
            rd.execute(f'create user "{name}" password {quote(quicksight_password)}')
        else:
            rd.execute(f'create user "{name}" password disable')
        print(f"created user {name}")


def migrate() -> None:
    out = tf_outputs()
    rd = redshift(admin=True)
    users = {**out["db_users"], "operator_user": operator_db_user()}
    create_users(rd, users, out["quicksight_db_password"])

    rd.execute("create schema if not exists ops")
    rd.execute(
        "create table if not exists ops.schema_migrations "
        "(version varchar(64), applied_at timestamp default getdate())"
    )
    applied = {row["version"] for row in rd.query("select version from ops.schema_migrations")}
    values = {**users, "copy_role_arn": out["copy_role_arn"]}
    for path in sorted(SQL_DIR.glob("*.sql")):
        if path.stem in applied:
            continue
        sql = Template(path.read_text()).substitute(values)
        rd.batch(
            [
                *statements(sql),
                f"insert into ops.schema_migrations (version) values ({quote(path.stem)})",
            ]
        )
        print(f"applied {path.name}")


def verify_raw(out_dir: Path) -> None:
    landed = {
        path.parent.name.removeprefix("ingest_date="): len(
            gzip.decompress(path.read_bytes()).splitlines()
        )
        for path in (out_dir / "landing" / "rack_events").rglob("*.jsonl.gz")
    }
    loaded = {
        row["d"]: int(row["n"])
        for row in redshift().query(
            "select cast(_ingest_date as varchar) as d, count(*) as n "
            "from raw.rack_events group by 1"
        )
    }
    mismatched = {d: (n, loaded.get(d, 0)) for d, n in landed.items() if n != loaded.get(d, 0)}
    print(f"ingest dates: {len(landed)} landed, {len(loaded)} in raw.rack_events")
    print(f"rows: {sum(landed.values()):,} landed, {sum(loaded.values()):,} loaded")
    if mismatched:
        raise SystemExit(
            f"{len(mismatched)} dates differ (landed, loaded): "
            f"{dict(list(mismatched.items())[:10])}"
        )
    print("every ingest date matches")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("command", choices=["migrate", "verify-raw"])
    parser.add_argument("--out", type=Path, default=ROOT / "out")
    args = parser.parse_args()
    if args.command == "migrate":
        migrate()
    else:
        verify_raw(args.out)


if __name__ == "__main__":
    main()
