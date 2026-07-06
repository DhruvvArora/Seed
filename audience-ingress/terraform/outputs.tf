output "uploads_bucket" {
  description = "Bucket clients upload gzipped audience CSVs to."
  value       = aws_s3_bucket.audience_uploads.bucket
}

output "membership_bucket" {
  description = "Bucket where membership parquet snapshots are written."
  value       = aws_s3_bucket.audience_membership.bucket
}

output "audience_events_stream" {
  description = "Kinesis stream the reducer consumes (streaming path)."
  value       = aws_kinesis_stream.audience_events.name
}

output "audience_events_stream_arn" {
  value = aws_kinesis_stream.audience_events.arn
}

output "metadata_table" {
  value = aws_dynamodb_table.audience_metadata.name
}

output "membership_table" {
  value = aws_dynamodb_table.audience_membership.name
}

output "state_machine_arn" {
  value = aws_sfn_state_machine.audience_ingest.arn
}

output "alert_topic_arn" {
  value = aws_sns_topic.audience_ingest_alerts.arn
}
