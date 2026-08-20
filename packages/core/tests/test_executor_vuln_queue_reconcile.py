"""executor vuln queue 写盘对账（spec 2026-08-19 §3.4）：collector 主通道六分支。

monkeypatch 模式对齐 test_executor_validation_diagnostics.py：fake run_claude_prompt
返回无 structured_output 的成功 result（Phase 2 后 vuln agent 停传 schema，
structured_output 恒 None），collector 预填 submitted_findings / findings_summary。
"""
import asyncio
import json

import pytest

from supernova_core.collectors.base import CollectorBase
from supernova_core.collectors.vuln import make_vuln_sections
from supernova_core.models.errors import PentestError


def _run(coro):
    return asyncio.run(coro)


class _R:
    success = True
    turns = 2
    cost = 0.1
    cost_currency = "CNY"
    text = "done"
    error = None
    retryable = False
    model = "stub"
    stop_reason = "end_turn"

    class tokens:
        input_tokens = 10
        output_tokens = 5
        cache_read_input_tokens = 0
        cache_creation_input_tokens = 0

    structured_output = None  # B 拓扑：vuln queue 不再走 structured_output


def _prefilled_collector(submitted: list[dict], roster=None) -> CollectorBase:
    c = CollectorBase(section_schemas=make_vuln_sections("auth"))
    for it in submitted:
        c.append_section("submit_finding", it)
    summary = {"key_outcome": "ko", "patterns": []}
    if roster is not None:
        summary["finding_roster"] = roster
    c.set_section("set_findings_summary", summary)
    return c


