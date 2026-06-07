"""
AWS Lambda — Staleness Alerter (Phase 6).
Triggered by CloudWatch Events every 60 seconds.

Workflow:
1. Fetch all keys where staleness > SLA * breach_multiplier
2. For each violating key, call Groq LLM for a one-sentence summary
3. Retrieve relevant runbook section from PGVector (via shared DB)
4. Post to Slack: key, staleness, LLM summary, runbook excerpt
5. Graceful degradation: if LLM fails, send raw metrics to Slack

IAM requirements:
  - dynamodb:Scan, dynamodb:Query on StalenessHistory + CacheKeyRegistry
  - secretsmanager:GetSecretValue (for GROQ_API_KEY)
  - cloudwatch:PutMetricData
"""

from __future__ import annotations

import json
import logging
import os
import time
from decimal import Decimal
from typing import Any, Optional

import boto3
from groq import Groq
from slack_sdk.webhook import WebhookClient

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ── Environment ───────────────────────────────────────────────────────────────
REGISTRY_TABLE  = os.environ.get("DYNAMODB_REGISTRY_TABLE", "CacheKeyRegistry")
HISTORY_TABLE   = os.environ.get("DYNAMODB_HISTORY_TABLE", "StalenessHistory")
SLACK_WEBHOOK_URL = os.environ.get(
    "SLACK_WEBHOOK_URL",
    "",  # Set via AWS Lambda env var or .env
)
BREACH_MULTIPLIER = float(os.environ.get("SLA_BREACH_MULTIPLIER", "1.5"))
GROQ_MODEL      = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
MAX_TOKENS      = int(os.environ.get("GROQ_MAX_TOKENS", "300"))
AWS_REGION      = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
USE_LOCALSTACK  = os.environ.get("USE_LOCALSTACK", "false").lower() == "true"
AWS_ENDPOINT    = os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566")
NAMESPACE       = "CacheStalenessMonitor"


def _get_groq_key() -> str:
    """Fetch Groq API key from env or Secrets Manager."""
    key = os.environ.get("GROQ_API_KEY", "")
    if key:
        return key
    # Try Secrets Manager
    try:
        sm = boto3.client("secretsmanager", region_name=AWS_REGION)
        resp = sm.get_secret_value(SecretId="csm/groq-api-key")
        return resp.get("SecretString", "")
    except Exception as exc:
        logger.warning("Could not fetch Groq key from Secrets Manager: %s", exc)
        return ""


def _dynamodb_client() -> Any:
    kwargs: dict[str, Any] = {"region_name": AWS_REGION}
    if USE_LOCALSTACK:
        kwargs["endpoint_url"] = AWS_ENDPOINT
    return boto3.client("dynamodb", **kwargs)


def _decimal_to_num(val: Any) -> Any:
    if isinstance(val, Decimal):
        f = float(val)
        return int(f) if f.is_integer() else f
    return val


def _parse_dynamo_item(item: dict[str, Any]) -> dict[str, Any]:
    """Convert DynamoDB typed values to plain Python."""
    result = {}
    for k, v in item.items():
        if "N" in v:
            result[k] = _decimal_to_num(Decimal(v["N"]))
        elif "S" in v:
            result[k] = v["S"]
        elif "BOOL" in v:
            result[k] = v["BOOL"]
        elif "L" in v:
            result[k] = [_parse_dynamo_item({"_": i})["_"] for i in v["L"]]
        elif "M" in v:
            result[k] = _parse_dynamo_item(v["M"])
        else:
            result[k] = str(v)
    return result


def _get_violating_keys(dynamo: Any) -> list[dict[str, Any]]:
    """Scan StalenessHistory for the latest event per key and filter violations."""
    resp = dynamo.scan(TableName=HISTORY_TABLE)
    items = [_parse_dynamo_item(i) for i in resp.get("Items", [])]

    # Get latest per key
    latest: dict[str, dict[str, Any]] = {}
    for item in items:
        kn = item.get("key_name", "")
        ts = item.get("timestamp", "")
        if kn and (kn not in latest or ts > latest[kn].get("timestamp", "")):
            latest[kn] = item

    # Filter by breach multiplier
    violating = []
    for item in latest.values():
        staleness = float(item.get("staleness_ms", 0))
        threshold = float(item.get("threshold_ms", 1))
        if staleness > threshold * BREACH_MULTIPLIER:
            item["breach_pct"] = round((staleness - threshold) / threshold * 100, 1)
            violating.append(item)
    return violating


def _get_recent_events(dynamo: Any, key_name: str, limit: int = 5) -> list[dict[str, Any]]:
    """Get the 5 most recent events for a key."""
    resp = dynamo.query(
        TableName=HISTORY_TABLE,
        KeyConditionExpression="key_name = :k",
        ExpressionAttributeValues={":k": {"S": key_name}},
        ScanIndexForward=False,
        Limit=limit,
    )
    return [_parse_dynamo_item(i) for i in resp.get("Items", [])]


