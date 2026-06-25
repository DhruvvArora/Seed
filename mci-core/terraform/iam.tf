# IAM ties identity to permission. Each Lambda runs AS a role; the role grants
# exactly the access the code needs and nothing more (least privilege).

# Trust policy: allows the Lambda service to assume this role.
data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "mci_lambda" {
  name               = "${var.env}-mci-core-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = var.tags
}

# Permission policy: the specific DynamoDB and S3 actions the handlers perform.
data "aws_iam_policy_document" "mci_permissions" {
  # Reads and writes on the main table.
  statement {
    sid = "TableAccess"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:BatchGetItem",
      "dynamodb:PutItem",
      "dynamodb:DeleteItem",
    ]
    resources = [aws_dynamodb_table.mci.arn]
  }

  # Query is only needed against the GSI (reverse lookup). The GSI ARN is the
  # table ARN plus "/index/*".
  statement {
    sid       = "IndexQuery"
    actions   = ["dynamodb:Query"]
    resources = ["${aws_dynamodb_table.mci.arn}/index/*"]
  }

  # Write structured logs and audit records to the two buckets.
  statement {
    sid     = "S3Write"
    actions = ["s3:PutObject"]
    resources = [
      "${aws_s3_bucket.logs.arn}/*",
      "${aws_s3_bucket.audit.arn}/*",
    ]
  }
}

resource "aws_iam_role_policy" "mci_permissions" {
  name   = "${var.env}-mci-core-permissions"
  role   = aws_iam_role.mci_lambda.id
  policy = data.aws_iam_policy_document.mci_permissions.json
}

# Managed policy that lets Lambda write its logs to CloudWatch. Standard for
# every Lambda; without it you get no logs.
resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.mci_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}
