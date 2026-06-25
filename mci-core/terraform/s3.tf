# Two S3 buckets the handlers write to:
#   - log bucket:   new mappings created by get-internal (JSON lines)
#   - audit bucket: CCPA deletion records from forget-external (compliance)
# Kept separate because the audit bucket has stricter retention/compliance needs.

resource "aws_s3_bucket" "logs" {
  bucket = "${var.env}-mci-core-logs"
  tags   = var.tags
}

resource "aws_s3_bucket" "audit" {
  bucket = "${var.env}-mci-core-audit"
  tags   = var.tags
}

# Block all public access on both buckets. Customer-identity data must never
# be publicly reachable.
resource "aws_s3_bucket_public_access_block" "logs" {
  bucket                  = aws_s3_bucket.logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_public_access_block" "audit" {
  bucket                  = aws_s3_bucket.audit.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Versioning on the audit bucket so deletion records cannot be silently
# overwritten; that protects the integrity of the compliance trail.
resource "aws_s3_bucket_versioning" "audit" {
  bucket = aws_s3_bucket.audit.id
  versioning_configuration {
    status = "Enabled"
  }
}
