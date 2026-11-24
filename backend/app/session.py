"""Single-participant session rules (pure logic, no I/O).

At most one Telegram private chat owns the bot at a time. /start claims a free
bot, /stop releases it (owner only). The latest session_id is kept after release
so browsers can still identify the history they are showing.
"""

import uuid
from enum import Enum

from app.models import Participant, Status


class ClaimResult(Enum):
    CLAIMED = "claimed"
    ALREADY_OWNER = "already_owner"
    BUSY = "busy"


class ReleaseResult(Enum):
    RELEASED = "released"
    NOT_CONNECTED = "not_connected"
    BUSY = "busy"


class SessionState:
    def __init__(self) -> None:
        self.owner: Participant | None = None
        self.session_id: str | None = None

    def is_owner(self, chat_id: int) -> bool:
        return self.owner is not None and self.owner.chat_id == chat_id

    def claim(self, participant: Participant) -> ClaimResult:
        if self.owner is None:
            self.owner = participant
            self.session_id = uuid.uuid4().hex
            return ClaimResult.CLAIMED
        if self.is_owner(participant.chat_id):
            return ClaimResult.ALREADY_OWNER
        return ClaimResult.BUSY

    def release(self, chat_id: int) -> ReleaseResult:
        if self.owner is None:
            return ReleaseResult.NOT_CONNECTED
        if not self.is_owner(chat_id):
            return ReleaseResult.BUSY
        self.owner = None
        return ReleaseResult.RELEASED

    def status(self) -> Status:
        return Status(
            connected=self.owner is not None,
            session_id=self.session_id,
            participant=self.owner,
        )
