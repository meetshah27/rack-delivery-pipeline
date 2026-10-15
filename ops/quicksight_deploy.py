"""`make qs-deploy`: create or update the QuickSight analysis and dashboard from
dashboards/rack_delivery_definition.json, publish the new dashboard version, and grant the
owner (QS_OWNER_ARN) full permissions. Rerunning only adds a dashboard version.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from ops.aws import ROOT, account_id, session

ANALYSIS_ID = DASHBOARD_ID = "rack-delivery"
NAME = "Rack delivery"
DEFINITION = ROOT / "dashboards" / "rack_delivery_definition.json"

ANALYSIS_ACTIONS = [
    "quicksight:RestoreAnalysis",
    "quicksight:UpdateAnalysisPermissions",
    "quicksight:DeleteAnalysis",
    "quicksight:DescribeAnalysisPermissions",
    "quicksight:QueryAnalysis",
    "quicksight:DescribeAnalysis",
    "quicksight:UpdateAnalysis",
]
DASHBOARD_ACTIONS = [
    "quicksight:DescribeDashboard",
    "quicksight:ListDashboardVersions",
    "quicksight:UpdateDashboardPermissions",
    "quicksight:QueryDashboard",
    "quicksight:UpdateDashboard",
    "quicksight:DeleteDashboard",
    "quicksight:DescribeDashboardPermissions",
    "quicksight:UpdateDashboardPublishedVersion",
]


def definition(account: str, region: str) -> dict[str, Any]:
    text = DEFINITION.read_text().replace("${account_id}", account).replace("${region}", region)
    result: dict[str, Any] = json.loads(text)
    return result


def _wait(describe: Any, key: str) -> dict[str, Any]:
    while True:
        item: dict[str, Any] = describe()[key]
        status = item.get("Status") or item["Version"]["Status"]
        if status.endswith("SUCCESSFUL"):
            return item
        if status.endswith("FAILED"):
            errors = item.get("Errors") or item.get("Version", {}).get("Errors")
            raise SystemExit(f"{key} {status}: {json.dumps(errors, indent=2)}")
        time.sleep(3)


def deploy() -> None:
    qs = session().client("quicksight")
    account, region = account_id(), session().region_name
    owner = os.environ["QS_OWNER_ARN"]
    body = definition(account, region)
    common = {"AwsAccountId": account, "Name": NAME, "Definition": body}

    exists = qs.list_analyses(AwsAccountId=account)["AnalysisSummaryList"]
    if any(a["AnalysisId"] == ANALYSIS_ID for a in exists):
        qs.update_analysis(AnalysisId=ANALYSIS_ID, **common)
    else:
        qs.create_analysis(
            AnalysisId=ANALYSIS_ID,
            **common,
            Permissions=[{"Principal": owner, "Actions": ANALYSIS_ACTIONS}],
        )
    _wait(lambda: qs.describe_analysis(AwsAccountId=account, AnalysisId=ANALYSIS_ID), "Analysis")

    dashboards = qs.list_dashboards(AwsAccountId=account)["DashboardSummaryList"]
    if any(d["DashboardId"] == DASHBOARD_ID for d in dashboards):
        qs.update_dashboard(DashboardId=DASHBOARD_ID, **common)
    else:
        qs.create_dashboard(
            DashboardId=DASHBOARD_ID,
            **common,
            Permissions=[{"Principal": owner, "Actions": DASHBOARD_ACTIONS}],
            DashboardPublishOptions={"AdHocFilteringOption": {"AvailabilityStatus": "DISABLED"}},
        )
    dashboard = _wait(
        lambda: qs.describe_dashboard(AwsAccountId=account, DashboardId=DASHBOARD_ID), "Dashboard"
    )
    version = dashboard["Version"]["VersionNumber"]
    qs.update_dashboard_published_version(
        AwsAccountId=account, DashboardId=DASHBOARD_ID, VersionNumber=version
    )
    print(f"dashboard {DASHBOARD_ID} published, version {version}")


if __name__ == "__main__":
    deploy()
