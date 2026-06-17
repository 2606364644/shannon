# OpenAI Agents SDK 引擎实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 shannon-py 新增基于 `openai-agents`（Chat Completions 模式，接 GLM）的 OpenAI 引擎，与 `claude_agent_sdk` 双引擎并存，经 `SHANNON_AI_PROVIDER` 切换，上层 provider 无关，能力对齐（多轮 tool use）。

**Architecture:** 重写 `OpenAIProvider` 为 `openai-agents` 的 `Agent`+`Runner`（显式 `OpenAIChatCompletionsModel` 接自定义 `AsyncOpenAI(base_url)`），新增 8 个 `@function_tool`（bash/read_file/write_file/edit_file/grep/glob/web_fetch/web_search），cwd 经 `RunContextWrapper[ToolContext]` 注入，流式 events 实时驱动逐轮 audit（复用现有 `ToolAuditLogger`），`RunResult` 映射回现有 `ClaudeRunResult`。`AnthropicProvider` / `message_dispatcher` / GitNexus 不动。

**Tech Stack:** `openai-agents` 0.17.5、`openai>=2.36.0`、asyncio、httpx、pytest + unittest.mock

## Global Constraints

（每个 task 的需求隐含包含本节）

- **版本**：`openai-agents>=0.17.5`，会拉入 `openai>=2.36.0,<3` 与 `pydantic>=2.12.2,<3`；现有 `AsyncOpenAI` / `chat.completions.create` 用法在 openai 2.x 兼容。`packages/core/pyproject.toml` 的 `openai>=1.50` 升为 `openai>=2.36.0`。
- **接入路径**：用显式 `OpenAIChatCompletionsModel(model=…, openai_client=AsyncOpenAI(base_url=…, api_key=…))`，**不**依赖 `set_default_openai_api`（那条只配环境变量时好用）。
- **tracing**：非 `openai.com` 的 key，`OpenAIProvider` 初始化时调一次 `agents.set_tracing_disabled(True)`，否则 trace 上传会 401。
- **usage**：第三方 Chat Completions 流式后端默认不返回 usage，`Agent(model_settings=ModelSettings(include_usage=True))`。
- **max_turns**：超限**抛** `agents.MaxTurnsExceeded`（非安静结束），`call()` 必须 `try/except` 映射到 `stop_reason="max_turns"`。
- **prompt 约定**：`prompt` 已由 `PromptManager.load_sync()` 拼好（含 system prompt），OpenAI 引擎把 `prompt` 当 `Runner` 的 `input`（user message），`Agent(instructions=None)`，行为对齐 anthropic 侧（`query(prompt=prompt)`）。
- **不动**：`providers_anthropic.py`、`message_dispatcher.py`、`gitnexus_mcp.py` 及 pipeline 调用层。
- **测试**：pytest 只跑改动相关子集（**全量会 hang**，见 memory）；`TestModel` 不可用，agent loop 集成测用 mock `Runner.run_streamed`。
- **配置**：`SHANNON_AI_PROVIDER=openai_compatible` 切引擎；新增 `SHANNON_OPENAI_BASE_URL` / `SHANNON_OPENAI_API_KEY` / `SHANNON_OPENAI_MAX_TURNS`（默认 200）。
- **依赖注入**：工具 cwd 经 `Runner.run_streamed(agent, input=prompt, context=ToolContext(cwd=cwd))` 注入；工具首参声明为 `ctx: RunContextWrapper[ToolContext]`，访问 `ctx.context.cwd`。
- **接口契约**：`OpenAIProvider.call` 签名与返回类型严格匹配 `BaseProvider.call(...)` → `ClaudeRunResult`（`runner.py`）。

---

## File Structure

- **Create** `packages/core/src/shannon_core/agents/tools_openai/__init__.py` — `ToolContext` 数据类 + `build_tools()` 注册表（导出全部 8 个工具）。
- **Create** `packages/core/src/shannon_core/agents/tools_openai/fs.py` — `read_file` / `write_file` / `edit_file` / `glob`（文件系统类）。
- **Create** `packages/core/src/shannon_core/agents/tools_openai/exec.py` — `bash` / `grep`（执行/搜索类）。
- **Create** `packages/core/src/shannon_core/agents/tools_openai/web.py` — `web_fetch` / `web_search`（httpx）。
- **Create** `packages/core/src/shannon_core/agents/openai_stream_collector.py` — 流式 event 收集器（实时 audit + 累积 text/turns）。
- **Create** `packages/core/src/shannon_core/agents/openai_result_mapper.py` — `RunResult` → `ClaudeRunResult` 映射（纯函数）。
- **Modify** `packages/core/src/shannon_core/agents/providers_openai.py` — 整体重写为 agents SDK 引擎。
- **Modify** `packages/core/src/shannon_core/agents/providers.py` — `build_provider_config()` 支持 `SHANNON_OPENAI_*`。
- **Modify** `packages/core/pyproject.toml` — 加 `openai-agents`，升 `openai>=2.36.0`。
- **Modify** `.env.example` — `SHANNON_OPENAI_*` 示例。
- **Test** `packages/core/tests/agents/tools_openai/test_fs.py` / `test_exec.py` / `test_web.py`。
- **Test** `packages/core/tests/agents/test_openai_stream_collector.py` / `test_openai_result_mapper.py`。
- **Test** `packages/core/tests/agents/test_providers.py` — 改/增 `TestOpenAIProvider` 用例。

---

## Task 1: 依赖 + tools_openai 骨架 + ToolContext + bash 工具

**Files:**
- Modify: `packages/core/pyproject.toml`
- Create: `packages/core/src/shannon_core/agents/tools_openai/__init__.py`
- Create: `packages/core/src/shannon_core/agents/tools_openai/exec.py`
- Test: `packages/core/tests/agents/tools_openai/test_exec.py`

**Interfaces:**
- Consumes: `agents.RunContextWrapper`、`agents.function_tool`
- Produces: `ToolContext`（`dataclass`，字段 `cwd: str`）、`bash(ctx, command, timeout=120) -> str`（async，`@function_tool`）

- [ ] **Step 1: 加依赖 + 建测试目录，先写失败测试**

`packages/core/pyproject.toml` 的 `dependencies` 列表：把 `"openai>=1.50",` 改为 `"openai>=2.36.0",`，并在 `"openai>=2.36.0",` 下一行新增 `"openai-agents>=0.17.5",`。

创建 `packages/core/tests/agents/tools_openai/__init__.py`（空文件）和 `packages/core/tests/agents/tools_openai/test_exec.py`：

