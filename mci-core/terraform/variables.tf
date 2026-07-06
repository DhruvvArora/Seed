# Inputs to this module. A "variable" is a parameter you pass in, so the same
# code can build a dev, staging, or prod copy just by changing these values.

variable "env" {
  description = "Environment name, used as a prefix on resource names (e.g. dev, staging, prod)."
  type        = string
}

variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-2"
}

variable "dynamo_parallelization_factor" {
  description = "How many parallel DynamoDB workers each Lambda uses."
  type        = number
  default     = 20
}

variable "lambda_zip_path" {
  description = "Path to the packaged Lambda deployment zip (built by CI or a local script)."
  type        = string
  default     = "../build/mci-core.zip"
}

variable "tags" {
  description = "Tags applied to every resource for cost tracking and ownership."
  type        = map(string)
  default = {
    project = "seed"
    service = "mci-core"
  }
}
