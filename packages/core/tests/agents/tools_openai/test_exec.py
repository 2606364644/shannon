import asyncio
import os

import pytest
from agents import RunContextWrapper

from supernova_core.agents.tools_openai import ToolContext
from supernova_core.agents.tools_openai.exec import _bash_impl


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


from supernova_core.agents.tools_openai.exec import _grep_impl


@pytest.mark.asyncio
async def test_grep_content_mode(tmp_path):
    (tmp_path / "a.py").write_text("def hello():\n    pass\n")
    (tmp_path / "b.py").write_text("world\n")
    out = await _grep_impl(_ctx(tmp_path), "hello")
    assert "hello" in out and "a.py" in out
    assert "b.py" not in out


@pytest.mark.asyncio
async def test_grep_files_with_matches_mode(tmp_path):
    (tmp_path / "a.py").write_text("target\n")
    (tmp_path / "b.py").write_text("target\ntarget\n")
    out = await _grep_impl(_ctx(tmp_path), "target", output_mode="files_with_matches")
    assert "a.py" in out and "b.py" in out


@pytest.mark.asyncio
async def test_grep_count_mode(tmp_path):
    (tmp_path / "a.py").write_text("x\nx\ny\n")
    out = await _grep_impl(_ctx(tmp_path), "x", output_mode="count")
    assert "2" in out
