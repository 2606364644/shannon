"""MrScanWorkflow 编排（spec 2026-09-03 §3.1/§7）。

- 空 diff 快速终态（workflow 级，WorkflowEnvironment）：diff 无变更 → 不跑
  child（双轨零消耗），走 finalize 产「无变更」报告，workflow completed。
- mr_meta 穿线（纯函数级）：_mr_child_input 锁定 diff_result 的
  base/head/selected_vuln_classes 灌 child.mr_meta + 全量字段透传。

范式注：child workflow 端到端穿线（MrScanWorkflow → WhiteboxScanWorkflow
setup_display 可观察）已用独立脚本验证通过（pytest WorkflowEnvironment +
child workflow 在本机有预存挂起——CLAUDE.md 测试陷阱，heartbeat 基准同挂，
见 docs/superpowers/plans/2026-07-27-web-config-isolation-stage3.md §732 注），
故不引入会挂起的 workflow 级 child 测试；穿线语义由纯函数单测锁定。
"""

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from supernova_whitebox.pipeline.workflows import MrScanWorkflow, _mr_child_input
from supernova_whitebox.pipeline.shared import PipelineInput


def _input(tmp_path, **kw):
    return PipelineInput(
        repo_path=str(tmp_path / "repo"),
        workspace_name="ws-mr",
        event_file=str(tmp_path / "events.ndjson"),
        enable_llm_track=False,
        mr_base_ref="main",
        mr_head_ref="feature/xss",
        **kw,
    )


_EMPTY_STATS = {"files": 0, "insertions": 0, "deletions": 0}


@pytest.mark.asyncio
async def test_empty_diff_short_circuits_before_child(tmp_path):
    """空 diff（spec §7）：stats.files==0 → 前置 prepare/diff 后直接 finalize，
    不跑删防护判定 / child（WhiteboxScanWorkflow 故意不注册——被调即炸）。"""
    inp = _input(tmp_path)
    calls: list[str] = []

    @activity.defn
    async def run_mr_repo_prepare(i):
        calls.append("prepare")
        return {"base_commit": "b1", "head_commit": "h1"}

    @activity.defn
    async def run_git_diff(i):
        calls.append("diff")
        return {"stats": _EMPTY_STATS, "selected_vuln_classes": [],
                "base_commit": "b1", "head_commit": "h1"}

    @activity.defn
    async def run_mr_empty_diff_finalize(i):
        calls.append("finalize")
        return {"vuln_count": 0}

    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(
            env.client, task_queue="tq-mr-empty",
            workflows=[MrScanWorkflow],
            activities=[run_mr_repo_prepare, run_git_diff, run_mr_empty_diff_finalize],
        ):
            state = await env.client.execute_workflow(
                MrScanWorkflow.run, inp,
                id="w-mr-empty", task_queue="tq-mr-empty",
            )
    assert calls == ["prepare", "diff", "finalize"]
    assert state.status == "completed"


def test_mr_child_input_threads_diff_result_into_mr_meta():
    """穿线语义（纯函数）：diff_result 的 base/head/selected_vuln_classes 优先、
    repo_prepare 兜底；verdict_flow_count 占位 0（child 侧 scope 回填）；
    base/head ref 不穿 child（仅前置 activity 消费）。"""
    inp = PipelineInput(
        repo_path="/r", web_url="https://t", workspace_name="ws-1",
        event_file="/ev/events.ndjson", enable_llm_track=False,
        provider_config={"model": "glm"}, max_concurrent=3,
    )
    prepared = {"base_commit": "pb1", "head_commit": "ph1"}
    diff_result = {"stats": {"files": 2}, "selected_vuln_classes": ["xss", "authz"],
                   "base_commit": "b1", "head_commit": "h1"}

    child = _mr_child_input(inp, prepared, diff_result)

    assert child.mr_meta == {
        "base_commit": "b1",          # diff_result 优先
        "head_commit": "h1",
        "selected_vuln_classes": ["xss", "authz"],
        "verdict_flow_count": 0,      # 占位；child 侧 run_incremental_scope 回填
    }
    # 全量主体字段原样透传
    assert child.repo_path == "/r"
    assert child.workspace_name == "ws-1"
    assert child.event_file == "/ev/events.ndjson"
    assert child.provider_config == {"model": "glm"}
    assert child.max_concurrent == 3
    # base/head ref 是前置 activity 输入（repo checkout/diff），不穿 child
    assert child.mr_base_ref is None
    assert child.mr_head_ref is None


def test_mr_child_input_falls_back_to_prepared_commits():
    """diff_result 缺 commit（异常形态）→ repo_prepare 的解析结果兜底，不落空。"""
    inp = PipelineInput(repo_path="/r", workspace_name="ws-1")
    child = _mr_child_input(inp, {"base_commit": "pb1", "head_commit": "ph1"},
                            {"stats": {"files": 1}, "selected_vuln_classes": ["xss"]})
    assert child.mr_meta["base_commit"] == "pb1"
    assert child.mr_meta["head_commit"] == "ph1"
