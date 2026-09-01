"""phase 聚合含失败 agent（2026-09-01 概览可观测性）。

历史行为：``_aggregate_phase`` 只在 ``result.success`` 时聚合——失败 agent 对
phase 零贡献，导致「阶段全失败」在瀑布上直接缺席、且 ``Σphase < total``（失败
attempt 的 cost/duration 无条件进 total 却不进 phase）。本组测试锁定修正后的
语义：

- 每次 ``end_agent``（无论成败）都累计该 agent 所属 phase 的 duration/cost/tokens，
  ``Σphase == total`` 严格成立；
- agent 计数按 unique agent 去重（重试不重复计）：``agent_count`` = 出现过的
  agent 数，``failed_agent_count`` = 最终态失败的 agent 数（last-wins）；
- 去重基础 ``agent_states: {agent_name: final_success}`` 随 session.json 落盘
  （resume/reload 后可继续维护）；旧 schema phase 无此字段时兼容初始化，
  ``agent_count`` 历史基数保留。
"""
from __future__ import annotations

import json

import pytest

from supernova_core.audit.metrics_tracker import MetricsTracker
from supernova_core.models.audit import AgentEndResult
from supernova_core.models.metrics import SessionMetadata


def _result(
    success: bool,
    duration_ms: int = 100,
    cost_usd: float = 0.1,
    attempt_number: int = 1,
) -> AgentEndResult:
    return AgentEndResult(
        success=success, duration_ms=duration_ms, cost_usd=cost_usd,
        cost_currency="CNY", attempt_number=attempt_number,
    )


@pytest.fixture
async def tracker(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    t = MetricsTracker(SessionMetadata(id="scan-1"))
    await t.initialize("wf-1")
    return t


async def test_failed_agent_enters_phase_aggregation(tracker):
    """失败 agent 也进 phase：phase 不再缺席，duration/cost 计入，failed 计数。"""
    await tracker.end_agent("injection-vuln", _result(False, duration_ms=500, cost_usd=0.5))
    phase = tracker._data["metrics"]["phases"]["vulnerability-analysis"]
    assert phase["duration_ms"] == 500
    assert phase["cost_usd"] == pytest.approx(0.5)
    assert phase["agent_count"] == 1
    assert phase["failed_agent_count"] == 1


async def test_retry_then_success_dedupes_agent(tracker):
    """attempt1 失败 + attempt2 成功：agent 计 1 个、最终态成功，耗时/成本两次都计。"""
    await tracker.end_agent("authz-vuln", _result(False, duration_ms=100, cost_usd=0.1, attempt_number=1))
    await tracker.end_agent("authz-vuln", _result(True, duration_ms=200, cost_usd=0.2, attempt_number=2))
    phase = tracker._data["metrics"]["phases"]["vulnerability-analysis"]
    assert phase["agent_count"] == 1
    assert phase["failed_agent_count"] == 0
    assert phase["duration_ms"] == 300
    assert phase["cost_usd"] == pytest.approx(0.3)


async def test_retry_all_failed_counts_once(tracker):
    """重试后仍失败：agent 计 1 个、failed 计 1 个（不随 attempt 数膨胀）。"""
    await tracker.end_agent("authz-vuln", _result(False, attempt_number=1))
    await tracker.end_agent("authz-vuln", _result(False, attempt_number=2))
    phase = tracker._data["metrics"]["phases"]["vulnerability-analysis"]
    assert phase["agent_count"] == 1
    assert phase["failed_agent_count"] == 1


async def test_partial_failure_across_agents(tracker):
    """同 phase 多 agent 部分失败：agent_count=2、failed=1。"""
    await tracker.end_agent("injection-vuln", _result(True))
    await tracker.end_agent("xss-vuln", _result(False))
    phase = tracker._data["metrics"]["phases"]["vulnerability-analysis"]
    assert phase["agent_count"] == 2
    assert phase["failed_agent_count"] == 1


async def test_phase_totals_match_session_totals_with_failures(tracker):
    """含失败 attempt 时 Σphase 与 session totals 一致（修口径分裂）。"""
    await tracker.end_agent("injection-vuln", _result(False, duration_ms=100, cost_usd=0.1))
    await tracker.end_agent("injection-vuln", _result(True, duration_ms=200, cost_usd=0.2, attempt_number=2))
    m = tracker._data["metrics"]
    phase_sum_cost = sum(p["cost_usd"] for p in m["phases"].values())
    phase_sum_duration = sum(p["duration_ms"] for p in m["phases"].values())
    assert phase_sum_cost == pytest.approx(m["total_cost_usd"])
    assert phase_sum_duration == m["total_duration_ms"]


async def test_agent_states_persisted_to_disk(tracker):
    """agent_states map 落盘（resume/reload 后去重基础可恢复）。"""
    await tracker.end_agent("xss-vuln", _result(False))
    data = json.loads(tracker._path.read_text(encoding="utf-8"))
    phase = data["metrics"]["phases"]["vulnerability-analysis"]
    assert phase["agent_states"] == {"xss-vuln": False}


async def test_legacy_phase_without_agent_states_backcompat(tracker):
    """旧 schema phase（无 agent_states/failed_agent_count）：基数保留、字段兼容初始化。"""
    path = tracker._path
    data = json.loads(path.read_text(encoding="utf-8"))
    data["metrics"]["phases"]["vulnerability-analysis"] = {
        "duration_ms": 1000, "duration_percentage": 50.0,
        "cost_usd": 1.0, "cost_currency": "CNY", "agent_count": 3,
        "input_tokens": 0, "output_tokens": 0,
        "cache_read_tokens": 0, "cache_creation_tokens": 0,
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    await tracker.reload()

    await tracker.end_agent("ssrf-vuln", _result(False, duration_ms=50, cost_usd=0.05))
    phase = tracker._data["metrics"]["phases"]["vulnerability-analysis"]
    assert phase["agent_count"] == 4          # 历史基数 3 + 新 agent 1
    assert phase["failed_agent_count"] == 1   # map 只含新 agent（旧数据无 per-agent 信息）
    assert phase["duration_ms"] == 1050
    assert phase["agent_states"] == {"ssrf-vuln": False}


async def test_unknown_agent_still_skips_phase(tracker):
    """AGENT_PHASE_MAP 外的唯一名（gn-enrich-* 等）不进 phase（现行为不变）。"""
    await tracker.end_agent("gn-enrich-sink-42", _result(True))
    assert "gn-enrich-sink-42" not in {
        a for p in tracker._data["metrics"]["phases"].values()
        for a in p.get("agent_states", {})
    }
    # map 外 agent 不新建 phase 条目
    assert tracker._data["metrics"]["phases"] == {}
