"""Rack lifecycle event generator: reproducible from one seed, with deliberate defects.

Full run:   python -m simulator.generator --start-date 2024-10-01 --end-date 2026-09-30 --out ./out
Daily mode: python -m simulator.generator --daily --date 2026-10-01 --out ./out
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import shutil
import sys
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from simulator import lifecycle as lc
from simulator.config import DEFAULT_CONFIG, SimConfig, load_config

STATE_FILE = "_state.parquet"
MILESTONE_COLUMNS = {status: f"{status.lower()}_at" for status in lc.STATUSES}


@dataclass
class RunStats:
    """Counts behind the measured defect rates."""

    racks: int = 0
    files_written: int = 0
    files_shuffled: int = 0
    lines: int = 0
    originals: int = 0
    flag_counts: dict[int, int] = field(default_factory=lambda: defaultdict(int))
    originals_by_vendor: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    late_by_vendor: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    schema_drift_eligible: int = 0
    racks_burn_in: int = 0
    racks_regressed: int = 0
    missing_days: list[date] = field(default_factory=list)

    def add_emissions(self, world: lc.World, emissions: Iterable[lc.Emission]) -> None:
        drift_vendor = world.cfg.defects.schema_drift_vendor
        for e in emissions:
            self.lines += 1
            for flag in (
                lc.DUPLICATE,
                lc.LATE,
                lc.VERY_LATE,
                lc.FUTURE_DATED,
                lc.INVALID_TRANSITION,
                lc.NULL_RECORDED_BY,
                lc.FORMAT_DRIFT,
                lc.SCHEMA_V2,
            ):
                if e.flags & flag:
                    self.flag_counts[flag] += 1
            if e.vendor_code == drift_vendor and e.ingest_ordinal >= world.schema_drift_ordinal:
                self.schema_drift_eligible += 1
            if not e.flags & (lc.DUPLICATE | lc.INVALID_TRANSITION):
                self.originals += 1
                self.originals_by_vendor[e.vendor_code] += 1
                if e.flags & lc.LATE:
                    self.late_by_vendor[e.vendor_code] += 1

    def rate_rows(self, cfg: SimConfig) -> list[tuple[str, float, float, int]]:
        """(defect, configured rate, measured rate, measured count)."""
        d = cfg.defects

        def row(
            name: str, configured: float, count: int, denom: int
        ) -> tuple[str, float, float, int]:
            return name, configured, count / denom if denom else 0.0, count

        rows = [
            row(
                "duplicate_event_id",
                d.duplicate_rate,
                self.flag_counts[lc.DUPLICATE],
                self.originals,
            ),
            row(
                "very_late_30_60d", d.very_late_rate, self.flag_counts[lc.VERY_LATE], self.originals
            ),
            row(
                "out_of_order_file",
                d.out_of_order_file_rate,
                self.files_shuffled,
                self.files_written,
            ),
            row(
                "timestamp_format_drift",
                d.format_drift_rate,
                self.flag_counts[lc.FORMAT_DRIFT],
                self.originals,
            ),
            row("schema_drift_v2", 1.0, self.flag_counts[lc.SCHEMA_V2], self.schema_drift_eligible),
            row(
                "invalid_transition",
                d.invalid_transition_rate,
                self.flag_counts[lc.INVALID_TRANSITION],
                self.originals,
            ),
            row("legit_regression", d.regression_rate, self.racks_regressed, self.racks_burn_in),
            row(
                "future_dated",
                d.future_dated_rate,
                self.flag_counts[lc.FUTURE_DATED],
                self.originals,
            ),
            row(
                "null_recorded_by",
                d.null_recorded_by_rate,
                self.flag_counts[lc.NULL_RECORDED_BY],
                self.lines - self.flag_counts[lc.DUPLICATE],
            ),
        ]
        for v in cfg.vendors:
            rows.append(
                row(
                    f"late_3_10d_{v.code}",
                    v.late_rate,
                    self.late_by_vendor[v.code],
                    self.originals_by_vendor[v.code],
                )
            )
        return rows


# ---------------------------------------------------------------- writers


def _write_gz_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = "".join(line + "\n" for line in lines).encode()
    # mtime=0 keeps the gzip header, and so the file bytes, reproducible.
    path.write_bytes(gzip.compress(data, compresslevel=6, mtime=0))


def _write_csv(path: Path, header: list[str], rows: Iterable[Iterable[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    path.write_bytes(buf.getvalue().encode())


def write_reference(world: lc.World, out: Path) -> None:
    _write_csv(
        out / "reference" / "sites.csv",
        ["site_code", "site_name", "site_region", "country", "capacity_racks", "opened_date"],
        (
            [
                s.cfg.code,
                s.cfg.name,
                s.cfg.region,
                s.cfg.country,
                s.capacity_racks,
                s.opened_date.isoformat(),
            ]
            for s in world.sites
        ),
    )
    _write_csv(
        out / "reference" / "rack_types.csv",
        ["rack_type_code", "description", "power_kw", "is_gpu"],
        ([t.code, t.description, t.power_kw, str(t.is_gpu).lower()] for t in world.cfg.rack_types),
    )


def write_day(
    world: lc.World, out: Path | None, day: date, emissions: list[lc.Emission], stats: RunStats
) -> None:
    """Landing files for one ingest date: rack events (unless the missing day) and contracts.

    With out=None nothing is written; only stats are collected.
    """
    tag = f"ingest_date={day.isoformat()}"
    if out is not None:
        _write_csv(
            out / "landing" / "vendor_contracts" / tag / "vendor_contracts.csv",
            [
                "vendor_code",
                "vendor_name",
                "sla_days_order_to_production",
                "contract_tier",
                "region",
            ],
            ([v.code, v.name, sla, tier, v.region] for v, sla, tier in world.contracts_on(day)),
        )
    if day.toordinal() == world.missing_ordinal:
        stats.missing_days.append(day)
        return

    rng = lc.stream_rng(world.seed, lc.STREAM_FILE, day.toordinal())
    shuffled = rng.random() < world.cfg.defects.out_of_order_file_rate
    stats.files_shuffled += shuffled
    stats.files_written += 1
    stats.add_emissions(world, emissions)
    if out is not None:
        ordered = sorted(emissions, key=lambda e: (e.sort_ts, e.line))
        if shuffled:
            ordered = [ordered[int(i)] for i in rng.permutation(len(ordered))]
        _write_gz_lines(
            out / "landing" / "rack_events" / tag / "part-0000.jsonl.gz",
            [e.line for e in ordered],
        )


def write_ground_truth(out: Path, results: list[lc.RackResult], as_of: date) -> None:
    """True milestones known by the end of `as_of` (never uploaded; tests use it)."""
    horizon = lc.day_start(as_of + timedelta(days=1))
    columns: dict[str, list[object]] = {
        "rack_id": [],
        "site_code": [],
        "vendor_code": [],
        "rack_type_code": [],
    }
    for col in MILESTONE_COLUMNS.values():
        columns[col] = []
    columns["regression_count"] = []
    for r in results:
        columns["rack_id"].append(r.rack.rack_id)
        columns["site_code"].append(r.rack.site_code)
        columns["vendor_code"].append(r.rack.vendor_code)
        columns["rack_type_code"].append(r.rack.rack_type_code)
        for status, col in MILESTONE_COLUMNS.items():
            ts = r.milestones.get(status)
            columns[col].append(ts if ts is not None and ts < horizon else None)
        regressed = r.regression_at is not None and r.regression_at < horizon
        columns["regression_count"].append(1 if regressed else 0)

    ts_type = pa.timestamp("s")  # naive UTC, the warehouse convention
    table = pa.table(
        {
            name: pa.array(
                values,
                type=ts_type
                if name.endswith("_at")
                else pa.int32()
                if name == "regression_count"
                else pa.string(),
            )
            for name, values in columns.items()
        }
    )
    path = out / "_ground_truth" / "racks.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)


def write_state(out: Path, world: lc.World, racks: list[lc.Rack], last_date: date) -> None:
    table = pa.table(
        {
            "idx": pa.array([r.idx for r in racks], type=pa.int64()),
            "rack_id": [r.rack_id for r in racks],
            "ordered_at": pa.array([r.ordered_at for r in racks], type=pa.int64()),
            "site_code": [r.site_code for r in racks],
            "vendor_code": [r.vendor_code for r in racks],
            "rack_type_code": [r.rack_type_code for r in racks],
        }
    ).replace_schema_metadata(
        {
            "seed": str(world.seed),
            "last_date": last_date.isoformat(),
            "config_sha256": world.cfg.sha256,
        }
    )
    pq.write_table(table, out / STATE_FILE)


def read_state(out: Path) -> tuple[int, date, str, list[lc.Rack]]:
    path = out / STATE_FILE
    if not path.exists():
        sys.exit(f"{path} not found: run a full generation before --daily")
    table = pq.read_table(path)
    meta = {k.decode(): v.decode() for k, v in (table.schema.metadata or {}).items()}
    racks = [lc.Rack(**row) for row in table.to_pylist()]
    return int(meta["seed"]), date.fromisoformat(meta["last_date"]), meta["config_sha256"], racks


# ---------------------------------------------------------------- runs


def generate_racks(world: lc.World, end: date) -> list[lc.Rack]:
    racks: list[lc.Rack] = []
    day = world.cfg.history_start
    while day <= end:
        racks += lc.arrivals_for_day(world, day, first_idx=len(racks) + 1)
        day += timedelta(days=1)
    return racks


def _regression_stats(stats: RunStats, results: list[lc.RackResult], as_of: date) -> None:
    horizon = lc.day_start(as_of + timedelta(days=1))
    for r in results:
        burn_in = r.milestones.get("BURN_IN")
        if burn_in is not None and burn_in < horizon:
            stats.racks_burn_in += 1
            if r.regression_at is not None and r.regression_at < horizon:
                stats.racks_regressed += 1


def run_full(cfg: SimConfig, seed: int, start: date, end: date, out: Path | None) -> RunStats:
    """Generate every ingest date in [start, end]. With out=None, measure without writing."""
    if start < cfg.history_start or end < start:
        sys.exit(f"need {cfg.history_start} <= start-date <= end-date")
    world = lc.World(cfg, seed)
    racks = generate_racks(world, end)
    results = [lc.simulate_rack(world, r) for r in racks]

    by_day: dict[int, list[lc.Emission]] = defaultdict(list)
    for r in results:
        for e in r.emissions:
            if start.toordinal() <= e.ingest_ordinal <= end.toordinal():
                by_day[e.ingest_ordinal].append(e)

    stats = RunStats(racks=len(racks))
    if out is not None:
        for owned in ("landing", "reference", "_ground_truth"):
            shutil.rmtree(out / owned, ignore_errors=True)
        (out / STATE_FILE).unlink(missing_ok=True)
        write_reference(world, out)

    day = start
    while day <= end:
        write_day(world, out, day, by_day.get(day.toordinal(), []), stats)
        day += timedelta(days=1)

    _regression_stats(stats, results, end)
    if out is not None:
        write_ground_truth(out, results, end)
        write_state(out, world, racks, end)
    return stats


def run_daily(cfg: SimConfig, day: date, out: Path) -> RunStats:
    """Generate one ingest date, continuing the racks persisted in out/_state.parquet."""
    seed, last_date, config_sha, racks = read_state(out)
    if config_sha != cfg.sha256:
        sys.exit("config.yaml changed since the state was written; rerun a full generation")
    if day != last_date + timedelta(days=1):
        sys.exit(
            f"state ends at {last_date}; next daily date must be {last_date + timedelta(days=1)}"
        )

    world = lc.World(cfg, seed)
    racks += lc.arrivals_for_day(world, day, first_idx=len(racks) + 1)
    results = [lc.simulate_rack(world, r) for r in racks]
    emissions = [e for r in results for e in r.emissions if e.ingest_ordinal == day.toordinal()]

    stats = RunStats(racks=len(racks))
    write_reference(world, out)
    write_day(world, out, day, emissions, stats)
    write_ground_truth(out, results, day)
    write_state(out, world, racks, day)
    return stats


def print_summary(cfg: SimConfig, stats: RunStats) -> None:
    print(f"racks: {stats.racks:,}")
    print(f"events (lines written): {stats.lines:,}  original events: {stats.originals:,}")
    print(
        f"files: {stats.files_written:,}  missing days: "
        f"{', '.join(d.isoformat() for d in stats.missing_days) or 'none'}"
    )
    print(f"\n{'defect':<26}{'configured':>12}{'measured':>12}{'count':>10}")
    for name, configured, measured, count in stats.rate_rows(cfg):
        print(f"{name:<26}{configured:>12.4%}{measured:>12.4%}{count:>10,}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--out", type=Path, default=Path("out"))
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--seed", type=int, help="overrides config seed (full run only)")
    parser.add_argument(
        "--start-date", type=date.fromisoformat, help="default: config history_start"
    )
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument("--days", type=int, help="alternative to --end-date")
    parser.add_argument("--daily", action="store_true")
    parser.add_argument("--date", type=date.fromisoformat, help="ingest date for --daily")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    if args.daily:
        if args.date is None:
            parser.error("--daily needs --date")
        stats = run_daily(cfg, args.date, args.out)
        print(f"{args.date}: {stats.lines:,} lines, {stats.racks:,} racks in state")
        return

    start = args.start_date or cfg.history_start
    if args.end_date is not None:
        end = args.end_date
    elif args.days is not None:
        end = start + timedelta(days=args.days - 1)
    else:
        parser.error("full run needs --end-date or --days")
    stats = run_full(cfg, args.seed if args.seed is not None else cfg.seed, start, end, args.out)
    print_summary(cfg, stats)


if __name__ == "__main__":
    main()
