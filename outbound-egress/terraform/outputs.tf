output "plague_stream_name" {
  description = "Kinesis stream aqueduct-distributor consumes."
  value       = aws_kinesis_stream.plague.name
}

output "plague_stream_arn" {
  value = aws_kinesis_stream.plague.arn
}

output "connectors_table" {
  value = aws_dynamodb_table.connectors.name
}

output "transformations_table" {
  value = aws_dynamodb_table.transformations.name
}

output "connector_destination_kms_key_id" {
  value = aws_kms_key.connector_destination.key_id
}

output "connector_destination_kms_key_arn" {
  value = aws_kms_key.connector_destination.arn
}

output "aqueduct_distributor_function_name" {
  value = aws_lambda_function.aqueduct_distributor.function_name
}

output "aqueduct_writer_function_name" {
  value = aws_lambda_function.aqueduct_writer.function_name
}

output "aqueduct_reader_function_name" {
  value = aws_lambda_function.aqueduct_reader.function_name
}

output "aqueduct_writer_function_arn" {
  description = "Invoke target for the GraphQL API layer."
  value       = aws_lambda_function.aqueduct_writer.arn
}

output "aqueduct_reader_function_arn" {
  description = "Invoke target for other internal services that need connector configs."
  value       = aws_lambda_function.aqueduct_reader.arn
}
