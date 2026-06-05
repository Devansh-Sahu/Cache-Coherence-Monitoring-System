"""
Runbook RAG Ingestor — Phase 9.
Loads Markdown runbooks from local disk or S3 and embeds them into PGVector.
Run once at startup or via `make seed` to populate the runbook index.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import boto3
import structlog

sys.path.insert(0, str(Path(__file__).parents[1]))

from api.config import get_settings
from api.services.rag import RunbookRAGService

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ]
)
logger = structlog.get_logger(__name__)

LOCAL_RUNBOOKS_DIR = Path(__file__).parents[1] / "runbooks"


def _load_local_runbooks() -> dict[str, str]:
    """Load all .md files from local runbooks/ directory."""
    runbooks: dict[str, str] = {}
    if not LOCAL_RUNBOOKS_DIR.exists():
        logger.warning("Local runbooks directory not found", path=str(LOCAL_RUNBOOKS_DIR))
        return runbooks

    for path in LOCAL_RUNBOOKS_DIR.glob("*.md"):
        content = path.read_text(encoding="utf-8")
        runbooks[path.stem] = content
        logger.info("Loaded runbook", name=path.stem, size_bytes=len(content))
    return runbooks


def _load_s3_runbooks() -> dict[str, str]:
    """Load .md files from S3 bucket (production path)."""
    settings = get_settings()
    runbooks: dict[str, str] = {}

    kwargs: dict[str, str] = {"region_name": settings.aws_default_region}
    if settings.use_localstack:
        kwargs["endpoint_url"] = settings.aws_endpoint_url

    try:
        s3 = boto3.client("s3", **kwargs)
        resp = s3.list_objects_v2(Bucket=settings.s3_runbooks_bucket, Prefix="runbooks/")
        for obj in resp.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".md"):
                name = Path(key).stem
                content = s3.get_object(Bucket=settings.s3_runbooks_bucket, Key=key)[
                    "Body"
                ].read().decode("utf-8")
                runbooks[name] = content
                logger.info("Loaded runbook from S3", name=name)
    except Exception as exc:
        logger.warning("S3 runbook load failed", error=str(exc))
    return runbooks


def ingest_all(use_s3: bool = False) -> None:
    """Ingest all runbooks into PGVector."""
    svc = RunbookRAGService()

    # Load from S3 first (prod), fall back to local
    runbooks = _load_s3_runbooks() if use_s3 else {}
    if not runbooks:
        runbooks = _load_local_runbooks()

    if not runbooks:
        logger.error("No runbooks found to ingest")
        return

    total_chunks = 0
    for name, content in runbooks.items():
        chunks = svc.ingest_runbook(name, content)
        total_chunks += chunks

    logger.info("Runbook ingestion complete", runbooks=len(runbooks), total_chunks=total_chunks)


def upload_to_s3() -> None:
    """Upload local runbooks to S3 bucket."""
    settings = get_settings()
    kwargs: dict[str, str] = {"region_name": settings.aws_default_region}
    if settings.use_localstack:
        kwargs["endpoint_url"] = settings.aws_endpoint_url

    s3 = boto3.client("s3", **kwargs)

    for path in LOCAL_RUNBOOKS_DIR.glob("*.md"):
        key = f"runbooks/{path.name}"
        s3.put_object(
            Bucket=settings.s3_runbooks_bucket,
            Key=key,
            Body=path.read_bytes(),
            ContentType="text/markdown",
        )
        logger.info("Uploaded runbook to S3", bucket=settings.s3_runbooks_bucket, key=key)


if __name__ == "__main__":
    use_s3 = "--s3" in sys.argv
    if "--upload" in sys.argv:
        upload_to_s3()
    ingest_all(use_s3=use_s3)
