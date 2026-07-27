#!/usr/bin/env python3
"""
Quick diagnostic to check Telegram webhook secret token mismatch.
"""
import httpx
from dotenv import load_dotenv
import os

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET")

if not TELEGRAM_BOT_TOKEN:
    print("❌ TELEGRAM_BOT_TOKEN not set")
    exit(1)

# Get current webhook info from Telegram
url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getWebhookInfo"
response = httpx.get(url, timeout=10)
data = response.json()

if not data.get("ok"):
    print(f"❌ Error: {data}")
    exit(1)

webhook_info = data["result"]
current_secret = webhook_info.get("secret_token", "")
current_url = webhook_info.get("url", "")

print("=" * 60)
print("Telegram Webhook Secret Token Diagnostic")
print("=" * 60)
print()
print(f"Current webhook URL: {current_url}")
print(f"Pending updates: {webhook_info.get('pending_update_count', 0)}")
print()
print("Secret Token Comparison:")
print(f"  Telegram has:    '{current_secret}'")
print(f"  Your .env has:   '{WEBHOOK_SECRET}'")
print()

if current_secret == WEBHOOK_SECRET:
    print("✅ Secret tokens MATCH")
    print()
    print("If you're still getting 403 errors, check:")
    print("  1. Make sure your app restarted after .env changes")
    print("  2. Verify the webhook endpoint is correct")
    print(f"  3. Current endpoint: {current_url}")
elif not current_secret and WEBHOOK_SECRET:
    print("❌ MISMATCH: Telegram has NO secret, but .env has one")
    print()
    print("Solution: Re-register webhook WITH the secret token")
    print(f"  The webhook URL should be: {current_url}")
    print(f"  The secret should be: {WEBHOOK_SECRET}")
elif current_secret and not WEBHOOK_SECRET:
    print("❌ MISMATCH: Telegram HAS a secret, but .env doesn't")
    print()
    print(f"Solution: Add this to your .env file:")
    print(f"  TELEGRAM_WEBHOOK_SECRET={current_secret}")
else:
    print(f"❌ MISMATCH: Secrets don't match!")
    print()
    print("Solution: Re-register webhook with correct secret")
    print(f"  Use secret: {WEBHOOK_SECRET}")

print()
print("Quick fix command:")
if current_secret != WEBHOOK_SECRET:
    print(f"  curl -F 'url={current_url}' -F 'secret_token={WEBHOOK_SECRET}' \\")
    print(f"    'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook'")
else:
    print("  No re-registration needed - secrets match!")