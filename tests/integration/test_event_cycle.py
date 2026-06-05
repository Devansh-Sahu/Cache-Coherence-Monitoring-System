"""
Integration tests for the event ingest → DynamoDB → retrieve cycle.
Uses moto (DynamoDB mock) so no real AWS needed.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws

# Set env vars before importing our modules
os.environ.setdefault("USE_LOCALSTACK", "false")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("DYNAMODB_REGISTRY_TABLE", "CacheKeyRegistry")
os.environ.setdefault("DYNAMODB_HISTORY_TABLE", "StalenessHistory")
os.environ.setdefault("DATABASE_URL", "")
os.environ.setdefault("ANTHROPIC_API_KEY", "")


@mock_aws
class TestEventIngestCycle:
    """Full event ingest → DynamoDB → retrieve integration test."""

    def setup_method(self) -> None:
        """Create DynamoDB tables before each test."""
        from api.services.dynamodb import ensure_tables_exist

        # Reset the lru_cache so settings pick up env vars
        from api.config import get_settings
        get_settings.cache_clear()

        ensure_tables_exist()

    def _make_registry_entry(self, key_name: str = "payments:user:1234:cart") -> dict:  # type: ignore[type-arg]
        return {
            "key_name": key_name,
            "owning_service": "payments",
            "sla_ms": 10_000,
            "tags": ["cart"],
            "created_at": datetime.utcnow().isoformat(),
            "auto_tagged": False,
        }

    @mock_aws
    def test_registry_put_and_get(self) -> None:
        """Can put a registry entry and retrieve it."""
        from api.services.dynamodb import RegistryService
        svc = RegistryService()
        entry = self._make_registry_entry()
        svc.put(entry)
        result = svc.get("payments:user:1234:cart")
        assert result is not None
        assert result["key_name"] == "payments:user:1234:cart"
        assert result["owning_service"] == "payments"
        assert result["sla_ms"] == 10_000

    @mock_aws
    def test_registry_list_all(self) -> None:
        """list_all returns all entries."""
        from api.services.dynamodb import RegistryService
        svc = RegistryService()
        for i in range(3):
            svc.put(self._make_registry_entry(f"test:key:{i}"))
        all_entries = svc.list_all()
        keys = [e["key_name"] for e in all_entries]
        assert "test:key:0" in keys
        assert "test:key:2" in keys

    @mock_aws
    def test_registry_update(self) -> None:
        """PATCH update modifies existing entry."""
        from api.services.dynamodb import RegistryService
        svc = RegistryService()
        svc.put(self._make_registry_entry())
        result = svc.update("payments:user:1234:cart", {"sla_ms": 20_000})
        assert result is not None
        assert result["sla_ms"] == 20_000

    @mock_aws
    def test_staleness_event_ingest_and_retrieve(self) -> None:
        """Staleness event can be written and retrieved."""
        from api.services.dynamodb import StalenessHistoryService
        svc = StalenessHistoryService()

        event = {
            "key_name": "payments:user:1234:cart",
            "staleness_ms": 15_000,
            "threshold_ms": 10_000,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "owning_service": "payments",
            "event_id": "test-event-001",
        }
        svc.put_event(event)

        events = svc.get_events("payments:user:1234:cart", hours=1)
        assert len(events) == 1
        assert events[0]["staleness_ms"] == 15_000
        assert events[0]["threshold_ms"] == 10_000

    @mock_aws
    def test_scan_violating_finds_breaches(self) -> None:
        """scan_violating correctly identifies keys over SLA * multiplier."""
        from api.services.dynamodb import StalenessHistoryService
        svc = StalenessHistoryService()

        # Healthy event
        svc.put_event({
            "key_name": "healthy:key",
            "staleness_ms": 1_000,
            "threshold_ms": 10_000,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "owning_service": "test",
            "event_id": "evt-healthy",
        })

        # Violating event (staleness > threshold * 1.5)
        svc.put_event({
            "key_name": "violating:key",
            "staleness_ms": 20_000,
            "threshold_ms": 10_000,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "owning_service": "test",
            "event_id": "evt-violating",
        })

        violating = svc.scan_violating(breach_multiplier=1.5)
        violating_keys = [v["key_name"] for v in violating]
        assert "violating:key" in violating_keys
        assert "healthy:key" not in violating_keys

    @mock_aws
    def test_registry_delete(self) -> None:
        """Entry can be deleted from registry."""
        from api.services.dynamodb import RegistryService
        svc = RegistryService()
        svc.put(self._make_registry_entry())
        svc.delete("payments:user:1234:cart")
        assert svc.get("payments:user:1234:cart") is None
