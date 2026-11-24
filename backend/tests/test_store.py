from datetime import datetime, timezone

import pytest

from app.store import MessageStore

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_append_requires_session():
    with pytest.raises(RuntimeError):
        MessageStore().append("incoming", "hi", NOW)


def test_seq_increases_and_since():
    s = MessageStore()
    s.reset("s1")
    a = s.append("incoming", "a", NOW)
    b = s.append("outgoing", "b", NOW, client_id="c1")
    assert (a.seq, b.seq) == (1, 2)
    assert b.client_id == "c1" and a.session_id == "s1"
    assert a.id != b.id
    assert [m.seq for m in s.since(1)] == [2]
    assert [m.seq for m in s.since(0)] == [1, 2]
    assert [m.seq for m in s.all()] == [1, 2]


def test_reset_clears_but_seq_keeps_growing():
    s = MessageStore()
    s.reset("s1")
    s.append("incoming", "a", NOW)
    s.reset("s2")
    assert s.all() == [] and s.session_id == "s2"
    assert s.append("incoming", "b", NOW).seq == 2


def test_new_store_has_no_session():
    assert MessageStore().session_id is None
