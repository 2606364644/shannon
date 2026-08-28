"""混合币种 session 聚合：last-wins 直加 + warning（per-model currency 2026-08-28）。

per-model 币种引入后，一个 session 可同时含 CNY（GLM）与 USD（海外）模型的
agent 成本；``total_cost_usd`` 仍是跨币种直加、``cost_currency`` 仍 last-wins
（行为不变，分币种聚合另列后续），但混合发生时必须可观测（warning）。
"""
from __future__ import annotations

import logging

import pytest

from supernova_core.audit.metrics_tracker import MetricsTracker
from supernova_core.models.audit import AgentEndResult
from supernova_core.models.metrics import SessionMetadata

_LOGGER = "supernova_core.audit.metrics_tracker"


def _result(cost: float, currency: str) -> AgentEndResult:
    return AgentEndResult(success=True, duration_ms=1, cost_usd=cost, cost_currency=currency)


@pytest.fixture
async def tracker(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    t = MetricsTracker(SessionMetadata(id="scan-1"))
    await t.initialize("wf-1")
    return t


async def test_mixed_currency_agents_sum_and_warn(tracker, caplog):
    await tracker.end_agent("agent-a", _result(1.0, "CNY"))
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        await tracker.end_agent("agent-b", _result(2.0, "USD"))
    m = tracker._data["metrics"]
    assert m["total_cost_usd"] == pytest.approx(3.0)   # 跨币种直加（行为不变）
    assert m["cost_currency"] == "USD"                 # last-wins（行为不变）
    assert any("混合币种" in r.message for r in caplog.records)


async def test_same_currency_no_warn(tracker, caplog):
    await tracker.end_agent("agent-a", _result(1.0, "CNY"))
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        await tracker.end_agent("agent-b", _result(2.0, "CNY"))
    assert not any("混合币种" in r.message for r in caplog.records)


async def test_zero_cost_currency_flip_no_warn(tracker, caplog):
    """0 成本 agent（未知模型守「不假估算」）不计入混合判定。"""
    await tracker.end_agent("agent-a", _result(1.0, "CNY"))
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        await tracker.end_agent("agent-b", _result(0.0, "USD"))
    assert not any("混合币种" in r.message for r in caplog.records)
