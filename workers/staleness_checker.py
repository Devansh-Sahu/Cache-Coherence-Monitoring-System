"""
Staleness Checker Worker — Phase 4.
Runs every 30 seconds, polls all registered Redis keys, and publishes
staleness events to the FastAPI backend.

Shadow key convention: __meta:{original_key}:last_write → Unix timestamp (float)
Written by the cache producer when it writes a new value.
If the shadow key doesn't exist, we fall back to TTL-based estimation.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
import redis.asyncio as aioredis
import structlog

# Add project root to path for standalone execution
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.config import get_settings

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ]
)
logger = structlog.get_logger(__name__)

API_BASE_URL = "http://localhost:8000"


class StalenessChecker:
    """
    Async Redis staleness checker.

    For each registered key:
    1. Check if the shadow key (__meta:{key}:last_write) exists
    2. If yes: staleness_ms = now - last_write_timestamp
    3. If no: estimate staleness from TTL (staleness ~ original_ttl - ttl_remaining)
    4. POST the event to the API
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._redis: Optional[aioredis.Redis] = None  # type: ignore[type-arg]
        self._http: Optional[httpx.AsyncClient] = None

    async def _get_redis(self) -> aioredis.Redis:  # type: ignore[type-arg]
        if self._redis is None:
            self._redis = await aioredis.from_url(
                self._settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
        return self._redis

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=API_BASE_URL,
                timeout=10.0,
            )
        return self._http

    async def _get_registered_keys(self) -> list[dict[str, Any]]:
        """Fetch all registered keys from the API."""
        http = await self._get_http()
        try:
            resp = await http.get("/api/registry/")
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning("Failed to fetch registry", error=str(exc))
            return []

    async def _check_key(
        self, redis: aioredis.Redis, entry: dict[str, Any]  # type: ignore[type-arg]
    ) -> Optional[dict[str, Any]]:
        """Check staleness for a single key. Returns event payload or None."""
        key_name: str = entry["key_name"]
        sla_ms: int = int(entry.get("sla_ms", 5000))
        shadow_key = f"__meta:{key_name}:last_write"

        now_ms = int(time.time() * 1000)
        staleness_ms: Optional[int] = None
        ttl_remaining_s: Optional[int] = None

        try:
            # Check if the key exists
            exists = await redis.exists(key_name)
            if not exists:
                logger.debug("Key not found in Redis, skipping", key=key_name)
                return None

            # Get TTL
            ttl = await redis.ttl(key_name)
            ttl_remaining_s = int(ttl)

            # Try shadow key first
            last_write_raw = await redis.get(shadow_key)
            if last_write_raw:
                last_write_ms = float(last_write_raw) * 1000
                staleness_ms = int(now_ms - last_write_ms)
            elif ttl_remaining_s > 0:
                # Heuristic: assume original TTL matches SLA, estimate staleness
                # staleness ≈ sla_ms - ttl_remaining_ms (rough estimate)
                staleness_ms = max(0, sla_ms - ttl_remaining_s * 1000)
            else:
                # No shadow key, no TTL → can't determine staleness
                staleness_ms = 0

        except Exception as exc:
            logger.warning("Error checking key", key=key_name, error=str(exc))
            return None

        return {
            "key_name": key_name,
            "staleness_ms": staleness_ms,
            "threshold_ms": sla_ms,
            "ttl_remaining_s": ttl_remaining_s,
            "owning_service": entry.get("owning_service"),
        }

    async def _post_event(self, event: dict[str, Any]) -> None:
        """POST a staleness event to the API."""
        http = await self._get_http()
        try:
            resp = await http.post("/api/events/", json=event)
            resp.raise_for_status()
            logger.debug(
                "Posted staleness event",
                key=event["key_name"],
                staleness_ms=event["staleness_ms"],
            )
        except Exception as exc:
            logger.warning("Failed to post event", key=event["key_name"], error=str(exc))

    async def run_once(self) -> int:
        """Run one staleness check pass. Returns number of events posted."""
        redis = await self._get_redis()
        entries = await self._get_registered_keys()

        if not entries:
            logger.info("No registered keys found")
            return 0

        tasks = [self._check_key(redis, entry) for entry in entries]
        results = await asyncio.gather(*tasks, return_exceptions=False)

        count = 0
        for result in results:
            if result is not None:
                await self._post_event(result)
                count += 1

        logger.info("Staleness check complete", keys_checked=len(entries), events_posted=count)
        return count

    async def run_forever(self) -> None:
        """Main loop — runs every STALENESS_CHECK_INTERVAL_SECONDS seconds."""
        interval = self._settings.staleness_check_interval_seconds
        logger.info(
            "Staleness checker started",
            interval_s=interval,
            redis_url=self._settings.redis_url,
        )
        while True:
            try:
                await self.run_once()
            except Exception as exc:
                logger.error("Unhandled error in staleness checker", error=str(exc))
            await asyncio.sleep(interval)

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()
        if self._http:
            await self._http.aclose()


async def main() -> None:
    checker = StalenessChecker()
    try:
        await checker.run_forever()
    except KeyboardInterrupt:
        logger.info("Staleness checker stopped by user")
    finally:
        await checker.close()


if __name__ == "__main__":
    asyncio.run(main())
