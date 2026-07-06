# Region comes from var.aws_region (default us-east-2), matching mci-core and
# mci-backfill so all four projects deploy to the same region.

provider "aws" {
  region = var.aws_region

  skip_metadata_api_check = true
}
