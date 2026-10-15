"""`make rls-check`: verify row-level security on ds_delivery.

1. Registers the two reader users from dbt/seeds/rls_users.csv (emails from
   RLS_READER_EMAIL_US_WEST / RLS_READER_EMAIL_AP_SOUTH) and gives them view access.
2. Prints an embed URL per reader: opening each in a browser shows only that reader's region.
3. Asserts, without a browser, that RLS is enabled on ds_delivery with the rules dataset, and
   that ops.rls_site_region maps each reader to exactly their region and the owner to all.
"""

from __future__ import annotations

import csv
import os

from ops.aws import ROOT, account_id, redshift, session
from ops.quicksight_deploy import DASHBOARD_ID

SEED = ROOT / "dbt" / "seeds" / "rls_users.csv"
VIEW_ACTIONS = [
    "quicksight:DescribeDashboard",
    "quicksight:QueryDashboard",
    "quicksight:ListDashboardVersions",
]


def main() -> None:
    qs = session().client("quicksight")
    account, region = account_id(), session().region_name
    readers = list(csv.DictReader(SEED.open()))
    existing = {
        u["UserName"]: u
        for u in qs.list_users(AwsAccountId=account, Namespace="default")["UserList"]
    }

    for reader in readers:
        name, site_region = reader["user_name"], reader["site_region"]
        if name not in existing:
            email = os.environ[f"RLS_READER_EMAIL_{site_region.upper().replace('-', '_')}"]
            existing[name] = qs.register_user(
                AwsAccountId=account,
                Namespace="default",
                IdentityType="QUICKSIGHT",
                UserRole="READER",
                UserName=name,
                Email=email,
            )["User"]
        arn = existing[name]["Arn"]
        qs.update_dashboard_permissions(
            AwsAccountId=account,
            DashboardId=DASHBOARD_ID,
            GrantPermissions=[{"Principal": arn, "Actions": VIEW_ACTIONS}],
        )
        url = qs.generate_embed_url_for_registered_user(
            AwsAccountId=account,
            UserArn=arn,
            SessionLifetimeInMinutes=60,
            ExperienceConfiguration={"Dashboard": {"InitialDashboardId": DASHBOARD_ID}},
        )["EmbedUrl"]
        print(f"{name} ({site_region}): {url}")

    ds = qs.describe_data_set(AwsAccountId=account, DataSetId="ds_delivery")["DataSet"]
    rls = ds.get("RowLevelPermissionDataSet") or {}
    expected_arn = f"arn:aws:quicksight:{region}:{account}:dataset/ds_rls_permissions"
    assert rls.get("Status") == "ENABLED", "RLS is not enabled on ds_delivery"
    assert rls.get("Arn") == expected_arn, f"RLS rules dataset is {rls.get('Arn')}"

    rules = redshift().query("select user_name, site_region from ops.rls_site_region")
    by_user: dict[str, list[str | None]] = {}
    for rule in rules:
        by_user.setdefault(rule["user_name"], []).append(rule["site_region"])
    for reader in readers:
        assert by_user.get(reader["user_name"]) == [reader["site_region"]], reader
    owners = [user for user, regions in by_user.items() if regions == [None]]
    assert len(owners) == 1, f"expected one all-regions owner, found {owners}"
    print("RLS check passed: enabled on ds_delivery; each reader sees one region, owner sees all")


if __name__ == "__main__":
    main()
