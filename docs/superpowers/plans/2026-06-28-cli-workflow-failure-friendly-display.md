# CLI workflow 失败友好展示 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 黑白盒 CLI 在 workflow 失败时打印一行人话（诊断 + 建议），不再裸抛 temporalio 堆栈。

**Architecture:** 方案 A——在 `shannon_core` 新增共享渲染层 `cli/error_render.py`（`extract_root_cause` 沿 `.cause`/`__cause__` 链挖根因 → 查 `FRIENDLY_HINTS` 映射 → `format_workflow_failure` 组装友好串；`persist_workflow_traceback` 落盘 `activity_failures.log`）；黑白盒 CLI 的 `start` 命令各包一层 `try/except Exception` 调用它，并加 `--debug` flag。worker / activity / retry policy / `security.py` 一律不动。

**Tech Stack:** Python 3.13、click（CliRunner 测试）、temporalio（`ApplicationError`/`ActivityError`/`WorkflowFailureError` 的 `.type`/`.cause` 属性）、pytest。

**Spec:** `docs/superpowers/specs/2026-06-28-cli-workflow-failure-friendly-display-design.md`

## Global Constraints

- **不改** `worker.py`、`pipeline/activities.py`、temporalio retry policy、`utils/security.py`（loopback/SSRF 保护不变）。
- **语义分类单一来源**：友好展示复用 `shannon_core.models.errors.classify_error_for_temporal`，不另造分类逻辑。
- **落盘路径**：`activity_failures.log` 位于 workspace 目录根（`generate_audit_path(meta)` = `<workspaces_root>/<workspace_name>`，见 `shannon_core/audit/utils.py:17`）。`persist_workflow_traceback` 只需 `workspace_dir`，不依赖 `SessionMetadata`。
- **temporalio 异常属性**（已源码确认）：`ApplicationError` 有 `.type`；`ApplicationError`/`ActivityError`/`FailureError` 有 `.cause`（temporalio 属性，区别于 Python `__cause__`）。`WorkflowFailureError`（来自 `temporalio.client`）不继承 `FailureError`，故 CLI 用宽 `except Exception` 兜底。
- **测试只跑改动相关文件**（CLAUDE.md「测试陷阱」：全套会 hang）。命令统一从 repo 根：`uv run pytest <path> -v`。
- **文案用中文**（项目惯例）；代码标识符、文件名保持原文。

## File Structure

- **新增** `packages/core/src/shannon_core/cli/error_render.py` — 共享渲染层（根因提取 + 映射 + 组装 + 落盘），单一职责，黑白盒共用。
- **新增** `packages/core/tests/test_cli_error_render.py` — core 渲染层单测。
- **改** `packages/blackbox/src/shannon_blackbox/cli/main.py` — `start` 加 `--debug` + `try/except`。
- **改** `packages/whitebox/src/shannon_whitebox/cli/main.py` — `start` 加 `--debug` + `try/except`。
- **改** `packages/blackbox/tests/test_cli.py` — 加 workflow-failure / `--debug` / help 三测试。
- **改** `packages/whitebox/tests/test_cli.py` — 加 workflow-failure / `--debug` 两测试。
- **新增** `packages/blackbox/tests/test_cli_error_guard.py` — AST 锚点：`run_scan(...)` 必须在 `try` 内。
- **新增** `packages/whitebox/tests/test_cli_error_guard.py` — 同上（白盒）。

---

### Task 1: core `extract_root_cause` — 沿异常链挖根因

**Files:**
- Create: `packages/core/src/shannon_core/cli/error_render.py`
- Test: `packages/core/tests/test_cli_error_render.py`

**Interfaces:**
- Consumes: `shannon_core.models.errors.classify_error_for_temporal(exc) -> (error_type: str, retryable: bool)`
- Produces:
  - `RootCause`（dataclass）：`error_type: str`、`message: str`
  - `extract_root_cause(exc: Exception) -> RootCause`

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/test_cli_error_render.py`:

```python
"""core CLI 错误渲染层单测：根因提取 / 映射 / 落盘。"""
from pathlib import Path

