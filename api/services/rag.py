"""
PGVector RAG service.
- Embeds staleness events and runbook chunks into PostgreSQL via pgvector
- Retrieves top-K similar events/runbooks for LLM context
- Uses sentence-transformers all-MiniLM-L6-v2 (384 dims, runs locally, no API cost)
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional
from uuid import uuid4

import structlog
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from api.config import get_settings

logger = structlog.get_logger(__name__)

# Model name for embeddings (384-dim, fast, good quality)
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# Lazy-loaded encoder (imported only when first used to avoid slow startup)
_encoder: Any = None


def _get_encoder() -> Any:
    global _encoder
    if _encoder is None:
        from sentence_transformers import SentenceTransformer  # type: ignore[import]
        _encoder = SentenceTransformer(EMBEDDING_MODEL)
        logger.info("Loaded sentence transformer", model=EMBEDDING_MODEL)
    return _encoder


def _embed(text_: str) -> list[float]:
    enc = _get_encoder()
    return enc.encode(text_, normalize_embeddings=True).tolist()


def _to_pgvector(embedding: list[float]) -> str:
    """Format a float list as a pgvector-compatible literal '[x,y,z,...]'."""
    return '[' + ','.join(f'{v:.8f}' for v in embedding) + ']'


def _get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True)


# ─────────────────────────────────────────────────────────────────────────────
# Staleness Event Embeddings
# ─────────────────────────────────────────────────────────────────────────────


class StalenessRAGService:
    """Embed SLA breach events and retrieve similar past incidents."""

    def __init__(self) -> None:
        self._engine = _get_engine()

    def embed_event(self, event: dict[str, Any]) -> str:
        """
        Convert a staleness event to natural language, embed it, and store in PGVector.
        Returns the doc ID.
        """
        key_name = event.get("key_name", "unknown")
        staleness_ms = event.get("staleness_ms", 0)
        threshold_ms = event.get("threshold_ms", 1)
        service = event.get("owning_service", "unknown")
        timestamp = event.get("timestamp", datetime.utcnow().isoformat())

        pct = 0.0
        if threshold_ms > 0:
            pct = round((staleness_ms - threshold_ms) / threshold_ms * 100, 1)

        doc = (
            f"Cache key '{key_name}' owned by service '{service}' breached its SLA "
            f"by {pct}% at {timestamp}. "
            f"Staleness: {staleness_ms}ms, SLA threshold: {threshold_ms}ms."
        )

        doc_id = str(uuid4())
        embedding = _embed(doc)
        meta = json.dumps(
            {"key_name": key_name, "owning_service": service, "timestamp": str(timestamp)}
        )

        with self._engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO staleness_embeddings (id, key_name, content, embedding, metadata)
                    VALUES (:id, :key_name, :content, :embedding::vector, :metadata::jsonb)
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {
                    "id": doc_id,
                    "key_name": key_name,
                    "content": doc,
                    "embedding": _to_pgvector(embedding),
                    "metadata": meta,
                },
            )
        logger.info("Embedded staleness event", doc_id=doc_id, key_name=key_name)
        return doc_id

    def retrieve_similar(
        self, current_event: dict[str, Any], top_k: int = 5
    ) -> list[dict[str, Any]]:
        """Retrieve the top-K most similar past breach events."""
        key_name = current_event.get("key_name", "unknown")
        staleness_ms = current_event.get("staleness_ms", 0)
        threshold_ms = current_event.get("threshold_ms", 1)
        service = current_event.get("owning_service", "unknown")
        pct = 0.0
        if threshold_ms > 0:
            pct = round((staleness_ms - threshold_ms) / threshold_ms * 100, 1)

        query_text = (
            f"Cache key '{key_name}' service '{service}' SLA breach {pct}% "
            f"staleness {staleness_ms}ms threshold {threshold_ms}ms"
        )
        embedding = _embed(query_text)

        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, key_name, content, metadata,
                           1 - (embedding <=> :embedding::vector) AS similarity
                    FROM staleness_embeddings
                    WHERE key_name != :exclude_key
                    ORDER BY embedding <=> :embedding::vector
                    LIMIT :top_k
                    """
                ),
                {
                    "embedding": _to_pgvector(embedding),
                    "exclude_key": key_name,
                    "top_k": top_k,
                },
            ).fetchall()

        results = []
        for row in rows:
            meta = json.loads(row.metadata) if row.metadata else {}
            results.append(
                {
                    "id": row.id,
                    "key_name": row.key_name,
                    "content": row.content,
                    "similarity": float(row.similarity),
                    **meta,
                }
            )
        return results


# ─────────────────────────────────────────────────────────────────────────────
# Runbook RAG Service
# ─────────────────────────────────────────────────────────────────────────────


class RunbookRAGService:
    """Embed runbook chunks and retrieve relevant sections for alerts."""

    CHUNK_SIZE = 500  # characters per chunk

    def __init__(self) -> None:
        self._engine = _get_engine()

    def ingest_runbook(self, runbook_name: str, content: str) -> int:
        """Chunk and embed a runbook. Returns number of chunks stored."""
        chunks = [
            content[i: i + self.CHUNK_SIZE]
            for i in range(0, len(content), self.CHUNK_SIZE)
        ]
        count = 0
        with self._engine.begin() as conn:
            for idx, chunk in enumerate(chunks):
                if not chunk.strip():
                    continue
                embedding = _embed(chunk)
                meta = json.dumps(
                    {"runbook_name": runbook_name, "chunk_index": idx}
                )
                conn.execute(
                    text(
                        """
                        INSERT INTO runbook_embeddings
                            (id, runbook_name, chunk_index, content, embedding, metadata)
                        VALUES (:id, :name, :idx, :content, :emb::vector, :meta::jsonb)
                        ON CONFLICT (id) DO NOTHING
                        """
                    ),
                    {
                        "id": f"{runbook_name}::{idx}",
                        "name": runbook_name,
                        "idx": idx,
                        "content": chunk,
                        "emb": _to_pgvector(embedding),
                        "meta": meta,
                    },
                )
                count += 1
        logger.info("Ingested runbook", runbook=runbook_name, chunks=count)
        return count

    def retrieve_relevant(
        self, incident_text: str, top_k: int = 3
    ) -> list[dict[str, Any]]:
        """Retrieve the most relevant runbook chunks for an incident description."""
        embedding = _embed(incident_text)
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, runbook_name, chunk_index, content, metadata,
                           1 - (embedding <=> :embedding::vector) AS similarity
                    FROM runbook_embeddings
                    ORDER BY embedding <=> :embedding::vector
                    LIMIT :top_k
                    """
                ),
                {"embedding": _to_pgvector(embedding), "top_k": top_k},
            ).fetchall()

        return [
            {
                "id": row.id,
                "runbook_name": row.runbook_name,
                "chunk_index": row.chunk_index,
                "content": row.content,
                "similarity": float(row.similarity),
            }
            for row in rows
        ]
