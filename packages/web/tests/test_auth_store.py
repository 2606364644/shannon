from supernova_web.auth.models import User, SessionRow
from supernova_web.auth.store import AuthStore


def test_init_schema_idempotent(tmp_path):
    db = tmp_path / "auth.db"
    s = AuthStore(str(db))
    s.init_schema()
    s.init_schema()  # 再次建表不报错
    assert db.exists()


def test_create_and_get_user(tmp_path):
    s = AuthStore(str(tmp_path / "auth.db")); s.init_schema()
    u = s.create_user("alice", "$2b$12$hash", role="user")
    assert u.id is not None and u.username == "alice" and u.role == "user"
    got = s.get_user_by_username("alice")
    assert got is not None and got.id == u.id
    assert s.get_user_by_username("nobody") is None
    assert s.get_user(u.id).username == "alice"


def test_get_password_hash(tmp_path):
    s = AuthStore(str(tmp_path / "auth.db")); s.init_schema()
    s.create_user("alice", "$2b$12$xxx")
    assert s.get_password_hash("alice") == "$2b$12$xxx"
    assert s.get_password_hash("nobody") is None


def test_username_unique(tmp_path):
    import pytest
    s = AuthStore(str(tmp_path / "auth.db")); s.init_schema()
    s.create_user("alice", "h")
    with pytest.raises(Exception):
        s.create_user("alice", "h")


def test_session_crud_and_purge(tmp_path):
    s = AuthStore(str(tmp_path / "auth.db")); s.init_schema()
    u = s.create_user("alice", "h")
    s.insert_session(SessionRow(id="sid-1", user_id=u.id, expires_at="2099-01-01T00:00:00", last_seen_at="2026-01-01T00:00:00"))
    s.insert_session(SessionRow(id="sid-old", user_id=u.id, expires_at="2020-01-01T00:00:00", last_seen_at="2019-01-01T00:00:00"))
    assert s.get_session("sid-1").user_id == u.id
    assert s.get_session("missing") is None
    s.update_session_seen("sid-1", "2026-06-01T00:00:00")
    n = s.purge_expired("2026-01-01T00:00:00")
    assert n == 1  # sid-old 过期被删
    assert s.get_session("sid-old") is None
    s.delete_session("sid-1")
    assert s.get_session("sid-1") is None
