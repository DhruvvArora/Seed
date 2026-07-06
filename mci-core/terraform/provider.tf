# Configures the AWS provider. The region comes from var.aws_region
# (default us-east-2), so all resources deploy to one consistent region.

provider "aws" {
  region                   = var.aws_region
  skip_metadata_api_check  = true
}