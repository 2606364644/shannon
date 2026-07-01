# GitNexus 轨 LLM 环节进度日志 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 GitNexus 轨 sink/source/taint 补召回 + chain_verdict 在运行中向 `workflow.log` + CLI 终端实时输出进度（计数 + 命中细节 + 汇总），终结长任务黑盒。

**Architecture:** core 层新增 `ProgressEmitter`（per-item 计数 + best-effort 上报）+ `GitnexusLlmEvent`（专属 `[GN-LLM]` 标签，对偶 LLM 轨 `LlmTurnEvent`）；进度经显式 `progress_cb` 从 activity 注入到 core 各调用点；采样/格式化集中在 activity 层；走现有 dispatcher → `FileLogRenderer`（workflow.log）+ `rich_renderer`（终端）。

**Tech Stack:** Python 3.11+ asyncio · dataclasses(frozen) · pytest（asyncio）· 现有 DisplayEvent 体系。

## Global Constraints

- **双轨铁律（CLAUDE.md §1）**：不改 LLM 轨（PRE_RECON / vuln agent）任何 prompt 或行为；不喂确定性产物给 LLM 轨。
- **不改 `map_llm_with_bounds` 签名**（进度在 per-item 闭包里发，不污染通用并发骨架）。
- **best-effort**：进度通道任何失败绝不影响扫描 —— 三层静默防护（emitter 吞 cb 异常 / session `if dispatcher is None: return` / activity 采样不满足直接 return）。
- **采样 K=10 固定，不开 env**（YAGNI）；`done == 1` 或 `done % 10 == 0` 打 progress；命中（`detail` 非空）即时打 hit；`finalize` 打 summary。
- **`progress_cb=None` 全程跳过**（测试 / 未注入 / `SHANNON_GITNEXUS_LLM_ENABLED=0` 时）。
- **标签用 `tag("GN-LLM")`** —— `formatters.tag` 是通用 `label.ljust(5)`，无需注册（spec §6.3"在 symbols/formatters 注册"的说法简化为：直接用，不动 symbols/formatters）。
- **测试只跑改动相关文件**（CLAUDE.md §3 测试陷阱：全套 pytest 有预存挂起/失败，勿广跑）。
- **提交风格**：`type(scope): 描述`，每个 task 一个 commit，中文描述。

## Spec 偏离（探索后修正）

**spec §6.5 / §12 的 authz IDOR 部分撤销**：`run_authz_gitnexus_judge`（`activities.py:269-362`）经探索确认是**单次** `run_claude_prompt`（所有 IDOR 候选一次性塞 prompt，line 327-342），**非逐候选循环**，且已有开始（`log_info` 候选数）/结束（`log_info` verdict 数）可观测性 —— 没有"逐单位黑盒"可报，不接 `ProgressEmitter`。本 plan 范围因此收为：sink/source/taint 补召回 + chain_verdict（4 类，都确属逐单位并发/串行 LLM）。

---

## File Structure

| 文件 | 职责 | task |
|------|------|------|
| 🆕 `core/src/shannon_core/code_index/progress.py` | `ProgressSample`（frozen dataclass）+ `ProgressEmitter`（计数器 + best-effort tick/finalize） | T1 |
| `core/src/shannon_core/display/events.py` | + `GitnexusLlmEvent`（对偶 `LlmTurnEvent`） | T2 |
| `core/src/shannon_core/display/file_renderer.py` | + `case GitnexusLlmEvent` + `_gitnexus()` 三态渲染 | T2 |
| `core/src/shannon_core/display/rich_renderer.py` | + `case GitnexusLlmEvent` + `_render_gitnexus()` + STYLE_MAP `"GN-LLM"` | T2 |
| `core/src/shannon_core/audit/workflow_logger.py` | + `log_gitnexus_progress()`（dispatch `GitnexusLlmEvent`） | T3 |
| `core/src/shannon_core/audit/session.py` | + `log_gitnexus_progress()`（转发 workflow_logger） | T3 |
| `core/src/shannon_core/audit/session_registry.py` | + `NullAuditSession.log_gitnexus_progress`（no-op） | T3 |
| `core/src/shannon_core/code_index/sink_discovery_llm.py` | `discover_sinks_llm` + `progress_cb` + emitter | T4 |
| `core/src/shannon_core/code_index/source_discovery_llm.py` | `discover_sources_llm` + `progress_cb` + emitter | T4 |
| `core/src/shannon_core/code_index/__init__.py` | `_taint_one` + emitter.tick；`build_code_index_with_gitnexus` + `progress_cb` 透传 | T5 |
| `core/src/shannon_core/code_index/vuln_chain_builders/{injection,xss,ssrf}_builder.py` | `build_*_findings` + `progress_cb` + emitter | T6 |
| `whitebox/src/shannon_whitebox/pipeline/activities.py` | `run_code_index` / `run_gitnexus_chain_verdict` 构造 `progress_cb`（采样 + 包装 session） | T7 |
| 🆕 `core/tests/code_index/test_progress.py` | emitter 单测 | T1 |
| `core/tests/display/test_file_renderer.py` | + `_gitnexus` 三态快照 | T2 |
| `core/tests/display/test_rich_renderer.py` | + `_render_gitnexus`（轻测） | T2 |
| `core/tests/code_index/test_sink_discovery_llm.py` / `test_source_discovery_llm.py` | + progress_cb 用例 | T4 |

**依赖顺序**：T1（emitter）┐ T2（event+renderer）┐ T3（audit 通道）三者独立 → T4/T5/T6（core 接线，依赖 T1）→ T7（activity wiring，依赖 T3 + T4/T5/T6）。

---

### Task 1: ProgressSample + ProgressEmitter

**Files:**
- Create: `packages/core/src/shannon_core/code_index/progress.py`
- Test: `packages/core/tests/code_index/test_progress.py`

**Interfaces:**
- Produces: `ProgressSample`（frozen dataclass: `phase:str, done:int, total:int, hits:int, detail:str|None, final:bool=False`）、`ProgressCb = Callable[[ProgressSample], Awaitable[None]] | None`、`ProgressEmitter.__init__(phase, total, cb)` / `async tick(detail=None, hits_delta=0)` / `async finalize(summary_detail)`。后续 T4/T5/T6 用这些。

- [ ] **Step 1: Write failing tests**

Create `packages/core/tests/code_index/test_progress.py`:

