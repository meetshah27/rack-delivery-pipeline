"""Lambda handlers end to end against fakes, and the Step Functions definition's structure."""

from __future__ import annotations

import gzip
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from orchestration import audit_writer, copy_to_redshift, land_to_raw, simulate_daily
from orchestration.redshift_data import RedshiftData, RedshiftDataError
from orchestration.tests.fakes import FakeCloudWatch, FakeRedshiftDataClient, FakeS3
from simulator.config import load_config
from simulator.generator import run_full

ROOT = Path(__file__).resolve().parents[2]
BUCKET = "bucket"
ROLE = "arn:aws:iam::123456789012:role/copy"
NOW = datetime(2024, 11, 1, 6, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def history(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """20 days of simulator output, like `make sim DAYS=20`."""
    out = tmp_path_factory.mktemp("sim")
    cfg = load_config()
    run_full(cfg, cfg.seed, cfg.history_start, cfg.history_start + timedelta(days=19), out)
    return out


@pytest.fixture()
def cloud(history: Path) -> tuple[FakeS3, RedshiftData]:
    s3 = FakeS3()
    s3.upload_tree(history, "landing")
    s3.upload_tree(history, "reference")
    return s3, RedshiftData(FakeRedshiftDataClient(s3), "wg", "dev")


def _load(s3: FakeS3, rd: RedshiftData, run_id: str) -> dict[str, Any]:
    landed = land_to_raw.land(s3, rd, BUCKET, run_id, NOW)
    copied = copy_to_redshift.copy(s3, rd, BUCKET, ROLE, run_id, landed["files_key"], None)
    return {"land": landed, "copy": copied}


def _landed_lines(history: Path) -> dict[str, int]:
    return {
        path.parent.name.removeprefix("ingest_date="): len(
            gzip.decompress(path.read_bytes()).splitlines()
        )
        for path in (history / "landing" / "rack_events").rglob("*.jsonl.gz")
    }


def test_raw_counts_match_landed_lines_per_date(
    cloud: tuple[FakeS3, RedshiftData], history: Path
) -> None:
    s3, rd = cloud
    first = _load(s3, rd, "run-1")
    lines = _landed_lines(history)
    assert first["land"]["rows_landed"] == first["copy"]["rows_loaded_raw"] == sum(lines.values())
    per_date = {
        row["d"]: row["n"]
        for row in rd.query(
            "select cast(_ingest_date as varchar) as d, count(*) as n "
            "from raw.rack_events group by 1"
        )
    }
    assert per_date == {d: n for d, n in lines.items() if n}


def test_loading_again_copies_nothing(cloud: tuple[FakeS3, RedshiftData]) -> None:
    s3, rd = cloud
    _load(s3, rd, "run-1")
    counts = rd.query(
        "select (select count(*) from raw.rack_events) as events, "
        "(select count(*) from raw.sites) as sites, "
        "(select count(*) from ops.file_manifest) as manifest"
    )
    second = _load(s3, rd, "run-2")
    assert second["land"]["files"] == 0
    assert second["copy"]["rows_loaded_raw"] == 0
    assert (
        rd.query(
            "select (select count(*) from raw.rack_events) as events, "
            "(select count(*) from raw.sites) as sites, "
            "(select count(*) from ops.file_manifest) as manifest"
        )
        == counts
    )


def test_changed_reference_file_replaces_the_table(cloud: tuple[FakeS3, RedshiftData]) -> None:
    s3, rd = cloud
    _load(s3, rd, "run-1")
    sites = s3.objects["reference/sites.csv"].decode().splitlines()
    s3.objects["reference/sites.csv"] = ("\n".join(sites[:-1]) + "\n").encode()
    second = _load(s3, rd, "run-2")
    assert second["land"]["files"] == 1
    assert rd.query("select count(*) as n from raw.sites")[0]["n"] == len(sites) - 2


def test_failed_batch_rolls_back_and_raises(cloud: tuple[FakeS3, RedshiftData]) -> None:
    _, rd = cloud
    with pytest.raises(RedshiftDataError):
        rd.batch(
            [
                "insert into ops.file_manifest (source_file) values ('x')",
                "select * from no_such_table",
            ]
        )
    assert rd.query("select count(*) as n from ops.file_manifest")[0]["n"] == 0


def test_simulate_daily_continues_from_state(history: Path, tmp_path: Path) -> None:
    s3 = FakeS3()
    s3.put_object(BUCKET, simulate_daily.STATE_KEY, (history / "_state.parquet").read_bytes())
    last = load_config().history_start + timedelta(days=19)
    target = last + timedelta(days=2)

    result = simulate_daily.simulate(s3, BUCKET, target, tmp_path / "a")
    assert result["simulated"] == [(last + timedelta(days=1)).isoformat(), target.isoformat()]
    assert f"landing/rack_events/ingest_date={target.isoformat()}/part-0000.jsonl.gz" in s3.objects
    assert simulate_daily.GROUND_TRUTH_KEY in s3.objects

    again = simulate_daily.simulate(s3, BUCKET, target, tmp_path / "b")
    assert again["simulated"] == [] and again["ground_truth_key"] is None

    with pytest.raises(RuntimeError, match="behind"):
        simulate_daily.simulate(s3, BUCKET, target + timedelta(days=8), tmp_path / "c")


def test_audit_row_and_metrics(cloud: tuple[FakeS3, RedshiftData]) -> None:
    s3, rd = cloud
    state = _load(s3, rd, "run-1")
    fake = rd.client
    fake.con.execute(
        "create table ops.quarantine_rack_events as "
        "select _ingest_date from raw.rack_events limit 7"
    )
    fake.con.execute(
        "create table intermediate.int_rack_events_validated as "
        "select _ingest_date, 45 as late_by_days from raw.rack_events limit 3"
    )
    fake.con.execute(
        "create table sys_serverless_usage as select "
        "timestamp '2024-11-01 05:01:00' as start_time, "
        "timestamp '2024-11-01 05:02:00' as end_time, "
        "7200 as charged_seconds"
    )
    s3.put_object(
        BUCKET,
        "runs/run-1/dbt/run_results.json",
        json.dumps(
            {
                "results": [
                    {"unique_id": "test.rack_delivery.a", "status": "pass"},
                    {"unique_id": "test.rack_delivery.b", "status": "warn"},
                    {
                        "unique_id": "model.rack_delivery.fct_rack_status_event",
                        "status": "success",
                        "adapter_response": {"rows_affected": 123},
                    },
                ]
            }
        ).encode(),
    )
    cloudwatch = FakeCloudWatch()
    event = {
        "run_id": "run-1",
        "started_at": "2024-11-01T05:00:00Z",
        "status": "SUCCEEDED",
        "state": state,
    }

    row = audit_writer.write_audit(s3, rd, cloudwatch, BUCKET, 0.375, event, NOW)
    audit_writer.write_audit(s3, rd, cloudwatch, BUCKET, 0.375, event, NOW)  # idempotent

    assert rd.query("select count(*) as n from ops.pipeline_run_audit")[0]["n"] == 1
    assert row["rows_quarantined"] == 7 and row["late_event_count"] == 3
    assert (row["dbt_tests_passed"], row["dbt_tests_warned"], row["dbt_tests_failed"]) == (1, 1, 0)
    assert row["rows_fct_event_merged"] == 123
    assert row["redshift_charged_seconds"] == 7200 and row["est_cost_usd"] == 0.75
    names = {m["MetricName"] for m in cloudwatch.metrics}
    assert names == {
        "RowsLoaded",
        "RowsQuarantined",
        "QuarantineRate",
        "RunDurationSeconds",
        "EstCostUsd",
    }


def test_audit_for_a_run_that_failed_early(cloud: tuple[FakeS3, RedshiftData]) -> None:
    s3, rd = cloud
    rd.client.con.execute(
        "create table sys_serverless_usage (start_time timestamp, end_time timestamp, "
        "charged_seconds bigint)"
    )
    event = {
        "run_id": "run-x",
        "started_at": "2024-11-01T05:00:00Z",
        "status": "FAILED",
        "state": {"error": {"Error": "Lambda.Unknown", "Cause": "boom"}},
    }
    row = audit_writer.write_audit(s3, rd, FakeCloudWatch(), BUCKET, 0.375, event, NOW)
    assert row["status"] == "FAILED" and row["error"] == "Lambda.Unknown: boom"
    assert row["rows_landed"] is None and row["dbt_tests_passed"] is None


# ---------------------------------------------------------------- state machine


def _render_asl() -> dict[str, Any]:
    template = (ROOT / "orchestration" / "state_machine.asl.json").read_text()
    values = {
        "quicksight_dataset_ids": '["ds_delivery", "ds_throughput"]',
        "account_id": "123456789012",
    }
    rendered = re.sub(
        r"\$\{(\w+)\}", lambda m: values.get(m.group(1), f"arn:aws:x:{m.group(1)}"), template
    )
    result: dict[str, Any] = json.loads(rendered)
    return result


def test_state_machine_definition_is_well_formed() -> None:
    asl = _render_asl()
    states = asl["States"]
    assert asl["StartAt"] in states

    def targets(state: dict[str, Any]) -> list[str]:
        found = [state[k] for k in ("Next", "Default") if k in state]
        found += [c["Next"] for c in state.get("Catch", [])]
        found += [c["Next"] for c in state.get("Choices", [])]
        return found

    reachable, frontier = set(), [asl["StartAt"]]
    while frontier:
        name = frontier.pop()
        if name in reachable:
            continue
        reachable.add(name)
        for target in targets(states[name]):
            assert target in states, f"{name} -> missing state {target}"
            frontier.append(target)
    assert reachable == set(states), f"unreachable: {set(states) - reachable}"

    for name, state in states.items():
        if state["Type"] in ("Succeed", "Fail"):
            continue
        assert "Next" in state or state.get("End"), name
        if state["Type"] in ("Task", "Map") and name != "NotifyFailure":
            assert state.get("Catch"), f"{name} has no Catch: a failure would skip SNS"
    assert {"Done", "Failed"} <= set(states)
    assert states["NotifyFailure"]["Resource"] == "arn:aws:states:::sns:publish"
