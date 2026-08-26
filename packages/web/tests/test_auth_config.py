import pytest

from supernova_web.config import get_config


def test_auth_defaults(tmp_workspaces):
    cfg = get_config()
    assert cfg.session_ttl_hours == 12
    # cookie_secure 默认 False：实际 secure 由 routes 按请求 scheme 判断
    # （生产 HTTPS 自动 + env=1 强制）。曾默认 True 致 http:// 下登录循环。
    assert cfg.cookie_secure is False
    assert cfg.users_seed_file == "configs/users.yaml"
    assert str(cfg.auth_db_path).endswith("auth.db")


def test_auth_env_override(tmp_workspaces, monkeypatch):
    monkeypatch.setenv("SUPERNOVA_WEB_SESSION_TTL_HOURS", "48")
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    cfg = get_config()
    assert cfg.session_ttl_hours == 48
    assert cfg.cookie_secure is False


def test_bootstrap_default_admin_config_defaults(tmp_workspaces):
    """默认开 bootstrap，账密 admin/123456（新环境开箱可登）。"""
    cfg = get_config()
    assert cfg.bootstrap_default_admin_enabled is True
    assert cfg.default_admin_username == "admin"
    assert cfg.default_admin_password == "123456"


def test_bootstrap_default_admin_config_env_override(tmp_workspaces, monkeypatch):
    """三个 env 可覆盖：开关、用户名、密码。"""
    monkeypatch.setenv("SUPERNOVA_WEB_BOOTSTRAP_DEFAULT_ADMIN", "0")
    monkeypatch.setenv("SUPERNOVA_WEB_DEFAULT_ADMIN_USERNAME", "root")
    monkeypatch.setenv("SUPERNOVA_WEB_DEFAULT_ADMIN_PASSWORD", "hunter2")
    cfg = get_config()
    assert cfg.bootstrap_default_admin_enabled is False
    assert cfg.default_admin_username == "root"
    assert cfg.default_admin_password == "hunter2"


# ---------- SSO config（spec 2026-08-25 §7）----------

def test_sso_defaults(monkeypatch):
    for k in ("SUPERNOVA_WEB_SSO_ENABLED", "SUPERNOVA_WEB_SSO_AUTH_DOMAIN",
              "SUPERNOVA_WEB_SSO_PUBLIC_BASE_URL", "SUPERNOVA_WEB_SSO_PASSPORT_BASE",
              "SUPERNOVA_WEB_SSO_SESSION_TTL_HOURS"):
        monkeypatch.delenv(k, raising=False)
    from supernova_web.config import WebConfig
    cfg = WebConfig()
    assert cfg.sso_enabled is False
    assert cfg.sso_passport_base == "https://passport.futuoa.com"
    assert cfg.sso_session_ttl_hours == 24


def test_sso_bad_env_no_longer_fail_fast(monkeypatch):
    """2026-08-26 语义迁移（spec 2026-08-26 §5/§6）:启动 fail-fast 已删——坏 env 不再崩
    WebConfig（原 RuntimeError 场景:enabled=1 缺 domain / passport 非 https）。env 种子保留
    原值,降级在 store.ensure_sso_config_seeded 种子时做（见 test_auth_sso_store 降级用例）。"""
    monkeypatch.setenv("SUPERNOVA_WEB_SSO_ENABLED", "1")
    monkeypatch.delenv("SUPERNOVA_WEB_SSO_AUTH_DOMAIN", raising=False)
    monkeypatch.setenv("SUPERNOVA_WEB_SSO_PASSPORT_BASE", "http://passport.test")
    from supernova_web.config import WebConfig
    cfg = WebConfig()  # 不抛
    assert cfg.sso_enabled is True  # WebConfig 保留 env 原值（种子降级在 store 层）


def test_sso_public_base_derivation(monkeypatch):
    monkeypatch.setenv("SUPERNOVA_WEB_SSO_ENABLED", "1")
    monkeypatch.setenv("SUPERNOVA_WEB_SSO_AUTH_DOMAIN", "codescan.test.local")
    monkeypatch.delenv("SUPERNOVA_WEB_SSO_PUBLIC_BASE_URL", raising=False)
    from supernova_web.config import WebConfig
    cfg = WebConfig()
    assert cfg.sso_enabled is True
    assert cfg.sso_public_base_url == "https://codescan.test.local"


def test_sso_public_base_override_and_trailing_slash(monkeypatch):
    monkeypatch.setenv("SUPERNOVA_WEB_SSO_ENABLED", "1")
    monkeypatch.setenv("SUPERNOVA_WEB_SSO_AUTH_DOMAIN", "d.local")
    monkeypatch.setenv("SUPERNOVA_WEB_SSO_PUBLIC_BASE_URL", "http://d.local:7878/")
    monkeypatch.setenv("SUPERNOVA_WEB_SSO_PASSPORT_BASE", "https://passport.test/")
    from supernova_web.config import WebConfig
    cfg = WebConfig()
    assert cfg.sso_public_base_url == "http://d.local:7878"
    assert cfg.sso_passport_base == "https://passport.test"
