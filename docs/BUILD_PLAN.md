# BUILD PLAN: Rack Delivery Pipeline

Execute one phase per session. Each phase lists tasks, acceptance criteria and the verification to run. Do not mark a phase done until every criterion is shown passing.

## Execution order ($0 plan, see docs/DEPLOY_WINDOW.md)

- **Local track (no AWS):** 0, 1, then 4 and 5 against the `local` DuckDB target, reading simulator Parquet from `./out` instead of S3. Every test and acceptance criterion in 4 and 5 must pass locally, including the ground-truth match and the idempotency proof.
- **Cloud track (deployment window only):** 2, 3, 4 and 5 again on the `dev` Redshift target (they should pass with no model changes), then 6, 7, 8 and 9.
- Never create AWS resources during the local track.

## Phase 0: Scaffold

Tasks
- Create the repository layout from CLAUDE.md.
- `pyproject.toml` with uv, ruff, mypy, pytest, sqlfluff, pre-commit.
- `Makefile` with all targets from CLAUDE.md as stubs that print "not implemented" where needed.
- `.gitignore`, `.env.example` (AWS_PROFILE, AWS_REGION, PROJECT_BUCKET, REDSHIFT_WORKGROUP, REDSHIFT_DATABASE, QS_OWNER_ARN, ALERT_EMAIL).
- GitHub Actions workflow `ci.yml`: ruff, mypy, pytest on push and PR.

Acceptance
- `make setup` succeeds on a clean clone.
- `make test` runs (zero tests is fine) and exits 0.
- CI passes on first push.

## Phase 1: Simulator

Tasks
- Implement `simulator/` per SIMULATOR_SPEC.md, including ground truth output and all defects.
- Implement the four simulator tests.

Acceptance
- Full 24-month run completes in under 5 minutes locally.
- All simulator tests pass.
- A printed summary shows: racks, events, and the measured rate of each defect next to its configured rate.

Verify: `make sim` then `uv run pytest simulator/tests -v`.

## Phase 2: Infrastructure

Tasks
- `infra/bootstrap`: S3 state bucket + DynamoDB lock table.
- `infra/modules`: s3 data bucket (landing/raw prefixes), Redshift Serverless namespace + workgroup (lowest base RPU, usage limit set), IAM roles (dbt, lambda, quicksight-read, nlq-read placeholder), SNS topic + email subscription variable, SSM parameters.
- `infra/envs/dev` composing the modules. All resources tagged.
- `make teardown` destroys dev env.

Acceptance
- `terraform validate` and `terraform plan` clean.
- `make tf-apply` creates resources; `aws redshift-serverless get-workgroup` shows AVAILABLE.
- Redshift usage limit is set (show the CLI output).
- `make teardown` then `make tf-apply` round-trips cleanly.

## Phase 3: Ingestion

Tasks
- `make upload` syncs simulator landing output to S3.
- Lambda `land_to_raw`: for a given ingest_date, reads JSONL.gz, checks `ops.file_manifest` for md5, converts to Parquet (pyarrow), writes to raw prefix, records manifest row.
- Redshift DDL for `raw.*`, `ops.file_manifest`, `ops.pipeline_run_audit`, `ops.quarantine_rack_events` (managed by a migration script in `infra/sql/`).
- Lambda `copy_to_redshift`: issues `COPY ... FORMAT AS PARQUET` via Redshift Data API, polls to completion.

Acceptance
- Loading the same ingest_date twice results in identical `raw.rack_events` row count (manifest skip works).
- Row count in raw for a date equals the simulator's line count for that date.

## Phase 4: dbt staging and intermediate

Tasks
- dbt project, profiles using IAM auth, sources with freshness config.
- Seeds: `status_transitions.csv`, `sla_targets.csv` if needed.
- `stg_*` models including multi-format timestamp parsing and v2 schema mapping.
- `int_rack_events_validated`, `int_rack_status_islands`, quarantine routing (post-hook or separate model writing to `ops`).
- Tests from SPEC section 6.

Acceptance
- `dbt build --select staging intermediate` passes.
- Quarantine count by reason is within ±20% of simulator's injected invalid-transition and future-dated counts.
- Zero duplicate event_id in staging.

## Phase 5: Dimensions, snapshots, facts

Tasks
- `dim_date`, `dim_site`, `dim_rack_type`, snapshot + `dim_vendor` SCD2.
- `fct_rack_status_event`, `fct_rack_delivery` incremental with lookback and backfill vars.
- Aggregate marts.
- All required SQL techniques from DATA_MODEL.md present.
- Singular test comparing `fct_rack_delivery` milestones to `_ground_truth/racks.parquet` (load ground truth as a seed or external table in dev only): at least 99% of racks match exactly on `production_at`.

