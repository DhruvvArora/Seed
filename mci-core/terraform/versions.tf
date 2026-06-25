# Pin Terraform and the AWS provider to known-good major versions. Pinning
# prevents a future provider release from silently changing behavior under you.

terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}
