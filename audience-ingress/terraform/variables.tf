# Inputs to the audience-ingress module.

variable "env" {
  description = "Environment name, used as a prefix on resource names (e.g. dev, staging, prod)."
  type        = string
}

variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-2"
}

variable "tags" {
  description = "Common tags applied to all resources."
  type        = map(string)
  default     = {}
}

variable "lambda_zip_path" {
  description = <<-EOT
    Path to the packaged Lambda deployment zip. As with mci-backfill, this zip
    must contain BOTH the audience-ingress package and the mci-core package it
    depends on (the monorepo path dependency), since Lambda has no pip step at
    runtime. Build it WITHOUT editable installs (no -e) so no stale egg-links
    ship. pyarrow/pandas are NOT in this zip; they come from the pandas layer
    below. Build with:
      pip install --target build/ ../mci-core .
      (cd build && zip -r ../build/audience-ingress.zip .)
  EOT
  type        = string
  default     = "build/audience-ingress.zip"
}

variable "pandas_layer_arn" {
  description = <<-EOT
    ARN of the AWS-managed AWSSDKPandas layer (bundles pyarrow + pandas) for the
    parquet writer. REGION-SPECIFIC: this value is for us-east-2 / Python 3.12 /
    x86_64. Do not copy an ARN from another region. The ":29" version suffix
    increments when AWS republishes; older published versions remain available,
    so pinning is safe. If a deploy fails with "layer version does not exist",
    bump the suffix (check the current value in the AWS SDK for pandas docs).
    Only the audience-parquet-writer Lambda attaches this layer.
  EOT
  type        = string
  default     = "arn:aws:lambda:us-east-2:336392948345:layer:AWSSDKPandas-Python312:29"
}

variable "mci_get_internal_function_arn" {
  description = "ARN of mci-core's get-internal-customer-ids Lambda, invoked by audience-ingest and audience-reducer. From the mci-core Terraform output."
  type        = string
}

variable "mci_get_internal_function_name" {
  description = "Function name of mci-core's get-internal-customer-ids Lambda (passed to handlers via env, never a hard-coded ARN)."
  type        = string
}

variable "mci_function_alias" {
  description = "Alias of the MCI function to invoke (LIVE or CANARY). Deferred in dev the same way as P2/P3: override to $LATEST until the LIVE alias is a real resource."
  type        = string
  default     = "LIVE"
}

variable "mci_probe_existing" {
  description = "When true, audience-ingest does a read-only MCI probe before creating so new_mappings_created is truthful. Doubles MCI invokes; leave false in dev."
  type        = bool
  default     = false
}

variable "reducer_parallelization_factor" {
  description = "DynamoDB write concurrency for audience-reducer (spec: 50x)."
  type        = number
  default     = 50
}
