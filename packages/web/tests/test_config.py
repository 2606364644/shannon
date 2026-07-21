import shutil

from supernova_web.config import get_config


def test_frontend_dir_defaults_none(monkeypatch):
    monkeypatch.delenv("SUPERNOVA_WEB_FRONTEND_DIR", raising=False)
    get_config.cache_clear()
    cfg = get_config()
    assert cfg.frontend_dir is None


def test_frontend_dir_reads_env(monkeypatch):
    monkeypatch.setenv("SUPERNOVA_WEB_FRONTEND_DIR", "/tmp/fe-dist")
    get_config.cache_clear()
    cfg = get_config()
    assert cfg.frontend_dir == "/tmp/fe-dist"


def test_git_binary_available_true_when_git_on_path(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/git")
    get_config.cache_clear()
    assert get_config().git_binary_available is True


def test_git_binary_available_false_when_git_missing(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda cmd: None)
    get_config.cache_clear()
    assert get_config().git_binary_available is False
