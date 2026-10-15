"""Run dbt commands and SQL in-process, against whichever target is selected.

Queries go through `dbt show --inline`, so they can use ref() and the project's macros and run
unchanged on the local DuckDB target and on Redshift.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
from typing import Any

from dbt.artifacts.schemas.run import RunExecutionResult
from dbt.cli.main import dbtRunner

DBT_DIR = Path(__file__).resolve().parent.parent / "dbt"


def _target_args(target: str, dbt_vars: dict[str, Any] | None) -> list[str]:
    args = ["--target", target]
    if dbt_vars:
        args += ["--vars", json.dumps(dbt_vars)]
    return args


def run_dbt(args: list[str], target: str, dbt_vars: dict[str, Any] | None = None) -> None:
    """Run a dbt command from the dbt/ directory; raise if it fails."""
    cwd = Path.cwd()
    os.chdir(DBT_DIR)
    try:
        result = dbtRunner().invoke([*args, *_target_args(target, dbt_vars)])
    finally:
        os.chdir(cwd)
    if not result.success:
        raise SystemExit(f"dbt {' '.join(args)} failed")


def query(sql: str, target: str) -> list[tuple[Any, ...]]:
    """Rows of a SQL query (dbt Jinja allowed)."""
    cwd = Path.cwd()
    os.chdir(DBT_DIR)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            result = dbtRunner().invoke(
                ["show", "--inline", sql, "--limit", "-1", "--quiet", *_target_args(target, None)]
            )
    finally:
        os.chdir(cwd)
    if not result.success or not isinstance(result.result, RunExecutionResult):
        raise SystemExit(f"query failed: {result.exception}")
    table = result.result.results[0].agate_table
    if table is None:
        raise SystemExit("query returned no table")
    return [tuple(row) for row in table.rows]
