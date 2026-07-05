output "state_machine_arn" {
  description = "ARN of the backfill Step Function. Start an execution against this with the run's input (temp_table, athena_database, athena_output_location, ctas_query, excluded_tenant_ids)."
  value       = aws_sfn_state_machine.backfill.arn
}

output "tenant_registry_table_name" {
  value = aws_dynamodb_table.tenant_registry.name
}

output "backfill_temp_bucket" {
  value = aws_s3_bucket.backfill_temp.id
}

output "athena_workgroup" {
  value = aws_athena_workgroup.backfill.name
}
