from supernova_web.auth.store import AuthStore
from supernova_web.auth.session import SessionManager


def _store(tmp_path):
    s = AuthStore(str(tmp_path / "auth.db")); s.init_schema()
    s.create_user("alice", "h")
    return s


def test_create_and_verify(tmp_path):
    s = _store(tmp_path)
    m = SessionManager(s, ttl_hours=12)
    sid = m.create(user_id=1)
    assert isinstance(sid, str) and len(sid) > 20
    user = m.verify(sid)
    assert user is not None and user.username == "alice"


def test_verify_unknown_returns_none(tmp_path):
    m = SessionManager(_store(tmp_path))
    assert m.verify("nope") is None


def test_expired_session_invalid(tmp_path):
    s = _store(tmp_path)
    m = SessionManager(s, ttl_hours=-1)  # 已过期
    sid = m.create(user_id=1)
    assert m.verify(sid) is None


def test_revoke(tmp_path):
    s = _store(tmp_path)
    m = SessionManager(s)
    sid = m.create(user_id=1)
    m.revoke(sid)
    assert m.verify(sid) is None


def test_purge_expired(tmp_path):
    s = _store(tmp_path)
    SessionManager(s, ttl_hours=-1).create(user_id=1)  # 过期
    m = SessionManager(s)
    assert m.purge_expired() == 1