```python
import asyncio
import pytest
from shannon_core.code_index.progress import ProgressEmitter, ProgressSample


def test_sample_is_frozen():
    s = ProgressSample("sink-discovery", 1, 10, 0, None)
    assert s.phase == "sink-discovery" and s.final is False
    with pytest.raises(Exception):
        s.done = 2  # frozen


async def _drain(emitter, ticks):
    """Simulate concurrent per-item ticks via gather (like map_llm_with_bounds)."""
    async def one(detail, delta):
        await emitter.tick(detail=detail, hits_delta=delta)
    await asyncio.gather(*[one(d, delta) for d, delta in ticks])


@pytest.mark.asyncio
async def test_tick_counts_done_and_hits():
    seen = []
    emitter = ProgressEmitter("sink-discovery", 3, lambda s: seen.append(s) or asyncio.sleep(0))
    await emitter.tick(detail=None, hits_delta=0)       # miss
    await emitter.tick(detail="hit-A", hits_delta=1)    # hit
    await emitter.tick(detail="hit-B", hits_delta=2)    # hit
    assert seen[-1].done == 3 and seen[-1].hits == 3
    assert seen[1].detail == "hit-A" and seen[2].detail == "hit-B"


@pytest.mark.asyncio
async def test_finalize_emits_final_sample():
    seen = []
    emitter = ProgressEmitter("chain-verdict", 5, lambda s: seen.append(s) or asyncio.sleep(0))
    await emitter.tick(hits_delta=1)
    await emitter.finalize("5 vulnerable · 4.2s/chain avg")
    assert seen[-1].final is True
    assert seen[-1].detail == "5 vulnerable · 4.2s/chain avg"


@pytest.mark.asyncio
async def test_cb_none_is_noop():
    emitter = ProgressEmitter("taint-analysis", 2, None)   # cb=None
    await emitter.tick(detail="x", hits_delta=1)           # must not raise
    await emitter.finalize("done")                         # must not raise


@pytest.mark.asyncio
async def test_cb_exception_is_swallowed():
    async def boom(s):
        raise RuntimeError("display channel down")
    emitter = ProgressEmitter("sink-discovery", 2, boom)
    await emitter.tick(detail="x", hits_delta=1)           # must not raise
    await emitter.finalize("done")


@pytest.mark.asyncio
async def test_concurrent_ticks_do_not_lose_count():
    seen = []
    emitter = ProgressEmitter("sink-discovery", 50, lambda s: seen.append(s) or asyncio.sleep(0))
    await _drain(emitter, [(None, 0)] * 40 + [("hit", 1)] * 10)
    assert emitter._done == 50 and emitter._hits == 10
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/core && python -m pytest tests/code_index/test_progress.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shannon_core.code_index.progress'`

- [ ] **Step 3: Implement progress.py**

Create `packages/core/src/shannon_core/code_index/progress.py`:

```python
"""GitNexus 轨 LLM 环节的进度计数与 best-effort 上报。

core 层只定义协议 + 计数器，不感知 whitebox 的 audit session；采样/格式化由
activity 层注入的 progress_cb 负责。cb=None 时全程 no-op（测试/未注入/
SHANNON_GITNEXUS_LLM_ENABLED=0）。cb raise 时吞掉（best-effort，显示通道
失败绝不影响扫描）。计数在 asyncio 单线程下原子（tick 内自增在 await cb 之前）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

Phase = Literal[
    "sink-discovery", "source-discovery", "taint-analysis", "chain-verdict",
]


@dataclass(frozen=True)
class ProgressSample:
    phase: Phase
    done: int
    total: int
    hits: int
    detail: str | None       # 命中细节（hit 行）；None=未命中
    final: bool = False      # True=结束汇总行（detail 此时承载汇总文案）


ProgressCb = Callable[[ProgressSample], Awaitable[None]] | None


class ProgressEmitter:
    """并发安全的 per-item 进度计数器。

    在 map_llm_with_bounds 的 per-item 函数或 builder 的候选循环里，每完成一个
    单位调一次 tick；环节结束调 finalize。total 可为 0（无候选）——此时 tick 不会被
    调，finalize 也只发 done=0 的汇总。
    """

    def __init__(self, phase: Phase, total: int, cb: ProgressCb):
        self._phase = phase
        self._total = total
        self._cb = cb
        self._done = 0
        self._hits = 0

    async def tick(self, detail: str | None = None, hits_delta: int = 0) -> None:
        self._done += 1
        self._hits += hits_delta
        if self._cb is None:
            return
        try:
            await self._cb(ProgressSample(
                self._phase, self._done, self._total, self._hits, detail))
        except Exception:
            pass  # best-effort

    async def finalize(self, summary_detail: str) -> None:
        if self._cb is None:
            return
        try:
            await self._cb(ProgressSample(
                self._phase, self._done, self._total, self._hits,
                summary_detail, final=True))
        except Exception:
            pass  # best-effort
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/core && python -m pytest tests/code_index/test_progress.py -v`
Expected: PASS（6 tests）

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/progress.py packages/core/tests/code_index/test_progress.py
git commit -m "feat(code_index): ProgressSample + ProgressEmitter 进度计数器"
```

---

### Task 2: GitnexusLlmEvent + 两个 renderer 分支

**Files:**
- Modify: `packages/core/src/shannon_core/display/events.py`（末尾追加）
- Modify: `packages/core/src/shannon_core/display/file_renderer.py`（import + match + `_gitnexus`）
- Modify: `packages/core/src/shannon_core/display/rich_renderer.py`（import + match + STYLE_MAP + `_render_gitnexus`）
- Test: `packages/core/tests/display/test_file_renderer.py`、`packages/core/tests/display/test_rich_renderer.py`

**Interfaces:**
- Produces: `GitnexusLlmEvent(timestamp, category, phase, kind: "progress"|"hit"|"summary", done, total, hits, detail=None)`；`FileLogRenderer._gitnexus(e)`、`RichConsoleRenderer._render_gitnexus(e)`。T3 的 `workflow_logger.log_gitnexus_progress` 构造此 event。

- [ ] **Step 1: Write failing tests**

Append to `packages/core/tests/display/test_file_renderer.py`（若文件不存在则参考同目录其它 test 的 import 模式创建）:

```python
import asyncio
from shannon_core.display.events import GitnexusLlmEvent
from shannon_core.display.file_renderer import FileLogRenderer