Acceptance
- `dbt build` full passes.
- `dim_vendor` shows 2 rows for V03, 2 for V05, 2 for V02, 1 for others.
- Idempotency proof: run `make backfill START=2025-06-01 END=2025-06-30` twice; script prints row counts and checksums for both facts, identical across runs.
- Ground truth match rate printed.

## Phase 6: Orchestration and observability

Tasks
- CodeBuild project running dbt with env overrides.
- Step Functions state machine: land_to_raw, copy, dbt build (.sync), audit writer, QuickSight ingestion trigger, failure catch to SNS.
- EventBridge Scheduler daily rule.
- Lambda in the state machine's first step runs the simulator's daily mode so the system produces new data each day (simulator state in S3).
- Audit writer computes cost from `SYS_SERVERLESS_USAGE`.
- CloudWatch metrics and two alarms.
- `make cost`.

Acceptance
- A manual state machine execution succeeds end to end; audit row written with non-null cost.
- Deliberately break one input (inject a file with 10% invalid transitions) and show the alarm fires and the email arrives.
- Deliberately make a dbt error test fail and show the execution fails and SNS fires.

## Phase 7: QuickSight as code

Prerequisite done by the owner once: QuickSight Enterprise subscription active in us-west-2, owner's QuickSight user ARN in `.env` as `QS_OWNER_ARN`.

Tasks
- Terraform: VPC connection, data source, datasets, refresh schedules, RLS permissions dataset from dbt model `ops.rls_site_region`.
- `dashboards/rack_delivery_definition.json` with all four sheets from DASHBOARD_SPEC.md.
- `ops/quicksight_deploy.py`, `ops/quicksight_snapshot.py`, `ops/rls_check.py`.
- State machine's final step triggers SPICE ingestion for each dataset.

Acceptance
- `make qs-deploy` creates or updates the dashboard with no errors; rerunning it is a no-op apart from a version bump.
- Snapshot PNGs exist for all four sheets. Claude Code has viewed each PNG and lists every visual by title with a one-line confirmation that it shows data.
- `ops/rls_check.py` passes.
- Ingestion succeeds after a pipeline run (show `DescribeIngestion` status COMPLETED).

## Phase 8: README and polish

Tasks
- README structure: Business question; What the dashboard shows (screenshots); Architecture (diagram); Data model (ERD); How late and bad data are handled; Results (numbers below); Cost; Limitations; How to run.
- Generate ERD from dbt docs or draw it.
- Measure and record:
  - rows processed per daily run and total
  - daily run duration p50
  - quarantine rate
  - late-event recovery rate (late events that ended up in facts / total late events)
  - cost per daily run and per backfill month
  - query speedup on `agg_cycle_time_monthly` from sort key choice (before/after with `EXPLAIN` and timing)
  - ground truth match rate
- `docs/RESUME_BULLETS.md` with 3 draft bullets using those numbers.
- `docs/INTERVIEW_PREP.md`: walk-through of the architecture in 2 minutes; why an accumulating snapshot fact; how SCD2 as-of joins work here; how late data and idempotent backfills work; every SQL technique from DATA_MODEL.md with the model where it lives; how the dashboard was built as code; cost numbers; 10 likely interviewer questions (including Leadership Principle angles: Frugality, Insist on the Highest Standards, Dive Deep) with answers grounded in this repo.

Acceptance
- Every number in README traces to a command in `ops/` that reproduces it.
- Fresh clone + `make setup` + README steps reproduce the dev environment.

## Phase 9: Evidence capture and teardown

Run before the QuickSight trial reaches day 20. Follow docs/DEPLOY_WINDOW.md.

Tasks
- Final `make qs-snapshot`; commit PNGs and the snapshot PDF to `evidence/` and `docs/img/`.
- Export `ops.pipeline_run_audit` and the 7-day cost report to `evidence/*.csv`.
- Render the Step Functions graph from the ASL file to `docs/img/state_machine.png`.
- Export CloudWatch metric charts with `GetMetricWidgetImage` to `docs/img/`.
- Record an asciinema session that starts an execution, waits for success and queries the audit table; convert it to GIF with `agg`; embed it in the README.
- `dbt docs generate` with the dev target, then publish the static site to GitHub Pages via a workflow (`gh-pages` branch). Link it from the README.
- Build a one-page GitHub Pages project site (`site/index.html`): business question, dashboard screenshots, headline numbers, architecture, links to repo and dbt docs.
- Add the deployment-window statement from DEPLOY_WINDOW.md to the README with real dates and run counts.
- `make teardown`, then run every check in the DEPLOY_WINDOW teardown checklist and paste the output. QuickSight unsubscribe is done by the owner in the console.

Acceptance
- All evidence files committed; GitHub Pages sites load.
- The tag-based resource search returns nothing for `project=rack-delivery-pipeline`.
