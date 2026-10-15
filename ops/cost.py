"""`make cost`: cost per pipeline run for the last 30 runs, from ops.pipeline_run_audit.

The audit writer prices each run as Redshift Serverless charged seconds (SYS_SERVERLESS_USAGE)
times the RPU-hour price in SSM /rack-pipeline/rpu_hour_usd. CodeBuild minutes and S3 storage
are billed separately and documented in the README.
"""

from __future__ import annotations

from ops.aws import redshift

SQL = """
select run_id, started_at, status, redshift_charged_seconds, est_cost_usd,
       datediff(second, started_at, finished_at) as duration_seconds
from ops.pipeline_run_audit
order by started_at desc
limit 30
"""


def main() -> None:
    rows = redshift().query(SQL)
    if not rows:
        print("no runs in ops.pipeline_run_audit yet")
        return
    print(f"{'started_at':<20} {'status':<10} {'duration':>9} {'charged s':>10} {'cost USD':>9}")
    for row in rows:
        print(
            f"{str(row['started_at'])[:19]:<20} {row['status']:<10} "
            f"{row['duration_seconds']:>8}s {row['redshift_charged_seconds']:>10} "
            f"{float(row['est_cost_usd'] or 0):>9.4f}"
        )
    succeeded = [r for r in rows if r["status"] == "SUCCEEDED"]
    if succeeded:
        average = sum(float(r["est_cost_usd"] or 0) for r in succeeded) / len(succeeded)
        print(f"\naverage cost per successful run: ${average:.4f} over {len(succeeded)} runs")


if __name__ == "__main__":
    main()
