from shannon_web.config import get_config


def test_frontend_dir_defaults_none(monkeypatch):
    monkeypatch.delenv("SHANNON_WEB_FRONTEND_DIR", raising=False)
    get_config.cache_clear()
    cfg = get_config()
    assert cfg.frontend_dir is None


def test_frontend_dir_reads_env(monkeypatch):
    monkeypatch.setenv("SHANNON_WEB_FRONTEND_DIR", "/tmp/fe-dist")
    get_config.cache_clear()
    cfg = get_config()
    assert cfg.frontend_dir == "/tmp/fe-dist"
