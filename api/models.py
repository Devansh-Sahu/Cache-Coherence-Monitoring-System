"""
Pydantic models for Cache Staleness Monitor.
All domain objects are defined here with full type annotations.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


# ─────────────────────────────────────────────────────────────────────────────
# Registry Models
# ─────────────────────────────────────────────────────────────────────────────


class CacheKeyRegistryEntry(BaseModel):
    """A Redis key registered in the CacheKeyRegistry DynamoDB table."""

    key_name: str = Field(..., description="Redis key name (primary key)")
    owning_service: str = Field(..., description="Service that owns this cache key")
    sla_ms: int = Field(
        ..., ge=0, description="Maximum acceptable staleness in milliseconds"
    )
    tags: list[str] = Field(default_factory=list, description="Arbitrary string tags")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None
    auto_tagged: bool = Field(
        default=False, description="True if created by LLM auto-tagger"
    )
    description: Optional[str] = None


class CacheKeyRegistryCreate(BaseModel):
    """Request body for creating a registry entry."""

    key_name: str
    owning_service: str
    sla_ms: int = Field(..., ge=0)
    tags: list[str] = Field(default_factory=list)
    description: Optional[str] = None


class CacheKeyRegistryUpdate(BaseModel):
    """Request body for partial registry update (PATCH)."""

    owning_service: Optional[str] = None
    sla_ms: Optional[int] = Field(default=None, ge=0)
    tags: Optional[list[str]] = None
    description: Optional[str] = None


class AutoTagRequest(BaseModel):
    """Request payload for LLM auto-tagging a new key."""

    key_name: str
    sample_value: str = Field(
        ..., max_length=500, description="First N chars of the Redis value"
    )


class AutoTagResponse(BaseModel):
    """Result of LLM auto-tagging."""

    key_name: str
    owning_service: str
    sla_ms: int
    tags: list[str]
    description: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    raw_llm_response: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# Staleness Event Models
# ─────────────────────────────────────────────────────────────────────────────


class StalenessEvent(BaseModel):
    """A single staleness measurement for a Redis key."""

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    key_name: str
    staleness_ms: int = Field(..., ge=0, description="How stale the key is in ms")
    threshold_ms: int = Field(..., ge=0, description="SLA threshold in ms")
    ttl_remaining_s: Optional[int] = Field(
        default=None, description="Redis TTL in seconds (-1 = no expiry, -2 = gone)"
    )
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    owning_service: Optional[str] = None

    @property
    def sla_breach_pct(self) -> float:
        """Percentage over SLA (0.0 = on SLA, 1.0 = 100% over SLA)."""
        if self.threshold_ms == 0:
            return 0.0
        return max(0.0, (self.staleness_ms - self.threshold_ms) / self.threshold_ms)

    @property
    def is_breaching(self) -> bool:
        return self.staleness_ms > self.threshold_ms


class StalenessEventCreate(BaseModel):
    """Request body for ingesting a staleness event."""

    key_name: str
    staleness_ms: int = Field(..., ge=0)
    threshold_ms: int = Field(..., ge=0)
    ttl_remaining_s: Optional[int] = None
    owning_service: Optional[str] = None


class StalenessEventHistory(BaseModel):
    """A list of staleness events for a single key."""

    key_name: str
    events: list[StalenessEvent]
    total_count: int
    breach_count: int
    avg_staleness_ms: float
    max_staleness_ms: int
    last_checked: Optional[datetime]


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard Models
# ─────────────────────────────────────────────────────────────────────────────


class KeyStats(BaseModel):
    """Aggregate stats for a single cache key shown in the dashboard."""

    key_name: str
    owning_service: str
    sla_ms: int
    current_staleness_ms: Optional[int] = None
    avg_staleness_ms_24h: Optional[float] = None
    breach_count_24h: int = 0
    sla_breach_pct: float = 0.0
    status: str = "unknown"  # "healthy" | "warning" | "critical" | "unknown"
    last_checked: Optional[datetime] = None
    tags: list[str] = Field(default_factory=list)

    @field_validator("status", mode="before")
    @classmethod
    def compute_status(cls, v: Any, info: Any) -> str:
        # Allow explicit override
        if v and v != "unknown":
            return v
        return "unknown"


class DashboardStats(BaseModel):
    """Aggregated dashboard statistics across all monitored keys."""

    total_keys: int
    healthy_keys: int
    warning_keys: int
    critical_keys: int
    unknown_keys: int
    keys: list[KeyStats]
    generated_at: datetime = Field(default_factory=datetime.utcnow)


# ─────────────────────────────────────────────────────────────────────────────
# Alert Models
# ─────────────────────────────────────────────────────────────────────────────


class AlertRecord(BaseModel):
    """A staleness alert sent to Slack."""

    alert_id: str = Field(default_factory=lambda: str(uuid4()))
    key_name: str
    owning_service: str
    staleness_ms: int
    sla_ms: int
    breach_pct: float
    llm_summary: Optional[str] = None
    runbook_excerpt: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    slack_delivered: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# NL Query Models
# ─────────────────────────────────────────────────────────────────────────────


class AskRequest(BaseModel):
    """Natural language question about staleness data."""

    question: str = Field(..., min_length=3, max_length=500)


class AskResponse(BaseModel):
    """Plain-English answer to an NL query."""

    question: str
    answer: str
    raw_query: Optional[dict[str, Any]] = None
    raw_results_count: Optional[int] = None
    generated_at: datetime = Field(default_factory=datetime.utcnow)


# ─────────────────────────────────────────────────────────────────────────────
# RAG / Explain Models
# ─────────────────────────────────────────────────────────────────────────────


class ExplainResponse(BaseModel):
    """LLM explanation for a SLA breach using RAG context."""

    key_name: str
    current_event: StalenessEvent
    similar_incidents_count: int
    explanation: str
    suggested_fix: Optional[str] = None
    relevant_runbook: Optional[str] = None
    runbook_excerpt: Optional[str] = None
    generated_at: datetime = Field(default_factory=datetime.utcnow)