```python
import asyncio
import os

import pytest
from agents import RunContextWrapper

from shannon_core.agents.tools_openai import ToolContext
from shannon_core.agents.tools_openai.exec import bash


def _ctx(tmp_path):
    return RunContextWrapper(ToolContext(cwd=str(tmp_path)))


@pytest.mark.asyncio
async def test_bash_returns_stdout(tmp_path):
    result = await bash(_ctx(tmp_path), "echo hello-world")
    assert "hello-world" in result


@pytest.mark.asyncio
async def test_bash_respects_cwd(tmp_path):
    (tmp_path / "marker.txt").write_text("x")
    result = await bash(_ctx(tmp_path), "test -f marker.txt && echo FOUND")
    assert "FOUND" in result


@pytest.mark.asyncio
async def test_bash_includes_stderr(tmp_path):
    result = await bash(_ctx(tmp_path), "echo oops 1>&2")
    assert "oops" in result


@pytest.mark.asyncio
async def test_bash_timeout_returns_error(tmp_path):
    result = await bash(_ctx(tmp_path), "sleep 5", timeout=1)
    assert "timed out" in result.lower() or "timeout" in result.lower()


@pytest.mark.asyncio
async def test_bash_truncates_long_output(tmp_path):
    result = await bash(_ctx(tmp_path), "yes x | head -c 60000")
    assert len(result) <= 32000
    assert result.endswith("...[truncated]")
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd packages/core && python -m pytest tests/agents/tools_openai/test_exec.py -v`
Expected: FAIL（`ModuleNotFoundError: shannon_core.agents.tools_openai`）。

- [ ] **Step 3: 实现 ToolContext + bash**

创建 `packages/core/src/shannon_core/agents/tools_openai/__init__.py`：

```python
"""OpenAI 引擎的工具集（对齐 claude code 内置工具的核心子集）。

cwd 经 RunContextWrapper[ToolContext] 注入，所有工具共享同一工作目录，
等价于 anthropic 侧 permission_mode=bypassPermissions + cwd。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ToolContext:
    """Runner context：注入工具的工作目录。"""

    cwd: str


def build_tools():
    """返回 OpenAI 引擎的全部 @function_tool 列表。

    分文件定义，这里汇总，供 OpenAIProvider 注入 Agent(tools=...)。
    """
    from .exec import bash, grep
    from .fs import edit_file, glob, read_file, write_file
    from .web import web_fetch, web_search

    return [bash, read_file, write_file, edit_file, grep, glob, web_fetch, web_search]


__all__ = ["ToolContext", "build_tools"]
```

创建 `packages/core/src/shannon_core/agents/tools_openai/exec.py`：

```python
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


@function_tool
async def bash(
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
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd packages/core && python -m pytest tests/agents/tools_openai/test_exec.py -v`
Expected: PASS（5 passed）。若提示缺 `openai-agents`，先 `pip install -e packages/core`。

- [ ] **Step 5: 提交**

```bash
git add packages/core/pyproject.toml \
  packages/core/src/shannon_core/agents/tools_openai/__init__.py \
  packages/core/src/shannon_core/agents/tools_openai/exec.py \
  packages/core/tests/agents/tools_openai/__init__.py \
  packages/core/tests/agents/tools_openai/test_exec.py
git commit -m "feat(openai-engine): 加 openai-agents 依赖 + ToolContext + bash 工具"
```

---

## Task 2: 文件工具 read_file / write_file / edit_file / glob

**Files:**
- Create: `packages/core/src/shannon_core/agents/tools_openai/fs.py`
- Test: `packages/core/tests/agents/tools_openai/test_fs.py`

**Interfaces:**
- Consumes: `ToolContext`（Task 1）、`RunContextWrapper`、`function_tool`
- Produces: `read_file` / `write_file` / `edit_file` / `glob`（均 `@function_tool`，`ctx` 首参）

- [ ] **Step 1: 写失败测试**

`packages/core/tests/agents/tools_openai/test_fs.py`：

```python
import pytest
from agents import RunContextWrapper

from shannon_core.agents.tools_openai import ToolContext
from shannon_core.agents.tools_openai.fs import edit_file, glob, read_file, write_file


def _ctx(tmp_path):
    return RunContextWrapper(ToolContext(cwd=str(tmp_path)))


@pytest.mark.asyncio
async def test_read_file_with_line_numbers(tmp_path):
    (tmp_path / "a.txt").write_text("alpha\nbeta\n")
    out = await read_file(_ctx(tmp_path), "a.txt")
    assert "1\talpha" in out and "2\tbeta" in out


@pytest.mark.asyncio
async def test_read_file_offset_limit(tmp_path):
    (tmp_path / "a.txt").write_text("l1\nl2\nl3\nl4\n")
    out = await read_file(_ctx(tmp_path), "a.txt", offset=1, limit=2)
    assert "l2" in out and "l3" in out and "l4" not in out


@pytest.mark.asyncio
async def test_write_file_creates_and_overwrites(tmp_path):
    await write_file(_ctx(tmp_path), "sub/dir/b.txt", "hello")
    assert (tmp_path / "sub" / "dir" / "b.txt").read_text() == "hello"
    await write_file(_ctx(tmp_path), "sub/dir/b.txt", "world")
    assert (tmp_path / "sub" / "dir" / "b.txt").read_text() == "world"


@pytest.mark.asyncio
async def test_edit_file_replaces_unique(tmp_path):
    (tmp_path / "c.txt").write_text("foo bar foo")
    await edit_file(_ctx(tmp_path), "c.txt", "bar", "baz")
    assert (tmp_path / "c.txt").read_text() == "foo baz foo"


@pytest.mark.asyncio
async def test_edit_file_error_when_not_unique(tmp_path):
    (tmp_path / "c.txt").write_text("dup dup")
    out = await edit_file(_ctx(tmp_path), "c.txt", "dup", "x")
    assert "not unique" in out.lower()


@pytest.mark.asyncio
async def test_edit_file_replace_all(tmp_path):
    (tmp_path / "c.txt").write_text("dup dup")
    await edit_file(_ctx(tmp_path), "c.txt", "dup", "x", replace_all=True)
    assert (tmp_path / "c.txt").read_text() == "x x"


@pytest.mark.asyncio
async def test_glob_matches_pattern(tmp_path):
    (tmp_path / "x.py").write_text("")
    (tmp_path / "y.txt").write_text("")
    (tmp_path / "z.py").write_text("")
    out = await glob(_ctx(tmp_path), "**/*.py")
    assert "x.py" in out and "z.py" in out and "y.txt" not in out
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd packages/core && python -m pytest tests/agents/tools_openai/test_fs.py -v`
Expected: FAIL（`ModuleNotFoundError: ...fs`）。

- [ ] **Step 3: 实现 fs.py**

