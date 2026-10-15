# DATA MODEL: Rack Delivery Pipeline

Kimball star schema. Two fact tables with different grains share conformed dimensions.

## Raw layer

### raw.rack_events

| Column | Type | Notes |
|---|---|---|
| event_id | VARCHAR(36) | UUID from source; duplicates possible |
| rack_id | VARCHAR(20) | e.g. `RK-000123` |
| site_code | VARCHAR(10) | |
| vendor_code | VARCHAR(10) | |
| rack_type_code | VARCHAR(20) | |
| status | VARCHAR(30) | |
| event_at | VARCHAR(40) | raw string; formats vary (see simulator) |
| recorded_by | VARCHAR(50) | nullable |
| payload_version | SMALLINT | 1 or 2 (schema drift) |
| _ingest_date | DATE | partition of landing |
| _run_id | VARCHAR(36) | |
| _loaded_at | TIMESTAMP | |
| _source_file | VARCHAR(512) | |

### raw.vendor_contracts

Full-snapshot file landed daily. Columns: `vendor_code`, `vendor_name`, `sla_days_order_to_production`, `contract_tier`, `region`, `_ingest_date`.

### raw.sites, raw.rack_types

Static reference, landed once; reloaded if changed.

### ops.file_manifest

`source_file`, `md5`, `row_count`, `_ingest_date`, `run_id`, `loaded_at`. Used to skip already-loaded files.

## Staging (views)

- `stg_rack_events`: parses `event_at` (handles ISO 8601 with Z, with offset, and epoch millis), maps payload v2 renamed column `rack_model` back to `rack_type_code`, uppercases codes, dedupes on `event_id` keeping latest `_loaded_at`.
- `stg_vendor_contracts`, `stg_sites`, `stg_rack_types`.

## Intermediate

- `int_rack_events_validated`: joins to `status_transitions` seed using `LAG(status) OVER (PARTITION BY rack_id ORDER BY event_at, event_id)`. Adds `prev_status`, `is_valid_transition`, `is_regression`, `is_future_dated`, `late_by_days` (`_ingest_date - event_at::date`). Invalid rows are routed to quarantine; valid rows continue.
- `int_rack_status_islands`: gaps-and-islands to collapse consecutive duplicate statuses and identify flapping (a rack entering the same status more than once). Uses the row_number difference technique.

## Dimensions

### dim_date
Generated with `dbt_utils.date_spine` from 2023-01-01 to 2028-12-31. Columns: `date_sk` (YYYYMMDD int), `date`, `year`, `quarter`, `month`, `month_name`, `iso_week`, `day_of_week`, `is_weekend`, `fiscal_year`, `fiscal_quarter` (fiscal year starts Jan, kept as a column so it can change).

### dim_site (SCD1)
`site_sk`, `site_code`, `site_name`, `site_region`, `country`, `capacity_racks`, `opened_date`.

### dim_rack_type (SCD1)
`rack_type_sk`, `rack_type_code`, `description`, `power_kw`, `is_gpu`.

### dim_vendor (SCD2)
Built from snapshot `snap_vendor_contracts` (strategy `check`, check_cols `[sla_days_order_to_production, contract_tier]`).

`vendor_sk`, `vendor_code`, `vendor_name`, `sla_days_order_to_production`, `contract_tier`, `valid_from`, `valid_to` (`9999-12-31` for current), `is_current`.

As-of join pattern used by facts:

```sql
left join dim_vendor v
    on f.vendor_code = v.vendor_code
    and f.ordered_at >= v.valid_from
    and f.ordered_at < v.valid_to
```

## Facts

### fct_rack_status_event (transaction fact)
Grain: one row per valid status change event.

`event_sk`, `event_id`, `rack_id`, `rack_type_sk`, `site_sk`, `vendor_sk` (as of event_at), `event_date_sk`, `status`, `prev_status`, `event_at`, `hours_since_prev_event`, `is_regression`, `late_by_days`, `_ingest_date`, `_run_id`.

Materialization: incremental, merge on `event_sk`, sort key `event_at`, dist style AUTO.

### fct_rack_delivery (accumulating snapshot fact)
Grain: one row per rack. Updated in place as milestones are reached.

`rack_sk`, `rack_id`, `site_sk`, `vendor_sk` (as of ordered_at), `rack_type_sk`, `ordered_date_sk`, `production_date_sk`,
milestone timestamps: `ordered_at`, `vendor_confirmed_at`, `shipped_at`, `received_at`, `racked_at`, `cabled_at`, `burn_in_started_at`, `production_at`, `decommission_requested_at`, `decommissioned_at`
(each = first time that status was reached; regressions do not reset),
stage durations: `vendor_lead_hours`, `transit_hours`, `install_hours`, `burn_in_hours`, `end_to_end_hours`,
`current_status`, `current_status_since`, `days_in_current_status`, `sla_days`, `sla_due_at`, `is_sla_breached`, `is_overdue_open` (no production yet and now > sla_due_at), `regression_count`, `last_event_at`, `_updated_run_id`.

Materialization: incremental merge on `rack_sk`, recomputing only racks touched in the lookback window.

### fct_site_capacity_daily (periodic snapshot fact, optional stretch)
Grain: site per day. `racks_in_production`, `racks_in_flight`, `capacity_racks`, `utilization_pct`. Enables capacity-planning visuals.

## Required analytical SQL (must appear somewhere in models or `analyses/`)

| Technique | Where |
|---|---|
| `LAG` / `LEAD` | `int_rack_events_validated`, `hours_since_prev_event` |
| Gaps and islands | `int_rack_status_islands` |
| Conditional aggregation pivot (`MIN(CASE WHEN status = ... THEN event_at END)`) | `fct_rack_delivery` milestones |
| `PERCENTILE_CONT ... WITHIN GROUP` | `analyses/cycle_time_percentiles.sql` and mart `agg_cycle_time_monthly` |
| As-of SCD2 join | facts to `dim_vendor` |
| Running total / moving average window | `agg_daily_throughput` (7-day moving avg of racks to production) |
| `QUALIFY`-style dedupe | Redshift supports `QUALIFY`; use in staging dedupe |
| Cohort analysis | `analyses/order_month_cohorts.sql`: % of racks in production by weeks since order, per order-month cohort |

## Aggregate marts for QuickSight

- `agg_cycle_time_monthly`: month, vendor, site, stage, p50, p90, mean, count.
- `agg_daily_throughput`: date, site, racks_to_production, 7d moving avg, open WIP.
- `agg_sla_vendor_monthly`: month, vendor, racks_completed, on_time, on_time_pct.

These keep SPICE datasets small and visuals fast.
