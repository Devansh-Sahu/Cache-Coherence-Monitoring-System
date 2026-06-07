# ─── DynamoDB: CacheKeyRegistry ───────────────────────────────────────────────
resource "aws_dynamodb_table" "cache_key_registry" {
  name         = var.registry_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "key_name"

  attribute {
    name = "key_name"
    type = "S"
  }

  attribute {
    name = "owning_service"
    type = "S"
  }

  global_secondary_index {
    name            = "owning_service-index"
    hash_key        = "owning_service"
    projection_type = "ALL"
  }

  tags = { Project = "csm", Environment = var.environment }
}

# ─── DynamoDB: StalenessHistory ───────────────────────────────────────────────
resource "aws_dynamodb_table" "staleness_history" {
  name         = var.history_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "key_name"
  range_key    = "timestamp"

  attribute {
    name = "key_name"
    type = "S"
  }

  attribute {
    name = "timestamp"
    type = "S"
  }

  attribute {
    name = "owning_service"
    type = "S"
  }

  global_secondary_index {
    name            = "owning_service-timestamp-index"
    hash_key        = "owning_service"
    range_key       = "timestamp"
    projection_type = "ALL"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  tags = { Project = "csm", Environment = var.environment }
}

# ─── S3: Runbooks bucket ───────────────────────────────────────────────────────
resource "aws_s3_bucket" "runbooks" {
  bucket        = var.s3_runbooks_bucket
  force_destroy = true
  tags          = { Project = "csm", Environment = var.environment }
}

# ─── SQS: Alert queue ─────────────────────────────────────────────────────────
resource "aws_sqs_queue" "alerts_dlq" {
  name = "${var.sqs_queue_name}-dlq"
}

resource "aws_sqs_queue" "alerts" {
  name                      = var.sqs_queue_name
  visibility_timeout_seconds = 120
  message_retention_seconds  = 86400

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.alerts_dlq.arn
    maxReceiveCount     = 3
  })

  tags = { Project = "csm", Environment = var.environment }
}

# ─── Secrets Manager: Anthropic API key ───────────────────────────────────────
resource "aws_secretsmanager_secret" "groq_key" {
  name = "csm/groq-api-key"
  tags = { Project = "csm" }
}

resource "aws_secretsmanager_secret_version" "groq_key_version" {
  secret_id     = aws_secretsmanager_secret.groq_key.id
  secret_string = "REPLACE_WITH_GROQ_API_KEY"
}

# ─── IAM: Lambda execution role ───────────────────────────────────────────────
resource "aws_iam_role" "lambda_role" {
  name = "csm-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "lambda_policy" {
  name = "csm-lambda-policy"
  role = aws_iam_role.lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "dynamodb:Scan", "dynamodb:Query", "dynamodb:GetItem",
          "dynamodb:PutItem", "dynamodb:UpdateItem"
        ]
        Resource = [
          aws_dynamodb_table.cache_key_registry.arn,
          aws_dynamodb_table.staleness_history.arn,
          "${aws_dynamodb_table.staleness_history.arn}/index/*",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = aws_secretsmanager_secret.groq_key.arn
      },
      {
        Effect   = "Allow"
        Action   = ["cloudwatch:PutMetricData"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Resource = aws_sqs_queue.alerts.arn
      }
    ]
  })
}

# ─── Lambda: Staleness Alerter ────────────────────────────────────────────────
resource "aws_lambda_function" "staleness_alerter" {
  function_name = var.lambda_function_name
  filename      = "../staleness_alerter.zip"
  role          = aws_iam_role.lambda_role.arn
  handler       = "staleness_alerter.handler"
  runtime       = "python3.11"
  timeout       = 60
  memory_size   = 256

  environment {
    variables = {
      DYNAMODB_REGISTRY_TABLE = var.registry_table_name
      DYNAMODB_HISTORY_TABLE  = var.history_table_name
      SLA_BREACH_MULTIPLIER   = "1.5"
      GROQ_MODEL              = "llama-3.3-70b-versatile"
      GROQ_MAX_TOKENS         = "300"
      SLACK_WEBHOOK_URL       = "https://hooks.slack.com/services/YOUR/WEBHOOK/URL"
      USE_LOCALSTACK          = tostring(var.use_localstack)
      AWS_ENDPOINT_URL        = var.use_localstack ? var.aws_endpoint_url : ""
      AWS_DEFAULT_REGION      = var.aws_region
    }
  }

  tags = { Project = "csm", Environment = var.environment }
}

# ─── CloudWatch Events: Run every 60 seconds ──────────────────────────────────
resource "aws_cloudwatch_event_rule" "every_60s" {
  name                = "csm-staleness-check"
  description         = "Trigger staleness alerter every 60 seconds"
  schedule_expression = "rate(1 minute)"
}

resource "aws_cloudwatch_event_target" "lambda_target" {
  rule      = aws_cloudwatch_event_rule.every_60s.name
  target_id = "staleness-alerter"
  arn       = aws_lambda_function.staleness_alerter.arn
}

resource "aws_lambda_permission" "allow_cloudwatch" {
  statement_id  = "AllowExecutionFromCloudWatch"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.staleness_alerter.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.every_60s.arn
}

# ─── CloudWatch Dashboard ─────────────────────────────────────────────────────
resource "aws_cloudwatch_dashboard" "csm" {
  dashboard_name = "CacheStalenessMonitor"
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric"
        properties = {
          title  = "Cache Staleness (ms)"
          metrics = [["CacheStalenessMonitor", "StalenessMs"]]
          period = 60
          stat   = "Maximum"
          view   = "timeSeries"
        }
      },
      {
        type = "metric"
        properties = {
          title  = "SLA Breach Percentage"
          metrics = [["CacheStalenessMonitor", "SLABreachPercent"]]
          period = 60
          stat   = "Maximum"
          view   = "timeSeries"
        }
      },
      {
        type = "metric"
        properties = {
          title  = "Lambda Errors"
          metrics = [["AWS/Lambda", "Errors", "FunctionName", var.lambda_function_name]]
          period = 60
          stat   = "Sum"
          view   = "timeSeries"
        }
      }
    ]
  })
}