```python
"""文件系统类工具：read_file / write_file / edit_file / glob。"""
from __future__ import annotations

from pathlib import Path

from agents import RunContextWrapper, function_tool

from . import ToolContext

_MAX_FILE_OUTPUT = 30000


def _resolve(ctx: RunContextWrapper[ToolContext], path: str) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = Path(ctx.context.cwd) / p
    return p


def _truncate(text: str) -> str:
    return text[:_MAX_FILE_OUTPUT] + ("...[truncated]" if len(text) > _MAX_FILE_OUTPUT else "")


@function_tool
async def read_file(
    ctx: RunContextWrapper[ToolContext],
    path: str,
    offset: int = 0,
    limit: int | None = None,
) -> str:
    """Read a text file and return it with 1-based line numbers (cat -n style).

    Args:
        path: File path, relative to the working directory or absolute.
        offset: Number of leading lines to skip (default 0).
        limit: Max number of lines to return (default all).
    """
    p = _resolve(ctx, path)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return f"[read_file error] file not found: {path}"
    lines = text.splitlines()
    start = max(0, int(offset))
    end = len(lines) if limit is None else start + int(limit)
    numbered = [f"{i + 1}\t{line}" for i, line in enumerate(lines[start:end], start=start)]
    return _truncate("\n".join(numbered))


@function_tool
async def write_file(
    ctx: RunContextWrapper[ToolContext],
    path: str,
    content: str,
) -> str:
    """Write content to a file (overwrite), creating parent directories.

    Args:
        path: File path, relative to the working directory or absolute.
        content: Full file content to write.
    """
    p = _resolve(ctx, path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} bytes to {path}"


@function_tool
async def edit_file(
    ctx: RunContextWrapper[ToolContext],
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> str:
    """Replace occurrences of old_string with new_string in a file.

    Args:
        path: File path.
        old_string: Exact text to find.
        new_string: Replacement text.
        replace_all: If False (default), old_string must appear exactly once.
    """
    p = _resolve(ctx, path)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"[edit_file error] file not found: {path}"
    count = text.count(old_string)
    if count == 0:
        return f"[edit_file error] old_string not found in {path}"
    if not replace_all and count > 1:
        return f"[edit_file error] old_string not unique ({count} matches) in {path}"
    new_text = text.replace(old_string, new_string) if replace_all else text.replace(old_string, new_string, 1)
    p.write_text(new_text, encoding="utf-8")
    return f"edited {path} ({count} replacement(s))"


@function_tool
async def glob(
    ctx: RunContextWrapper[ToolContext],
    pattern: str,
    path: str = ".",
) -> str:
    """List file paths matching a glob pattern, newest-first.

    Args:
        pattern: Glob pattern, e.g. "**/*.py".
        path: Directory to search (default working directory).
    """
    base = _resolve(ctx, path)
    matches = sorted(base.glob(pattern), key=lambda f: f.stat().st_mtime if f.exists() else 0, reverse=True)
    return _truncate("\n".join(str(m.relative_to(base)) if m.is_relative_to(base) else str(m) for m in matches))
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd packages/core && python -m pytest tests/agents/tools_openai/test_fs.py -v`
Expected: PASS（7 passed）。

- [ ] **Step 5: 提交**

```bash
git add packages/core/src/shannon_core/agents/tools_openai/fs.py \
  packages/core/tests/agents/tools_openai/test_fs.py
git commit -m "feat(openai-engine): 加文件工具 read_file/write_file/edit_file/glob"
```

---

## Task 3: grep 工具（ripgrep + fallback）

**Files:**
- Modify: `packages/core/src/shannon_core/agents/tools_openai/exec.py`（追加 `grep`）
- Test: `packages/core/tests/agents/tools_openai/test_exec.py`（追加用例）

**Interfaces:**
- Consumes: `ToolContext`、`RunContextWrapper`、`function_tool`、`_truncate`（Task 1）
- Produces: `grep(ctx, pattern, path=".", glob="*", output_mode="content") -> str`

- [ ] **Step 1: 写失败测试（追加到 test_exec.py 末尾）**

```python
from shannon_core.agents.tools_openai.exec import grep


@pytest.mark.asyncio
async def test_grep_content_mode(tmp_path):
    (tmp_path / "a.py").write_text("def hello():\n    pass\n")
    (tmp_path / "b.py").write_text("world\n")
    out = await grep(_ctx(tmp_path), "hello")
    assert "hello" in out and "a.py" in out
    assert "b.py" not in out


@pytest.mark.asyncio
async def test_grep_files_with_matches_mode(tmp_path):
    (tmp_path / "a.py").write_text("target\n")
    (tmp_path / "b.py").write_text("target\ntarget\n")
    out = await grep(_ctx(tmp_path), "target", output_mode="files_with_matches")
    assert "a.py" in out and "b.py" in out


@pytest.mark.asyncio
async def test_grep_count_mode(tmp_path):
    (tmp_path / "a.py").write_text("x\nx\ny\n")
    out = await grep(_ctx(tmp_path), "x", output_mode="count")
    assert "2" in out
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd packages/core && python -m pytest tests/agents/tools_openai/test_exec.py -v`
Expected: FAIL（`ImportError: cannot import name 'grep'`）。

- [ ] **Step 3: 实现 grep（追加到 exec.py）**

在 `exec.py` 末尾追加：

```python
@function_tool
async def grep(
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
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd packages/core && python -m pytest tests/agents/tools_openai/test_exec.py -v`
Expected: PASS（8 passed：5 bash + 3 grep）。

- [ ] **Step 5: 提交**

```bash
git add packages/core/src/shannon_core/agents/tools_openai/exec.py \
  packages/core/tests/agents/tools_openai/test_exec.py
git commit -m "feat(openai-engine): 加 grep 工具（ripgrep + python fallback）"
```

---

## Task 4: web 工具 web_fetch / web_search（httpx）

**Files:**
- Create: `packages/core/src/shannon_core/agents/tools_openai/web.py`
- Test: `packages/core/tests/agents/tools_openai/test_web.py`

**Interfaces:**
- Consumes: `ToolContext`、`RunContextWrapper`、`function_tool`、`httpx`
- Produces: `web_fetch(ctx, url, max_length=30000) -> str`、`web_search(ctx, query, max_results=10) -> str`

- [ ] **Step 1: 写失败测试（mock httpx）**

`packages/core/tests/agents/tools_openai/test_web.py`：

```python
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agents import RunContextWrapper

from shannon_core.agents.tools_openai import ToolContext
from shannon_core.agents.tools_openai.web import web_fetch, web_search


def _ctx(tmp_path):
    return RunContextWrapper(ToolContext(cwd=str(tmp_path)))


@pytest.mark.asyncio
async def test_web_fetch_strips_html(tmp_path):
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.text = "<html><body><p>Hello there</p></body></html>"
    fake_resp.raise_for_status = MagicMock()
    client = AsyncMock()
    client.get = AsyncMock(return_value=fake_resp)
    with patch("shannon_core.agents.tools_openai.web.httpx.AsyncClient", return_value=client):
        out = await web_fetch(_ctx(tmp_path), "https://example.com")
    assert "Hello there" in out
    assert "<p>" not in out


@pytest.mark.asyncio
async def test_web_fetch_truncates(tmp_path):
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.text = "A" * 60000
    fake_resp.raise_for_status = MagicMock()
    client = AsyncMock()
    client.get = AsyncMock(return_value=fake_resp)
    with patch("shannon_core.agents.tools_openai.web.httpx.AsyncClient", return_value=client):
        out = await web_fetch(_ctx(tmp_path), "https://example.com", max_length=1000)
    assert len(out) <= 1100


@pytest.mark.asyncio
async def test_web_search_returns_results(tmp_path):
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    # 极简 DDG Lite 片段
    fake_resp.text = (
        '<a href="//duckduckgo.com/l/?uddg=https%3A%2F%2Ffoo.example%2F">Foo</a>'
        "<td>foo snippet text</td>"
    )
    fake_resp.raise_for_status = MagicMock()
    client = AsyncMock()
    client.get = AsyncMock(return_value=fake_resp)
    with patch("shannon_core.agents.tools_openai.web.httpx.AsyncClient", return_value=client):
        out = await web_search(_ctx(tmp_path), "foo")
    assert "foo.example" in out or "Foo" in out
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd packages/core && python -m pytest tests/agents/tools_openai/test_web.py -v`
Expected: FAIL（`ModuleNotFoundError: ...web`）。

