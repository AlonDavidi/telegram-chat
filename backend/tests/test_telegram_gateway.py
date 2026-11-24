from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import telegram.error
from telegram import Update, User

from app.service import GatewayError, GatewayForbidden
from app.telegram_gateway import TelegramGateway, participant_from

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
TOKEN = "123456:TEST-token"


class RecordingService:
    def __init__(self, reply=None):
        self.calls = []
        self.reply = reply

    async def on_start(self, p):
        self.calls.append(("start", p))
        return self.reply

    async def on_stop(self, chat_id):
        self.calls.append(("stop", chat_id))
        return self.reply

    async def on_text(self, chat_id, text, sent_at):
        self.calls.append(("text", chat_id, text, sent_at))
        return self.reply

    async def on_non_text(self, chat_id):
        self.calls.append(("non_text", chat_id))
        return self.reply


def _gateway(reply=None):
    gw = TelegramGateway(TOKEN)
    service = RecordingService(reply)
    gw.attach(service)
    # CommandHandler needs bot.username, which PTB normally fetches via getMe
    # in initialize(); give the offline bot an identity instead.
    gw._app.bot._bot_user = User(id=1, first_name="Bot", is_bot=True, username="test_bot")
    return gw, service


def _fake_update(text="hi", username="dana"):
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=42),
        effective_user=SimpleNamespace(first_name="Dana", username=username),
        message=SimpleNamespace(text=text, date=NOW, reply_text=AsyncMock()),
    )


def _real_update(gw, chat_type="private", **message):
    data = {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "date": 1767268800,
            "chat": {"id": 42 if chat_type == "private" else -100, "type": chat_type},
            "from": {"id": 42, "is_bot": False, "first_name": "Dana"},
            **message,
        },
    }
    return Update.de_json(data, gw._app.bot)


def _routed_to(gw, update):
    for handler in gw._app.handlers[0]:
        if handler.check_update(update) not in (None, False):
            return handler.callback.__name__
    return None


# ---- routing ------------------------------------------------------------------


def test_routing_private_chat():
    gw, _ = _gateway()
    cmd = lambda t: [{"type": "bot_command", "offset": 0, "length": len(t)}]  # noqa: E731
    assert _routed_to(gw, _real_update(gw, text="/start", entities=cmd("/start"))) == "_on_start"
    assert _routed_to(gw, _real_update(gw, text="/stop", entities=cmd("/stop"))) == "_on_stop"
    assert _routed_to(gw, _real_update(gw, text="hello")) == "_on_text"
    assert _routed_to(gw, _real_update(gw, text="/help", entities=cmd("/help"))) == "_on_text"
    sticker = {"file_id": "f", "file_unique_id": "u", "width": 1, "height": 1,
               "is_animated": False, "is_video": False, "type": "regular"}
    assert _routed_to(gw, _real_update(gw, sticker=sticker)) == "_on_non_text"


def test_group_chats_are_ignored():
    gw, _ = _gateway()
    assert _routed_to(gw, _real_update(gw, chat_type="group", text="hello")) is None
    assert _routed_to(gw, _real_update(gw, chat_type="group", text="/start",
                      entities=[{"type": "bot_command", "offset": 0, "length": 6}])) is None


# ---- handlers -----------------------------------------------------------------


async def test_start_handler_replies():
    gw, service = _gateway(reply="Connected.")
    upd = _fake_update()
    await gw._on_start(upd, None)
    kind, participant = service.calls[0]
    assert kind == "start" and participant.chat_id == 42 and participant.first_name == "Dana"
    upd.message.reply_text.assert_awaited_once_with("Connected.")


async def test_stop_handler():
    gw, service = _gateway(reply="Disconnected.")
    upd = _fake_update()
    await gw._on_stop(upd, None)
    assert service.calls == [("stop", 42)]
    upd.message.reply_text.assert_awaited_once_with("Disconnected.")


async def test_text_handler_forwards_date_and_skips_empty_reply():
    gw, service = _gateway(reply=None)
    upd = _fake_update(text="hello")
    await gw._on_text(upd, None)
    assert service.calls == [("text", 42, "hello", NOW)]
    upd.message.reply_text.assert_not_awaited()


async def test_non_text_handler():
    gw, service = _gateway(reply="Only text messages are supported.")
    upd = _fake_update()
    await gw._on_non_text(upd, None)
    assert service.calls == [("non_text", 42)]
    upd.message.reply_text.assert_awaited_once()


def test_participant_without_username():
    assert participant_from(_fake_update(username=None)).username is None


# ---- sending ------------------------------------------------------------------


def _with_send(gw, **kw):
    gw._send_message = AsyncMock(**kw)
    return gw._send_message


async def test_send_text_returns_telegram_date():
    gw, _ = _gateway()
    send = _with_send(gw, return_value=SimpleNamespace(date=NOW))
    assert await gw.send_text(42, "hi") == NOW
    send.assert_awaited_once_with(chat_id=42, text="hi")


@pytest.mark.parametrize(
    "exc, expected",
    [
        (telegram.error.Forbidden("blocked"), GatewayForbidden),
        (telegram.error.TimedOut(), GatewayError),
        (telegram.error.NetworkError("down"), GatewayError),
        (telegram.error.BadRequest("chat not found"), GatewayError),
    ],
)
async def test_send_text_maps_errors(exc, expected):
    gw, _ = _gateway()
    _with_send(gw, side_effect=exc)
    with pytest.raises(expected):
        await gw.send_text(42, "hi")