from temporalio.exceptions import ApplicationError

from shannon_core.cli.error_render import extract_root_cause


class _FakeTemporalError(Exception):
    """模拟 temporalio 异常的 .cause / .type 属性链（不依赖内部构造签名）。"""

    def __init__(self, message: str, cause=None, type: str | None = None):
        super().__init__(message)
        self.cause = cause
        self.type = type


def test_root_cause_via_cause_attr_chain():
    """temporalio .cause 属性链：外层无 type，内层 ApplicationError 带 type → 取内层 type/message。"""
    inner = ApplicationError(
        "Target http://localhost:4000 resolves to loopback address 127.0.0.1",
        type="InvalidTargetError",
    )
    outer = _FakeTemporalError("workflow failed", cause=inner)
    rc = extract_root_cause(outer)
    assert rc.error_type == "InvalidTargetError"
    assert "loopback" in rc.message


def test_root_cause_via_dunder_cause_chain():
    """Python __cause__ 链（raise X from Y）同样能挖到带 type 的内层。"""
    inner = ApplicationError("deep config err", type="ConfigurationError")
    outer = RuntimeError("wrap")
    outer.__cause__ = inner
    rc = extract_root_cause(outer)
    assert rc.error_type == "ConfigurationError"
    assert "deep config err" in rc.message


def test_root_cause_falls_back_to_classify_when_no_type():
    """链上无 .type 时，对最深层异常跑 classify 兜底分类。"""
    rc = extract_root_cause(ValueError("authentication failed boom"))
    assert rc.error_type == "AuthenticationError"  # classify 命中 "authentication"


def test_root_cause_message_from_deepest():
    rc = extract_root_cause(RuntimeError("shallow msg"))
    assert rc.message == "shallow msg"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/test_cli_error_render.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shannon_core.cli.error_render'`

- [ ] **Step 3: Write minimal implementation**

Create `packages/core/src/shannon_core/cli/error_render.py`:

```python
"""CLI workflow 失败友好展示共享层。

黑白盒 CLI 的 start 命令在 run_scan 抛异常时调用本模块：从层层包装的
temporalio 异常（WorkflowFailureError → ActivityError → ApplicationError）里
挖出根因（error_type + message），映射成人话诊断 + 建议；完整 traceback 落
activity_failures.log。worker / activity / retry policy 都不感知本模块。

设计见 docs/superpowers/specs/2026-06-28-cli-workflow-failure-friendly-display-design.md。
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from shannon_core.models.errors import classify_error_for_temporal


@dataclass
class RootCause:
    error_type: str
    message: str


def _walk_cause_chain(exc: Exception) -> list[Exception]:
    """沿 temporalio ``.cause`` 属性 + Python ``__cause__`` 链收集异常（从外到内）。

    temporalio 异常用 ``.cause`` 属性链接（ActivityError.cause → ApplicationError），
    activity 内 ``raise ApplicationFailure(...) from e`` 另设 ``__cause__``；两路都走。
    """
    chain: list[Exception] = []
    seen: set[int] = set()
    cur: Exception | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        chain.append(cur)
        nxt = getattr(cur, "cause", None) or cur.__cause__
        cur = nxt if isinstance(nxt, Exception) else None
    return chain


def extract_root_cause(exc: Exception) -> RootCause:
    """挖到最深层异常：优先取链上带 ``.type`` 的 temporalio 异常的 type，否则 classify 兜底。"""
    chain = _walk_cause_chain(exc)
    deepest = chain[-1]

    error_type: str | None = None
    for err in chain:  # 从外到内，后写者更深、覆盖前者
        t = getattr(err, "type", None)
        if t:
            error_type = t
    if not error_type:
        error_type = classify_error_for_temporal(deepest)[0]

    message = str(deepest) or str(exc)
    return RootCause(error_type=error_type, message=message)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/test_cli_error_render.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/cli/error_render.py packages/core/tests/test_cli_error_render.py
git commit -m "feat(core): extract_root_cause 沿异常链挖 workflow 失败根因"
```

