# Production deployment

## 1. Choose the active channel combination

Run Telegram and WATI together:

```env
TELEGRAM_ENABLED=true
WATI_ENABLED=true
```

## 2. Required production settings

```env
APP_ENV=production
REQUIRE_REDIS=true
REQUIRE_WEBHOOK_SECRETS=true
REDIS_URL=rediss://...
SESSION_TIMEOUT_SECONDS=600
IDEMPOTENCY_TTL_SECONDS=86400
```

Use the matching provider section in `.env.example` for credentials and public
HTTPS webhook URLs. Store those values in your hosting provider's secret
manager; do not commit `.env`.

## 3. Build and run

```bash
docker build -t tailorsin-bot .
docker run --env-file .env -p 8000:8000 tailorsin-bot
```

For a managed host, use its Docker deployment support and set the same values
as encrypted environment variables. Do not use `uvicorn --reload` in
production.

## 4. Configure provider webhooks

| Provider | Webhook URL |
| --- | --- |
| Telegram | `https://your-domain/telegram/webhook` |
| WATI | `https://your-domain/wati/webhook` |

Telegram must be registered with `secret_token` equal to
`TELEGRAM_WEBHOOK_SECRET`. Configure the matching WATI webhook secret in the
WATI console.

## 5. Verify before launch

```bash
curl -i https://your-domain/health
```

Expect HTTP 200 and all selected channels to be `true`. A production app fails
startup for missing required secrets, non-HTTPS provider URLs, or unavailable
Redis.

Run the complete test suite before every deployment:

```bash
PYTHONPATH=. python -m pytest -q
```
