# Two roles: one for the four backfill Lambdas (used when AWS Lambda executes
# their code), and one for the Step Function itself (used whenever the ASL
# directly invokes an Athena or Lambda SDK integration, i.e. the CollectCustomerIDs
# and DropTempTable* states, and every lambda:invoke Task).

data "aws_caller_identity" "current" {}

# ---- Lambda execution role ---------------------------------------------------

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "backfill_lambda" {
  name               = "${var.env}-mci-backfill-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "backfill_lambda_permissions" {
  # list-tenants reads the tenant registry (a small table; a Scan is
  # appropriate, see src/backfill/store/tenant_registry.py).
  statement {
    sid       = "TenantRegistryRead"
    actions   = ["dynamodb:Scan"]
    resources = [aws_dynamodb_table.tenant_registry.arn]
  }

  # backfill-mci calls MCI through the invoker client. Scoped to MCI's
  # get-internal function specifically (both unqualified and any alias),
  # not a blanket lambda:InvokeFunction on everything.
  statement {
    sid     = "InvokeMci"
    actions = ["lambda:InvokeFunction"]
    resources = [
      var.mci_get_internal_function_arn,
      "${var.mci_get_internal_function_arn}:*",
    ]
  }

  # athena-query-runner: run and poll queries, then fetch typed results.
  statement {
    sid = "AthenaQuery"
    actions = [
      "athena:StartQueryExecution",
      "athena:GetQueryExecution",
      "athena:GetQueryResults",
      "athena:StopQueryExecution",
      "athena:GetWorkGroup",
    ]
    resources = ["*"] # Athena query/workgroup resources are not individually ARN-addressable for these actions.
  }

  # Athena needs Glue Data Catalog access to plan and execute any query
  # against the 4 source tables, regardless of who is invoking it.
  statement {
    sid = "GlueCatalogRead"
    actions = [
      "glue:GetDatabase",
      "glue:GetTable",
      "glue:GetTables",
      "glue:GetPartitions",
    ]
    resources = ["*"]
  }

  # Read the historical data lake data the 4 source tables sit on top of.
  dynamic "statement" {
    for_each = length(var.source_data_s3_bucket_arns) > 0 ? [1] : []
    content {
      sid     = "SourceDataRead"
      actions = ["s3:GetObject", "s3:ListBucket"]
      resources = flatten([
        for arn in var.source_data_s3_bucket_arns : [arn, "${arn}/*"]
      ])
    }
  }

  # Read/write the CTAS temp table's data and Athena's own query-result output.
  statement {
    sid     = "TempBucketAccess"
    actions = ["s3:GetObject", "s3:PutObject", "s3:ListBucket", "s3:GetBucketLocation"]
    resources = [
      aws_s3_bucket.backfill_temp.arn,
      "${aws_s3_bucket.backfill_temp.arn}/*",
    ]
  }
}

resource "aws_iam_role_policy" "backfill_lambda_permissions" {
  name   = "${var.env}-mci-backfill-lambda-permissions"
  role   = aws_iam_role.backfill_lambda.id
  policy = data.aws_iam_policy_document.backfill_lambda_permissions.json
}

resource "aws_iam_role_policy_attachment" "backfill_lambda_logs" {
  role       = aws_iam_role.backfill_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# ---- Step Function execution role ---------------------------------------------

data "aws_iam_policy_document" "states_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "backfill_state_machine" {
  name               = "${var.env}-mci-backfill-state-machine"
  assume_role_policy = data.aws_iam_policy_document.states_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "backfill_state_machine_permissions" {
  # Every lambda:invoke Task in the ASL: ListTenants, IteratorUtil,
  # GetMinMaxRowNumber, GetCustomerIDs, BackfillMCI.
  statement {
    sid     = "InvokeBackfillLambdas"
    actions = ["lambda:InvokeFunction"]
    resources = [
      aws_lambda_function.list_tenants.arn,
      aws_lambda_function.iteration_utility.arn,
      aws_lambda_function.athena_query_runner.arn,
      aws_lambda_function.backfill_mci.arn,
    ]
  }

  # CollectCustomerIDs, DropTempTable, and DropTempTableOnFailure call Athena
  # directly via the SDK integration (arn:aws:states:::athena:startQueryExecution.sync),
  # not through a Lambda, so the state machine's own role needs these.
  statement {
    sid = "AthenaDirectSdkIntegration"
    actions = [
      "athena:StartQueryExecution",
      "athena:GetQueryExecution",
      "athena:StopQueryExecution",
      "athena:GetWorkGroup",
    ]
    resources = ["*"]
  }

  statement {
    sid = "GlueCatalogAccess"
    actions = [
      "glue:GetDatabase",
      "glue:GetTable",
      "glue:GetTables",
      "glue:GetPartitions",
      "glue:CreateTable",
      "glue:DeleteTable",
    ]
    resources = ["*"]
  }

  dynamic "statement" {
    for_each = length(var.source_data_s3_bucket_arns) > 0 ? [1] : []
    content {
      sid     = "SourceDataRead"
      actions = ["s3:GetObject", "s3:ListBucket"]
      resources = flatten([
        for arn in var.source_data_s3_bucket_arns : [arn, "${arn}/*"]
      ])
    }
  }

  statement {
    sid     = "TempBucketAccess"
    actions = ["s3:GetObject", "s3:PutObject", "s3:ListBucket", "s3:GetBucketLocation"]
    resources = [
      aws_s3_bucket.backfill_temp.arn,
      "${aws_s3_bucket.backfill_temp.arn}/*",
    ]
  }
}

resource "aws_iam_role_policy" "backfill_state_machine_permissions" {
  name   = "${var.env}-mci-backfill-state-machine-permissions"
  role   = aws_iam_role.backfill_state_machine.id
  policy = data.aws_iam_policy_document.backfill_state_machine_permissions.json
}
