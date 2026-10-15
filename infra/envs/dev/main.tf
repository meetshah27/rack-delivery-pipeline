# dev environment. State in the bootstrap bucket (make tf-init passes the backend config).
# Every resource is tagged project=rack-delivery-pipeline, env=dev (provider default_tags), so
# `make teardown` can prove nothing is left with a tag search.

terraform {
  required_version = ">= 1.8"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
  backend "s3" {
    key     = "rack-delivery-pipeline/dev.tfstate"
    encrypt = true
  }
}

variable "region" {
  type    = string
  default = "us-west-2"
}

variable "alert_email" {
  description = "Receives SNS pipeline alerts (ALERT_EMAIL); confirm the subscription email."
  type        = string
}

variable "operator_cidr" {
  description = "Your IP as a /32, for dbt show and checks against Redshift; null for none."
  type        = string
  default     = null
}

variable "base_capacity" {
  description = "Redshift Serverless base RPUs: the lowest the region allows."
  type        = number
  default     = 4
}

variable "daily_rpu_hours_limit" {
  type    = number
  default = 8
}

variable "rpu_hour_usd" {
  description = "Redshift Serverless RPU-hour price for the region (check the pricing page)."
  type        = number
  default     = 0.375
}

variable "enable_quicksight" {
  description = "Phase 7, after subscribing to QuickSight Enterprise."
  type        = bool
  default     = false
}

variable "qs_owner_arn" {
  description = "QuickSight owner user ARN (QS_OWNER_ARN); required when enable_quicksight."
  type        = string
  default     = ""
}

variable "qs_owner_user_name" {
  description = "QuickSight owner user name; sees every region under row-level security."
  type        = string
  default     = ""
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { project = "rack-delivery-pipeline", env = "dev" }
  }
}

data "aws_caller_identity" "current" {}

locals {
  name       = "rack-pipeline-dev"
  build_dir  = "${path.root}/../../../build"
  quicksight = var.enable_quicksight ? 1 : 0
}

module "storage" {
  source = "../../modules/storage"
  name   = "${local.name}-${data.aws_caller_identity.current.account_id}"
}

module "warehouse" {
  source                = "../../modules/warehouse"
  name                  = local.name
  bucket_arn            = module.storage.bucket_arn
  base_capacity         = var.base_capacity
  daily_rpu_hours_limit = var.daily_rpu_hours_limit
  operator_cidr         = var.operator_cidr
}

module "pipeline" {
  source                 = "../../modules/pipeline"
  name                   = local.name
  bucket                 = module.storage.bucket
  bucket_arn             = module.storage.bucket_arn
  workgroup_name         = module.warehouse.workgroup_name
  workgroup_arn          = module.warehouse.workgroup_arn
  redshift_endpoint      = module.warehouse.endpoint
  database               = module.warehouse.database
  copy_role_arn          = module.warehouse.copy_role_arn
  alert_email            = var.alert_email
  rpu_hour_usd           = var.rpu_hour_usd
  layer_zip              = "${local.build_dir}/lambda_layer.zip"
  code_zip               = "${local.build_dir}/lambda_code.zip"
  quicksight_dataset_ids = var.enable_quicksight ? keys(jsondecode(file("${path.root}/../../../dashboards/datasets.json"))) : []
  qs_owner_user_name     = var.qs_owner_user_name
}

# Password user for the QuickSight data source (QuickSight cannot use IAM auth here). Created
# in Redshift by ops/redshift_admin.py with SELECT on marts and ops only.
resource "random_password" "quicksight" {
  length      = 32
  special     = false
  min_upper   = 2
  min_lower   = 2
  min_numeric = 2 # Redshift needs an upper, a lower and a digit
}

module "quicksight" {
  count                      = local.quicksight
  source                     = "../../modules/quicksight"
  name                       = local.name
  owner_arn                  = var.qs_owner_arn
  vpc_id                     = module.warehouse.vpc_id
  subnet_ids                 = module.warehouse.subnet_ids
  redshift_security_group_id = module.warehouse.security_group_id
  redshift_endpoint          = module.warehouse.endpoint
  database                   = module.warehouse.database
  db_user                    = "quicksight_reader"
  db_password                = random_password.quicksight.result
}

output "bucket" {
  value = module.storage.bucket
}

output "redshift_workgroup" {
  value = module.warehouse.workgroup_name
}

output "redshift_endpoint" {
  value = module.warehouse.endpoint
}

output "redshift_database" {
  value = module.warehouse.database
}

output "redshift_admin_secret_arn" {
  value = module.warehouse.admin_secret_arn
}

output "copy_role_arn" {
  value = module.warehouse.copy_role_arn
}

output "state_machine_arn" {
  value = module.pipeline.state_machine_arn
}

output "schedule_name" {
  value = module.pipeline.schedule_name
}

output "codebuild_project" {
  value = module.pipeline.codebuild_project
}

output "function_names" {
  value = module.pipeline.function_names
}

output "db_users" {
  value = merge(module.pipeline.db_users, { quicksight_user = "quicksight_reader" })
}

output "quicksight_db_password" {
  value     = random_password.quicksight.result
  sensitive = true
}

output "quicksight_dataset_arns" {
  value = var.enable_quicksight ? module.quicksight[0].dataset_arns : {}
}
