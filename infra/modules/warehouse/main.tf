# Redshift Serverless: namespace (admin password managed in Secrets Manager), workgroup at the
# lowest base capacity with a daily usage limit that turns off user queries when reached, and
# the IAM role COPY uses to read raw Parquet from S3.
#
# Network: the workgroup sits in the default VPC. It is publicly addressable only so CodeBuild
# (which runs outside any VPC, to avoid a NAT gateway) can reach it; the security group admits
# port 5439 from AWS CodeBuild's published IP ranges for the region and the operator's IP only.
# All database users except the admin and the QuickSight reader authenticate with IAM.

terraform {
  required_providers {
    aws = { source = "hashicorp/aws" }
  }
}

variable "name" {
  type = string
}

variable "bucket_arn" {
  type = string
}

variable "database" {
  type    = string
  default = "dev"
}

variable "base_capacity" {
  description = "Base RPUs; the lowest the region allows."
  type        = number
}

variable "daily_rpu_hours_limit" {
  description = "Usage limit per day in RPU-hours; reaching it turns off user queries."
  type        = number
}

variable "operator_cidr" {
  description = "CIDR of the operator's machine (dbt show, checks); null to allow none."
  type        = string
  default     = null
}

data "aws_region" "current" {}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

data "aws_ip_ranges" "codebuild" {
  regions  = [data.aws_region.current.name]
  services = ["codebuild"]
}

resource "aws_security_group" "redshift" {
  name        = "${var.name}-redshift"
  description = "Redshift Serverless: 5439 from CodeBuild and the operator"
  vpc_id      = data.aws_vpc.default.id
}

resource "aws_vpc_security_group_ingress_rule" "codebuild" {
  for_each          = toset(data.aws_ip_ranges.codebuild.cidr_blocks)
  security_group_id = aws_security_group.redshift.id
  cidr_ipv4         = each.value
  ip_protocol       = "tcp"
  from_port         = 5439
  to_port           = 5439
  description       = "AWS CodeBuild (dbt)"
}

resource "aws_vpc_security_group_ingress_rule" "operator" {
  count             = var.operator_cidr == null ? 0 : 1
  security_group_id = aws_security_group.redshift.id
  cidr_ipv4         = var.operator_cidr
  ip_protocol       = "tcp"
  from_port         = 5439
  to_port           = 5439
  description       = "Operator"
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.redshift.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_iam_role" "copy" {
  name = "${var.name}-redshift-copy"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "redshift.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "copy" {
  role = aws_iam_role.copy.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["s3:GetObject"]
        Resource = [
          "${var.bucket_arn}/raw/*",
          "${var.bucket_arn}/runs/*",
          "${var.bucket_arn}/ground_truth/*",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = var.bucket_arn
      },
    ]
  })
}

resource "aws_redshiftserverless_namespace" "this" {
  namespace_name        = var.name
  db_name               = var.database
  admin_username        = "pipeline_admin"
  manage_admin_password = true
  iam_roles             = [aws_iam_role.copy.arn]
  default_iam_role_arn  = aws_iam_role.copy.arn
}

resource "aws_redshiftserverless_workgroup" "this" {
  workgroup_name      = var.name
  namespace_name      = aws_redshiftserverless_namespace.this.namespace_name
  base_capacity       = var.base_capacity
  publicly_accessible = true
  subnet_ids          = data.aws_subnets.default.ids
  security_group_ids  = [aws_security_group.redshift.id]
}

resource "aws_redshiftserverless_usage_limit" "daily" {
  resource_arn  = aws_redshiftserverless_workgroup.this.arn
  usage_type    = "serverless-compute"
  amount        = var.daily_rpu_hours_limit
  period        = "daily"
  breach_action = "deactivate"
}

output "workgroup_name" {
  value = aws_redshiftserverless_workgroup.this.workgroup_name
}

output "workgroup_arn" {
  value = aws_redshiftserverless_workgroup.this.arn
}

output "endpoint" {
  value = aws_redshiftserverless_workgroup.this.endpoint[0].address
}

output "database" {
  value = var.database
}

output "admin_secret_arn" {
  value = aws_redshiftserverless_namespace.this.admin_password_secret_arn
}

output "copy_role_arn" {
  value = aws_iam_role.copy.arn
}

output "security_group_id" {
  value = aws_security_group.redshift.id
}

output "vpc_id" {
  value = data.aws_vpc.default.id
}

output "subnet_ids" {
  value = data.aws_subnets.default.ids
}
