"""Tests for get_max_concurrent — env-driven concurrency limit."""

import logging

from shannon_core.config.concurrency import (
    get_max_concurrent,
    get_per_call_timeout,
    is_gitnexus_llm_enabled,
    is_llm_track_enabled,
)


def test_default_when_unset(monkeypatch):
    monkeypatch.delenv("SHANNON_MAX_CONCURRENT", raising=False)
    assert get_max_concurrent() == 3


def test_valid_value(monkeypatch):
    monkeypatch.setenv("SHANNON_MAX_CONCURRENT", "2")
    assert get_max_concurrent() == 2


def test_non_int_falls_back(monkeypatch, caplog):
    monkeypatch.setenv("SHANNON_MAX_CONCURRENT", "abc")
    with caplog.at_level(logging.WARNING):
        assert get_max_concurrent() == 3
    assert "not an int" in caplog.text


def test_zero_falls_back(monkeypatch, caplog):
    monkeypatch.setenv("SHANNON_MAX_CONCURRENT", "0")
    with caplog.at_level(logging.WARNING):
        assert get_max_concurrent() == 3
    assert "must be >=1" in caplog.text


def test_negative_falls_back(monkeypatch, caplog):
    monkeypatch.setenv("SHANNON_MAX_CONCURRENT", "-1")
    with caplog.at_level(logging.WARNING):
        assert get_max_concurrent() == 3
    assert "must be >=1" in caplog.text


def test_gitnexus_llm_default_on(monkeypatch):
    monkeypatch.delenv("SHANNON_GITNEXUS_LLM_ENABLED", raising=False)
    assert is_gitnexus_llm_enabled() is True


def test_gitnexus_llm_off(monkeypatch):
    monkeypatch.setenv("SHANNON_GITNEXUS_LLM_ENABLED", "0")
    assert is_gitnexus_llm_enabled() is False


def test_llm_track_default_on(monkeypatch):
    monkeypatch.delenv("SHANNON_LLM_TRACK_ENABLED", raising=False)
    assert is_llm_track_enabled() is True


def test_llm_track_off(monkeypatch):
    monkeypatch.setenv("SHANNON_LLM_TRACK_ENABLED", "0")
    assert is_llm_track_enabled() is False


# --- SHANNON_LLM_PER_CALL_TIMEOUT (单次 GitNexus 轨 LLM 调用超时秒数) ---

def test_per_call_timeout_default_when_unset(monkeypatch):
    monkeypatch.delenv("SHANNON_LLM_PER_CALL_TIMEOUT", raising=False)
    assert get_per_call_timeout() == 60.0


def test_per_call_timeout_valid(monkeypatch):
    monkeypatch.setenv("SHANNON_LLM_PER_CALL_TIMEOUT", "180")
    assert get_per_call_timeout() == 180.0


def test_per_call_timeout_float(monkeypatch):
    monkeypatch.setenv("SHANNON_LLM_PER_CALL_TIMEOUT", "90.5")
    assert get_per_call_timeout() == 90.5


def test_per_call_timeout_non_float_falls_back(monkeypatch, caplog):
    monkeypatch.setenv("SHANNON_LLM_PER_CALL_TIMEOUT", "abc")
    with caplog.at_level(logging.WARNING):
        assert get_per_call_timeout() == 60.0
    assert "not a float" in caplog.text


def test_per_call_timeout_zero_falls_back(monkeypatch, caplog):
    monkeypatch.setenv("SHANNON_LLM_PER_CALL_TIMEOUT", "0")
    with caplog.at_level(logging.WARNING):
        assert get_per_call_timeout() == 60.0
    assert "must be >0" in caplog.text


def test_per_call_timeout_negative_falls_back(monkeypatch, caplog):
    monkeypatch.setenv("SHANNON_LLM_PER_CALL_TIMEOUT", "-5")
    with caplog.at_level(logging.WARNING):
        assert get_per_call_timeout() == 60.0
    assert "must be >0" in caplog.text
