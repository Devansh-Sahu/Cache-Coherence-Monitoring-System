variable "aws_region" {
  default = "us-east-1"
}

variable "aws_access_key" {
  default = "test"
}

variable "aws_secret_key" {
  default = "test"
}

variable "aws_endpoint_url" {
  default = "http://localhost:4566"
}

variable "use_localstack" {
  default = true
  type    = bool
}

variable "registry_table_name" {
  default = "CacheKeyRegistry"
}

variable "history_table_name" {
  default = "StalenessHistory"
}

variable "s3_runbooks_bucket" {
  default = "csm-runbooks"
}

variable "sqs_queue_name" {
  default = "csm-alerts"
}

variable "lambda_function_name" {
  default = "staleness-alerter"
}

variable "environment" {
  default = "dev"
}
