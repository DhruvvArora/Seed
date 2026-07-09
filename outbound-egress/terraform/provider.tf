# Region comes from var.aws_region (default us-east-2), matching mci-core,
# mci-backfill, event-ingress, and audience-ingress so all five projects
# deploy to the same region.

provider "aws" {
  region = var.aws_region

  skip_metadata_api_check = true
}
