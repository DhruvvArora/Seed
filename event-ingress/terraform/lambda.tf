# Four Lambda functions for the Event Ingress Service. All share one deployment
# zip (event-ingress + mci-core path dependency) and one IAM role.

locals {
  common_lambda = {
    filename         = var.lambda_zip_path
    source_code_hash = filebase64sha256(var.lambda_zip_path)
    runtime          = "python3.12"
    role             = aws_iam_role.event_ingress_lambda.arn
  }
}

# ---- joust (API key authorizer) --------------------------------------------

resource "aws_lambda_function" "joust" {
  function_name    = "${var.env}-joust"
  handler          = "event_ingress.handlers.joust.handler"
  filename         = local.common_lambda.filename
  source_code_hash = local.common_lambda.source_code_hash
  runtime          = local.common_lambda.runtime
  role             = local.common_lambda.role

  memory_size = 256
  timeout     = 5

  environment {
    variables = {
      API_KEY_TABLE_NAME = aws_dynamodb_table.apikey_metadata.name
      CACHE_TTL_SECONDS  = tostring(var.cache_ttl_seconds)
    }
  }

  tags = var.tags
}

resource "aws_lambda_permission" "apigw_invoke_joust" {
  statement_id  = "AllowAPIGatewayInvokeJoust"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.joust.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.event_api.execution_arn}/*/*"
}

# ---- event-enqueue ---------------------------------------------------------

resource "aws_lambda_function" "event_enqueue" {
  function_name    = "${var.env}-event-enqueue"
  handler          = "event_ingress.handlers.event_enqueue.handler"
  filename         = local.common_lambda.filename
  source_code_hash = local.common_lambda.source_code_hash
  runtime          = local.common_lambda.runtime
  role             = local.common_lambda.role

  memory_size = 256
  timeout     = 30

  # Reserved concurrency omitted for dev: the account's concurrent execution
  # limit is too low to reserve capacity here without breaching the minimum
  # unreserved floor. Re-add reserved_concurrent_executions = 20 for prod.

  environment {
    variables = {
      TRANSACTIONS_STREAM_NAME = aws_kinesis_stream.transactions_internal.name
      ACTIONS_STREAM_NAME      = aws_kinesis_stream.action_internal.name
      MCI_FUNCTION_NAME        = var.mci_get_internal_function_name
      MCI_FUNCTION_ALIAS       = var.mci_function_alias
    }
  }

  tags = var.tags
}

resource "aws_lambda_permission" "apigw_invoke_event_enqueue" {
  statement_id  = "AllowAPIGatewayInvokeEventEnqueue"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.event_enqueue.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.event_api.execution_arn}/*/*"
}

# ---- mparticle-enqueue -----------------------------------------------------

resource "aws_lambda_function" "mparticle_enqueue" {
  function_name    = "${var.env}-mparticle-enqueue"
  handler          = "event_ingress.handlers.mparticle_enqueue.handler"
  filename         = local.common_lambda.filename
  source_code_hash = local.common_lambda.source_code_hash
  runtime          = local.common_lambda.runtime
  role             = local.common_lambda.role

  memory_size = 256
  timeout     = 30

  environment {
    variables = {
      API_KEY_TABLE_NAME       = aws_dynamodb_table.apikey_metadata.name
      CACHE_TTL_SECONDS        = tostring(var.cache_ttl_seconds)
      MPARTICLE_ENQUEUE_STREAM = aws_kinesis_stream.mparticle_enqueue.name
    }
  }

  tags = var.tags
}

resource "aws_lambda_permission" "apigw_invoke_mparticle_enqueue" {
  statement_id  = "AllowAPIGatewayInvokeMpEnqueue"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.mparticle_enqueue.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.event_api.execution_arn}/*/*"
}

# ---- mparticle-processing --------------------------------------------------

resource "aws_lambda_function" "mparticle_processing" {
  function_name    = "${var.env}-mparticle-processing"
  handler          = "event_ingress.handlers.mparticle_processing.handler"
  filename         = local.common_lambda.filename
  source_code_hash = local.common_lambda.source_code_hash
  runtime          = local.common_lambda.runtime
  role             = local.common_lambda.role

  memory_size = 256
  timeout     = 30

  environment {
    variables = {
      TRANSACTIONS_STREAM_NAME = aws_kinesis_stream.transactions_internal.name
      ACTIONS_STREAM_NAME      = aws_kinesis_stream.action_internal.name
      MCI_FUNCTION_NAME        = var.mci_get_internal_function_name
      MCI_FUNCTION_ALIAS       = var.mci_function_alias
    }
  }

  tags = var.tags
}

resource "aws_lambda_event_source_mapping" "mparticle_processing_trigger" {
  event_source_arn               = aws_kinesis_stream.mparticle_enqueue.arn
  function_name                  = aws_lambda_function.mparticle_processing.arn
  starting_position              = "LATEST"
  batch_size                     = 100
  maximum_retry_attempts         = 6
  bisect_batch_on_function_error = true
  parallelization_factor         = 1
}

# Provisioned concurrency and the LIVE alias are omitted for the dev run.
# Add them back when deploying to a production account with higher limits.
