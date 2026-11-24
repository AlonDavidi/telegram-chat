from app.models import Participant
from app.session import ClaimResult, ReleaseResult, SessionState

A = Participant(chat_id=1, first_name="A", username="a")
B = Participant(chat_id=2, first_name="B", username=None)


def test_initial_status():
    st = SessionState().status()
    assert st.connected is False and st.session_id is None and st.participant is None


def test_claim_free():
    s = SessionState()
    assert s.claim(A) is ClaimResult.CLAIMED
    assert s.is_owner(1) and not s.is_owner(2) and s.session_id


def test_claim_again_same_owner_keeps_session():
    s = SessionState()
    s.claim(A)
    sid = s.session_id
    assert s.claim(A) is ClaimResult.ALREADY_OWNER and s.session_id == sid


def test_claim_busy():
    s = SessionState()
    s.claim(A)
    assert s.claim(B) is ClaimResult.BUSY and s.is_owner(1)


def test_release_rules():
    s = SessionState()
    assert s.release(1) is ReleaseResult.NOT_CONNECTED
    s.claim(A)
    assert s.release(2) is ReleaseResult.BUSY
    sid = s.session_id
    assert s.release(1) is ReleaseResult.RELEASED
    st = s.status()
    assert st.connected is False and st.session_id == sid and st.participant is None


def test_new_claim_after_release_gets_new_session():
    s = SessionState()
    s.claim(A)
    first = s.session_id
    s.release(1)
    assert s.claim(B) is ClaimResult.CLAIMED
    assert s.session_id != first
    assert s.status().participant.username is None
