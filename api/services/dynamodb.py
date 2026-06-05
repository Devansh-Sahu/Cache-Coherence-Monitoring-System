"""
DynamoDB service — abstracted client for CacheKeyRegistry and StalenessHistory.
Works transparently with LocalStack or real AWS based on settings.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

import boto3
import structlog
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from api.config import get_settings

logger = structlog.get_logger(__name__)


def _get_dynamodb_resource() -> Any:
    """Return a DynamoDB resource, routed to LocalStack when USE_LOCALSTACK=true."""
    settings = get_settings()
    kwargs: dict[str, Any] = {
        "region_name": settings.aws_default_region,
        "aws_access_key_id": settings.aws_access_key_id,
        "aws_secret_access_key": settings.aws_secret_access_key,
    }
    if settings.use_localstack:
        kwargs["endpoint_url"] = settings.aws_endpoint_url
    return boto3.resource("dynamodb", **kwargs)


def _float_to_decimal(obj: Any) -> Any:
    """DynamoDB doesn't support float — convert to Decimal recursively."""
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _float_to_decimal(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_float_to_decimal(i) for i in obj]
    return obj


def _decimal_to_float(obj: Any) -> Any:
    """Convert Decimal back to float when reading from DynamoDB."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _decimal_to_float(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decimal_to_float(i) for i in obj]
    return obj


# ─────────────────────────────────────────────────────────────────────────────
# Table bootstrap (idempotent)
# ─────────────────────────────────────────────────────────────────────────────


def ensure_tables_exist() -> None:
    """Create DynamoDB tables if they don't exist (idempotent)."""
    settings = get_settings()
    dynamodb = _get_dynamodb_resource()
    existing = {t.name for t in dynamodb.tables.all()}

    # CacheKeyRegistry
    if settings.dynamodb_registry_table not in existing:
        dynamodb.create_table(
            TableName=settings.dynamodb_registry_table,
            KeySchema=[{"AttributeName": "key_name", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "key_name", "AttributeType": "S"},
                {"AttributeName": "owning_service", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "owning_service-index",
                    "KeySchema": [
                        {"AttributeName": "owning_service", "KeyType": "HASH"}
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
        )
        logger.info("Created DynamoDB table", table=settings.dynamodb_registry_table)

    # StalenessHistory
    if settings.dynamodb_history_table not in existing:
        table = dynamodb.create_table(
            TableName=settings.dynamodb_history_table,
            KeySchema=[
                {"AttributeName": "key_name", "KeyType": "HASH"},
                {"AttributeName": "timestamp", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "key_name", "AttributeType": "S"},
                {"AttributeName": "timestamp", "AttributeType": "S"},
                {"AttributeName": "owning_service", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "owning_service-timestamp-index",
                    "KeySchema": [
                        {"AttributeName": "owning_service", "KeyType": "HASH"},
                        {"AttributeName": "timestamp", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
        )
        # Enable TTL for auto-purge after 30 days
        table.meta.client.update_time_to_live(
            TableName=settings.dynamodb_history_table,
            TimeToLiveSpecification={"Enabled": True, "AttributeName": "expires_at"},
        )
        logger.info("Created DynamoDB table", table=settings.dynamodb_history_table)


# ─────────────────────────────────────────────────────────────────────────────
# CacheKeyRegistry CRUD
# ─────────────────────────────────────────────────────────────────────────────


class RegistryService:
    def __init__(self) -> None:
        self._db = _get_dynamodb_resource()
        self._table = self._db.Table(get_settings().dynamodb_registry_table)

    def get(self, key_name: str) -> Optional[dict[str, Any]]:
        resp = self._table.get_item(Key={"key_name": key_name})
        item = resp.get("Item")
        return _decimal_to_float(item) if item else None

    def list_all(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        resp = self._table.scan()
        items.extend(resp.get("Items", []))
        while "LastEvaluatedKey" in resp:
            resp = self._table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"])
            items.extend(resp.get("Items", []))
        return [_decimal_to_float(i) for i in items]

    def put(self, entry: dict[str, Any]) -> None:
        item = _float_to_decimal(entry)
        self._table.put_item(Item=item)

    def update(self, key_name: str, updates: dict[str, Any]) -> Optional[dict[str, Any]]:
        updates["updated_at"] = datetime.utcnow().isoformat()
        expr_parts = []
        names: dict[str, str] = {}
        values: dict[str, Any] = {}
        for i, (k, v) in enumerate(updates.items()):
            placeholder = f"#a{i}"
            val_placeholder = f":v{i}"
            names[placeholder] = k
            values[val_placeholder] = _float_to_decimal(v)
            expr_parts.append(f"{placeholder} = {val_placeholder}")
        resp = self._table.update_item(
            Key={"key_name": key_name},
            UpdateExpression="SET " + ", ".join(expr_parts),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ReturnValues="ALL_NEW",
        )
        item = resp.get("Attributes")
        return _decimal_to_float(item) if item else None

    def delete(self, key_name: str) -> None:
        self._table.delete_item(Key={"key_name": key_name})


# ─────────────────────────────────────────────────────────────────────────────
# StalenessHistory CRUD
# ─────────────────────────────────────────────────────────────────────────────


class StalenessHistoryService:
    TTL_DAYS = 30

    def __init__(self) -> None:
        self._db = _get_dynamodb_resource()
        self._table = self._db.Table(get_settings().dynamodb_history_table)

    def put_event(self, event: dict[str, Any]) -> None:
        item = dict(event)
        # DynamoDB TTL — auto-expire after 30 days
        item["expires_at"] = int(time.time()) + self.TTL_DAYS * 86400
        item = _float_to_decimal(item)
        self._table.put_item(Item=item)

    def get_events(
        self, key_name: str, hours: int = 24
    ) -> list[dict[str, Any]]:
        since = (
            datetime.now(timezone.utc) - timedelta(hours=hours)
        ).isoformat()
        resp = self._table.query(
            KeyConditionExpression=Key("key_name").eq(key_name)
            & Key("timestamp").gte(since),
            ScanIndexForward=False,
        )
        return [_decimal_to_float(i) for i in resp.get("Items", [])]

    def get_all_latest(self) -> list[dict[str, Any]]:
        """Scan all keys and return the most recent event per key."""
        resp = self._table.scan()
        items: list[dict[str, Any]] = resp.get("Items", [])
        while "LastEvaluatedKey" in resp:
            resp = self._table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"])
            items.extend(resp.get("Items", []))

        # Group by key_name and keep the most recent
        latest: dict[str, dict[str, Any]] = {}
        for item in items:
            kn = item["key_name"]
            if kn not in latest or item["timestamp"] > latest[kn]["timestamp"]:
                latest[kn] = item
        return [_decimal_to_float(v) for v in latest.values()]

    def scan_violating(self, breach_multiplier: float = 1.5) -> list[dict[str, Any]]:
        """Return latest events where staleness > threshold * multiplier."""
        latest = self.get_all_latest()
        violating = []
        for item in latest:
            staleness = item.get("staleness_ms", 0)
            threshold = item.get("threshold_ms", 1)
            if staleness > threshold * breach_multiplier:
                violating.append(item)
        return violating
