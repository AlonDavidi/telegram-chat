import asyncio
from datetime import datetime, timezone

import pytest

from app.models import HelloFrame, Participant
from app.service import (
    REPLY_ALREADY_CONNECTED,
    REPLY_BUSY,
    REPLY_CONNECTED,
    REPLY_DISCONNECTED,
    REPLY_NOT_CONNECTED,
    REPLY_SEND_START,
    REPLY_TEXT_ONLY,
    ChatError,
    GatewayError,
    GatewayForbidden,
)
from tests.conftest import FakeWebSocket, drain

A = Participant(chat_id=1, first_name="A", username="a")
B = Participant(chat_id=2, first_name="B", username=None)
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_reply_copy_is_exact():
    assert REPLY_CONNECTED == "Connected. Messages you send here will appear in the web chat."
    assert REPLY_ALREADY_CONNECTED == "You're already connected."
    assert REPLY_BUSY == "This bot is already in a conversation with someone else. Try again later."
    assert REPLY_NOT_CONNECTED == "You're not connected. Send /start to connect."
    assert REPLY_DISCONNECTED == "Disconnected."
    assert REPLY_SEND_START == "Send /start to connect."
    assert REPLY_TEXT_ONLY == "Only text messages are supported."


async def test_send_without_participant(service):
    with pytest.raises(ChatError) as e:
        await service.send("c1", "hi")
    assert e.value.code == "NO_PARTICIPANT"


# ---- spec 6.2 session table ---------------------------------------------------


async def test_table_bot_free(service):
    assert await service.on_stop(1) == REPLY_NOT_CONNECTED
    assert await service.on_text(1, "hi", NOW) == REPLY_SEND_START
    assert await service.on_non_text(1) == REPLY_SEND_START
    assert service.store.all() == []
    assert await service.on_start(A) == REPLY_CONNECTED
    assert service.status().connected is True


async def test_table_sender_is_owner(service):
    await service.on_start(A)
    assert await service.on_start(A) == REPLY_ALREADY_CONNECTED
    assert await service.on_text(1, "hello", NOW) is None
    assert [(m.direction, m.text, m.timestamp) for m in service.store.all()] == [
        ("incoming", "hello", NOW)
    ]
    assert await service.on_non_text(1) == REPLY_TEXT_ONLY
    assert await service.on_stop(1) == REPLY_DISCONNECTED
    assert service.status().connected is False


async def test_table_bot_busy(service):
    await service.on_start(A)
    assert await service.on_start(B) == REPLY_BUSY
    assert await service.on_stop(2) == REPLY_BUSY
    assert await service.on_text(2, "sneaky", NOW) == REPLY_BUSY
    assert await service.on_non_text(2) == REPLY_BUSY
    assert service.store.all() == []
    assert service.status().participant.chat_id == 1


async def test_concurrent_claims_one_winner(service):
    replies = await asyncio.gather(
        *(service.on_start(Participant(chat_id=i, first_name=str(i))) for i in range(10))
    )
    assert replies.count(REPLY_CONNECTED) == 1
    assert replies.count(REPLY_BUSY) == 9


async def test_new_claim_resets_history_release_keeps_it(service):
    await service.on_start(A)
    await service.on_text(1, "old", NOW)
    first_session = service.status().session_id
    await service.on_stop(1)
    assert [m.text for m in service.store.all()] == ["old"]  # kept after /stop
    await service.on_start(B)
    assert service.store.all() == []
    assert service.status().session_id != first_session


# ---- outgoing -----------------------------------------------------------------


async def test_send_success_stores_and_targets_owner(service, gateway):
    await service.on_start(A)
    msg = await service.send("c1", "שלום 🙂")
    assert gateway.sent == [(1, "שלום 🙂")]
    assert msg.direction == "outgoing" and msg.client_id == "c1" and msg.text == "שלום 🙂"
    assert service.store.all() == [msg]


