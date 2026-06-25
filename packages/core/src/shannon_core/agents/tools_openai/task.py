"""Task/Agent 子代理委派工具（spec 改动 4a）。

对齐 Claude Code CLI 的 Task 语义：给定 prompt → spawn 子代理读码 → 返回结果。
让 openai 引擎与 CLI 引擎流程一致（同一 Task-delegation prompt），vuln prompt 不用改。
"""
from __future__ import annotations

from agents import RunContextWrapper, function_tool

from . import ToolContext


async def _task_impl(
    ctx: RunContextWrapper[ToolContext],
    description: str,
    prompt: str,
    subagent_type: str = "general-purpose",
) -> str:
    """Delegate a code-analysis subtask to a fresh subagent (Task Agent).

    Use this for source code analysis to keep the parent context lean.
    Mirrors Claude Code's Task tool so the same vuln prompt works on both engines.

    Args:
        description: Short description of the subtask.
        prompt: Full instruction for the subagent (e.g. "read app.py, trace data flow to the sink").
        subagent_type: Subagent profile (default general-purpose).
    """
    runner = ctx.context.subagent_run
    if runner is None:
        return "[task error] subagent runner unavailable in this engine context"
    try:
        return await runner(prompt)
    except Exception as exc:  # noqa: BLE001 — 子代理失败不能拖垮父 agent
        return f"[task error] subagent failed: {exc}"


task = function_tool(_task_impl, name_override="task")
