# Pin Terraform and the AWS provider to the same versions as the other four
# projects so provider drift cannot silently change behavior across the
# monorepo.

terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}
