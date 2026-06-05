"""
Seed script — populates DynamoDB CacheKeyRegistry with 10 example keys
across 3 mock services: payments, auth, catalog.

Run: python -m scripts.seed_registry
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import structlog

from api.services.dynamodb import RegistryService, ensure_tables_exist
from datetime import datetime

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ]
)
logger = structlog.get_logger(__name__)

SEED_KEYS = [
    # ── Payments Service ─────────────────────────────────────────────────────
    {
        "key_name": "payments:user:1234:cart",
        "owning_service": "payments",
        "sla_ms": 10000,
        "tags": ["cart", "user-data", "ecommerce"],
        "description": "Shopping cart contents for user 1234",
        "created_at": datetime.utcnow().isoformat(),
        "auto_tagged": False,
    },
    {
        "key_name": "payments:session:token:abc123",
        "owning_service": "payments",
        "sla_ms": 2000,
        "tags": ["session", "auth", "token"],
        "description": "Payment session token for transaction abc123",
        "created_at": datetime.utcnow().isoformat(),
        "auto_tagged": False,
    },
    {
        "key_name": "payments:rate_limit:ip:192.168.1.1",
        "owning_service": "payments",
        "sla_ms": 1000,
        "tags": ["rate-limiting", "ip", "security"],
        "description": "Rate limit counter for IP 192.168.1.1",
        "created_at": datetime.utcnow().isoformat(),
        "auto_tagged": False,
    },
    # ── Auth Service ─────────────────────────────────────────────────────────
    {
        "key_name": "auth:user:5678:permissions",
        "owning_service": "auth",
        "sla_ms": 5000,
        "tags": ["permissions", "rbac", "user-data"],
        "description": "RBAC permissions for user 5678",
        "created_at": datetime.utcnow().isoformat(),
        "auto_tagged": False,
    },
    {
        "key_name": "auth:jwt:blacklist:token_xyz",
        "owning_service": "auth",
        "sla_ms": 500,
        "tags": ["jwt", "blacklist", "security", "real-time"],
        "description": "JWT token blacklist entry for revoked token",
        "created_at": datetime.utcnow().isoformat(),
        "auto_tagged": False,
    },
    {
        "key_name": "auth:mfa:otp:user:9012",
        "owning_service": "auth",
        "sla_ms": 1000,
        "tags": ["mfa", "otp", "security", "time-sensitive"],
        "description": "One-time password for MFA flow for user 9012",
        "created_at": datetime.utcnow().isoformat(),
        "auto_tagged": False,
    },
    {
        "key_name": "auth:oauth2:state:request_abc",
        "owning_service": "auth",
        "sla_ms": 3000,
        "tags": ["oauth2", "state", "csrf"],
        "description": "OAuth2 state parameter for CSRF protection",
        "created_at": datetime.utcnow().isoformat(),
        "auto_tagged": False,
    },
    # ── Catalog Service ───────────────────────────────────────────────────────
    {
        "key_name": "catalog:product:sku:WIDGET-001",
        "owning_service": "catalog",
        "sla_ms": 60000,
        "tags": ["product", "catalog", "sku", "read-heavy"],
        "description": "Product details for SKU WIDGET-001",
        "created_at": datetime.utcnow().isoformat(),
        "auto_tagged": False,
    },
    {
        "key_name": "catalog:category:electronics:top_sellers",
        "owning_service": "catalog",
        "sla_ms": 120000,
        "tags": ["category", "ranking", "analytics"],
        "description": "Top-selling products in electronics category",
        "created_at": datetime.utcnow().isoformat(),
        "auto_tagged": False,
    },
    {
        "key_name": "catalog:inventory:WIDGET-001:stock",
        "owning_service": "catalog",
        "sla_ms": 30000,
        "tags": ["inventory", "stock", "real-time"],
        "description": "Current stock level for WIDGET-001",
        "created_at": datetime.utcnow().isoformat(),
        "auto_tagged": False,
    },
]


def main() -> None:
    logger.info("Ensuring DynamoDB tables exist...")
    try:
        ensure_tables_exist()
    except Exception as exc:
        logger.warning("Table setup warning", error=str(exc))

    registry = RegistryService()
    seeded = 0
    skipped = 0

    for entry in SEED_KEYS:
        key_name = entry["key_name"]
        existing = registry.get(key_name)
        if existing:
            logger.info("Key already exists, skipping", key_name=key_name)
            skipped += 1
            continue
        registry.put(entry)
        logger.info("Seeded key", key_name=key_name, service=entry["owning_service"])
        seeded += 1

    logger.info(
        "Seed complete",
        seeded=seeded,
        skipped=skipped,
        total=len(SEED_KEYS),
    )


if __name__ == "__main__":
    main()