---

### Task 2: core `FRIENDLY_HINTS` + `format_workflow_failure` — 映射 + 组装友好串

**Files:**
- Modify: `packages/core/src/shannon_core/cli/error_render.py`
- Test: `packages/core/tests/test_cli_error_render.py`

**Interfaces:**
- Consumes: Task 1 的 `extract_root_cause`、`RootCause`
- Produces: `format_workflow_failure(exc: Exception) -> str`（纯函数，不含落盘 / `--debug` 信息——那两样由 CLI 层补）

- [ ] **Step 1: Write the failing test**

Append to `packages/core/tests/test_cli_error_render.py`:

```python
from shannon_core.cli.error_render import format_workflow_failure


def test_format_loopback_target():
    exc = ApplicationError(
        "Target http://localhost:4000 resolves to loopback address 127.0.0.1",
        type="InvalidTargetError",
    )
    out = format_workflow_failure(exc)
    assert "InvalidTargetError" in out
    assert "loopback" in out.lower() or "本机" in out
    assert "SSRF" in out or "ssrf" in out.lower()


def test_format_ssrf_target():
    exc = ApplicationError(
        "Target resolves to SSRF-sensitive IP 169.254.1.1", type="InvalidTargetError"
    )
    out = format_workflow_failure(exc)
    assert "169.254" in out or "SSRF" in out


def test_format_unresolvable_target():
    exc = ApplicationError("Cannot resolve hostname for http://x", type="InvalidTargetError")
    out = format_workflow_failure(exc)
    assert "解析" in out or "resolve" in out.lower()


def test_format_configuration_error():
    exc = ApplicationError("config missing field", type="ConfigurationError")
    out = format_workflow_failure(exc)
    assert "ConfigurationError" in out
    assert "配置" in out


def test_format_unknown_type_falls_back():
    """未命中映射的 error_type 走通用兜底（含原始 message）。"""
    out = format_workflow_failure(RuntimeError("something weird boom"))
    assert "TransientError" in out  # classify(RuntimeError 未知) → TransientError
    assert "something weird boom" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/test_cli_error_render.py -v`
Expected: FAIL with `ImportError: cannot import name 'format_workflow_failure'`

- [ ] **Step 3: Write minimal implementation**

Append to `packages/core/src/shannon_core/cli/error_render.py` (after `extract_root_cause`):

```python
def _invalid_target_hint(message: str) -> str:
    """InvalidTargetError 按 message 子串区分 loopback / SSRF / 不可解析三支。"""
    msg = message.lower()
    if "loopback" in msg:
        return (
            "目标解析到本机 loopback 地址。黑盒扫描不允许扫 loopback/内网地址（SSRF 防护）。\n"
            "  建议：用公网地址，或目标容器在宿主网络可达的地址。"
        )
    if "ssrf" in msg or "169.254" in msg:
        return "目标解析到 SSRF 敏感网段（169.254.x.x）。\n  建议：换非链路本地地址。"
    if "cannot resolve" in msg or "resolve" in msg:
        return "无法解析目标域名。\n  建议：检查 URL 拼写 / DNS / 目标是否启动。"
    return f"目标地址无效：{message}\n  建议：检查目标 URL。"


# error_type → 人话诊断 + 建议。callable 接收原始 message（用于按子串细分）。
FRIENDLY_HINTS: dict[str, str | Callable[[str], str]] = {
    "InvalidTargetError": _invalid_target_hint,
    "ConfigurationError": "配置或必要文件有问题。\n  建议：检查 profile / config 文件。",
    "AuthenticationError": "鉴权失败。\n  建议：检查 API key / profile 配置。",
    "AuthLoginFailedError": "目标登录失败。\n  建议：检查登录流程配置 / 凭据。",
    "GitError": "Git 操作失败。\n  建议：检查仓库路径 / git 可用性。",
    "PermissionError": "权限不足。\n  建议：检查访问权限 / token。",
}


def format_workflow_failure(exc: Exception) -> str:
    """组装多行友好串。落盘 / --debug 提示由 CLI 层补充（保持本函数纯）。"""
    rc = extract_root_cause(exc)
    hint = FRIENDLY_HINTS.get(rc.error_type)
    if callable(hint):
        detail = hint(rc.message)
    elif isinstance(hint, str):
        detail = hint
    else:
        detail = f"扫描因 {rc.error_type} 失败：{rc.message}"
    return f"✗ 扫描失败：{rc.error_type}\n  {detail}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/test_cli_error_render.py -v`
