#!/usr/bin/env python3
"""
Diagnose and test Telegram webhook issues.
This tests your deployed endpoint to determine the exact problem.
"""
import httpx
import sys
from dotenv import load_dotenv
import os

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
WEBHOOK_URL = os.getenv("TELEGRAM_WEBHOOK_URL", "")

def test_endpoint():
    """Test the deployed endpoint to see what response we get."""
    
    print("=" * 70)
    print("Telegram Endpoint Diagnostic")
    print("=" * 70)
    print()
    
    # Test 1: Without secret token
    print("1. Testing WITHOUT secret token...")
    test_url = "https://tailorsin-deterministic-bot.onrender.com/telegram/webhook"
    payload = {
        "message": {
            "message_id": 1,
            "date": 1234567890,
            "text": "/start",
            "from": {"id": 123456789, "first_name": "Test"},
            "chat": {"id": 123456789, "type": "private"}
        }
    }
    
    try:
        response = httpx.post(test_url, json=payload, timeout=10, follow_redirects=True)
        print(f"   Status: {response.status_code}")
        print(f"   Response: {response.text[:200]}")
        print()
    except Exception as e:
        print(f"   Error: {e}")
        print()
    
    # Test 2: With secret token
    print("2. Testing WITH secret token...")
    headers = {"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET} if WEBHOOK_SECRET else {}
    
    try:
        response = httpx.post(test_url, json=payload, headers=headers, timeout=10, follow_redirects=True)
        print(f"   Status: {response.status_code}")
        print(f"   Response: {response.text[:200]}")
        print()
    except Exception as e:
        print(f"   Error: {e}")
        print()
    
    # Test 3: Check what Telegram sees
    print("3. Checking Telegram webhook status...")
    if TELEGRAM_BOT_TOKEN:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getWebhookInfo"
        try:
            resp = httpx.get(url, timeout=10)
            data = resp.json()
            if data.get("ok"):
                result = data["result"]
                print(f"   URL: {result.get('url', '(not set)')}")
                print(f"   Pending updates: {result.get('pending_update_count', 0)}")
                print(f"   Secret token: {result.get('secret_token', '(none)')}")
                print(f"   Last error: {result.get('last_error_message', 'none')}")
                print()
                
                # Diagnosis
                current_secret = result.get('secret_token', '')
                if not current_secret and WEBHOOK_SECRET:
                    print("❌ PROBLEM IDENTIFIED:")
                    print(f"   Telegram has NO secret token")
                    print(f"   Your .env has: {WEBHOOK_SECRET}")
                    print()
                    print("   Your deployed app is REJECTING webhooks because:")
                    print("   - App expects secret token")
                    print("   - Telegram isn't sending one")
                    print("   - Result: 403 Forbidden")
                    print()
                    print("   SOLUTIONS (pick one):")
                    print()
                    print("   A. Update your deployed app's environment variables:")
                    print(f"      Add TELEGRAM_WEBHOOK_SECRET with value: (empty or remove it)")
                    print("      Then redeploy WITHOUT setting a secret in Telegram")
                    print()
                    print("   B. Update this .env file to remove the secret, then redeploy:")
                    print("      Comment out or remove: TELEGRAM_WEBHOOK_SECRET")
                    print()
                    print("   C. Quick workaround - redeploy your app with updated code")
                    print("      (the code changes I made make secret validation optional)")
                    return False
                elif current_secret != WEBHOOK_SECRET:
                    print("❌ PROBLEM: Secret token mismatch")
                    print(f"   Telegram has: {current_secret}")
                    print(f"   .env has: {WEBHOOK_SECRET}")
                    return False
                else:
                    print("✅ Secret tokens match")
                    return True
        except Exception as e:
            print(f"   Error: {e}")
    
    return False

if __name__ == "__main__":
    success = test_endpoint()
    sys.exit(0 if success else 1)