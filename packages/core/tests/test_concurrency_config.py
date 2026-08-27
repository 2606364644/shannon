"""Tests for get_max_concurrent — env-driven concurrency limit."""

import logging

from supernova_core.config.concurrency import (
    get_chain_verdict_concurrency,
    get_chunk_max_calls,
    get_max_concurrent,
    get_per_call_timeout,
    is_gitnexus_llm_enabled,
    is_llm_track_enabled,
)


def test_default_when_unset(monkeypatch):
    monkeypatch.delenv("SUPERNOVA_MAX_CONCURRENT", raising=False)
    assert get_max_concurrent() == 3


def test_valid_value(monkeypatch):
    monkeypatch.setenv("SUPERNOVA_MAX_CONCURRENT", "2")
    assert get_max_concurrent() == 2


def test_non_int_falls_back(monkeypatch, caplog):
    monkeypatch.setenv("SUPERNOVA_MAX_CONCURRENT", "abc")
    with caplog.at_level(logging.WARNING):
        assert get_max_concurrent() == 3
    assert "not an int" in caplog.text


def test_zero_falls_back(monkeypatch, caplog):
    monkeypatch.setenv("SUPERNOVA_MAX_CONCURRENT", "0")
    with caplog.at_level(logging.WARNING):
        assert get_max_concurrent() == 3
    assert "must be >=1" in caplog.text


def test_negative_falls_back(monkeypatch, caplog):
    monkeypatch.setenv("SUPERNOVA_MAX_CONCURRENT", "-1")
    with caplog.at_level(logging.WARNING):
        assert get_max_concurrent() == 3
    assert "must be >=1" in caplog.text


def test_gitnexus_llm_default_on(monkeypatch):
    monkeypatch.delenv("SUPERNOVA_GITNEXUS_LLM_ENABLED", raising=False)
    assert is_gitnexus_llm_enabled() is True


def test_gitnexus_llm_off(monkeypatch):
    monkeypatch.setenv("SUPERNOVA_GITNEXUS_LLM_ENABLED", "0")
    assert is_gitnexus_llm_enabled() is False


def test_llm_track_default_on(monkeypatch):
    monkeypatch.delenv("SUPERNOVA_LLM_TRACK_ENABLED", raising=False)
    assert is_llm_track_enabled() is True


def test_llm_track_off(monkeypatch):
    monkeypatch.setenv("SUPERNOVA_LLM_TRACK_ENABLED", "0")
    assert is_llm_track_enabled() is False


# --- SUPERNOVA_LLM_PER_CALL_TIMEOUT (单次 GitNexus 轨 LLM 调用超时秒数) ---

def test_per_call_timeout_default_when_unset(monkeypatch):
    monkeypatch.delenv("SUPERNOVA_LLM_PER_CALL_TIMEOUT", raising=False)
    assert get_per_call_timeout() == 60.0


def test_per_call_timeout_valid(monkeypatch):
    monkeypatch.setenv("SUPERNOVA_LLM_PER_CALL_TIMEOUT", "180")
    assert get_per_call_timeout() == 180.0


def test_per_call_timeout_float(monkeypatch):
    monkeypatch.setenv("SUPERNOVA_LLM_PER_CALL_TIMEOUT", "90.5")
    assert get_per_call_timeout() == 90.5


def test_per_call_timeout_non_float_falls_back(monkeypatch, caplog):
    monkeypatch.setenv("SUPERNOVA_LLM_PER_CALL_TIMEOUT", "abc")
    with caplog.at_level(logging.WARNING):
        assert get_per_call_timeout() == 60.0
    assert "not a float" in caplog.text


def test_per_call_timeout_zero_falls_back(monkeypatch, caplog):
    monkeypatch.setenv("SUPERNOVA_LLM_PER_CALL_TIMEOUT", "0")
    with caplog.at_level(logging.WARNING):
        assert get_per_call_timeout() == 60.0
    assert "must be >0" in caplog.text


def test_per_call_timeout_negative_falls_back(monkeypatch, caplog):
    monkeypatch.setenv("SUPERNOVA_LLM_PER_CALL_TIMEOUT", "-5")
    with caplog.at_level(logging.WARNING):
        assert get_per_call_timeout() == 60.0
    assert "must be >0" in caplog.text


