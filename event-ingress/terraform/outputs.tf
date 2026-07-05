# Outputs for use by other modules or humans running live tests.

output "api_endpoint" {
  description = "Base URL of the deployed API Gateway stage."
  value       = aws_api_gateway_stage.event_api.invoke_url
}

output "post_event_url" {
  description = "Full URL for POST /event (the direct REST API path)."
  value       = "${aws_api_gateway_stage.event_api.invoke_url}/event"
}

output "post_mparticle_url" {
  description = "Full URL for POST /mparticle (the mParticle Firehose webhook path)."
  value       = "${aws_api_gateway_stage.event_api.invoke_url}/mparticle"
}

output "transactions_stream_name" {
  description = "Name of the transactions-internal Kinesis stream."
  value       = aws_kinesis_stream.transactions_internal.name
}

output "action_stream_name" {
  description = "Name of the action-internal Kinesis stream."
  value       = aws_kinesis_stream.action_internal.name
}

output "mparticle_enqueue_stream_name" {
  description = "Name of the mparticle-enqueue Kinesis stream."
  value       = aws_kinesis_stream.mparticle_enqueue.name
}

output "apikey_table_name" {
  description = "Name of the apikey-metadata DynamoDB table."
  value       = aws_dynamodb_table.apikey_metadata.name
}
