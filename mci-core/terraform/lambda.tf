# The three MCI Lambdas. All share one deployment package and one IAM role;
# they differ only in which handler function AWS calls and a few env vars.

locals {
  # Env vars common to all three functions.
  common_env = {
    TABLE_NAME                    = aws_dynamodb_table.mci.name
    LOG_BUCKET                    = aws_s3_bucket.logs.id
    AUDIT_BUCKET                  = aws_s3_bucket.audit.id
    DYNAMO_PARALLELIZATION_FACTOR = tostring(var.dynamo_parallelization_factor)
  }
}

# ---- get-internal-customer-ids ---------------------------------------------
resource "aws_lambda_function" "get_internal" {
  function_name = "${var.env}-get-internal-customer-ids"
  role          = aws_iam_role.mci_lambda.arn
  runtime       = "python3.12"
  handler       = "mci.handlers.get_internal_customer_ids.handler"

  filename         = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)

  memory_size = 2048 # spec: 2,048 MB
  timeout     = 900  # spec: 900 seconds

  environment {
    variables = local.common_env
  }

  tags = var.tags
}

# Callers handle their own retry logic, so AWS should not auto-retry async
# invokes of this function (spec: maximum_retry_attempts = 0).
resource "aws_lambda_function_event_invoke_config" "get_internal" {
  function_name          = aws_lambda_function.get_internal.function_name
  maximum_retry_attempts = 0
}

# ---- get-external-customer-ids ---------------------------------------------
resource "aws_lambda_function" "get_external" {
  function_name = "${var.env}-get-external-customer-ids"
  role          = aws_iam_role.mci_lambda.arn
  runtime       = "python3.12"
  handler       = "mci.handlers.get_external_customer_ids.handler"

  filename         = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)

  memory_size = 512
  timeout     = 60

  environment {
    variables = local.common_env
  }

  tags = var.tags
}

# ---- forget-external-customer-ids ------------------------------------------
resource "aws_lambda_function" "forget_external" {
  function_name = "${var.env}-forget-external-customer-ids"
  role          = aws_iam_role.mci_lambda.arn
  runtime       = "python3.12"
  handler       = "mci.handlers.forget_external_customer_ids.handler"

  filename         = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)

  memory_size = 512
  timeout     = 300

  environment {
    variables = local.common_env
  }

  tags = var.tags
}
