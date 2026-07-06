# Two tables:
#   bullseye-audience-metadata-v2 -- one row per (tenant, audience); audience
#                                    level state/size/run_date/parquet_path.
#   audience-membership           -- one row per member; state ELIGIBLE /
#                                    INELIGIBLE, optional TTL.
#
# Both PAY_PER_REQUEST (no capacity planning) with the AudienceIDIndex GSI for
# cross-tenant/audience queries. Membership enables TTL on the `ttl` attribute;
# rows without a ttl value simply never expire, which is the current default
# since streaming events carry no audience end date.

resource "aws_dynamodb_table" "audience_metadata" {
  name         = "${var.env}-bullseye-audience-metadata-v2"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "partition_key"
  range_key    = "sort_key"

  attribute {
    name = "partition_key"
    type = "S"
  }
  attribute {
    name = "sort_key"
    type = "S"
  }
  attribute {
    name = "audience_id"
    type = "S"
  }

  global_secondary_index {
    name            = "AudienceIDIndex"
    hash_key        = "audience_id"
    range_key       = "sort_key"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = var.tags
}

resource "aws_dynamodb_table" "audience_membership" {
  name         = "${var.env}-audience-membership"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "partition_key"
  range_key    = "sort_key"

  attribute {
    name = "partition_key"
    type = "S"
  }
  attribute {
    name = "sort_key"
    type = "S"
  }
  attribute {
    name = "audience_id"
    type = "S"
  }

  global_secondary_index {
    name            = "AudienceIDIndex"
    hash_key        = "audience_id"
    range_key       = "sort_key"
    projection_type = "ALL"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = var.tags
}
