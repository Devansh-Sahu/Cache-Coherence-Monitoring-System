"""
Seed script — writes example Redis keys with shadow keys for staleness tracking.
Creates keys at various staleness levels for testing.

Run: python -m scripts.seed_redis_keys
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import redis
import structlog

from api.config import get_settings

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ]
)
logger = structlog.get_logger(__name__)


def write_key(
    r: redis.Redis,  # type: ignore[type-arg]
    key_name: str,
    value: object,
    ttl_s: int,
    fake_age_s: int = 0,
) -> None:
    """
    Write a key with its shadow key.
    fake_age_s: simulate a key that was written N seconds ago (for testing staleness).
    """
    now = time.time()
    last_write = now - fake_age_s

    # Write the actual value
    r.setex(key_name, ttl_s, json.dumps(value))

    # Write shadow key (stores last-write Unix timestamp)
    shadow_key = f"__meta:{key_name}:last_write"
    r.setex(shadow_key, ttl_s + 300, str(last_write))

    staleness_ms = int(fake_age_s * 1000)
    logger.info(
        "Wrote key",
        key=key_name,
        ttl_s=ttl_s,
        staleness_ms=staleness_ms,
    )


def main() -> None:
    settings = get_settings()
    r = redis.from_url(settings.redis_url, decode_responses=True)

    try:
        r.ping()
        logger.info("Connected to Redis", url=settings.redis_url)
    except Exception as exc:
        logger.error("Cannot connect to Redis", error=str(exc))
        sys.exit(1)

    # ── Healthy keys (low staleness) ─────────────────────────────────────────
    write_key(
        r,
        "payments:user:1234:cart",
        {"items": [{"sku": "WIDGET-001", "qty": 2}], "total": 49.99},
        ttl_s=300,
        fake_age_s=5,  # 5s old — well within 10s SLA
    )
    write_key(
        r,
        "auth:user:5678:permissions",
        {"roles": ["customer", "premium"], "can_checkout": True},
        ttl_s=600,
        fake_age_s=2,  # 2s old — within 5s SLA
    )
    write_key(
        r,
        "catalog:product:sku:WIDGET-001",
        {"name": "Widget Pro", "price": 24.99, "in_stock": True, "category": "electronics"},
        ttl_s=3600,
        fake_age_s=30,  # 30s old — within 60s SLA
    )

    # ── Warning keys (near SLA) ───────────────────────────────────────────────
    write_key(
        r,
        "catalog:category:electronics:top_sellers",
        ["WIDGET-001", "GADGET-050", "DEVICE-103"],
        ttl_s=7200,
        fake_age_s=100,  # 100s old — near 120s SLA
    )
    write_key(
        r,
        "auth:oauth2:state:request_abc",
        {"state": "xyz789", "redirect_uri": "https://app.example.com/callback"},
        ttl_s=300,
        fake_age_s=2,  # 2s old — within 3s SLA
    )

    # ── Breaching keys (over SLA) ─────────────────────────────────────────────
    write_key(
        r,
        "payments:session:token:abc123",
        {"user_id": "1234", "amount": 99.99, "currency": "USD"},
        ttl_s=3600,
        fake_age_s=15,  # 15s old — OVER 2s SLA (750% breach)
    )
    write_key(
        r,
        "auth:jwt:blacklist:token_xyz",
        {"revoked_at": "2024-01-15T10:00:00Z", "reason": "logout"},
        ttl_s=86400,
        fake_age_s=5,  # 5s old — OVER 500ms SLA (900% breach)
    )
    write_key(
        r,
        "payments:rate_limit:ip:192.168.1.1",
        {"count": 95, "window_start": int(time.time() - 60)},
        ttl_s=60,
        fake_age_s=3,  # 3s old — OVER 1s SLA (200% breach)
    )
    write_key(
        r,
        "catalog:inventory:WIDGET-001:stock",
        {"available": 42, "reserved": 8, "last_sync": "2024-01-15T10:00:00Z"},
        ttl_s=1800,
        fake_age_s=45,  # 45s old — OVER 30s SLA (50% breach)
    )
    write_key(
        r,
        "auth:mfa:otp:user:9012",
        {"otp": "847291", "expires_in": 30},
        ttl_s=30,
        fake_age_s=25,  # 25s old — OVER 1s SLA (severe breach)
    )

    logger.info("Redis seed complete", total_keys=10)
    r.close()


if __name__ == "__main__":
    main()
