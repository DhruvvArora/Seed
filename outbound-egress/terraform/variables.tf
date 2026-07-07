# Inputs to the outbound-egress module.

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
    Path to the packaged Lambda deployment zip. Unlike mci-backfill,
    event-ingress, and audience-ingress, this zip does NOT need the mci-core
    path dependency bundled in, since this package never calls MCI (the
    plague stream already carries resolved internal customer IDs). It DOES
    need pyjwt, cryptography, and requests bundled, since none of those are
    part of the default Lambda runtime and none come from a managed layer.
    It must NOT include the jq package; that comes from jq_layer_arn below.
    Build with:
      pip install --target build/ .
      (cd build && zip -r ../build/outbound-egress.zip . -x 'jq*')
  EOT
  type        = string
  default     = "../build/outbound-egress.zip"
}

variable "jq_layer_arn" {
  description = <<-EOT
    ARN of the self-built Lambda layer bundling the jq package (real libjq C
    bindings). Unlike the AWS-managed AWSSDKPandas layer audience-ingress
    uses, AWS does not publish a jq layer, so this one is built and
    published by hand: `pip install jq --target python/` into a layer.zip
    (the PyPI wheel is a self-contained manylinux build with libjq and
    oniguruma statically linked, confirmed locally via `ldd` on the compiled
    extension, so no from-source Docker build is required), then
    `aws lambda publish-layer-version`. REGION- AND RUNTIME-SPECIFIC: this
    ARN is only valid for the account/region it was published in and for
    cp312/Amazon Linux 2023. It must be rebuilt and republished whenever the
    jq package version changes, or whenever the Lambda runtime moves off
    cp312/Amazon Linux 2023. Only aqueduct-distributor attaches this layer;
    aqueduct-writer and aqueduct-reader never apply a JQ transformation.
  EOT
  type        = string
}
