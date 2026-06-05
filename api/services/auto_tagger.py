"""
LLM Auto-Tagger service.
Phase 5: When a new Redis key appears, infer owning_service, sla_ms, and tags
using Claude Haiku. Result is stored in the registry with auto_tagged=True.
PII-safe: only key name + value prefix (max 200 chars) are sent to the LLM.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import structlog

from api.config import get_settings
from api.models import AutoTagRequest, AutoTagResponse
from api.services.dynamodb import RegistryService
from api.services.llm import LLMService, load_prompt

logger = structlog.get_logger(__name__)


class AutoTaggerService:
    """Pipeline: key + sample_value → Claude Haiku JSON → registry entry."""

    def __init__(self) -> None:
        self._llm = LLMService()
        self._registry = RegistryService()
        self._settings = get_settings()

    async def auto_tag(self, request: AutoTagRequest) -> AutoTagResponse:
        """
        Run auto-tagging for a Redis key.
        1. Truncate sample_value to AUTO_TAG_VALUE_MAX_CHARS (PII safety)
        2. Call Claude Haiku with auto_tag.txt prompt
        3. Parse JSON response
        4. Store in registry with auto_tagged=True
        5. Return AutoTagResponse
        """
        max_chars = self._settings.auto_tag_value_max_chars
        safe_value = request.sample_value[:max_chars]

        system_prompt = load_prompt("auto_tag")
        user_message = (
            f'Key name: "{request.key_name}"\n'
            f"Sample value: {safe_value}\n\n"
            f"Infer: owning_service, sla_ms, tags[], description, confidence (0-1).\n"
            f"Return valid JSON only — no explanation."
        )

        raw_response: Optional[str] = None
        parsed: Optional[dict[str, Any]] = None

        try:
            parsed = await self._llm.complete_json(
                system=system_prompt, user=user_message
            )
            if parsed:
                raw_response = str(parsed)
        except Exception as exc:
            logger.warning("Auto-tag LLM call failed", error=str(exc))

        # Fallback if LLM fails
        if not parsed:
            logger.warning(
                "Auto-tag falling back to defaults", key_name=request.key_name
            )
            parsed = self._fallback_tag(request.key_name)

        # Validate required fields
        owning_service = str(parsed.get("owning_service", "unknown"))
        sla_ms = int(parsed.get("sla_ms", 5000))
        tags: list[str] = parsed.get("tags", [])
        description: Optional[str] = parsed.get("description")
        confidence = float(parsed.get("confidence", 0.0))

        # Write to registry
        entry: dict[str, Any] = {
            "key_name": request.key_name,
            "owning_service": owning_service,
            "sla_ms": sla_ms,
            "tags": tags,
            "description": description,
            "created_at": datetime.utcnow().isoformat(),
            "auto_tagged": True,
        }
        self._registry.put(entry)
        logger.info(
            "Auto-tagged key",
            key_name=request.key_name,
            owning_service=owning_service,
            confidence=confidence,
        )

        return AutoTagResponse(
            key_name=request.key_name,
            owning_service=owning_service,
            sla_ms=sla_ms,
            tags=tags,
            description=description,
            confidence=confidence,
            raw_llm_response=raw_response,
        )

    @staticmethod
    def _fallback_tag(key_name: str) -> dict[str, Any]:
        """Heuristic fallback when LLM is unavailable."""
        parts = key_name.split(":")
        service = parts[0] if parts else "unknown"
        tag_hints: list[str] = []

        if "cart" in key_name:
            tag_hints = ["cart", "ecommerce"]
            sla = 10_000
        elif "session" in key_name or "auth" in key_name:
            tag_hints = ["auth", "session"]
            sla = 2_000
        elif "catalog" in key_name or "product" in key_name:
            tag_hints = ["catalog", "read-heavy"]
            sla = 60_000
        elif "rate" in key_name or "limit" in key_name:
            tag_hints = ["rate-limiting"]
            sla = 1_000
        else:
            sla = 5_000

        return {
            "owning_service": service,
            "sla_ms": sla,
            "tags": tag_hints,
            "description": f"Auto-tagged from key pattern: {key_name}",
            "confidence": 0.3,
        }
