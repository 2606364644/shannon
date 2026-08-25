"""SSO 数据层（spec 2026-08-25 §6）：迁移幂等 + whitelist/used_tickets/avatar + auth_provider。"""
from supernova_web.auth.store import AuthStore


def _store(tmp_workspaces):
    store = AuthStore(str(tmp_workspaces / "auth.db"))
    store.init_schema()
    return store


def test_init_schema_idempotent(tmp_workspaces):
    store = _store(tmp_workspaces)
    store.init_schema()  # 第二次不抛（新表 IF NOT EXISTS + 补列 OperationalError 吞）


def test_sso_whitelist_crud(tmp_workspaces):
    store = _store(tmp_workspaces)
    assert store.is_nick_whitelisted("niu") is False
    store.add_sso_whitelist("niu", "admin")
    store.add_sso_whitelist("niu", "admin")  # 幂等
    assert store.is_nick_whitelisted("niu") is True
    rows = store.get_sso_whitelist()
    assert len(rows) == 1 and rows[0][0] == "niu" and rows[0][1] == "admin"
    store.remove_sso_whitelist("niu")
    assert store.is_nick_whitelisted("niu") is False


def test_used_tickets(tmp_workspaces):
    store = _store(tmp_workspaces)
    assert store.is_ticket_used("t1") is False
    store.mark_ticket_used("t1")
    assert store.is_ticket_used("t1") is True
    # 惰性清理：now - 25h 之前的记录被删，24h 内保留
    from datetime import datetime, timedelta, timezone
    # 时钟无关：now 取真实时刻 +25h，使 t1（真实时刻标记）恰为「now-25h 之前」、
    # t2 落在 24h 窗口内（硬编码日期会在真实时钟越过该日 12:00Z 后永远无法清理 t1）
    now = datetime.now(timezone.utc) + timedelta(hours=25)
    assert store.purge_used_tickets(now.isoformat()) == 1
    store.mark_ticket_used("t2")
    assert store.purge_used_tickets((now - timedelta(hours=1)).isoformat()) == 0
    assert store.is_ticket_used("t2") is True


def test_create_user_with_sso_provider_and_avatar(tmp_workspaces):
    store = _store(tmp_workspaces)
    u = store.create_user("niu", "x" * 60, role="user", auth_provider="sso")
    assert u.auth_provider == "sso" and u.avatar_url is None
    got = store.get_user_by_username("niu")
    assert got.auth_provider == "sso"
    store.update_avatar(got.id, "https://cdn.test/a.png")
    assert store.get_user(got.id).avatar_url == "https://cdn.test/a.png"


def test_session_auth_method_roundtrip(tmp_workspaces):
    store = _store(tmp_workspaces)
    u = store.create_user("a", "x" * 60)
    from supernova_web.auth.session import SessionManager
    sm = SessionManager(store, ttl_hours=12)
    sid = sm.create(u.id, ttl_hours=24, auth_method="sso")
    row = store.get_session(sid)
    assert row.auth_method == "sso"


def test_whitelist_state_toggle(tmp_workspaces):
    """白名单运行时开关（Task 10）：单行状态表，无行默认开（存量库零回归）。"""
    store = _store(tmp_workspaces)
    assert store.get_whitelist_enabled() is True  # 无行默认开
    store.set_whitelist_enabled(False, "admin")
    assert store.get_whitelist_enabled() is False
    store.set_whitelist_enabled(True, "admin")
    assert store.get_whitelist_enabled() is True  # 幂等 upsert