async def test_outgoing_stored_only_after_gateway_success(service, gateway):
    await service.on_start(A)
    gateway.fail_with = GatewayError("boom")
    with pytest.raises(ChatError) as e:
        await service.send("c1", "hi")
    assert e.value.code == "TELEGRAM_ERROR"
    assert service.store.all() == []
    assert service.status().connected is True


async def test_forbidden_releases_session(service, gateway):
    await service.on_start(A)
    gateway.fail_with = GatewayForbidden("blocked")
    with pytest.raises(ChatError) as e:
        await service.send("c1", "hi")
    assert e.value.code == "TELEGRAM_ERROR"
    assert service.status().connected is False


async def test_concurrent_sends_keep_order(service, gateway):
    await service.on_start(A)
    gateway.delay = 0.01
    await asyncio.gather(*(service.send(f"c{i}", f"m{i}") for i in range(5)))
    stored = service.store.all()
    assert [m.text for m in stored] == [t for _, t in gateway.sent]
    assert [m.seq for m in stored] == sorted(m.seq for m in stored)


async def test_stop_cannot_interleave_with_send(service, gateway):
    await service.on_start(A)
    gateway.delay = 0.01
    send = asyncio.create_task(service.send("c1", "hi"))
    await asyncio.sleep(0)  # send now holds the lock
    assert await service.on_stop(1) == REPLY_DISCONNECTED
    msg = await send
    assert gateway.sent == [(1, "hi")] and msg.client_id == "c1"


# ---- browsers -----------------------------------------------------------------


async def _connect(service, **hello):
    ws = FakeWebSocket()
    conn = await service.connect(ws, HelloFrame(type="hello", **hello))
    await drain()
    return ws, conn


async def test_connect_before_any_claim(service):
    ws, _ = await _connect(service)
    assert ws.sent == [
        {
            "type": "snapshot",
            "status": {"connected": False, "session_id": None, "participant": None},
            "messages": [],
        }
    ]


async def test_connect_after_restart_with_stale_session(service):
    ws, _ = await _connect(service, session_id="old", last_seq=57)
    assert ws.sent[0]["type"] == "snapshot" and ws.sent[0]["messages"] == []
    assert ws.sent[0]["status"]["session_id"] is None


async def test_connect_resume_rules(service):
    await service.on_start(A)
    for t in ("a", "b", "c"):
        await service.on_text(1, t, NOW)
    sid = service.status().session_id

    ws, _ = await _connect(service, session_id=sid, last_seq=2)
    assert [m["text"] for m in ws.sent[0]["messages"]] == ["c"]

    ws, _ = await _connect(service, session_id="other", last_seq=2)
    assert [m["text"] for m in ws.sent[0]["messages"]] == ["a", "b", "c"]

    ws, _ = await _connect(service, last_seq=2)  # no session known → everything
    assert [m["text"] for m in ws.sent[0]["messages"]] == ["a", "b", "c"]


async def test_events_are_broadcast_after_snapshot(service, gateway):
    ws, _ = await _connect(service)
    await service.on_start(A)
    await service.on_text(1, "in", NOW)
    await service.send("c1", "out")
    await drain()
    assert [f["type"] for f in ws.sent] == ["snapshot", "status", "message", "message"]
    assert ws.sent[1]["status"]["participant"] == {"first_name": "A", "username": "a"}
    assert ws.sent[3]["message"]["client_id"] == "c1"
    assert ws.sent[2]["message"]["seq"] < ws.sent[3]["message"]["seq"]


async def test_forbidden_broadcasts_status(service, gateway):
    await service.on_start(A)
    ws, _ = await _connect(service)
    gateway.fail_with = GatewayForbidden("blocked")
    with pytest.raises(ChatError):
        await service.send("c1", "hi")
    await drain()
    assert ws.sent[-1] == {
        "type": "status",
        "status": {"connected": False, "session_id": service.status().session_id, "participant": None},
    }


async def test_disconnect_removes_connection(service):
    ws, conn = await _connect(service)
    await service.disconnect(conn)
    await service.on_start(A)
    await drain()
    assert len(ws.sent) == 1  # only the snapshot
