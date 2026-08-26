"""SSO 数据层（spec 2026-08-25 §6）：迁移幂等 + whitelist/used_tickets/avatar + auth_provider。
2026-08-26 增：sso_config 运行时配置（spec 2026-08-26 §4/§5——种子/降级/get/update）。"""
from supernova_web.auth.models import SsoConfig
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


# ── sso_config 运行时配置（spec 2026-08-26 §4/§5）──────────────────────────────

def test_sso_config_seed_from_env_values(tmp_workspaces):
    """首启表空 + env 有值：种入 env 值（此后 env 失效，DB 是唯一真相）。"""
    store = _store(tmp_workspaces)
    store.ensure_sso_config_seeded(SsoConfig(enabled=True, auth_domain="codescan.futu5.com"))
    got = store.get_sso_config()
    assert got.enabled is True
    assert got.auth_domain == "codescan.futu5.com"


def test_sso_config_seed_defaults_when_no_env(tmp_workspaces):
    """首启表空 + 无 env：种默认值（关/passport 默认/24h）。"""
    store = _store(tmp_workspaces)
    store.ensure_sso_config_seeded()
    got = store.get_sso_config()
    assert got.enabled is False
    assert got.auth_domain == ""
    assert got.passport_base == "https://passport.futuoa.com"
    assert got.session_ttl_hours == 24


def test_sso_config_seed_idempotent(tmp_workspaces):
    """表非空不覆盖（种子一次性；admin 改过的配置不被重启冲掉）。"""
    store = _store(tmp_workspaces)
    store.ensure_sso_config_seeded(SsoConfig(enabled=True, auth_domain="a.com"))
    store.update_sso_config(SsoConfig(enabled=False, auth_domain="b.com"), "admin")
    store.ensure_sso_config_seeded(SsoConfig(enabled=True, auth_domain="a.com"))  # 二次种子:不生效
    got = store.get_sso_config()
    assert got.enabled is False
    assert got.auth_domain == "b.com"


def test_sso_config_seed_bad_env_enabled_without_domain_degrades(tmp_workspaces):
    """坏 env 降级①:enabled=1 缺 auth_domain（旧版启动 fail-fast 场景）→ 种 enabled=0 不崩溃。"""
    store = _store(tmp_workspaces)
    store.ensure_sso_config_seeded(SsoConfig(enabled=True, auth_domain=""))
    got = store.get_sso_config()
    assert got.enabled is False


def test_sso_config_seed_bad_env_passport_scheme_degrades(tmp_workspaces):
    """坏 env 降级②:passport_base 非 https → 种默认 passport 基址。"""
    store = _store(tmp_workspaces)
    store.ensure_sso_config_seeded(SsoConfig(passport_base="http://passport.futuoa.com"))
    assert store.get_sso_config().passport_base == "https://passport.futuoa.com"


def test_sso_config_get_before_seed_returns_defaults(tmp_workspaces):
    """防御:老库升级后 seed 未跑时 get 不崩,回落默认值(不落库)。"""
    store = _store(tmp_workspaces)
    got = store.get_sso_config()
    assert got.enabled is False
    assert got.session_ttl_hours == 24


def test_sso_config_update_writes_audit(tmp_workspaces):
    """update 全量覆写 5 项并记 updated_at/updated_by。"""
    store = _store(tmp_workspaces)
    store.ensure_sso_config_seeded()
    store.update_sso_config(
        SsoConfig(enabled=True, auth_domain="x.com", public_base_url="http://x.com",
                  passport_base="https://pp.example.com", session_ttl_hours=48),
        "boss",
    )
    got = store.get_sso_config()
    assert got.enabled is True
    assert got.auth_domain == "x.com"
    assert got.public_base_url == "http://x.com"
    assert got.passport_base == "https://pp.example.com"
    assert got.session_ttl_hours == 48
    assert got.updated_by == "boss"
    assert got.updated_at != ""
