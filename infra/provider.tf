terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  required_version = ">= 1.6.0"
}

# ── LocalStack (dev) provider ──────────────────────────────────────────────────
provider "aws" {
  region                      = var.aws_region
  access_key                  = var.aws_access_key
  secret_key                  = var.aws_secret_key
  skip_credentials_validation = var.use_localstack
  skip_metadata_api_check     = var.use_localstack
  skip_requesting_account_id  = var.use_localstack

  dynamic "endpoints" {
    for_each = var.use_localstack ? [1] : []
    content {
      dynamodb       = var.aws_endpoint_url
      s3             = var.aws_endpoint_url
      sqs            = var.aws_endpoint_url
      lambda         = var.aws_endpoint_url
      iam            = var.aws_endpoint_url
      cloudwatch     = var.aws_endpoint_url
      secretsmanager = var.aws_endpoint_url
    }
  }
}
