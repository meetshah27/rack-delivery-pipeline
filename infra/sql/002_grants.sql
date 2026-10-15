-- Least-privilege access per role. Redshift only; applied by ops/redshift_admin.py as the namespace
-- admin, which creates the users first. Placeholders such as $dbt_user come from Terraform
-- outputs.
--
-- Database users: Redshift Serverless maps an IAM role to the user "IAMR:<role-name>".
--   $dbt_user        CodeBuild role: the only identity that builds dbt models, so it owns
--                    every dbt table (Redshift lets only the owner replace a table)
--   $lambda_user     pipeline Lambdas: load raw, write the manifest and audit, read results
--   $quicksight_user QuickSight data source (password user): read marts and ops
--   $nlq_user        metric-layer-nlq service: read marts only
--   $operator_user   the person running make targets: read everything (dbt show, checks)

-- dbt: write staging, intermediate, marts, ops; read raw and ground truth
grant usage, create on schema staging, intermediate, marts, ops to "$dbt_user";
grant usage on schema raw, ground_truth to "$dbt_user";
grant select on all tables in schema raw to "$dbt_user";
grant select on all tables in schema ground_truth to "$dbt_user";
grant select on ops.pipeline_run_audit to "$dbt_user";

-- Lambdas: load raw and ground truth, keep the manifest and audit, read dbt results
grant usage on schema raw, ops, ground_truth, intermediate, marts to "$lambda_user";
grant select, insert, delete on all tables in schema raw to "$lambda_user";
grant select, insert, delete on ground_truth.racks to "$lambda_user";
grant select, insert, delete on ops.file_manifest, ops.pipeline_run_audit to "$lambda_user";
grant assumerole on '$copy_role_arn' to "$lambda_user" for copy;
grant role sys:monitor to "$lambda_user";

-- QuickSight and NLQ: read only
grant usage on schema marts, ops to "$quicksight_user";
grant select on ops.pipeline_run_audit to "$quicksight_user";
grant usage on schema marts to "$nlq_user";

-- Operator: read everything
grant usage on schema raw, staging, intermediate, marts, ops, ground_truth to "$operator_user";
grant select on all tables in schema raw to "$operator_user";
grant select on all tables in schema ground_truth to "$operator_user";
grant select on all tables in schema ops to "$operator_user";

-- Tables dbt creates later get the same read access automatically
alter default privileges for user "$dbt_user" in schema marts
    grant select on tables to "$quicksight_user", "$nlq_user", "$lambda_user", "$operator_user";
alter default privileges for user "$dbt_user" in schema ops
    grant select on tables to "$quicksight_user", "$lambda_user", "$operator_user";
alter default privileges for user "$dbt_user" in schema intermediate
    grant select on tables to "$lambda_user", "$operator_user";
alter default privileges for user "$dbt_user" in schema staging
    grant select on tables to "$operator_user";