def _render(e) -> str:
    out: list[str] = []
    r = FileLogRenderer(writer=_AsyncAppend(out))
    asyncio.get_event_loop().run_until_complete(r.render(e))
    return out[0]


class _AsyncAppend:
    def __init__(self, buf): self._buf = buf
    async def write(self, s): self._buf.append(s)


def _evt(kind, **kw):
    base = dict(timestamp="2026-07-01 14:32:05", category="GN-LLM",
                phase="sink-discovery", kind=kind, done=10, total=87, hits=3)
    base.update(kw)
    return GitnexusLlmEvent(**base)


def test_gitnexus_progress_line():
    assert _render(_evt("progress")) == (
        "[2026-07-01 14:32:05] [GN-LLM] sink-discovery  10/87  · 3 sinks so far\n")


def test_gitnexus_hit_line():
    e = _evt("hit", done=5, hits=1, detail="'pg.executeQuery' @ src/api/users.py:42 slot=args")
    assert _render(e) == (
        "[2026-07-01 14:32:05] [GN-LLM] sink-discovery  ✓ 'pg.executeQuery' "
        "@ src/api/users.py:42 slot=args\n")


def test_gitnexus_summary_line():
    e = _evt("summary", done=87, hits=12, detail="12 soft sinks · 5 rule gaps · 2 timeouts")
    assert _render(e) == (
        "[2026-07-01 14:32:05] [GN-LLM] sink-discovery  done 87/87 → "
        "12 soft sinks · 5 rule gaps · 2 timeouts\n")


def test_gitnexus_progress_noun_varies_by_phase():
    e = _evt("progress", phase="chain-verdict", hits=2, done=10, total=34)
    assert "· 2 vulnerable so far" in _render(e)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/core && python -m pytest tests/display/test_file_renderer.py -k gitnexus -v`
Expected: FAIL — `ImportError: cannot import name 'GitnexusLlmEvent'`

- [ ] **Step 3: Add GitnexusLlmEvent to events.py**

Append to `packages/core/src/shannon_core/display/events.py`:

```python
@dataclass(frozen=True)
class GitnexusLlmEvent(DisplayEvent):
    """GitNexus 轨 LLM 环节的进度行 —— 与 LLM 轨 LlmTurnEvent 对偶：
    LLM 轨是单个 agent 的 turn 流，GitNexus 轨是批量函数/候选的并发判定。
    专属标签 GN-LLM 便于 grep 所有 LLM 活动。"""
    phase: str
    kind: Literal["progress", "hit", "summary"]
    done: int
    total: int
    hits: int
    detail: str | None = None
```

- [ ] **Step 4: Add file_renderer branch**

In `packages/core/src/shannon_core/display/file_renderer.py`:

(a) In `render()`'s import list (line ~36) add `GitnexusLlmEvent`, and in the `match` block add a case (before `InfoEvent` for clarity):

```python
        match event:
            case WorkflowHeader(): await self._writer.write(self._header(event))
            case PhaseEvent(): await self._writer.write(self._phase(event))
            case StepEvent(): await self._writer.write(self._step(event))
            case AgentEvent(): await self._writer.write(self._agent(event))
            case ToolCallEvent(): await self._writer.write(self._tool(event))
            case LlmTurnEvent(): await self._writer.write(self._llm(event))
            case GitnexusLlmEvent(): await self._writer.write(self._gitnexus(event))
            case ErrorEvent(): await self._writer.write(self._error(event))
            case SummaryEvent(): await self._writer.write(self._summary(event))
            case InfoEvent(): await self._writer.write(self._info(event))
            case ResumeEvent(): await self._writer.write(self._resume(event))
```

(b) Add the renderer method (after `_llm`):

```python
    _HITS_NOUN = {
        "sink-discovery": "sinks", "source-discovery": "sources",
        "taint-analysis": "taint_flows", "chain-verdict": "vulnerable",
    }

    def _gitnexus(self, e) -> str:
        tag_label = "[GN-LLM]"
        if e.kind == "hit":
            return f"[{e.timestamp}] {tag_label} {e.phase}  ✓ {e.detail}\n"
        if e.kind == "summary":
            return (f"[{e.timestamp}] {tag_label} {e.phase}  "
                    f"done {e.done}/{e.total} → {e.detail}\n")
        noun = self._HITS_NOUN.get(e.phase, "hits")
        return (f"[{e.timestamp}] {tag_label} {e.phase}  {e.done}/{e.total}  "
                f"· {e.hits} {noun} so far\n")
```

- [ ] **Step 5: Add rich_renderer branch**

In `packages/core/src/shannon_core/display/rich_renderer.py`:

(a) Add to `STYLE_MAP` (after `"LLM": "magenta",`):

```python
        "GN-LLM": "magenta",
```

(b) In `render()`'s import list add `GitnexusLlmEvent`, and add a case:

```python
            case LlmTurnEvent(): self._render_llm(event)
            case GitnexusLlmEvent(): self._render_gitnexus(event)
```

(c) Add the method (after `_render_llm`):

```python
    def _render_gitnexus(self, e) -> None:
        if e.kind == "hit":
            self._console.print(
                f"[{e.timestamp}] [magenta]{tag('GN-LLM')}[/]  {e.phase}  ✓ {e.detail}",
                highlight=False)
        elif e.kind == "summary":
            self._console.print(
                f"[{e.timestamp}] [magenta]{tag('GN-LLM')}[/]  {e.phase}  "
                f"done {e.done}/{e.total} → {e.detail}", highlight=False)
        else:
            self._console.print(
                f"[{e.timestamp}] [magenta]{tag('GN-LLM')}[/]  {e.phase}  "
                f"{e.done}/{e.total}  · {e.hits} so far", highlight=False)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd packages/core && python -m pytest tests/display/test_file_renderer.py -k gitnexus tests/display/test_rich_renderer.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add packages/core/src/shannon_core/display/events.py packages/core/src/shannon_core/display/file_renderer.py packages/core/src/shannon_core/display/rich_renderer.py packages/core/tests/display/test_file_renderer.py
