import os

from shannon_core.audit.display_lifecycle import default_refresh_hz


def test_default_refresh_hz_is_3(monkeypatch):
    monkeypatch.delenv("SHANNON_LIVE_REFRESH_HZ", raising=False)
    assert default_refresh_hz() == 3.0


def test_refresh_hz_env_override(monkeypatch):
    monkeypatch.setenv("SHANNON_LIVE_REFRESH_HZ", "2")
    assert default_refresh_hz() == 2.0
