"""ChatService: the single entry point for every state change.

Telegram updates (/start, /stop, text) and browser actions (connect, send) all
go through one asyncio.Lock. That gives us, without further coordination:

* atomic claim/release of the single participant;
* one total order of messages: seq is assigned and the frame is broadcast inside
  the same critical section;
* outgoing messages reach Telegram in the same order as their seq, and a /stop
  cannot slip in between "read the owner" and "send to the owner".

Trade-off: the lock is held during the Telegram API call, so a slow call delays
other events for its duration. Fine for one participant; see README for the
multi-instance design.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Protocol

from app.hub import Connection, ConnectionHub
from app.models import (
    ErrorCode,
    HelloFrame,
    Message,
    Participant,
    Status,
    message_frame,
    snapshot_frame,
    status_frame,
)
from app.session import ClaimResult, ReleaseResult, SessionState
from app.store import MessageStore

logger = logging.getLogger(__name__)

REPLY_CONNECTED = "Connected. Messages you send here will appear in the web chat."
REPLY_ALREADY_CONNECTED = "You're already connected."
REPLY_BUSY = "This bot is already in a conversation with someone else. Try again later."
REPLY_NOT_CONNECTED = "You're not connected. Send /start to connect."
REPLY_DISCONNECTED = "Disconnected."
REPLY_SEND_START = "Send /start to connect."
REPLY_TEXT_ONLY = "Only text messages are supported."


class GatewayError(Exception):
    """Telegram could not deliver the message."""


class GatewayForbidden(GatewayError):
    """The participant blocked the bot (Telegram 403)."""


class TelegramGateway(Protocol):
    async def send_text(self, chat_id: int, text: str) -> datetime: ...


class ChatError(Exception):
    def __init__(self, code: ErrorCode, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class ChatService:
    def __init__(
        self,
        store: MessageStore,
        session: SessionState,
        hub: ConnectionHub,
        gateway: TelegramGateway,
    ) -> None:
        self.store = store
        self.session = session
        self.hub = hub
        self.gateway = gateway
        self._lock = asyncio.Lock()

    def status(self) -> Status:
        return self.session.status()

    # ---- browsers ---------------------------------------------------------------

    async def connect(self, ws: Any, hello: HelloFrame) -> Connection:
        async with self._lock:
            conn = self.hub.add(ws)
            same_session = hello.session_id is not None and hello.session_id == self.store.session_id
            messages = self.store.since(hello.last_seq) if same_session else self.store.all()
            # Enqueued under the lock: no broadcast can overtake the snapshot.
            self.hub.send(conn, snapshot_frame(self.status(), messages))
            return conn

    async def disconnect(self, conn: Connection) -> None:
        self.hub.remove(conn)
        await conn.close(code=1000)

    async def send(self, client_id: str, text: str) -> Message:
        async with self._lock:
            owner = self.session.owner
            if owner is None:
                raise ChatError("NO_PARTICIPANT", "No Telegram user is connected. Ask them to send /start.")
            try:
                sent_at = await self.gateway.send_text(owner.chat_id, text)
            except GatewayForbidden as exc:
                logger.info("participant blocked the bot; releasing session")
                self.session.release(owner.chat_id)
                self._broadcast_status()
                raise ChatError("TELEGRAM_ERROR", "The Telegram user blocked the bot.") from exc
            except GatewayError as exc:
                logger.warning("telegram send failed: %s", exc)
                raise ChatError("TELEGRAM_ERROR", "Telegram did not accept the message. Try again.") from exc
            msg = self.store.append("outgoing", text, sent_at, client_id=client_id)
            self.hub.broadcast(message_frame(msg))
            return msg

    # ---- telegram ---------------------------------------------------------------

    async def on_start(self, participant: Participant) -> str:
        async with self._lock:
            result = self.session.claim(participant)
            if result is ClaimResult.CLAIMED:
                self.store.reset(self.session.session_id)
                self._broadcast_status()
                return REPLY_CONNECTED
            if result is ClaimResult.ALREADY_OWNER:
                return REPLY_ALREADY_CONNECTED
            return REPLY_BUSY

    async def on_stop(self, chat_id: int) -> str:
        async with self._lock:
            result = self.session.release(chat_id)
            if result is ReleaseResult.RELEASED:
                self._broadcast_status()
                return REPLY_DISCONNECTED
            if result is ReleaseResult.NOT_CONNECTED:
                return REPLY_NOT_CONNECTED
            return REPLY_BUSY

    async def on_text(self, chat_id: int, text: str, sent_at: datetime) -> str | None:
        async with self._lock:
            refusal = self._refusal_for(chat_id)
            if refusal is not None:
                return refusal
            msg = self.store.append("incoming", text, sent_at)
            self.hub.broadcast(message_frame(msg))
            return None

    async def on_non_text(self, chat_id: int) -> str | None:
        async with self._lock:
            return self._refusal_for(chat_id) or REPLY_TEXT_ONLY

    # ---- helpers ----------------------------------------------------------------

    def _refusal_for(self, chat_id: int) -> str | None:
        """Reply for a non-owner, or None if chat_id owns the session."""
        if self.session.owner is None:
            return REPLY_SEND_START
        if not self.session.is_owner(chat_id):
            return REPLY_BUSY
        return None

    def _broadcast_status(self) -> None:
        self.hub.broadcast(status_frame(self.status()))