Expected: PASS (9 tests: 4 from Task 1 + 5 here)

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/cli/error_render.py packages/core/tests/test_cli_error_render.py
git commit -m "feat(core): format_workflow_failure 映射 error_type 到人话诊断"
```

---

### Task 3: core `persist_workflow_traceback` — 完整堆栈落 activity_failures.log

**Files:**
- Modify: `packages/core/src/shannon_core/cli/error_render.py`
- Test: `packages/core/tests/test_cli_error_render.py`

**Interfaces:**
- Produces: `persist_workflow_traceback(exc: Exception, workspace_dir: Path | None) -> Path | None`（best-effort：`workspace_dir` 为 None 或写失败时返回 None，绝不抛）

- [ ] **Step 1: Write the failing test**

Append to `packages/core/tests/test_cli_error_render.py`:

```python
from shannon_core.cli.error_render import persist_workflow_traceback


def test_persist_writes_activity_failures_log(tmp_path):
    exc = ApplicationError("boom", type="InvalidTargetError")
    path = persist_workflow_traceback(exc, tmp_path)
    assert path == tmp_path / "activity_failures.log"
    content = path.read_text(encoding="utf-8")
    assert "boom" in content
    assert "workflow-level failure" in content


def test_persist_returns_none_when_no_workspace():
    assert persist_workflow_traceback(RuntimeError("x"), None) is None


def test_persist_appends_to_existing(tmp_path):
    log = tmp_path / "activity_failures.log"
    log.write_text("PREEXISTING\n", encoding="utf-8")
    persist_workflow_traceback(ApplicationError("second"), tmp_path)
    content = log.read_text(encoding="utf-8")
    assert "PREEXISTING" in content
    assert "second" in content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/test_cli_error_render.py -v`
Expected: FAIL with `ImportError: cannot import name 'persist_workflow_traceback'`

- [ ] **Step 3: Write minimal implementation**

Append to `packages/core/src/shannon_core/cli/error_render.py` (after `format_workflow_failure`):

```python
def persist_workflow_traceback(exc: Exception, workspace_dir: Path | None) -> Path | None:
    """把完整 traceback append 到 ``<workspace_dir>/activity_failures.log``（best-effort）。

    workspace_dir 为 None（如 standalone 黑盒无 workspace）或写失败时返回 None；
    调用方据此决定是否提示「加 --debug 看堆栈」。绝不抛异常（别让落盘盖过友好展示）。
    """
    if workspace_dir is None:
        return None
    try:
        workspace_dir.mkdir(parents=True, exist_ok=True)
        log_path = workspace_dir / "activity_failures.log"
        tb = "".join(traceback.format_exception(exc))
        with log_path.open("a", encoding="utf-8") as f:
            f.write("\n=== workflow-level failure ===\n")
            f.write(tb)
        return log_path
    except OSError:
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/test_cli_error_render.py -v`
Expected: PASS (12 tests total)

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/cli/error_render.py packages/core/tests/test_cli_error_render.py
git commit -m "feat(core): persist_workflow_traceback 落盘完整堆栈到 activity_failures.log"
```

---

