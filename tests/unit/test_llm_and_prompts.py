"""Unit tests for LLM prompt building and query sanitizer."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.services.llm import LLMService, load_prompt
from api.services.auto_tagger import AutoTaggerService
from api.models import AutoTagRequest


class TestPromptLoader:
    """Tests for the prompt loader utility."""

    def test_load_existing_prompt(self) -> None:
        """Successfully loads a prompt from prompts/ directory."""
        prompt = load_prompt("auto_tag")
        assert len(prompt) > 50
        assert "JSON" in prompt or "json" in prompt

    def test_load_nonexistent_prompt_raises(self) -> None:
        """Raises FileNotFoundError for missing prompt."""
        with pytest.raises(FileNotFoundError):
            load_prompt("nonexistent_prompt_xyz")


class TestLLMService:
    """Tests for the LLM service wrapper."""

    @pytest.fixture
    def llm(self) -> LLMService:
        return LLMService()

    @pytest.mark.asyncio
    async def test_returns_none_without_api_key(self, llm: LLMService) -> None:
        """Returns None gracefully when no API key is configured."""
        with patch.object(llm._settings, "anthropic_api_key", ""):
            result = await llm.complete(system="test", user="test")
        assert result is None

    @pytest.mark.asyncio
    async def test_complete_json_strips_code_fences(self, llm: LLMService) -> None:
        """Strips markdown code fences from JSON response."""
        mock_response = '```json\n{"key": "value"}\n```'
        with patch.object(llm, "complete", return_value=mock_response):
            result = await llm.complete_json(system="sys", user="usr")
        assert result == {"key": "value"}

    @pytest.mark.asyncio
    async def test_complete_json_returns_none_on_invalid(self, llm: LLMService) -> None:
        """Returns None when LLM returns non-JSON."""
        with patch.object(llm, "complete", return_value="this is not JSON"):
            result = await llm.complete_json(system="sys", user="usr")
        assert result is None

    @pytest.mark.asyncio
    async def test_complete_json_returns_none_when_complete_fails(self, llm: LLMService) -> None:
        """Returns None when complete() returns None (API failure)."""
        with patch.object(llm, "complete", return_value=None):
            result = await llm.complete_json(system="sys", user="usr")
        assert result is None


class TestAutoTagger:
    """Tests for the auto-tagger pipeline."""

    @pytest.fixture
    def tagger(self) -> AutoTaggerService:
        return AutoTaggerService()

    @pytest.mark.asyncio
    async def test_fallback_tag_for_cart_key(self, tagger: AutoTaggerService) -> None:
        """Heuristic fallback correctly identifies cart key."""
        result = tagger._fallback_tag("payments:user:1234:cart")
        assert result["owning_service"] == "payments"
        assert "cart" in result["tags"]
        assert result["sla_ms"] > 0

    @pytest.mark.asyncio
    async def test_fallback_tag_for_auth_key(self, tagger: AutoTaggerService) -> None:
        """Heuristic fallback identifies auth/session keys."""
        result = tagger._fallback_tag("auth:session:token:abc")
        assert result["sla_ms"] <= 2000

    @pytest.mark.asyncio
    async def test_auto_tag_uses_fallback_when_llm_fails(
        self, tagger: AutoTaggerService
    ) -> None:
        """Falls back to heuristics when LLM returns None."""
        with (
            patch.object(tagger._llm, "complete_json", return_value=None),
            patch.object(tagger._registry, "put"),
        ):
            result = await tagger.auto_tag(
                AutoTagRequest(key_name="payments:cart", sample_value='{"items": []}')
            )
        assert result.key_name == "payments:cart"
        assert result.owning_service is not None
        assert result.sla_ms > 0

    @pytest.mark.asyncio
    async def test_auto_tag_truncates_sample_value(self, tagger: AutoTaggerService) -> None:
        """Sample value is truncated to max_chars before sending to LLM."""
        long_value = "x" * 1000
        received_user_msgs = []

        async def mock_complete_json(system: str, user: str, **_) -> dict:  # type: ignore[type-arg]
            received_user_msgs.append(user)
            return {"owning_service": "test", "sla_ms": 5000, "tags": [], "confidence": 0.8}

        with (
            patch.object(tagger._llm, "complete_json", side_effect=mock_complete_json),
            patch.object(tagger._registry, "put"),
        ):
            await tagger.auto_tag(AutoTagRequest(key_name="test:key", sample_value=long_value))

        assert received_user_msgs
        max_chars = tagger._settings.auto_tag_value_max_chars
        assert f"{'x' * (max_chars + 1)}" not in received_user_msgs[0]


class TestQuerySanitizer:
    """Tests for NL query safety validator."""

    def test_valid_scan_operation_passes(self) -> None:
        from api.routers.ask import _validate_query
        _validate_query("scan", {"FilterExpression": "staleness_ms > :v"})  # Should not raise

    def test_valid_query_operation_passes(self) -> None:
        from api.routers.ask import _validate_query
        _validate_query("query", {"KeyConditionExpression": "key_name = :k"})

    def test_put_operation_rejected(self) -> None:
        from fastapi import HTTPException
        from api.routers.ask import _validate_query
        with pytest.raises(HTTPException) as exc_info:
            _validate_query("put_item", {})
        assert exc_info.value.status_code == 422

    def test_delete_keyword_in_params_rejected(self) -> None:
        from fastapi import HTTPException
        from api.routers.ask import _validate_query
        with pytest.raises(HTTPException):
            _validate_query("scan", {"FilterExpression": "delete from table"})
