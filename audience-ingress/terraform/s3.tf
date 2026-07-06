# Two buckets:
#   audience-uploads    -- clients drop gzipped CSVs here; ObjectCreated fans
#                          out to EventBridge, which starts the Step Function.
#   audience-membership -- the parquet writer lands Hive-partitioned membership
#                          snapshots here.
#
# EventBridge (not a direct S3->Lambda notification) is used to start the state
# machine because S3 cannot start a Step Function directly. Enabling
# eventbridge = true on the bucket emits "Object Created" events that an
# EventBridge rule (see step_function.tf) routes to StartExecution.

resource "aws_s3_bucket" "audience_uploads" {
  bucket = "${var.env}-audience-uploads"
  tags   = var.tags
}

resource "aws_s3_bucket_notification" "audience_uploads" {
  bucket      = aws_s3_bucket.audience_uploads.id
  eventbridge = true
}

resource "aws_s3_bucket_public_access_block" "audience_uploads" {
  bucket                  = aws_s3_bucket.audience_uploads.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket" "audience_membership" {
  bucket = "${var.env}-audience-membership"
  tags   = var.tags
}

resource "aws_s3_bucket_public_access_block" "audience_membership" {
  bucket                  = aws_s3_bucket.audience_membership.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
