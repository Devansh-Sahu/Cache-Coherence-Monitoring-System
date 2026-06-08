# Redis Cache Staleness Monitor (CCMS)

> Production-grade Redis cache staleness monitoring with **RAG + LLM** integrations, FastAPI backend, React dashboard, and full AWS infrastructure.

![Tests](https://img.shields.io/badge/Tests-24%2F24%20passing-22c55e)
![Stack](https://img.shields.io/badge/Stack-FastAPI%20%7C%20Redis%20%7C%20DynamoDB%20%7C%20PGVector%20%7C%20Groq%20AI-6366f1)
![LLM](https://img.shields.io/badge/LLM-Groq%20llama--3.3--70b-f97316)

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
     ├──► AWS Lambda (staleness alerter, runs every 60s)
     │         ├──► CloudWatch Metrics
     │         ├──► Slack (Groq LLM summary alert)
     │         └──► SQS (alert queue)
     │
     ├──► POST /api/ask  (NL query → DynamoDB → plain English)
     │
     └──► React Dashboard (staleness table, sparklines, charts)
```

---

## Quick Start (No AWS account needed)

### Option A — LocalStack (Docker, recommended for dev)

**Prerequisites:** Docker Desktop, Python 3.10+, Node.js 18+

```powershell
# 1. Clone and set up environment
cp .env.example .env
# Edit .env: fill in GROQ_API_KEY and SLACK_WEBHOOK_URL

# 2. Start all services (Redis, PostgreSQL+pgvector, LocalStack)
.\make.ps1 up

# 3. Bootstrap LocalStack AWS resources (DynamoDB, S3, SQS, Secrets Manager)
.\make.ps1 localstack

# 4. Seed sample data
.\make.ps1 seed

# 5. Start everything (3 terminals)
.\make.ps1 dev-api      # Terminal 1 → http://localhost:8000
.\make.ps1 dev-worker   # Terminal 2
.\make.ps1 dev-ui       # Terminal 3 → http://localhost:5173
```

### Option B — Real AWS (Production)

**Prerequisites:** AWS account + credentials in `.env`

```powershell
# Set in .env:
# AWS_ACCESS_KEY_ID=...
# AWS_SECRET_ACCESS_KEY=...
# USE_LOCALSTACK=false

# Creates ALL AWS resources automatically (no Terraform CLI needed):
python scripts/aws_setup.py

# Then start the local services (Redis + PGVector still run locally):
.\make.ps1 up
.\make.ps1 dev-api
.\make.ps1 dev-ui
```

**What `aws_setup.py` creates:**
- ✅ `CacheKeyRegistry` DynamoDB table (GSI on owning_service)
- ✅ `StalenessHistory` DynamoDB table (sort key, TTL, GSI)
- ✅ `csm-runbooks` S3 bucket (private)
- ✅ `csm-alerts` SQS queue + dead-letter queue
- ✅ `csm/groq-api-key` Secrets Manager secret
- ✅ `csm-lambda-role` IAM role + policy
- ✅ `csm-staleness-alerter` Lambda function (Python 3.11)
- ✅ CloudWatch EventBridge rule (fires every 1 minute)
- ✅ CloudWatch dashboard `CacheStalenessMonitor`

---

## Environment Variables

Copy `.env.example` → `.env` and fill in:

```env
# Required
GROQ_API_KEY=gsk_...              # Get free at console.groq.com
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...

# AWS (real) — leave defaults for LocalStack
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
USE_LOCALSTACK=true               # false for real AWS

# Already filled with sensible defaults
REDIS_URL=redis://localhost:6379/0
DATABASE_URL=postgresql://csm_user:csm_password@localhost:5432/csm_db
```

---

## All Commands

```powershell
.\make.ps1 up           # Start Docker (Redis, Postgres, LocalStack)
.\make.ps1 down         # Stop Docker
.\make.ps1 localstack   # Bootstrap LocalStack AWS resources
.\make.ps1 aws-setup    # Create real AWS resources
.\make.ps1 dev-api      # FastAPI backend  → http://localhost:8000
.\make.ps1 dev-worker   # Staleness checker worker
.\make.ps1 dev-ui       # React dashboard  → http://localhost:5173
.\make.ps1 test         # Run 24 tests (no Docker needed)
.\make.ps1 smoke        # Live Groq + Slack smoke test
.\make.ps1 seed         # Seed sample Redis + registry data
.\make.ps1 lint         # ruff linter
.\make.ps1 status       # Show service health
```

---

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/health` | Health check |
| `GET`  | `/api/registry/` | List all registered cache keys |
| `POST` | `/api/registry/` | Register a new key |
| `PATCH`| `/api/registry/{key}` | Update key metadata |
| `POST` | `/api/registry/auto-tag` | Groq LLM auto-tag a new key |
| `POST` | `/api/events/` | Ingest a staleness reading |
| `GET`  | `/api/events/{key}?hours=24` | Get staleness history |
| `GET`  | `/api/events/{key}/explain` | RAG + LLM breach explanation |
| `GET`  | `/api/dashboard/summary` | Aggregate stats for all keys |
| `POST` | `/api/ask/` | Natural language query |

Full interactive docs: `http://localhost:8000/docs`

---

## Features

### 🤖 LLM Auto-Tagger
When a new Redis key appears, Groq (llama-3.3-70b) infers:
- `owning_service` — which team owns it
- `sla_ms` — appropriate freshness SLA
- `tags` — descriptive labels

### 🚨 Lambda Alerter with LLM
Runs every 60s via CloudWatch Events:
- Detects keys where `staleness > SLA × 1.5`
- Groq generates a one-sentence root cause + fix
- Posts rich Slack block with runbook excerpt

### 🔍 RAG Anomaly Explainer
- Every SLA breach is embedded into PGVector
- On new breach, retrieves 5 most similar historical incidents
- LLM explains current breach using past patterns + relevant runbook

### 💬 Natural Language Query
```json
POST /api/ask
{ "question": "Which service had worst staleness last week?" }
```
→ Groq generates a DynamoDB query → executes it → returns plain English answer

### 📚 Runbook RAG
5 expert runbooks pre-ingested into PGVector:
- Redis connection pool exhausted
- Invalidation handler timeout
- TTL misconfiguration
- High write latency
- Cache stampede

---

## Shadow Key Convention

Cache producers should write shadow keys when updating the cache:

```python
import time, redis
r = redis.Redis()

def write_to_cache(key: str, value: str, ttl_s: int) -> None:
    r.setex(key, ttl_s, value)
    # Shadow key stores last-write Unix timestamp
    r.setex(f"__meta:{key}:last_write", ttl_s + 300, str(time.time()))
```

Without shadow keys, the worker falls back to TTL-based staleness estimation.

---

## Project Structure

```
CCMS/
├── api/                     # FastAPI application
│   ├── main.py              # App entry point + CORS
│   ├── models.py            # Pydantic schemas
│   ├── config.py            # Settings (GROQ_API_KEY, SLACK, AWS...)
│   └── routers/             # registry, events, dashboard, ask
│   └── services/            # dynamodb, llm (Groq), rag, auto_tagger
├── workers/
│   └── staleness_checker.py # Polls Redis every 30s
├── lambdas/
│   └── staleness_alerter.py # AWS Lambda (Groq + Slack alerts)
├── rag/
│   └── runbook_ingestor.py  # Embeds runbooks into PGVector
├── runbooks/                # 5 Markdown SRE runbooks
├── prompts/                 # Versioned LLM prompt files
├── scripts/
│   ├── aws_setup.py         # One-command real AWS setup
│   ├── setup_localstack.py  # LocalStack bootstrap
│   ├── smoke_test_live.py   # Live Groq + Slack test
│   ├── seed_redis_keys.py   # Sample Redis data
│   └── seed_registry.py     # Sample DynamoDB registry data
├── dashboard/               # Vite + React frontend
├── infra/                   # Terraform (alternative to aws_setup.py)
│   ├── main.tf
│   ├── provider.tf
│   └── variables.tf
├── tests/
│   ├── unit/                # 20 unit tests
│   └── integration/         # 6 integration tests (moto mock AWS)
├── docker-compose.yml       # Redis + Postgres + LocalStack
├── make.ps1                 # Windows command runner
├── .env.example             # Environment template
└── pyproject.toml           # Python deps (groq, fastapi, moto...)
```

---

## Test Results

```
24 passed, 8 warnings in 13.42s
```

| Suite | Tests | Status |
|-------|-------|--------|
| Integration (DynamoDB CRUD) | 6 | ✅ All pass |
| Unit (LLM service + Groq) | 4 | ✅ All pass |
| Unit (Auto-tagger) | 4 | ✅ All pass |
| Unit (Query sanitizer) | 4 | ✅ All pass |
| Unit (Prompt loader) | 2 | ✅ All pass |
| Unit (Staleness calculation) | 4 | ✅ All pass |

Run tests (no Docker needed):
```powershell
.\make.ps1 test
```
