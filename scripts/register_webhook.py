#!/usr/bin/env python3
"""
Register a webhook and call event subscription with Dialpad.

Usage:
    python scripts/register_webhook.py --url https://your-domain.com/webhooks/call

For testing with ngrok:
    python scripts/register_webhook.py --url https://abc123.ngrok-free.app/webhooks/call

Options:
    --url         Your webhook endpoint URL (required)
    --secret      Webhook secret for JWT signing (optional, recommended for production)
    --sandbox     Use sandbox API (default: True)
    --target-type Scope to specific target: company, office, department, callcenter, user
    --target-id   Target ID (required if target-type is set)
    --states      Comma-separated call states to subscribe to (default: all important ones)
"""
import argparse
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def main():
    parser = argparse.ArgumentParser(description="Register Dialpad webhook & subscriptions")
    parser.add_argument("--url", required=True, help="Webhook endpoint URL")
    parser.add_argument("--secret", default=os.getenv("DIALPAD_WEBHOOK_SECRET", ""),
                        help="Webhook secret for JWT signing")
    parser.add_argument("--sandbox", action="store_true", default=True,
                        help="Use sandbox API")
    parser.add_argument("--production", action="store_true", default=False,
                        help="Use production API")
    parser.add_argument("--target-type", default=None,
                        help="Target type: company, office, department, callcenter, user")
    parser.add_argument("--target-id", default=None,
                        help="Target ID (required with --target-type)")
    parser.add_argument("--states", default="hangup,call_transcription,recording,missed,voicemail",
                        help="Comma-separated call states")
    args = parser.parse_args()

    api_key = os.getenv("DIALPAD_API_KEY")
    if not api_key:
        print("ERROR: DIALPAD_API_KEY not set in .env or environment")
        sys.exit(1)

    base_url = os.getenv("DIALPAD_API_BASE_URL", "https://sandbox.dialpad.com/api/v2").rstrip("/")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # ── Step 1: Create Webhook ───────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"Registering webhook with Dialpad")
    print(f"API: {base_url}")
    print(f"URL: {args.url}")
    print(f"Secret: {'***' + args.secret[-4:] if args.secret else '(none - dev mode)'}")
    print(f"{'='*60}\n")

    webhook_payload = {"hook_url": args.url}
    if args.secret:
        webhook_payload["secret"] = args.secret

    print("Step 1: Creating webhook...")
    resp = requests.post(f"{base_url}/webhooks", json=webhook_payload, headers=headers)

    if resp.status_code not in (200, 201):
        print(f"FAILED: {resp.status_code} {resp.text}")
        sys.exit(1)

    webhook_data = resp.json()
    webhook_id = webhook_data.get("id") or webhook_data.get("webhook_id")
    print(f"  Webhook created: {webhook_id}")

    # ── Step 2: Create Call Event Subscription ───────────────────────
    print("\nStep 2: Creating call event subscription...")
    call_states = [s.strip() for s in args.states.split(",")]

    sub_payload = {
        "webhook_id": webhook_id,
        "call_states": call_states,
    }

    if args.target_type and args.target_id:
        sub_payload["target_type"] = args.target_type
        sub_payload["target_id"] = args.target_id
        print(f"  Scoped to {args.target_type}: {args.target_id}")

    print(f"  States: {call_states}")

    resp = requests.post(f"{base_url}/subscriptions/call", json=sub_payload, headers=headers)

    if resp.status_code not in (200, 201):
        print(f"FAILED: {resp.status_code} {resp.text}")
        sys.exit(1)

    sub_data = resp.json()
    sub_id = sub_data.get("id") or sub_data.get("subscription_id")
    print(f"  Subscription created: {sub_id}")

    # ── Summary ──────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("SUCCESS! Webhook registered and subscribed.")
    print(f"\n  Webhook ID:       {webhook_id}")
    print(f"  Subscription ID:  {sub_id}")
    print(f"  Endpoint:         {args.url}")
    print(f"  Call States:      {', '.join(call_states)}")
    print(f"\nSave these IDs — you'll need them to manage or delete later.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
