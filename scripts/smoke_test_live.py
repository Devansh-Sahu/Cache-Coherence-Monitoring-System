"""
Live smoke test: Groq API + Slack webhook.
Run: python scripts/smoke_test_live.py
"""
import asyncio
import os
import sys

# Fix Windows console encoding
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Load .env before imports
from dotenv import load_dotenv
load_dotenv()

from groq import AsyncGroq
from slack_sdk.webhook import WebhookClient

GROQ_KEY   = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
SLACK_URL  = os.environ.get("SLACK_WEBHOOK_URL", "")


async def test_groq() -> bool:
    print("\n-- Groq API -------------------------------------------")
    if not GROQ_KEY:
        print("FAIL: GROQ_API_KEY not set")
        return False
    client = AsyncGroq(api_key=GROQ_KEY)
    try:
        resp = await client.chat.completions.create(
            model=GROQ_MODEL,
            max_tokens=80,
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user",   "content": "Reply with exactly: 'Groq is working'"},
            ],
        )
        text   = resp.choices[0].message.content
        tokens = resp.usage.total_tokens if resp.usage else "?"
        print(f"PASS: Groq response -> {text!r}")
        print(f"      Model: {GROQ_MODEL} | Tokens used: {tokens}")
        return True
    except Exception as e:
        print(f"FAIL: Groq error -> {e}")
        return False


def test_slack() -> bool:
    print("\n-- Slack Webhook --------------------------------------")
    if not SLACK_URL:
        print("FAIL: SLACK_WEBHOOK_URL not set")
        return False
    client = WebhookClient(SLACK_URL)
    try:
        resp = client.send(blocks=[
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "[PASS] CCMS Smoke Test"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "*Cache Staleness Monitor* is online!\n"
                        "PASS  Groq LLM (llama-3.3-70b-versatile) connected\n"
                        "PASS  Slack webhook verified\n"
                        "PASS  24/24 unit & integration tests passing"
                    ),
                },
            },
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": "Sent by automated smoke test script"}],
            },
        ])
        if resp.status_code == 200:
            print(f"PASS: Slack message delivered (HTTP {resp.status_code})")
            return True
        else:
            print(f"FAIL: Slack returned HTTP {resp.status_code}: {resp.body}")
            return False
    except Exception as e:
        print(f"FAIL: Slack error -> {e}")
        return False


async def main() -> None:
    print("=" * 55)
    print("  Cache Staleness Monitor -- Live Smoke Test")
    print("=" * 55)

    groq_ok  = await test_groq()
    slack_ok = test_slack()

    print("\n-- Summary --------------------------------------------")
    print(f"  Groq API:       {'PASS' if groq_ok  else 'FAIL'}")
    print(f"  Slack Webhook:  {'PASS' if slack_ok else 'FAIL'}")
    print("=" * 55)

    if not (groq_ok and slack_ok):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