### Task 4: 黑盒 CLI 接入 try/except + `--debug`

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/cli/main.py`（`start` 签名约 `:53`、`use_rich`/`run_scan` 调用约 `:131-146`）
- Test: `packages/blackbox/tests/test_cli.py`

**Interfaces:**
- Consumes: Task 2/3 的 `format_workflow_failure`、`persist_workflow_traceback`；`BlackboxPipelineInput.workspaces_root` + `.workspace_name`
- Produces: `start` 新增 `--debug` flag；`asyncio.run(run_scan(...))` 外包 `try/except Exception`

- [ ] **Step 1: Write the failing test**

Append to `packages/blackbox/tests/test_cli.py`:

```python
def test_start_workflow_failure_shows_friendly_and_exits_1(tmp_path, monkeypatch):
    """run_scan 抛 ApplicationFailure → CLI 友好展示 + exit 1，不裸抛 traceback。"""
    from temporalio.exceptions import ApplicationError

    err = ApplicationError(
        "Target http://localhost:4000 resolves to loopback address 127.0.0.1",
        type="InvalidTargetError",
    )
    monkeypatch.chdir(tmp_path)
    ep = _patch_env_profile()
    with (
        ep[0], ep[1],
        patch("shannon_blackbox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("shannon_blackbox.cli.main.find_latest_workspace", return_value=None),
        patch("shannon_core.runtime.prerequisites.ensure_prerequisite"),
        patch("shannon_blackbox.worker.run_scan", side_effect=err),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "http://localhost:4000"])

    assert result.exit_code == 1
    assert "InvalidTargetError" in result.output
    assert "loopback" in result.output.lower() or "本机" in result.output
    assert "--debug" in result.output
    assert "Traceback" not in result.output  # 默认不裸抛堆栈


def test_start_workflow_failure_debug_prints_traceback(tmp_path, monkeypatch):
    """--debug 时除友好串外，额外把完整 traceback 打到 stderr。"""
    from temporalio.exceptions import ApplicationError

    err = ApplicationError("boom loopback detail", type="InvalidTargetError")
    monkeypatch.chdir(tmp_path)
    ep = _patch_env_profile()
    with (
        ep[0], ep[1],
        patch("shannon_blackbox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("shannon_blackbox.cli.main.find_latest_workspace", return_value=None),
        patch("shannon_core.runtime.prerequisites.ensure_prerequisite"),
        patch("shannon_blackbox.worker.run_scan", side_effect=err),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "http://localhost:4000", "--debug"])

    assert result.exit_code == 1
    assert "Traceback" in result.output  # --debug 打了堆栈（CliRunner mix_stderr）


def test_start_help_shows_debug_option():
    runner = CliRunner()
    result = runner.invoke(cli, ["start", "--help"])
    assert result.exit_code == 0
    assert "--debug" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/blackbox/tests/test_cli.py::test_start_workflow_failure_shows_friendly_and_exits_1 -v`
Expected: FAIL — `run_scan` 抛出的异常未被捕获，`result.exit_code` 为 1 但 `result.output` 含 `Traceback`（裸抛），且断言 `"InvalidTargetError" in result.output` 失败 / `"--debug" in result.output` 失败。

- [ ] **Step 3: Write minimal implementation**

In `packages/blackbox/src/shannon_blackbox/cli/main.py`:

(a) 给 `start` 加 `--debug` flag。在 `--rerun` option 之后、`def start(...)` 之前加：

```python
@click.option("--debug", is_flag=True, help="扫描失败时在终端打印完整堆栈（调试用）")
```

并把 `debug` 追加到 `def start` 参数列表末尾：

```python
def start(url, repo, output, workspace, latest, config_path, vuln_classes, no_exploit, pipeline_testing, temporal_address, max_concurrent, retry_profile, plain, rerun, correlated_workspace, debug):
```

(b) 把 `use_rich = ...` / `result = asyncio.run(run_scan(...))` 段（约 `:131-133`）包进 try/except。当前代码：

```python
    import sys
    use_rich = sys.stdout.isatty() and not plain
    result = asyncio.run(run_scan(input, temporal_address, use_rich=use_rich))
