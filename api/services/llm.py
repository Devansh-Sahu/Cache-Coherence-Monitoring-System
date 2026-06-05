"""
Anthropic LLM service wrapper.
- All calls are async (httpx-based Anthropic client)
- Output capped at ANTHROPIC_MAX_TOKENS (default 300)
- Input token count is logged
- Graceful degradation: returns None on failure, never raises
- Prompts loaded from prompts/ directory at runtime
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

import anthropic
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from api.config import get_settings

logger = structlog.get_logger(__name__)

PROMPTS_DIR = Path(__file__).parents[2] / "prompts"


def load_prompt(name: str) -> str:
    """Load a prompt from prompts/<name>.txt. Raises FileNotFoundError if missing."""
    path = PROMPTS_DIR / f"{name}.txt"
    return path.read_text(encoding="utf-8").strip()


class LLMService:
    """
    Async Anthropic client wrapper.
    
    Usage:
        llm = LLMService()
        result = await llm.complete(system="...", user="...")
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._client: Optional[anthropic.AsyncAnthropic] = None

    def _get_client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            if not self._settings.anthropic_api_key:
                raise RuntimeError("ANTHROPIC_API_KEY is not configured")
            self._client = anthropic.AsyncAnthropic(
                api_key=self._settings.anthropic_api_key
            )
        return self._client

    @retry(
        retry=retry_if_exception_type(anthropic.RateLimitError),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def complete(
        self,
        system: str,
        user: str,
        max_tokens: Optional[int] = None,
    ) -> Optional[str]:
        """
        Call Claude and return the text response.
        Returns None (never raises) on non-retryable errors.
        """
        if not self._settings.anthropic_api_key:
            logger.warning("LLM call skipped — no API key configured")
            return None

        max_tok = max_tokens or self._settings.anthropic_max_tokens
        try:
            client = self._get_client()
            msg = await client.messages.create(
                model=self._settings.anthropic_model,
                max_tokens=max_tok,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            input_tokens = msg.usage.input_tokens
            output_tokens = msg.usage.output_tokens
            logger.info(
                "LLM call completed",
                model=self._settings.anthropic_model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            return msg.content[0].text if msg.content else None
        except anthropic.RateLimitError:
            logger.warning("LLM rate limited — retrying")
            raise  # Let tenacity handle retry
        except anthropic.APIError as exc:
            logger.error("LLM API error — degrading gracefully", error=str(exc))
            return None
        except Exception as exc:
            logger.error("Unexpected LLM error", error=str(exc))
            return None

    async def complete_json(
        self,
        system: str,
        user: str,
        max_tokens: Optional[int] = None,
    ) -> Optional[dict[str, Any]]:
        """Call Claude and parse JSON from the response. Returns None on parse failure."""
        raw = await self.complete(system=system, user=user, max_tokens=max_tokens)
        if raw is None:
            return None
        # Strip markdown code fences if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        raw = re.sub(r"\s*```$", "", raw)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse LLM JSON response", error=str(exc), raw=raw)
            return None
