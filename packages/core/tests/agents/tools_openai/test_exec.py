import asyncio
import os

import pytest
from agents import RunContextWrapper

from shannon_core.agents.tools_openai import ToolContext
from shannon_core.agents.tools_openai.exec import _bash_impl


def _ctx(tmp_path):
    return RunContextWrapper(ToolContext(cwd=str(tmp_path)))


@pytest.mark.asyncio
async def test_bash_returns_stdout(tmp_path):
    result = await _bash_impl(_ctx(tmp_path), "echo hello-world")
    assert "hello-world" in result


@pytest.mark.asyncio
async def test_bash_respects_cwd(tmp_path):
    (tmp_path / "marker.txt").write_text("x")
    result = await _bash_impl(_ctx(tmp_path), "test -f marker.txt && echo FOUND")
    assert "FOUND" in result


@pytest.mark.asyncio
async def test_bash_includes_stderr(tmp_path):
    result = await _bash_impl(_ctx(tmp_path), "echo oops 1>&2")
    assert "oops" in result


@pytest.mark.asyncio
async def test_bash_timeout_returns_error(tmp_path):
    result = await _bash_impl(_ctx(tmp_path), "sleep 5", timeout=1)
    assert "timed out" in result.lower() or "timeout" in result.lower()


@pytest.mark.asyncio
async def test_bash_truncates_long_output(tmp_path):
    result = await _bash_impl(_ctx(tmp_path), "yes x | head -c 60000")
    assert len(result) <= 32000
    assert result.endswith("...[truncated]")
