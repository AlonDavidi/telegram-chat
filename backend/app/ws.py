"""The /ws endpoint: parses and validates browser frames, delegates to ChatService.

After the hello handshake every frame to this socket goes through the hub's
per-connection queue, so there is exactly one writer per socket.
"""

import json
import logging

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
    await websocket.accept()

    try:
        hello = _parse_hello(await websocket.receive_text())
    except WebSocketDisconnect:
        return
    if hello is None:
        await websocket.send_json(error_frame("BAD_REQUEST", "The first frame must be a hello frame."))
        await websocket.close(code=CLOSE_POLICY_VIOLATION)
        return

    conn = await service.connect(websocket, hello)
    try:
        while True:
            raw = await websocket.receive_text()
            reply = await _handle_frame(service, raw)
            if reply is not None:
                service.hub.send(conn, reply)
    except WebSocketDisconnect:
        pass
    finally:
        await service.disconnect(conn)


def _parse_hello(raw: str) -> HelloFrame | None:
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
