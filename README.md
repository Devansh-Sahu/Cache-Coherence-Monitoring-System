# Redis Cache Staleness Monitor

> Production-grade Redis cache staleness monitoring with **RAG + LLM** integrations, FastAPI backend, React dashboard, and AWS infrastructure.

![Stack](https://img.shields.io/badge/Stack-FastAPI%20%7C%20Redis%20%7C%20DynamoDB%20%7C%20PGVector%20%7C%20Claude%20AI-6366f1)

---

## Architecture

```
Redis Cluster
     │
     ▼
FastAPI Backend ──► DynamoDB (metrics)
     │                    │
     │              PGVector (embeddings for RAG)
     │
     ├──► AWS Lambda (staleness checker, runs every 60s)
     │         ├──► CloudWatch Metrics
     │         ├──► Slack (LLM summary)
     │         └──► SQS (alert queue)
     │
     ├──► POST /api/ask  (NL query → DynamoDB → plain English)
     │
     └──► React Dashboard (staleness table, sparklines, charts)
```

## Quick Start

### 1. Prerequisites
- Docker Desktop
- Python 3.11+ with [Poetry](https://python-poetry.org/)
- Node.js 18+

### 2. Environment setup
```bash
cp .env.example .env
# Edit .env: fill in ANTHROPIC_API_KEY, SLACK_WEBHOOK_URL
```

### 3. Start services
```bash
make docker-up          # Redis, PostgreSQL (pgvector), LocalStack
```

### 4. Install Python deps
```bash
poetry install
```

### 5. Bootstrap AWS (LocalStack) resources
```bash
make setup-aws-local    # Creates DynamoDB tables, S3, SQS, Secrets Manager
```

### 6. Seed data
```bash
make seed               # 10 registry entries + 10 Redis keys at various staleness levels
```

### 7. Ingest runbooks into PGVector
```bash
poetry run python -m rag.runbook_ingestor
```

### 8. Run the API
```bash
make dev                # FastAPI on http://localhost:8000
```

### 9. Run the staleness worker
```bash
make worker             # Polls Redis every 30s, posts events to API
```

### 10. Run the dashboard
```bash
make dashboard          # Vite + React on http://localhost:5173
```

---

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/registry/` | List all registered cache keys |
| `POST` | `/api/registry/` | Register a new key |
| `PATCH` | `/api/registry/{key}` | Update a key's metadata |
| `POST` | `/api/registry/auto-tag` | LLM auto-tag a new key |
| `POST` | `/api/events/` | Ingest a staleness reading |
| `GET` | `/api/events/{key}?hours=24` | Get staleness history |
| `GET` | `/api/events/{key}/explain` | RAG + LLM breach explanation |
| `GET` | `/api/dashboard/` | Aggregate stats for all keys |
| `POST` | `/api/ask/` | Natural language query |

Full docs at `http://localhost:8000/docs`

---

## Features

### 🤖 LLM Auto-Tagger (Phase 5)
When a new Redis key appears, Claude Haiku infers:
- `owning_service` (which team owns it)
- `sla_ms` (appropriate freshness SLA)
- `tags` (descriptive labels)

### 🚨 Lambda Alerter with LLM (Phase 6)
Runs every 60s via CloudWatch Events:
- Detects keys where `staleness > SLA × 1.5`
- Asks Claude for a one-sentence root cause + fix
- Posts rich Slack block message with runbook excerpt

### 🔍 RAG Anomaly Explainer (Phase 7)
- Every SLA breach is embedded into PGVector
- On new breach, retrieves 5 most similar historical incidents
- Claude explains current breach using past patterns + relevant runbook

### 💬 Natural Language Query (Phase 8)
```json
POST /api/ask
{ "question": "Which service had worst staleness last week?" }
```
→ Claude generates a DynamoDB query → executes it → returns plain English answer

### 📚 Runbook RAG (Phase 9)
5 expert runbooks pre-ingested into PGVector:
- Redis connection pool exhausted
- Invalidation handler timeout
- TTL misconfiguration
- High write latency
- Cache stampede

---

## Shadow Key Convention

Cache producers should write to shadow keys when updating cache:

```python
import time
import redis

r = redis.Redis()

def write_to_cache(key: str, value: str, ttl_s: int) -> None:
    r.setex(key, ttl_s, value)
    # Shadow key stores last-write Unix timestamp
    r.setex(f"__meta:{key}:last_write", ttl_s + 300, str(time.time()))
```

Without shadow keys, the worker falls back to TTL-based staleness estimation.

---

## Development

```bash
make test           # Run pytest suite (mocked AWS, no tokens burned)
make lint           # ruff + mypy
make format         # Auto-format
make deploy-lambda  # Package + deploy to LocalStack
make infra-apply    # Terraform apply (LocalStack)
```

---

## Project Structure

```
cache-staleness-monitor/
├── api/                    # FastAPI app
│   ├── main.py
│   ├── models.py           # Pydantic models
│   ├── config.py           # Settings (pydantic-settings)
│   ├── routers/            # registry, events, dashboard, ask
│   └── services/           # dynamodb, llm, rag, auto_tagger
├── workers/
│   └── staleness_checker.py
├── lambdas/
│   └── staleness_alerter.py
├── rag/
│   └── runbook_ingestor.py
├── runbooks/               # 5 Markdown runbooks
├── prompts/                # Versioned LLM prompts
├── scripts/                # Seed + setup scripts
├── dashboard/              # Vite + React frontend
├── infra/                  # Terraform (DynamoDB, Lambda, S3, SQS)
├── tests/
│   ├── unit/
│   └── integration/
├── docker-compose.yml
├── Makefile
└── pyproject.toml
```