git commit -m "feat(display): GitnexusLlmEvent + GN-LLM 标签渲染(file/rich)"
```

---

### Task 3: workflow_logger + session + NullAuditSession 转发

**Files:**
- Modify: `packages/core/src/shannon_core/audit/workflow_logger.py`（+ `log_gitnexus_progress`）
- Modify: `packages/core/src/shannon_core/audit/session.py`（+ `log_gitnexus_progress` 转发）
- Modify: `packages/core/src/shannon_core/audit/session_registry.py`（+ `NullAuditSession.log_gitnexus_progress` no-op）
- Test: 新增或追加 `packages/core/tests/audit/test_workflow_logger_gitnexus.py`

**Interfaces:**
- Produces: `WorkflowLogger.log_gitnexus_progress(phase, kind, done, total, hits, detail=None)`、`AuditSession.log_gitnexus_progress(...)`（同参转发）、`NullAuditSession.log_gitnexus_progress`（no-op）。T7 的 activity progress_cb 调 `session.log_gitnexus_progress(...)`。

- [ ] **Step 1: Write failing test**

Create `packages/core/tests/audit/test_workflow_logger_gitnexus.py`:

```python
import asyncio
from shannon_core.display.events import GitnexusLlmEvent
from shannon_core.audit.session_registry import NullAuditSession


def test_null_session_log_gitnexus_progress_is_noop():
    # NullAuditSession must expose the method (no AttributeError) and be awaitable.
    asyncio.get_event_loop().run_until_complete(
        NullAuditSession().log_gitnexus_progress(
            "sink-discovery", "hit", 5, 87, 1, "'x' @ f.py:1 slot=a"))


def test_workflow_logger_dispatches_gitnexus_event(monkeypatch):
    from shannon_core.audit.workflow_logger import WorkflowLogger
    dispatched = []
    wl = WorkflowLogger.__new__(WorkflowLogger)   # bypass __init__ (needs meta)
    wl._dispatcher = type("D", (), {"dispatch": staticmethod(
        lambda ev: dispatched.append(ev) or asyncio.sleep(0))})()
    asyncio.get_event_loop().run_until_complete(
        wl.log_gitnexus_progress("chain-verdict", "summary", 34, 34, 5, "5 vulnerable"))
    assert isinstance(dispatched[0], GitnexusLlmEvent)
    assert dispatched[0].kind == "summary" and dispatched[0].phase == "chain-verdict"


def test_workflow_logger_no_dispatcher_is_safe():
    from shannon_core.audit.workflow_logger import WorkflowLogger
    wl = WorkflowLogger.__new__(WorkflowLogger)
    wl._dispatcher = None
    asyncio.get_event_loop().run_until_complete(
        wl.log_gitnexus_progress("sink-discovery", "progress", 10, 87, 3))  # no raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/core && python -m pytest tests/audit/test_workflow_logger_gitnexus.py -v`
Expected: FAIL — `AttributeError: log_gitnexus_progress`

- [ ] **Step 3: Add workflow_logger.log_gitnexus_progress**