- [ ] **Step 3: 实现 web.py**

```python
"""Web 工具：web_fetch（抓取去标签）、web_search（DuckDuckGo Lite，无 key）。"""
from __future__ import annotations

import re
import urllib.parse

import httpx
from agents import RunContextWrapper, function_tool

from . import ToolContext

_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def _strip_html(html: str) -> str:
    text = _TAG_RE.sub(" ", html)
    return _WHITESPACE_RE.sub(" ", text).strip()


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "...[truncated]"


@function_tool
async def web_fetch(
    ctx: RunContextWrapper[ToolContext],
    url: str,
    max_length: int = 30000,
) -> str:
    """Fetch a URL and return its text content (HTML stripped).

    Args:
        url: The URL to fetch.
        max_length: Max characters to return (default 30000).
    """
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "shannon-openai-engine/1.0"})
            resp.raise_for_status()
            return _truncate(_strip_html(resp.text), int(max_length))
    except Exception as e:
        return f"[web_fetch error] {type(e).__name__}: {e}"


@function_tool
async def web_search(
    ctx: RunContextWrapper[ToolContext],
    query: str,
    max_results: int = 10,
) -> str:
    """Search the web via DuckDuckGo and return results (title, url, snippet).

    Args:
        query: Search query.
        max_results: Max number of results (default 10).
    """
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(
                "https://lite.duckduckgo.com/lite/",
                params={"q": query, "kl": "us-en"},
                headers={"User-Agent": "shannon-openai-engine/1.0"},
            )
            resp.raise_for_status()
            html = resp.text
    except Exception as e:
        return f"[web_search error] {type(e).__name__}: {e}"

    rows: list[str] = []
    # 解析结果链接 (uddg=) 与相邻文本片段
    for href, snippet in re.findall(r'uddg=([^"&]+).*?</a>.*?<td[^>]*>(.*?)</td>', html, re.S)[: int(max_results)]:
        link = urllib.parse.unquote(href)
        rows.append(f"- {snippet.strip()[:200]}\n  {link}")
    return _truncate("\n".join(rows), 30000) or "[web_search] no results"
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd packages/core && python -m pytest tests/agents/tools_openai/test_web.py -v`
Expected: PASS（3 passed）。

- [ ] **Step 5: 提交**

```bash
git add packages/core/src/shannon_core/agents/tools_openai/web.py \
  packages/core/tests/agents/tools_openai/test_web.py
git commit -m "feat(openai-engine): 加 web_fetch / web_search 工具（httpx）"
```

---

## Task 5: 工具注册表 build_tools 回归

**Files:**
- Test: `packages/core/tests/agents/tools_openai/test_registry.py`（新建）

**Interfaces:**
- Consumes: `build_tools()`（Task 1 已定义于 `__init__.py`，但那时 fs/web 模块未实现；本 task 在所有工具就位后验证注册表）
- Produces: 确认 `build_tools()` 返回 8 个 function_tool

- [ ] **Step 1: 写失败测试**

`packages/core/tests/agents/tools_openai/test_registry.py`：

```python
from shannon_core.agents.tools_openai import build_tools


def test_build_tools_returns_eight():
    tools = build_tools()
    names = {t.name for t in tools}  # agents function_tool 暴露 .name
    assert names == {
        "bash", "read_file", "write_file", "edit_file",
        "grep", "glob", "web_fetch", "web_search",
    }
```

- [ ] **Step 2: 运行测试，确认失败/通过**

Run: `cd packages/core && python -m pytest tests/agents/tools_openai/test_registry.py -v`
Expected: PASS（Task 1-4 已实现全部 8 个工具；若 FAIL 说明某个工具名/导出不一致，修正对应模块）。

- [ ] **Step 3: 提交**

```bash
git add packages/core/tests/agents/tools_openai/test_registry.py
git commit -m "test(openai-engine): build_tools 注册表回归（8 个工具）"
```

---

## Task 6: 流式 event 收集器 StreamCollector

**Files:**
- Create: `packages/core/src/shannon_core/agents/openai_stream_collector.py`
- Test: `packages/core/tests/agents/test_openai_stream_collector.py`

**Interfaces:**
- Consumes: `ToolAuditLogger`（`log_assistant_turn` / `log_tool_start` / `log_tool_end`）、agents stream events（`raw_response_event` 含 `ResponseTextDeltaEvent`、`run_item_stream_event` 含 `RunItem`）
- Produces: `StreamCollector`（`async on_event(event)`、属性 `.text`、`.turns`、`.tool_call_count`）

- [ ] **Step 1: 写失败测试（构造 mock events）**

`packages/core/tests/agents/test_openai_stream_collector.py`：

```python
from unittest.mock import AsyncMock, MagicMock

import pytest
from openai.types.responses import ResponseTextDeltaEvent

from shannon_core.agents.openai_stream_collector import StreamCollector


def _text_event(delta: str):
    ev = MagicMock()
    ev.type = "raw_response_event"
    ev.data = ResponseTextDeltaEvent(type="response.output_text.delta", delta=delta, item_id="i", output_index=0, content_index=0)
    return ev


def _run_item_event(item_type: str, name: str, output: str | None = None):
    item = MagicMock()
    item.type = item_type
    item.output = output
    if item_type == "message_output_item":
        item.raw_item = MagicMock()
        from agents import ItemHelpers
        item._helpers_text = None  # 占位
    ev = MagicMock()
    ev.type = "run_item_stream_event"
    ev.name = name
    ev.item = item
    return ev


def _agent_event():
    ev = MagicMock()
    ev.type = "agent_updated_stream_event"
    ev.new_agent = MagicMock()
    ev.new_agent.name = "A"
    return ev


@pytest.mark.asyncio
async def test_collects_text_and_reports_turn():
    audit = AsyncMock()
    collector = StreamCollector(audit)
    await collector.on_event(_agent_event())  # 新 agent → 新 turn
    await collector.on_event(_text_event("hello "))
    await collector.on_event(_text_event("world"))
    await collector.on_event(_agent_event())  # 再次新 agent → 第二 turn
    await collector.on_event(_text_event("second"))
    assert collector.text == "hello worldsecond"
    assert collector.turns == 2
    audit.log_assistant_turn.assert_any_call(1, "hello world")
    audit.log_assistant_turn.assert_any_call(2, "second")


@pytest.mark.asyncio
async def test_reports_tool_calls():
    audit = AsyncMock()
    collector = StreamCollector(audit)
    await collector.on_event(_run_item_event("tool_call_item", "tool_called"))
    await collector.on_event(_run_item_event("tool_call_output_item", "tool_output", output="result-data"))
    assert collector.tool_call_count == 1
    audit.log_tool_start.assert_awaited()
    audit.log_tool_end.assert_awaited_with("result-data")
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd packages/core && python -m pytest tests/agents/test_openai_stream_collector.py -v`
Expected: FAIL（`ModuleNotFoundError`）。

- [ ] **Step 3: 实现 StreamCollector**

