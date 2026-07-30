"""Single configuration boundary for all deployment environments.

Keep credentials and public webhook URLs in `.env` locally, or in the hosting
platform's environment-variable manager in production. Application code reads
configuration through `settings`, never through `os.getenv` directly.
"""

import os

from dotenv import load_dotenv


load_dotenv()


def _as_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _as_positive_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


class Settings:
    # Shared runtime settings
    app_env = os.getenv("APP_ENV", "development").strip().lower()
    production_mode = app_env in {"production", "prod"}
    redis_url = os.getenv("REDIS_URL", "")
    http_timeout = _as_positive_float("HTTP_TIMEOUT", 20.0)
    session_timeout_seconds = _as_positive_int("SESSION_TIMEOUT_SECONDS", 600)
    idempotency_ttl_seconds = _as_positive_int("IDEMPOTENCY_TTL_SECONDS", 86400)
    require_redis = _as_bool("REQUIRE_REDIS", production_mode)
    require_webhook_secrets = _as_bool("REQUIRE_WEBHOOK_SECRETS", production_mode)

    # Telegram
    telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_webhook_url = os.getenv("TELEGRAM_WEBHOOK_URL", "")
    telegram_webhook_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    telegram_enabled = _as_bool("TELEGRAM_ENABLED", bool(telegram_bot_token))

    # WATI WhatsApp
    wati_base_url = os.getenv("WATI_BASE_URL", "")
    wati_api_key = os.getenv("WATI_API_KEY", "")
    wati_webhook_url = os.getenv("WATI_WEBHOOK_URL", "")
    wati_webhook_secret = os.getenv("WATI_WEBHOOK_SECRET", "")
    wati_enabled = _as_bool("WATI_ENABLED", bool(wati_base_url and wati_api_key))

    def production_errors(self) -> list[str]:
        """Return actionable configuration errors without exposing secrets."""
        if not self.production_mode:
            return []

        errors: list[str] = []
        if self.require_redis and not self.redis_url:
            errors.append("REDIS_URL is required in production")
        if self.telegram_enabled:
            if not self.telegram_bot_token:
                errors.append("TELEGRAM_BOT_TOKEN is required when Telegram is enabled")
            if not self.telegram_webhook_url.startswith("https://"):
                errors.append("TELEGRAM_WEBHOOK_URL must be an HTTPS URL in production")
            if self.require_webhook_secrets and not self.telegram_webhook_secret:
                errors.append("TELEGRAM_WEBHOOK_SECRET is required in production")
        if self.wati_enabled:
            if not self.wati_base_url or not self.wati_api_key:
                errors.append("WATI_BASE_URL and WATI_API_KEY are required when WATI is enabled")
            if not self.wati_webhook_url.startswith("https://"):
                errors.append("WATI_WEBHOOK_URL must be an HTTPS URL in production")
            if self.require_webhook_secrets and not self.wati_webhook_secret:
                errors.append("WATI_WEBHOOK_SECRET is required in production")
        return errors


settings = Settings()
