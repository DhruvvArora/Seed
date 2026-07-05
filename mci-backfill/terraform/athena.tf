# The Athena workgroup all backfill queries run under, and the S3 bucket that
# holds both the CTAS temp table's data and Athena's own query-result output.
# This is a one-time migration, so the bucket auto-expires objects rather than
# relying on someone remembering to clean up manually.

resource "aws_athena_workgroup" "backfill" {
  name = "backfill-workgroup"

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true

    result_configuration {
      output_location = "s3://${aws_s3_bucket.backfill_temp.id}/query-results/"
    }
  }

  tags = var.tags
}

resource "aws_s3_bucket" "backfill_temp" {
  bucket = "${var.env}-backfill-temp"
  tags   = var.tags
}

resource "aws_s3_bucket_public_access_block" "backfill_temp" {
  bucket                  = aws_s3_bucket.backfill_temp.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "backfill_temp" {
  bucket = aws_s3_bucket.backfill_temp.id

  rule {
    id     = "expire-temp-data"
    status = "Enabled"

    filter {}

    expiration {
      days = var.temp_bucket_retention_days
    }
  }
}