```python
"""openai-agents 流式 event 收集器：实时驱动逐轮 audit + 累积 text/turns。

对齐 anthropic 侧 MessageDispatcher 的逐轮上报语义：
- 每个 agent_updated_stream_event 开启一个新 turn；
- turn 内的文本累积，turn 结束（下一个 agent_updated 或流结束）时上报 log_assistant_turn；
- tool 调用 → log_tool_start，tool 输出 → log_tool_end。
"""
from __future__ import annotations

from typing import Any

from openai.types.responses import ResponseTextDeltaEvent

from .tool_audit_logger import ToolAuditLogger


class StreamCollector:
    def __init__(self, audit_logger: ToolAuditLogger | None):
        self._audit = audit_logger
        self._turn_count = 0
        self._turn_text = ""
        self._all_text: list[str] = []
        self.tool_call_count = 0

    @property
    def turns(self) -> int:
        return self._turn_count

    @property
    def text(self) -> str:
        return "".join(self._all_text)

    async def on_event(self, event: Any) -> None:
        etype = getattr(event, "type", None)

        if etype == "agent_updated_stream_event":
            await self._close_turn()
            self._turn_count += 1
            return

        if etype == "raw_response_event":
            data = getattr(event, "data", None)
            if isinstance(data, ResponseTextDeltaEvent):
                delta = getattr(data, "delta", "") or ""
                self._turn_text += delta
                self._all_text.append(delta)
            return

        if etype == "run_item_stream_event":
            name = getattr(event, "name", None)
            item = getattr(event, "item", None)
            item_type = getattr(item, "type", None)
            if name == "tool_called" or item_type == "tool_call_item":
                self.tool_call_count += 1
                if self._audit is not None:
                    await self._audit.log_tool_start(_item_tool_name(item), _item_tool_args(item))
            elif name == "tool_output" or item_type == "tool_call_output_item":
                if self._audit is not None:
                    await self._audit.log_tool_end(getattr(item, "output", ""))
            return

    async def close(self) -> None:
        await self._close_turn()

    async def _close_turn(self) -> None:
        if self._turn_count > 0 and self._turn_text and self._audit is not None:
            await self._audit.log_assistant_turn(self._turn_count, self._turn_text)
        self._turn_text = ""


def _item_tool_name(item: Any) -> str:
    raw = getattr(item, "raw_item", None)
    return getattr(raw, "name", None) or getattr(item, "name", None) or "tool"


def _item_tool_args(item: Any) -> Any:
    raw = getattr(item, "raw_item", None)
    return getattr(raw, "arguments", None) or getattr(raw, "input", None) or {}
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd packages/core && python -m pytest tests/agents/test_openai_stream_collector.py -v`
Expected: PASS（2 passed）。

- [ ] **Step 5: 提交**

```bash
git add packages/core/src/shannon_core/agents/openai_stream_collector.py \
  packages/core/tests/agents/test_openai_stream_collector.py
git commit -m "feat(openai-engine): 流式 event 收集器（逐轮 audit + text/turns 累积）"
```

---

## Task 7: RunResult → ClaudeRunResult 映射

**Files:**
- Create: `packages/core/src/shannon_core/agents/openai_result_mapper.py`
- Test: `packages/core/tests/agents/test_openai_result_mapper.py`

**Interfaces:**
- Consumes: `ClaudeRunResult`、`TokenUsage`（`runner.py`）、agents `RunResult`（`.final_output`、`.context_wrapper.usage`）
- Produces: `map_run_result(run_result, *, duration_ms, model, turns, stop_reason=None, output_format=None) -> ClaudeRunResult`

- [ ] **Step 1: 写失败测试**

`packages/core/tests/agents/test_openai_result_mapper.py`：

```python
from unittest.mock import MagicMock

from shannon_core.agents.openai_result_mapper import map_run_result
from shannon_core.agents.runner import ClaudeRunResult, TokenUsage


def _usage(inp, outp):
    u = MagicMock()
    u.input_tokens = inp
    u.output_tokens = outp
    return u


def _run_result(final_output, usage):
    rr = MagicMock()
    rr.final_output = final_output
    rr.context_wrapper = MagicMock()
    rr.context_wrapper.usage = usage
    return rr


def test_map_plain_text():
    rr = _run_result("hello", _usage(10, 5))
    res = map_run_result(rr, duration_ms=123, model="GLM-5.2[1m]", turns=1)
    assert isinstance(res, ClaudeRunResult)
    assert res.text == "hello"
    assert res.success is True
    assert res.duration == 123
    assert res.turns == 1
    assert res.model == "GLM-5.2[1m]"
    assert res.tokens.input_tokens == 10
    assert res.tokens.output_tokens == 5


def test_map_stop_reason_max_turns():
    rr = _run_result("partial", _usage(1, 1))
    res = map_run_result(rr, duration_ms=10, model="m", turns=200, stop_reason="max_turns")
    assert res.stop_reason == "max_turns"


def test_map_structured_output():
    rr = _run_result('{"k": "v"}', _usage(1, 1))
    res = map_run_result(rr, duration_ms=10, model="m", turns=1, output_format={"type": "object"})
    assert res.structured_output == {"k": "v"}
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd packages/core && python -m pytest tests/agents/test_openai_result_mapper.py -v`
Expected: FAIL（`ModuleNotFoundError`）。

- [ ] **Step 3: 实现映射（纯函数）**

```python
"""openai-agents RunResult → shannon ClaudeRunResult 映射（纯函数，无副作用）。"""
from __future__ import annotations

import json
from typing import Any

from agents import RunResult

from .runner import ClaudeRunResult, TokenUsage

# 与 providers_openai 现有定价表共用；未知模型回退到 gpt-4o 档
_DEFAULT_PRICING = {"input": 0.0025, "output": 0.01}


def _estimate_cost(model: str, tokens: TokenUsage) -> float:
    # GLM 等模型定价未知，这里给 0；真实成本以 provider 账单为准。
    # 保留估算入口，后续可按模型补定价表。
    pricing = _DEFAULT_PRICING
    return (tokens.input_tokens / 1000) * pricing["input"] + (tokens.output_tokens / 1000) * pricing["output"]


def _usage_from(run_result: RunResult) -> TokenUsage:
    usage = getattr(getattr(run_result, "context_wrapper", None), "usage", None)
    if usage is None:
        return TokenUsage()
    return TokenUsage(
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
    )


def map_run_result(
    run_result: RunResult,
    *,
    duration_ms: int,
    model: str,
    turns: int,
    stop_reason: str | None = None,
    output_format: dict | None = None,
) -> ClaudeRunResult:
    final = getattr(run_result, "final_output", "")
    text = final if isinstance(final, str) else str(final)
    tokens = _usage_from(run_result)

    structured_output: Any | None = None
    if output_format and text:
        try:
            structured_output = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            structured_output = final if not isinstance(final, str) else None

    return ClaudeRunResult(
        text=text,
        success=True,
        duration=duration_ms,
        turns=turns,
        cost=_estimate_cost(model, tokens),
        model=model,
        structured_output=structured_output,
        tokens=tokens,
        stop_reason=stop_reason,
    )
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd packages/core && python -m pytest tests/agents/test_openai_result_mapper.py -v`
Expected: PASS（3 passed）。

- [ ] **Step 5: 提交**

