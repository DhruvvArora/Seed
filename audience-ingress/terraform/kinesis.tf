# audience-events: the streaming path's internal stream. P3's mparticle-processing
# (once wired -- see that project's follow-up) puts audience_membership_change
# records here; audience-reducer consumes them. 1 shard, 7-day retention, KMS
# encrypted with the AWS-managed Kinesis key.

resource "aws_kinesis_stream" "audience_events" {
  name             = "${var.env}-audience-events"
  shard_count      = 1
  retention_period = 168 # hours = 7 days

  encryption_type = "KMS"
  kms_key_id      = "alias/aws/kinesis"

  tags = var.tags
}
