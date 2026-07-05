# Configures the AWS provider. The region comes from var.aws_region (default
# us-east-2), matching mci-core so both projects deploy to the same region.

provider "aws" {
  region = var.aws_region
}
