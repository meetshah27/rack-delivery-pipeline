# rack-delivery-pipeline. Targets mirror CLAUDE.md "Commands".
# Stubs exit 1 with the phase that implements them, so nothing silently "succeeds".

SHELL := /bin/sh
DBT := cd dbt && uv run dbt
SIM_START := 2024-10-01
SIM_END := 2026-09-30

define not_implemented
	@echo "make $@: not implemented (BUILD_PLAN $(1))" >&2; exit 1
endef

.PHONY: setup lint typecheck test sim land-local dbt-deps tf-plan tf-apply upload dbt-local dbt-build \
	schedule-on schedule-off evidence backfill cost qs-deploy qs-snapshot teardown

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

# Full 24-month history by default; `make sim DAYS=30` generates only the first N days.
sim:
	uv run python -m simulator.generator --start-date $(SIM_START) \
		$(if $(DAYS),--days $(DAYS),--end-date $(SIM_END)) --out ./out --seed 42

tf-plan tf-apply:
	$(call not_implemented,Phase 2)

upload:
	$(call not_implemented,Phase 3)

# Local track: stand-in for the land_to_raw Lambda, landing JSONL -> raw Parquet in ./out/raw.
land-local:
	uv run python -m orchestration.land_to_raw --out ./out

dbt-deps:
	@test -d dbt/dbt_packages || ($(DBT) deps)

dbt-local: land-local dbt-deps
	mkdir -p local
	$(DBT) seed --target local
	$(DBT) build --target local

dbt-build: dbt-deps
	$(DBT) build --target dev

backfill:
	$(call not_implemented,Phase 5)

schedule-on schedule-off cost:
	$(call not_implemented,Phase 6)

qs-deploy qs-snapshot:
	$(call not_implemented,Phase 7)

evidence:
	$(call not_implemented,Phase 9)

teardown:
	$(call not_implemented,Phase 2)
