"""The /ws endpoint: parses and validates browser frames, delegates to ChatService.

After the hello handshake every frame to this socket goes through the hub's
per-connection queue, so there is exactly one writer per socket.
"""

import json
import logging
from urllib.parse import urlparse

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.models import HelloFrame, SendFrame, error_frame, parse_client_frame
from app.service import ChatError, ChatService

logger = logging.getLogger(__name__)

router = APIRouter()

CLOSE_POLICY_VIOLATION = 1008


@router.websocket("/ws")
async def chat_socket(websocket: WebSocket) -> None:
    service: ChatService = websocket.app.state.service
    # CORS does not apply to WebSockets: without this check any web page open
    # in the user's browser could drive the bot.
    if not _origin_allowed(websocket, websocket.app.state.settings.cors_origins):
        await websocket.close(code=CLOSE_POLICY_VIOLATION)
        return
    await websocket.accept()

    try:
        hello = _parse_hello(await _receive_text(websocket))
    except WebSocketDisconnect:
        return
    if hello is None:
        await websocket.send_json(error_frame("BAD_REQUEST", "The first frame must be a hello frame."))
        await websocket.close(code=CLOSE_POLICY_VIOLATION)
        return

    conn = await service.connect(websocket, hello)
    try:
        while True:
            raw = await _receive_text(websocket)
            reply = (
                error_frame("BAD_REQUEST", "Frames must be JSON text.")
                if raw is None
                else await _handle_frame(service, raw)
            )
            if reply is not None:
                service.hub.send(conn, reply)
    except WebSocketDisconnect:
        pass
    finally:
        await service.disconnect(conn)


def _origin_allowed(websocket: WebSocket, allowed: list[str]) -> bool:
    origin = websocket.headers.get("origin")
    if origin is None:  # not a browser (curl, scripts): no cross-site risk
        return True
    if origin in allowed:
        return True
    return urlparse(origin).netloc == websocket.headers.get("host")


async def _receive_text(websocket: WebSocket) -> str | None:
    """Next text frame, or None for a binary frame."""
    message = await websocket.receive()
    if message["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(message.get("code", 1000))
    return message.get("text")


def _parse_hello(raw: str | None) -> HelloFrame | None:
    if raw is None:
        return None
    try:
        frame = parse_client_frame(json.loads(raw))
    except (ValueError, ValidationError):  # JSONDecodeError is a ValueError
        return None
    return frame if isinstance(frame, HelloFrame) else None


async def _handle_frame(service: ChatService, raw: str) -> dict | None:
    """Process one frame after the handshake; return an error frame for this socket, if any."""
    try:
        data = json.loads(raw)
    except ValueError:
        return error_frame("BAD_REQUEST", "Frames must be JSON objects.")
    if not isinstance(data, dict):
        return error_frame("BAD_REQUEST", "Frames must be JSON objects.")

    client_id = data.get("client_id") if isinstance(data.get("client_id"), str) else None
    try:
        frame = parse_client_frame(data)
    except ValidationError:
        if data.get("type") == "send":
            return error_frame(
                "INVALID_MESSAGE", "Message must be 1-4096 characters after trimming.", client_id
            )
        return error_frame("BAD_REQUEST", "Unknown or malformed frame.")

    if not isinstance(frame, SendFrame):
        return error_frame("BAD_REQUEST", "Already said hello.")

    try:
        await service.send(frame.client_id, frame.text)
    except ChatError as exc:
        return error_frame(exc.code, exc.detail, frame.client_id)
    return None  # success is the broadcast message frame
