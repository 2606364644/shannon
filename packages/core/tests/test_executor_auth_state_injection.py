"""AgentExecutor.execute 基层统一注入 AUTH_STATE_FILE（对齐 TS agent-execution.ts:133）。

截获 prompt_manager.load_sync 收到的 variables，断言 AUTH_STATE_FILE
= <deliverables.parent>/auth-state.json（与 auth save 的 input.workspace_path 同文件）。
"""
import asyncio

from shannon_core.agents import executor as exec_mod
from shannon_core.models.agents import AgentName


def _run(coro):
    return asyncio.run(coro)


class _RunResult:
    """run_claude_prompt 返回的桩（execute 期望 success/turns/cost/tokens 等）。"""
    success = True
    turns = 1
    cost = 0.0
    text = ""
    error = None
    retryable = True
    model = "stub"
    stop_reason = "end_turn"

    class tokens:
        input_tokens = 0
        output_tokens = 0
        cache_read_input_tokens = 0
        cache_creation_input_tokens = 0

    structured_output = None


def test_executor_injects_auth_state_file(tmp_path, monkeypatch):
    deliverables = tmp_path / "workspaces" / "session" / "deliverables"
    deliverables.mkdir(parents=True)

    async def fake_run(**kw):
        return _RunResult()

    monkeypatch.setattr(exec_mod, "run_claude_prompt", fake_run)
    monkeypatch.setattr(
        exec_mod.GitManager, "ensure_repository",
        classmethod(lambda cls, p: asyncio.sleep(0)),
    )
    monkeypatch.setattr(
        exec_mod.GitManager, "create_checkpoint",
        lambda *a, **k: asyncio.sleep(0),
    )
    monkeypatch.setattr(
        exec_mod.GitManager, "commit",
        lambda *a, **k: asyncio.sleep(0),
    )

    from shannon_core.prompts.manager import PromptManager
    pm = PromptManager.__new__(PromptManager)
    pm.prompts_dir = tmp_path
    captured = {}

    def fake_load(template, *, variables=None, **kw):
        captured["variables"] = variables
        return "PROMPT"

    monkeypatch.setattr(pm, "load_sync", fake_load)

    ex = exec_mod.AgentExecutor(pm)
    _run(ex.execute(
        agent_name=AgentName.RECON_BLACKBOX,
        repo_path=str(deliverables),
        web_url="https://example.com",
        deliverables_path=str(deliverables),
        skip_artifact_postprocess=True,
    ))

    assert "AUTH_STATE_FILE" in captured["variables"], \
        "AgentExecutor.execute 必须基层统一注入 AUTH_STATE_FILE（对齐 TS）"
    assert captured["variables"]["AUTH_STATE_FILE"] == \
        str(deliverables.parent / "auth-state.json")
