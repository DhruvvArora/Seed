# Outputs expose values other modules (or humans) need after apply. Projects 2,
# 3, and 4 call MCI by function name, so they read these.

output "table_name" {
  description = "Name of the MCI DynamoDB table."
  value       = aws_dynamodb_table.mci.name
}

output "get_internal_function_name" {
  description = "Function name callers invoke to resolve external -> internal IDs."
  value       = aws_lambda_function.get_internal.function_name
}

output "get_external_function_name" {
  value = aws_lambda_function.get_external.function_name
}

output "forget_external_function_name" {
  value = aws_lambda_function.forget_external.function_name
}

output "log_bucket" {
  value = aws_s3_bucket.logs.id
}

output "audit_bucket" {
  value = aws_s3_bucket.audit.id
}