```

改为：

```python
    import sys
    use_rich = sys.stdout.isatty() and not plain
    from shannon_core.utils.paths import resolve_workspaces_dir
    try:
        result = asyncio.run(run_scan(input, temporal_address, use_rich=use_rich))
    except Exception as e:
        from shannon_core.cli.error_render import format_workflow_failure, persist_workflow_traceback
        workspace_dir = None
        if input.workspaces_root and input.workspace_name:
            workspace_dir = Path(input.workspaces_root) / input.workspace_name
        log_path = persist_workflow_traceback(e, workspace_dir)
        click.echo(format_workflow_failure(e))
        if log_path:
            click.echo(f"  完整错误已记录到 {log_path}")
        click.echo("  加 --debug 可在终端查看完整堆栈。")
        if debug:
            import traceback as _tb
            _tb.print_exc()
        raise SystemExit(1)
```

> 说明：`Path` 已在文件顶部 `from pathlib import Path` 导入；`input.workspaces_root` 由本命令 `:106` 处 `resolve_workspaces_dir(repo_path_resolved)` 填充（恒非 None），`workspace_name` 在 standalone 模式为 None → 跳过落盘（best-effort，符合 spec §5.4）。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/blackbox/tests/test_cli.py -v`
Expected: PASS（含新增 3 个 + 原有全绿；若原有 standalone/latest 测试因 env 失败，确认是 pre-existing 而非本改动引入）

- [ ] **Step 5: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/cli/main.py packages/blackbox/tests/test_cli.py
git commit -m "feat(blackbox): start 友好展示 workflow 失败 + --debug"
```

---

### Task 5: 白盒 CLI 接入 try/except + `--debug`

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/cli/main.py`（`start` 签名约 `:44`、`use_rich`/`run_scan` 调用约 `:68-70`）
- Test: `packages/whitebox/tests/test_cli.py`

**Interfaces:**
- Consumes: Task 2/3 的 `format_workflow_failure`、`persist_workflow_traceback`；`PipelineInput.repo_path` + `.workspace_name`（白盒无 `workspaces_root`，由 `resolve_workspaces_dir(repo_path)` 推导）
- Produces: `start` 新增 `--debug` flag；`asyncio.run(run_scan(...))` 外包 `try/except Exception`

- [ ] **Step 1: Write the failing test**

Append to `packages/whitebox/tests/test_cli.py`:

```python
def test_start_workflow_failure_shows_friendly_and_exits_1():
    """run_scan 抛 ApplicationFailure → CLI 友好展示 + exit 1，不裸抛 traceback。"""
    from temporalio.exceptions import ApplicationError

    err = ApplicationError(
        "Target http://localhost:4000 resolves to loopback address 127.0.0.1",
        type="InvalidTargetError",
    )
    with (
        patch("shannon_whitebox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("shannon_core.runtime.prerequisites.ensure_prerequisite"),
        patch("shannon_whitebox.worker.run_scan", side_effect=err),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--repo", "/tmp/fake"])

    assert result.exit_code == 1
    assert "InvalidTargetError" in result.output
    assert "loopback" in result.output.lower() or "本机" in result.output
    assert "--debug" in result.output
    assert "Traceback" not in result.output


def test_start_workflow_failure_debug_prints_traceback():
    """--debug 时额外把完整 traceback 打到 stderr。"""
    from temporalio.exceptions import ApplicationError

    err = ApplicationError("boom loopback detail", type="InvalidTargetError")
    with (
        patch("shannon_whitebox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("shannon_core.runtime.prerequisites.ensure_prerequisite"),
        patch("shannon_whitebox.worker.run_scan", side_effect=err),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--repo", "/tmp/fake", "--debug"])

    assert result.exit_code == 1
    assert "Traceback" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/whitebox/tests/test_cli.py::test_start_workflow_failure_shows_friendly_and_exits_1 -v`
Expected: FAIL — 异常裸抛，`"InvalidTargetError" in result.output` / `"--debug" in result.output` 不成立。

- [ ] **Step 3: Write minimal implementation**

In `packages/whitebox/src/shannon_whitebox/cli/main.py`:

(a) 给 `start` 加 `--debug` flag。在现有 `@click.option(... rewind ...)` 之后加：

