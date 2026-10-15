"""After `terraform destroy`: prove nothing billable is left (DEPLOY_WINDOW teardown checklist).

Exits non-zero if any check finds something. The Terraform state bucket and lock table are
expected to remain until `make teardown-state`.
"""

from __future__ import annotations

from ops.aws import session

TAG = {"Key": "project", "Values": ["rack-delivery-pipeline"]}
STATE_PREFIXES = ("rack-pipeline-tfstate",)


def main() -> None:
    s = session()
    problems: list[str] = []

    tagged = s.client("resourcegroupstaggingapi").get_resources(TagFilters=[TAG])[
        "ResourceTagMappingList"
    ]
    leftover = [
        r["ResourceARN"] for r in tagged if not any(p in r["ResourceARN"] for p in STATE_PREFIXES)
    ]
    problems += [f"tagged resource: {arn}" for arn in leftover]

    serverless = s.client("redshift-serverless")
    problems += [
        f"workgroup: {w['workgroupName']}" for w in serverless.list_workgroups()["workgroups"]
    ]
    problems += [
        f"namespace: {n['namespaceName']}" for n in serverless.list_namespaces()["namespaces"]
    ]

    sfn = s.client("stepfunctions")
    for machine in sfn.list_state_machines()["stateMachines"]:
        running = sfn.list_executions(
            stateMachineArn=machine["stateMachineArn"], statusFilter="RUNNING"
        )
        problems += [f"running execution: {e['executionArn']}" for e in running["executions"]]

    schedules = s.client("scheduler").list_schedules(NamePrefix="rack-pipeline")["Schedules"]
    problems += [f"schedule: {sch['Name']}" for sch in schedules]
    functions = s.client("lambda").list_functions()["Functions"]
    problems += [
        f"lambda: {f['FunctionName']}"
        for f in functions
        if f["FunctionName"].startswith("rack-pipeline")
    ]
    projects = s.client("codebuild").list_projects()["projects"]
    problems += [f"codebuild: {p}" for p in projects if p.startswith("rack-pipeline")]

    for problem in problems:
        print(problem)
    if problems:
        raise SystemExit(f"{len(problems)} resources remain")
    print(
        "teardown verified: no tagged resources, workgroups, namespaces, running executions, "
        "schedules, Lambda functions or CodeBuild projects remain"
    )
    print("still manual: cancel the QuickSight subscription; check Billing for month-to-date $0.00")


if __name__ == "__main__":
    main()
