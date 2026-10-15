"""Rack lifecycle model: order arrivals, true timelines, and the defective events a source emits.

Every random draw is keyed by (seed, stream, key), so one day's arrivals or one rack's whole
history can be regenerated in isolation. Daily mode depends on this: it rebuilds each rack's
timeline and keeps only the events whose ingest date is the requested day, which yields the
same bytes a full run produces for that day.
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from typing import NamedTuple

import numpy as np
from numpy.typing import NDArray

from simulator.config import SimConfig, SiteCfg, VendorCfg

STATUSES = (
    "ORDERED",
    "VENDOR_CONFIRMED",
    "SHIPPED",
    "RECEIVED",
    "RACKED",
    "CABLED",
    "BURN_IN",
    "PRODUCTION",
    "DECOMMISSION_REQUESTED",
    "DECOMMISSIONED",
)
STATUS_INDEX = {status: i for i, status in enumerate(STATUSES)}

SECONDS_PER_DAY = 86_400
EPOCH_ORDINAL = date(1970, 1, 1).toordinal()

STREAM_REFERENCE = 0
STREAM_ARRIVALS = 1
STREAM_RACK = 2
STREAM_FILE = 3

# Emission flags, used only for measuring defect rates; never written to output.
DUPLICATE = 1
LATE = 2
VERY_LATE = 4
FUTURE_DATED = 8
INVALID_TRANSITION = 16
NULL_RECORDED_BY = 32
FORMAT_DRIFT = 64
SCHEMA_V2 = 128


def stream_rng(seed: int, stream: int, key: int = 0) -> np.random.Generator:
    return np.random.default_rng(np.random.SeedSequence(seed, spawn_key=(stream, key)))


def day_start(day: date) -> int:
    return (day.toordinal() - EPOCH_ORDINAL) * SECONDS_PER_DAY


def ordinal_of(ts: int) -> int:
    return EPOCH_ORDINAL + ts // SECONDS_PER_DAY


def month_index(history_start: date, day: date) -> int:
    return (day.year - history_start.year) * 12 + day.month - history_start.month + 1


def lognormal_seconds(rng: np.random.Generator, mean_days: float, std_days: float) -> int:
    sigma2 = math.log(1 + (std_days / mean_days) ** 2)
    mu = math.log(mean_days) - sigma2 / 2
    return int(rng.lognormal(mu, math.sqrt(sigma2)) * SECONDS_PER_DAY)


@dataclass(frozen=True, slots=True)
class Rack:
    idx: int
    rack_id: str
    ordered_at: int
    site_code: str
    vendor_code: str
    rack_type_code: str


class Emission(NamedTuple):
    ingest_ordinal: int
    sort_ts: int
    line: str
    flags: int
    vendor_code: str


@dataclass(slots=True)
class RackResult:
    rack: Rack
    milestones: dict[str, int]  # first time each status was truly reached
    regression_at: int | None
    emissions: list[Emission] = field(default_factory=list)


@dataclass(frozen=True)
class SiteRef:
    cfg: SiteCfg
    capacity_racks: int
    opened_date: date


class World:
    """Config plus everything derived once from the seed (site capacities, lookups)."""

    def __init__(self, cfg: SimConfig, seed: int) -> None:
        self.cfg = cfg
        self.seed = seed
        rng = stream_rng(seed, STREAM_REFERENCE)
        lo, hi = cfg.capacity_range
        self.sites = tuple(
            SiteRef(
                cfg=s,
                capacity_racks=int(rng.integers(lo, hi + 1)),
                opened_date=date(2015, 1, 1) + timedelta(days=int(rng.integers(0, 8 * 365))),
            )
            for s in cfg.sites
        )
        self.site_by_code = {s.cfg.code: s.cfg for s in self.sites}
        self.vendor_by_code = {v.code: v for v in cfg.vendors}
        self.rack_type_by_code = {t.code: t for t in cfg.rack_types}
        self.site_p = _normalized([float(s.capacity_racks) for s in self.sites])
        self.vendor_p = _normalized([v.weight for v in cfg.vendors])
        self.rack_type_p = _normalized([t.weight for t in cfg.rack_types])
        self.missing_ordinal = cfg.defects.missing_day.toordinal()
        self.schema_drift_ordinal = cfg.defects.schema_drift_from.toordinal()

    def month(self, day: date) -> int:
        return month_index(self.cfg.history_start, day)

    def contracts_on(self, day: date) -> list[tuple[VendorCfg, int, str]]:
        """(vendor, sla_days, tier) in effect on `day`."""
        month = self.month(day)
        rows = []
        for v in self.cfg.vendors:
            sla, tier = v.sla_days, v.tier
            for change in sorted(self.cfg.contract_changes, key=lambda c: c.month):
                if change.vendor == v.code and change.month <= month:
                    if change.field == "sla_days":
                        sla = int(change.value)
                    else:
                        tier = str(change.value)
            rows.append((v, sla, tier))
        return rows


def _normalized(weights: list[float]) -> NDArray[np.float64]:
    arr = np.asarray(weights, dtype=np.float64)
    normalized: NDArray[np.float64] = arr / arr.sum()
    return normalized


def arrivals_for_day(world: World, day: date, first_idx: int) -> list[Rack]:
    """Racks ordered on `day`, numbered from `first_idx`."""
    cfg = world.cfg.arrivals
    rng = stream_rng(world.seed, STREAM_ARRIVALS, day.toordinal())
    lam = cfg.base_daily_orders
    if day.month in (10, 11, 12):
        lam *= cfg.q4_lift
    if world.month(day) >= cfg.expansion_from_month:
        lam *= cfg.expansion_factor
    n = int(rng.poisson(lam))
    offsets = np.sort(rng.integers(0, SECONDS_PER_DAY, size=n))
    sites = rng.choice(len(world.sites), size=n, p=world.site_p)
    vendors = rng.choice(len(world.cfg.vendors), size=n, p=world.vendor_p)
    types = rng.choice(len(world.cfg.rack_types), size=n, p=world.rack_type_p)
    start = day_start(day)
    return [
        Rack(
            idx=first_idx + i,
            rack_id=f"RK-{first_idx + i:06d}",
            ordered_at=start + int(offsets[i]),
            site_code=world.sites[int(sites[i])].cfg.code,
            vendor_code=world.cfg.vendors[int(vendors[i])].code,
            rack_type_code=world.cfg.rack_types[int(types[i])].code,
        )
        for i in range(n)
    ]


def _true_timeline(
    world: World, rack: Rack, rng: np.random.Generator
) -> tuple[list[tuple[str, int]], int | None]:
    """Ordered (status, ts) events and the regression timestamp, if any."""
    d = world.cfg.durations
    vendor = world.vendor_by_code[rack.vendor_code]
    site = world.site_by_code[rack.site_code]
    rack_type = world.rack_type_by_code[rack.rack_type_code]

    order_month = world.month(date.fromordinal(ordinal_of(rack.ordered_at)))
    lead_mult = 1.0
    if vendor.degrade_from_month is not None and order_month >= vendor.degrade_from_month:
        lead_mult = vendor.degrade_lead_multiplier

    lead = lognormal_seconds(
        rng, vendor.lead_mean_days * lead_mult, vendor.lead_std_days * lead_mult
    )
    transit = lognormal_seconds(rng, vendor.transit_mean_days, vendor.transit_std_days)
    install = lognormal_seconds(rng, site.install_mean_days, site.install_std_days)
    burn_mult = d.gpu_burn_in_multiplier if rack_type.is_gpu else 1.0
    burn = lognormal_seconds(rng, d.burn_in_mean_days * burn_mult, d.burn_in_std_days * burn_mult)

    ordered = rack.ordered_at
    shipped = ordered + lead
    received = shipped + transit
    cabled = received + install
    burn_start = cabled + int(burn * rng.uniform(0.02, 0.1))
    events = [
        ("ORDERED", ordered),
        ("VENDOR_CONFIRMED", ordered + int(lead * rng.uniform(0.05, 0.25))),
        ("SHIPPED", shipped),
        ("RECEIVED", received),
        ("RACKED", received + int(install * rng.uniform(0.3, 0.6))),
        ("CABLED", cabled),
        ("BURN_IN", burn_start),
    ]

    regression_at: int | None = None
    production = cabled + burn
    if rng.random() < world.cfg.defects.regression_rate:
        regression_at = burn_start + int(burn * rng.uniform(0.3, 0.7))
        retest = regression_at + lognormal_seconds(rng, d.retest_mean_days, d.retest_std_days)
        events += [("CABLED", regression_at), ("BURN_IN", retest)]
        production = retest + burn
    events.append(("PRODUCTION", production))

    if rng.random() < d.early_decommission_rate:
        lo, hi = d.early_decommission_days
        lifespan = int(rng.uniform(lo, hi) * SECONDS_PER_DAY)
    else:
        lo_y, hi_y = d.lifespan_years
        lifespan = int(rng.uniform(lo_y, hi_y) * 365 * SECONDS_PER_DAY)
    decom_requested = production + lifespan
    decommissioned = decom_requested + lognormal_seconds(
        rng, d.decommission_mean_days, d.decommission_std_days
    )
    events += [("DECOMMISSION_REQUESTED", decom_requested), ("DECOMMISSIONED", decommissioned)]
    return events, regression_at


def format_event_at(ts: int, style: int, utc_offset_minutes: int) -> str:
    if style == 0:
        return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if style == 1:
        tz = timezone(timedelta(minutes=utc_offset_minutes))
        return datetime.fromtimestamp(ts, tz).isoformat()
    return str(ts * 1000)


def simulate_rack(world: World, rack: Rack) -> RackResult:
    """True milestones plus every event the source will emit for this rack, ever."""
    rng = stream_rng(world.seed, STREAM_RACK, rack.idx)
    events, regression_at = _true_timeline(world, rack, rng)

    milestones: dict[str, int] = {}
    for status, ts in events:
        milestones.setdefault(status, ts)
    result = RackResult(rack=rack, milestones=milestones, regression_at=regression_at)

    defects = world.cfg.defects
    vendor = world.vendor_by_code[rack.vendor_code]
    site = world.site_by_code[rack.site_code]
    techs = world.cfg.durations.technicians_per_site

    def emit(
        status: str,
        event_at: int,
        style: int,
        recorded_by: str | None,
        ingest: int,
        event_id: str,
        flags: int,
    ) -> None:
        if ingest == world.missing_ordinal:
            ingest += 1
        v2 = (
            rack.vendor_code == defects.schema_drift_vendor and ingest >= world.schema_drift_ordinal
        )
        payload: dict[str, object] = {
            "event_id": event_id,
            "rack_id": rack.rack_id,
            "site_code": rack.site_code,
            "vendor_code": rack.vendor_code,
            ("rack_model" if v2 else "rack_type_code"): rack.rack_type_code,
            "status": status,
            "event_at": format_event_at(event_at, style, site.utc_offset_minutes),
            "recorded_by": recorded_by,
            "payload_version": 2 if v2 else 1,
        }
        if v2:
            flags |= SCHEMA_V2
        line = json.dumps(payload, separators=(",", ":"))
        result.emissions.append(Emission(ingest, event_at, line, flags, rack.vendor_code))

    def new_id() -> str:
        return str(uuid.UUID(bytes=rng.bytes(16), version=4))

    def recorder(flags: int) -> tuple[str | None, int]:
        if rng.random() < defects.null_recorded_by_rate:
            return None, flags | NULL_RECORDED_BY
        return f"tech-{rack.site_code}-{int(rng.integers(1, techs + 1)):02d}", flags

    for i, (status, ts) in enumerate(events):
        event_id = new_id()
        recorded_by, flags = recorder(0)

        style = 0
        if rng.random() < defects.format_drift_rate:
            style = 1 + int(rng.integers(0, 2))
            flags |= FORMAT_DRIFT

        delay = 0
        if rng.random() < defects.very_late_rate:
            delay = int(
                rng.integers(defects.very_late_delay_days[0], defects.very_late_delay_days[1] + 1)
            )
            flags |= VERY_LATE
        elif rng.random() < vendor.late_rate:
            delay = int(rng.integers(defects.late_delay_days[0], defects.late_delay_days[1] + 1))
            flags |= LATE

        event_at = ts
        if rng.random() < defects.future_dated_rate:
            event_at = ts + defects.future_dated_offset_days * SECONDS_PER_DAY
            flags |= FUTURE_DATED

        ingest = ordinal_of(ts) + delay
        emit(status, event_at, style, recorded_by, ingest, event_id, flags)

        if rng.random() < defects.duplicate_rate:
            lo, hi = defects.duplicate_delay_days
            dup_ingest = ingest + int(rng.integers(lo, hi + 1))
            emit(status, event_at, style, recorded_by, dup_ingest, event_id, flags | DUPLICATE)

        # Spurious event that skips at least one lifecycle step (e.g. RECEIVED -> PRODUCTION).
        # It lands within 2 days, before the next true event, so it is not pushed past the
        # horizon when the next event is years away (PRODUCTION -> DECOMMISSION_REQUESTED).
        current = STATUS_INDEX[status]
        if current + 2 < len(STATUSES) and rng.random() < defects.invalid_transition_rate:
            bogus = STATUSES[int(rng.integers(current + 2, len(STATUSES)))]
            next_gap = events[i + 1][1] - ts if i + 1 < len(events) else SECONDS_PER_DAY
            gap = min(next_gap, 2 * SECONDS_PER_DAY)
            bogus_at = ts + max(1, int(gap * rng.uniform(0.2, 0.8)))
            bogus_rec, bogus_flags = recorder(INVALID_TRANSITION)
            emit(bogus, bogus_at, 0, bogus_rec, ordinal_of(bogus_at), new_id(), bogus_flags)

    return result
