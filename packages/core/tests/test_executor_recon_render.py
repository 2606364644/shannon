"""executor host 渲染:recon agent 跑完后,host 用 collector payload 确定性渲染
recon_deliverable.md,落盘到 deliverables 目录(validate 之前)。

对齐 test_executor_collector_render.py（pre_recon 端到端模板）—— Plan 2 Task E
把它从 PRE_RECON 复刻到 RECON:mock run_claude_prompt 模拟 agent 调
set_executive_summary（8 one-shot 之一）+ set_endpoints append 多次,验 md 落盘 +
内容 + skipped section → placeholder。

对齐 TS agent-execution.ts:295-297 writeDeliverable —— TS 是 validate 后写 + pre-recon
validator no-op;PY 选 validate 前写,host 必渲染故 validate 见文件即过。
"""
import asyncio

import pytest


@pytest.mark.asyncio
async def test_recon_executor_renders_md_from_collector(monkeypatch, tmp_path):
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
            # 模拟 agent 调 8 one-shot 之一(set_executive_summary → §1)
            collector.set_section("set_executive_summary", {"text": "RECON OVERVIEW."})
            # 模拟 agent 调 set_endpoints append 多次(Section 4)
            collector.append_section("set_endpoints", {
                "method": "GET", "path": "/api/users/me", "required_role": "user",
                "object_id_parameters": "None", "authorization_mechanism": "Bearer Token",
                "description_code_pointer": "users.controller.ts:10",
            })
            collector.append_section("set_endpoints", {
                "method": "POST", "path": "/api/auth/login", "required_role": "anon",
                "object_id_parameters": "None", "authorization_mechanism": "None",
                "description_code_pointer": "auth.controller.ts:5",
            })
            # 其余 7 个 set_* 不调 → skipped → renderer 补 placeholder(不 fail activity)
        return FakeResult()

    monkeypatch.setattr(exec_mod, "run_claude_prompt", fake_run)

    # GitManager 全静态/异步方法打桩(对齐 sibling 测试 test_executor_collector_render /
    # test_executor_artifact_postprocess / test_executor_missing_deliverable_diagnostics
    # 的 fixture 约定)。ensure_repository 实际签名:@staticmethod async def
    # ensure_repository(repo_path);用 classmethod lambda 返 asyncio.sleep(0) 已完成
    # coroutine,确保 await 不阻塞且不真跑 git。
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
        agent_name=AgentName.RECON,
        repo_path=str(repo),
        deliverables_path=str(deliverables),
    )

    # collector 已透传给 run_claude_prompt
    assert captured["collector_passed"] is True
    # host 渲染的 md 已落盘到 deliverables 目录
    md_file = deliverables / "recon_deliverable.md"
    assert md_file.exists()
    content = md_file.read_text(encoding="utf-8")
    # 标题 + §0 静态常量
    assert "# Reconnaissance Deliverable" in content
    assert "## 0) HOW TO READ THIS" in content
    # Section 1: agent 调 set_executive_summary 喂的 payload
    assert "## 1. Executive Summary" in content
    assert "RECON OVERVIEW." in content
    # Section 4: set_endpoints append 两次 → 两行 endpoint(证明 append 语义流到 renderer)
    assert "## 4. API Endpoint Inventory" in content
    assert "/api/users/me" in content
    assert "/api/auth/login" in content
    # skipped section → placeholder(set_authentication 未调,证明 host-render 哲学:
    # 缺 section 不 fail activity,补占位提示)
    assert "set_authentication` was not called" in content


@pytest.mark.asyncio
async def test_recon_executor_skipped_all_renders_placeholders(monkeypatch, tmp_path):
    """fake_run 不调任何 set_* → md 仍落盘 + 全 section placeholder(不 fail activity)。

    锁 host-render 哲学:agent 即使完全没调 set_* 工具(空 payload bag),host 仍必
    渲染 recon_deliverable.md(全 placeholder),validate 见文件即过,不 raise
    OUTPUT_VALIDATION_FAILED。对齐 TS host-render 必渲染行为。
    """
    from supernova_core.agents import executor as exec_mod
    from supernova_core.models.agents import AgentName

    repo = tmp_path / "repo"
    repo.mkdir()
    deliverables = tmp_path / "deliverables"

    class FakeResult:
        success = True
        turns = 1
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
            input_tokens = 5
            output_tokens = 2
            cache_read_input_tokens = 0
            cache_creation_input_tokens = 0

        tokens = _T()

    async def fake_run(**kwargs):
        # 不调任何 set_* —— collector payload bag 为空,renderer 全补 placeholder
        return FakeResult()

    monkeypatch.setattr(exec_mod, "run_claude_prompt", fake_run)
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
    # 不 raise —— host-render 必渲染,validate 见文件即过
    await ex.execute(
        agent_name=AgentName.RECON,
        repo_path=str(repo),
        deliverables_path=str(deliverables),
    )

    md_file = deliverables / "recon_deliverable.md"
    assert md_file.exists()
    content = md_file.read_text(encoding="utf-8")
    # §0 静态常量仍在
    assert "# Reconnaissance Deliverable" in content
    assert "## 0) HOW TO READ THIS" in content
    # 9 个 section 全 placeholder(每个 set_* 都未调)
    for tool in (
        "set_executive_summary", "set_technology_stack", "set_authentication",
        "set_input_vectors", "set_network_map", "set_role_architecture",
        "set_authz_candidates", "set_injection_sources",
        # set_endpoints 是 append,空时 renderer 也补 placeholder
    ):
        assert f"`{tool}` was not called" in content, (
            f"missing placeholder for skipped tool {tool}"
        )
