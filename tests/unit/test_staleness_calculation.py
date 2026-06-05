"""Unit tests for staleness calculation logic."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workers.staleness_checker import StalenessChecker


class TestStalenessCalculation:
    """Tests for the staleness checker's core calculation logic."""

    @pytest.fixture
    def checker(self) -> StalenessChecker:
        return StalenessChecker()

    @pytest.mark.asyncio
    async def test_shadow_key_staleness(self, checker: StalenessChecker) -> None:
        """Staleness calculated correctly from shadow key."""
        now = time.time()
        last_write = now - 5.0  # 5 seconds ago

        mock_redis = AsyncMock()
        mock_redis.exists.return_value = 1
        mock_redis.ttl.return_value = 295
        mock_redis.get.return_value = str(last_write)

        with patch.object(checker, "_get_redis", return_value=mock_redis):
            # Monkey-patch _get_redis as async
            checker._redis = mock_redis
            entry = {"key_name": "test:key", "sla_ms": 10_000}
            result = await checker._check_key(mock_redis, entry)

        assert result is not None
        assert abs(result["staleness_ms"] - 5000) < 200  # within 200ms tolerance
        assert result["threshold_ms"] == 10_000
        assert result["ttl_remaining_s"] == 295

    @pytest.mark.asyncio
    async def test_missing_shadow_key_uses_ttl_heuristic(self, checker: StalenessChecker) -> None:
        """Falls back to TTL heuristic when shadow key is missing."""
        mock_redis = AsyncMock()
        mock_redis.exists.return_value = 1
        mock_redis.ttl.return_value = 5000  # ttl_remaining_s
        mock_redis.get.return_value = None  # No shadow key

        checker._redis = mock_redis
        entry = {"key_name": "test:key", "sla_ms": 10_000}
        result = await checker._check_key(mock_redis, entry)

        assert result is not None
        # Heuristic: staleness = max(0, sla_ms - ttl_remaining_ms)
        # = max(0, 10000 - 5000*1000) = 0 (can't be negative)
        assert result["staleness_ms"] >= 0

    @pytest.mark.asyncio
    async def test_nonexistent_key_returns_none(self, checker: StalenessChecker) -> None:
        """Returns None for keys not present in Redis."""
        mock_redis = AsyncMock()
        mock_redis.exists.return_value = 0  # Key doesn't exist

        checker._redis = mock_redis
        entry = {"key_name": "missing:key", "sla_ms": 5_000}
        result = await checker._check_key(mock_redis, entry)

        assert result is None

    @pytest.mark.asyncio
    async def test_redis_error_returns_none(self, checker: StalenessChecker) -> None:
        """Returns None gracefully when Redis throws an error."""
        mock_redis = AsyncMock()
        mock_redis.exists.side_effect = ConnectionError("Redis down")

        checker._redis = mock_redis
        entry = {"key_name": "test:key", "sla_ms": 5_000}
        result = await checker._check_key(mock_redis, entry)

        assert result is None