```bash
git add packages/core/src/shannon_core/agents/openai_result_mapper.py \
  packages/core/tests/agents/test_openai_result_mapper.py
git commit -m "feat(openai-engine): RunResult → ClaudeRunResult 映射（纯函数）"
```

---

## Task 8: 重写 OpenAIProvider（组装 agent + loop + call）

**Files:**
- Modify: `packages/core/src/shannon_core/agents/providers_openai.py`（整体重写）
- Test: `packages/core/tests/agents/test_providers.py`（替换 `TestOpenAIProvider` 旧用例）

**Interfaces:**
- Consumes: `BaseProvider`、`ProviderConfig`、`ClaudeRunResult`（`runner.py`）、`ToolAuditLogger`、`build_tools()`（Task 1/5）、`StreamCollector`（Task 6）、`map_run_result`（Task 7）、`agents`（`Agent` / `Runner` / `OpenAIChatCompletionsModel` / `ModelSettings` / `set_tracing_disabled` / `MaxTurnsExceeded` / `RunContextWrapper`）
- Produces: `OpenAIProvider(BaseProvider)`，`.call(...)` 满足基类契约；`.build_agent(model, output_format)` 可单测

- [ ] **Step 1: 写失败测试（替换 test_providers.py 中的 TestOpenAIProvider）**

在 `packages/core/tests/agents/test_providers.py` 里，删除原有 `class TestOpenAIProvider:` 的旧方法（`test_get_model_default` / `test_estimate_cost` / `test_openai_call_logs_single_turn`，它们针对已被重写的单轮实现），替换为：

```python
class TestOpenAIProvider:
    def test_get_model_resolves_tier(self):
        from shannon_core.agents.providers_openai import OpenAIProvider
        config = ProviderConfig(
            type="openai_compatible",
            medium_model="GLM-5.2[1m]",
        )
        provider = OpenAIProvider(config)
        assert provider._get_model("medium") == "GLM-5.2[1m]"

    def test_get_model_falls_back_to_default(self):
        from shannon_core.agents.providers_openai import OpenAIProvider
        config = ProviderConfig(type="openai_compatible")
        provider = OpenAIProvider(config)
        # DEFAULT_MODELS["openai_compatible"]["medium"]
        assert provider._get_model("medium") == DEFAULT_MODELS["openai_compatible"]["medium"]

    def test_build_agent_wires_chatcompletions_model_and_tools(self):
        from agents import Agent, OpenAIChatCompletionsModel
        from shannon_core.agents.providers_openai import OpenAIProvider
        config = ProviderConfig(type="openai_compatible", base_url="https://x/v4", api_key="k", medium_model="m")
        provider = OpenAIProvider(config)
        agent = provider.build_agent("m", output_format=None)
        assert isinstance(agent, Agent)
        assert isinstance(agent.model, OpenAIChatCompletionsModel)
        assert len(agent.tools) == 8

    @pytest.mark.asyncio
    async def test_call_maps_result_and_audits(self, monkeypatch, tmp_path):
        # 用 mock Runner.run_streamed 验证 call() 的组装：event 收集 + 映射 + audit
        from unittest.mock import AsyncMock, MagicMock
        from shannon_core.agents.providers_openai import OpenAIProvider

        config = ProviderConfig(type="openai_compatible", base_url="https://x/v4", api_key="k", medium_model="m")
        provider = OpenAIProvider(config)

        async def _empty():  # stream_events 占位迭代器
            if False:
                yield  # 让它成为 async generator

        fake_result = MagicMock()
        fake_result.final_output = "done"
        fake_result.context_wrapper = MagicMock()
        fake_result.context_wrapper.usage = MagicMock(input_tokens=3, output_tokens=2)
        fake_result.stream_events = _empty

        monkeypatch.setattr("shannon_core.agents.providers_openai.Runner.run_streamed",
                            AsyncMock(return_value=fake_result))

        audit = AsyncMock()
        res = await provider.call(prompt="hi", cwd=str(tmp_path), model_tier="medium", audit_logger=audit)
        assert res.success is True
        assert res.text == "done"
        assert res.model == "m"
        assert res.tokens.input_tokens == 3

    @pytest.mark.asyncio
    async def test_call_handles_max_turns(self, monkeypatch, tmp_path):
        from unittest.mock import AsyncMock
        from agents import MaxTurnsExceeded
        from shannon_core.agents.providers_openai import OpenAIProvider

        config = ProviderConfig(type="openai_compatible", base_url="https://x/v4", api_key="k", medium_model="m")
        provider = OpenAIProvider(config)

        async def _boom(*a, **kw):
            raise MaxTurnsExceeded("hit")

        monkeypatch.setattr("shannon_core.agents.providers_openai.Runner.run_streamed", _boom)
        res = await provider.call(prompt="hi", cwd=str(tmp_path), model_tier="medium")
        assert res.stop_reason == "max_turns"
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd packages/core && python -m pytest tests/agents/test_providers.py::TestOpenAIProvider -v`
Expected: FAIL（`OpenAIProvider` 仍是单轮旧实现，无 `build_agent`，`Runner` 未接入）。

- [ ] **Step 3: 重写 providers_openai.py（整体替换文件内容）**

