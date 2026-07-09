# Two tables:
#   connectors      -- one row per connector. partition_key = tenant_id,
#                      sort_key = connector name. destination is stored
#                      encrypted (see kms.tf); connection_status and enabled
#                      are updated in place over a connector's lifetime.
#   transformations -- one row per named JQ script. partition_key = tenant_id,
#                      sort_key = transformation name.
#
# Both PAY_PER_REQUEST (no capacity planning), PITR, and SSE, matching the
# other four projects' tables. Neither needs a GSI: connectors is always
# queried by (tenant_id) for lists or (tenant_id, name) for point lookups,
# never by a non-key attribute.

resource "aws_dynamodb_table" "connectors" {
  name         = "${var.env}-connectors"
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

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = var.tags
}

resource "aws_dynamodb_table" "transformations" {
  name         = "${var.env}-transformations"
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

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = var.tags
}
