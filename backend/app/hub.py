"""Fan-out of server frames to connected browsers.

Each connection gets its own bounded queue drained by a writer task, so one slow
browser never blocks the others or the ChatService lock. A connection whose
queue overflows is closed (code 1013 "try again later"); the browser reconnects
and resyncs from its last seq.
"""

import asyncio
import contextlib
import logging
from typing import Any

logger = logging.getLogger(__name__)

CLOSE_TRY_AGAIN = 1013


class Connection:
    def __init__(self, ws: Any, queue_size: int) -> None:
        self.ws = ws
        self.queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=queue_size)
        self.closed = False
        self._writer: asyncio.Task | None = None

    def start(self) -> None:
        self._writer = asyncio.create_task(self._write_loop())

    async def _write_loop(self) -> None:
        try:
            while True:
                frame = await self.queue.get()
                await self.ws.send_json(frame)
        except asyncio.CancelledError:
            raise
        except Exception:  # client went away mid-send; the reader side cleans up
            logger.debug("websocket writer stopped", exc_info=True)

    async def close(self, code: int = CLOSE_TRY_AGAIN) -> None:
        if self.closed:
            return
        self.closed = True
        if self._writer is not None and self._writer is not asyncio.current_task():
            self._writer.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._writer
        with contextlib.suppress(Exception):
            await self.ws.close(code=code)


class ConnectionHub:
    def __init__(self, queue_size: int = 100) -> None:
        self._queue_size = queue_size
        self._connections: set[Connection] = set()
        self._closing: set[asyncio.Task] = set()

    def __len__(self) -> int:
        return len(self._connections)

    def add(self, ws: Any) -> Connection:
        conn = Connection(ws, self._queue_size)
        self._connections.add(conn)
        conn.start()
        return conn

    def remove(self, conn: Connection) -> None:
        self._connections.discard(conn)

    def send(self, conn: Connection, frame: dict) -> None:
        self._put(conn, frame)

    def broadcast(self, frame: dict) -> None:
        for conn in list(self._connections):
            self._put(conn, frame)

    def _put(self, conn: Connection, frame: dict) -> None:
        if conn.closed:
            return
        try:
            conn.queue.put_nowait(frame)
        except asyncio.QueueFull:
            logger.warning("dropping slow websocket client")
            self.remove(conn)
            task = asyncio.create_task(conn.close(CLOSE_TRY_AGAIN))
            self._closing.add(task)
            task.add_done_callback(self._closing.discard)
