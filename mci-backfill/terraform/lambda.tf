# The four backfill Lambdas. All share one deployment package (mci-backfill
# plus its mci-core path dependency, see variables.tf) and one IAM role; they
# differ in handler, memory/timeout, and env vars.

locals {
  common_zip_args = {
    filename         = var.lambda_zip_path
    source_code_hash = filebase64sha256(var.lambda_zip_path)
    runtime          = "python3.12"
    role             = aws_iam_role.backfill_lambda.arn
  }
}

# ---- backfill-mci -----------------------------------------------------------
# Spec: 8,192 MB / 900s (large; processes big batches). It does not write to
# DynamoDB itself; it calls MCI through the invoker, so it needs no DynamoDB
# permissions of its own, only lambda:InvokeFunction on MCI (see iam.tf).
resource "aws_lambda_function" "backfill_mci" {
  function_name    = "${var.env}-backfill-mci"
  handler          = "backfill.handlers.backfill_mci.handler"
  filename         = local.common_zip_args.filename
  source_code_hash = local.common_zip_args.source_code_hash
  runtime          = local.common_zip_args.runtime
  role             = local.common_zip_args.role

  memory_size = var.backfill_mci_memory_size
  timeout     = 900

  environment {
    variables = {
      MCI_FUNCTION_NAME  = var.mci_get_internal_function_name
      MCI_FUNCTION_ALIAS = var.mci_function_alias
    }
  }

  tags = var.tags
}

# ---- backfill-iteration-utility ---------------------------------------------
# Pure arithmetic, no AWS calls inside the handler, so this is deliberately
# small: minimum useful memory and a short timeout.
resource "aws_lambda_function" "iteration_utility" {
  function_name    = "${var.env}-backfill-iteration-utility"
  handler          = "backfill.handlers.iteration_utility.handler"
  filename         = local.common_zip_args.filename
  source_code_hash = local.common_zip_args.source_code_hash
  runtime          = local.common_zip_args.runtime
  role             = local.common_zip_args.role

  memory_size = 128
  timeout     = 10

  tags = var.tags
}

# ---- list-tenants -------------------------------------------------------------
resource "aws_lambda_function" "list_tenants" {
  function_name    = "${var.env}-list-tenants"
  handler          = "backfill.handlers.list_tenants.handler"
  filename         = local.common_zip_args.filename
  source_code_hash = local.common_zip_args.source_code_hash
  runtime          = local.common_zip_args.runtime
  role             = local.common_zip_args.role

  memory_size = 256
  timeout     = 30

  environment {
    variables = {
      TENANT_TABLE_NAME   = aws_dynamodb_table.tenant_registry.name
      EXCLUDED_TENANT_IDS = var.excluded_tenant_ids
    }
  }

  tags = var.tags
}

# ---- athena-query-runner ------------------------------------------------------
# Timeout must exceed the poll ceiling in src/backfill/athena/client.py
# (default max_poll_attempts=30 * poll_interval_seconds=2 = 60s worst case).
# 120s gives real headroom over that default without being generous enough to
# mask a genuinely stuck query; if the client's polling defaults change, this
# timeout should be revisited alongside them.
resource "aws_lambda_function" "athena_query_runner" {
  function_name    = "${var.env}-athena-query-runner"
  handler          = "backfill.handlers.athena_query_runner.handler"
  filename         = local.common_zip_args.filename
  source_code_hash = local.common_zip_args.source_code_hash
  runtime          = local.common_zip_args.runtime
  role             = local.common_zip_args.role

  memory_size = 256
  timeout     = 120

  environment {
    variables = {
      ATHENA_WORKGROUP = aws_athena_workgroup.backfill.name
    }
  }

  tags = var.tags
}
