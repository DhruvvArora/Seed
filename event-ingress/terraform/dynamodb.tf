# The apikey-metadata table stores the mapping from API key -> tenant_id.
# Both joust (REST API authorizer) and mparticle-enqueue (Firehose auth) read
# from this table. The Lambda-side in-memory cache reduces DynamoDB load under
# sustained traffic; the table itself is the single source of truth.
#
# Schema:
#   partition_key (String, hash) -- the raw API key value
#   tenant_id     (String)       -- associated tenant
#   enabled       (Boolean)      -- whether the key is active
#   created_at    (String)       -- ISO timestamp
#   name          (String)       -- human-readable label

resource "aws_dynamodb_table" "apikey_metadata" {
  name         = "${var.env}-apikey-metadata"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "partition_key"

  attribute {
    name = "partition_key"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = var.tags
}
