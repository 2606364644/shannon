# events.ndjson SSOT · Plan A（日志一致性）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修裂痕四（worker 容器漏调 `configure_logging`）让 `LogEvent` 进 events.ndjson/diagnostic.log；前端 LogStream 渲染 LogEvent；CLI 加 `logs --full` 全量文本视图；bb_worker 并发=1。

**Architecture:** worker 容器入口（白盒 `setup_display` activity）在 `session.initialize` 前补 `configure_logging(ws/logs)` 单点（CLI 已自配，不碰 `run_with_display`）；前端 LogStream 按 `level` 字段着色（绕过 EventCategory 枚举）；core 新增同步 `render_event_line` + `tail_events_ndjson` 复用 `display/formatters.py` 纯函数；bb_worker 加 `max_concurrent_workflow_tasks=1` 对齐 wb_worker。

**Tech Stack:** Python 3.12 / pytest / temporalio / React + TypeScript + vitest

**对应 spec：** `docs/superpowers/specs/2026-07-16-events-ndjson-ssot-design.md`（改动 1/2/3/5；改动 4 黑盒 C1 化在 Plan B）

## Global Constraints

- **保 clean separation**：`workflow.log` 仍不收 LogEvent——回归测试 `packages/core/tests/display/test_logevent_render.py::test_logevent_not_written_to_workflow_log` 必须全程绿。
- **不碰 `run_with_display`**（CLI 三入口 `main.py` 已 `configure_logging(ws/logs)`；碰它有 meta 路径 ≠ main.py 路径风险）。
- **并发=1 是正确性前提**：wb_worker 已 `max_concurrent_workflow_tasks=1`（`runner.py:71`）；bb_worker 本次对齐。LogBus 单例 + AuditSession 全局单例要求并发=1。
- **LogEvent.category = levelname 动态字符串**（"INFO"/"WARNING"/"ERROR"/"DEBUG"），**不在** 前端 `EventCategory` 枚举（只有 "WARN" 无 "WARNING"）→ 前端着色必须基于 `level` 字段，绕过 `CAT_CLASS`。
- **测试只跑改动相关文件**，勿广跑全套（CLAUDE.md：全套 pytest 有预存挂起/失败）。

## File Structure

| 文件 | 责任 | 改动 |
|---|---|---|
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | `setup_display` activity | Task 1：`session.initialize` 前加 `configure_logging` |
| `packages/whitebox/tests/pipeline/test_migration_activities.py` | setup_display 测试 | Task 1：加 configure_logging 测试 |
| `packages/worker/src/shannon_worker/runner.py` | bb_worker 构造 | Task 2：加 `max_concurrent_workflow_tasks=1` |
| `packages/worker/tests/test_runner.py` | runner 测试 | Task 2：加并发断言 |
| `packages/web/frontend/src/api/types.ts` | NdjsonEvent 联合类型 | Task 3：加 `LogEventEvent` |
| `packages/web/frontend/src/components/LogStream.tsx` | live 页事件渲染 | Task 3：`summarize` + `rowClass` 加 LogEvent |
| `packages/web/frontend/src/components/LogStream.test.tsx` | LogStream 测试 | Task 3：加 LogEvent 着色测试 |
| `packages/core/src/shannon_core/cli/logs.py` | CLI 日志 tail | Task 4：加 `render_event_line` + `tail_events_ndjson` |
| `packages/core/tests/test_cli_logs.py` | cli/logs 测试 | Task 4：加 renderer 测试 |
| `packages/whitebox/src/shannon_whitebox/cli/main.py` | `logs` 命令 | Task 5：加 `--full` 选项 |

---

