# One shared role, matching audience-ingress's pattern: scoped to the union
# of what all three Lambdas need, rather than a role per function.

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "outbound_egress_lambda" {
  name               = "${var.env}-outbound-egress-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.outbound_egress_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "lambda_permissions" {
  # aqueduct-distributor: consume the plague stream (event source mapping).
  statement {
    sid = "ReadPlagueStream"
    actions = [
      "kinesis:GetRecords",
      "kinesis:GetShardIterator",
      "kinesis:DescribeStream",
      "kinesis:DescribeStreamSummary",
      "kinesis:ListShards",
      "kinesis:ListStreams"
    ]
    resources = [aws_kinesis_stream.plague.arn]
  }

  # Decrypt records read from the KMS-encrypted stream. The stream uses the
  # AWS-managed alias/aws/kinesis key, whose ARN is not directly referenceable
  # here, so this is scoped by the kinesis via-service condition instead
  # (same pattern as audience-ingress and event-ingress).
  statement {
    sid       = "DecryptPlagueStream"
    actions   = ["kms:Decrypt"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["kinesis.${var.aws_region}.amazonaws.com"]
    }
  }

  # aqueduct-distributor reads and updates connection_status; aqueduct-writer
  # creates/updates/deletes; aqueduct-reader reads. The union covers all of
  # DynamoDB's item-level operations on this table for all three Lambdas.
  statement {
    sid = "ConnectorsTableAccess"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:BatchGetItem",
      "dynamodb:PutItem",
      "dynamodb:DeleteItem",
      "dynamodb:UpdateItem",
      "dynamodb:Query"
    ]
    resources = [aws_dynamodb_table.connectors.arn]
  }

  # aqueduct-distributor: load a connector's configured JQ script.
  statement {
    sid       = "TransformationsTableRead"
    actions   = ["dynamodb:GetItem"]
    resources = [aws_dynamodb_table.transformations.arn]
  }

  # aqueduct-writer encrypts destination configs and integration credentials
  # on write; aqueduct-distributor decrypts them at delivery time.
  # aqueduct-reader never calls either.
  statement {
    sid       = "ConnectorDestinationCrypto"
    actions   = ["kms:Encrypt", "kms:Decrypt"]
    resources = [aws_kms_key.connector_destination.arn]
  }

  # aqueduct-distributor: cross-account Kinesis delivery. The destination
  # account and role name are tenant-controlled, stored per-connector, and
  # only known at connector-config time, not at Terraform apply time, so this
  # is intentionally broad on OUR side. Actual authorization is enforced by
  # the trust policy on the DESTINATION account's role, which must explicitly
  # allow this role's ARN (aws_iam_role.outbound_egress_lambda.arn) to assume
  # it. Terraform here cannot scope this any tighter without knowing every
  # brand's AWS account ahead of time.
  statement {
    sid       = "AssumeCrossAccountDeliveryRole"
    actions   = ["sts:AssumeRole"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "lambda_permissions" {
  name   = "${var.env}-outbound-egress-lambda-policy"
  role   = aws_iam_role.outbound_egress_lambda.id
  policy = data.aws_iam_policy_document.lambda_permissions.json
}