```python
@click.option("--debug", is_flag=True, help="扫描失败时在终端打印完整堆栈（调试用）")
```

并把 `debug` 追加到 `def start` 参数列表末尾（当前签名 `def start(repo, output, workspace, config_path, pipeline_testing, temporal_address, plain, url, fresh, rewind)`）：

```python
def start(repo, output, workspace, config_path, pipeline_testing, temporal_address, plain, url, fresh, rewind, debug):
```

(b) 把 `use_rich = ...` / `result = asyncio.run(run_scan(...))` 段（约 `:68-70`）包进 try/except。当前代码：

```python
    import sys
    use_rich = sys.stdout.isatty() and not plain
    result = asyncio.run(run_scan(input, temporal_address, use_rich=use_rich))
```

改为：

```python
    import sys
    use_rich = sys.stdout.isatty() and not plain
    from shannon_core.utils.paths import resolve_workspaces_dir
    try:
        result = asyncio.run(run_scan(input, temporal_address, use_rich=use_rich))
    except Exception as e:
        from shannon_core.cli.error_render import format_workflow_failure, persist_workflow_traceback
        workspace_dir = None
        if input.workspace_name:
            workspace_dir = Path(resolve_workspaces_dir(input.repo_path)) / input.workspace_name
        log_path = persist_workflow_traceback(e, workspace_dir)
        click.echo(format_workflow_failure(e))
        if log_path:
            click.echo(f"  完整错误已记录到 {log_path}")
        click.echo("  加 --debug 可在终端查看完整堆栈。")
        if debug:
            import traceback as _tb
            _tb.print_exc()
        raise SystemExit(1)
```

> 说明：确认 `from pathlib import Path` 已在文件顶部（白盒 main.py 顶部已 `from pathlib import Path`）；若未导入则补。`PipelineInput` 无 `workspaces_root`，故由 `resolve_workspaces_dir(input.repo_path)` 推导（spec §5.4）。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/whitebox/tests/test_cli.py -v`
Expected: PASS（新增 2 个 + 原有全绿）

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/cli/main.py packages/whitebox/tests/test_cli.py
git commit -m "feat(whitebox): start 友好展示 workflow 失败 + --debug"
```

---

### Task 6: AST 防回归锚点 — `run_scan(...)` 必须在 `try` 内

**Files:**
- Create: `packages/blackbox/tests/test_cli_error_guard.py`
- Create: `packages/whitebox/tests/test_cli_error_guard.py`

**Interfaces:**
- Consumes: 无（纯 AST 解析源文件）
- Produces: 防回归断言——黑白盒 `start` 内的 `run_scan(...)` 调用必须位于 `ast.Try` 块内

- [ ] **Step 1: Write the failing test (黑盒)**

Create `packages/blackbox/tests/test_cli_error_guard.py`:

```python
"""防回归：blackbox start 内的 run_scan(...) 必须位于 try 块内。

CLI 顶层 try/except 是 workflow 失败友好展示的不变量（见
docs/superpowers/specs/2026-06-28-cli-workflow-failure-friendly-display-design.md §8）。
有人若误删 try/except 会让裸 traceback 回归，本锚点守住。
"""
import ast
from pathlib import Path

CLI_FILE = (
    Path(__file__).resolve().parents[1]
    / "src" / "shannon_blackbox" / "cli" / "main.py"
)


def _start_func(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "start":
            return node
    return None


def test_run_scan_call_is_inside_try():
    tree = ast.parse(CLI_FILE.read_text(encoding="utf-8"))
    start = _start_func(tree)
    assert start is not None, "未找到 start 命令"

    try_nodes = [n for n in ast.walk(start) if isinstance(n, ast.Try)]
    run_scan_calls = [
        n for n in ast.walk(start)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name) and n.func.id == "run_scan"
    ]
    assert run_scan_calls, "start 内未找到 run_scan(...) 调用"

    for call in run_scan_calls:
        inside = any(call in ast.walk(t) for t in try_nodes)
        assert inside, (
            "run_scan(...) 必须位于 try 块内（CLI 顶层友好错误展示不变量）。"
        )
```

