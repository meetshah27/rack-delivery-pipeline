"""Shared plumbing for ops scripts that talk to AWS: Terraform outputs, a boto3 session for the
operator's profile, and Redshift Data API clients."""

from __future__ import annotations

import json
import os
import subprocess
from functools import cache
from pathlib import Path
from typing import Any

import boto3

from orchestration.redshift_data import RedshiftData

ROOT = Path(__file__).resolve().parent.parent
DEV_ENV = ROOT / "infra" / "envs" / "dev"


@cache
def tf_outputs() -> dict[str, Any]:
    """Outputs of infra/envs/dev (sensitive values included)."""
    raw = subprocess.run(
        ["terraform", f"-chdir={DEV_ENV}", "output", "-json"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return {name: item["value"] for name, item in json.loads(raw).items()}


@cache
def session() -> boto3.Session:
    return boto3.Session(
        profile_name=os.environ.get("AWS_PROFILE") or None,
        region_name=os.environ.get("AWS_REGION", "us-west-2"),
    )


def account_id() -> str:
    return str(session().client("sts").get_caller_identity()["Account"])


def operator_db_user() -> str:
    """The database user Redshift Serverless maps the operator's IAM identity to."""
    arn = str(session().client("sts").get_caller_identity()["Arn"])
    if ":assumed-role/" in arn:
        return "IAMR:" + arn.split(":assumed-role/")[1].split("/")[0]
    return "IAM:" + arn.split("/")[-1]


def redshift(admin: bool = False) -> RedshiftData:
    """Data API client as the operator, or as the namespace admin (migrations)."""
    out = tf_outputs()
    return RedshiftData(
        session().client("redshift-data"),
        out["redshift_workgroup"],
        out["redshift_database"],
        secret_arn=out["redshift_admin_secret_arn"] if admin else None,
    )
