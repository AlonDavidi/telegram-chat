import pytest
from pydantic import ValidationError

from app.config import Settings


def test_missing_token_fails_fast(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_token_and_defaults(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    s = Settings(_env_file=None)
    assert s.telegram_bot_token.get_secret_value() == "123:abc"
    assert s.cors_origins == ["http://localhost:5173"]
    assert s.ws_queue_size == 100


def test_empty_token_fails_fast(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
