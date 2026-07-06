# Three Kinesis streams powering the event ingest pipelines.
#
# transactions-internal (4 shards): purchase events from both the REST API and
# mParticle. Spec shard calculation: expected peak ~4 MB/s, 1 MB/s per shard.
#
# action-internal (1 shard): non-purchase customer actions.
#
# mparticle-enqueue (1 shard): raw mParticle Firehose batches. mparticle-enqueue
# writes here immediately (fast-ack path); mparticle-processing reads and converts
# asynchronously. KMS-encrypted because the raw Firehose payload is PII-containing.

resource "aws_kinesis_stream" "transactions_internal" {
  name             = "${var.env}-transactions-internal"
  shard_count      = 4
  retention_period = 168 # 7 days

  encryption_type = "KMS"
  kms_key_id      = "alias/aws/kinesis"

  tags = var.tags
}

resource "aws_kinesis_stream" "action_internal" {
  name             = "${var.env}-action-internal"
  shard_count      = 1
  retention_period = 168 # 7 days

  encryption_type = "KMS"
  kms_key_id      = "alias/aws/kinesis"

  tags = var.tags
}

resource "aws_kinesis_stream" "mparticle_enqueue" {
  name             = "${var.env}-mparticle-enqueue"
  shard_count      = 1
  retention_period = 168 # 7 days

  encryption_type = "KMS"
  kms_key_id      = "alias/aws/kinesis"

  tags = var.tags
}
