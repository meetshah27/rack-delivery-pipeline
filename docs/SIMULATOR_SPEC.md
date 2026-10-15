# SIMULATOR SPEC

Python package `simulator/` generating realistic rack lifecycle events with deliberate defects. All randomness flows from one seed in `config.yaml` so output is reproducible.

## CLI

```
uv run python -m simulator.generator \
  --start-date 2024-10-01 --end-date 2026-09-30 \
  --out ./out --seed 42
uv run python -m simulator.generator --daily --date 2026-10-15 --out ./out
```

`--daily` mode generates only events whose *ingest date* is the given date, using persisted rack state in `./out/_state.parquet`, so scheduled runs continue the same racks over time.

## Output

`out/landing/rack_events/ingest_date=YYYY-MM-DD/part-0000.jsonl.gz`
`out/landing/vendor_contracts/ingest_date=YYYY-MM-DD/vendor_contracts.csv`
`out/reference/sites.csv`, `out/reference/rack_types.csv`
`out/_ground_truth/racks.parquet` (true milestone timestamps per rack, never uploaded; used by tests to verify the pipeline recovers truth)

## Entities

These exact codes are fixed. The P3 golden question set references them, so do not rename them.

Sites (20). Each has `capacity_racks` drawn from 800 to 4000 with the seed.

| Region | Site codes |
|---|---|
| us-west | SEA-01, SEA-02, PDX-01, SJC-01, PHX-01, LAS-01 |
| us-east | IAD-01, IAD-02, ATL-01, CMH-01, NYC-01 |
| eu-central | FRA-01, FRA-02, AMS-01, DUB-01 |
| ap-south | BOM-01, BOM-02, HYD-01, MAA-01, DEL-01 |

Vendors (6), with distinct personalities:

| Code | Name | Lead time mean (days) | Lead std | Late-event rate | Initial SLA days | Initial tier | Notes |
|---|---|---|---|---|---|---|---|
| V01 | Northwind Racks | 21 | 4 | 1% | 40 | gold | reliable |
| V02 | Cascade Systems | 28 | 10 | 3% | 50 | silver | high variance |
| V03 | Halcyon Hardware | 18 | 3 | 1% | 45 | gold | fast, SLA tightened mid-history |
| V04 | Ironbark Supply | 35 | 8 | 8% | 60 | bronze | sloppy event reporting |
| V05 | Meridian Compute | 25 | 6 | 2% | 45 | gold | degrades after month 15 |
| V06 | Summit Integration | 30 | 5 | 2% | 50 | silver | |

Rack types (8). The GPU types have burn-in 2x longer.

| Code | Description | power_kw | is_gpu |
|---|---|---|---|
| CPU-42U-STD | Standard compute 42U | 12 | false |
| CPU-48U-STD | Standard compute 48U | 15 | false |
| CPU-HPC | High-density compute | 25 | false |
| STO-HD | High-density storage | 10 | false |
| NET-SPINE | Network spine | 8 | false |
| NET-LEAF | Network leaf | 6 | false |
| GPU-8X | 8-GPU training rack | 45 | true |
| GPU-4X | 4-GPU inference rack | 30 | true |
- Racks: order arrivals follow a Poisson process with seasonal lift in Q4 and a step-up at month 12 (capacity expansion).

## Durations

Stage durations drawn from lognormal distributions parameterized per vendor (vendor_lead, transit) and per site (install) and per rack type (burn_in). Store parameters in `config.yaml`. Production lifespan before decommission request: 3 to 5 years, so only early racks decommission within the window; also generate a small set of early decommissions (2%).

## Deliberate defects

| Defect | Rate | Implementation |
|---|---|---|
| Duplicate event_id | 1.5% | Same event emitted again in a later ingest file |
| Late arrival | per vendor table above | Event's ingest_date = event_at date + U(3,10) days; 0.2% are 30 to 60 days late |
| Out-of-order within file | 5% of files | Shuffle lines |
| Timestamp format drift | 10% | Mix of `2025-03-04T10:22:00Z`, `2025-03-04T02:22:00-08:00`, epoch millis as string |
| Schema drift | from 2026-03-01 onward | payload_version 2 renames `rack_type_code` to `rack_model` for vendor V04 only |
| Invalid transition | 0.3% | e.g. `RECEIVED` directly to `PRODUCTION` |
| Legit regression | 1% | `BURN_IN` back to `CABLED` (failed burn-in), then forward again |
| Future-dated | 0.05% | event_at a year in the future (clock bug) |
| Null recorded_by | 4% | |
| Missing day | 1 day in history | No file landed; next day carries double volume |

## Vendor contract changes (SCD2 fuel)

- V03 SLA tightens from 45 to 35 days at month 9.
- V05 tier changes from `gold` to `silver` at month 16.
- V02 SLA loosens from 50 to 55 days at month 20.

## Tests (`simulator/tests/`)

- Same seed produces byte-identical output.
- Defect rates within ±20% relative of configured rates on a full run.
- Every ground-truth rack has a monotonic milestone sequence ignoring regressions.
- Daily mode across 3 consecutive days matches a full run over the same 3 days (state continuity).
