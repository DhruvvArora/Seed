# The master-customer-index table: the single source of truth mapping
# {tenant}#{external_id} -> internal UUID.

resource "aws_dynamodb_table" "mci" {
  name         = "${var.env}-master-customer-index"
  billing_mode = "PAY_PER_REQUEST" # no capacity planning; pay per request

  hash_key  = "partition_key"
  range_key = "sort_key"

  # Only attributes used as keys (table or index) must be declared here.
  # Everything else (internal_customer_id value, events, etc.) is schemaless.
  attribute {
    name = "partition_key"
    type = "S"
  }
  attribute {
    name = "sort_key"
    type = "S"
  }
  attribute {
    name = "internal_customer_id"
    type = "S"
  }
  attribute {
    name = "tenant_id"
    type = "S"
  }

  # GSI enabling reverse lookup (internal UUID -> external id) used by
  # get-external-customer-ids. Without it you cannot query by UUID.
  global_secondary_index {
    name            = "internal-customer-id-index"
    hash_key        = "internal_customer_id"
    range_key       = "tenant_id"
    projection_type = "ALL" # copy all attributes into the index
  }

  # Lets you restore the table to any second in the last 35 days.
  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true # AWS-managed key
  }

  tags = var.tags
}
