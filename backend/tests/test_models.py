from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models import (
    Message,
    Participant,
    Status,
    error_frame,
    message_frame,
    parse_client_frame,
    snapshot_frame,
    status_frame,
)


def test_send_frame_strips_and_accepts_4096_hebrew_emoji():
    text = ("שלום🙂" * 820)[:4096]
    assert len(text) == 4096
    f = parse_client_frame({"type": "send", "client_id": "c1", "text": f"  {text}  "})
    assert f.text == text


@pytest.mark.parametrize("text", ["", "   ", "x" * 4097])
def test_send_frame_rejects_invalid_text(text):
    with pytest.raises(ValidationError):
        parse_client_frame({"type": "send", "client_id": "c1", "text": text})


def test_send_frame_requires_client_id():
    with pytest.raises(ValidationError):
        parse_client_frame({"type": "send", "client_id": "", "text": "hi"})


def test_unknown_type_rejected():
    with pytest.raises(ValidationError):
        parse_client_frame({"type": "nope"})


def test_non_dict_rejected():
    with pytest.raises(ValidationError):
        parse_client_frame(["hello"])


def test_hello_defaults():
    f = parse_client_frame({"type": "hello"})
    assert f.session_id is None and f.last_seq == 0


def test_hello_rejects_negative_last_seq():
    with pytest.raises(ValidationError):
        parse_client_frame({"type": "hello", "last_seq": -1})


def test_status_hides_chat_id_and_allows_no_username():
    s = Status(
        connected=True,
        session_id="s1",
        participant=Participant(chat_id=42, first_name="Dana", username=None),
    )
    assert status_frame(s) == {
        "type": "status",
        "status": {
            "connected": True,
            "session_id": "s1",
            "participant": {"first_name": "Dana", "username": None},
        },
    }


def _msg(**kw):
    base = dict(
        seq=1,
        id="m1",
        session_id="s1",
        direction="incoming",
        text="hi",
        timestamp=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
    )
    base.update(kw)
    return Message(**base)


def test_message_frame_is_json_ready_utc():
    frame = message_frame(_msg())
    assert frame["type"] == "message"
    assert frame["message"]["timestamp"].startswith("2026-01-01T12:00:00")
    assert frame["message"]["client_id"] is None


def test_snapshot_frame():
    s = Status(connected=False, session_id=None, participant=None)
    frame = snapshot_frame(s, [_msg()])
    assert frame["type"] == "snapshot"
    assert frame["status"] == {"connected": False, "session_id": None, "participant": None}
    assert frame["messages"][0]["seq"] == 1


def test_error_frame():
    assert error_frame("NO_PARTICIPANT", "x", "c1") == {
        "type": "error",
        "client_id": "c1",
        "code": "NO_PARTICIPANT",
        "detail": "x",
    }