def _setup(tmp_path, monkeypatch, collector):
    from supernova_core.agents import executor as exec_mod
    from supernova_core.models.agents import AGENTS, AgentName
    from supernova_core.prompts.manager import PromptManager

    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    defn = AGENTS[AgentName.AUTH_VULN]
    (deliverables / defn.deliverable_filename).write_text("placeholder", encoding="utf-8")

    async def fake_run(**kw):
        return _R()

    monkeypatch.setattr(exec_mod, "run_claude_prompt", fake_run)
    monkeypatch.setattr(exec_mod, "make_collector", lambda name: collector)
    monkeypatch.setattr(exec_mod.GitManager, "ensure_repository",
                        classmethod(lambda cls, p: asyncio.sleep(0)))
    monkeypatch.setattr(exec_mod.GitManager, "create_checkpoint",
                        lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(exec_mod.GitManager, "commit",
                        lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(exec_mod, "render_deliverable", lambda *a, **k: None)

    pm = PromptManager.__new__(PromptManager)
    pm.prompts_dir = tmp_path
    monkeypatch.setattr(pm, "load_sync", lambda *a, **k: "PROMPT")
    return exec_mod.AgentExecutor(pm), exec_mod, deliverables


def _queue_path(deliverables):
    from supernova_core.utils.paths import intermediate_path
    return intermediate_path(deliverables, "auth_exploitation_queue.json")


def _execute(ax, exec_mod, deliverables):
    return _run(ax.execute(
        agent_name=exec_mod.AgentName.AUTH_VULN,
        repo_path=str(deliverables), deliverables_path=str(deliverables),
    ))


def test_full_match_writes_queue_from_collector(tmp_path, monkeypatch):
    submitted = [{"ID": f"AUTH-VULN-{i:02d}", "title": f"t{i}"} for i in (1, 2)]
    roster = [{"id": f["ID"], "title": f["title"]} for f in submitted]
    ax, exec_mod, deliverables = _setup(
        tmp_path, monkeypatch, _prefilled_collector(submitted, roster))
    _execute(ax, exec_mod, deliverables)
    data = json.loads(_queue_path(deliverables).read_text("utf-8"))
    assert [f["ID"] for f in data["vulnerabilities"]] == ["AUTH-VULN-01", "AUTH-VULN-02"]


def test_true_zero_vulns_writes_empty_queue(tmp_path, monkeypatch):
    ax, exec_mod, deliverables = _setup(
        tmp_path, monkeypatch, _prefilled_collector([], []))
    _execute(ax, exec_mod, deliverables)
    assert json.loads(_queue_path(deliverables).read_text("utf-8")) == {"vulnerabilities": []}


def test_total_defiance_still_hits_validator_line(tmp_path, monkeypatch):
    """无 roster 无提交 → 不写盘 → validate 防线 raise（整跑重试语义保留）。"""
    c = CollectorBase(section_schemas=make_vuln_sections("auth"))  # 什么都没调
    ax, exec_mod, deliverables = _setup(tmp_path, monkeypatch, c)
    with pytest.raises(PentestError) as ei:
        _execute(ax, exec_mod, deliverables)
    assert "Missing exploitation queue" in str(ei.value)
    ctx = ei.value.context
    assert ctx["collector_submitted_count"] == 0
    assert ctx["collector_roster_count"] == 0


def test_missing_subset_still_writes_and_warns(tmp_path, monkeypatch, caplog):
    """漏交 1 条（Task 3 阶段：warning 降级、写 11 条；Task 4 升级为定向重查）。"""
    import logging
    submitted = [{"ID": f"AUTH-VULN-{i:02d}", "title": f"t{i}"} for i in range(1, 12)]
    roster = [{"id": f["ID"], "title": f["title"]} for f in submitted] + [
        {"id": "AUTH-VULN-12", "title": "lost one"}]
    ax, exec_mod, deliverables = _setup(
        tmp_path, monkeypatch, _prefilled_collector(submitted, roster))
    with caplog.at_level(logging.WARNING, logger="supernova_core.agents.executor"):
        _execute(ax, exec_mod, deliverables)
    data = json.loads(_queue_path(deliverables).read_text("utf-8"))
    assert len(data["vulnerabilities"]) == 11
    assert any("missing" in r.getMessage() and "AUTH-VULN-12" in r.getMessage()
               for r in caplog.records)


def test_no_roster_but_submitted_writes(tmp_path, monkeypatch):
    c = CollectorBase(section_schemas=make_vuln_sections("auth"))
    c.append_section("submit_finding", {"ID": "AUTH-VULN-01", "title": "t"})
    ax, exec_mod, deliverables = _setup(tmp_path, monkeypatch, c)
    _execute(ax, exec_mod, deliverables)
    data = json.loads(_queue_path(deliverables).read_text("utf-8"))
    assert [f["ID"] for f in data["vulnerabilities"]] == ["AUTH-VULN-01"]


# ── Task 4：定向重查（spec 2026-08-19 §3.4）──────────────────────────────────

class _RecheckR(_R):
    """重查 agent 的假 result：structured_output 返回补交条目。"""
    success = True
    structured_output = {"vulnerabilities": [
        {"ID": "AUTH-VULN-12", "title": "lost one", "vulnerability_type": "X",
         "externally_exploitable": False, "confidence": "Medium", "notes": "rechecked"},
    ]}


def _setup_recheck(monkeypatch, recheck_result,
                   expect_clues=("AUTH-VULN-12", "lost one"), md_check=None):
    """fake run_claude_prompt：第 1 次主 agent、第 2 次定向重查。

    expect_clues：重查 prompt 须携带的 (ID, title) 线索子串——brief 原版硬编码
    AUTH-VULN-12/lost one，与 test 3 的 missing 数据（02/lost）不符，参数化之。
    md_check：重查调用时刻须为真的额外断言（fix round 1：重查前须已预渲染
    deliverable md——重查 agent 读的 md 不能等到主渲染块才落盘）。"""
    from supernova_core.agents import executor as exec_mod
    calls = {"n": 0}

    async def fake_run(prompt=None, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _R()
        assert all(clue in prompt for clue in expect_clues), (
            "recheck prompt must carry missing (ID, title) clues")
        if md_check is not None:
            assert md_check(), (
                "recheck must be called after deliverable md is pre-rendered")
        return recheck_result

    monkeypatch.setattr(exec_mod, "run_claude_prompt", fake_run)
    return calls


def test_missing_triggers_recheck_and_merges(tmp_path, monkeypatch):
    submitted = [{"ID": f"AUTH-VULN-{i:02d}", "title": f"t{i}"} for i in range(1, 12)]
    roster = [{"id": f["ID"], "title": f["title"]} for f in submitted] + [
        {"id": "AUTH-VULN-12", "title": "lost one"}]
    c = _prefilled_collector(submitted, roster)
    ax, exec_mod, deliverables = _setup(tmp_path, monkeypatch, c)
    # fix round 1：_setup 预写的 placeholder md 删掉（否则 exists() 恒真无鉴别力）；
    # render_deliverable 重 patch 返真值（_setup 默认返 None 会跳过预渲染写盘）。
    md_file = deliverables / "auth_analysis_deliverable.md"
    md_file.unlink()
    monkeypatch.setattr(exec_mod, "render_deliverable",
                        lambda *a, **k: "PRERENDERED MD")
    calls = _setup_recheck(monkeypatch, _RecheckR(), md_check=md_file.exists)
    _execute(ax, exec_mod, deliverables)
    assert calls["n"] == 2  # 主 agent + 一次定向重查（一轮封顶）
    data = json.loads(_queue_path(deliverables).read_text("utf-8"))
    assert len(data["vulnerabilities"]) == 12
    merged12 = next(f for f in data["vulnerabilities"] if f["ID"] == "AUTH-VULN-12")
    assert merged12["notes"] == "rechecked"


def test_recheck_cost_merged_into_metrics(tmp_path, monkeypatch):
    """final review fix 1：重查 result 的 cost/turns/tokens 并入 AgentMetrics。

    原 _targeted_recheck 的 result 被丢弃——重查消耗在 session 成本核算完全
    不可见。主 _R 与 _RecheckR 均 cost=0.1/turns=2/tokens(10,5,0,0)。
    """
    submitted = [{"ID": "AUTH-VULN-01", "title": "t1"}]
    roster = [{"id": "AUTH-VULN-01", "title": "t1"},
              {"id": "AUTH-VULN-02", "title": "lost"}]
    c = _prefilled_collector(submitted, roster)
    ax, exec_mod, deliverables = _setup(tmp_path, monkeypatch, c)
    _setup_recheck(monkeypatch, _RecheckR(), expect_clues=("AUTH-VULN-02", "lost"))
    metrics = _execute(ax, exec_mod, deliverables)
    assert metrics.cost_usd == pytest.approx(0.2)   # 主 0.1 + 重查 0.1
    assert metrics.num_turns == 4                    # 主 2 + 重查 2
    assert metrics.input_tokens == 20 and metrics.output_tokens == 10
    assert metrics.cache_read_tokens == 0 and metrics.cache_creation_tokens == 0


def test_recheck_failure_degrades_to_warning(tmp_path, monkeypatch, caplog):
    """重查 agent 整体失败（raise / 无 structured_output）→ 降级：写 11 条 + warning。"""
    import logging

    class _BrokenR(_R):
        structured_output = None

    submitted = [{"ID": f"AUTH-VULN-{i:02d}", "title": f"t{i}"} for i in range(1, 12)]
    roster = [{"id": f["ID"], "title": f["title"]} for f in submitted] + [
        {"id": "AUTH-VULN-12", "title": "lost one"}]
    c = _prefilled_collector(submitted, roster)
    ax, exec_mod, deliverables = _setup(tmp_path, monkeypatch, c)
    _setup_recheck(monkeypatch, _BrokenR())
    with caplog.at_level(logging.WARNING, logger="supernova_core.agents.executor"):
        _execute(ax, exec_mod, deliverables)
    data = json.loads(_queue_path(deliverables).read_text("utf-8"))
    assert len(data["vulnerabilities"]) == 11  # 已到手 11 条不置于风险
    assert any("still missing" in r.getMessage() and "AUTH-VULN-12" in r.getMessage()
               for r in caplog.records)


def test_recheck_output_outside_missing_appended_with_warning(tmp_path, monkeypatch, caplog):
    """重查产出非 missing ID → 追加（召回优先）+ warning；仍不覆盖已交条目。"""
    import logging

    class _OffTargetR(_R):
        structured_output = {"vulnerabilities": [
            {"ID": "AUTH-VULN-99", "title": "off target"}]}

    submitted = [{"ID": "AUTH-VULN-01", "title": "t1"}]
    roster = [{"id": "AUTH-VULN-01", "title": "t1"},
              {"id": "AUTH-VULN-02", "title": "lost"}]
    c = _prefilled_collector(submitted, roster)
    ax, exec_mod, deliverables = _setup(tmp_path, monkeypatch, c)
    _setup_recheck(monkeypatch, _OffTargetR(), expect_clues=("AUTH-VULN-02", "lost"))
    with caplog.at_level(logging.WARNING, logger="supernova_core.agents.executor"):
        _execute(ax, exec_mod, deliverables)
    data = json.loads(_queue_path(deliverables).read_text("utf-8"))
    assert [f["ID"] for f in data["vulnerabilities"]] == ["AUTH-VULN-01", "AUTH-VULN-99"]
    assert any("still missing" in r.getMessage() and "AUTH-VULN-02" in r.getMessage()
               for r in caplog.records)
    # fix round 1：off-target 并入须有专属 warning（含 agent 侧 ID 清单）
    assert any("outside the missing list" in r.getMessage()
               and "AUTH-VULN-99" in r.getMessage()
               for r in caplog.records)
