# $0 Plan: Build Locally, Deploy Once, Capture Proof, Tear Down

Goal: three resume projects that show real AWS deployments of Amazon Redshift, Amazon QuickSight, AWS Glue, Amazon Athena, AWS Step Functions, AWS Lambda and Amazon Bedrock, at no cost. Recruiters and interviewers look at the GitHub repo, the screenshots and the numbers, not at a live AWS endpoint. The cloud resources only need to exist long enough to run, measure and capture evidence.

## What pays for what

| Service | Covered by |
|---|---|
| S3, Glue, Athena, Lambda, Step Functions, EventBridge, CodeBuild, API Gateway, SNS, CloudWatch, Bedrock | AWS Free Tier credits (up to $200 for new accounts) and always-free tiers |
| Redshift Serverless | Redshift Serverless free trial: $300 credit, usable within 90 days, for accounts that never used Redshift Serverless |
| QuickSight (Amazon Quick Enterprise) | 30-day Enterprise trial with fees waived. Must be cancelled before day 30. Otherwise it bills the per-user fee plus a monthly account infrastructure fee |
| GitHub repos, Actions, Pages | Free for public repos |
| dbt, DuckDB, Python, Terraform | Free and local |

Re-check each trial's terms on its AWS pricing page on the day you start it.

## Timeline

### Stage 1: Local build, no AWS account needed (about 3 to 5 weeks)

| Project | Work |
|---|---|
| P1 | Simulator, plus all dbt models, snapshots and tests on the `local` DuckDB target (BUILD_PLAN "local track") |
| P2 | Package code, stats helpers and their unit tests |
| P3 | Semantic layer and metric dictionary on the DuckDB marts; guard and eval harness code with unit tests |

### Stage 2: AWS free plan account (about 1 to 2 weeks)

Create the account on the free plan. A free plan account cannot be charged; it is suspended when credits or 6 months run out.

| Project | Work |
|---|---|
| P2 | Run fully: S3, Glue, Athena, notebooks, memo. Then tear down P2 |
| P3 | Bedrock prompt development against local DuckDB marts. Use a small, cheap model while iterating |

### Stage 3: Deployment window (about 3 to 4 weeks, one continuous block)

1. Upgrade the account to the paid plan. Before clicking, check on the account page whether remaining credits carry over.
2. Apply all guardrails below before creating anything.
3. **P1, cloud track:** Phases 2, 3, 4, 5 on Redshift, then 6.
   - Let the scheduled pipeline run 7 days.
4. Start the QuickSight trial and note the start date.
   - **P1:** Phase 7 (QuickSight as code).
5. **P3:** Phases 4 to 6 on Redshift. Final eval numbers come from this run only.
6. **Evidence phase** in every repo (see below).
7. Teardown checklist (see below). Cancel QuickSight by trial day 25 at the latest.

## Guardrails (create before any paid-plan resource)

- **AWS Budgets:**
  - a cost budget of $1 with alerts at 50% actual and 100% forecast, emailed to you
  - a budget action at 100% actual that attaches a deny-all IAM policy to your working IAM user, which stops new spend even if you are asleep
- **Redshift Serverless:** the workgroup usage limit is set to cap RPU-hours per day, with action "turn off user queries". Terraform in P1 already sets this.
- **EventBridge Scheduler:** P1's daily schedule is created disabled. Enable it only during the 7-day run, then disable it.
- **Calendar reminders:**
  - QuickSight trial day 20: final snapshot
  - QuickSight trial day 25: unsubscribe
  - Redshift trial day 80: teardown
  - Free plan month 5: confirm everything is gone
- **No idle-cost resources:** no NAT Gateways, no MWAA, no provisioned Redshift clusters, no Elastic IPs, no always-on EC2.

## Evidence that outlives teardown

Each repo's final phase produces these. All are committed to GitHub or hosted on GitHub Pages, so they stay up at $0 forever.

- `docs/img/`:
  - QuickSight sheet PNGs (P1)
  - Step Functions execution graph rendered from the ASL definition (P1)
  - CloudWatch metric charts exported via `GetMetricWidgetImage` (P1, P3)
- `evidence/`:
  - exported audit table CSV of all pipeline runs (P1)
  - QuickSight snapshot PDF and dashboard definition JSON (P1)
  - eval reports (P3)
  - memo PDF and cost log (P2)
- A terminal recording of the live system with asciinema, converted to GIF with `agg` and embedded in the README:
  - P1: triggering a pipeline execution and querying the audit table
  - P3: three `make ask` calls (answered, refused, rejected)
- dbt docs site (`dbt docs generate`) published to GitHub Pages: a live, clickable lineage graph and model documentation for P1, with P3's semantic models included.
- A one-page GitHub Pages project site per repo with the business question, screenshots, headline numbers and links. This is the link to put on the resume.
- Optional, done by you: a 2 to 3 minute screen recording walking through the QuickSight dashboard during the trial, uploaded unlisted to YouTube and linked from the README.

## Teardown checklist

Run `make teardown` in each repo, then verify:

- `aws resourcegroupstaggingapi get-resources --tag-filters Key=project,Values=<repo>` returns nothing for each project tag
- Redshift Serverless workgroups and namespaces list is empty
- QuickSight subscription cancelled (Account settings, unsubscribe) and the account is deleted from QuickSight
- S3 buckets deleted, including the Terraform state bucket last
- No running Step Functions executions; EventBridge schedules deleted
- Lambda functions, API Gateway APIs and CodeBuild projects deleted
- Billing console shows month-to-date $0.00 beyond credits

## How to describe it on the resume

The projects were genuinely deployed and measured on AWS. Taking them down afterwards is an engineering decision, not a weakness. In the README:

> Deployed on AWS from <start date> to <end date>; ran <N> scheduled pipeline executions over <N> days. Infrastructure is fully defined in Terraform and was torn down after validation to control cost (Frugality). `make deploy` recreates it.

Resume bullets use the measured numbers from the deployment window and link to the GitHub Pages project site.