### Task 1: 白盒 `setup_display` 补 `configure_logging`（修裂痕四）

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`（`setup_display`，`session.initialize` 在 :1549）
- Test: `packages/whitebox/tests/pipeline/test_migration_activities.py`

**Interfaces:**
- Consumes: `shannon_core.logging.configure_logging(log_dir: Path|str) -> None`（已存在，幂等）
- Produces: `setup_display` 后 root logger 挂 `LogBusHandler` + per-scan `diagnostic.log` → 后续散落 `getLogger` 的 LogEvent 进 dispatcher（events.ndjson + diagnostic.log）

- [ ] **Step 1: 写失败测试**

在 `packages/whitebox/tests/pipeline/test_migration_activities.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_setup_display_mounts_logbus_handler_so_logevent_reaches_files(tmp_path):
    """裂痕四修复: setup_display 调 configure_logging 挂 LogBusHandler, 之后散落
    getLogger 的 LogEvent 经 LogBus→dispatcher 进 events.ndjson + diagnostic.log。
    （修前: worker 路径 root 无 LogBusHandler → LogEvent 走 lastResort stderr, 两文件皆空。）"""
    import asyncio, logging
    from shannon_whitebox.pipeline.activities import setup_display
    from shannon_whitebox.pipeline.shared import ActivityInput
    from shannon_core.audit.session_registry import clear_audit_session
    from shannon_core.logging.log_bus import LogBus

    # 快照 + 还原 root logger（configure_logging 是进程级）
    root = logging.getLogger()
    saved_handlers = list(root.handlers)

    event_file = str(tmp_path / "events.ndjson")
    inp = ActivityInput(
        repo_path=str(tmp_path),
        workspace_path=str(tmp_path),
        workspace_name=tmp_path.name,
        event_file=event_file,
    )
    try:
        await setup_display(inp)
        logging.getLogger("shannon_test_diag").warning("diag-from-activity")
        await asyncio.sleep(0.3)  # 让 LogBus drain task 把 LogEvent dispatch 落盘
    finally:
        await LogBus.drain_and_detach()
        clear_audit_session()
        root.handlers = saved_handlers  # 还原，避免泄漏到其它测试

    ndjson = (tmp_path / "events.ndjson").read_text("utf-8")
    assert '"type": "LogEvent"' in ndjson, ndjson
    assert "diag-from-activity" in ndjson
    diag = (tmp_path / "logs" / "diagnostic.log").read_text("utf-8")
    assert "diag-from-activity" in diag, diag
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/shannon-py && uv run pytest packages/whitebox/tests/pipeline/test_migration_activities.py::test_setup_display_mounts_logbus_handler_so_logevent_reaches_files -xvs`
Expected: FAIL（events.ndjson 无 LogEvent —— setup_display 还没调 configure_logging）。

- [ ] **Step 3: 实现**

在 `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` 的 `setup_display` 函数内，现有 import 块（:1530-1533）加一行，并在 `session = AuditSession(...)` 之前插入 configure_logging。最终 `setup_display` 体（:1530 起）改为：

```python
    from rich.console import Console
    from shannon_core.models.metrics import SessionMetadata
    from shannon_core.logging import configure_logging
    from shannon_whitebox.audit.session import AuditSession
    from shannon_whitebox.audit.session_registry import set_audit_session

    if input.workspace_path:
        ws_path = Path(input.workspace_path)
    else:
        ws_path = (
            Path(input.repo_path).parent / "workspaces"
            / (input.workspace_name or "scan"))
    meta = SessionMetadata(
        id=input.workspace_name or ws_path.name,
        web_url=input.web_url,
        repo_path=input.repo_path,
        output_path=str(ws_path.parent),
    )
    # 裂痕四修复: worker 容器入口挂 LogBusHandler + per-scan diagnostic.log。
    # CLI 路径 main.py 已配；worker 路径此前漏配 → LogEvent 走 lastResort stderr,
    # live 页/events.ndjson 无诊断行、不产 diagnostic.log。幂等(setup.py:57-65)。
    configure_logging(log_dir=ws_path / "logs")
    console = Console()  # auto-detects non-TTY in pipes -> plain text per event
    session = AuditSession(meta, use_rich=False, console=console)
    await session.initialize(workflow_id=meta.id, event_file=input.event_file)
    await LogBus.attach(session.dispatcher)
    set_audit_session(session)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /root/shannon-py && uv run pytest packages/whitebox/tests/pipeline/test_migration_activities.py -xvs`
Expected: PASS（含原有 setup_display/heartbeat/finalize 测试 + 新测试）。

- [ ] **Step 5: 回归 clean separation**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/display/test_logevent_render.py -xvs`
Expected: PASS（确认 LogEvent 仍不进 workflow.log）。

