"""
Dashboard router — aggregated stats for all monitored cache keys.
Powers the React dashboard table and summary cards.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter

from api.models import DashboardStats, KeyStats
from api.services.dynamodb import RegistryService, StalenessHistoryService

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

_registry = RegistryService()
_history = StalenessHistoryService()


def _classify_status(staleness_ms: float, sla_ms: int) -> str:
    if sla_ms == 0:
        return "unknown"
    ratio = staleness_ms / sla_ms
    if ratio <= 0.8:
        return "healthy"
    elif ratio <= 1.0:
        return "warning"
    else:
        return "critical"


@router.get("/", response_model=DashboardStats)
async def get_dashboard() -> dict[str, Any]:
    """
    Aggregate stats for all registered cache keys.
    Returns current staleness, 24h average, breach count, and status per key.
    """
    registry_entries = _registry.list_all()
    latest_events = _history.get_all_latest()

    # Build lookup: key_name → latest event
    latest_by_key: dict[str, dict[str, Any]] = {
        e["key_name"]: e for e in latest_events
    }

    key_stats: list[KeyStats] = []

    for entry in registry_entries:
        key_name = entry["key_name"]
        sla_ms = int(entry.get("sla_ms", 5000))
        owning_service = entry.get("owning_service", "unknown")
        tags = entry.get("tags", [])

        latest = latest_by_key.get(key_name)
        current_staleness: int | None = None
        last_checked: datetime | None = None
        status = "unknown"

        if latest:
            current_staleness = int(latest.get("staleness_ms", 0))
            ts_raw = latest.get("timestamp")
            if ts_raw:
                try:
                    last_checked = datetime.fromisoformat(str(ts_raw))
                except ValueError:
                    last_checked = None
            status = _classify_status(current_staleness, sla_ms)

        # 24h history for avg and breach count
        events_24h = _history.get_events(key_name, hours=24)
        staleness_values = [e.get("staleness_ms", 0) for e in events_24h]
        breach_count = sum(
            1 for e in events_24h
            if e.get("staleness_ms", 0) > e.get("threshold_ms", sla_ms)
        )
        avg_24h = (
            sum(staleness_values) / len(staleness_values) if staleness_values else None
        )
        breach_pct = 0.0
        if current_staleness is not None and sla_ms > 0:
            breach_pct = max(0.0, (current_staleness - sla_ms) / sla_ms)

        key_stats.append(
            KeyStats(
                key_name=key_name,
                owning_service=owning_service,
                sla_ms=sla_ms,
                current_staleness_ms=current_staleness,
                avg_staleness_ms_24h=avg_24h,
                breach_count_24h=breach_count,
                sla_breach_pct=breach_pct,
                status=status,
                last_checked=last_checked,
                tags=tags,
            )
        )

    status_counts = {s: 0 for s in ["healthy", "warning", "critical", "unknown"]}
    for ks in key_stats:
        status_counts[ks.status] = status_counts.get(ks.status, 0) + 1

    return {
        "total_keys": len(key_stats),
        "healthy_keys": status_counts["healthy"],
        "warning_keys": status_counts["warning"],
        "critical_keys": status_counts["critical"],
        "unknown_keys": status_counts["unknown"],
        "keys": [ks.model_dump() for ks in key_stats],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
