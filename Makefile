# rack-delivery-pipeline. Targets mirror CLAUDE.md "Commands".
# Configuration comes from .env (copy .env.example); nothing account-specific lives here.

SHELL := /bin/sh
-include .env
# Pass .env settings to commands only when set: an empty AWS_PROFILE would make boto3 look for
# a profile named "".
ENV_KEYS := AWS_PROFILE AWS_REGION REDSHIFT_HOST REDSHIFT_WORKGROUP REDSHIFT_DATABASE 	QS_OWNER_ARN RLS_READER_EMAIL_US_WEST RLS_READER_EMAIL_AP_SOUTH
$(foreach key,$(ENV_KEYS),$(if $($(key)),$(eval export $(key))))

DBT := cd dbt && uv run dbt
TF := terraform
DEV := infra/envs/dev
SIM_START := 2024-10-01
SIM_END := 2026-09-30
TARGET ?= local

# Terraform variables from .env (unset ones fall back to the defaults in infra/envs/dev)
tf_var = $(if $($(2)),$(eval export TF_VAR_$(1) = $($(2))))
$(call tf_var,region,AWS_REGION)
$(call tf_var,alert_email,ALERT_EMAIL)
$(call tf_var,operator_cidr,OPERATOR_CIDR)
$(call tf_var,qs_owner_arn,QS_OWNER_ARN)
$(call tf_var,qs_owner_user_name,QS_OWNER_USER_NAME)
export TF_VAR_enable_quicksight = $(if $(filter true,$(QS_ENABLED)),true,false)

BUCKET = $(shell $(TF) -chdir=$(DEV) output -raw bucket)

.PHONY: setup lint typecheck test sim land-local dbt-deps dbt-local backfill ground-truth \
	mf-validate qs-definition package tf-bootstrap tf-init tf-plan tf-apply migrate upload \
	package-upload load-raw verify-raw dbt-bootstrap dbt-build run-pipeline schedule-on \
	schedule-off cost qs-deploy qs-snapshot rls-check evidence teardown teardown-state

# ---------------------------------------------------------------- local track

setup:
	uv sync
	$(DBT) deps
	uv run pre-commit install

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy simulator ops orchestration

# dbt test runs against the local warehouse, so run `make dbt-local` first.
test:
	uv run pytest -q
	$(DBT) test --target local
	cd dbt && uv run sqlfluff lint models tests

# Full 24-month history by default; `make sim DAYS=30` generates only the first N days, and
# `make sim SIM_END=<yesterday>` brings the history up to date before the daily schedule starts.
sim:
	uv run python -m simulator.generator --start-date $(SIM_START) \
		$(if $(DAYS),--days $(DAYS),--end-date $(SIM_END)) --out ./out --seed 42

# Stand-in for the land_to_raw Lambda: landing JSONL -> raw Parquet in ./out/raw.
land-local:
	uv run python -m orchestration.land_to_raw --out ./out

dbt-deps:
	@test -d dbt/dbt_packages || ($(DBT) deps)

# The local warehouse is fully derived from ./out, so it starts fresh: staging first, then the
# vendor contract history is replayed into the SCD2 snapshot, then everything else.
dbt-local: land-local dbt-deps
	rm -f local/warehouse.duckdb && mkdir -p local
	$(DBT) build --target local --select staging seeds
	uv run python -m ops.snapshot_replay --target local
	$(DBT) build --target local

# Rebuild both facts for racks with events ingested in [START, END]; prints row counts and
# md5 checksums. Run twice: identical output proves idempotency. TARGET=dev runs on CodeBuild.
backfill:
	uv run python -m ops.backfill --start $(START) --end $(END) --target $(TARGET)

ground-truth:
	uv run python -m ops.ground_truth --target $(TARGET)

# Semantic layer (models/semantic): YAML, semantics, and every metric against the warehouse.
# PYTHONUTF8 keeps mf's progress spinner from failing on Windows consoles.
mf-validate:
	cd dbt && PYTHONUTF8=1 uv run mf validate-configs

# Regenerate and validate dashboards/rack_delivery_definition.json.
qs-definition:
	uv run python -m ops.quicksight_definition

# ---------------------------------------------------------------- AWS (deployment window)

# Lambda code, Lambda dependency layer, and the dbt project zip for CodeBuild, into ./build.
package:
	uv run python -m ops.package

# Once per account: the Terraform state bucket and lock table (local state).
tf-bootstrap:
	cd infra/bootstrap && $(TF) init -input=false && $(TF) apply

tf-init:
	$(TF) -chdir=$(DEV) init -input=false -reconfigure \
		-backend-config=bucket=$$($(TF) -chdir=infra/bootstrap output -raw state_bucket) \
		-backend-config=dynamodb_table=$$($(TF) -chdir=infra/bootstrap output -raw lock_table) \
		-backend-config=region=$(AWS_REGION)

tf-plan: package
	$(TF) -chdir=$(DEV) plan

# Apply, then create the database users, schemas, raw tables and grants it needs.
tf-apply: package
	$(TF) -chdir=$(DEV) apply
	$(MAKE) migrate package-upload

migrate:
	uv run python -m ops.redshift_admin migrate

# Push local simulator output (history, reference, ground truth, state) to S3.
upload:
	aws s3 sync out/landing s3://$(BUCKET)/landing --only-show-errors
	aws s3 sync out/reference s3://$(BUCKET)/reference --only-show-errors
	aws s3 cp out/_ground_truth/racks.parquet s3://$(BUCKET)/ground_truth/racks.parquet --only-show-errors
	aws s3 cp out/_state.parquet s3://$(BUCKET)/sim/_state.parquet --only-show-errors

# dbt project for CodeBuild; rerun after changing anything under dbt/.
package-upload:
	uv run python -c "from ops.package import build_dbt_project; build_dbt_project()"
	aws s3 cp build/dbt_project.zip s3://$(BUCKET)/artifacts/dbt_project.zip --only-show-errors

load-raw:
	uv run python -m ops.pipeline load-raw

verify-raw:
	uv run python -m ops.redshift_admin verify-raw

# First cloud dbt build: replays vendor contract history into the SCD2 snapshot.
dbt-bootstrap:
	uv run python -m ops.pipeline dbt --mode bootstrap

dbt-build:
	uv run python -m ops.pipeline dbt

run-pipeline:
	uv run python -m ops.pipeline run

schedule-on:
	uv run python -m ops.pipeline schedule on

schedule-off:
	uv run python -m ops.pipeline schedule off

cost:
	uv run python -m ops.cost

qs-deploy: qs-definition
	uv run python -m ops.quicksight_deploy

qs-snapshot:
	uv run python -m ops.quicksight_snapshot

rls-check:
	uv run python -m ops.rls_check

evidence:
	uv run python -m ops.evidence

# Destroy everything except the state bucket, then prove nothing billable is left.
teardown:
	$(TF) -chdir=$(DEV) destroy
	uv run python -m ops.teardown_check

teardown-state:
	cd infra/bootstrap && $(TF) destroy
