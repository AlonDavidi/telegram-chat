"""In-memory message history for the current session.

Not thread/task safe on its own: ChatService serializes every call under its lock.
"""

import uuid
from datetime import datetime

from app.models import Direction, Message


class MessageStore:
    def __init__(self) -> None:
        self._session_id: str | None = None
        self._messages: list[Message] = []
        # Process-wide counter: never reset, so seq is strictly increasing for
        # the life of the process even across sessions.
        self._last_seq = 0

    @property
    def session_id(self) -> str | None:
        return self._session_id

    def reset(self, session_id: str) -> None:
        self._session_id = session_id
        self._messages = []

    def append(
        self,
        direction: Direction,
        text: str,
        timestamp: datetime,
        client_id: str | None = None,
    ) -> Message:
        if self._session_id is None:
            raise RuntimeError("cannot store a message without an active session")
        self._last_seq += 1
        msg = Message(
            seq=self._last_seq,
            id=uuid.uuid4().hex,
            session_id=self._session_id,
            direction=direction,
            text=text,
            timestamp=timestamp,
            client_id=client_id,
        )
        self._messages.append(msg)
        return msg

    def since(self, last_seq: int) -> list[Message]:
        return [m for m in self._messages if m.seq > last_seq]

    def all(self) -> list[Message]:
        return list(self._messages)