In `packages/core/src/shannon_core/audit/workflow_logger.py`, add method (next to `log_llm_response`, ~line 172). Add `GitnexusLlmEvent` to the local import inside the method (matching the file's lazy-import style) or top of file:

```python
    async def log_gitnexus_progress(self, phase: str, kind: str, done: int,
                                    total: int, hits: int,
                                    detail: str | None = None) -> None:
        """Emit a GitNexus-track LLM progress line (sink/source/taint/chain-verdict).

        Routed through the dispatcher like other events → scrolls above the Live
        footer and persists to workflow.log. best-effort: no-op when no dispatcher.
        """
        if self._dispatcher is None:
            return
        from shannon_core.display.events import GitnexusLlmEvent
        await self._dispatcher.dispatch(GitnexusLlmEvent(
            timestamp=format_log_time(), category="GN-LLM", phase=phase, kind=kind,
            done=done, total=total, hits=hits, detail=detail,
        ))
```

- [ ] **Step 4: Add session.log_gitnexus_progress (forward)**

In `packages/core/src/shannon_core/audit/session.py`, add method (next to `log_tool_call`, ~line 69):

```python
    async def log_gitnexus_progress(self, phase: str, kind: str, done: int,
                                    total: int, hits: int,
                                    detail: str | None = None) -> None:
        """Route a GitNexus-track LLM progress line to the workflow log."""
        if self._workflow_logger:
            await self._workflow_logger.log_gitnexus_progress(
                phase, kind, done, total, hits, detail)
```

- [ ] **Step 5: Add NullAuditSession no-op**

In `packages/core/src/shannon_core/audit/session_registry.py`, add to `NullAuditSession` (next to `log_info`):

```python
    async def log_gitnexus_progress(self, phase: str, kind: str, done: int,
                                    total: int, hits: int,
                                    detail: str | None = None) -> None: pass
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd packages/core && python -m pytest tests/audit/test_workflow_logger_gitnexus.py -v`
Expected: PASS（3 tests）

- [ ] **Step 7: Commit**

```bash
git add packages/core/src/shannon_core/audit/workflow_logger.py packages/core/src/shannon_core/audit/session.py packages/core/src/shannon_core/audit/session_registry.py packages/core/tests/audit/test_workflow_logger_gitnexus.py
git commit -m "feat(audit): log_gitnexus_progress 通道(workflow_logger+session+Null)"
```

---

### Task 4: discover_sinks_llm + discover_sources_llm 接 progress_cb

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/sink_discovery_llm.py`（`discover_sinks_llm` + `progress_cb` 参数 + emitter）
- Modify: `packages/core/src/shannon_core/code_index/source_discovery_llm.py`（`discover_sources_llm` + `progress_cb` + emitter，对称）
- Test: `packages/core/tests/code_index/test_sink_discovery_llm.py`、`test_source_discovery_llm.py`

**Interfaces:**
- Consumes: T1 的 `ProgressEmitter`、`ProgressCb`。
- Produces: `discover_sinks_llm(suspicious, llm_client, *, concurrency=None, per_call_timeout=None, progress_cb=None)`；`discover_sources_llm(candidates, llm_client, *, concurrency=None, per_call_timeout=None, progress_cb=None)`。T5 透传 `progress_cb` 进来。

- [ ] **Step 1: Write failing tests**

Append to `packages/core/tests/code_index/test_sink_discovery_llm.py`（参考文件内已有用例的 fixtures：fake llm_client / SuspiciousCall 构造）:

```python
import asyncio
from shannon_core.code_index.sink_discovery_llm import discover_sinks_llm
from shannon_core.code_index.progress import ProgressSample


@pytest.mark.asyncio
async def test_discover_sinks_llm_reports_progress_and_hits(monkeypatch):
    # 复用文件内已有的 suspicious-calls + fake llm_client fixtures/builder。
    suspicious = _build_two_suspicious_calls()   # 2 funcs, 1 will be judged sink
    samples: list[ProgressSample] = []

    async def fake_client(prompt, **kw):
        return '{"calls":[{"call_ref":"<callee>:<line>","is_sink":true}]}'

    async def cb(s: ProgressSample):
        samples.append(s)

    soft, gaps = await discover_sinks_llm(suspicious, fake_client, progress_cb=cb)
    # 每个 function 一次 tick + 一次 finalize
    assert any(s.detail and "is_sink" not in (s.detail or "") for s in samples)  # 至少一 hit
    assert samples[-1].final is True
    assert samples[-1].done == len({sc.block.id for sc in suspicious})


@pytest.mark.asyncio
async def test_discover_sinks_llm_progress_cb_none_ok():
    suspicious = _build_two_suspicious_calls()
    async def fake_client(prompt, **kw):
        return '[]'
    soft, gaps = await discover_sinks_llm(suspicious, fake_client, progress_cb=None)
    assert soft == []
```

> 注：`_build_two_suspicious_calls` 复用该 test 文件已有的 helper（若名称不同，按文件内实际 fixture 替换）。source 侧在 `test_source_discovery_llm.py` 加对称用例（`discover_sources_llm` + `progress_cb`，断言 finalize + hits）。

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/core && python -m pytest tests/code_index/test_sink_discovery_llm.py tests/code_index/test_source_discovery_llm.py -v`
Expected: FAIL — `TypeError: discover_sinks_llm() got an unexpected keyword argument 'progress_cb'`

- [ ] **Step 3: Wire emitter into discover_sinks_llm**

In `packages/core/src/shannon_core/code_index/sink_discovery_llm.py`:

(a) Add import near top:
```python
from shannon_core.code_index.progress import ProgressCb, ProgressEmitter
```

(b) Change signature (line ~230) and body — add `progress_cb` param, build emitter, tick in `_discover_one`, finalize at end:

```python
async def discover_sinks_llm(
    suspicious: list[SuspiciousCall],
    llm_client: LLMClient | None,
    *,
    concurrency: int | None = None,
    per_call_timeout: float | None = None,
    progress_cb: ProgressCb = None,
) -> tuple[list[SinkCallSite], list[RuleGap]]:
    if llm_client is None or not suspicious:
        return [], []
    by_func: dict[str, list[SuspiciousCall]] = defaultdict(list)
    for sc in suspicious:
        by_func[sc.block.id].append(sc)

    emitter = ProgressEmitter("sink-discovery", len(by_func), progress_cb)

    async def _discover_one(item: tuple[str, list[SuspiciousCall]]) -> list[SinkCallSite]:
        _, calls = item
        block = calls[0].block
        prompt = _build_discovery_prompt(block, calls)
        raw = await llm_client(prompt)
        verdicts = _parse_verdicts(raw)
        vmap = {str(v.get("call_ref")): v for v in verdicts}
        out: list[SinkCallSite] = []
        for sc in calls:
            v = vmap.get(f"{sc.callee}:{sc.line}")
            if v is None or not v.get("is_sink"):
                continue
            out.append(_to_soft_sink(sc, v))
        detail = None
        if out:
            s0 = out[0]
            slot = s0.dangerous_slots[0].slot.value if s0.dangerous_slots else "generic"
            detail = f"'{s0.callee_name}' @ {s0.file_path}:{s0.line} slot={slot}"
        await emitter.tick(detail=detail, hits_delta=len(out))
        return out

    conc = concurrency if concurrency is not None else get_max_concurrent()
    timeout = (per_call_timeout if per_call_timeout is not None
               else DEFAULT_PER_CALL_TIMEOUT)
    per_func = await map_llm_with_bounds(
        list(by_func.items()), _discover_one,
        concurrency=conc, per_call_timeout=timeout, label="discover_sinks_llm",
    )
    soft_sinks: list[SinkCallSite] = [s for func_sinks in per_func for s in func_sinks]
    skipped = len(by_func) - len(per_func)   # 超时/失败被 map_llm_with_bounds 丢弃
    await emitter.finalize(
        f"{len(soft_sinks)} soft sinks · {len(_aggregate_gaps(soft_sinks))} rule gaps"
        f" · {skipped} timeouts")
    return soft_sinks, _aggregate_gaps(soft_sinks)
```

- [ ] **Step 4: Wire emitter into discover_sources_llm (symmetric)**

In `packages/core/src/shannon_core/code_index/source_discovery_llm.py`:

(a) Import:
```python
from shannon_core.code_index.progress import ProgressCb, ProgressEmitter
```

(b) Signature + body (line ~141):

```python
async def discover_sources_llm(
    candidates: list[SourceCandidate],
    llm_client: LLMClient | None,
    *,
    concurrency: int | None = None,
    per_call_timeout: float | None = None,
    progress_cb: ProgressCb = None,
) -> list[SourcePoint]:
    if llm_client is None or not candidates:
        return []
    by_func: dict[str, list[SourceCandidate]] = defaultdict(list)
    for c in candidates:
        by_func[c.block.id].append(c)

    emitter = ProgressEmitter("source-discovery", len(by_func), progress_cb)

    async def _discover_one(item):
        _, cands = item
        block = cands[0].block
        prompt = _build_prompt(block)
        raw = await llm_client(prompt)
        fields = _parse_fields(raw)
        out = [_to_soft_source(block, f) for f in fields if f.get("is_source") is True]
        detail = None
        if out:
            s0 = out[0]
            detail = f"'{s0.param_name}' @ {s0.file_path}:{s0.line} source={s0.source_type.value}"
        await emitter.tick(detail=detail, hits_delta=len(out))
        return out

    conc = concurrency if concurrency is not None else get_max_concurrent()
    timeout = (per_call_timeout if per_call_timeout is not None
               else DEFAULT_PER_CALL_TIMEOUT)
    per_func = await map_llm_with_bounds(
        list(by_func.items()), _discover_one,
        concurrency=conc, per_call_timeout=timeout, label="discover_sources_llm",
    )
    all_sources = [s for func_sources in per_func for s in func_sources]
    skipped = len(by_func) - len(per_func)
    await emitter.finalize(f"{len(all_sources)} sources · {skipped} timeouts")
    return all_sources
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd packages/core && python -m pytest tests/code_index/test_sink_discovery_llm.py tests/code_index/test_source_discovery_llm.py -v`
Expected: PASS（含新 progress_cb 用例 + 原有用例不回归）

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/code_index/sink_discovery_llm.py packages/core/src/shannon_core/code_index/source_discovery_llm.py packages/core/tests/code_index/test_sink_discovery_llm.py packages/core/tests/code_index/test_source_discovery_llm.py
git commit -m "feat(code_index): discover_sinks/sources_llm 接 progress_cb 进度"
```

---

### Task 5: taint analysis + build_code_index_with_gitnexus 透传

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/__init__.py`（`build_code_index_with_gitnexus` + `progress_cb` 参数 + 透传给 discover_sinks/sources + `_taint_one` 内 emitter.tick）
- Test: `packages/core/tests/code_index/test_build_code_index.py`（或对应已有 test 文件）

**Interfaces:**
- Consumes: T1 `ProgressEmitter`、T4 `discover_sinks_llm`/`discover_sources_llm` 的 `progress_cb` 参数。
- Produces: `build_code_index_with_gitnexus(repo_path, *, mcp_client, llm_client, auto_index=False, progress_cb=None)`。T7 的 `run_code_index` 注入。

- [ ] **Step 1: Write failing test**

In the build-code-index test file（参考已有用例的 mock 形式，确认 `build_code_index_with_gitnexus` 可在 llm_client=None 或 mock 下跑）:

```python
@pytest.mark.asyncio
async def test_build_code_index_threads_progress_cb(monkeypatch):
    # mock 掉 GitNexus MCP + parse，让 discover_sinks_llm / analyze_taint_llm 收到 cb。
    samples: list = []
    async def cb(s): samples.append(s)
    # ... 复用文件内已有 mock 设置 (mcp_client, blocks) ...
    await build_code_index_with_gitnexus(
        str(repo), mcp_client=mock_mcp, llm_client=None,
        auto_index=False, progress_cb=cb)
    # llm_client=None → discover_*/taint 早退，emitter 不 tick → samples 为空（不爆）。
    # 用 mock llm_client 时断言 samples 含 sink-discovery/taint-analysis 的 finalize。
    assert isinstance(samples, list)
```

> 注：此 task 的集成测试较重；最低限度保证 `progress_cb=None` 与 `progress_cb=<callable>` 两条路径不爆即可，详细样本断言放在 T4 单测层（已覆盖）。

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/core && python -m pytest tests/code_index/test_build_code_index.py -k progress -v`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'progress_cb'`

- [ ] **Step 3: Add progress_cb param + thread through**

In `packages/core/src/shannon_core/code_index/__init__.py`:

(a) Change signature (line ~64):
```python
async def build_code_index_with_gitnexus(
    repo_path: str,
    *,
    mcp_client,
    llm_client,
    auto_index: bool = False,
    progress_cb=None,
) -> tuple[CodeIndex, list[RuleGap]]:
```

(b) Pass `progress_cb` to `discover_sinks_llm` (line ~168):
```python
    soft_sinks, rule_gaps = await discover_sinks_llm(suspicious, llm_client,
                                                     progress_cb=progress_cb)
```

(c) Pass `progress_cb` to `discover_sources_llm`（找到它的调用点，按对称方式加 `progress_cb=progress_cb`；若 source discovery 在另一函数内，确保 `progress_cb` 经参数传达到该调用点 —— 实现时 grep `discover_sources_llm(` 定位）。

(d) Thread emitter into `_taint_one` (line ~185-203). Build emitter before `map_llm_with_bounds`, tick inside `_taint_one`:
```python
    from shannon_core.code_index.progress import ProgressEmitter
    taint_emitter = ProgressEmitter("taint-analysis", len(sinks_by_func), progress_cb)

    async def _taint_one(item):
        func_id, func_sinks = item
        block = blocks_by_id.get(func_id)
        if block is None:
            await taint_emitter.tick(detail=None, hits_delta=0)   # 仍计数
            return None
        result = await analyze_taint_llm(
            block=block, sinks_in_func=func_sinks, llm_client=llm_client,
        )
        # result 的 taint_flow 计数：实现时按 analyze_taint_llm 实际返回结构取
        flows_count = _count_taint_flows(result)   # 见下方 helper
        detail = f"taint flow in {block.function_name}" if flows_count else None
        await taint_emitter.tick(detail=detail, hits_delta=flows_count)
        return (func_id, result)

    ...
    taint_pairs = await map_llm_with_bounds(
        list(sinks_by_func.items()), _taint_one,
        concurrency=get_max_concurrent(),
        label="analyze_taint_llm",
    )
    await taint_emitter.finalize(
        f"{sum(_count_taint_flows(r) for _, r in taint_pairs)} taint_flows")
```

Add helper near top of the function（实现时按 `analyze_taint_llm` 实际返回字段名定，spec §6.5 不臆测；先用一个能跑通的取值）:
```python
def _count_taint_flows(result) -> int:
    if result is None:
        return 0
    # analyze_taint_llm 返回结构实现时确认：如 result.taint_flows / result.flows / len(result)
    flows = getattr(result, "taint_flows", None) or getattr(result, "flows", None)
    if flows is not None:
        return len(flows)
    return 0
```

> 实现者注：`_count_taint_flows` 的字段名是本 plan 唯一需运行时确认的点 —— 先 `grep -n "class TaintAnalysisResult\|def analyze_taint_llm" packages/core/src/shannon_core/code_index/llm_taint_analyzer.py` 看返回结构，对齐字段后简化此 helper（可能直接 `len(result.taint_flows)`）。这是 T5 的第一个实现步骤（在写 test 之前先确认字段，使 test 断言准确）。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/core && python -m pytest tests/code_index/test_build_code_index.py -v`
Expected: PASS（新用例 + 原有用例不回归）

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/__init__.py packages/core/tests/code_index/test_build_code_index.py
git commit -m "feat(code_index): build_code_index_with_gitnexus 透传 progress_cb(taint+sink+source)"
```

---

### Task 6: chain_verdict builders 接 progress_cb

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/vuln_chain_builders/injection_builder.py`
- Modify: `packages/core/src/shannon_core/code_index/vuln_chain_builders/xss_builder.py`
- Modify: `packages/core/src/shannon_core/code_index/vuln_chain_builders/ssrf_builder.py`
- Test: `packages/core/tests/code_index/vuln_chain_builders/test_injection_builder.py`（及 xss/ssrf 对应 test）

**Interfaces:**
- Consumes: T1 `ProgressEmitter`、`ProgressCb`。
- Produces: `build_injection_findings(pgraph, *, llm_client, progress_cb=None)` / `build_xss_findings(pgraph, *, llm_client, sink_call_sites=..., progress_cb=None)` / `build_ssrf_findings(pgraph, *, llm_client, progress_cb=None)`。T7 的 `run_gitnexus_chain_verdict` 透传。

- [ ] **Step 1: Write failing test**

In `packages/core/tests/code_index/vuln_chain_builders/test_injection_builder.py`（参考已有用例的 pgraph + mock judge_chain_verdict fixture）:

```python
@pytest.mark.asyncio
async def test_build_injection_findings_reports_chain_progress(monkeypatch):
    samples: list = []
    async def cb(s): samples.append(s)
    pgraph = _build_pgraph_with_n_chains(3)   # 复用文件内 helper
    async def fake_judge(chain, *, llm_client):
        return _fake_verdict(vulnerable=(chain is _build_pgraph_with_n_chains.first))
    monkeypatch.setattr("shannon_core.code_index.vuln_chain_builders.injection_builder.judge_chain_verdict", fake_judge)
    findings = await build_injection_findings(pgraph, llm_client=_fake_llm, progress_cb=cb)
    assert len([s for s in samples if not s.final]) == 3   # 3 chains → 3 ticks
    assert samples[-1].final is True
```

> 注：`_build_pgraph_with_n_chains` / `_fake_verdict` 复用该 test 文件已有 fixtures（按实际名称替换）。xss/ssrf 加对称用例。

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/core && python -m pytest tests/code_index/vuln_chain_builders/ -v`
Expected: FAIL — `TypeError: build_injection_findings() got an unexpected keyword argument 'progress_cb'`

- [ ] **Step 3: Wire emitter into each builder**

In `packages/core/src/shannon_core/code_index/vuln_chain_builders/injection_builder.py`:

(a) Import:
```python
from shannon_core.code_index.progress import ProgressCb, ProgressEmitter
```

(b) Signature + loop (line ~36-44). Add `progress_cb` param, build emitter from `candidates`, tick + finalize:
```python
async def build_injection_findings(
    pgraph: ParameterPropagationGraph,
    *,
    llm_client: Callable[..., Awaitable[str]],
    progress_cb: ProgressCb = None,
) -> list[InjectionVulnerability]:
    candidates = extract_candidate_chains(pgraph, vuln_class="injection")
    emitter = ProgressEmitter("chain-verdict", len(candidates), progress_cb)
    findings: list[InjectionVulnerability] = []
    for i, chain in enumerate(candidates, start=1):
        verdict = await judge_chain_verdict(chain, llm_client=llm_client)
        is_vuln = (verdict.verdict == "vulnerable")
        detail = None
        if is_vuln:
            detail = f"INJ-GN-{i:02d} vulnerable: source={_source_text(chain)} → sink={chain.sink_call_site_id}"
        await emitter.tick(detail=detail, hits_delta=1 if is_vuln else 0)
        # ... 原有 InjectionVulnerability 构造 + append 不变 ...
    await emitter.finalize(f"{len(findings)} vulnerable · "
                           f"{len(candidates)} candidates judged")
    return findings
```

> 实现者注：保留原循环体内 `verdict` 之后构造 `InjectionVulnerability` 并 `findings.append(...)` 的全部逻辑（line 45-59+），仅在外层包 `emitter.tick(detail=..., hits_delta=...)`。`ID` 用的 `i` 与原有 `enumerate(candidates, start=1)` 一致（INJ-GN-{i:02d}）。

(c) 对 `xss_builder.py` / `ssrf_builder.py` 做对称改动（phase 同为 `"chain-verdict"`，detail 前缀 `XSS-GN-NN` / `SSRF-GN-NN`；xss 保留 `sink_call_sites` 参数）。

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/core && python -m pytest tests/code_index/vuln_chain_builders/ -v`
Expected: PASS（新用例 + 原有用例不回归）

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/vuln_chain_builders/ packages/core/tests/code_index/vuln_chain_builders/
git commit -m "feat(code_index): inj/xss/ssrf builder 接 progress_cb chain-verdict 进度"
```

---

### Task 7: whitebox activity 注入 progress_cb（采样 + 包装 session）

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`（`run_code_index` 注入给 `build_code_index_with_gitnexus`；`run_gitnexus_chain_verdict` 注入给三个 builder；+ 共用 `_make_gitnexus_progress_cb` helper）
- Test: `packages/whitebox/tests/pipeline/test_activities_gitnexus_progress.py`（新增）

**Interfaces:**
- Consumes: T3 `session.log_gitnexus_progress`、T5 `build_code_index_with_gitnexus(progress_cb=)`、T6 `build_*_findings(progress_cb=)`。
- Produces: `_make_gitnexus_progress_cb(session)` —— 采样（final/hit/done==1/done%10==0）后调 `session.log_gitnexus_progress`，phase 透传自 `sample.phase`（core 已带正确 phase），best-effort。

**说明（authz）**：`run_authz_gitnexus_judge` 是单次 LLM 调用（非逐候选循环），已有开始/结束 `log_info`，**不接 progress_cb**（spec 偏离已在上文说明）。

- [ ] **Step 1: Write failing test**

Create `packages/whitebox/tests/pipeline/test_activities_gitnexus_progress.py`:

```python
import asyncio
import pytest
from shannon_whitebox.pipeline.activities import _make_gitnexus_progress_cb
from shannon_core.code_index.progress import ProgressSample


class _FakeSession:
    def __init__(self): self.calls = []
    async def log_gitnexus_progress(self, phase, kind, done, total, hits, detail=None):
        self.calls.append((phase, kind, done, total, hits, detail))


def _sample(done, *, detail=None, final=False, hits=0, phase="sink-discovery"):
    return ProgressSample(phase, done, 87, hits, detail, final)


@pytest.mark.asyncio
async def test_sampling_progress_emits_only_at_1_and_every_10():
    sess = _FakeSession()
    cb = _make_gitnexus_progress_cb(sess)
    for done in range(1, 26):
        await cb(_sample(done))   # all misses (detail=None, not final)
    kinds = [c[1] for c in sess.calls]
    assert kinds == ["progress", "progress", "progress"]   # done == 1, 10, 20
    assert all(c[0] == "sink-discovery" for c in sess.calls)  # phase 透传自 sample


@pytest.mark.asyncio
async def test_hit_emitted_immediately_regardless_of_done():
    sess = _FakeSession()
    cb = _make_gitnexus_progress_cb(sess)
    await cb(_sample(7, detail="'x' @ f.py:1 slot=a", hits=1))   # 7 不是采样点
    assert sess.calls[0][1] == "hit" and sess.calls[0][5].startswith("'x'")


@pytest.mark.asyncio
async def test_summary_emitted_on_final():
    sess = _FakeSession()
    cb = _make_gitnexus_progress_cb(sess)
    await cb(_sample(87, detail="12 soft sinks", final=True))
    assert sess.calls[0][1] == "summary"


@pytest.mark.asyncio
async def test_mid_range_miss_is_silent():
    sess = _FakeSession()
    cb = _make_gitnexus_progress_cb(sess)
    for done in (2, 3, 5, 7, 9):   # 非 1、非 %10、无 hit、非 final
        await cb(_sample(done))
    assert sess.calls == []


@pytest.mark.asyncio
async def test_session_exception_swallowed():
    class Boom:
        async def log_gitnexus_progress(self, *a, **k):
            raise RuntimeError("down")
    cb = _make_gitnexus_progress_cb(Boom())
    await cb(_sample(1))   # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/whitebox && python -m pytest tests/pipeline/test_activities_gitnexus_progress.py -v`
Expected: FAIL — `ImportError: cannot import name '_make_gitnexus_progress_cb'`

- [ ] **Step 3: Add the helper + wire into run_code_index / run_gitnexus_chain_verdict**

In `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`:

(a) Add helper near `_make_gitnexus_llm_client` (~line 460)。cb 从 `sample.phase` 取 phase（core 的 ProgressEmitter 已带 sink-discovery/source-discovery/taint-analysis/chain-verdict），故 helper 只需 `session` 一个参数；best-effort：

```python
def _make_gitnexus_progress_cb(session):
    """采样 + 包装 session.log_gitnexus_progress。best-effort。

    触发：final→summary；detail 非空→hit；done==1 或 done%10==0→progress；其余静默。
    phase 透传自 sample.phase（core 的 ProgressEmitter 已带正确 phase）。
    """
    async def cb(sample) -> None:
        if sample.final:
            kind, detail = "summary", sample.detail
        elif sample.detail:
            kind, detail = "hit", sample.detail
        elif sample.done == 1 or sample.done % 10 == 0:
            kind, detail = "progress", None
        else:
            return
        try:
            await session.log_gitnexus_progress(
                sample.phase, kind, sample.done, sample.total, sample.hits, detail)
        except Exception:
            pass
    return cb
```

(b) In `run_code_index`（~line 502，`build_code_index_with_gitnexus(...)` 调用处）inject cb —— sink/source/taint 三段在 core 内各自建 emitter、phase 由 core 定，activity 只需透传同一个 cb：

```python
            index, rule_gaps = await build_code_index_with_gitnexus(
                str(repo),
                mcp_client=mcp,
                llm_client=_llm_taint_client,
                auto_index=False,
                progress_cb=_make_gitnexus_progress_cb(get_audit_session()),
            )
```

(c) In `run_gitnexus_chain_verdict`（~line 940-950 builder 循环）inject cb（三个 builder 共用同一 cb，phase 都标 chain-verdict，detail 前缀 INJ/XSS/SSRF-GN-NN 区分）：

```python
            _chain_cb = _make_gitnexus_progress_cb(get_audit_session())
            for vc, builder in (
                ("injection", build_injection_findings),
                ("xss", build_xss_findings),
                ("ssrf", build_ssrf_findings),
            ):
                try:
                    if vc == "xss":
                        findings = await builder(pgraph, llm_client=llm,
                                                 sink_call_sites=sink_call_sites,
                                                 progress_cb=_chain_cb)
                    else:
                        findings = await builder(pgraph, llm_client=llm,
                                                 progress_cb=_chain_cb)
                except Exception as exc:
                    logger.warning("gitnexus chain-verdict %s failed: %s", vc, exc)
                    continue
                ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/whitebox && python -m pytest tests/pipeline/test_activities_gitnexus_progress.py -v`
Expected: PASS（5 tests）

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/tests/pipeline/test_activities_gitnexus_progress.py
git commit -m "feat(whitebox): activity 注入 GitNexus 轨 progress_cb(采样+session 包装)"
```

---

## Definition of Done

- 所有 7 个 task 的测试通过（只跑改动相关文件，勿广跑全套）。
- `workflow.log` 在一次白盒扫描中可见 `[GN-LLM] sink-discovery` / `[GN-LLM] chain-verdict` 等进度行（progress/hit/summary 三态）。
- `progress_cb=None` 路径（`SHANNON_GITNEXUS_LLM_ENABLED=0`）不爆、不影响扫描。
- 真机冒烟：跑一次 juice-shop 白盒，`tail -f` workflow.log 观察进度行；`grep GN-LLM` 验证可过滤。

## Self-Review（写完后自查，已合并入上述）

- **Spec 覆盖**：sink/source/taint/chain_verdict 全覆盖（T4/T5/T6）；GitnexusLlmEvent+渲染（T2）；audit 通道（T3）；activity wiring（T7）。authz 经探索撤销（偏离已声明）。
- **类型一致**：`ProgressSample` 字段（phase/done/total/hits/detail/final）跨 T1→T4/T5/T6/T7 一致；`log_gitnexus_progress(phase, kind, done, total, hits, detail)` 跨 T3→T7 一致；`build_*_findings(progress_cb=)` / `discover_*(progress_cb=)` / `build_code_index_with_gitnexus(progress_cb=)` 命名一致。
- **唯一运行时确认点**：`_count_taint_flows` 的字段名（T5 已标注，实现时 grep `analyze_taint_llm` 返回结构）。
