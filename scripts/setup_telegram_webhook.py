#!/usr/bin/env python3
"""
Script to set up and verify Telegram webhook configuration.
Run this after deploying your app to register the webhook URL.
"""
import os
import sys
import httpx
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = os.getenv("TELEGRAM_WEBHOOK_URL")  # Your deployed webhook URL
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET")


def get_webhook_info():
    """Get current webhook information from Telegram."""
    if not TELEGRAM_BOT_TOKEN:
        print("❌ Error: TELEGRAM_BOT_TOKEN not set in .env")
        return None

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getWebhookInfo"
    
    try:
        response = httpx.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if not data.get("ok"):
            print(f"❌ Telegram API error: {data}")
            return None
            
        return data.get("result", {})
    except Exception as e:
        print(f"❌ Error getting webhook info: {e}")
        return None


def verify_secret_token_match():
    """Check if the webhook's secret token matches what's in .env."""
    webhook_info = get_webhook_info()
    if not webhook_info:
        return False
    
    current_secret = webhook_info.get("secret_token", "")
    env_secret = WEBHOOK_SECRET or ""
    
    if current_secret and current_secret != env_secret:
        print(f"⚠️  SECRET TOKEN MISMATCH!")
        print(f"   Telegram has: {current_secret}")
        print(f"   Your .env has: {env_secret}")
        print()
        print("   This will cause 403 Forbidden errors!")
        print()
        return False
    
    elif not current_secret and env_secret:
        print(f"⚠️  Webhook has NO secret token but .env has one set")
        print(f"   Telegram has: (none)")
        print(f"   Your .env has: {env_secret}")
        print()
        print("   You need to re-register the webhook with the secret token!")
        print()
        return False
    
    elif current_secret and not env_secret:
        print(f"⚠️  Webhook has a secret token but .env doesn't")
        print(f"   Telegram has: {current_secret}")
        print(f"   Your .env has: (none)")
        print()
        print("   Add TELEGRAM_WEBHOOK_SECRET to your .env file!")
        print()
        return False
    
    else:
        print(f"✅ Secret token matches: {current_secret or '(none)'}")
        print()
        return True


def set_webhook(webhook_url: str, secret_token: str = None):
    """Set the Telegram webhook URL."""
    if not TELEGRAM_BOT_TOKEN:
        print("❌ Error: TELEGRAM_BOT_TOKEN not set in .env")
        return False

    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook"
    
    payload = {
        "url": webhook_url,
        "allowed_updates": ["message", "edited_message", "callback_query"],
    }
    
    if secret_token:
        payload["secret_token"] = secret_token
    
    try:
        response = httpx.post(api_url, json=payload, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if data.get("ok"):
            print(f"✅ Webhook set successfully to: {webhook_url}")
            if secret_token:
                print(f"   Secret token configured")
            return True
        else:
            print(f"❌ Failed to set webhook: {data}")
            return False
    except Exception as e:
        print(f"❌ Error setting webhook: {e}")
        return False


def delete_webhook():
    """Delete the current webhook."""
    if not TELEGRAM_BOT_TOKEN:
        print("❌ Error: TELEGRAM_BOT_TOKEN not set in .env")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook"
    
    try:
        response = httpx.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if data.get("ok"):
            print("✅ Webhook deleted successfully")
            return True
        else:
            print(f"❌ Failed to delete webhook: {data}")
            return False
    except Exception as e:
        print(f"❌ Error deleting webhook: {e}")
        return False


def main():
    print("=" * 60)
    print("Telegram Webhook Setup Tool")
    print("=" * 60)
    print()

    # Check current webhook status
    print("📋 Checking current webhook status...")
    webhook_info = get_webhook_info()
    
    if webhook_info:
        current_url = webhook_info.get("url", "")
        has_cert = webhook_info.get("has_custom_certificate", False)
        pending_count = webhook_info.get("pending_update_count", 0)
        last_error = webhook_info.get("last_error_message", "")
        last_error_date = webhook_info.get("last_error_date", "")
        current_secret = webhook_info.get("secret_token", "")
        
        print(f"   Current webhook URL: {current_url or '(not set)'}")
        print(f"   Pending updates: {pending_count}")
        print(f"   Has custom certificate: {has_cert}")
        print(f"   Current secret token in Telegram: {current_secret or '(none)'}")
        print(f"   Secret token in .env: {WEBHOOK_SECRET or '(none)'}")
        
        if last_error:
            print(f"   ⚠️  Last error: {last_error}")
            print(f"   Last error date: {last_error_date}")
        print()
    
    # If WEBHOOK_URL is not set, show instructions
    if not WEBHOOK_URL:
        print("⚠️  TELEGRAM_WEBHOOK_URL not set in .env")
        print()
        print("To set up your webhook, add the following to your .env file:")
        print()
        print("  TELEGRAM_WEBHOOK_URL=https://your-domain.com/telegram/webhook")
        print()
        print("Then run this script again.")
        print()
        print("Available webhook endpoints in your app:")
        print("  - POST /telegram/webhook (with secret token in header)")
        print("  - POST /telegram/webhook/{secret} (with secret in URL)")
        print("  - POST /webhook/telegram (alternative, no secret)")
        print()
        return 1
    
    # Check for secret token mismatch (403 Forbidden errors)
    if not verify_secret_token_match():
        print("❌ Secret token mismatch detected!")
        print()
        print("This is causing 403 Forbidden errors. You need to re-register the webhook.")
        print()
        choice = input("Do you want to fix this by re-registering the webhook? (y/N): ").strip().lower()
        if choice != "y":
            print("Aborted. Please manually fix the secret token mismatch.")
            return 1
        print()

    # Ask what to do
    print("What would you like to do?")
    print("  1. Set/update webhook")
    print("  2. Delete webhook")
    print("  3. Check webhook status (already done)")
    print()
    
    choice = input("Enter choice (1-3): ").strip()
    print()

    if choice == "1":
        # Determine which webhook URL to use
        if "/telegram/webhook" not in WEBHOOK_URL:
            print(f"⚠️  Warning: Your WEBHOOK_URL doesn't contain '/telegram/webhook'")
            print(f"   Current: {WEBHOOK_URL}")
            print()
            confirm = input("Continue anyway? (y/N): ").strip().lower()
            if confirm != "y":
                print("Aborted.")
                return 1
        
        success = set_webhook(WEBHOOK_URL, WEBHOOK_SECRET)
        return 0 if success else 1
    
    elif choice == "2":
        confirm = input("Are you sure you want to delete the webhook? (y/N): ").strip().lower()
        if confirm == "y":
            success = delete_webhook()
            return 0 if success else 1
        else:
            print("Aborted.")
            return 0
    
    elif choice == "3":
        print("Webhook status already displayed above.")
        return 0
    
    else:
        print("Invalid choice.")
        return 1


if __name__ == "__main__":
    sys.exit(main())