- [ ] **Step 6: Commit**

```bash
cd /root/shannon-py
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/tests/pipeline/test_migration_activities.py
git commit -m "fix(whitebox): setup_display 补 configure_logging 修裂痕四 — LogEvent 进 events.ndjson/diagnostic.log"
```

---

### Task 2: bb_worker 并发=1

**Files:**
- Modify: `packages/worker/src/shannon_worker/runner.py`（bb_worker 构造 :74-87）
- Test: `packages/worker/tests/test_runner.py`

**Interfaces:**
- Consumes: 无
- Produces: bb_worker `max_concurrent_workflow_tasks=1`（LogBus/AuditSession 单例并发安全前提）

- [ ] **Step 1: 写失败测试**

在 `packages/worker/tests/test_runner.py` 的 `test_run_worker_connects_and_registers_two_workers` 内，`bb_worker.run.assert_awaited_once()` 之前（:51 附近）加一行断言：

```python
    # bb_worker 并发=1：LogBus 单例 + AuditSession 全局单例多 scan 并发会串台/冲突，
    # 对齐 wb_worker(max_concurrent_workflow_tasks=1)。
    assert bb_call.kwargs["max_concurrent_workflow_tasks"] == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/shannon-py && uv run pytest packages/worker/tests/test_runner.py::test_run_worker_connects_and_registers_two_workers -xvs`
Expected: FAIL（`KeyError: 'max_concurrent_workflow_tasks'` —— bb_worker 还没设）。

- [ ] **Step 3: 实现**

在 `packages/worker/src/shannon_worker/runner.py` 的 `bb_worker = Worker(...)`（:74）加 `max_concurrent_workflow_tasks=1`：

