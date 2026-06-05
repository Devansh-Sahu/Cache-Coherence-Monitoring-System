-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Staleness events embeddings table (for RAG anomaly explainer)
CREATE TABLE IF NOT EXISTS staleness_embeddings (
    id          TEXT PRIMARY KEY,
    key_name    TEXT NOT NULL,
    content     TEXT NOT NULL,
    embedding   vector(384),
    metadata    JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Runbook embeddings table (for runbook RAG)
CREATE TABLE IF NOT EXISTS runbook_embeddings (
    id          TEXT PRIMARY KEY,
    runbook_name TEXT NOT NULL,
    chunk_index  INTEGER NOT NULL,
    content     TEXT NOT NULL,
    embedding   vector(384),
    metadata    JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fast cosine similarity search
CREATE INDEX IF NOT EXISTS staleness_emb_idx 
    ON staleness_embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);

CREATE INDEX IF NOT EXISTS runbook_emb_idx 
    ON runbook_embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);
