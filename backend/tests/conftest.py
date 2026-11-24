import asyncio


class FakeWebSocket:
    """Minimal stand-in for starlette's WebSocket used by ConnectionHub."""

    def __init__(self, block: asyncio.Event | None = None) -> None:
        self.sent: list[dict] = []
        self.closed_code: int | None = None
        self.block = block  # when set, send_json waits on it (a slow client)

    async def send_json(self, data: dict) -> None:
        if self.block is not None:
            await self.block.wait()
        self.sent.append(data)

    async def close(self, code: int = 1000) -> None:
        self.closed_code = code


async def drain(iterations: int = 50) -> None:
    """Let background writer tasks run."""
    for _ in range(iterations):
        await asyncio.sleep(0)


# ---- service-level fakes and fixtures ----------------------------------------

from datetime import datetime, timezone  # noqa: E402

import pytest  # noqa: E402


class FakeGateway:
    """In-memory TelegramGateway: records sends, can fail or be slow."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.fail_with: Exception | None = None
        self.delay: float = 0
        self.started = False
        self.stopped = False
        self.service = None

    async def send_text(self, chat_id: int, text: str) -> datetime:
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail_with is not None:
            raise self.fail_with
        self.sent.append((chat_id, text))
        return datetime.now(timezone.utc)

    def attach(self, service) -> None:
        self.service = service

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


@pytest.fixture
def gateway() -> FakeGateway:
    return FakeGateway()


@pytest.fixture
def service(gateway):
    from app.hub import ConnectionHub
    from app.service import ChatService
    from app.session import SessionState
    from app.store import MessageStore

    return ChatService(MessageStore(), SessionState(), ConnectionHub(), gateway)
