from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response as FastAPIResponse
from fastapi.responses import Response

from channels.telegram import router as telegram_router, webhook_router as telegram_webhook_router
from channels.wati import router as wati_router
from channels.twilio import (
    process_twilio_update,
    router as twilio_router,
    validate_twilio_signature,
)
from config import settings
from conversation.session import check_session_store


@asynccontextmanager
async def lifespan(_: FastAPI):
    errors = settings.production_errors()
    if errors:
        raise RuntimeError("Invalid production configuration: " + "; ".join(errors))
    if settings.require_redis and not await check_session_store():
        raise RuntimeError("Redis is required but unavailable")
    yield


app = FastAPI(title="Tailorsin Backend", lifespan=lifespan)
if settings.telegram_enabled:
    app.include_router(telegram_router)
    app.include_router(telegram_webhook_router)
if settings.wati_enabled:
    app.include_router(wati_router)
if settings.twilio_enabled:
    app.include_router(twilio_router)


@app.get("/health")
async def healthcheck(response: FastAPIResponse) -> dict[str, object]:
    """Non-sensitive readiness information for local and production monitors."""
    session_store_ready = await check_session_store()
    configuration_errors = settings.production_errors()
    ready = session_store_ready and not configuration_errors
    if not ready:
        response.status_code = 503
    return {
        "status": "ok" if ready else "degraded",
        "session_store": "ok" if session_store_ready else "unavailable",
        "channels": {
            "telegram": settings.telegram_enabled,
            "wati": settings.wati_enabled,
            "twilio": settings.twilio_enabled,
        },
    }


# Catch-all route for Twilio webhooks (backward compatibility)
@app.post("/webhook")
async def twilio_webhook_root(request: Request) -> Response:
    """Handle Twilio webhooks sent to /webhook (backward compatibility)."""
    if not settings.twilio_enabled:
        raise HTTPException(status_code=404, detail="Twilio channel is disabled")
    form_data = await request.form()
    form_dict = dict(form_data)
    webhook_url = settings.twilio_webhook_url or str(request.url)
    validate_twilio_signature(webhook_url, request.headers.get("X-Twilio-Signature"), form_dict)
    twiml_response = await process_twilio_update(form_dict)
    return Response(content=twiml_response, media_type="text/xml")