```python
"""OpenAI Provider（基于 openai-agents，Chat Completions 模式接第三方 OpenAI 兼容接口）。

设计见 docs/superpowers/specs/2026-06-17-openai-agents-engine-design.md。
与 AnthropicProvider 双引擎并存，经 SHANNON_AI_PROVIDER=openai_compatible 切换。
"""
from __future__ import annotations

import os
import time

from agents import (
    Agent,
    MaxTurnsExceeded,
    ModelSettings,
    OpenAIChatCompletionsModel,
    Runner,
    RunContextWrapper,
    set_tracing_disabled,
)
from openai import AsyncOpenAI

from .openai_result_mapper import map_run_result
from .openai_stream_collector import StreamCollector
from .providers import BaseProvider, ProviderConfig
from .runner import DEFAULT_MODELS, ClaudeRunResult, TokenUsage
from .tool_audit_logger import ToolAuditLogger
from .tools_openai import ToolContext, build_tools

_tracing_disabled = False


class OpenAIProvider(BaseProvider):
    """使用 openai-agents 的 Provider（多轮 tool use agent loop）。"""

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        global _tracing_disabled
        if not _tracing_disabled:
            set_tracing_disabled(True)  # 第三方 base_url，关掉 trace 上传避免 401
            _tracing_disabled = True
        self._client: AsyncOpenAI | None = None

    # —— 模型解析（沿用现有语义）——
    def _get_model(self, model_tier: str) -> str:
        tier_models = {
            "small": self.config.small_model,
            "medium": self.config.medium_model,
            "large": self.config.large_model,
        }
        if tier_models.get(model_tier):
            return tier_models[model_tier]
        if self.config.model:
            return self.config.model
        key = "litellm_router" if self.type == "litellm_router" else "openai_compatible"
        models = DEFAULT_MODELS.get(key, DEFAULT_MODELS["openai_compatible"])
        return models.get(model_tier, models.get("medium", "gpt-4o"))

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            kwargs: dict = {}
            api_key = self.config.api_key or os.getenv("OPENAI_API_KEY")
            if api_key:
                kwargs["api_key"] = api_key
            if self.config.base_url:
                kwargs["base_url"] = self.config.base_url
            if self.type == "litellm_router" and self.config.auth_token:
                kwargs["api_key"] = self.config.auth_token
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    def _max_turns(self) -> int:
        return int(os.getenv("SHANNON_OPENAI_MAX_TURNS", "200"))

    def build_agent(self, model: str, output_format: dict | None) -> Agent:
        client = self._get_client()
        chat_model = OpenAIChatCompletionsModel(model=model, openai_client=client)
        return Agent(
            name="shannon-openai-agent",
            instructions=None,  # prompt 已含 system prompt，整段当 user input
            tools=build_tools(),
            model=chat_model,
            model_settings=ModelSettings(include_usage=True),
        )

    async def call(
        self,
        prompt: str,
        cwd: str,
        model_tier: str = "medium",
        output_format: dict | None = None,
        deliverables_subdir: str | None = None,
        audit_logger: ToolAuditLogger | None = None,
    ) -> ClaudeRunResult:
        start_time = time.time()
        model = self._get_model(model_tier)
        try:
            agent = self.build_agent(model, output_format)
            collector = StreamCollector(audit_logger)
            stop_reason: str | None = None
            try:
                result = Runner.run_streamed(
                    agent,
                    input=prompt,
                    context=ToolContext(cwd=cwd),
                    max_turns=self._max_turns(),
                )
                async for event in result.stream_events():
                    await collector.on_event(event)
                await collector.close()
                run_result = result
            except MaxTurnsExceeded:
                await collector.close()
                # 无可用 RunResult，构造一个最小结果对象
                run_result = _MaxTurnsStub(collector.text)
                stop_reason = "max_turns"

            duration = int((time.time() - start_time) * 1000)
            return map_run_result(
                run_result,
                duration_ms=duration,
                model=model,
                turns=max(collector.turns, 1),
                stop_reason=stop_reason,
                output_format=output_format,
            )
        except Exception as e:
            duration = int((time.time() - start_time) * 1000)
            return self._handle_error(e, duration, model)

    def _handle_error(self, error: Exception, duration: int, model: str) -> ClaudeRunResult:
        return ClaudeRunResult(
            text="",
            success=False,
            duration=duration,
            turns=0,
            cost=0.0,
            model=model,
            error=str(error),
            retryable=self._is_retryable_error(error),
        )


class _MaxTurnsStub:
    """MaxTurnsExceeded 时无 RunResult，伪造一个只含 final_output 的对象供 map_run_result 使用。"""

    def __init__(self, text: str):
        class _CW:
            class _U:
                input_tokens = 0
                output_tokens = 0
            usage = _U()
        self.final_output = text
        self.context_wrapper = _CW()
```

> 说明：`map_run_result` 对 `final_output`/`context_wrapper.usage` 用 `getattr` 读取，`_MaxTurnsStub` 满足该 duck-typing 契约。

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd packages/core && python -m pytest tests/agents/test_providers.py::TestOpenAIProvider -v`
Expected: PASS（5 passed）。若 `DEFAULT_MODELS` 在 `test_providers.py` 未导入，在文件顶部补 `from shannon_core.agents.runner import DEFAULT_MODELS`（及 `ProviderConfig`、`TokenUsage`，按缺失补）。

- [ ] **Step 5: 提交**

```bash
git add packages/core/src/shannon_core/agents/providers_openai.py \
  packages/core/tests/agents/test_providers.py
