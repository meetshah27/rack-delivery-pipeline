"""`make evidence`: artifacts that outlive the AWS teardown.

- evidence/pipeline_run_audit.csv: every pipeline run
- evidence/cost_report.csv: cost per run for the last 7 days
- docs/img/metric_*.png: CloudWatch metric charts via GetMetricWidgetImage
- docs/img/state_machine.dot (+ .png when Graphviz `dot` is installed): the pipeline graph
  rendered from orchestration/state_machine.asl.json
"""

from __future__ import annotations

import csv
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ops.aws import ROOT, redshift, session, tf_outputs

EVIDENCE = ROOT / "evidence"
IMG = ROOT / "docs" / "img"
METRICS: list[tuple[str, str, str, list[str]]] = [
    ("RackPipeline", "QuarantineRate", "Quarantine rate per run", []),
    ("RackPipeline", "RunDurationSeconds", "Run duration (seconds)", []),
    ("RackPipeline", "EstCostUsd", "Estimated cost per run (USD)", []),
    ("RackPipeline", "RowsLoaded", "Rows loaded per run", []),
]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["empty"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {path.relative_to(ROOT)} ({len(rows)} rows)")


def export_tables() -> None:
    rd = redshift()
    _write_csv(
        EVIDENCE / "pipeline_run_audit.csv",
        rd.query("select * from ops.pipeline_run_audit order by started_at"),
    )
    _write_csv(
        EVIDENCE / "cost_report.csv",
        rd.query(
            "select run_id, started_at, status, redshift_charged_seconds, est_cost_usd "
            "from ops.pipeline_run_audit where started_at >= dateadd(day, -7, getdate()) "
            "order by started_at"
        ),
    )


def metric_charts() -> None:
    cloudwatch = session().client("cloudwatch")
    IMG.mkdir(parents=True, exist_ok=True)
    state_machine = tf_outputs()["state_machine_arn"]
    charts = [
        *METRICS,
        ("AWS/States", "ExecutionsFailed", "Failed executions", ["StateMachineArn", state_machine]),
    ]
    for namespace, metric, title, dimensions in charts:
        widget = {
            "title": title,
            "view": "timeSeries",
            "stat": "Maximum",
            "period": 3600,
            "start": "-P14D",
            "width": 900,
            "height": 300,
            "region": session().region_name,
            "metrics": [[namespace, metric, *dimensions]],
        }
        image = cloudwatch.get_metric_widget_image(MetricWidget=json.dumps(widget))[
            "MetricWidgetImage"
        ]
        path = IMG / f"metric_{metric}.png"
        path.write_bytes(image)
        print(f"wrote {path.relative_to(ROOT)}")


def state_machine_graph() -> None:
    asl = json.loads((ROOT / "orchestration" / "state_machine.asl.json").read_text())
    lines = [
        "digraph pipeline {",
        "  rankdir=TB; node [shape=box, style=rounded, fontname=Helvetica];",
    ]
    for name, state in asl["States"].items():
        shape = {"Succeed": "doublecircle", "Fail": "octagon", "Map": "box3d"}.get(
            state["Type"], "box"
        )
        lines.append(f'  "{name}" [shape={shape}];')
        if "Next" in state:
            lines.append(f'  "{name}" -> "{state["Next"]}";')
        for catch in state.get("Catch", []):
            lines.append(
                f'  "{name}" -> "{catch["Next"]}" [style=dashed, color=red, label="error"];'
            )
    lines.append("}")
    dot_path = IMG / "state_machine.dot"
    IMG.mkdir(parents=True, exist_ok=True)
    dot_path.write_text("\n".join(lines) + "\n")
    if shutil.which("dot"):
        subprocess.run(
            ["dot", "-Tpng", str(dot_path), "-o", str(IMG / "state_machine.png")], check=True
        )
        print("wrote docs/img/state_machine.png")
    else:
        print("wrote docs/img/state_machine.dot (install Graphviz to render the PNG)")


def main() -> None:
    export_tables()
    metric_charts()
    state_machine_graph()


if __name__ == "__main__":
    main()
