"""`make qs-snapshot`: export every dashboard sheet as PDF with StartDashboardSnapshotJob,
convert the pages to PNG in docs/img/, and keep the PDFs in evidence/.

Snapshot jobs render as an anonymous user, which only tag-based row-level security can
serve; ds_delivery uses user-based RLS (verified by ops/rls_check.py). The script therefore
disables RLS on ds_delivery for the duration of the job and restores it in `finally`.
"""

from __future__ import annotations

import time
import urllib.request
import uuid
from typing import Any

import pypdfium2 as pdfium

from ops.aws import ROOT, account_id, session
from ops.quicksight_deploy import DASHBOARD_ID

IMG = ROOT / "docs" / "img"
EVIDENCE = ROOT / "evidence"
RLS_DATASET = "ds_delivery"
SHEETS = ["executive", "cycle-time", "sla-breaches", "pipeline-health"]


def _set_rls(qs: Any, account: str, status: str) -> None:
    ds = qs.describe_data_set(AwsAccountId=account, DataSetId=RLS_DATASET)["DataSet"]
    rls = ds.get("RowLevelPermissionDataSet")
    if not rls or rls["Status"] == status:
        return
    update = {key: ds[key] for key in ("Name", "PhysicalTableMap", "LogicalTableMap", "ImportMode")}
    qs.update_data_set(
        AwsAccountId=account,
        DataSetId=RLS_DATASET,
        **update,
        RowLevelPermissionDataSet={**rls, "Status": status},
    )


def snapshot() -> None:
    qs = session().client("quicksight")
    account, region = account_id(), session().region_name
    IMG.mkdir(parents=True, exist_ok=True)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    _set_rls(qs, account, "DISABLED")
    try:
        job_id = f"snapshot-{uuid.uuid4().hex[:12]}"
        qs.start_dashboard_snapshot_job(
            AwsAccountId=account,
            DashboardId=DASHBOARD_ID,
            SnapshotJobId=job_id,
            UserConfiguration={"AnonymousUsers": [{}]},
            SnapshotConfiguration={
                "FileGroups": [
                    {
                        "Files": [
                            {
                                "FormatType": "PDF",
                                "SheetSelections": [
                                    {"SheetId": sheet, "SelectionScope": "ALL_VISUALS"}
                                ],
                            }
                        ]
                    }
                    for sheet in SHEETS
                ]
            },
        )
        while True:
            job = qs.describe_dashboard_snapshot_job(
                AwsAccountId=account, DashboardId=DASHBOARD_ID, SnapshotJobId=job_id
            )
            if job["JobStatus"] in ("COMPLETED", "FAILED"):
                break
            time.sleep(5)
        result = qs.describe_dashboard_snapshot_job_result(
            AwsAccountId=account, DashboardId=DASHBOARD_ID, SnapshotJobId=job_id
        )
        if job["JobStatus"] == "FAILED":
            raise SystemExit(f"snapshot failed: {result.get('ErrorInfo')}")
    finally:
        _set_rls(qs, account, "ENABLED")

    groups = result["Result"]["AnonymousUsers"][0]["FileGroups"]
    for sheet, group in zip(SHEETS, groups, strict=True):
        pdf_path = EVIDENCE / f"dashboard_{sheet}.pdf"
        with urllib.request.urlopen(group["S3Results"][0]["S3Uri"]) as response:
            pdf_path.write_bytes(response.read())
        for number, page in enumerate(pdfium.PdfDocument(str(pdf_path)), start=1):
            png = IMG / f"dashboard_{sheet}{'' if number == 1 else f'_{number}'}.png"
            page.render(scale=2).to_pil().save(png)
            print(f"wrote {png.relative_to(ROOT)}")
    print(f"region {region}: review each PNG for titles, data and sensible axes")


if __name__ == "__main__":
    snapshot()
