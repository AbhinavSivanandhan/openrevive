provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
      CostControl = "openrevive"
    }
  }
}

data "aws_caller_identity" "current" {}

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  name = "${var.project_name}-${var.environment}"

  availability_zones = slice(
    data.aws_availability_zones.available.names,
    0,
    2,
  )

  public_subnets = {
    for index, zone in local.availability_zones :
    zone => cidrsubnet(var.vpc_cidr, 4, index)
  }

  database_subnets = {
    for index, zone in local.availability_zones :
    zone => cidrsubnet(var.vpc_cidr, 4, index + 8)
  }

  artifact_bucket_name = lower("${var.project_name}-${var.environment}-${data.aws_caller_identity.current.account_id}-${var.aws_region}-artifacts")
}
