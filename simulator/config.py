"""Typed loader for simulator/config.yaml."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG = Path(__file__).with_name("config.yaml")


@dataclass(frozen=True)
class SiteCfg:
    code: str
    name: str
    region: str
    country: str
    utc_offset_minutes: int
    install_mean_days: float
    install_std_days: float


@dataclass(frozen=True)
class VendorCfg:
    code: str
    name: str
    region: str
    weight: float
    lead_mean_days: float
    lead_std_days: float
    transit_mean_days: float
    transit_std_days: float
    late_rate: float
    sla_days: int
    tier: str
    degrade_from_month: int | None = None
    degrade_lead_multiplier: float = 1.0


@dataclass(frozen=True)
class RackTypeCfg:
    code: str
    description: str
    power_kw: int
    is_gpu: bool
    weight: float


@dataclass(frozen=True)
class ContractChange:
    vendor: str
    month: int
    field: str  # "sla_days" or "tier"
    value: int | str


@dataclass(frozen=True)
class ArrivalsCfg:
    base_daily_orders: float
    q4_lift: float
    expansion_from_month: int
    expansion_factor: float


@dataclass(frozen=True)
class DurationsCfg:
    burn_in_mean_days: float
    burn_in_std_days: float
    gpu_burn_in_multiplier: float
    retest_mean_days: float
    retest_std_days: float
    decommission_mean_days: float
    decommission_std_days: float
    lifespan_years: tuple[float, float]
    early_decommission_rate: float
    early_decommission_days: tuple[int, int]
    technicians_per_site: int


@dataclass(frozen=True)
class DefectsCfg:
    duplicate_rate: float
    duplicate_delay_days: tuple[int, int]
    late_delay_days: tuple[int, int]
    very_late_rate: float
    very_late_delay_days: tuple[int, int]
    out_of_order_file_rate: float
    format_drift_rate: float
    schema_drift_from: date
    schema_drift_vendor: str
    invalid_transition_rate: float
    regression_rate: float
    future_dated_rate: float
    future_dated_offset_days: int
    null_recorded_by_rate: float
    missing_day: date


@dataclass(frozen=True)
class SimConfig:
    seed: int
    history_start: date
    capacity_range: tuple[int, int]
    arrivals: ArrivalsCfg
    durations: DurationsCfg
    defects: DefectsCfg
    sites: tuple[SiteCfg, ...]
    vendors: tuple[VendorCfg, ...]
    rack_types: tuple[RackTypeCfg, ...]
    contract_changes: tuple[ContractChange, ...]
    sha256: str  # of the config file bytes; daily mode refuses state from another config


def _with_pairs(raw: dict[str, Any], *keys: str) -> dict[str, Any]:
    out = dict(raw)
    for key in keys:
        lo, hi = out[key]
        out[key] = (lo, hi)
    return out


def load_config(path: Path = DEFAULT_CONFIG) -> SimConfig:
    data = path.read_bytes()
    raw = yaml.safe_load(data)
    return SimConfig(
        seed=int(raw["seed"]),
        history_start=raw["history_start"],
        capacity_range=_with_pairs(raw, "capacity_range")["capacity_range"],
        arrivals=ArrivalsCfg(**raw["arrivals"]),
        durations=DurationsCfg(
            **_with_pairs(raw["durations"], "lifespan_years", "early_decommission_days")
        ),
        defects=DefectsCfg(
            **_with_pairs(
                raw["defects"], "duplicate_delay_days", "late_delay_days", "very_late_delay_days"
            )
        ),
        sites=tuple(SiteCfg(**s) for s in raw["sites"]),
        vendors=tuple(VendorCfg(**v) for v in raw["vendors"]),
        rack_types=tuple(RackTypeCfg(**t) for t in raw["rack_types"]),
        contract_changes=tuple(ContractChange(**c) for c in raw["contract_changes"]),
        sha256=hashlib.sha256(data).hexdigest(),
    )