git commit -m "feat(openai-engine): 重写 OpenAIProvider 为 openai-agents 引擎（Chat Completions 模式）"
```

---

## Task 9: build_provider_config 支持 SHANNON_OPENAI_* + .env.example

**Files:**
- Modify: `packages/core/src/shannon_core/agents/providers.py`（`build_provider_config`）
- Modify: `.env.example`
- Test: `packages/core/tests/agents/test_providers.py`（增 `TestBuildProviderConfigOpenAI`）

**Interfaces:**
- Consumes: `ProviderConfig`、`build_provider_config`
- Produces: `build_provider_config()` 在 `type ∈ {openai_compatible, litellm_router}` 时，`base_url`/`api_key` 优先读 `SHANNON_OPENAI_BASE_URL` / `SHANNON_OPENAI_API_KEY`，回退现有 `SHANNON_BASE_URL` / `SHANNON_API_KEY` / `OPENAI_API_KEY`

- [ ] **Step 1: 写失败测试**

在 `test_providers.py` 追加：

```python
class TestBuildProviderConfigOpenAI:
    def test_openai_env_precedence(self, monkeypatch):
        from shannon_core.agents.providers import build_provider_config
        monkeypatch.setenv("SHANNON_AI_PROVIDER", "openai_compatible")
        monkeypatch.setenv("SHANNON_OPENAI_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
        monkeypatch.setenv("SHANNON_OPENAI_API_KEY", "glm-key")
        monkeypatch.delenv("SHANNON_BASE_URL", raising=False)
        monkeypatch.delenv("SHANNON_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        cfg = build_provider_config()
        assert cfg.type == "openai_compatible"
        assert cfg.base_url == "https://open.bigmodel.cn/api/paas/v4"
        assert cfg.api_key == "glm-key"

    def test_openai_falls_back_to_shannon_vars(self, monkeypatch):
        from shannon_core.agents.providers import build_provider_config
        monkeypatch.setenv("SHANNON_AI_PROVIDER", "openai_compatible")
        monkeypatch.delenv("SHANNON_OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("SHANNON_OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("SHANNON_BASE_URL", "https://shared/v4")
        monkeypatch.setenv("SHANNON_API_KEY", "shared-key")
        cfg = build_provider_config()
        assert cfg.base_url == "https://shared/v4"
        assert cfg.api_key == "shared-key"

    def test_anthropic_unchanged_by_openai_vars(self, monkeypatch):
        from shannon_core.agents.providers import build_provider_config
        monkeypatch.setenv("SHANNON_AI_PROVIDER", "anthropic_api")
        monkeypatch.setenv("SHANNON_OPENAI_BASE_URL", "https://should-be-ignored/v4")
        cfg = build_provider_config()
        assert cfg.base_url != "https://should-be-ignored/v4"
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd packages/core && python -m pytest tests/agents/test_providers.py::TestBuildProviderConfigOpenAI -v`
Expected: FAIL（`base_url` 还读 `SHANNON_BASE_URL`，忽略 `SHANNON_OPENAI_*`）。

- [ ] **Step 3: 改 build_provider_config**

在 `providers.py` 的 `build_provider_config()` 内，找到现有 base_url / api_key 解析段，改为（替换对应两段）：

```python
    is_openai_family = provider_type in ("openai_compatible", "litellm_router")

    # Base URL - openai 系优先 SHANNON_OPENAI_BASE_URL，否则通用 SHANNON_BASE_URL > ANTHROPIC_BASE_URL
    if base_url is None:
        if is_openai_family:
            base_url = os.getenv("SHANNON_OPENAI_BASE_URL")
        if base_url is None:
            base_url = os.getenv("SHANNON_BASE_URL") or os.getenv("ANTHROPIC_BASE_URL")

    # API Key - openai 系优先 SHANNON_OPENAI_API_KEY，否则 SHANNON_API_KEY > ANTHROPIC_API_KEY > OPENAI_API_KEY
    if api_key is None:
        if is_openai_family:
            api_key = os.getenv("SHANNON_OPENAI_API_KEY")
        if api_key is None:
            api_key = (
                os.getenv("SHANNON_API_KEY")
                or os.getenv("ANTHROPIC_API_KEY")
                or os.getenv("OPENAI_API_KEY")
            )
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd packages/core && python -m pytest tests/agents/test_providers.py::TestBuildProviderConfigOpenAI tests/agents/test_providers.py::TestOpenAIProvider -v`
Expected: PASS（全绿，且原有 anthropic 侧测试不受影响）。

- [ ] **Step 5: 更新 .env.example 并提交**

在 `.env.example` 的「6. OpenAI 兼容 / LiteLLM Router」段补充：

```bash
# SHANNON_AI_PROVIDER=openai_compatible
# 双引擎：openai 系专用变量（优先级高于通用 SHANNON_* 变量）
# SHANNON_OPENAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4   # 智谱 OpenAI 兼容端点
# SHANNON_OPENAI_API_KEY=your-glm-key                            # 缺失时回退 SHANNON_API_KEY / OPENAI_API_KEY
# SHANNON_OPENAI_MAX_TURNS=200                                   # 与 AnthropicProvider 的 CLAUDE_MAX_TURNS 对齐
```

```bash
git add packages/core/src/shannon_core/agents/providers.py .env.example \
  packages/core/tests/agents/test_providers.py
git commit -m "feat(openai-engine): build_provider_config 支持 SHANNON_OPENAI_* 切换"
```

---

## Task 10: 手动冒烟验证（真 GLM，双引擎对照）

**Files:**
- Create: `docs/superpowers/plans/2026-06-17-openai-agents-engine-smoke.md`

> 这是手动验证任务（agent loop 集成测无法 mock 真实 GLM 兼容性），不是自动测试。memory：pytest 全量会 hang，集成验证走冒烟。

**Interfaces:**
- Consumes: Task 1-9 全部交付物

- [ ] **Step 1: 写冒烟 checklist 文档**

`docs/superpowers/plans/2026-06-17-openai-agents-engine-smoke.md`：

```markdown
# OpenAI 引擎手动冒烟 checklist

前置：在 .env 配好智谱 GLM OpenAI 兼容端点
```
SHANNON_AI_PROVIDER=openai_compatible
SHANNON_OPENAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4
SHANNON_OPENAI_API_KEY=<glm key>
SHANNON_LARGE_MODEL=GLM-5.2[1m]
SHANNON_MEDIUM_MODEL=GLM-5.2[1m]
SHANNON_SMALL_MODEL=GLM-4.5-Air
```

## 最小冒烟（单 agent，验 loop + tool calling）
- [ ] 跑一个会触发 bash 工具的简单 agent（如 pre-recon 子步），确认：
  - [ ] `ClaudeRunResult.success == True`
  - [ ] `ClaudeRunResult.turns > 1`（证明 tool use loop 跑了多轮）
  - [ ] audit 落库含 `log_tool_start("bash", ...)` + `log_tool_end(...)` + `log_assistant_turn`
  - [ ] `ClaudeRunResult.tokens.input_tokens/output_tokens > 0`（验证 include_usage 生效）

## 双引擎对照
- [ ] 同一 agent 同一输入，分别用 `SHANNON_AI_PROVIDER=anthropic_api` 和 `openai_compatible` 各跑一次
- [ ] 两者都能正常结束、产出可比结果

## 兼容性专项
- [ ] 确认 `open.bigmodel.cn/api/paas/v4` 是智谱当前 OpenAI 兼容端点（核对智谱文档）
- [ ] 确认 GLM-5.2[1m] 在该端点支持 function calling（tool calling）
- [ ] 若 usage 为 0，确认 `ModelSettings(include_usage=True)` 对该端点的流式生效

## 回归
- [ ] `SHANNON_AI_PROVIDER=anthropic_api` 下原有渗透流水线不受影响（AnthropicProvider 未改动）
```

- [ ] **Step 2: 执行冒烟（由人工或执行 agent 在配好 .env 的环境跑）**

按 checklist 跑最小冒烟。记录任何失败项，作为后续修复输入。

- [ ] **Step 3: 提交冒烟文档**

```bash
git add docs/superpowers/plans/2026-06-17-openai-agents-engine-smoke.md
git commit -m "docs(openai-engine): 新增 OpenAI 引擎手动冒烟 checklist"
```

---

## Self-Review

**Spec coverage**（逐节核对 `2026-06-17-openai-agents-engine-design.md`）：
- §5.1 OpenAIProvider 重写 / Chat Completions 接入 → Task 8 ✓（显式 `OpenAIChatCompletionsModel`，`set_tracing_disabled`，`ModelSettings(include_usage=True)`）。
- §5.2 工具套件（8 个核心） → Task 1-4 ✓；剔除 Task/MultiEdit/NotebookEdit/TodoWrite ✓。
- §5.3 loop / max_turns → Task 8（`Runner.run_streamed` + `MaxTurnsExceeded` 映射 `stop_reason`）✓。
- §5.4 产出对齐 → Task 7（`map_run_result`）✓。
- §5.5 逐轮 audit → Task 6（`StreamCollector` 调 `log_assistant_turn`/`log_tool_start`/`log_tool_end`）✓。
- §5.6 结构化输出 → Task 7（`output_format` → `structured_output` JSON 解析）✓。
- §5.7 配置 → Task 9（`SHANNON_OPENAI_*` + `SHANNON_OPENAI_MAX_TURNS`）✓。
- §5.8 GitNexus 不动 → 无 task 涉及 ✓。
- §11 核实点：web_search 搜索源（Task 4 用 DDG Lite，冒烟 checklist §兼容性 标注可替换）✓；GLM base_url（Task 10 checklist 核对）✓；openai-agents 版本（Global Constraints 锁 0.17.5）✓。

**Placeholder scan**：无 TBD/TODO；每个工具与映射均有完整实现代码；Task 8/10 的"集成/冒烟"显式说明为何不写自动测试（mock 限制 + 真实兼容性）。

**Type consistency**：`ToolContext.cwd`、`build_tools()`、`StreamCollector.{on_event,close,turns,text,tool_call_count}`、`map_run_result(run_result, *, duration_ms, model, turns, stop_reason=None, output_format=None)` 在各 task 间命名一致；`OpenAIProvider.build_agent(model, output_format)` 在 Task 8 定义并被其测试消费。

**未覆盖项（已知限制，非 spec 要求）**：GLM 对 agents SDK 某些字段（strict json schema、parallel_tool_calls）的兼容性留待 Task 10 冒烟暴露；若冒烟失败，新增小 task 处理（如关闭 strict/parallel）。

---

## Execution Handoff

计划已保存到 `docs/superpowers/plans/2026-06-17-openai-agents-engine.md`。两种执行方式：

1. **Subagent-Driven（推荐）**：我每个 task 派一个全新 subagent，task 间 review，快速迭代。
2. **Inline Execution**：在本会话用 executing-plans 批量执行，带 checkpoint。

选哪种？
