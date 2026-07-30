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
