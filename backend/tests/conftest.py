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
