from supernova_web.config import get_config


def test_auth_defaults(tmp_workspaces):
    cfg = get_config()
    assert cfg.session_ttl_hours == 12
    assert cfg.cookie_secure is True
    assert cfg.users_seed_file == "configs/users.yaml"
    assert str(cfg.auth_db_path).endswith("auth.db")


def test_auth_env_override(tmp_workspaces, monkeypatch):
    monkeypatch.setenv("SUPERNOVA_WEB_SESSION_TTL_HOURS", "48")
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    cfg = get_config()
    assert cfg.session_ttl_hours == 48
    assert cfg.cookie_secure is False
