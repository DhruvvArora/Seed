# REST API Gateway for the Event Ingress Service.
#
# Two endpoints:
#   POST /event          -- direct REST API for clients; guarded by joust authorizer
#   POST /mparticle      -- mParticle Firehose webhook; no Lambda authorizer (the
#                           mparticle-enqueue Lambda validates the key internally,
#                           since the Firehose payload carries the key in the body)
#
# The joust TOKEN authorizer reads the x-api-key header and injects tenant_id
# into the request context. event-enqueue reads tenant_id from there rather than
# doing its own DynamoDB lookup.

# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------

resource "aws_api_gateway_rest_api" "event_api" {
  name        = "${var.env}-event-ingress"
  description = "Event Ingress Service -- direct REST and mParticle Firehose ingest paths"

  endpoint_configuration {
    types = ["REGIONAL"]
  }

  tags = var.tags
}

# ---------------------------------------------------------------------------
# joust: Lambda TOKEN authorizer on the x-api-key header
# ---------------------------------------------------------------------------

# API Gateway needs an IAM role to invoke the authorizer Lambda.
resource "aws_iam_role" "apigw_authorizer_invoker" {
  name = "${var.env}-apigw-authorizer-invoker"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "apigateway.amazonaws.com" }
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy" "apigw_authorizer_invoker" {
  name = "${var.env}-apigw-invoke-joust"
  role = aws_iam_role.apigw_authorizer_invoker.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_function.joust.arn
    }]
  })
}

resource "aws_api_gateway_authorizer" "joust" {
  name                             = "${var.env}-joust"
  rest_api_id                      = aws_api_gateway_rest_api.event_api.id
  authorizer_uri                   = aws_lambda_function.joust.invoke_arn
  authorizer_credentials           = aws_iam_role.apigw_authorizer_invoker.arn
  type                             = "TOKEN"
  identity_source                  = "method.request.header.x-api-key"
  # 5-minute API Gateway cache for the authorizer result, keyed by the API key
  # value. Complements the in-memory Lambda cache inside joust itself.
  authorizer_result_ttl_in_seconds = 300
}

# ---------------------------------------------------------------------------
# POST /event -- direct REST API (authenticated via joust)
# ---------------------------------------------------------------------------

resource "aws_api_gateway_resource" "event" {
  rest_api_id = aws_api_gateway_rest_api.event_api.id
  parent_id   = aws_api_gateway_rest_api.event_api.root_resource_id
  path_part   = "event"
}

resource "aws_api_gateway_method" "post_event" {
  rest_api_id   = aws_api_gateway_rest_api.event_api.id
  resource_id   = aws_api_gateway_resource.event.id
  http_method   = "POST"
  authorization = "CUSTOM"
  authorizer_id = aws_api_gateway_authorizer.joust.id
}

resource "aws_api_gateway_integration" "post_event" {
  rest_api_id             = aws_api_gateway_rest_api.event_api.id
  resource_id             = aws_api_gateway_resource.event.id
  http_method             = aws_api_gateway_method.post_event.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.event_enqueue.invoke_arn
}

# ---------------------------------------------------------------------------
# POST /mparticle -- mParticle Firehose webhook (no Lambda authorizer)
# ---------------------------------------------------------------------------

resource "aws_api_gateway_resource" "mparticle" {
  rest_api_id = aws_api_gateway_rest_api.event_api.id
  parent_id   = aws_api_gateway_rest_api.event_api.root_resource_id
  path_part   = "mparticle"
}

resource "aws_api_gateway_method" "post_mparticle" {
  rest_api_id   = aws_api_gateway_rest_api.event_api.id
  resource_id   = aws_api_gateway_resource.mparticle.id
  http_method   = "POST"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "post_mparticle" {
  rest_api_id             = aws_api_gateway_rest_api.event_api.id
  resource_id             = aws_api_gateway_resource.mparticle.id
  http_method             = aws_api_gateway_method.post_mparticle.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.mparticle_enqueue.invoke_arn
}

# ---------------------------------------------------------------------------
# Deployment and stage
# ---------------------------------------------------------------------------

resource "aws_api_gateway_deployment" "event_api" {
  rest_api_id = aws_api_gateway_rest_api.event_api.id

  # Force redeployment when the API structure changes.
  triggers = {
    redeployment = sha1(jsonencode([
      aws_api_gateway_resource.event.id,
      aws_api_gateway_method.post_event.id,
      aws_api_gateway_integration.post_event.id,
      aws_api_gateway_resource.mparticle.id,
      aws_api_gateway_method.post_mparticle.id,
      aws_api_gateway_integration.post_mparticle.id,
      aws_api_gateway_authorizer.joust.id,
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_api_gateway_stage" "event_api" {
  deployment_id = aws_api_gateway_deployment.event_api.id
  rest_api_id   = aws_api_gateway_rest_api.event_api.id
  stage_name    = var.env

  # Usage plan and throttling (spec: 10,000 RPS, burst 5,000).
  # These are applied via a usage plan below.

  tags = var.tags
}

# ---------------------------------------------------------------------------
# Usage plan: enforce the spec's rate limits
# ---------------------------------------------------------------------------

resource "aws_api_gateway_usage_plan" "event_api" {
  name = "${var.env}-event-ingress-usage-plan"

  api_stages {
    api_id = aws_api_gateway_rest_api.event_api.id
    stage  = aws_api_gateway_stage.event_api.stage_name
  }

  throttle_settings {
    rate_limit  = 10000
    burst_limit = 5000
  }

  tags = var.tags
}