- [ ] **Step 2: Write the same guard for 白盒**

Create `packages/whitebox/tests/test_cli_error_guard.py`（与黑盒同构，仅 `CLI_FILE` 路径不同）:

```python
"""防回归：whitebox start 内的 run_scan(...) 必须位于 try 块内。"""
import ast
from pathlib import Path

CLI_FILE = (
    Path(__file__).resolve().parents[1]
    / "src" / "shannon_whitebox" / "cli" / "main.py"
)


def _start_func(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "start":
            return node
    return None


def test_run_scan_call_is_inside_try():
    tree = ast.parse(CLI_FILE.read_text(encoding="utf-8"))
    start = _start_func(tree)
    assert start is not None, "未找到 start 命令"

    try_nodes = [n for n in ast.walk(start) if isinstance(n, ast.Try)]
    run_scan_calls = [
        n for n in ast.walk(start)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name) and n.func.id == "run_scan"
    ]
    assert run_scan_calls, "start 内未找到 run_scan(...) 调用"

    for call in run_scan_calls:
        inside = any(call in ast.walk(t) for t in try_nodes)
        assert inside, (
            "run_scan(...) 必须位于 try 块内（CLI 顶层友好错误展示不变量）。"
        )
```

> 说明：`run_scan(...)` 的 AST 是 `asyncio.run(run_scan(...))` 的内层 Call（`func=Name(id="run_scan")`）；`call in ast.walk(try_node)` 用身份比较（walk 不复制节点），命中即在该 try 内。`from ... import run_scan` 是 ImportFrom，不产生 ast.Call，不会误匹配。

- [ ] **Step 3: Run tests to verify they pass**（Task 4/5 已让 run_scan 进 try，故锚点直接绿）

Run: `uv run pytest packages/blackbox/tests/test_cli_error_guard.py packages/whitebox/tests/test_cli_error_guard.py -v`
Expected: PASS (2 tests)

- [ ] **Step 4: Verify the guard catches regression (手动 sanity)**

临时确认锚点有效：把黑盒 `start` 的 `try:` / `except Exception as e:` 两行注释掉、重跑 `uv run pytest packages/blackbox/tests/test_cli_error_guard.py -v`，应 FAIL（"必须位于 try 块内"）；确认后 `git checkout -- packages/blackbox/src/shannon_blackbox/cli/main.py` 还原。

- [ ] **Step 5: Commit**

```bash
git add packages/blackbox/tests/test_cli_error_guard.py packages/whitebox/tests/test_cli_error_guard.py
git commit -m "test: AST 锚点守 run_scan 必须在 try 内（防裸抛回归）"
```

---

## Self-Review（写完后自查通过）

**Spec coverage：**
- §5.2 `extract_root_cause` → Task 1 ✓
- §5.2 `FRIENDLY_HINTS` + `format_workflow_failure` → Task 2 ✓
- §5.2 `persist_workflow_traceback` → Task 3 ✓
- §5.3 映射表（InvalidTargetError 三分支 / ConfigurationError / AuthenticationError / GitError / 通用兜底）→ Task 2 ✓
- §5.4 落点 `activity_failures.log` + standalone 降级 → Task 3 + Task 4/5（workspace_name None 时跳过）✓
- §5.5 `--debug` → Task 4/5 ✓
- §6 测试锚点（error_render 单测 + CliRunner + 落盘 + AST）→ Task 1-6 ✓
- §8 不变量（CLI 顶层捕获 / 分类单一来源 / traceback 不丢）→ Task 4/5/6 + 复用 classify ✓

**Placeholder scan：** 无 TBD/TODO；每个代码步都是完整可运行代码。✓

**Type consistency：** `RootCause.error_type/message`（Task 1）在 Task 2 `format_workflow_failure` 用法一致；`format_workflow_failure(exc)->str`、`persist_workflow_traceback(exc, workspace_dir)->Path|None` 在 Task 4/5 调用一致；`--debug` flag 名黑白盒一致。✓
