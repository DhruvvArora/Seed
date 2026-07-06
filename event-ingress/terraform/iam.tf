# One IAM role shared by all four event-ingress Lambdas. Permissions are scoped
# to exactly what each Lambda needs:
#   joust              -- DynamoDB GetItem on apikey-metadata
#   event-enqueue      -- DynamoDB GetItem + lambda:InvokeFunction on MCI + Kinesis PutRecords
#   mparticle-enqueue  -- DynamoDB GetItem on apikey-metadata + Kinesis PutRecords (enqueue stream)
#   mparticle-processing -- lambda:InvokeFunction on MCI + Kinesis PutRecords (both internal streams)
#                         + Kinesis GetRecords on mparticle-enqueue (event source mapping)
#
# CloudWatch Logs is attached via the standard AWS-managed policy.

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "event_ingress_lambda" {
  name               = "${var.env}-event-ingress-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = var.tags
}

# Standard Lambda logging.
resource "aws_iam_role_policy_attachment" "event_ingress_logs" {
  role       = aws_iam_role.event_ingress_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "event_ingress_lambda_permissions" {
  # joust + mparticle-enqueue: read the apikey-metadata table.
  statement {
    sid       = "ApiKeyTableRead"
    actions   = ["dynamodb:GetItem"]
    resources = [aws_dynamodb_table.apikey_metadata.arn]
  }

  # event-enqueue + mparticle-processing: invoke MCI to resolve customer IDs.
  # Unqualified ARN covers $LATEST invocations. The LIVE alias is deferred
  # (same as P1 and P2); override mci_function_alias to $LATEST for the dev run.
  statement {
    sid       = "InvokeMci"
    actions   = ["lambda:InvokeFunction"]
    resources = [var.mci_get_internal_function_arn]
  }

  # event-enqueue + mparticle-processing: write events to the two internal streams.
  statement {
    sid     = "WriteInternalStreams"
    actions = ["kinesis:PutRecords", "kinesis:PutRecord"]
    resources = [
      aws_kinesis_stream.transactions_internal.arn,
      aws_kinesis_stream.action_internal.arn,
    ]
  }

  # mparticle-enqueue: write raw batches to the enqueue stream.
  statement {
    sid       = "WriteMpEnqueueStream"
    actions   = ["kinesis:PutRecords", "kinesis:PutRecord"]
    resources = [aws_kinesis_stream.mparticle_enqueue.arn]
  }

  # mparticle-processing: read from the mparticle-enqueue Kinesis stream
  # (required for the event source mapping to function).
  statement {
    sid = "ReadMpEnqueueStream"
    actions = [
      "kinesis:GetRecords",
      "kinesis:GetShardIterator",
      "kinesis:DescribeStream",
      "kinesis:DescribeStreamSummary",
      "kinesis:ListShards",
      "kinesis:ListStreams",
    ]
    resources = [aws_kinesis_stream.mparticle_enqueue.arn]
  }
}

resource "aws_iam_role_policy" "event_ingress_lambda" {
  name   = "${var.env}-event-ingress-lambda-policy"
  role   = aws_iam_role.event_ingress_lambda.id
  policy = data.aws_iam_policy_document.event_ingress_lambda_permissions.json
}
