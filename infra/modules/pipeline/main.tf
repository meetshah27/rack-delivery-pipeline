# The daily pipeline: four Lambdas, CodeBuild (dbt), the Step Functions state machine, the
# EventBridge schedule (created disabled), SNS alerts, CloudWatch alarms and SSM parameters.

terraform {
  required_providers {
    aws = { source = "hashicorp/aws" }
  }
}

variable "name" {
  type = string
}

variable "bucket" {
  type = string
}

variable "bucket_arn" {
  type = string
}

variable "workgroup_name" {
  type = string
}

variable "workgroup_arn" {
  type = string
}

variable "redshift_endpoint" {
  type = string
}

variable "database" {
  type = string
}

variable "copy_role_arn" {
  type = string
}

variable "alert_email" {
  type = string
}

variable "rpu_hour_usd" {
  description = "Redshift Serverless price per RPU-hour (pricing page); edit in SSM if it changes."
  type        = number
}

variable "layer_zip" {
  description = "Local path of the dependency layer zip (make package)."
  type        = string
}

variable "code_zip" {
  description = "Local path of the function code zip (make package)."
  type        = string
}

variable "quicksight_dataset_ids" {
  description = "Datasets refreshed after each successful run; empty until QuickSight exists."
  type        = list(string)
  default     = []
}

variable "qs_owner_user_name" {
  description = "QuickSight owner user name, passed to dbt for the RLS rules; empty for none."
  type        = string
  default     = ""
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name
  lambda_env = {
    BUCKET             = var.bucket
    REDSHIFT_WORKGROUP = var.workgroup_name
    REDSHIFT_DATABASE  = var.database
  }
  functions = {
    simulate_daily   = { handler = "orchestration.simulate_daily.handler", memory = 2048, timeout = 600 }
    land_to_raw      = { handler = "orchestration.land_to_raw.handler", memory = 1024, timeout = 900 }
    copy_to_redshift = { handler = "orchestration.copy_to_redshift.handler", memory = 256, timeout = 600 }
    audit_writer     = { handler = "orchestration.audit_writer.handler", memory = 256, timeout = 300 }
  }
  redshift_data_actions = [
    "redshift-data:ExecuteStatement",
    "redshift-data:BatchExecuteStatement",
  ]
  redshift_data_read_actions = [
    "redshift-data:DescribeStatement",
    "redshift-data:GetStatementResult",
    "redshift-data:CancelStatement",
  ]
}

# ---------------------------------------------------------------- alerts and parameters

resource "aws_sns_topic" "alerts" {
  name = "${var.name}-alerts"
}

resource "aws_sns_topic_policy" "alerts" {
  arn = aws_sns_topic.alerts.arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "CloudWatchAlarms"
      Effect    = "Allow"
      Principal = { Service = "cloudwatch.amazonaws.com" }
      Action    = "sns:Publish"
      Resource  = aws_sns_topic.alerts.arn
      Condition = { StringEquals = { "aws:SourceAccount" = local.account_id } }
    }]
  })
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_ssm_parameter" "rpu_hour_usd" {
  name  = "/rack-pipeline/rpu_hour_usd"
  type  = "String"
  value = tostring(var.rpu_hour_usd)
  lifecycle {
    ignore_changes = [value] # maintained by hand from the pricing page
  }
}

resource "aws_ssm_parameter" "config" {
  for_each = {
    bucket             = var.bucket
    redshift_workgroup = var.workgroup_name
    redshift_database  = var.database
    redshift_endpoint  = var.redshift_endpoint
    nlq_read_role_arn  = aws_iam_role.nlq_read.arn
  }
  name  = "/rack-pipeline/${each.key}"
  type  = "String"
  value = each.value
}

# ---------------------------------------------------------------- Lambdas