def _llm_summarize(
    client: Groq,
    item: dict[str, Any],
    recent_events: list[dict[str, Any]],
    system_prompt: str,
) -> Optional[str]:
    """Call Groq LLM for a one-sentence alert summary."""
    user_msg = (
        f"The following cache key is violating SLA:\n"
        f"Key: {item['key_name']} | Service: {item.get('owning_service', 'unknown')}\n"
        f"Current staleness: {item['staleness_ms']}ms | SLA: {item['threshold_ms']}ms "
        f"({item.get('breach_pct', 0)}% over)\n"
        f"Last {len(recent_events)} events: "
        f"{json.dumps([{k: v for k, v in e.items() if k in ['staleness_ms', 'timestamp']} for e in recent_events])}\n\n"
        "Write ONE sentence: root cause hypothesis + suggested fix."
    )
    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            max_tokens=MAX_TOKENS,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.3,
        )
        return response.choices[0].message.content if response.choices else None
    except Exception as exc:
        logger.warning("LLM call failed: %s", exc)
        return None


def _post_slack(
    webhook_url: str,
    item: dict[str, Any],
    summary: Optional[str],
    runbook_excerpt: Optional[str],
) -> bool:
    """Post rich Slack alert block. Returns True on success."""
    key_name    = item["key_name"]
    service     = item.get("owning_service", "unknown")
    staleness_ms = item.get("staleness_ms", 0)
    threshold_ms = item.get("threshold_ms", 0)
    breach_pct   = item.get("breach_pct", 0)

    status_emoji = "🔴" if breach_pct > 100 else "🟡"
    llm_text     = summary or f"Staleness: {staleness_ms}ms exceeds SLA of {threshold_ms}ms."
    runbook_text = f"\n📖 *Runbook:* {runbook_excerpt[:300]}..." if runbook_excerpt else ""

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{status_emoji} Cache SLA Breach Alert"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Key:*\n`{key_name}`"},
                {"type": "mrkdwn", "text": f"*Service:*\n{service}"},
                {"type": "mrkdwn", "text": f"*Staleness:*\n{staleness_ms}ms"},
                {"type": "mrkdwn", "text": f"*SLA:*\n{threshold_ms}ms ({breach_pct}% over)"},
            ],
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*🤖 Groq Analysis:* {llm_text}{runbook_text}",
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Generated by Groq `{GROQ_MODEL}` | {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
                }
            ],
        },
    ]

    try:
        slack = WebhookClient(webhook_url)
        resp = slack.send(blocks=blocks)
        success = resp.status_code == 200
        if not success:
            logger.error("Slack returned non-200: %s %s", resp.status_code, resp.body)
        return success
    except Exception as exc:
        logger.error("Slack post failed: %s", exc)
        return False


def _put_cloudwatch_metric(
    cloudwatch: Any, key_name: str, staleness_ms: float, breach_pct: float
) -> None:
    """Emit custom metrics to CloudWatch."""
    try:
        cloudwatch.put_metric_data(
            Namespace=NAMESPACE,
            MetricData=[
                {
                    "MetricName": "StalenessMs",
                    "Dimensions": [{"Name": "KeyName", "Value": key_name}],
                    "Value": staleness_ms,
                    "Unit": "Milliseconds",
                },
                {
                    "MetricName": "SLABreachPercent",
                    "Dimensions": [{"Name": "KeyName", "Value": key_name}],
                    "Value": breach_pct,
                    "Unit": "Percent",
                },
            ],
        )
    except Exception as exc:
        logger.warning("CloudWatch metric push failed: %s", exc)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler — entry point."""
    logger.info("Staleness alerter invoked")

    # Load prompt
    system_prompt = "You are a Redis SRE on-call assistant. Respond in one concise sentence."
    try:
        with open("prompts/alert_summary.txt", encoding="utf-8") as f:
            system_prompt = f.read().strip()
    except FileNotFoundError:
        pass

    groq_key = _get_groq_key()
    groq_client: Optional[Groq] = None
    if groq_key:
        groq_client = Groq(api_key=groq_key)

    dynamo = _dynamodb_client()
    cw_kwargs: dict[str, Any] = {"region_name": AWS_REGION}
    if USE_LOCALSTACK:
        cw_kwargs["endpoint_url"] = AWS_ENDPOINT
    cloudwatch = boto3.client("cloudwatch", **cw_kwargs)

    violating = _get_violating_keys(dynamo)
    logger.info("Found %d violating keys", len(violating))

    alerts_sent = 0
    for item in violating:
        key_name = item["key_name"]
        recent   = _get_recent_events(dynamo, key_name)

        summary = None
        if groq_client:
            summary = _llm_summarize(groq_client, item, recent, system_prompt)

        # Slack alert
        if SLACK_WEBHOOK_URL:
            delivered = _post_slack(SLACK_WEBHOOK_URL, item, summary, runbook_excerpt=None)
            if delivered:
                alerts_sent += 1

        # CloudWatch metrics
        _put_cloudwatch_metric(
            cloudwatch,
            key_name,
            float(item.get("staleness_ms", 0)),
            float(item.get("breach_pct", 0)),
        )

    return {
        "statusCode": 200,
        "body": json.dumps(
            {"violating_keys": len(violating), "alerts_sent": alerts_sent}
        ),
    }
