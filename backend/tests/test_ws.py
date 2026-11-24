from datetime import datetime, timezone

import pytest
from starlette.websockets import WebSocketDisconnect

from app.models import Participant

A = Participant(chat_id=1, first_name="A", username="a")
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
EMPTY_STATUS = {"connected": False, "session_id": None, "participant": None}


def _hello(ws, **kw):
    ws.send_json({"type": "hello", **kw})
    return ws.receive_json()


def _claim(client):
    service = client.app.state.service
    client.portal.call(service.on_start, A)
    return service


def test_health_and_status(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/status").json() == EMPTY_STATUS


def test_lifespan_starts_and_attaches_gateway(client):
    service = client.app.state.service
    assert service.gateway.started is True
    assert service.gateway.service is service


def test_hello_snapshot(client):
    with client.websocket_connect("/ws") as ws:
        assert _hello(ws) == {"type": "snapshot", "status": EMPTY_STATUS, "messages": []}


def test_first_frame_must_be_hello(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "send", "client_id": "c1", "text": "hi"})
        assert ws.receive_json()["code"] == "BAD_REQUEST"
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
        assert e.value.code == 1008


def test_garbage_after_hello_keeps_connection(client):
    with client.websocket_connect("/ws") as ws:
        _hello(ws)
        ws.send_text("not json")
        assert ws.receive_json()["code"] == "BAD_REQUEST"
        ws.send_json({"type": "x"})
        assert ws.receive_json()["code"] == "BAD_REQUEST"
        ws.send_json(["a", "list"])
        assert ws.receive_json()["code"] == "BAD_REQUEST"
        ws.send_json({"type": "hello"})  # second hello
        assert ws.receive_json()["code"] == "BAD_REQUEST"
        ws.send_json({"type": "send", "client_id": "c1", "text": "hi"})
        assert ws.receive_json() == {
            "type": "error",
            "client_id": "c1",
            "code": "NO_PARTICIPANT",
            "detail": "No Telegram user is connected. Ask them to send /start.",
        }


@pytest.mark.parametrize("text", ["   ", "x" * 4097])
def test_invalid_text(client, text):
    _claim(client)
    with client.websocket_connect("/ws") as ws:
        _hello(ws)
        ws.send_json({"type": "send", "client_id": "c1", "text": text})
        frame = ws.receive_json()
        assert frame["code"] == "INVALID_MESSAGE" and frame["client_id"] == "c1"


def test_send_broadcasts_to_two_tabs(client):
    service = _claim(client)
    with client.websocket_connect("/ws") as tab1, client.websocket_connect("/ws") as tab2:
        _hello(tab1)
        _hello(tab2)
        tab1.send_json({"type": "send", "client_id": "c1", "text": "שלום 🙂"})
        f1, f2 = tab1.receive_json(), tab2.receive_json()
        assert f1 == f2
        assert f1["type"] == "message"
        assert f1["message"]["client_id"] == "c1"
        assert f1["message"]["direction"] == "outgoing"
        assert f1["message"]["text"] == "שלום 🙂"
    assert service.gateway.sent == [(1, "שלום 🙂")]


def test_resume_delta(client):
    service = _claim(client)
    sid = service.status().session_id
    for t in ("a", "b"):
        client.portal.call(service.on_text, 1, t, NOW)
    with client.websocket_connect("/ws") as ws:
        snap = _hello(ws, session_id=sid, last_seq=1)
        assert [m["text"] for m in snap["messages"]] == ["b"]
        assert snap["status"]["participant"] == {"first_name": "A", "username": "a"}


def test_binary_frame_before_hello_is_bad_request(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_bytes(b"\x00\x01")
        assert ws.receive_json()["code"] == "BAD_REQUEST"
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
        assert e.value.code == 1008


def test_binary_frame_after_hello_keeps_connection(client):
    with client.websocket_connect("/ws") as ws:
        _hello(ws)
        ws.send_bytes(b"\x00\x01")
        assert ws.receive_json()["code"] == "BAD_REQUEST"
        ws.send_json({"type": "send", "client_id": "c1", "text": "hi"})
        assert ws.receive_json()["code"] == "NO_PARTICIPANT"


def test_foreign_origin_is_rejected(client):
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/ws", headers={"origin": "https://evil.example"}) as ws:
            ws.send_json({"type": "hello"})
            ws.receive_json()
    assert e.value.code == 1008


@pytest.mark.parametrize(
    "origin",
    [
        "http://testserver",        # same origin as the Host header (Vite/nginx proxy case)
        "http://localhost:5173",    # listed in cors_origins
    ],
)
def test_allowed_origins_connect(client, origin):
    with client.websocket_connect("/ws", headers={"origin": origin}) as ws:
        assert _hello(ws)["type"] == "snapshot"
