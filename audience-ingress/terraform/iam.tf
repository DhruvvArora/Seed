# Three roles:
#   audience_ingress_lambda -- shared by all 3 Lambdas, scoped to the union of
#                              what they each need.
#   audience_ingest_sfn     -- the state machine: invoke the 2 task Lambdas,
#                              update metadata, publish alerts.
#   audience_upload_events  -- EventBridge: start the state machine.

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "audience_ingress_lambda" {
  name               = "${var.env}-audience-ingress-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.audience_ingress_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "lambda_permissions" {
  # audience-ingest: read uploaded CSVs.
  statement {
    sid       = "ReadUploads"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.audience_uploads.arn}/*"]
  }

  # audience-parquet-writer: write membership parquet.
  statement {
    sid       = "WriteMembershipParquet"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.audience_membership.arn}/*"]
  }

  # audience-ingest + audience-reducer: invoke MCI to resolve customer ids.
  # Unqualified ARN covers $LATEST (LIVE alias deferred, as in P2/P3).
  statement {
    sid     = "InvokeMci"
    actions = ["lambda:InvokeFunction"]
    resources = [
      var.mci_get_internal_function_arn,
      "${var.mci_get_internal_function_arn}:${var.mci_function_alias}"
    ]
  }

  # audience-reducer: upsert membership rows.
  statement {
    sid       = "WriteMembership"
    actions   = ["dynamodb:PutItem"]
    resources = [aws_dynamodb_table.audience_membership.arn]
  }

  # audience-reducer: consume the audience-events stream (event source mapping).
  statement {
    sid = "ReadAudienceEvents"
    actions = [
      "kinesis:GetRecords",
      "kinesis:GetShardIterator",
      "kinesis:DescribeStream",
      "kinesis:DescribeStreamSummary",
      "kinesis:ListShards",
      "kinesis:ListStreams"
    ]
    resources = [aws_kinesis_stream.audience_events.arn]
  }

  # Decrypt records read from the KMS-encrypted stream. The stream uses the
  # AWS-managed alias/aws/kinesis key, whose ARN is not directly referenceable
  # here, so this is scoped by the kinesis via-service condition instead.
  statement {
    sid       = "DecryptStream"
    actions   = ["kms:Decrypt"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["kinesis.${var.aws_region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "lambda_permissions" {
  name   = "${var.env}-audience-ingress-lambda-policy"
  role   = aws_iam_role.audience_ingress_lambda.id
  policy = data.aws_iam_policy_document.lambda_permissions.json
}

# --- State machine role -------------------------------------------------------
data "aws_iam_policy_document" "sfn_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "audience_ingest_sfn" {
  name               = "${var.env}-audience-ingest-sfn"
  assume_role_policy = data.aws_iam_policy_document.sfn_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "sfn_permissions" {
  statement {
    sid     = "InvokeTaskLambdas"
    actions = ["lambda:InvokeFunction"]
    resources = [
      aws_lambda_function.audience_ingest.arn,
      aws_lambda_function.audience_parquet_writer.arn
    ]
  }

  statement {
    sid       = "UpdateAudienceMetadata"
    actions   = ["dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.audience_metadata.arn]
  }

  statement {
    sid       = "PublishAlerts"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.audience_ingest_alerts.arn]
  }
}

resource "aws_iam_role_policy" "sfn_permissions" {
  name   = "${var.env}-audience-ingest-sfn-policy"
  role   = aws_iam_role.audience_ingest_sfn.id
  policy = data.aws_iam_policy_document.sfn_permissions.json
}

# --- EventBridge role ---------------------------------------------------------
data "aws_iam_policy_document" "events_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "audience_upload_events" {
  name               = "${var.env}-audience-upload-events"
  assume_role_policy = data.aws_iam_policy_document.events_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "events_permissions" {
  statement {
    sid       = "StartIngest"
    actions   = ["states:StartExecution"]
    resources = [aws_sfn_state_machine.audience_ingest.arn]
  }
}

resource "aws_iam_role_policy" "events_permissions" {
  name   = "${var.env}-audience-upload-events-policy"
  role   = aws_iam_role.audience_upload_events.id
  policy = data.aws_iam_policy_document.events_permissions.json
}
