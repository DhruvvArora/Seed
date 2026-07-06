# The batch ingest state machine, the SNS topic its failure path publishes to,
# and the EventBridge rule that starts it when a CSV lands in the uploads bucket.

resource "aws_sns_topic" "audience_ingest_alerts" {
  name = "${var.env}-audience-ingest-alerts"
  tags = var.tags
}

resource "aws_sfn_state_machine" "audience_ingest" {
  name     = "${var.env}-audience-ingest"
  role_arn = aws_iam_role.audience_ingest_sfn.arn

  definition = templatefile("${path.module}/../step_function/audience_ingest.asl.json", {
    ingest_function_arn  = aws_lambda_function.audience_ingest.arn
    parquet_function_arn = aws_lambda_function.audience_parquet_writer.arn
    metadata_table_name  = aws_dynamodb_table.audience_metadata.name
    alert_topic_arn      = aws_sns_topic.audience_ingest_alerts.arn
  })

  tags = var.tags
}

# --- S3 -> EventBridge -> StartExecution --------------------------------------
# S3 cannot start a Step Function directly. The uploads bucket emits "Object
# Created" to EventBridge (enabled in s3.tf); this rule matches those events for
# our bucket and starts the state machine, mapping bucket+key into the ASL input.

resource "aws_cloudwatch_event_rule" "audience_upload" {
  name        = "${var.env}-audience-upload"
  description = "Start audience ingest when a CSV is uploaded to the uploads bucket."

  event_pattern = jsonencode({
    source        = ["aws.s3"]
    "detail-type" = ["Object Created"]
    detail = {
      bucket = {
        name = [aws_s3_bucket.audience_uploads.bucket]
      }
    }
  })

  tags = var.tags
}

resource "aws_cloudwatch_event_target" "audience_upload" {
  rule     = aws_cloudwatch_event_rule.audience_upload.name
  arn      = aws_sfn_state_machine.audience_ingest.arn
  role_arn = aws_iam_role.audience_upload_events.arn

  # Map the S3 event into the state machine's expected {s3_bucket, s3_key} input.
  input_transformer {
    input_paths = {
      bucket = "$.detail.bucket.name"
      key    = "$.detail.object.key"
    }
    input_template = <<-EOT
      {
        "s3_bucket": "<bucket>",
        "s3_key": "<key>"
      }
    EOT
  }
}
