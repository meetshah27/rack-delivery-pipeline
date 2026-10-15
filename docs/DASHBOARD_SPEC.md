# DASHBOARD SPEC: Amazon QuickSight

Built entirely as code by Claude Code. Nothing is clicked together in the console.

- Terraform creates:
  - the QuickSight VPC connection (`aws_quicksight_vpc_connection`) into the Redshift Serverless workgroup's subnets, with a security group allowing port 5439 from the QuickSight network interfaces
  - the Redshift data source (`aws_quicksight_data_source`)
  - the SPICE datasets (`aws_quicksight_data_set`), including the row-level security permissions dataset
  - refresh schedules
- The analysis and dashboard are defined as JSON in `dashboards/rack_delivery_definition.json`, which follows the QuickSight `Definition` structure. `ops/quicksight_deploy.py` (boto3) does create-or-update for the analysis and dashboard, publishes the new version, and grants permissions to the owner's QuickSight user ARN taken from `.env`.
- `ops/quicksight_snapshot.py` uses `StartDashboardSnapshotJob` to export each sheet as PDF, converts the pages to PNG for the README, and writes them to `docs/img/`. Claude Code reads the PNGs to visually check every visual: titles present, no empty visuals, sensible axes. It fixes the definition JSON and redeploys until the check is clean.
- `ops/rls_check.py` verifies row-level security. It registers two QuickSight reader users mapped to different regions in `ops.rls_site_region`. It then generates an embed URL for each user with `GenerateEmbedUrlForRegisteredUser`, logs in through the built-in browser if one is available to the session, and confirms each user sees only their region. If no browser is available, it falls back to asserting the RLS rules dataset contents and that RLS is attached to `ds_delivery` via `DescribeDataSet`.

## Datasets (SPICE)

| Dataset | Source | Refresh |
|---|---|---|
| ds_delivery | marts.fct_rack_delivery joined to dims | Daily, triggered after pipeline success |
| ds_cycle_monthly | marts.agg_cycle_time_monthly | Daily |
| ds_throughput | marts.agg_daily_throughput | Daily |
| ds_pipeline_health | ops.pipeline_run_audit | Daily |
| ds_rls_permissions | ops.rls_site_region (UserName, site_region) | Daily |

Trigger refresh from the state machine's final step with `quicksight:CreateIngestion`.

## Sheet 1: Executive

- KPI: On-time to production %, this month vs last month (conditional color, target 90%).
- KPI: p90 end-to-end days.
- KPI: Racks in flight (ordered, not in production).
- KPI: Overdue open racks.
- Line: Monthly on-time % by vendor, last 12 months.
- Bar: Racks to production per week with 7-day moving average line.

## Sheet 2: Cycle Time

- Stacked bar: median hours per stage by vendor (shows where time goes).
- Box plot: end-to-end days by site.
- Heat map: p90 install hours, site x month.
- Filter controls: vendor, site, rack type, order month range.

## Sheet 3: SLA Breaches

- Table: overdue open racks with rack_id, site, vendor, current_status, days_in_current_status, days overdue. Sorted by days overdue.
- Bar: breach count by vendor x contract tier.
- Drill-down from vendor to site to rack.
- Calculated field: `days_overdue = dateDiff(sla_due_at, now(), 'DD')`.

## Sheet 4: Pipeline Health

- KPI: Last run status and finished_at.
- KPI: Hours since last successful run (red if > 26).
- Line: quarantine rate per run with 5% reference line.
- Line: est_cost_usd per run.
- Table: last 14 runs with all audit columns.

## Standards

- Every visual has a title phrased as the question it answers.
- Dates in `MMM d, yyyy`. Durations in days with one decimal unless hours are more readable.
- Row-level security on ds_delivery by site_region, verified by `ops/rls_check.py`.
- Add a narrative text box on Sheet 1 stating data freshness and known caveats (synthetic data).
