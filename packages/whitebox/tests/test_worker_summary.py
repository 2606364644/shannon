"""worker._build_final_summary: 最终 WorkflowSummary 的 cost 数据源测试。

回归 NodeGoat CLI 最终 ``Total Cost: $0.0000``(2026-07-14):旧代码
``sum(PipelineState.agent_metrics.cost_usd)`` 在 LLM 轨关闭时为空(该字段只收录
pre-recon/recon/vuln),而 attack-chain/report 等烧钱 agent 经 run_agent → MetricsTracker
累积,完整 cost 躺在 ``session.get_metrics()``。修复后 total_cost/currency 从 session
metrics 读(MetricsTracker 是 single source of truth,含所有 agent + 所有 attempt)。
"""
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from supernova_whitebox.pipeline.shared import PipelineState


@pytest.mark.asyncio
async def test_total_cost_reads_from_session_metrics_not_pipeline():
    from supernova_whitebox.worker import _build_final_summary

    # PipelineState.agent_metrics 空(模拟 LLM 轨关闭:pre-recon/recon/vuln 全跳过)
    result = PipelineState(status="completed", agent_metrics={})
    # session.get_metrics 返完整 metrics(attack-chain/report 经 run_agent 累积)
    session = SimpleNamespace(get_metrics=AsyncMock(return_value={
        "total_cost_usd": 6.494492, "cost_currency": "CNY",
        "agents": {
            "attack-chain": {"cost_usd": 2.0556, "cost_currency": "CNY", "duration_ms": 394616},
            "report": {"cost_usd": 1.6136, "cost_currency": "CNY", "duration_ms": 342312},
        },
    }))

    summary = await _build_final_summary(result, session, time.monotonic())

    assert summary.total_cost_usd == pytest.approx(6.494492)   # 非 sum(空 agent_metrics)=0
    assert summary.cost_currency == "CNY"
    assert set(summary.agent_metrics) == {"attack-chain", "report"}
    assert summary.agent_metrics["attack-chain"].cost_currency == "CNY"


@pytest.mark.asyncio
async def test_total_cost_ignores_nonempty_pipeline_agent_metrics():
    """即便 PipelineState.agent_metrics 非空(LLM 轨开),total_cost 仍以 session metrics 为准
    (后者含 attack-chain/report 等 PipelineState 不收录的 agent)。"""
    from supernova_whitebox.worker import _build_final_summary

    result = PipelineState(status="completed", agent_metrics={
        "injection-vuln": {"cost_usd": 1.0, "duration_ms": 1000},
    })
    session = SimpleNamespace(get_metrics=AsyncMock(return_value={
        "total_cost_usd": 10.0, "cost_currency": "CNY",
        "agents": {
            "injection-vuln": {"cost_usd": 1.0, "cost_currency": "CNY", "duration_ms": 1000},
            "attack-chain": {"cost_usd": 9.0, "cost_currency": "CNY", "duration_ms": 5000},
        },
    }))

    summary = await _build_final_summary(result, session, time.monotonic())

    assert summary.total_cost_usd == pytest.approx(10.0)   # 非 sum(pipeline)=1.0


@pytest.mark.asyncio
async def test_cost_currency_defaults_usd_when_missing():
    from supernova_whitebox.worker import _build_final_summary

    result = PipelineState(status="completed")
    session = SimpleNamespace(get_metrics=AsyncMock(return_value={"total_cost_usd": 0.0}))

    summary = await _build_final_summary(result, session, time.monotonic())

    assert summary.cost_currency == "USD"
    assert summary.total_cost_usd == 0.0


@pytest.mark.asyncio
async def test_status_falls_back_to_failed_for_non_terminal():
    from supernova_whitebox.worker import _build_final_summary

    result = PipelineState(status="running")   # 非终态 → failed
    session = SimpleNamespace(get_metrics=AsyncMock(return_value={}))

    summary = await _build_final_summary(result, session, time.monotonic())

    assert summary.status == "failed"


@pytest.mark.asyncio
async def test_get_metrics_returning_none_is_safe():
    """session.get_metrics() 返 None(未 initialize)→ 不崩,currency 回落 USD。"""
    from supernova_whitebox.worker import _build_final_summary

    result = PipelineState(status="completed")
    session = SimpleNamespace(get_metrics=AsyncMock(return_value=None))

    summary = await _build_final_summary(result, session, time.monotonic())

    assert summary.cost_currency == "USD"
    assert summary.total_cost_usd == 0.0
    assert summary.agent_metrics == {}
