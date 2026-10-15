# rack-delivery-pipeline. Targets mirror CLAUDE.md "Commands".
# Stubs exit 1 with the phase that implements them, so nothing silently "succeeds".

SHELL := /bin/sh
DBT_PROJECT := dbt/dbt_project.yml
SIM_START := 2024-10-01
SIM_END := 2026-09-30

define not_implemented
	@echo "make $@: not implemented (BUILD_PLAN $(1))" >&2; exit 1
endef

.PHONY: setup lint typecheck test sim tf-plan tf-apply upload dbt-local dbt-build \
	schedule-on schedule-off evidence backfill cost qs-deploy qs-snapshot teardown

setup:
	uv sync
	@if [ -f $(DBT_PROJECT) ]; then cd dbt && uv run dbt deps; else echo "dbt deps: skipped, no dbt project yet (Phase 4)"; fi
	uv run pre-commit install

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy simulator ops

# dbt test and sqlfluff join once the dbt project exists (Phase 4).
test:
	uv run pytest -q
	@if [ -f $(DBT_PROJECT) ]; then \
		cd dbt && uv run dbt test --target local && uv run sqlfluff lint models; \
	else echo "dbt test, sqlfluff: skipped, no dbt project yet (Phase 4)"; fi

# Full 24-month history by default; `make sim DAYS=30` generates only the first N days.
sim:
	uv run python -m simulator.generator --start-date $(SIM_START) \
		$(if $(DAYS),--days $(DAYS),--end-date $(SIM_END)) --out ./out --seed 42

tf-plan tf-apply:
	$(call not_implemented,Phase 2)

upload:
	$(call not_implemented,Phase 3)

dbt-local dbt-build:
	$(call not_implemented,Phase 4)

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
