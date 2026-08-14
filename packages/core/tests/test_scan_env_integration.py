"""scan_env 覆盖层 ↔ 读取点集成：验证 ws_getenv 让 pricing / llm_track 真正支持 per-scan 覆盖。

set_scan_env（不传 workflow_id → 落 None 键 = 模拟「当前扫描」上下文，等价 worker activity
内 activity.info() 解析到的当前扫描）。读取点（pricing._load_override / concurrency._is_truthy_env）
经 ws_getenv 读到覆盖值；clear 后回落 os.environ / 内置默认。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from supernova_core.config import scan_env
from supernova_core.config.scan_env import clear_scan_env, set_scan_env
from supernova_core.config.concurrency import is_gitnexus_llm_enabled, is_llm_track_enabled
from supernova_core.agents.pricing import compute_cost


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    scan_env._SCAN_ENV.clear()
    monkeypatch.delenv("SUPERNOVA_PRICING_OVERRIDE", raising=False)
    monkeypatch.delenv("SUPERNOVA_LLM_TRACK_ENABLED", raising=False)
    monkeypatch.delenv("SUPERNOVA_GITNEXUS_LLM_ENABLED", raising=False)
    yield
    scan_env._SCAN_ENV.clear()


def _usage(input_tokens: int) -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=input_tokens, output_tokens=0,
        cache_read_input_tokens=0, cache_creation_input_tokens=0,
    )


def _write_pricing(tmp_path, name: str, model: str, input_price: float) -> str:
    p = tmp_path / name
    p.write_text(json.dumps({"currency": "CNY", "models": {
        model: {"input": input_price, "output": 0.0, "cache_read": 0.0, "cache_creation": 0.0},
    }}), encoding="utf-8")
    return str(p)


# ---- pricing：覆盖层 → compute_cost 用 ws 价表 ----

def test_pricing_uses_scan_env_override(tmp_path):
    """ws 覆盖层 PRICING_OVERRIDE → compute_cost 用该价表（非内置 0）。"""
    model = "custom-ws-model"
    path = _write_pricing(tmp_path, "ws.json", model, 1234.0)
    set_scan_env({"SUPERNOVA_PRICING_OVERRIDE": path})
    cost = compute_cost(model, _usage(1_000_000)).cost  # 1234/1M × 1M = 1234
    assert cost == pytest.approx(1234.0)


def test_pricing_falls_back_when_cleared(tmp_path):
    """清覆盖层 → 回落内置（未知模型 → 0，守「不假估算」）。"""
    model = "custom-ws-model"
    path = _write_pricing(tmp_path, "ws.json", model, 1234.0)
    set_scan_env({"SUPERNOVA_PRICING_OVERRIDE": path})
    assert compute_cost(model, _usage(1_000_000)).cost == pytest.approx(1234.0)
    clear_scan_env()
    assert compute_cost(model, _usage(1_000_000)).cost == 0.0  # 未知模型回落 0


def test_two_overrides_swap_via_scan_env(tmp_path):
    """切换覆盖层值 → 同模型 compute_cost 随之变化（per-scan 切换语义）。"""
    model = "swap-model"
    pa = _write_pricing(tmp_path, "a.json", model, 10.0)
    pb = _write_pricing(tmp_path, "b.json", model, 20.0)
    set_scan_env({"SUPERNOVA_PRICING_OVERRIDE": pa})
    assert compute_cost(model, _usage(1_000_000)).cost == pytest.approx(10.0)
    set_scan_env({"SUPERNOVA_PRICING_OVERRIDE": pb})  # 切到 B 扫描
    assert compute_cost(model, _usage(1_000_000)).cost == pytest.approx(20.0)


# ---- llm_track / gitnexus_llm：覆盖层 → is_*_enabled 读 ws 值 ----

def test_llm_track_uses_scan_env_override():
    set_scan_env({"SUPERNOVA_LLM_TRACK_ENABLED": "0"})
    assert is_llm_track_enabled() is False
    set_scan_env({"SUPERNOVA_LLM_TRACK_ENABLED": "1"})
    assert is_llm_track_enabled() is True


def test_gitnexus_llm_uses_scan_env_override():
    set_scan_env({"SUPERNOVA_GITNEXUS_LLM_ENABLED": "false"})
    assert is_gitnexus_llm_enabled() is False
    clear_scan_env()
    assert is_gitnexus_llm_enabled() is True  # 默认开


def test_llm_track_falls_back_to_os_environ(monkeypatch):
    """无覆盖层 → 回落 os.environ（CLI / 全局 .env 语义不变）。"""
    monkeypatch.setenv("SUPERNOVA_LLM_TRACK_ENABLED", "0")
    assert is_llm_track_enabled() is False
