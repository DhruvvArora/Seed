# Three Lambdas, one shared deployment zip, one shared IAM role (see iam.tf).
# Only aqueduct-distributor attaches the jq layer; it is the only one that
# ever applies a JQ transformation. Reserved concurrency is intentionally
# omitted on all three, matching the lesson from every earlier project: dev
# accounts have a low account-level unreserved concurrency pool, and setting
# reserved concurrency there fails deploys.
#
# Handler paths use the src-layout package:
#   outbound_egress.handlers.<module>.handler

locals {
  common_env = {
    CONNECTORS_TABLE_NAME      = aws_dynamodb_table.connectors.name
    TRANSFORMATIONS_TABLE_NAME = aws_dynamodb_table.transformations.name
  }
}

# --- aqueduct-distributor: plague stream consumer, fan-out + deliver ---------
resource "aws_lambda_function" "aqueduct_distributor" {
  function_name = "${var.env}-aqueduct-distributor"
  role          = aws_iam_role.outbound_egress_lambda.arn
  runtime       = "python3.12"
  handler       = "outbound_egress.handlers.aqueduct_distributor.handler"

  filename         = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)

  memory_size = 512
  timeout     = 60

  # Only this Lambda applies JQ transformations, so only this one needs the
  # self-built jq layer (see variables.tf for why it can't come from a
  # managed layer the way AWSSDKPandas does).
  layers = [var.jq_layer_arn]

  environment {
    variables = local.common_env
  }

  tags = var.tags
}

# Kinesis trigger. Spec: batch size 100, max 3 retries, bisect-on-error.
# This is Lambda's batch-level retry for the trigger itself; it sits on top
# of, and is separate from, the per-connector delivery retries the handler
# does internally (see aqueduct_distributor.py).
resource "aws_lambda_event_source_mapping" "aqueduct_distributor" {
  event_source_arn               = aws_kinesis_stream.plague.arn
  function_name                  = aws_lambda_function.aqueduct_distributor.arn
  starting_position              = "LATEST"
  batch_size                     = 100
  maximum_retry_attempts         = 3
  bisect_batch_on_function_error = true
  maximum_record_age_in_seconds  = 604800 # 7 days, matches stream retention
}

# --- aqueduct-writer: CRUD API for connector management, invoked by GraphQL --
resource "aws_lambda_function" "aqueduct_writer" {
  function_name = "${var.env}-aqueduct-writer"
  role          = aws_iam_role.outbound_egress_lambda.arn
  runtime       = "python3.12"
  handler       = "outbound_egress.handlers.aqueduct_writer.handler"

  filename         = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)

  memory_size = 256
  timeout     = 30

  environment {
    variables = merge(local.common_env, {
      CONNECTOR_KMS_KEY_ID = aws_kms_key.connector_destination.key_id
    })
  }

  tags = var.tags
}

# --- aqueduct-reader: internal connector config loader -----------------------
resource "aws_lambda_function" "aqueduct_reader" {
  function_name = "${var.env}-aqueduct-reader"
  role          = aws_iam_role.outbound_egress_lambda.arn
  runtime       = "python3.12"
  handler       = "outbound_egress.handlers.aqueduct_reader.handler"

  filename         = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)

  memory_size = 256
  timeout     = 30

  environment {
    variables = local.common_env
  }

  tags = var.tags
}
