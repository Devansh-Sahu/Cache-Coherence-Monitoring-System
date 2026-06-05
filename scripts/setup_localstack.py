"""
LocalStack bootstrap script — creates all required AWS resources locally.
Run: python -m scripts.setup_localstack
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import boto3
import structlog
from botocore.exceptions import ClientError

from api.config import get_settings

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ]
)
logger = structlog.get_logger(__name__)


def _client(service: str) -> object:
    settings = get_settings()
    return boto3.client(
        service,
        region_name=settings.aws_default_region,
        endpoint_url=settings.aws_endpoint_url,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
    )


def setup_s3() -> None:
    settings = get_settings()
    s3 = _client("s3")
    try:
        s3.create_bucket(Bucket=settings.s3_runbooks_bucket)  # type: ignore[union-attr]
        logger.info("Created S3 bucket", bucket=settings.s3_runbooks_bucket)
    except ClientError as e:
        if "BucketAlreadyExists" in str(e) or "BucketAlreadyOwnedByYou" in str(e):
            logger.info("S3 bucket already exists", bucket=settings.s3_runbooks_bucket)
        else:
            raise


def setup_sqs() -> None:
    sqs = _client("sqs")
    # Main alert queue
    try:
        resp = sqs.create_queue(QueueName="csm-alerts")  # type: ignore[union-attr]
        logger.info("Created SQS queue", url=resp["QueueUrl"])
    except ClientError:
        logger.info("SQS queue already exists")

    # DLQ
    try:
        resp = sqs.create_queue(QueueName="csm-alerts-dlq")  # type: ignore[union-attr]
        logger.info("Created SQS DLQ", url=resp["QueueUrl"])
    except ClientError:
        logger.info("SQS DLQ already exists")


def setup_secretsmanager(anthropic_key: str = "") -> None:
    sm = _client("secretsmanager")
    try:
        sm.create_secret(  # type: ignore[union-attr]
            Name="csm/anthropic-api-key",
            SecretString=anthropic_key or "placeholder-replace-me",
        )
        logger.info("Created Secrets Manager secret")
    except ClientError as e:
        if "ResourceExistsException" in str(e):
            if anthropic_key:
                sm.update_secret(  # type: ignore[union-attr]
                    SecretId="csm/anthropic-api-key", SecretString=anthropic_key
                )
                logger.info("Updated Secrets Manager secret with API key")
            else:
                logger.info("Secrets Manager secret already exists")
        else:
            raise


def setup_cloudwatch_dashboard() -> None:
    cw = _client("cloudwatch")
    dashboard_body = {
        "widgets": [
            {
                "type": "metric",
                "properties": {
                    "title": "Cache Key Staleness (ms)",
                    "metrics": [
                        ["CacheStalenessMonitor", "StalenessMs"]
                    ],
                    "period": 60,
                    "stat": "Maximum",
                },
            },
            {
                "type": "metric",
                "properties": {
                    "title": "SLA Breach Percentage",
                    "metrics": [
                        ["CacheStalenessMonitor", "SLABreachPercent"]
                    ],
                    "period": 60,
                    "stat": "Maximum",
                },
            },
        ]
    }
    try:
        cw.put_dashboard(  # type: ignore[union-attr]
            DashboardName="CacheStalenessMonitor",
            DashboardBody=json.dumps(dashboard_body),
        )
        logger.info("Created CloudWatch dashboard")
    except Exception as exc:
        logger.warning("CloudWatch dashboard creation failed", error=str(exc))


def main() -> None:
    logger.info("Setting up LocalStack resources...")
    setup_s3()
    setup_sqs()
    setup_secretsmanager()
    setup_cloudwatch_dashboard()
    logger.info("LocalStack setup complete")


if __name__ == "__main__":
    main()
