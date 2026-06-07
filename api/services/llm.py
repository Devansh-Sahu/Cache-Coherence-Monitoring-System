"""
Groq LLM service wrapper.
- Uses groq Python SDK (OpenAI-compatible REST)
- Default model: llama-3.3-70b-versatile (fast, generous free tier)
- All calls are async
- Output capped at GROQ_MAX_TOKENS (default 500)
- Token usage is logged
- Graceful degradation: returns None on failure, never raises
- Prompts loaded from prompts/ directory at runtime
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

import structlog
from groq import AsyncGroq, RateLimitError, APIError
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
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8").strip()


class LLMService:
    """
    Async Groq client wrapper.

    Usage:
        llm = LLMService()
        result = await llm.complete(system="...", user="...")
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._client: Optional[AsyncGroq] = None

    def _get_client(self) -> AsyncGroq:
        if self._client is None:
            if not self._settings.groq_api_key:
                raise RuntimeError("GROQ_API_KEY is not configured")
            self._client = AsyncGroq(api_key=self._settings.groq_api_key)
        return self._client

    @retry(
        retry=retry_if_exception_type(RateLimitError),
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
        Call the Groq LLM and return the text response.
        Returns None (never raises) on non-retryable errors.
        """
        if not self._settings.groq_api_key:
            logger.warning("LLM call skipped — GROQ_API_KEY not configured")
            return None

        max_tok = max_tokens or self._settings.groq_max_tokens
        try:
            client = self._get_client()
            response = await client.chat.completions.create(
                model=self._settings.groq_model,
                max_tokens=max_tok,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.3,
            )
            usage = response.usage
            if usage:
                logger.info(
                    "LLM call completed",
                    model=self._settings.groq_model,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                    total_tokens=usage.total_tokens,
                )
            text = response.choices[0].message.content if response.choices else None
            return text

        except RateLimitError:
            logger.warning("Groq rate limited — retrying")
            raise  # Let tenacity handle retry

        except APIError as exc:
            logger.error("Groq API error — degrading gracefully", error=str(exc))
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
        """Call Groq and parse JSON from the response. Returns None on parse failure."""
        raw = await self.complete(system=system, user=user, max_tokens=max_tokens)
        if raw is None:
            return None
        # Strip markdown code fences if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        raw = re.sub(r"\s*```$", "", raw)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse LLM JSON response", error=str(exc), raw=raw[:200])
            return None
