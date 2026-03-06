#!/usr/bin/env python3
"""
Send test webhook payloads to the local service.
Simulates Dialpad call events without needing actual Dialpad calls.

Usage:
    python scripts/test_webhook.py
    python scripts/test_webhook.py --url http://localhost:8000
"""
import argparse
import json
import os
import time
from pathlib import Path

import jwt
import requests
from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


# Sample payloads (fictional data for testing)
SAMPLE_HANGUP_EVENT = {
    "call_id": "1000000000000001",
    "state": "hangup",
    "direction": "outbound",
    "external_number": "+15555550101",
    "internal_number": "+15555550201",
    "date_started": 1741158385,
    "date_connected": 1741158391,
    "date_ended": 1741158409,
    "duration": 18,
    "contact": {
        "phone": "+15555550101",
        "name": "Jane Doe",
        "type": "shared",
        "id": "contact_001"
    },
    "target": {
        "type": "user",
        "id": "9000000000000001",
        "name": "Alice Smith"
    },
    "recording_url": None,
    "labels": ["outbound", "outbound_connected", "user_initiated"]
}

SAMPLE_INBOUND_HANGUP = {
    "call_id": "1000000000000002",
    "state": "hangup",
    "direction": "inbound",
    "external_number": "+15555550102",
    "internal_number": "+15555550202",
    "date_started": 1741165493,
    "date_connected": 1741165512,
    "date_ended": 1741165713,
    "duration": 201,
    "contact": {
        "phone": "+15555550102",
        "name": "Bob Johnson",
        "type": "shared",
        "id": "contact_002"
    },
    "target": {
        "type": "user",
        "id": "9000000000000002",
        "name": "Charlie Brown"
    },
    "recording_url": None,
    "labels": ["answered", "inbound"]
}

SAMPLE_MISSED_CALL = {
    "call_id": "1000000000000003",
    "state": "hangup",
    "direction": "inbound",
    "external_number": "+15555550103",
    "internal_number": "+15555550203",
    "date_started": 1741161437,
    "date_connected": None,
    "date_ended": 1741161519,
    "duration": 0,
    "contact": {
        "phone": "+15555550103",
        "name": None,
        "type": None,
        "id": None
    },
    "target": {
        "type": "user",
        "id": "9000000000000003",
        "name": "Dana White"
    },
    "recording_url": None,
    "labels": ["inbound", "missed", "unanswered", "voicemail"]
}

SAMPLE_TRANSCRIPT_READY = {
    "call_id": "1000000000000001",
    "state": "call_transcription",
}

SAMPLE_RECORDING = {
    "call_id": "1000000000000001",
    "state": "recording",
    "recording_url": "https://example.com/recording/test_123"
}


def sign_payload(payload: dict, secret: str | None) -> tuple[bytes, dict]:
    """Sign payload as JWT if secret is set, otherwise return plain JSON."""
    if secret:
        token = jwt.encode(payload, secret, algorithm="HS256")
        return token.encode("utf-8") if isinstance(token, str) else token, {"Content-Type": "text/plain"}
    else:
        return json.dumps(payload).encode("utf-8"), {"Content-Type": "application/json"}


def send_event(url: str, payload: dict, name: str, secret: str | None = None):
    """Send a test event and print the result."""
    print(f"\n  Sending: {name}")
    print(f"  Payload: call_id={payload.get('call_id')} state={payload.get('state')}")

    try:
        body, headers = sign_payload(payload, secret)
        resp = requests.post(url, data=body, headers=headers, timeout=10)
        print(f"  Response: {resp.status_code} {resp.json()}")
        return resp.status_code == 200
    except requests.ConnectionError:
        print(f"  ERROR: Could not connect to {url}")
        return False
    except Exception as e:
        print(f"  ERROR: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Test webhook with sample payloads")
    parser.add_argument("--url", default="http://localhost:8000", help="Service base URL")
    parser.add_argument("--secret", default=os.environ.get("DIALPAD_WEBHOOK_SECRET"),
                        help="Webhook secret for JWT signing (reads from DIALPAD_WEBHOOK_SECRET env var)")
    args = parser.parse_args()

    secret = args.secret if args.secret else None
    webhook_url = f"{args.url}/webhooks/call"

    print(f"\n{'='*60}")
    print(f"Testing Dialpad Webhook Service")
    print(f"Endpoint: {webhook_url}")
    print(f"JWT signing: {'enabled' if secret else 'disabled (plain JSON)'}")
    print(f"{'='*60}")

    events = [
        (SAMPLE_HANGUP_EVENT, "Outbound call hangup (Alice → +15555550101)"),
        (SAMPLE_INBOUND_HANGUP, "Inbound call hangup (+15555550102 → Charlie)"),
        (SAMPLE_MISSED_CALL, "Missed inbound call (+15555550103 → Dana)"),
        (SAMPLE_RECORDING, "Recording available for call 1000000000000001"),
        (SAMPLE_TRANSCRIPT_READY, "Transcript ready for call 1000000000000001"),
    ]

    passed = 0
    for payload, name in events:
        if send_event(webhook_url, payload, name, secret=secret):
            passed += 1
        time.sleep(0.5)

    # Query stored data
    print(f"\n{'='*60}")
    print("Verifying stored data...")
    print(f"{'='*60}")

    try:
        # Check stats
        resp = requests.get(f"{args.url}/api/stats", timeout=5)
        if resp.status_code == 200:
            stats = resp.json()
            print(f"\n  Stats: {json.dumps(stats, indent=2)}")

        # Check calls
        resp = requests.get(f"{args.url}/api/calls?limit=5", timeout=5)
        if resp.status_code == 200:
            calls = resp.json()
            print(f"\n  Stored calls: {calls['count']}")
            for c in calls["calls"]:
                print(f"    - {c['call_id']}: {c['category']} | {c['direction']} | {c['name']} | transcript: {c.get('has_transcript')}")

        # Check specific call with transcript
        resp = requests.get(f"{args.url}/api/calls/1000000000000001", timeout=5)
        if resp.status_code == 200:
            call_data = resp.json()
            print(f"\n  Call detail for 1000000000000001:")
            print(f"    Recording: {call_data['call'].get('recording_url')}")
            if call_data.get("transcript"):
                print(f"    Transcript status: {call_data['transcript']['status']}")

    except requests.ConnectionError:
        print(f"\n  Could not connect to {args.url} for verification")

    print(f"\n{'='*60}")
    print(f"Results: {passed}/{len(events)} events processed successfully")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
