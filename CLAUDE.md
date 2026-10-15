# CLAUDE.md: rack-delivery-pipeline

## What this repo is

A production-grade analytics pipeline that tracks data center server racks from order intake to decommission. Synthetic event data lands in Amazon S3, is transformed by dbt into a Kimball star schema on Amazon Redshift Serverless, is orchestrated by AWS Step Functions on an Amazon EventBridge Scheduler schedule, and is visualized in Amazon QuickSight.

Read `docs/SPEC.md`, `docs/DATA_MODEL.md`, `docs/SIMULATOR_SPEC.md` and `docs/BUILD_PLAN.md` before writing any code.

## Hard rules

- Work one phase of `docs/BUILD_PLAN.md` at a time. Never start the next phase unprompted.
- A phase is done only when every acceptance criterion passes. Run the verification commands and show their output; do not claim success without it.
- Every dbt model has a `.yml` entry with a description and at least one test. No exceptions.
- Every incremental model must be idempotent: running the same date range twice produces identical row counts and identical `md5` checksums of key columns.
- Never hardcode AWS account IDs, ARNs, endpoints or credentials. Use Terraform outputs, environment variables or SSM Parameter Store.
- Never commit secrets. `.env` is gitignored; `.env.example` is committed.
- Every AWS resource carries tags `project=rack-delivery-pipeline` and `env=<dev|prod>`.
- Do not create MWAA, NAT Gateways, provisioned Redshift clusters, or anything with an idle hourly charge. Ask before creating any resource not listed in `docs/SPEC.md`.
- Never create AWS resources during the local track of BUILD_PLAN. The EventBridge schedule is created in the disabled state; `make schedule-on` and `make schedule-off` toggle it.
- The Redshift Serverless workgroup always has a usage limit with breach action `deactivate` (turn off user queries).
- Do not modify `docs/` files unless explicitly asked. If a spec seems wrong, stop and say so.

## Stack and versions

- Python 3.12, managed with `uv`. Lint with `ruff`, type-check with `mypy --strict` on `simulator/` and `ops/`.
- dbt-core 1.8+ with two targets: `local` (dbt-duckdb, file `./local/warehouse.duckdb`, sources read Parquet from `./out`) and `dev` (dbt-redshift). Packages: `dbt_utils`, `dbt_expectations` (metaplane fork).
- Models must run unchanged on both targets. Use dbt cross-database macros (`dbt.datediff`, `dbt.dateadd`, `dbt.date_trunc`, `dbt.safe_cast`, `dbt.type_*`). Put any unavoidable dialect difference in an adapter-dispatched macro in `macros/`, never inline. Source definitions switch between `read_parquet` (local) and `raw.*` tables (dev) via a macro.
- Incremental models use `merge` on Redshift and `delete+insert` on DuckDB, selected by a macro; the idempotency test must pass on both.
- Terraform 1.8+, AWS provider 5.x. State in an S3 backend with DynamoDB locking (bootstrap script in `infra/bootstrap/`).
- Redshift Serverless, lowest base RPU available in the region.
- GitHub Actions for CI.

## Repository layout

```
simulator/            Python event generator (see docs/SIMULATOR_SPEC.md)
  generator.py
  config.yaml
  tests/
infra/                Terraform
  bootstrap/          state bucket + lock table
  modules/
  envs/dev/
dbt/
  models/
    staging/          stg_* : 1:1 with raw, typed, deduped
    intermediate/     int_* : business logic, not exposed
    marts/            fct_*, dim_* : star schema
    ops/              pipeline_run_audit, quarantine views
  snapshots/          SCD2 snapshots
  macros/
  tests/              singular tests
  seeds/              static reference data (status transitions, SLA targets)
orchestration/        Step Functions ASL definition + Lambda handlers
ops/                  cost report script, backfill CLI, teardown
dashboards/           exported QuickSight dashboard definition JSON
docs/
```

## Naming conventions

- Tables and columns: `snake_case`.
- Timestamps: `<event>_at` in UTC, type `TIMESTAMP`. Dates: `<event>_date`.
- Surrogate keys: `<entity>_sk` built with `dbt_utils.generate_surrogate_key`. Natural keys: `<entity>_id`.
- Booleans: `is_` or `has_` prefix.
- Durations: `<stage>_hours` as `DECIMAL(10,2)`.

## SQL style

- CTEs over subqueries. One CTE per logical step, named for what it produces.
- Explicit column lists; never `SELECT *` outside staging's first CTE.
- Window functions must specify `PARTITION BY` and `ORDER BY` explicitly; frame clauses explicit where the default would be ambiguous.
- Lowercase keywords, trailing commas, 4-space indent. Enforce with `sqlfluff` (dialect `redshift`, templater `dbt`).

## Commands

```
make setup            # uv sync, dbt deps, pre-commit install
make sim DAYS=30      # generate local simulator output
make tf-plan / tf-apply
make upload           # push simulator output to S3 landing
make dbt-local        # dbt build --target local (no AWS)
make dbt-build        # dbt build --target dev
make schedule-on / schedule-off
make evidence         # Phase 9 evidence capture
make backfill START=2025-01-01 END=2025-01-31
make cost             # print cost per refresh from SYS_SERVERLESS_USAGE
make qs-deploy        # deploy QuickSight analysis + dashboard from definition JSON
make qs-snapshot      # export sheet PNGs for review and README
make teardown         # destroy everything except state bucket
make test             # pytest + dbt test + sqlfluff lint
```

## Commit messages

Conventional Commits: `feat(dbt): ...`, `fix(sim): ...`, `chore(infra): ...`. One phase may produce several commits.
