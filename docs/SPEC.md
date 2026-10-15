# SPEC: Rack Delivery Pipeline

## 1. Business question

How long does it take to get a server rack from order to production, where in the lifecycle does it stall, and which vendors and sites miss their delivery SLAs?

The dashboard must answer, in under 30 seconds of looking:

1. What percentage of racks reached production within SLA this month, and how does that compare to last month?
2. Which lifecycle stage contributes most to p90 cycle time?
3. Which vendor-site combinations are breaching SLA, and which specific racks are currently overdue?
4. Is the data trustworthy today (pipeline ran, freshness within threshold, quarantine rate normal)?

## 2. Domain: rack lifecycle

Ordered statuses. A rack moves forward through these; the simulator occasionally produces regressions (see SIMULATOR_SPEC).

| Seq | Status | Meaning |
|---|---|---|
| 1 | `ORDERED` | Purchase order placed with vendor |
| 2 | `VENDOR_CONFIRMED` | Vendor accepted the order and committed a ship date |
| 3 | `SHIPPED` | Left vendor facility |
| 4 | `RECEIVED` | Arrived at data center loading dock |
| 5 | `RACKED` | Physically installed in position |
| 6 | `CABLED` | Power and network connected |
| 7 | `BURN_IN` | Hardware stress testing in progress |
| 8 | `PRODUCTION` | Serving workloads |
| 9 | `DECOMMISSION_REQUESTED` | Marked for removal |
| 10 | `DECOMMISSIONED` | Removed and wiped |

Allowed transitions live in `dbt/seeds/status_transitions.csv` (from_status, to_status, is_regression). Any transition not in that seed is a data quality failure.

Lifecycle stages used for cycle time:

| Stage | From | To |
|---|---|---|
| vendor_lead | ORDERED | SHIPPED |
| transit | SHIPPED | RECEIVED |
| install | RECEIVED | CABLED |
| burn_in | CABLED | PRODUCTION |
| end_to_end | ORDERED | PRODUCTION |

SLA: each vendor contract defines `sla_days_order_to_production`. Contracts change over time, which is why `dim_vendor` is SCD Type 2. A rack is judged against the SLA in effect on its `ordered_at` date.

## 3. Architecture

```
simulator (Python, local or Lambda)
   |  daily event files, JSON Lines, gzip
   v
S3 landing:  s3://<bucket>/landing/rack_events/ingest_date=YYYY-MM-DD/part-*.jsonl.gz
   |  Step Functions state 1: Lambda validates file manifest, writes run_id
   v
S3 raw:      s3://<bucket>/raw/rack_events/ingest_date=YYYY-MM-DD/*.parquet
   |  Step Functions state 2: Redshift COPY via Redshift Data API into raw.rack_events
   v
Redshift Serverless
   raw.*         append-only, exactly as landed + _ingest_date, _run_id, _loaded_at
   staging.*     dbt views
   intermediate.* dbt tables
   marts.*       dbt incremental tables + dims
   ops.*         audit + quarantine
   |  Step Functions state 3: ECS Fargate task or CodeBuild runs `dbt build`
   |  Step Functions state 4: Lambda writes audit row, publishes CloudWatch metrics
   v
QuickSight (SPICE dataset, scheduled refresh after pipeline)
```

Scheduling: EventBridge Scheduler triggers the state machine daily at 06:00 UTC. Failure in any state routes to an SNS topic with email subscription.

Where dbt runs: AWS CodeBuild project (pay per build minute, no idle cost). The state machine starts the build with `run_id`, `start_date`, `end_date` environment overrides and waits with the `.sync` integration.

Redshift connectivity: use the Redshift Data API from Lambda (no VPC attachment needed). CodeBuild connects with IAM temporary credentials (`dbt-redshift` method `iam`).

## 4. Data volume targets

- About 20 sites, 6 vendors, 8 rack types.
- About 50,000 racks over 24 months of history, 2 to 4 million events total.
- Daily increment after backfill: 3,000 to 8,000 events.

## 5. Incremental strategy

- Raw: append-only. Re-landing the same file is detected by file checksum in a manifest table and skipped.
- Staging: deduplicate on `event_id`, keep the row with the latest `_loaded_at`.
- `fct_rack_status_event`: incremental, `merge` on `event_sk`, filter `_ingest_date >= max(_ingest_date) - 14 days` to absorb late arrivals.
- `fct_rack_delivery`: rebuilt incrementally for any `rack_id` that received an event in the lookback window (select affected keys, recompute those rows, merge).
- Backfill: `dbt build --vars '{start_date: X, end_date: Y}'` overrides the lookback. Must be idempotent.

## 6. Data quality

Severity `error` fails the run; `warn` logs to audit and continues.

| Check | Severity | Where |
|---|---|---|
| `event_id` unique in staging after dedupe | error | stg test |
| `rack_id`, `status`, `event_at` not null | error | stg test |
| `status` in accepted values | error | stg test |
| Transition exists in `status_transitions` seed | warn, row to quarantine | int model |
| `event_at` not in the future (> now + 1h) | warn, quarantine | int model |
| Event arrives more than 30 days late | warn | audit metric |
| Daily row count within 50% to 200% of trailing 7-day mean | warn | singular test |
| Source freshness: newest `_loaded_at` < 26h | error | dbt source freshness |
| Each rack has exactly one row in `fct_rack_delivery` | error | mart test |
| Stage durations non-negative | error | mart test |

Quarantined rows land in `ops.quarantine_rack_events` with `reason`, `run_id`, `quarantined_at`. They never enter marts.

## 7. Observability

`ops.pipeline_run_audit` columns: `run_id`, `started_at`, `finished_at`, `status`, `ingest_date_min`, `ingest_date_max`, `rows_landed`, `rows_loaded_raw`, `rows_quarantined`, `rows_fct_event_merged`, `late_event_count`, `dbt_tests_passed`, `dbt_tests_warned`, `dbt_tests_failed`, `redshift_charged_seconds`, `est_cost_usd`.

CloudWatch custom metrics namespace `RackPipeline`: `RowsLoaded`, `RowsQuarantined`, `QuarantineRate`, `RunDurationSeconds`, `EstCostUsd`. Alarm on `QuarantineRate > 0.05` and on state machine `ExecutionsFailed >= 1`.

## 8. Cost accounting

After each run, query `SYS_SERVERLESS_USAGE` for the run window, sum `charged_seconds`, multiply by RPU-hour price for the region (stored in SSM parameter `/rack-pipeline/rpu_hour_usd`, set manually from the pricing page). Write to the audit table. `make cost` prints last 30 runs and the average.

Also document in README: S3 storage cost, QuickSight author cost, CodeBuild minutes per run.

## 9. Security

- S3 buckets: block public access, SSE-S3, versioning on landing.
- Redshift: dbt role has write on staging/intermediate/marts/ops only; QuickSight role has read on marts and ops only; P3's NLQ role has read on marts only.
- QuickSight row-level security by `site_region` via a permissions dataset.

## 10. Out of scope

Real vendor APIs, streaming ingestion, multi-region, CDC from an operational database.

## 11. Deliverables

- Working pipeline in a dev environment with 24 months backfilled and 7+ days of scheduled runs.
- QuickSight dashboard (4 sheets, see DASHBOARD_SPEC.md) with exported definition in `dashboards/`.
- README written as a short narrative doc: business question first, then architecture diagram, results with numbers, cost, limitations.
- Architecture diagram in `docs/architecture.png` (draw.io or diagrams-as-code with the `diagrams` Python package).
