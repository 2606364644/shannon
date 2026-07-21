"""executor host 渲染:pre-recon agent 跑完后,host 用 collector payload 确定性渲染
pre_recon_deliverable.md,落盘到 deliverables 目录(validate 之前)。

对齐 TS agent-execution.ts:295-297 writeDeliverable —— TS 是 validate 后写 + pre-recon
validator no-op;PY 选 validate 前写,host 必渲染故 validate 见文件即过,无需把 pre-recon
validator 改 no-op。

mock run_claude_prompt 模拟 agent 调 set_*,验 md 落盘 + 内容。collector 与 renderer
本身已有专测;此处只验 executor 的接入点(建 collector → 传 run_claude_prompt →
queue 写盘后 / validate 前 host 渲染写盘)。
"""
import asyncio

import pytest


def _run(coro):
    return asyncio.run(coro)


@pytest.mark.asyncio
async def test_pre_recon_executor_renders_md_from_collector(monkeypatch, tmp_path):
    from supernova_core.agents import executor as exec_mod
    from supernova_core.models.agents import AgentName

    repo = tmp_path / "repo"
    repo.mkdir()
    deliverables = tmp_path / "deliverables"

    captured: dict = {}

    class FakeResult:
        success = True
        turns = 3
        cost = 0.0
        cost_currency = "USD"
        text = ""
        model = "glm-5.2"
        structured_output = None
        stop_reason = "end_turn"
        error = None
        retryable = False
        error_code = None

        class _T:
            input_tokens = 10
            output_tokens = 5
            cache_read_input_tokens = 0
            cache_creation_input_tokens = 0

        tokens = _T()

    async def fake_run(**kwargs):
        collector = kwargs.get("collector")
        captured["collector_passed"] = collector is not None
        if collector is not None:
            collector.set_section("set_executive_summary", {"text": "OVERVIEW."})
            collector.set_section("set_xss_sinks", {"applicable": False})
        return FakeResult()

    monkeypatch.setattr(exec_mod, "run_claude_prompt", fake_run)

    # GitManager 全静态/异步方法打桩(对齐 sibling 测试 test_executor_artifact_postprocess
    # 与 test_executor_missing_deliverable_diagnostics 的 fixture 约定)。
    # ensure_repository 实际签名:@staticmethod async def ensure_repository(repo_path);
    # 用 classmethod lambda 返回已完成的 coroutine,确保 await 不阻塞且不真跑 git。
    monkeypatch.setattr(exec_mod.GitManager, "ensure_repository",
                        classmethod(lambda cls, p: asyncio.sleep(0)))
    monkeypatch.setattr(exec_mod.GitManager, "create_checkpoint",
                        lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(exec_mod.GitManager, "commit",
                        lambda *a, **k: asyncio.sleep(0))

    class StubPM:
        def load_sync(self, *a, **kw):
            return "stub prompt"

    ex = exec_mod.AgentExecutor(prompt_manager=StubPM())
    await ex.execute(
        agent_name=AgentName.PRE_RECON,
        repo_path=str(repo),
        deliverables_path=str(deliverables),
    )

    # collector 已透传给 run_claude_prompt
    assert captured["collector_passed"] is True
    # host 渲染的 md 已落盘到 deliverables 目录
    md_file = deliverables / "pre_recon_deliverable.md"
    assert md_file.exists()
    content = md_file.read_text(encoding="utf-8")
    # preamble
    assert content.startswith("# Penetration Test Scope & Boundaries")
    # Section 1: agent 调 set_executive_summary 喂的 payload
    assert "## 1. Executive Summary" in content
    assert "OVERVIEW." in content
    # Section 9: applicable=False → 渲染 NA 提示(证明 agent payload 真的经 collector 流到 renderer)
    assert "N/A — the application has no web frontend" in content
    # 未调的 section → placeholder(set_auth_deep_dive skipped)
    assert "set_auth_deep_dive` was not called" in content
