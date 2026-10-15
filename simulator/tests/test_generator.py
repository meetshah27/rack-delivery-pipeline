"""The four simulator tests from docs/SIMULATOR_SPEC.md."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from simulator.config import SimConfig, load_config
from simulator.generator import MILESTONE_COLUMNS, RunStats, run_daily, run_full

FULL_END = date(2026, 9, 30)


@pytest.fixture(scope="session")
def cfg() -> SimConfig:
    return load_config()


@pytest.fixture(scope="session")
def full_run(cfg: SimConfig, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, RunStats]:
    out = tmp_path_factory.mktemp("full")
    return out, run_full(cfg, cfg.seed, cfg.history_start, FULL_END, out)


def _files(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_same_seed_is_byte_identical(cfg: SimConfig, tmp_path: Path) -> None:
    end = cfg.history_start + timedelta(days=44)
    run_full(cfg, cfg.seed, cfg.history_start, end, tmp_path / "a")
    run_full(cfg, cfg.seed, cfg.history_start, end, tmp_path / "b")
    a, b = _files(tmp_path / "a"), _files(tmp_path / "b")
    assert len(a) > 45
    assert a == b


def test_defect_rates_within_20pct(cfg: SimConfig, full_run: tuple[Path, RunStats]) -> None:
    _, stats = full_run
    off = {
        name: (configured, measured)
        for name, configured, measured, _ in stats.rate_rows(cfg)
        if abs(measured - configured) > 0.2 * configured
    }
    assert not off, f"defect rates outside +/-20% relative: {off}"
    assert stats.missing_days == [cfg.defects.missing_day]


def test_ground_truth_milestones_monotonic(full_run: tuple[Path, RunStats]) -> None:
    out, _ = full_run
    table = pq.read_table(out / "_ground_truth" / "racks.parquet")
    columns = list(MILESTONE_COLUMNS.values())
    # Compare as epoch seconds; tz-aware -> datetime conversion needs tzdata on Windows.
    rows = pa.table(
        {"rack_id": table.column("rack_id")}
        | {c: table.column(c).cast(pa.int64()) for c in columns}
    ).to_pylist()
    assert len(rows) > 40_000
    for row in rows:
        values = [row[c] for c in columns]
        reached = [v for v in values if v is not None]
        # Milestones are first-reached times, so regressions never reorder them; a rack only
        # lacks later milestones, never an earlier one.
        assert values[: len(reached)] == reached, row["rack_id"]
        assert reached == sorted(reached), row["rack_id"]


def test_daily_mode_matches_full_run(cfg: SimConfig, tmp_path: Path) -> None:
    d0 = cfg.history_start + timedelta(days=59)
    days = [d0 + timedelta(days=n) for n in (1, 2, 3)]

    run_full(cfg, cfg.seed, cfg.history_start, d0, tmp_path / "daily")
    for day in days:
        run_daily(cfg, day, tmp_path / "daily")
    run_full(cfg, cfg.seed, cfg.history_start, days[-1], tmp_path / "full")

    daily, full = _files(tmp_path / "daily"), _files(tmp_path / "full")
    compared = [
        path
        for path in full
        if any(f"ingest_date={day.isoformat()}" in path for day in days)
        or path.startswith("_ground_truth")
        or path == "_state.parquet"
    ]
    assert len(compared) == 2 * len(days) + 2
    for path in compared:
        assert daily[path] == full[path], path
