# QuickSight (needs the Enterprise subscription first): a VPC connection into the
# Redshift workgroup's subnets, the Redshift data source, the SPICE datasets defined in
# dashboards/datasets.json (one definition shared with the dashboard builder), row-level
# security on ds_delivery by site_region, and daily refresh schedules. The analysis and
# dashboard are deployed from JSON by ops/quicksight_deploy.py.

terraform {
  required_providers {
    aws = { source = "hashicorp/aws" }
  }
}

variable "name" {
  type = string
}

variable "owner_arn" {
  description = "QuickSight user ARN of the owner (QS_OWNER_ARN)."
  type        = string
}

variable "vpc_id" {
  type = string
}

variable "subnet_ids" {
  type = list(string)
}

variable "redshift_security_group_id" {
  type = string
}

variable "redshift_endpoint" {
  type = string
}

variable "database" {
  type = string
}

variable "db_user" {
  type = string
}

variable "db_password" {
  type      = string
  sensitive = true
}

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  datasets   = jsondecode(file("${path.module}/../../../dashboards/datasets.json"))
  dataset_actions = [
    "quicksight:DescribeDataSet", "quicksight:DescribeDataSetPermissions", "quicksight:PassDataSet",
    "quicksight:DescribeIngestion", "quicksight:ListIngestions", "quicksight:UpdateDataSet",
    "quicksight:DeleteDataSet", "quicksight:CreateIngestion", "quicksight:CancelIngestion",
    "quicksight:UpdateDataSetPermissions",
  ]
}

# ---------------------------------------------------------------- network

resource "aws_security_group" "quicksight" {
  name        = "${var.name}-quicksight"
  description = "QuickSight network interfaces to Redshift"
  vpc_id      = var.vpc_id
}

# QuickSight's interfaces are not stateful, so return traffic needs an explicit inbound rule.
resource "aws_vpc_security_group_ingress_rule" "quicksight_return" {
  security_group_id            = aws_security_group.quicksight.id
  referenced_security_group_id = var.redshift_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 0
  to_port                      = 65535
}

resource "aws_vpc_security_group_egress_rule" "quicksight_to_redshift" {
  security_group_id            = aws_security_group.quicksight.id
  referenced_security_group_id = var.redshift_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 5439
  to_port                      = 5439
}

resource "aws_vpc_security_group_ingress_rule" "redshift_from_quicksight" {
  security_group_id            = var.redshift_security_group_id
  referenced_security_group_id = aws_security_group.quicksight.id
  ip_protocol                  = "tcp"
  from_port                    = 5439
  to_port                      = 5439
  description                  = "QuickSight"
}

resource "aws_iam_role" "vpc_connection" {
  name = "${var.name}-quicksight-vpc"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "quicksight.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "vpc_connection" {
  role = aws_iam_role.vpc_connection.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "ec2:CreateNetworkInterface", "ec2:ModifyNetworkInterfaceAttribute",
        "ec2:DeleteNetworkInterface", "ec2:DescribeSubnets", "ec2:DescribeSecurityGroups",
      ]
      Resource = "*"
    }]
  })
}

resource "aws_quicksight_vpc_connection" "redshift" {
  vpc_connection_id  = "${var.name}-redshift"
  name               = "${var.name}-redshift"
  role_arn           = aws_iam_role.vpc_connection.arn
  security_group_ids = [aws_security_group.quicksight.id]
  subnet_ids         = var.subnet_ids
}

# ---------------------------------------------------------------- data

resource "aws_quicksight_data_source" "redshift" {
  data_source_id = "${var.name}-redshift"
  name           = "Rack pipeline (Redshift)"
  type           = "REDSHIFT"
  parameters {
    redshift {
      host     = var.redshift_endpoint
      port     = 5439
      database = var.database
    }
  }
  credentials {
    credential_pair {
      username = var.db_user
      password = var.db_password
    }
  }
  vpc_connection_properties {
    vpc_connection_arn = aws_quicksight_vpc_connection.redshift.arn
  }
  ssl_properties {
    disable_ssl = false
  }
  permission {
    principal = var.owner_arn
    actions = [
      "quicksight:DescribeDataSource", "quicksight:DescribeDataSourcePermissions",
      "quicksight:PassDataSource", "quicksight:UpdateDataSource", "quicksight:DeleteDataSource",
      "quicksight:UpdateDataSourcePermissions",
    ]
  }
}

resource "aws_quicksight_data_set" "rls" {
  data_set_id = "ds_rls_permissions"
  name        = local.datasets.ds_rls_permissions.name
  import_mode = "SPICE"
  physical_table_map {
    physical_table_map_id = "rls"
    custom_sql {
      data_source_arn = aws_quicksight_data_source.redshift.arn
      name            = "rls_site_region"
      sql_query       = local.datasets.ds_rls_permissions.sql
      dynamic "columns" {
        for_each = local.datasets.ds_rls_permissions.columns
        content {
          name = columns.key
          type = columns.value
        }
      }
    }
  }
  logical_table_map {
    logical_table_map_id = "rls"
    alias                = "rls_site_region"
    source {
      physical_table_id = "rls"
    }
    data_transforms {
      rename_column_operation {
        column_name     = "user_name"
        new_column_name = "UserName"
      }
    }
  }
  permissions {
    principal = var.owner_arn
    actions   = local.dataset_actions
  }
}

resource "aws_quicksight_data_set" "this" {
  for_each    = { for id, ds in local.datasets : id => ds if id != "ds_rls_permissions" }
  data_set_id = each.key
  name        = each.value.name
  import_mode = "SPICE"
  physical_table_map {
    physical_table_map_id = "t"
    custom_sql {
      data_source_arn = aws_quicksight_data_source.redshift.arn
      name            = each.key
      sql_query       = each.value.sql
      dynamic "columns" {
        for_each = each.value.columns
        content {
          name = columns.key
          type = columns.value
        }
      }
    }
  }
  dynamic "row_level_permission_data_set" {
    for_each = try(each.value.rls, false) ? [1] : []
    content {
      arn               = aws_quicksight_data_set.rls.arn
      permission_policy = "GRANT_ACCESS"
      format_version    = "VERSION_2"
      status            = "ENABLED"
    }
  }
  permissions {
    principal = var.owner_arn
    actions   = local.dataset_actions
  }
}

resource "aws_quicksight_refresh_schedule" "daily" {
  for_each    = merge(aws_quicksight_data_set.this, { ds_rls_permissions = aws_quicksight_data_set.rls })
  data_set_id = each.value.data_set_id
  schedule_id = "${each.key}-daily"
  schedule {
    refresh_type = "FULL_REFRESH"
    schedule_frequency {
      interval        = "DAILY"
      time_of_the_day = "07:30"
      timezone        = "UTC"
    }
  }
}

output "dataset_ids" {
  value = concat([for ds in aws_quicksight_data_set.this : ds.data_set_id], [aws_quicksight_data_set.rls.data_set_id])
}

output "dataset_arns" {
  value = merge(
    { for id, ds in aws_quicksight_data_set.this : id => ds.arn },
    { ds_rls_permissions = aws_quicksight_data_set.rls.arn },
  )
}
