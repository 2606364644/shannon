"""执行类工具：bash（shell）、grep（ripgrep + fallback）。"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
from pathlib import Path

from agents import RunContextWrapper, function_tool

from . import ToolContext

_MAX_OUTPUT = 30000
_TRUNCATED = "...[truncated]"

# 不走 per-scan 代理的目的地——loopback 本机服务（LLM / temporal / 子代理 IPC 等）。
# per-scan 代理只拦截出公网的扫描流量；本机回路走代理会断内部通信。
_NO_PROXY_LOOPBACK = "127.0.0.1,localhost"


def _truncate(text: str) -> str:
    if len(text) > _MAX_OUTPUT:
        return text[:_MAX_OUTPUT] + _TRUNCATED
    return text


def _build_proxy_env(proxy_url: str | None) -> dict[str, str] | None:
    """构造子进程 env：proxy_url 非空→注入 HTTPS/HTTP_PROXY + NO_PROXY(loopback)。

    proxy_url 为 None 时返 None，让 asyncio 继承父进程 env（向后兼容铁律）。
    """
    if not proxy_url:
        return None
    return {
        **os.environ,
        "HTTPS_PROXY": proxy_url,
        "HTTP_PROXY": proxy_url,
        "NO_PROXY": _NO_PROXY_LOOPBACK,
    }


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
    # proxy_url=None→env=None（继承 worker env，向后兼容）；非空→注入出口代理 env。
    env = _build_proxy_env(ctx.context.proxy_url)
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            env=env,
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


bash = function_tool(_bash_impl, name_override="bash")


async def _grep_impl(
    ctx: RunContextWrapper[ToolContext],
    pattern: str,
    path: str = ".",
    glob: str = "*",
    output_mode: str = "content",
) -> str:
    """Search file contents for a regex pattern.

    Args:
        pattern: Regular expression to search for.
        path: Directory or file to search (default working directory).
        glob: File-name glob filter (default "*").
        output_mode: "content" (default, matching lines), "files_with_matches" (file list), or "count".
    """
    cwd = ctx.context.cwd
    base = Path(path)
    if not base.is_absolute():
        base = Path(cwd) / base
    regex = re.compile(pattern)
    files: list[Path] = []
    if base.is_file():
        files = [base]
    else:
        files = [f for f in base.rglob(glob) if f.is_file()]

    rg = shutil.which("rg")
    if rg:
        mode_flag = {"files_with_matches": "-l", "count": "-c"}.get(output_mode)
        cmd = [rg, "-n", "--color=never"]
        if mode_flag:
            cmd.append(mode_flag)
        cmd += ["-g", glob, pattern, str(base)]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            return _truncate(res.stdout)
        except Exception:
            pass  # 退化到 python 正则扫描

    matches_content: list[str] = []
    matched_files: list[str] = []
    counts: list[str] = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        hit_lines = [ln for ln in text.splitlines() if regex.search(ln)]
        if not hit_lines:
            continue
        matched_files.append(str(f))
        counts.append(f"{f}: {len(hit_lines)}")
        for i, ln in enumerate(text.splitlines(), 1):
            if regex.search(ln):
                matches_content.append(f"{f}:{i}:{ln}")
    if output_mode == "files_with_matches":
        return _truncate("\n".join(matched_files))
    if output_mode == "count":
        return _truncate("\n".join(counts))
    return _truncate("\n".join(matches_content))


grep = function_tool(_grep_impl, name_override="grep")
