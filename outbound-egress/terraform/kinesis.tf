# plague: the internal fan-out stream. The offer state machine (upstream of
# this project) emits BatchProgress messages here on every offer state
# transition. aqueduct-distributor is the sole consumer. 48 shards in prod,
# 7-day retention, KMS-encrypted with the AWS-managed Kinesis key (this is a
# separate key from the customer-managed one in kms.tf, which encrypts
# connector destination configs at the application level, not the stream
# itself).

resource "aws_kinesis_stream" "plague" {
  name             = "${var.env}-plague"
  shard_count      = 48
  retention_period = 168 # hours = 7 days

  encryption_type = "KMS"
  kms_key_id      = "alias/aws/kinesis"

  tags = var.tags
}
