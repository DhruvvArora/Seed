# Inputs to this module.

variable "env" {
  description = "Environment name, used as a prefix on resource names (e.g. dev, staging, prod)."
  type        = string
}

variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-2"
}

variable "lambda_zip_path" {
  description = <<-EOT
    Path to the packaged Lambda deployment zip. Unlike mci-core's zip, this one
    must contain BOTH the mci-backfill package and the mci-core package it
    depends on (the monorepo path dependency), since Lambda has no pip install
    step at runtime, only whatever is physically in the zip. Build it with:
      pip install --target build/ -e ../mci-core -e .
      (cd build && zip -r ../build/mci-backfill.zip .)
  EOT
  type        = string
  default     = "build/mci-backfill.zip"
}

variable "mci_get_internal_function_name" {
  description = "Function name of mci-core's get-internal-customer-ids Lambda, which backfill-mci invokes through the MCI invoker client. Comes from the mci-core Terraform module's output."
  type        = string
}

variable "mci_function_alias" {
  description = "Alias of the MCI function to invoke (LIVE or CANARY)."
  type        = string
  default     = "LIVE"
}

variable "excluded_tenant_ids" {
  description = "Comma-separated well-known test/internal tenant ids excluded by list-tenants. Kept in config, not code, so adding a test tenant never needs a code change."
  type        = string
  default     = ""
}

variable "athena_source_database" {
  description = "Glue/Athena database containing the 4 pre-existing historical source tables (action_events, audience_membership, ingested_attributes, transactions). This module does not create that database or those tables; it only reads them."
  type        = string
}

variable "temp_bucket_retention_days" {
  description = "How long the backfill temp bucket keeps objects before S3 auto-expires them. This is a one-time migration, so temp data has no reason to linger past the run plus a safety margin."
  type        = number
  default     = 7
}

variable "tags" {
  description = "Tags applied to every resource for cost tracking and ownership."
  type        = map(string)
  default = {
    project = "seed"
    service = "mci-backfill"
  }
}

variable "mci_get_internal_function_arn" {
  description = "ARN of mci-core's get-internal-customer-ids Lambda (unqualified). Comes from the mci-core Terraform module's output; used to scope the lambda:InvokeFunction permission narrowly rather than granting invoke on all functions."
  type        = string
}

variable "source_data_s3_bucket_arns" {
  description = <<-EOT
    ARNs of the S3 bucket(s) backing the 4 pre-existing Glue tables
    (action_events, audience_membership, ingested_attributes, transactions)
    that CollectCustomerIDs reads from. This module does not own that data, so
    it only requests read access; fill this in with the real data lake
    bucket ARN(s) before applying, or CollectCustomerIDs will fail on S3 access
    denied even though the Glue/Athena permissions are otherwise correct.
  EOT
  type        = list(string)
  default     = []
}
