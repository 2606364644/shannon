"""执行类工具：bash（shell）、grep（ripgrep + fallback）。"""
from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
from pathlib import Path

from agents import RunContextWrapper, function_tool

from . import ToolContext

_MAX_OUTPUT = 30000
_TRUNCATED = "...[truncated]"


def _truncate(text: str) -> str:
    if len(text) > _MAX_OUTPUT:
        return text[:_MAX_OUTPUT] + _TRUNCATED
    return text


async def _bash_impl(
    ctx: RunContextWrapper[ToolContext],
    command: str,
    timeout: int = 120,
) -> str:
    """Execute a shell command and return combined stdout+stderr.

    Args:
        command: The shell command to execute.
        timeout: Max seconds before the command is killed (default 120, hard cap 600).
    """
    cwd = ctx.context.cwd
    timeout = max(1, min(int(timeout), 600))
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return _truncate(f"[command timed out after {timeout}s]: {command}")
        text = stdout.decode(errors="replace") if stdout else ""
        return _truncate(text)
    except Exception as e:  # 工具内异常默认会被 SDK 当结果回喂模型，这里也兜底
        return _truncate(f"[bash error] {type(e).__name__}: {e}")


bash = function_tool(_bash_impl)
