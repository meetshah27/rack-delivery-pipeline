"""Cloud artifacts that can be checked without an AWS account."""

from __future__ import annotations

import json
import re
from string import Template

from ops import quicksight_definition as qsd
from ops.redshift_admin import SQL_DIR, statements

USERS = {
    "dbt_user": "IAMR:rack-pipeline-dev-dbt",
    "lambda_user": "IAMR:rack-pipeline-dev-lambda",
    "nlq_user": "IAMR:rack-pipeline-dev-nlq-read",
    "quicksight_user": "quicksight_reader",
    "operator_user": "IAM:meet",
    "copy_role_arn": "arn:aws:iam::123456789012:role/rack-pipeline-dev-redshift-copy",
}


def test_dashboard_definition_is_valid_and_committed_fresh() -> None:
    definition = qsd.build()
    qsd.validate(definition)  # QuickSight CreateAnalysis input shape, from botocore
    committed = json.loads(qsd.OUTPUT.read_text())
    assert committed == definition, "run `make qs-definition` and commit"


def test_dashboard_has_the_four_specified_sheets() -> None:
    definition = qsd.build()
    names = [sheet["Name"] for sheet in definition["Sheets"]]
    assert names == ["Executive", "Cycle Time", "SLA Breaches", "Pipeline Health"]
    for sheet in definition["Sheets"]:
        for visual in sheet["Visuals"]:
            inner = next(iter(visual.values()))
            text = inner["Title"]["FormatText"]["PlainText"]
            assert text.endswith("?") or "?" in text, f"title is not a question: {text}"
        laid_out = {
            e["ElementId"] for e in sheet["Layouts"][0]["Configuration"]["GridLayout"]["Elements"]
        }
        visual_ids = {next(iter(v.values()))["VisualId"] for v in sheet["Visuals"]}
        assert visual_ids <= laid_out, f"{sheet['Name']}: visuals missing from the layout"


def test_dashboard_reads_only_declared_datasets() -> None:
    definition = qsd.build()
    declared = {d["Identifier"] for d in definition["DataSetIdentifierDeclarations"]}
    used = set(re.findall(r'"DataSetIdentifier": "(\w+)"', json.dumps(definition)))
    assert used <= declared


def test_migrations_render_completely() -> None:
    for path in sorted(SQL_DIR.glob("*.sql")):
        sql = Template(path.read_text()).substitute(USERS)
        assert "$" not in sql, f"{path.name}: unfilled placeholder"
        assert statements(sql), path.name


def test_grants_follow_spec_access() -> None:
    sql = Template((SQL_DIR / "002_grants.sql").read_text()).substitute(USERS)
    nlq = [s for s in statements(sql) if '"IAMR:rack-pipeline-dev-nlq-read"' in s]
    # The NLQ service may read marts and nothing else.
    assert all("marts" in s and "insert" not in s and "create" not in s for s in nlq)
    quicksight = [s for s in statements(sql) if '"quicksight_reader"' in s]
    assert all("raw" not in s and "intermediate" not in s for s in quicksight)
