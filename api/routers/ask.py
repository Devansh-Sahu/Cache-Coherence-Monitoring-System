"""
Natural Language Query router — Phase 8.
POST /api/ask: NL question → Claude generates DynamoDB query → execute → Claude summarizes.
Safety: validates LLM-generated query before execution (read-only operations only).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, status

from api.models import AskRequest, AskResponse
from api.services.dynamodb import StalenessHistoryService, _get_dynamodb_resource
from api.services.llm import LLMService, load_prompt
from api.config import get_settings

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/ask", tags=["ask"])

_llm = LLMService()
_history = StalenessHistoryService()

# Only these DynamoDB operations are allowed from LLM-generated queries
ALLOWED_OPERATIONS = {"scan", "query", "get_item", "batch_get_item"}


def _validate_query(operation: str, params: dict[str, Any]) -> None:
    """Raise if the LLM-generated query attempts a write operation."""
    if operation.lower().strip() not in ALLOWED_OPERATIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"LLM generated disallowed operation '{operation}'. Only reads are permitted.",
        )
    # Block any expression that looks like a write
    expr = str(params).lower()
    write_keywords = ["put", "delete", "update", "create", "drop", "insert"]
    for kw in write_keywords:
        if kw in expr:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Potential write keyword '{kw}' detected in LLM query. Rejected.",
            )


async def _execute_dynamodb_query(
    operation: str, params: dict[str, Any]
) -> list[dict[str, Any]]:
    """Execute a validated DynamoDB read operation and return items."""
    settings = get_settings()
    dynamodb = _get_dynamodb_resource()
    table = dynamodb.Table(settings.dynamodb_history_table)

    op = operation.lower().strip()
    try:
        if op == "scan":
            resp = table.scan(**{k: v for k, v in params.items() if k != "TableName"})
            return resp.get("Items", [])
        elif op == "query":
            resp = table.query(**{k: v for k, v in params.items() if k != "TableName"})
            return resp.get("Items", [])
        elif op == "get_item":
            resp = table.get_item(**{k: v for k, v in params.items() if k != "TableName"})
            item = resp.get("Item")
            return [item] if item else []
        else:
            return []
    except Exception as exc:
        logger.error("DynamoDB query execution failed", operation=op, error=str(exc))
        return []


@router.post("/", response_model=AskResponse)
async def ask_question(body: AskRequest) -> dict[str, Any]:
    """
    Phase 8 — Natural Language Query.
    1. Claude generates DynamoDB query from NL question
    2. Validate (read-only)
    3. Execute against DynamoDB
    4. Claude summarizes results in plain English
    """
    settings = get_settings()

    # Step 1: NL → DynamoDB query generation
    system_prompt = load_prompt("nl_query")
    query_user_msg = (
        f"DynamoDB table name: {settings.dynamodb_history_table}\n\n"
        f"User question: {body.question}\n\n"
        "Return JSON with fields: {\"operation\": \"scan|query|get_item\", \"params\": {...}}"
    )

    query_json = await _llm.complete_json(system=system_prompt, user=query_user_msg)

    results: list[dict[str, Any]] = []
    raw_query: dict[str, Any] | None = None

    if query_json:
        operation = query_json.get("operation", "scan")
        params = query_json.get("params", {})
        raw_query = query_json

        try:
            _validate_query(operation, params)
            results = await _execute_dynamodb_query(operation, params)
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("Query execution failed, using fallback", error=str(exc))
            results = _history.get_all_latest()
    else:
        # LLM failed to generate query — use all latest as context
        logger.warning("LLM failed to generate query, using all latest events")
        results = _history.get_all_latest()

    # Step 2: Summarize results in plain English
    summary_system = (
        "You are a Redis cache analytics assistant. "
        "Summarize the following DynamoDB query results in plain English. "
        "Be concise and highlight key insights. Max 150 words."
    )
    results_text = json.dumps(results[:20], default=str)  # Cap at 20 items
    summary_user = (
        f"Question: {body.question}\n\n"
        f"Query results ({len(results)} items):\n{results_text}\n\n"
        "Provide a plain-English answer to the question based on this data."
    )

    answer = await _llm.complete(system=summary_system, user=summary_user)
    if not answer:
        if results:
            answer = (
                f"Found {len(results)} records. "
                f"Unable to generate a natural language summary (LLM unavailable). "
                f"First result: {json.dumps(results[0], default=str)}"
            )
        else:
            answer = "No data found matching your query."

    return {
        "question": body.question,
        "answer": answer,
        "raw_query": raw_query,
        "raw_results_count": len(results),
        "generated_at": datetime.utcnow().isoformat(),
    }
