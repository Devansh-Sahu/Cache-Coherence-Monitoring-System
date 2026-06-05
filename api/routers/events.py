"""
Events router — staleness event ingest, history retrieval, and LLM anomaly explanation.
Phase 3 + Phase 7 (RAG explain).
"""

from __future__ import annotations

from typing import Any, Optional

import structlog
from fastapi import APIRouter, HTTPException, Query, status

from api.models import (
    ExplainResponse,
    StalenessEvent,
    StalenessEventCreate,
    StalenessEventHistory,
)
from api.services.dynamodb import RegistryService, StalenessHistoryService
from api.services.llm import LLMService, load_prompt
from api.services.rag import StalenessRAGService

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/events", tags=["events"])

_history = StalenessHistoryService()
_registry = RegistryService()
_rag = StalenessRAGService()
_llm = LLMService()


@router.post("/", response_model=StalenessEvent, status_code=status.HTTP_201_CREATED)
async def ingest_event(body: StalenessEventCreate) -> dict[str, Any]:
    """
    Ingest a new staleness measurement.
    - Stores in DynamoDB StalenessHistory
    - If SLA is breached, embeds event into PGVector for RAG
    """
    event = StalenessEvent(**body.model_dump())

    # Enrich with registry metadata if available
    reg_entry = _registry.get(event.key_name)
    if reg_entry and not event.owning_service:
        event.owning_service = reg_entry.get("owning_service")

    item: dict[str, Any] = {
        **event.model_dump(),
        "timestamp": event.timestamp.isoformat(),
    }
    _history.put_event(item)
    logger.info(
        "Ingested staleness event",
        key_name=event.key_name,
        staleness_ms=event.staleness_ms,
        is_breaching=event.is_breaching,
    )

    # Phase 7: Embed breach events into PGVector (best-effort, don't block response)
    if event.is_breaching:
        try:
            _rag.embed_event(item)
        except Exception as exc:
            logger.warning(
                "RAG embed failed (non-critical)", error=str(exc)
            )

    return item


@router.get("/{key_name}", response_model=StalenessEventHistory)
async def get_event_history(
    key_name: str,
    hours: int = Query(default=24, ge=1, le=720),
) -> dict[str, Any]:
    """Get staleness history for a key over the last N hours."""
    events_raw = _history.get_events(key_name, hours=hours)

    if not events_raw:
        return {
            "key_name": key_name,
            "events": [],
            "total_count": 0,
            "breach_count": 0,
            "avg_staleness_ms": 0.0,
            "max_staleness_ms": 0,
            "last_checked": None,
        }

    staleness_values = [e.get("staleness_ms", 0) for e in events_raw]
    threshold_values = [e.get("threshold_ms", 0) for e in events_raw]
    breach_count = sum(
        1
        for s, t in zip(staleness_values, threshold_values)
        if s > t
    )

    return {
        "key_name": key_name,
        "events": events_raw,
        "total_count": len(events_raw),
        "breach_count": breach_count,
        "avg_staleness_ms": (
            sum(staleness_values) / len(staleness_values)
        ),
        "max_staleness_ms": max(staleness_values),
        "last_checked": events_raw[0].get("timestamp"),
    }


@router.get("/{key_name}/explain", response_model=ExplainResponse)
async def explain_breach(key_name: str) -> dict[str, Any]:
    """
    Phase 7 — RAG Anomaly Explainer.
    Retrieve the most recent breach event, find similar past incidents via PGVector,
    and ask Claude Haiku to explain the likely cause.
    """
    # Get most recent breach event
    recent = _history.get_events(key_name, hours=168)  # last 7 days
    breach_events = [
        e for e in recent if e.get("staleness_ms", 0) > e.get("threshold_ms", 0)
    ]
    if not breach_events:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No SLA breach events found for key '{key_name}' in the last 7 days",
        )

    current_event_raw = breach_events[0]
    current_event = StalenessEvent(**{
        k: v for k, v in current_event_raw.items()
        if k in StalenessEvent.model_fields
    })

    # Retrieve similar past incidents from PGVector
    similar = _rag.retrieve_similar(current_event_raw, top_k=5)
    similar_count = len(similar)
    similar_text = "\n".join(
        f"- [{s['key_name']} @ {s.get('timestamp', 'unknown')}]: {s['content']}"
        for s in similar
    )

    # Retrieve relevant runbook
    from api.services.rag import RunbookRAGService
    runbook_svc = RunbookRAGService()
    incident_text = (
        f"{key_name} SLA breach {current_event.staleness_ms}ms "
        f"threshold {current_event.threshold_ms}ms"
    )
    runbook_chunks = runbook_svc.retrieve_relevant(incident_text, top_k=1)
    runbook_excerpt: Optional[str] = None
    runbook_name: Optional[str] = None
    if runbook_chunks:
        runbook_name = runbook_chunks[0]["runbook_name"]
        runbook_excerpt = runbook_chunks[0]["content"][:300]

    # Call Claude Haiku
    system_prompt = load_prompt("anomaly_explain")
    user_message = (
        f"Current incident:\n"
        f"  Key: {key_name}\n"
        f"  Service: {current_event.owning_service or 'unknown'}\n"
        f"  Staleness: {current_event.staleness_ms}ms (SLA: {current_event.threshold_ms}ms)\n"
        f"  Timestamp: {current_event.timestamp.isoformat()}\n\n"
        f"Similar past incidents ({similar_count}):\n{similar_text or 'None found.'}\n\n"
        f"Relevant runbook section:\n{runbook_excerpt or 'No runbook matched.'}\n\n"
        "What likely caused this breach? What has fixed it before? "
        "Provide: 1) root cause hypothesis, 2) suggested fix."
    )

    explanation = await _llm.complete(system=system_prompt, user=user_message)
    if not explanation:
        explanation = (
            f"LLM unavailable. Key '{key_name}' is {current_event.staleness_ms}ms stale "
            f"(SLA: {current_event.threshold_ms}ms). "
            f"Check cache write pipeline and TTL configuration."
        )

    return {
        "key_name": key_name,
        "current_event": current_event.model_dump(),
        "similar_incidents_count": similar_count,
        "explanation": explanation,
        "suggested_fix": None,  # Parsed from LLM text if structured
        "relevant_runbook": runbook_name,
        "runbook_excerpt": runbook_excerpt,
    }
