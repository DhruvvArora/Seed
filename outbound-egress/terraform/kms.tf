# Customer-managed key for encrypting connector destination configs and
# integration credentials at the application level (aqueduct-writer encrypts
# on write, aqueduct-distributor decrypts on read, aqueduct-reader never
# decrypts). This is separate from the AWS-managed alias/aws/kinesis key that
# encrypts the plague stream itself (kinesis.tf); that one is transport-level
# stream encryption, this one is the actual destination config's own
# encryption at rest inside the connectors table, which is what the spec's
# "connector destination config is encrypted at rest using KMS" acceptance
# criterion is about. Key rotation is enabled (annual, AWS-managed rotation).

resource "aws_kms_key" "connector_destination" {
  description             = "Encrypts connector destination configs and integration credentials for outbound-egress (Project 5)."
  deletion_window_in_days = 30
  enable_key_rotation     = true

  tags = var.tags
}

resource "aws_kms_alias" "connector_destination" {
  name          = "alias/${var.env}-outbound-egress-connector-destination"
  target_key_id = aws_kms_key.connector_destination.key_id
}
