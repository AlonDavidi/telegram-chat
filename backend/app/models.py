"""Domain models and the WebSocket protocol (see README: "WebSocket protocol")."""

from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter, field_validator

MAX_TEXT_LENGTH = 4096  # Telegram's limit for a text message

Direction = Literal["incoming", "outgoing"]
ErrorCode = Literal["NO_PARTICIPANT", "INVALID_MESSAGE", "TELEGRAM_ERROR", "BAD_REQUEST"]


class Participant(BaseModel):
    chat_id: int = Field(exclude=True)  # internal; never sent to the browser
    first_name: str
    username: str | None = None


class Message(BaseModel):
    seq: int
    id: str
    session_id: str
    direction: Direction
    text: str
    timestamp: datetime
    client_id: str | None = None


class Status(BaseModel):
    connected: bool
    session_id: str | None
    participant: Participant | None


# ---- client -> server frames -------------------------------------------------


class HelloFrame(BaseModel):
    type: Literal["hello"]
    session_id: str | None = None
    last_seq: int = Field(0, ge=0)


class SendFrame(BaseModel):
    type: Literal["send"]
    client_id: str = Field(min_length=1, max_length=64)
    text: str

    @field_validator("text")
    @classmethod
    def _clean_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message text is empty")
        if len(value) > MAX_TEXT_LENGTH:
            raise ValueError(f"message text exceeds {MAX_TEXT_LENGTH} characters")
        return value


ClientFrame = Annotated[Union[HelloFrame, SendFrame], Field(discriminator="type")]
_client_frame_adapter: TypeAdapter[HelloFrame | SendFrame] = TypeAdapter(ClientFrame)


def parse_client_frame(data: object) -> HelloFrame | SendFrame:
    """Validate a decoded JSON frame. Raises pydantic.ValidationError."""
    return _client_frame_adapter.validate_python(data)


# ---- server -> client frames -------------------------------------------------


def snapshot_frame(status: Status, messages: list[Message]) -> dict:
    return {
        "type": "snapshot",
        "status": status.model_dump(mode="json"),
        "messages": [m.model_dump(mode="json") for m in messages],
    }


def message_frame(msg: Message) -> dict:
    return {"type": "message", "message": msg.model_dump(mode="json")}


def status_frame(status: Status) -> dict:
    return {"type": "status", "status": status.model_dump(mode="json")}


def error_frame(code: ErrorCode, detail: str, client_id: str | None = None) -> dict:
    return {"type": "error", "client_id": client_id, "code": code, "detail": detail}
