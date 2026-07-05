# Tenant registry: source of truth for which tenants list-tenants fans out
# over. Schema matches src/backfill/store/tenant_registry.py: partition_key
# tenant_id, an optional (unindexed) "active" attribute. Backfill owns this
# table; mci-core has no notion of it.

resource "aws_dynamodb_table" "tenant_registry" {
  name         = "${var.env}-tenant-registry"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "tenant_id"

  attribute {
    name = "tenant_id"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = var.tags
}
