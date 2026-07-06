# Three Lambdas, all sharing one deployment zip (audience-ingress + mci-core
# path dependency) and one IAM role (see iam.tf). Only the parquet writer
# attaches the AWSSDKPandas layer.
#
# Handler paths use the src-layout package: audience_ingress.handlers.<mod>.handler
# Reserved concurrency is intentionally omitted (dev accounts have a low
# account-level unreserved pool; the same lesson as P3).

locals {
  common_env = {
    MCI_FUNCTION_NAME  = var.mci_get_internal_function_name
    MCI_FUNCTION_ALIAS = var.mci_function_alias
  }
}

# --- audience-ingest: Step Function task 1 (decompress + MCI resolve) ---------
resource "aws_lambda_function" "audience_ingest" {
  function_name = "${var.env}-audience-ingest"
  role          = aws_iam_role.audience_ingress_lambda.arn
  runtime       = "python3.12"
  handler       = "audience_ingress.handlers.audience_ingest.handler"
  filename      = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)

  memory_size = 2048 # spec: large CSVs processed in memory
  timeout     = 900

  environment {
    variables = merge(local.common_env, {
      MCI_PROBE_EXISTING = tostring(var.mci_probe_existing)
    })
  }

  tags = var.tags
}

# --- audience-parquet-writer: Step Function task 2 (write parquet) ------------
resource "aws_lambda_function" "audience_parquet_writer" {
  function_name = "${var.env}-audience-parquet-writer"
  role          = aws_iam_role.audience_ingress_lambda.arn
  runtime       = "python3.12"
  handler       = "audience_ingress.handlers.audience_parquet_writer.handler"
  filename      = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)

  memory_size = 1024
  timeout     = 300

  # pyarrow/pandas come from the managed layer, not the zip.
  layers = [var.pandas_layer_arn]

  environment {
    variables = {
      AUDIENCE_MEMBERSHIP_BUCKET = aws_s3_bucket.audience_membership.bucket
    }
  }

  tags = var.tags
}

# --- audience-reducer: Kinesis consumer (streaming add/remove) ----------------
resource "aws_lambda_function" "audience_reducer" {
  function_name = "${var.env}-audience-reducer"
  role          = aws_iam_role.audience_ingress_lambda.arn
  runtime       = "python3.12"
  handler       = "audience_ingress.handlers.audience_reducer.handler"
  filename      = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)

  memory_size = 256
  timeout     = 30

  environment {
    variables = merge(local.common_env, {
      MEMBERSHIP_TABLE_NAME         = aws_dynamodb_table.audience_membership.name
      DYNAMO_PARALLELIZATION_FACTOR = tostring(var.reducer_parallelization_factor)
    })
  }

  tags = var.tags
}

# Kinesis trigger for the reducer. Spec: batch 100, retries 7, bisect on error,
# parallelization factor up to 10.
resource "aws_lambda_event_source_mapping" "audience_reducer" {
  event_source_arn                   = aws_kinesis_stream.audience_events.arn
  function_name                      = aws_lambda_function.audience_reducer.arn
  starting_position                  = "LATEST"
  batch_size                         = 100
  maximum_retry_attempts             = 7
  bisect_batch_on_function_error     = true
  parallelization_factor             = 10
  maximum_record_age_in_seconds      = 604800 # 7 days, matches retention
}
