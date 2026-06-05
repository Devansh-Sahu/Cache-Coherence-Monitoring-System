"""Registry router — CRUD for CacheKeyRegistry + LLM auto-tag endpoint."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, status

from api.models import (
    AutoTagRequest,
    AutoTagResponse,
    CacheKeyRegistryCreate,
    CacheKeyRegistryEntry,
    CacheKeyRegistryUpdate,
)
from api.services.auto_tagger import AutoTaggerService
from api.services.dynamodb import RegistryService

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/registry", tags=["registry"])

_registry = RegistryService()
_tagger = AutoTaggerService()


@router.get("/", response_model=list[CacheKeyRegistryEntry])
async def list_registry() -> list[dict[str, Any]]:
    """List all registered cache keys."""
    return _registry.list_all()


@router.get("/{key_name}", response_model=CacheKeyRegistryEntry)
async def get_registry_entry(key_name: str) -> dict[str, Any]:
    """Get a single registry entry by key name."""
    entry = _registry.get(key_name)
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Key '{key_name}' not found in registry",
        )
    return entry


@router.post("/", response_model=CacheKeyRegistryEntry, status_code=status.HTTP_201_CREATED)
async def create_registry_entry(
    body: CacheKeyRegistryCreate,
) -> dict[str, Any]:
    """Register a new cache key manually."""
    if _registry.get(body.key_name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Key '{body.key_name}' already exists. Use PATCH to update.",
        )
    entry: dict[str, Any] = {
        **body.model_dump(),
        "created_at": datetime.utcnow().isoformat(),
        "auto_tagged": False,
    }
    _registry.put(entry)
    logger.info("Registered new key", key_name=body.key_name)
    return entry


@router.patch("/{key_name}", response_model=CacheKeyRegistryEntry)
async def update_registry_entry(
    key_name: str, body: CacheKeyRegistryUpdate
) -> dict[str, Any]:
    """Partially update an existing registry entry."""
    if not _registry.get(key_name):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Key '{key_name}' not found",
        )
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    result = _registry.update(key_name, updates)
    if not result:
        raise HTTPException(status_code=500, detail="Update failed")
    return result


@router.delete("/{key_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_registry_entry(key_name: str) -> None:
    """Remove a key from the registry."""
    if not _registry.get(key_name):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Key '{key_name}' not found",
        )
    _registry.delete(key_name)
    logger.info("Deleted registry entry", key_name=key_name)


@router.post(
    "/auto-tag",
    response_model=AutoTagResponse,
    status_code=status.HTTP_201_CREATED,
)
async def auto_tag_key(body: AutoTagRequest) -> AutoTagResponse:
    """
    Phase 5 — LLM Auto-Tagger.
    Infer owning_service, sla_ms, and tags for an unregistered Redis key.
    """
    result = await _tagger.auto_tag(body)
    logger.info(
        "Auto-tagged key via API",
        key_name=body.key_name,
        service=result.owning_service,
    )
    return result