# --- SUPERNOVA_CHUNK_MAX_CALLS (chunk 内 suspicious/source call 数上限, spec 2026-07-10) ---

def test_chunk_max_calls_default_when_unset(monkeypatch):
    """未设 → 默认 100(spec §3 模块2: 默认 100, 海量小文件聚合的下限保护)。"""
    monkeypatch.delenv("SUPERNOVA_CHUNK_MAX_CALLS", raising=False)
    assert get_chunk_max_calls() == 100


def test_chunk_max_calls_valid(monkeypatch):
    """合法 int → 直接用(运营可调, 如收紧到 50 减单 chunk 失败粒度)。"""
    monkeypatch.setenv("SUPERNOVA_CHUNK_MAX_CALLS", "50")
    assert get_chunk_max_calls() == 50


def test_chunk_max_calls_non_int_falls_back(monkeypatch, caplog):
    """非 int → 回落 100 + warning, 不崩 scan(对齐 get_max_concurrent 容错契约)。"""
    monkeypatch.setenv("SUPERNOVA_CHUNK_MAX_CALLS", "abc")
    with caplog.at_level(logging.WARNING):
        assert get_chunk_max_calls() == 100
    assert "not an int" in caplog.text


def test_chunk_max_calls_zero_falls_back(monkeypatch, caplog):
    """<=0 → 回落 100 + warning(call 上限必须 >=1, 否则每个 call 都触发拆分无意义)。"""
    monkeypatch.setenv("SUPERNOVA_CHUNK_MAX_CALLS", "0")
    with caplog.at_level(logging.WARNING):
        assert get_chunk_max_calls() == 100
    assert "must be >=1" in caplog.text


def test_chunk_max_calls_negative_falls_back(monkeypatch, caplog):
    monkeypatch.setenv("SUPERNOVA_CHUNK_MAX_CALLS", "-5")
    with caplog.at_level(logging.WARNING):
        assert get_chunk_max_calls() == 100
    assert "must be >=1" in caplog.text


def test_chain_verdict_concurrency_default_when_unset(monkeypatch):
    """未设 → 默认 4（并行研判的保守起点，对齐 vuln agents 并行量级）。"""
    monkeypatch.delenv("SUPERNOVA_CHAIN_VERDICT_CONCURRENCY", raising=False)
    assert get_chain_verdict_concurrency() == 4


def test_chain_verdict_concurrency_valid(monkeypatch):
    """合法 int → 生效（ws 页 / 进程 env 均可调）。"""
    monkeypatch.setenv("SUPERNOVA_CHAIN_VERDICT_CONCURRENCY", "8")
    assert get_chain_verdict_concurrency() == 8


def test_chain_verdict_concurrency_non_int_falls_back(monkeypatch, caplog):
    """非 int → 回落 4 + warning，不崩 scan（容错契约对齐 max_agents）。"""
    monkeypatch.setenv("SUPERNOVA_CHAIN_VERDICT_CONCURRENCY", "abc")
    with caplog.at_level(logging.WARNING):
        assert get_chain_verdict_concurrency() == 4
    assert "not an int" in caplog.text


def test_chain_verdict_concurrency_zero_falls_back(monkeypatch, caplog):
    """0 → 回落 4 + warning（并发必须 >=1）。"""
    monkeypatch.setenv("SUPERNOVA_CHAIN_VERDICT_CONCURRENCY", "0")
    with caplog.at_level(logging.WARNING):
        assert get_chain_verdict_concurrency() == 4
    assert "must be >=1" in caplog.text


def test_chain_verdict_concurrency_ws_override(monkeypatch):
    """读取点走 ws_getenv：工作区覆盖层优先于进程 env（per-workspace 不串台）。"""
    from supernova_core.config import scan_env

    monkeypatch.setenv("SUPERNOVA_CHAIN_VERDICT_CONCURRENCY", "2")
    scan_env.set_scan_env({"SUPERNOVA_CHAIN_VERDICT_CONCURRENCY": "9"})
    try:
        assert get_chain_verdict_concurrency() == 9
    finally:
        scan_env.clear_scan_env()
    assert get_chain_verdict_concurrency() == 2  # 覆盖层清掉 → 回落进程 env
