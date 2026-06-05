"""
FastAPI application entrypoint.
- Mounts all routers
- Bootstraps DynamoDB tables on startup
- Configures CORS for React dashboard
- Configures structured logging
"""

from __future__ import annotations

import structlog
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.config import get_settings
from api.routers import ask, dashboard, events, registry
from api.services.dynamodb import ensure_tables_exist

# Configure structlog
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ]
)

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: setup on startup, teardown on shutdown."""
    settings = get_settings()
    logger.info(
        "Starting Cache Staleness Monitor",
        env=settings.app_env,
        localstack=settings.use_localstack,
    )
    try:
        ensure_tables_exist()
        logger.info("DynamoDB tables ready")
    except Exception as exc:
        logger.warning(
            "Could not verify DynamoDB tables (continuing anyway)",
            error=str(exc),
        )
    yield
    logger.info("Cache Staleness Monitor shutting down")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Redis Cache Staleness Monitor",
        description=(
            "Production-grade Redis cache staleness monitoring with RAG + LLM integrations. "
            "Monitors key freshness, auto-tags new keys, and answers natural language questions."
        ),
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # CORS — allow React dev server
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",  # Vite
            "http://localhost:3000",  # CRA fallback
            "http://localhost:8080",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount routers
    app.include_router(registry.router)
    app.include_router(events.router)
    app.include_router(dashboard.router)
    app.include_router(ask.router)

    @app.get("/health", tags=["health"])
    async def health_check() -> dict[str, str]:
        return {"status": "ok", "env": settings.app_env}

    return app


app = create_app()
