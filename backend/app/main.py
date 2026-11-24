"""FastAPI app factory.

Run with a single worker: `uvicorn app.main:app` (Telegram long polling allows
only one consumer per bot token).
"""

import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.hub import ConnectionHub
from app.service import ChatService
from app.session import SessionState
from app.store import MessageStore
from app.ws import router as ws_router

logger = logging.getLogger(__name__)

GatewayFactory = Callable[[Settings], Any]


def _telegram_gateway(settings: Settings) -> Any:
    from app.telegram_gateway import TelegramGateway

    return TelegramGateway(settings.telegram_bot_token.get_secret_value())


def create_app(
    settings: Settings | None = None,
    gateway_factory: GatewayFactory | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    gateway_factory = gateway_factory or _telegram_gateway

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        gateway = gateway_factory(settings)
        service = ChatService(
            MessageStore(), SessionState(), ConnectionHub(settings.ws_queue_size), gateway
        )
        gateway.attach(service)
        app.state.service = service
        await gateway.start()
        logger.info("telegram bot started")
        try:
            yield
        finally:
            await gateway.stop()

    app = FastAPI(title="Telegram Chat Backend", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/status")
    async def status() -> dict:
        return app.state.service.status().model_dump(mode="json")

    app.include_router(ws_router)
    return app


def __getattr__(name: str) -> FastAPI:
    # `uvicorn app.main:app` builds the app on first access, so importing this
    # module (e.g. in tests) never requires a token.
    if name != "app":
        raise AttributeError(name)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        settings = get_settings()
    except ValidationError as exc:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN is not set. Copy .env.example to .env and add your BotFather token."
        ) from exc
    application = create_app(settings)
    globals()["app"] = application
    return application
