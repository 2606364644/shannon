import pytest
from agents import RunContextWrapper

from shannon_core.agents.tools_openai import ToolContext
from shannon_core.agents.tools_openai.task import _task_impl


def _ctx(tmp_path, subagent_run=None):
    return RunContextWrapper(ToolContext(cwd=str(tmp_path), subagent_run=subagent_run))


@pytest.mark.asyncio
async def test_task_impl_delegates_to_subagent_run(tmp_path):
    seen = []

    async def fake_run(prompt: str) -> str:
        seen.append(prompt)
        return f"subagent: {prompt}"

    ctx = _ctx(tmp_path, subagent_run=fake_run)
    out = await _task_impl(ctx, "analyze app.py", "read app.py and report SQLi")
    assert "subagent: read app.py and report SQLi" in out
    assert seen == ["read app.py and report SQLi"]


@pytest.mark.asyncio
async def test_task_impl_graceful_when_no_subagent_run(tmp_path):
    ctx = _ctx(tmp_path)  # subagent_run=None
    out = await _task_impl(ctx, "d", "p")
    assert "error" in out.lower() or "unavailable" in out.lower()
