variable "env" {
  description = "Environment prefix applied to every resource name (e.g. dev, staging, prod)."
  type        = string
}

variable "aws_region" {
  description = "AWS region to deploy into. Must match the region used by mci-core."
  type        = string
  default     = "us-east-2"
}

variable "lambda_zip_path" {
  description = <<-EOT
    Path to the packaged Lambda deployment zip. Must include BOTH the
    event-ingress package and the mci-core path dependency, since Lambda
    has no pip install step at runtime.

    Build with (from event-ingress/):
      pip install --target build/ ../mci-core .
      (cd build && zip -r ../build/event-ingress.zip .)
  EOT
  type    = string
  default = "build/event-ingress.zip"
}

variable "mci_get_internal_function_name" {
  description = "Function name of mci-core's get-internal-customer-ids Lambda. From mci-core Terraform output."
  type        = string
}

variable "mci_get_internal_function_arn" {
  description = "ARN of mci-core's get-internal-customer-ids Lambda (unqualified). Used to scope the lambda:InvokeFunction permission narrowly."
  type        = string
}

variable "mci_function_alias" {
  description = <<-EOT
    Alias of the MCI function to invoke (LIVE or CANARY).

    The LIVE alias does not yet exist as a real AWS resource in mci-core's
    Terraform (deferred, same as Project 2). For the live run, override this
    to "$LATEST" exactly as we did in Project 2.
  EOT
  type    = string
  default = "LIVE"
}

variable "cache_ttl_seconds" {
  description = "How long joust and mparticle-enqueue cache DynamoDB API key lookups in memory. 900 = 15 minutes (spec default)."
  type        = number
  default     = 900
}

variable "tags" {
  description = "Tags applied to every resource."
  type        = map(string)
  default = {
    project = "seed"
    service = "event-ingress"
  }
}