resource "aws_iam_role" "lambda" {
  name = "${var.name}-lambda"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "lambda" {
  role = aws_iam_role.lambda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject"], Resource = "${var.bucket_arn}/*" },
      { Effect = "Allow", Action = ["s3:ListBucket"], Resource = var.bucket_arn },
      { Effect = "Allow", Action = local.redshift_data_actions, Resource = var.workgroup_arn },
      { Effect = "Allow", Action = local.redshift_data_read_actions, Resource = "*" },
      { Effect = "Allow", Action = ["redshift-serverless:GetCredentials"], Resource = var.workgroup_arn },
      { Effect = "Allow", Action = ["ssm:GetParameter"], Resource = aws_ssm_parameter.rpu_hour_usd.arn },
      {
        Effect    = "Allow"
        Action    = ["cloudwatch:PutMetricData"]
        Resource  = "*"
        Condition = { StringEquals = { "cloudwatch:namespace" = "RackPipeline" } }
      },
    ]
  })
}

resource "aws_s3_object" "layer" {
  bucket      = var.bucket
  key         = "artifacts/lambda_layer.zip"
  source      = var.layer_zip
  source_hash = filemd5(var.layer_zip)
}

resource "aws_lambda_layer_version" "deps" {
  layer_name          = "${var.name}-deps"
  s3_bucket           = var.bucket
  s3_key              = aws_s3_object.layer.key
  s3_object_version   = aws_s3_object.layer.version_id
  compatible_runtimes = ["python3.12"]
  source_code_hash    = filebase64sha256(var.layer_zip)
}

resource "aws_cloudwatch_log_group" "lambda" {
  for_each          = local.functions
  name              = "/aws/lambda/${var.name}-${replace(each.key, "_", "-")}"
  retention_in_days = 14
}

resource "aws_lambda_function" "this" {
  for_each         = local.functions
  function_name    = "${var.name}-${replace(each.key, "_", "-")}"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = each.value.handler
  filename         = var.code_zip
  source_code_hash = filebase64sha256(var.code_zip)
  layers           = [aws_lambda_layer_version.deps.arn]
  memory_size      = each.value.memory
  timeout          = each.value.timeout
  environment {
    variables = merge(
      local.lambda_env,
      each.key == "copy_to_redshift" ? { COPY_ROLE_ARN = var.copy_role_arn } : {},
      each.key == "audit_writer" ? { RPU_PRICE_PARAMETER = aws_ssm_parameter.rpu_hour_usd.name } : {},
    )
  }
  depends_on = [aws_cloudwatch_log_group.lambda]
}

# ---------------------------------------------------------------- CodeBuild (dbt)

resource "aws_iam_role" "dbt" {
  name = "${var.name}-dbt"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "codebuild.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_cloudwatch_log_group" "dbt" {
  name              = "/aws/codebuild/${var.name}-dbt"
  retention_in_days = 14
}

resource "aws_iam_role_policy" "dbt" {
  role = aws_iam_role.dbt.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.dbt.arn}:*"
      },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:GetObjectVersion"], Resource = "${var.bucket_arn}/artifacts/*" },
      { Effect = "Allow", Action = ["s3:PutObject"], Resource = "${var.bucket_arn}/runs/*" },
      {
        Effect   = "Allow"
        Action   = ["redshift-serverless:GetCredentials", "redshift-serverless:GetWorkgroup"]
        Resource = var.workgroup_arn
      },
    ]
  })
}

resource "aws_codebuild_project" "dbt" {
  name          = "${var.name}-dbt"
  service_role  = aws_iam_role.dbt.arn
  build_timeout = 60
  source {
    type      = "S3"
    location  = "${var.bucket}/artifacts/dbt_project.zip"
    buildspec = "buildspec.yml"
  }
  artifacts {
    type = "NO_ARTIFACTS"
  }
  environment {
    compute_type = "BUILD_GENERAL1_SMALL"
    image        = "aws/codebuild/amazonlinux-x86_64-standard:5.0"
    type         = "LINUX_CONTAINER"
    environment_variable {
      name  = "BUCKET"
      value = var.bucket
    }
    environment_variable {
      name  = "REDSHIFT_HOST"
      value = var.redshift_endpoint
    }
    environment_variable {
      name  = "REDSHIFT_DATABASE"
      value = var.database
    }
    environment_variable {
      name  = "REDSHIFT_WORKGROUP"
      value = var.workgroup_name
    }
    environment_variable {
      name  = "QS_OWNER_USER_NAME"
      value = var.qs_owner_user_name
    }
  }
  logs_config {
    cloudwatch_logs {
      group_name = aws_cloudwatch_log_group.dbt.name
    }
  }
}

# ---------------------------------------------------------------- Step Functions

resource "aws_iam_role" "sfn" {
  name = "${var.name}-sfn"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "states.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "sfn" {
  role = aws_iam_role.sfn.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        { Effect = "Allow", Action = ["lambda:InvokeFunction"], Resource = [for f in aws_lambda_function.this : f.arn] },
        {
          Effect   = "Allow"
          Action   = ["codebuild:StartBuild", "codebuild:StopBuild", "codebuild:BatchGetBuilds"]
          Resource = aws_codebuild_project.dbt.arn
        },
        {
          # required by the codebuild:startBuild.sync integration
          Effect   = "Allow"
          Action   = ["events:PutTargets", "events:PutRule", "events:DescribeRule"]
          Resource = "arn:aws:events:${local.region}:${local.account_id}:rule/StepFunctionsGetEventForCodeBuildStartBuildRule"
        },
        { Effect = "Allow", Action = ["sns:Publish"], Resource = aws_sns_topic.alerts.arn },
      ],
      length(var.quicksight_dataset_ids) == 0 ? [] : [{
        Effect   = "Allow"
        Action   = ["quicksight:CreateIngestion"]
        Resource = [for id in var.quicksight_dataset_ids : "arn:aws:quicksight:${local.region}:${local.account_id}:dataset/${id}"]
      }],
    )
  })
}

resource "aws_sfn_state_machine" "pipeline" {
  name     = "${var.name}-pipeline"
  role_arn = aws_iam_role.sfn.arn
  definition = templatefile("${path.module}/../../../orchestration/state_machine.asl.json", {
    simulate_arn           = aws_lambda_function.this["simulate_daily"].arn
    land_to_raw_arn        = aws_lambda_function.this["land_to_raw"].arn
    copy_to_redshift_arn   = aws_lambda_function.this["copy_to_redshift"].arn
    audit_writer_arn       = aws_lambda_function.this["audit_writer"].arn
    codebuild_project      = aws_codebuild_project.dbt.name
    sns_topic_arn          = aws_sns_topic.alerts.arn
    account_id             = local.account_id
    quicksight_dataset_ids = jsonencode(var.quicksight_dataset_ids)
  })
}

# ---------------------------------------------------------------- schedule

resource "aws_iam_role" "scheduler" {
  name = "${var.name}-scheduler"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "scheduler.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "scheduler" {
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = ["states:StartExecution"], Resource = aws_sfn_state_machine.pipeline.arn }]
  })
}

resource "aws_scheduler_schedule" "daily" {
  name                         = "${var.name}-daily"
  schedule_expression          = "cron(0 6 * * ? *)"
  schedule_expression_timezone = "UTC"
  # Created disabled (DEPLOY_WINDOW guardrail); `make schedule-on/off` flips it through the
  # API, so a later apply does not undo it.
  state = "DISABLED"
  lifecycle {
    ignore_changes = [state]
  }
  flexible_time_window {
    mode = "OFF"
  }
  target {
    arn      = aws_sfn_state_machine.pipeline.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = "{}"
  }
}

# ---------------------------------------------------------------- alarms

resource "aws_cloudwatch_metric_alarm" "quarantine_rate" {
  alarm_name          = "${var.name}-quarantine-rate"
  alarm_description   = "More than 5% of a run's landed rows were quarantined."
  namespace           = "RackPipeline"
  metric_name         = "QuarantineRate"
  statistic           = "Maximum"
  period              = 3600
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0.05
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "executions_failed" {
  alarm_name          = "${var.name}-executions-failed"
  alarm_description   = "A pipeline execution failed."
  namespace           = "AWS/States"
  metric_name         = "ExecutionsFailed"
  dimensions          = { StateMachineArn = aws_sfn_state_machine.pipeline.arn }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

# ---------------------------------------------------------------- metric-layer-nlq

# Read-only role for the NLQ service in metric-layer-nlq. Redshift maps it to the database user
# IAMR:<name>-nlq-read, which infra/sql grants SELECT on marts only.
resource "aws_iam_role" "nlq_read" {
  name = "${var.name}-nlq-read"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "nlq_read" {
  role = aws_iam_role.nlq_read.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = local.redshift_data_actions, Resource = var.workgroup_arn },
      { Effect = "Allow", Action = local.redshift_data_read_actions, Resource = "*" },
      { Effect = "Allow", Action = ["redshift-serverless:GetCredentials"], Resource = var.workgroup_arn },
    ]
  })
}

output "state_machine_arn" {
  value = aws_sfn_state_machine.pipeline.arn
}

output "schedule_name" {
  value = aws_scheduler_schedule.daily.name
}

output "codebuild_project" {
  value = aws_codebuild_project.dbt.name
}

output "function_names" {
  value = { for k, f in aws_lambda_function.this : k => f.function_name }
}

output "sns_topic_arn" {
  value = aws_sns_topic.alerts.arn
}

output "db_users" {
  description = "Database users Redshift maps the pipeline roles to (infra/sql/002_grants.sql)."
  value = {
    dbt_user    = "IAMR:${aws_iam_role.dbt.name}"
    lambda_user = "IAMR:${aws_iam_role.lambda.name}"
    nlq_user    = "IAMR:${aws_iam_role.nlq_read.name}"
  }
}
