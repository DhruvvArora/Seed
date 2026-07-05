# Pin Terraform and the AWS provider to known-good major versions, matching
# mci-core, so a future provider release cannot silently change behavior here.

terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}