```python
    bb_worker = Worker(
        client=client,
        task_queue=WEB_TASK_QUEUE_BLACKBOX,
        workflows=[BlackboxScanWorkflow],
        activities=[
            run_blackbox_preflight, run_blackbox_auth_validation, run_recon,
            run_exploit_agent, validate_exploitation_queue, bb_assemble_report,
            run_report_agent, finalize_report, bb_generate_poc_report,
            bb_log_phase_start, bb_log_phase_complete, bb_log_info,
            load_correlation_context, resolve_blackbox_engine, detect_whitebox_results,
            write_engine_config_for_session, cleanup_engine_configs,
        ],
        # 对齐 wb_worker: AuditSession 全局单例 + LogBus 单例多 scan 并发会冲突/串台
        # (runner.py:68-71 注释同因)。黑盒 web 扫描 C1 化(Plan B)前先就位。
        max_concurrent_workflow_tasks=1,
        graceful_shutdown_timeout=_GRACEFUL_SHUTDOWN,
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /root/shannon-py && uv run pytest packages/worker/tests/test_runner.py -xvs`
Expected: PASS（2 个测试）。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/worker/src/shannon_worker/runner.py packages/worker/tests/test_runner.py
git commit -m "fix(worker): bb_worker 并发=1 对齐 wb_worker — AuditSession/LogBus 单例并发安全"
```

---

### Task 3: 前端 LogEvent 类型 + LogStream 渲染（按 level 着色）

**Files:**
- Modify: `packages/web/frontend/src/api/types.ts`（加 `LogEventEvent`，扩 `NdjsonEvent`）
- Modify: `packages/web/frontend/src/components/LogStream.tsx`（`summarize` + `rowClass`）
- Test: `packages/web/frontend/src/components/LogStream.test.tsx`

**Interfaces:**
- Consumes: 后端 `events.ndjson` 的 LogEvent 行（`{type:"LogEvent", category:<levelname>, level, logger_name, message, exc_txt}`，对齐 `core display/events.py:103`）
- Produces: LogStream 渲染 LogEvent 行，按 level 着色

- [ ] **Step 1: 写失败测试**

在 `packages/web/frontend/src/components/LogStream.test.tsx` 末尾追加：

```tsx
  it("LogEvent 渲染 [LEVEL] logger: msg 且按 level 着色", () => {
    const evs: NdjsonEvent[] = [
      { ts: "2026-07-16T02:00:00.000Z", category: "WARNING", type: "LogEvent", logger_name: "mod.a", level: "WARNING", message: "careful" } as NdjsonEvent,
      { ts: "2026-07-16T02:00:01.000Z", category: "ERROR", type: "LogEvent", logger_name: "mod.b", level: "ERROR", message: "boom" } as NdjsonEvent,
      { ts: "2026-07-16T02:00:02.000Z", category: "INFO", type: "LogEvent", logger_name: "mod.c", level: "INFO", message: "hi" } as NdjsonEvent,
    ];
    const { container } = render(<LogStream events={evs} />);
    // WARNING → ev-warn, ERROR → ev-error, INFO → 灰显(text-muted-foreground)
    expect(rowText(container, "ev-warn")).toMatch(/\[WARNING\]/);
    expect(rowText(container, "ev-warn")).toMatch(/mod\.a: careful/);
    expect(rowText(container, "ev-error")).toMatch(/\[ERROR\]/);
    expect(rowText(container, "ev-error")).toMatch(/mod\.b: boom/);
    const infoRow = container.querySelector(".text-muted-foreground");
    expect(infoRow?.textContent ?? "").toMatch(/\[INFO\]/);
    expect(infoRow?.textContent ?? "").toMatch(/mod\.c: hi/);
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/shannon-py/packages/web/frontend && pnpm vitest run src/components/LogStream.test.tsx`
Expected: FAIL（LogEvent 走 `summarize` default 分支显示裸 type，无 ev-warn/ev-error class）。

- [ ] **Step 3a: types.ts 加 LogEventEvent**

在 `packages/web/frontend/src/api/types.ts` 的 `GitnexusLlmEvent` 之后（:64 前）加：

```ts
export interface LogEventEvent {
  ts: string;
  // category=levelname 动态值(INFO/WARNING/ERROR/DEBUG), 非 EventCategory 枚举(只有 WARN 无 WARNING)
  category: string;
  type: "LogEvent";
  logger_name: string;
  level: string;       // "INFO" | "WARNING" | "ERROR" | "DEBUG" | "NOTSET"
  message: string;
  exc_txt?: string;
}
```

并把 `NdjsonEvent` 联合（:74-77）加上 `LogEventEvent`：

```ts
export type NdjsonEvent =
  | WorkflowHeaderEvent | PhaseEvent | StepEvent | AgentEvent | ToolCallEvent
  | LlmTurnEvent | InfoEvent | ErrorEvent | SummaryEvent | ResumeEvent
  | GitnexusLlmEvent | ScanEndEvent | CorrelationProgressEvent | LogEventEvent;
```

- [ ] **Step 3b: LogStream.tsx 加 summarize + rowClass 分支**

在 `packages/web/frontend/src/components/LogStream.tsx` 的 `summarize` switch 内，`case "ResumeEvent"` 之前加：

```tsx
    case "LogEvent": {
      const line = `[${e.level}] ${e.logger_name}: ${e.message}`;
      return e.exc_txt ? `${line}\n${e.exc_txt}` : line;
    }
```

并把 `rowClass`（:143）改为对 LogEvent 按 level 着色（绕过 CAT_CLASS，因 LogEvent.category=levelname 不在 EventCategory 枚举）：

```tsx
function rowClass(e: NdjsonEvent): string {
  if (e.type === "LogEvent") {
    if (e.level === "ERROR") return "ev-error";
    if (e.level === "WARNING") return "ev-warn";
    return "text-muted-foreground";  // INFO/DEBUG/NOTSET 灰显
  }
  const base = CAT_CLASS[e.category] ?? "text-muted-foreground";
  if (e.type === "AgentEvent" && e.event === "end") {
    if (e.success === false) return `${base} ev-agent-fail`;
    return `${base} ev-agent-ok`;
  }
  return base;
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /root/shannon-py/packages/web/frontend && pnpm vitest run src/components/LogStream.test.tsx`
Expected: PASS（含原有测试 + 新 LogEvent 测试）。

- [ ] **Step 5: tsc 类型检查**

Run: `cd /root/shannon-py/packages/web/frontend && pnpm tsc --noEmit`
Expected: 0 error。

- [ ] **Step 6: Commit**

```bash
cd /root/shannon-py
git add packages/web/frontend/src/api/types.ts packages/web/frontend/src/components/LogStream.tsx packages/web/frontend/src/components/LogStream.test.tsx
git commit -m "feat(web): LogStream 渲染 LogEvent — 按 level 着色(ERROR/WARNING 高亮, INFO 灰显)"
```

---

### Task 4: core events.ndjson 同步 renderer（`logs --full` 核心）

**Files:**
- Modify: `packages/core/src/shannon_core/cli/logs.py`（加 `render_event_line` + `JsonLogHandler` + `tail_events_ndjson`）
- Test: `packages/core/tests/test_cli_logs.py`

**Interfaces:**
- Consumes: `shannon_core.display.formatters`（`step_body`/`phase_body`/`agent_body`/`gitnexus_body`/`tag`/`format_duration`，纯函数，接 dataclass-like 对象）
- Produces: `render_event_line(data: dict) -> str`（同步，渲染一条 ndjson 行为文本）；`tail_events_ndjson(workspace_id, workspaces_dir)`（tail events.ndjson，遇 `scan_end` 退出）

- [ ] **Step 1: 写失败测试**

在 `packages/core/tests/test_cli_logs.py` 末尾追加：

```python
from types import SimpleNamespace


def test_render_event_line_logevent_uses_diagnostic_format():
    from shannon_core.cli.logs import render_event_line
    line = render_event_line({
        "ts": "2026-07-16 02:00:00", "category": "WARNING", "type": "LogEvent",
        "logger_name": "mod.x", "level": "WARNING", "message": "careful",
    })
    assert "[WARNING]" in line
    assert "mod.x: careful" in line


def test_render_event_line_step_reuses_formatters():
    from shannon_core.cli.logs import render_event_line
    line = render_event_line({
        "ts": "2026-07-16 02:00:00", "category": "STEP", "type": "StepEvent",
        "name": "code-index", "phase": "pre-recon", "event": "complete",
        "duration_ms": 430238, "intent": "构建调用图",
    })
    assert "[STEP" in line
    assert "构建调用图" in line
    assert "430238ms" in line or "7m " in line  # format_duration


def test_render_event_line_scan_end():
    from shannon_core.cli.logs import render_event_line
    line = render_event_line({
        "ts": "2026-07-16 02:00:00", "category": "CONTROL",
        "type": "scan_end", "status": "completed",
    })
    assert "scan_end" in line
    assert "completed" in line


def test_tail_events_ndjson_renders_and_exits_on_scan_end(tmp_path, capsys):
    """一次性 flush 全量 + 遇 scan_end 立即返回（不等 watchdog）。"""
    import shannon_core.cli.logs as L
    ws = tmp_path / "ws1"
    ws.mkdir()
    ndjson = ws / "events.ndjson"
    ndjson.write_text(
        '{"ts":"2026-07-16 02:00:00","category":"STEP","type":"StepEvent","name":"x","phase":"p","event":"complete","duration_ms":12,"intent":"i"}\n'
        '{"ts":"2026-07-16 02:00:01","category":"CONTROL","type":"scan_end","status":"completed"}\n',
        encoding="utf-8",
    )
    # 直接驱动 JsonLogHandler.flush 验证渲染 + scan_end 退出（不启 watchdog，避免阻塞）
    handler = L.JsonLogHandler(ndjson)
    done = handler.flush()
    out = capsys.readouterr().out
    assert "[STEP" in out
    assert "scan_end" in out
    assert done is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/test_cli_logs.py -xvs -k "render_event_line or tail_events_ndjson"`
Expected: FAIL（`ImportError: cannot import name 'render_event_line'`）。

- [ ] **Step 3: 实现**

在 `packages/core/src/shannon_core/cli/logs.py` 顶部 import 块后（`COMPLETION_PATTERN` 之后）加 renderer + handler + tail：

```python
import json
from types import SimpleNamespace

from shannon_core.display.formatters import (
    agent_body, format_duration, gitnexus_body, humanize_tool_call,
    phase_body, step_body, tag,
)


def render_event_line(data: dict) -> str:
    """渲染一条 events.ndjson JSON 行为文本（logs --full 用）。

    DisplayEvent 复用 display/formatters 纯函数（传 SimpleNamespace）；LogEvent 用
    diagnostic 格式 [LEVEL] logger: msg（对齐 logging/diagnostic_log）。同步、无 ANSI。
    """
    ts = data.get("ts", "")
    etype = data.get("type", "")
    e = SimpleNamespace(**data)
    if etype == "LogEvent":
        level = data.get("level", "INFO")
        return f"[{ts}] [{level:>5}] {data.get('logger_name', '')}: {data.get('message', '')}"
    if etype == "PhaseEvent":
        prefix = "\n" if data.get("event") == "start" else ""
        return f"{prefix}[{ts}] [{tag('PHASE')}] {phase_body(e)}"
    if etype == "StepEvent":
        return f"[{ts}] [{tag('STEP')}] {step_body(e)}"
    if etype == "AgentEvent":
        return f"[{ts}] [{tag('AGENT')}] {agent_body(e)}"
    if etype == "ToolCallEvent":
        params = humanize_tool_call(data.get("tool_name", ""), data.get("parameters") or {})
        return f"[{ts}] [TOOL]  {data.get('agent_name', '')}: {data.get('tool_name', '')}: {params}"
    if etype == "LlmTurnEvent":
        return f"[{ts}] [LLM]   {data.get('agent_name', '')}: Turn {data.get('turn', '')}: {data.get('content', '')[:200]}"
    if etype == "GitnexusLlmEvent":
        return f"[{ts}] [LLM]   [GitNexus] {gitnexus_body(e)}"
    if etype == "InfoEvent":
        label = "WARNING" if data.get("level") == "warning" else "INFO"
        return f"[{ts}] [{tag(label)}] {data.get('message', '')}"
    if etype == "ErrorEvent":
        return f"[{ts}] [ERROR] {data.get('error_type', '')}: {data.get('message', '')}"
    if etype == "SummaryEvent":
        return f"[{ts}] [SUMMARY] {data.get('status', '?')}  {format_duration(data.get('total_duration_ms') or 0)}"
    if etype == "WorkflowHeader":
        return f"[{ts}] [HEADER] {data.get('repo_path', '')}  ({data.get('mode', '')})"
    if etype == "scan_end":
        return f"[{ts}] --- scan_end: {data.get('status', '?')} ---"
    return f"[{ts}] [{etype}]"


SCAN_END_TYPE = "scan_end"


class JsonLogHandler(FileSystemEventHandler):
    """Watches events.ndjson: renders each new JSON line; exits on scan_end."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._position = 0

    def _read_new(self) -> tuple[list[str], bool]:
        """Return (rendered_lines, saw_scan_end) for new bytes since last read."""
        try:
            size = self._path.stat().st_size
            if size <= self._position:
                return [], False
            content = self._path.read_text(encoding="utf-8")
            new = content[self._position:]
            self._position = size
        except Exception:
            return [], True  # 文件不可读 → 视为完成
        rendered, saw_end = [], False
        for line in new.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            rendered.append(render_event_line(data))
            if data.get("type") == SCAN_END_TYPE:
                saw_end = True
        return rendered, saw_end

    def flush(self) -> bool:
        rendered, saw_end = self._read_new()
        for r in rendered:
            sys.stdout.write(r + "\n")
        sys.stdout.flush()
        return saw_end

    def on_modified(self, event) -> None:
        if event.src_path == str(self._path):
            if self.flush():
                raise SystemExit(0)


def tail_events_ndjson(
    workspace_id: str,
    workspaces_dir: str = "workspaces",
) -> None:
    """Tail events.ndjson, render each JSON line; auto-exit on scan_end (type=scan_end)."""
    base = Path(workspaces_dir)
    path = base / workspace_id / "events.ndjson"
    if not path.exists():
        stripped = re.sub(r"_resume_\d+$", "", workspace_id)
        if stripped != workspace_id:
            alt = base / stripped / "events.ndjson"
            if alt.exists():
                path = alt
    if not path.exists():
        print(f"ERROR: events.ndjson not found for: {workspace_id}", file=sys.stderr)
        sys.exit(1)

    handler = JsonLogHandler(path)
    print(f"Tailing events.ndjson (full): {path}")
    if handler.flush():  # 既有内容里已含 scan_end → 直接退出
        sys.exit(0)
    observer = Observer()
    observer.schedule(handler, str(path.parent), recursive=False)
    observer.start()
    try:
        observer.join()
    except KeyboardInterrupt:
        observer.stop()
        observer.join()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/test_cli_logs.py -xvs`
Expected: PASS（含原有 + 4 个新测试）。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/cli/logs.py packages/core/tests/test_cli_logs.py
git commit -m "feat(core): render_event_line + tail_events_ndjson — events.ndjson 全量同步渲染(logs --full)"
```

---

### Task 5: CLI `logs --full` 接线

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/cli/main.py`（`logs` 命令 :196-220）
- Test: `packages/whitebox/tests/test_cli.py`（现有 CliRunner 用例所在，同包不跨依赖）

**Interfaces:**
- Consumes: Task 4 的 `render_event_line` + `tail_events_ndjson`
- Produces: `shannon-whitebox logs <ws> --full [--follow]`

- [ ] **Step 1: 写失败测试**

在 `packages/whitebox/tests/test_cli.py` 末尾追加（用 click 的 CliRunner，对齐该文件现有用例）：

```python
def test_logs_full_flag_renders_events_ndjson(tmp_path, monkeypatch):
    """logs --full 读 ws/events.ndjson 全量渲染（非 follow）。"""
    from click.testing import CliRunner
    from shannon_whitebox.cli.main import cli
    ws = tmp_path / "ws1"
    ws.mkdir()
    (ws / "events.ndjson").write_text(
        '{"ts":"2026-07-16 02:00:00","category":"WARNING","type":"LogEvent","logger_name":"m","level":"WARNING","message":"hi"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "shannon_whitebox.cli.main.resolve_workspaces_dir", lambda: tmp_path)
    res = CliRunner().invoke(cli, ["logs", "ws1", "--full"])
    assert res.exit_code == 0, res.output
    assert "[WARNING]" in res.output
    assert "m: hi" in res.output
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/shannon-py && uv run pytest packages/whitebox/tests/test_cli.py::test_logs_full_flag_renders_events_ndjson -xvs`
Expected: FAIL（`--full` 选项不存在 → click `NoSuchOption`）。

- [ ] **Step 3: 实现**

改 `packages/whitebox/src/shannon_whitebox/cli/main.py` 的 `logs` 命令（:196-220）为：

```python
@cli.command()
@click.argument("workspace_name")
@click.option("--follow", is_flag=True, help="Tail the log in real-time (auto-exits on completion)")
@click.option(
    "--diagnostic", is_flag=True,
    help="Read diagnostic.log (logging WARNING/ERROR) instead of workflow.log",
)
@click.option(
    "--full", is_flag=True,
    help="Read events.ndjson (full stream incl. LogEvent diagnostic lines)",
)
def logs(workspace_name, follow, diagnostic, full):
    """View workspace execution logs."""
    import json as _json
    workspaces_dir = resolve_workspaces_dir()
    ws = workspaces_dir / workspace_name
    if not ws.exists():
        click.echo(f"Workspace not found: {workspace_name}")
        raise SystemExit(1)
    if full:
        events_file = ws / "events.ndjson"
        if not events_file.exists():
            click.echo("No events.ndjson found")
            return
        if follow:
            from shannon_core.cli.logs import tail_events_ndjson
            tail_events_ndjson(workspace_name)
        else:
            from shannon_core.cli.logs import render_event_line
            for line in events_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    click.echo(render_event_line(_json.loads(line)))
                except _json.JSONDecodeError:
                    continue
        return
    # spec 组件 6：--diagnostic 读 logs/diagnostic.log，否则 workflow.log（display 流产物）。
    log_filename = "diagnostic.log" if diagnostic else "workflow.log"
    log_file = ws / ("logs" if diagnostic else "") / log_filename
    if not log_file.exists():
        click.echo("No logs found")
        return
    if follow:
        from shannon_core.cli.logs import tail_workflow_log
        tail_workflow_log(workspace_name, log_filename=log_filename)
    else:
        click.echo(log_file.read_text())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /root/shannon-py && uv run pytest packages/whitebox/tests/test_cli.py::test_logs_full_flag_renders_events_ndjson packages/core/tests/test_cli_logs.py -xvs`
Expected: PASS（Task 4 core renderer 测试 + Task 5 whitebox 接线测试都绿）。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/whitebox/src/shannon_whitebox/cli/main.py packages/whitebox/tests/test_cli.py
git commit -m "feat(whitebox-cli): logs --full — events.ndjson 全量文本视图(含 LogEvent)"
```

---

## Self-Review（写完后自查）

**1. Spec 覆盖**（Plan A = spec 改动 1/2/3/5）：
- 改动 1（worker configure_logging）→ Task 1 ✅
- 改动 2（前端 LogEvent）→ Task 3 ✅
- 改动 3（logs --full）→ Task 4 + 5 ✅
- 改动 5（bb_worker 并发=1）→ Task 2 ✅
- 黑盒 C1 化（改动 4）→ **不在 Plan A，属 Plan B**（spec §8 已说明）

**2. 占位符扫描**：无 TBD/TODO；每个 step 都有完整代码 + 命令 + 期望输出。✅

**3. 类型一致性**：
- `render_event_line`（Task 4 定义）↔ Task 5 调用：签名 `(data: dict) -> str` 一致 ✅
- `tail_events_ndjson`（Task 4）↔ Task 5：`(workspace_id, workspaces_dir="workspaces")` 一致 ✅
- `LogEventEvent`（Task 3 types.ts）字段 ↔ `LogEvent` dataclass（core events.py:114-117）：`logger_name/level/message/exc_txt` 一致 ✅
- `JsonLogHandler.flush() -> bool`（Task 4）↔ 测试 `done = handler.flush()` 一致 ✅

**4. 风险已标注**：Task 1 测试 root logger 快照/还原；Task 3 level 着色绕过 CAT_CLASS；Task 4 复用 formatters 不污染 FileLogRenderer。
