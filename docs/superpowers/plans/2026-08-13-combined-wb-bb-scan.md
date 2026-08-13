# 白盒+黑盒一键组合扫描 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 白盒扫描页加开关，一键发起「白盒→黑盒自动接力」组合扫描，单目录三桶产物，三视图报告（白盒/黑盒/融合）。

**Architecture:** 方案 D（phase_end + 独立编排 task + _watch 纯 tail）。单目录靠接力时把黑盒 `event_file`/`repo_path` 指向白盒 scan_dir（黑盒 workflow 零改动，`scan_dir=event_file.parent`）。接力编排由 scan_manager 新增 `_combined_orchestrator`（`await wb_handle.result()` → 预检 → submit blackbox → `await bb_handle.result()` → 融合报告）驱动；白盒 `finalize_summary` 组合模式写 `phase_end` 而非 `scan_end`（保持 scan_end 语义不变量）；`_watch` 回归纯 tail。

**Tech Stack:** Python 3.11 / temporalio / pydantic / pytest（后端）；React + TypeScript + TanStack + vitest（前端）。双引擎（claude-agent-sdk / openai-agents）流程一致。

**Spec:** `docs/superpowers/specs/2026-08-12-combined-wb-bb-scan-design.md`（commit cf9df41c）

## Global Constraints

- **纯白盒/纯黑盒零回归**：所有改动必须守「开关关 / 黑盒独立入口」行为一字不改。每个相关任务带回归测试。
- **scan_end 语义不变量**：全场景 events 文件只有一个 `scan_end` = 真结束。组合模式阶段边界用 `phase_end`，绝不剥 scan_end。
- **黑盒 workflow 零代码改动**：单目录仅靠接力时控制 `event_file`/`repo_path` 两入参；不动 `BlackboxScanWorkflow` / 黑盒 activities。
- **双引擎一致**：流程层抽象，业务侧不感知 claude/openai。
- **预存陷阱**：前端命令必须 `cd packages/web/frontend`（cwd 不持久）；改 web/worker src 须 rebuild `supernova-worker` 镜像；pytest 只跑改动相关子集（全套会 hang）。
- **计费不变量**：`cost_usd` 字段名留（值=cost_currency 金额），组合扫描 cost = 白盒 + 黑盒各自 metrics 累积，不重复计。

## File Structure

**core（事件基础设施）**
- `packages/core/src/supernova_core/display/events.py` — 加 `PhaseEndEvent` dataclass
- `packages/core/src/supernova_core/display/structured_event_renderer.py` — `render()` 识别 `PhaseEndEvent` 写 `phase_end` 行（非 `scan_end`）
- `packages/core/src/supernova_core/audit/workflow_logger.py` — 加 `log_phase_complete(summary)`（dispatch `PhaseEndEvent`，不发 `SummaryEvent`，不触发 scan_end）
- `packages/core/src/supernova_core/audit/session.py` — 加 `log_phase_complete(summary)`（调 logger + close，**不 update_session_status**）

**whitebox（finalize 组合分支）**
- `packages/whitebox/src/supernova_whitebox/pipeline/shared.py` — `PipelineInput` 加 `combined: bool = False`
- `packages/whitebox/src/supernova_whitebox/pipeline/activities.py:1806` — `finalize_summary` 组合分支调 `log_phase_complete`（纯白盒不变）

**web（接力编排）**
- `packages/web/src/supernova_web/models.py` — `ScanRequest` 加 `_whitebox_combined_optional` validator
- `packages/web/src/supernova_web/components/scan_manager.py` — `start` 白盒组合分支（单目录 + session 字段）；新增 `_combined_orchestrator` / `_submit_blackbox_chained` / `_write_final_scan_end` / `_emit_phase_boundary`；扩展 `orphan_reconciler`/`resume`/`cancel`
- `packages/web/src/supernova_web/components/combined_report_renderer.py` — **新增**，融合报告生成（复用 FindingsRenderer 读 queue 逻辑）
- `packages/web/src/supernova_web/components/deliverables_reader.py` — `_infer_track` + `resolve_track_deliverable` 支持 `combined` track

**前端**
- `packages/web/frontend/src/pages/ScanNewPage.tsx` — 白盒开关 + `buildBody` 组合分支
- `packages/web/frontend/src/components/ScanFormFields.tsx` — 抽共享认证组件 + 白盒组合展开区
- `packages/web/frontend/src/routes/WorkspaceDetail/ScanDetail.tsx` — 两段时间线（`phase_boundary` 切分）
- `packages/web/frontend/src/routes/WorkspaceDetail/ReportTab.tsx` — 三子 tab
- `packages/web/frontend/src/routes/WorkspaceDetail/DeliverablesTab.tsx` — 三桶

---

## Task 0: 验证关键假设（黑盒 workspace_name 用途 + finalize status 写入点）

spec §13 风险表首项。写一个探查测试锁死两个不变量，后续任务据此放心。

**Files:**
- Test: `packages/web/tests/test_combined_assumptions.py`

**Interfaces:**
- Produces: 两个被锁死的事实——(1) 黑盒 web 路径产物/heartbeat/session 落点 = `event_file.parent`（非 `workspace_name`）；(2) `log_workflow_complete` 既写 scan_end 又写 session status（故组合模式必须换 `log_phase_complete` 跳过两者）。

- [ ] **Step 1: 写探查测试（黑盒 workspace_name 用途）**

```python
# packages/web/tests/test_combined_assumptions.py
"""锁死组合扫描单目录方案依赖的两个不变量。"""
import re
from pathlib import Path


def test_blackbox_web_path_uses_event_file_parent_not_workspace_name():
    """黑盒 web 路径：workspace_path = event_file.parent（非 ws_root/workspace_name）。
    组合扫描接力把黑盒 event_file 指向白盒 scan_dir → 黑盒产物自动落白盒目录。"""
    wf = Path("packages/blackbox/src/supernova_blackbox/pipeline/workflows.py").read_text()
    # 97 行附近：event_file 分支优先
    assert "Path(input.event_file).parent" in wf
    # workspace_name 仅 CLI 路径用（elif 分支），web 路径不走
    m = re.search(r"if input\.event_file:.*?elif input\.workspace_name:", wf, re.S)
    assert m, "黑盒须先判 event_file 再判 workspace_name（web 路径优先）"
```

- [ ] **Step 2: 写探查测试（finalize status 写入点）**

```python
def test_log_workflow_complete_writes_both_scan_end_and_status():
    """log_workflow_complete 三合一：dispatch SummaryEvent(→scan_end) + close + update_session_status。
    故白盒组合模式不能调它（会写 scan_end + 终态 status），须用新 log_phase_complete。"""
    sess = Path("packages/core/src/supernova_core/audit/session.py").read_text()
    # session.py:162 log_workflow_complete 同时调 logger.log_workflow_complete(→SummaryEvent→scan_end)
    # 与 update_session_status(summary.status)
    assert "log_workflow_complete" in sess and "update_session_status" in sess
    wl = Path("packages/core/src/supernova_core/audit/workflow_logger.py").read_text()
    assert "SummaryEvent(" in wl  # log_workflow_complete dispatch SummaryEvent
    ser = Path("packages/core/src/supernova_core/display/structured_event_renderer.py").read_text()
    assert 'isinstance(event, SummaryEvent)' in ser and '"type": "scan_end"' in ser
```

- [ ] **Step 3: 运行测试确认通过（锁死现状）**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/web/tests/test_combined_assumptions.py -v`
Expected: PASS（锁死两个不变量为真；若 FAIL，说明假设破裂，须回到 spec §13 调整方案）

- [ ] **Step 4: Commit**

```bash
git add packages/web/tests/test_combined_assumptions.py
git commit -m "test(combined): 锁止单目录接力依赖的两个不变量（workspace_name/status）"
```

---

## Task 1: PhaseEndEvent + renderer 识别 + log_phase_complete（core 基础设施）

给 events 加阶段结束事件类型，renderer 写 `phase_end` 行（非 `scan_end`），logger/session 加 `log_phase_complete`（不发 SummaryEvent、不写 status）。

**Files:**
- Modify: `packages/core/src/supernova_core/display/events.py`（加 `PhaseEndEvent`）
- Modify: `packages/core/src/supernova_core/display/structured_event_renderer.py`（`render` 识别）
- Modify: `packages/core/src/supernova_core/audit/workflow_logger.py`（加 `log_phase_complete`）
- Modify: `packages/core/src/supernova_core/audit/session.py`（加 `log_phase_complete`）+ `session_registry.py` `NullAuditSession`（no-op）
- Test: `packages/core/tests/test_phase_end_event.py`

**Interfaces:**
- Produces: `PhaseEndEvent(phase: str, status: str)`；`StructuredEventRenderer.render` 收到 `PhaseEndEvent` 时写 `{type:"phase_end", phase, status}`；`AuditSession.log_phase_complete(summary)` / `WorkflowLogger.log_phase_complete(summary)`。

- [ ] **Step 1: 写失败测试**

```python
# packages/core/tests/test_phase_end_event.py
import asyncio, json
from pathlib import Path
from supernova_core.display.events import PhaseEndEvent
from supernova_core.display.structured_event_renderer import StructuredEventRenderer


def test_phase_end_event_written_as_phase_end_not_scan_end(tmp_path: Path):
    evf = tmp_path / "events.ndjson"
    r = StructuredEventRenderer(str(evf))
    asyncio.run(r.render(PhaseEndEvent(
        timestamp="2026-08-13T00:00:00", category="CONTROL", phase="whitebox", status="completed")))
    lines = evf.read_text().strip().splitlines()
    payload = json.loads(lines[-1])
    assert payload["type"] == "phase_end"
    assert payload["phase"] == "whitebox"
    assert payload["type"] != "scan_end"  # 关键：不写 scan_end
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/test_phase_end_event.py -v`
Expected: FAIL（`PhaseEndEvent` 未定义）

- [ ] **Step 3: 加 PhaseEndEvent（events.py，SummaryEvent 之后 ~line 145）**

```python
@dataclass(frozen=True)
class PhaseEndEvent(DisplayEvent):
    """组合扫描阶段边界（方案 D）：白盒完成时写，表阶段结束非整体结束。
    renderer 写 phase_end 行（非 scan_end），保持 scan_end 语义不变量。"""
    phase: str          # "whitebox" | "blackbox"
    status: str = "completed"
```

- [ ] **Step 4: renderer 识别 PhaseEndEvent（structured_event_renderer.py:render，SummaryEvent 分支后加）**

```python
    async def render(self, event: DisplayEvent) -> None:
        async with self._lock:
            fh = await self._ensure_open()
            await fh.write(json.dumps(self._serialize(event), default=str, ensure_ascii=False) + "\n")
            await fh.flush()
            if isinstance(event, SummaryEvent):
                await fh.write(json.dumps({
                    "ts": _normalize_ts(event.timestamp), "category": "CONTROL",
                    "type": "scan_end", "status": event.status,
                }, ensure_ascii=False) + "\n")
                await fh.flush()
            elif isinstance(event, PhaseEndEvent):
                await fh.write(json.dumps({
                    "ts": _normalize_ts(event.timestamp), "category": "CONTROL",
                    "type": "phase_end", "phase": event.phase, "status": event.status,
                }, ensure_ascii=False) + "\n")
                await fh.flush()
```
顶部 import 加 `PhaseEndEvent`：`from supernova_core.display.events import DisplayEvent, SummaryEvent, PhaseEndEvent`

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest packages/core/tests/test_phase_end_event.py -v`
Expected: PASS

- [ ] **Step 6: 加 WorkflowLogger.log_phase_complete（workflow_logger.py，log_workflow_complete 之后）**

```python
    async def log_phase_complete(self, summary: WorkflowSummary) -> None:
        """组合扫描白盒阶段结束（方案 D）：dispatch PhaseEndEvent（renderer 写 phase_end），
        不发 SummaryEvent（不触发 scan_end），供 finalize_summary 组合分支调用。"""
        if self._dispatcher is None:
            return
        await self._dispatcher.dispatch(PhaseEndEvent(
            timestamp=format_log_time(), category="CONTROL",
            phase="whitebox", status=summary.status))
```
import 加 `PhaseEndEvent`。

- [ ] **Step 7: 加 AuditSession.log_phase_complete（session.py:162 旁）**

```python
    async def log_phase_complete(self, summary: WorkflowSummary) -> None:
        """组合扫描白盒阶段结束：写 phase_end + close logger，【不 update_session_status】
        （status 保持 running，由黑盒 finalize 写终态 completed）。"""
        if self._workflow_logger:
            await self._workflow_logger.log_phase_complete(summary)
            await self._workflow_logger.close()
        # 注意：不调 metrics_tracker.update_session_status（区别于 log_workflow_complete）
```

- [ ] **Step 8: NullAuditSession.log_phase_complete no-op（session_registry.py:89 旁）**

```python
    async def log_phase_complete(self, summary: Any) -> None: pass
```

- [ ] **Step 9: 回归测试（现有 scan_end 行为不破）**

Run: `uv run pytest packages/core/tests/ -k "event or renderer or scan_end" -v 2>&1 | tail -20`
Expected: 相关测试 PASS（PhaseEndEvent 新增不影响 SummaryEvent/scan_end 路径）

- [ ] **Step 10: Commit**

```bash
git add packages/core/src/supernova_core/display/events.py packages/core/src/supernova_core/display/structured_event_renderer.py packages/core/src/supernova_core/audit/workflow_logger.py packages/core/src/supernova_core/audit/session.py packages/core/src/supernova_core/audit/session_registry.py packages/core/tests/test_phase_end_event.py
git commit -m "feat(core): PhaseEndEvent + log_phase_complete（组合扫描阶段边界，方案 D）"
```

---

## Task 2: 白盒 PipelineInput.combined + finalize_summary 组合分支

白盒 finalize 组合模式调 `log_phase_complete`（写 phase_end、不写 scan_end/status），纯白盒不变。

**Files:**
- Modify: `packages/whitebox/src/supernova_whitebox/pipeline/shared.py:8`（PipelineInput 加 `combined`）
- Modify: `packages/whitebox/src/supernova_whitebox/pipeline/activities.py:1806`（finalize_summary 组合分支）
- Test: `packages/whitebox/tests/test_finalize_combined_phase_end.py`

**Interfaces:**
- Consumes: Task 1 的 `AuditSession.log_phase_complete`。
- Produces: `PipelineInput.combined: bool = False`；`finalize_summary(input, summary)` 在 `input.combined=True` 时写 phase_end 不写 scan_end。

- [ ] **Step 1: 写失败测试**

```python
# packages/whitebox/tests/test_finalize_combined_phase_end.py
"""白盒 finalize 组合分支：combined=True 调 log_phase_complete（phase_end，不 scan_end/status）。
纯白盒（combined=False）仍调 log_workflow_complete（scan_end + status）。"""
from unittest.mock import AsyncMock, patch
from supernova_whitebox.pipeline.activities import finalize_summary
from supernova_whitebox.pipeline.shared import ActivityInput


def _input(combined=False, **kw):
    base = dict(repo_path="/r", workspace_name="w", event_file="/e/events.ndjson")
    base.update(kw)
    # ActivityInput 无 combined；走 finalize 内部从全局读 or 参数。见实现说明。
    return ActivityInput(**base)


async def test_finalize_combined_calls_log_phase_complete():
    with patch("supernova_whitebox.pipeline.activities.ensure_audit_session", new=AsyncMock()), \
         patch("supernova_whitebox.pipeline.activities.get_audit_session") as gs, \
         patch("supernova_whitebox.pipeline.activities.stop_heartbeat", new=AsyncMock()):
        session = AsyncMock()
        session.log_phase_complete = AsyncMock()
        session.log_workflow_complete = AsyncMock()
        # 区分 NullAuditSession：isinstance 检查。用真实 MagicMock 非 NullAuditSession
        from unittest.mock import MagicMock
        gs.return_value = MagicMock(
            __class__=type("S", (), {}),
            log_phase_complete=session.log_phase_complete,
            log_workflow_complete=session.log_workflow_complete,
            get_metrics=AsyncMock(return_value={}))
        await finalize_summary(_input(combined=True), {"status": "completed"})
        session.log_phase_complete.assert_awaited()
        session.log_workflow_complete.assert_not_awaited()  # 关键：组合模式不走 scan_end 路径
```
> 注：`finalize_summary` 当前签名是 `(input: ActivityInput, summary)`。组合标志需经 `ActivityInput` 或进程上下文传入。Step 3 在 `ActivityInput` 加 `combined: bool = False`，finalize 读 `input.combined`。

- [ ] **Step 2: 运行确认失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/whitebox/tests/test_finalize_combined_phase_end.py -v`
Expected: FAIL（`ActivityInput.combined` 不存在 / `log_phase_complete` 未被调）

- [ ] **Step 3: ActivityInput + PipelineInput 加 combined**

`shared.py` ActivityInput（~line 43）加字段：
```python
    combined: bool = False          # 组合扫描：finalize 写 phase_end（非 scan_end），不写终态 status
```
PipelineInput（~line 8）加同字段：
```python
    combined: bool = False          # 组合扫描白盒阶段：True=接力模式
```
workflow 构造 `ActivityInput` 处需透传 `combined=input.combined`——在 workflow 的 finalize_summary 调用处（`workflows.py` grep `finalize_summary`）加 `combined=input.combined`。

- [ ] **Step 4: finalize_summary 组合分支（activities.py:1806）**

把现有：
```python
        await session.log_workflow_complete(ws)
```
改为：
```python
        if input.combined:
            # 方案 D：组合扫描白盒阶段——写 phase_end（非 scan_end），不写终态 status
            await session.log_phase_complete(ws)
        else:
            await session.log_workflow_complete(ws)
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest packages/whitebox/tests/test_finalize_combined_phase_end.py -v`
Expected: PASS

- [ ] **Step 6: 回归测试（纯白盒 finalize 仍写 scan_end）**

Run: `uv run pytest packages/whitebox/tests/ -k "finalize" -v 2>&1 | tail -20`
Expected: 现有 finalize 测试 PASS（combined 默认 False 走原路径）

- [ ] **Step 7: Commit**

```bash
git add packages/whitebox/src/supernova_whitebox/pipeline/shared.py packages/whitebox/src/supernova_whitebox/pipeline/activities.py packages/whitebox/src/supernova_whitebox/pipeline/workflows.py packages/whitebox/tests/test_finalize_combined_phase_end.py
git commit -m "feat(whitebox): finalize_summary 组合分支写 phase_end（纯白盒零回归）"
```

---

## Task 3: ScanRequest 白盒组合 validator

白盒请求带 `url` = 组合扫描模式；复用黑盒认证字段；纯白盒禁认证字段。

**Files:**
- Modify: `packages/web/src/supernova_web/models.py:23`（加 `_whitebox_combined_optional` validator）
- Test: `packages/web/tests/test_scan_request_combined.py`

**Interfaces:**
- Produces: `ScanRequest(type="whitebox", url=..., authentication/auth_profile_id=...)` 合法；纯白盒带认证字段 → ValidationError。

- [ ] **Step 1: 写失败测试**

```python
# packages/web/tests/test_scan_request_combined.py
import pytest
from supernova_web.models import ScanRequest, RepoSource


def test_whitebox_with_url_is_combined():
    req = ScanRequest(type="whitebox", workspace="ws",
                      source=RepoSource(kind="repo", value="r"),
                      url="http://t", authentication={"login_url": "http://t/login"})
    assert req.url == "http://t"


def test_whitebox_without_url_rejects_auth():
    with pytest.raises(Exception):
        ScanRequest(type="whitebox", workspace="ws",
                    source=RepoSource(kind="repo", value="r"),
                    authentication={"login_url": "http://t/login"})


def test_whitebox_combined_auth_xor_enforced():
    # profile + inline 互斥（复用黑盒校验）
    with pytest.raises(Exception):
        ScanRequest(type="whitebox", workspace="ws", url="http://t",
                    source=RepoSource(kind="repo", value="r"),
                    authentication={"login_url": "x"},
                    auth_profile_id="p")
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/web/tests/test_scan_request_combined.py -v`
Expected: FAIL（白盒带认证当前未约束）

- [ ] **Step 3: 抽共享认证校验 + 加 _whitebox_combined_optional（models.py）**

把 `_auth_profile_xor_inline` 的校验体抽成模块函数 `_validate_auth_fields(self)`（复用），在 `_auth_profile_xor_inline`（blackbox）和新 `_whitebox_combined_optional` 都调：

```python
    @model_validator(mode="after")
    def _whitebox_combined_optional(self) -> "ScanRequest":
        """白盒 + url = 组合扫描模式（允许认证字段）；纯白盒（无 url）禁认证字段。"""
        if self.type != "whitebox":
            return self
        has_auth_fields = any([
            self.authentication, self.auth_accounts,
            self.auth_profile_id, self.auth_credential_id, self.auth_credential_ids])
        if self.url:
            # 组合模式：认证字段走与黑盒相同的互斥校验
            self._validate_auth_fields()
        elif has_auth_fields:
            raise ValueError("纯白盒扫描不支持认证字段；如需同时跑黑盒请提供 url（组合扫描）")
        return self
```
`_auth_profile_xor_inline` 内的 `if self.type == "blackbox":` 块改为调 `self._validate_auth_fields()`（提取后逻辑不变），保证 blackbox 行为零回归。`_validate_auth_fields` 把原来 blackbox 分支体内的 has_profile/has_cred/... 校验整体搬入（去掉 `if self.type=="blackbox"` 外壳，因为两个 validator 都已先判定 type）。

- [ ] **Step 4: 运行测试确认通过 + 黑盒回归**

Run: `uv run pytest packages/web/tests/test_scan_request_combined.py packages/web/tests/ -k "scan_request or blackbox or auth" -v 2>&1 | tail -25`
Expected: 新测试 PASS + 现有黑盒 validator 测试 PASS

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/supernova_web/models.py packages/web/tests/test_scan_request_combined.py
git commit -m "feat(web): ScanRequest 白盒组合模式 validator（url+认证，纯白盒禁认证）"
```

---

## Task 4: scan_manager 组合提交（单目录 + session 字段）

`start()` 白盒组合分支：单目录（不新建黑盒 scan_dir）、session 写 `combined`/`bb_phase`/`bb_url`/`bb_auth_ref`、提交白盒时 `PipelineInput.combined=True`、起 `_combined_orchestrator`。

**Files:**
- Modify: `packages/web/src/supernova_web/components/scan_manager.py:148`（start 白盒分支）+ `:299`（_submit_whitebox 传 combined）
- Test: `packages/web/tests/test_scan_manager_combined_start.py`

**Interfaces:**
- Consumes: Task 2 的 `PipelineInput.combined`。
- Produces: `start(whitebox+url)` 产单 scan_id，session 带 `combined=True, bb_phase="pending", bb_url, bb_auth_ref`；`_submit_whitebox` 传 `combined=True`。

- [ ] **Step 1: 写失败测试**

```python
# packages/web/tests/test_scan_manager_combined_start.py
"""白盒组合提交：单 scan_id + session combined/bb_phase 字段 + PipelineInput.combined=True。"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from supernova_web.components.scan_manager import ScanManager
from supernova_web.models import ScanRequest, RepoSource


@pytest.fixture
def mgr(tmp_path):
    m = ScanManager.__new__(ScanManager)
    m._workspaces_dir = tmp_path
    m._store = MagicMock()
    m._store.create_scan.return_value = ("repo-ts", tmp_path / "repo-ts")
    m._store.get_scan_dir.return_value = None
    m._handles = {}; m._tasks = {}; m._active_reqs = {}
    m._max_concurrent = 5; m._scan_timeout = 0
    m._create_scan_lock = MagicMock()
    return m


async def test_combined_start_writes_session_fields(mgr, tmp_path):
    scan_dir = tmp_path / "repo-ts"; scan_dir.mkdir()
    mgr._store.create_scan.return_value = ("repo-ts", scan_dir)
    req = ScanRequest(type="whitebox", workspace="ws", url="http://t",
                      source=RepoSource(kind="repo", value="r"),
                      authentication={"login_url": "http://t/l"})
    with patch.object(mgr, "_check_temporal", new=AsyncMock()), \
         patch.object(mgr, "_resolve_inputs", new=AsyncMock(return_value=("r", None))), \
         patch.object(mgr, "_submit_whitebox", new=AsyncMock(return_value=MagicMock())), \
         patch.object(mgr, "_watch", new=MagicMock()), \
         patch.object(mgr, "_spawn_combined_orchestrator", new=AsyncMock()), \
         patch("supernova_web.components.scan_manager.SessionManager") as SM:
        sm = MagicMock(); SM.return_value = sm
        ws, scan_id = await mgr.start(req)
        # session 写了组合字段
        calls = sm.update_session.call_args_list
        written = {}
        for c in calls:
            written.update(c.kwargs.get("data", c.args[-1] if c.args else {}))
        assert written.get("combined") is True
        assert written.get("bb_phase") == "pending"
        assert written.get("bb_url") == "http://t"
        # _submit_whitebox 被传 combined（经 PipelineInput，在 _submit_whitebox 内构造）
        mgr._submit_whitebox.assert_awaited()
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/web/tests/test_scan_manager_combined_start.py -v`
Expected: FAIL（start 未写 combined 字段 / `_spawn_combined_orchestrator` 不存在）

- [ ] **Step 3: start 白盒分支识别组合 + 写 session 字段（scan_manager.py:197-203）**

现有白盒分支 `if req.type == "whitebox":` 块内，在 `update_session({"source_repo": ...})` 处扩展：

```python
            if req.type == "whitebox":
                is_combined = bool(req.url)
                handle = await self._submit_whitebox(
                    target, ws, scan_id, scan_dir, event_file, req.url or "",
                    combined=is_combined)
                sess_update = {"source_repo": req.source.value if req.source else None}
                if is_combined:
                    sess_update.update({
                        "combined": True, "bb_phase": "pending",
                        "bb_url": req.url,
                        # bb_auth_ref：认证快照（profile_id 或 inline 标记）；接力展开后清明文
                        "bb_auth_ref": self._snapshot_auth_ref(req),
                    })
                SessionManager(scan_dir.parent).update_session(scan_dir, sess_update)
```

- [ ] **Step 4: _submit_whitebox 接 combined 参数 + 传 PipelineInput（scan_manager.py:299）**

签名加 `combined: bool = False`；构造 `PipelineInput` 加 `combined=combined`：
```python
    async def _submit_whitebox(self, target, ws, scan_id, scan_dir, event_file,
                               web_url, combined: bool = False) -> Any:
        ...
        inp = PipelineInput(
            ...,
            combined=combined,
        )
```

- [ ] **Step 5: start 末尾起编排 task（scan_manager.py:222 附近）**

`self._tasks[scan_key] = asyncio.create_task(self._watch(...))` 之后：
```python
        if req.type == "whitebox" and req.url:
            self._spawn_combined_orchestrator(scan_key, handle, scan_dir, req)
```

- [ ] **Step 6: 加 _snapshot_auth_ref + _spawn_combined_orchestrator 占位（scan_manager.py）**

```python
    def _snapshot_auth_ref(self, req: ScanRequest) -> dict:
        """接力参数快照（存 session，接力时展开成 scan-config.yaml）。
        inline 存原始 dict（敏感，接力后清）；profile 存 {profile_id, cred_id, cred_ids}。"""
        if req.auth_profile_id:
            return {"profile_id": req.auth_profile_id,
                    "cred_id": req.auth_credential_id,
                    "cred_ids": req.auth_credential_ids}
        return {"inline": req.authentication, "accounts": req.auth_accounts}

    def _spawn_combined_orchestrator(self, scan_key, wb_handle, scan_dir, req):
        """Task 5 实现接力体；此任务先接线（start 调用点）。"""
        task = asyncio.create_task(self._combined_orchestrator(scan_key, wb_handle, scan_dir, req))
        self._orchestrator_tasks = getattr(self, "_orchestrator_tasks", {})
        self._orchestrator_tasks[scan_key] = task
```

- [ ] **Step 7: 运行测试确认通过**

Run: `uv run pytest packages/web/tests/test_scan_manager_combined_start.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add packages/web/src/supernova_web/components/scan_manager.py packages/web/tests/test_scan_manager_combined_start.py
git commit -m "feat(web): scan_manager 组合提交（单目录 + session combined/bb_phase 字段）"
```

---

## Task 5: _combined_orchestrator 接力编排（核心）

实现接力：`await wb_handle.result()` → 预检产物 → `_submit_blackbox_chained` → 写 phase_boundary → `await bb_handle.result()` → 融合报告。try/except 契约必写最终 scan_end。

**Files:**
- Modify: `packages/web/src/supernova_web/components/scan_manager.py`（新增 `_combined_orchestrator` / `_submit_blackbox_chained` / `_write_final_scan_end` / `_emit_phase_boundary` / `_whitebox_deliverables_ready` / `_mark_bb`）
- Test: `packages/web/tests/test_combined_orchestrator.py`

**Interfaces:**
- Consumes: Task 4 的 `_spawn_combined_orchestrator` 调用点 + session 字段；Task 2 的白盒 phase_end。
- Produces: `_combined_orchestrator`（接力 + 报告 + 异常收尾）；`_submit_blackbox_chained`（event_file/repo_path 指向白盒 scan_dir）。

- [ ] **Step 1: 写失败测试（接力成功路径）**

```python
# packages/web/tests/test_combined_orchestrator.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from supernova_web.components.scan_manager import ScanManager


@pytest.fixture
def mgr(tmp_path):
    m = ScanManager.__new__(ScanManager)
    m._workspaces_dir = tmp_path
    m._temporal_address = lambda *a: "localhost:7233"
    m._resolve_provider_config = lambda *a: {}
    return m


async def test_orchestrator_chains_whitebox_to_blackbox(mgr, tmp_path):
    scan_dir = tmp_path / "repo-ts"; scan_dir.mkdir()
    (scan_dir / "deliverables" / "whitebox").mkdir(parents=True)
    # 白盒产物就绪（recon + 非空 queue）
    (scan_dir / "deliverables" / "whitebox" / "recon_deliverable.md").write_text("x")
    (scan_dir / "deliverables" / "whitebox" / "injection_exploitation_queue.json").write_text(
        '{"vulnerabilities":[{"id":1}]}')
    wb_handle = AsyncMock(); wb_handle.result = AsyncMock(return_value=None)
    bb_handle = MagicMock()
    req = MagicMock(); req.url = "http://t"
    with patch.object(mgr, "_whitebox_deliverables_ready", return_value=True), \
         patch.object(mgr, "_submit_blackbox_chained", new=AsyncMock(return_value=bb_handle)) as sb, \
         patch.object(mgr, "_emit_phase_boundary", new=AsyncMock()) as epb, \
         patch.object(mgr, "_generate_combined_report", new=AsyncMock()) as gcr, \
         patch.object(mgr, "_mark_bb", new=AsyncMock()) as mb, \
         patch("supernova_web.components.scan_manager.SessionManager") as SM:
        sm = MagicMock(); SM.return_value = sm
        bb_handle.result = AsyncMock(return_value=None)
        await mgr._combined_orchestrator(("ws", "repo-ts"), wb_handle, scan_dir, req)
        sb.assert_awaited()               # 提交黑盒
        epb.assert_awaited()              # 写 phase_boundary
        gcr.assert_awaited()              # 生成融合报告
        # bb_phase 最终 completed
        assert any(c.kwargs.get("data", {}).get("bb_phase") == "completed"
                   for c in sm.update_session.call_args_list)


async def test_orchestrator_skips_when_no_whitebox_deliverables(mgr, tmp_path):
    scan_dir = tmp_path / "repo-ts"; scan_dir.mkdir()
    wb_handle = AsyncMock(); wb_handle.result = AsyncMock(return_value=None)
    req = MagicMock(); req.url = "http://t"
    with patch.object(mgr, "_whitebox_deliverables_ready", return_value=False), \
         patch.object(mgr, "_submit_blackbox_chained", new=AsyncMock()) as sb, \
         patch.object(mgr, "_write_final_scan_end", new=AsyncMock()) as wse, \
         patch.object(mgr, "_mark_bb", new=AsyncMock()) as mb, \
         patch("supernova_web.components.scan_manager.SessionManager"):
        await mgr._combined_orchestrator(("ws", "repo-ts"), wb_handle, scan_dir, req)
        sb.assert_not_awaited()           # 不接力
        mb.assert_awaited()               # 标 skipped
        wse.assert_awaited()              # 写最终 scan_end（防 _watch 永久 tail）


async def test_orchestrator_exception_writes_scan_end(mgr, tmp_path):
    """契约：任意异常必写最终 scan_end（否则 _watch 永久 tail）。"""
    scan_dir = tmp_path / "repo-ts"; scan_dir.mkdir()
    wb_handle = AsyncMock(); wb_handle.result = AsyncMock(side_effect=RuntimeError("boom"))
    req = MagicMock(); req.url = "http://t"
    with patch.object(mgr, "_mark_bb", new=AsyncMock()), \
         patch.object(mgr, "_write_final_scan_end", new=AsyncMock()) as wse, \
         patch("supernova_web.components.scan_manager.SessionManager"):
        await mgr._combined_orchestrator(("ws", "repo-ts"), wb_handle, scan_dir, req)
        wse.assert_awaited()              # 异常也写 scan_end
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/web/tests/test_combined_orchestrator.py -v`
Expected: FAIL（`_combined_orchestrator` 等方法不存在）

- [ ] **Step 3: 实现 _combined_orchestrator（scan_manager.py）**

```python
    async def _combined_orchestrator(self, scan_key, wb_handle, scan_dir, req):
        """方案 D 接力编排：白盒完成 → 预检 → 黑盒 → 融合报告。
        契约：任何出口（成功/失败/跳过）都写最终 scan_end，防 _watch 永久 tail。"""
        ws, scan_id = scan_key
        event_file = scan_dir / "events.ndjson"
        try:
            await wb_handle.result()
            if not self._whitebox_deliverables_ready(scan_dir):
                await self._mark_bb(scan_dir, "skipped", "白盒无可利用产物")
                return
            # 展开接力参数成 scan-config.yaml（复用 _resolve_blackbox_inputs 的 dump 逻辑）
            config_path = await self._dump_chained_auth(req, scan_dir)
            bb_handle = await self._submit_blackbox_chained(scan_dir, ws, scan_id, req.url, config_path)
            await self._mark_bb(scan_dir, "running")
            await self._emit_phase_boundary(event_file, "blackbox")
            await bb_handle.result()
            await self._generate_combined_report(scan_dir)
            await self._mark_bb(scan_dir, "completed")
        except Exception as exc:
            await self._mark_bb(scan_dir, "failed", str(exc))
        finally:
            await self._write_final_scan_end(scan_dir)  # 契约：必写 scan_end
            self._orchestrator_tasks.pop(scan_key, None)
```

- [ ] **Step 4: 实现 _submit_blackbox_chained（关键：event_file/repo_path 指向白盒 scan_dir）**

```python
    async def _submit_blackbox_chained(self, wb_scan_dir, ws, scan_id, web_url, config_path):
        """接力提交黑盒——event_file/repo_path 都指向白盒 scan_dir（单目录三桶核心）。
        黑盒 scan_dir = event_file.parent = 白盒 scan_dir，产物落 deliverables/blackbox/。"""
        from supernova_blackbox.pipeline.shared import BlackboxPipelineInput
        from supernova_blackbox.pipeline.workflows import BlackboxScanWorkflow
        from supernova_core.services.temporal_infra import WEB_TASK_QUEUE_BLACKBOX
        from temporalio.client import Client
        event_file = str(wb_scan_dir / "events.ndjson")  # ← 指向白盒的 events
        repo_path = str(wb_scan_dir)                     # ← 指向白盒 scan_dir
        client = await Client.connect(self._temporal_address())
        inp = BlackboxPipelineInput(
            web_url=web_url, repo_path=repo_path,
            workspace_name=scan_id,          # 仅展示
            config_path=config_path, event_file=event_file,
            provider_config=self._resolve_provider_config(ws),
            workspaces_root=str(self._workspaces_dir), exploit=True)
        return await client.start_workflow(
            BlackboxScanWorkflow.run, inp,
            id=f"{ws}-{scan_id}-bb",         # 组合黑盒 workflow_id 后缀
            task_queue=WEB_TASK_QUEUE_BLACKBOX,
            run_timeout=workflow_run_timeout())
```

- [ ] **Step 5: 实现辅助方法（_whitebox_deliverables_ready / _mark_bb / _emit_phase_boundary / _write_final_scan_end / _dump_chained_auth）**

```python
    def _whitebox_deliverables_ready(self, wb_scan_dir) -> bool:
        """预检：recon_deliverable.md + 至少一个非空 *_exploitation_queue.json。"""
        from supernova_core.utils.paths import whitebox_dir
        dlv = whitebox_dir(wb_scan_dir / "deliverables")
        if not (dlv / "recon_deliverable.md").exists():
            return False
        return any(_queue_has_vulns(f) for f in dlv.glob("*_exploitation_queue.json"))

    async def _mark_bb(self, scan_dir, phase, reason=None):
        data = {"bb_phase": phase}
        if phase == "completed": data["status"] = "completed"
        elif phase in ("failed", "skipped"): data["status"] = phase
        if reason: data["bb_reason"] = reason
        SessionManager(scan_dir.parent).update_session(scan_dir, data)

    async def _emit_phase_boundary(self, event_file, phase):
        """写 phase_boundary 控制事件（前端分段锚点）；非 scan_end，_watch 天然忽略。"""
        payload = {"ts": _now_iso(), "category": "CONTROL",
                   "type": "phase_boundary", "phase": phase}
        async with aiofiles.open(event_file, "a") as fh:
            await fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

    async def _write_final_scan_end(self, scan_dir):
        """编排契约：写最终 scan_end 让 _watch 退出。status 取 session 当前 bb_phase。"""
        mgr = SessionManager(scan_dir.parent)
        data = mgr.get_session_data(scan_dir)
        status = data.get("bb_phase", "completed")
        await self._write_scan_end(scan_dir / "events.ndjson", status, 0,
                                   f"combined scan {status}", scan_dir=scan_dir)

    async def _dump_chained_auth(self, req, scan_dir):
        """展开 bb_auth_ref 成 scan-config.yaml（复用 _resolve_blackbox_inputs 的 dump）。"""
        # 重建子请求走现有 _dump_auth_payload 逻辑；profile 展开同 _resolve_blackbox_inputs
        _, config_path = await self._resolve_blackbox_inputs(
            req, scan_dir.parent.name, scan_dir, req.source.value if req.source else None)
        return config_path
```
`_queue_has_vulns` 已存在于 `paths.py`（Task 0 探查可见 `deliverables_reader` / `paths` 用过）——import 复用；若不存在则就地实现（读 `vulnerabilities` 列表非空）。`_now_iso`、`aiofiles`、`workflow_run_timeout` 已是 scan_manager 顶部 import。

- [ ] **Step 6: 运行测试确认通过**

Run: `uv run pytest packages/web/tests/test_combined_orchestrator.py -v`
Expected: PASS（3 个测试：成功接力 / 无产物跳过 / 异常写 scan_end）

- [ ] **Step 7: 回归（纯白盒/纯黑盒 start 不受影响）**

Run: `uv run pytest packages/web/tests/ -k "scan_manager or start or submit" -v 2>&1 | tail -25`
Expected: 现有 scan_manager 测试 PASS（组合分支仅 `req.type=="whitebox" and req.url` 进入）

- [ ] **Step 8: Commit**

```bash
git add packages/web/src/supernova_web/components/scan_manager.py packages/web/tests/test_combined_orchestrator.py
git commit -m "feat(web): _combined_orchestrator 接力编排（phase_end 模型 + 异常契约必写 scan_end）"
```

---

## Task 6: orphan_reconciler 扩展（崩溃恢复）

scan_manager 启动时，对 `combined=true` 的中断 session 按 `bb_phase` 补接力/补报告/补 scan_end。

**Files:**
- Modify: `packages/web/src/supernova_web/components/scan_manager.py`（找 `orphan_reconciler` / 启动恢复逻辑，grep `reconcil`）
- Test: `packages/web/tests/test_combined_reconcile.py`

**Interfaces:**
- Consumes: Task 5 的 `_combined_orchestrator` + `_write_final_scan_end`。

- [ ] **Step 1: 定位 orphan_reconciler**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && grep -n "reconcil\|async def.*recover\|def _on_startup\|async def start_service" packages/web/src/supernova_web/components/scan_manager.py`
确认 reconciler 方法名与调用点。

- [ ] **Step 2: 写失败测试（bb_phase=pending 且白盒已 completed → 补接力）**

```python
# packages/web/tests/test_combined_reconcile.py
import json, pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from supernova_web.components.scan_manager import ScanManager


async def test_reconcile_combined_pending_whitebox_done_rechains(tmp_path):
    scan_dir = tmp_path / "ws" / "scans" / "repo-ts"
    (scan_dir / "deliverables" / "whitebox").mkdir(parents=True)
    (scan_dir / "deliverables" / "whitebox" / "recon_deliverable.md").write_text("x")
    (scan_dir / "session.json").write_text(json.dumps(
        {"combined": True, "bb_phase": "pending", "bb_url": "http://t", "status": "running"}))
    mgr = ScanManager.__new__(ScanManager)
    mgr._workspaces_dir = tmp_path / "ws"
    with patch.object(mgr, "_temporal_address", return_value="localhost:7233"), \
         patch("supernova_web.components.scan_manager.Client") as Cli, \
         patch.object(mgr, "_submit_blackbox_chained", new=AsyncMock()) as sb, \
         patch.object(mgr, "_generate_combined_report", new=AsyncMock()):
        # 模拟白盒 workflow 已 completed
        h = AsyncMock(); h.describe = AsyncMock(return_value=MagicMock(status=MagicMock(name="COMPLETED")))
        Cli.connect = AsyncMock(return_value=MagicMock(get_workflow=MagicMock(return_value=h)))
        await mgr._reconcile_combined_scan(scan_dir)
        sb.assert_awaited()  # 补接力
```

- [ ] **Step 3: 运行确认失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/web/tests/test_combined_reconcile.py -v`
Expected: FAIL（`_reconcile_combined_scan` 不存在）

- [ ] **Step 4: 实现 _reconcile_combined_scan（scan_manager.py）**

```python
    async def _reconcile_combined_scan(self, scan_dir):
        """进程重启后兜底组合接力状态（spec §7.5）。"""
        mgr = SessionManager(scan_dir.parent)
        data = mgr.get_session_data(scan_dir)
        if not data.get("combined"):
            return
        bb_phase = data.get("bb_phase")
        ws = scan_dir.parent.name
        scan_id = scan_dir.name
        client = await Client.connect(self._temporal_address())
        if bb_phase == "pending":
            wb = client.get_workflow_handle(f"{ws}-{scan_id}")
            desc = await wb.describe()
            from temporalio.client import WorkflowExecutionStatus
            if desc.status == WorkflowExecutionStatus.COMPLETED:
                if self._whitebox_deliverables_ready(scan_dir):
                    req = self._rebuild_req_from_session(data, ws, scan_id)
                    bb = await self._submit_blackbox_chained(
                        scan_dir, ws, scan_id, data.get("bb_url"),
                        await self._dump_chained_auth(req, scan_dir))
                    await self._mark_bb(scan_dir, "running")
                    await bb.result()
                    await self._generate_combined_report(scan_dir)
                    await self._mark_bb(scan_dir, "completed")
                else:
                    await self._mark_bb(scan_dir, "skipped", "白盒无可利用产物")
                await self._write_final_scan_end(scan_dir)
        elif bb_phase == "running":
            bb = client.get_workflow_handle(f"{ws}-{scan_id}-bb")
            desc = await bb.describe()
            from temporalio.client import WorkflowExecutionStatus
            if desc.status == WorkflowExecutionStatus.COMPLETED:
                await self._generate_combined_report(scan_dir)
                await self._mark_bb(scan_dir, "completed")
                await self._write_final_scan_end(scan_dir)
        # bb_phase in {completed, failed, skipped}：无需补；若 events 无 scan_end 则补
        elif not self._has_scan_end(scan_dir / "events.ndjson"):
            await self._write_final_scan_end(scan_dir)
```
`_rebuild_req_from_session`：从 session 的 `bb_url` + `bb_auth_ref` 重建 `ScanRequest`（type=whitebox, url=bb_url, 认证字段按 bb_auth_ref 还原）。

- [ ] **Step 5: 在现有 orphan_reconciler/recovery 调用点接入**

在 scan_manager 启动恢复逻辑（Step 1 定位处）遍历中断 scan 时，对每个 scan_dir 调 `await self._reconcile_combined_scan(scan_dir)`（在现有 per-scan 恢复之后）。

- [ ] **Step 6: 运行测试确认通过**

Run: `uv run pytest packages/web/tests/test_combined_reconcile.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add packages/web/src/supernova_web/components/scan_manager.py packages/web/tests/test_combined_reconcile.py
git commit -m "feat(web): orphan_reconciler 扩展——组合接力崩溃恢复（bb_phase 分支补接力/补报告）"
```

---

## Task 7: resume / cancel 按 bb_phase 分阶段

组合扫描 resume/cancel 按 session `bb_phase` 定位对应 workflow_id。

**Files:**
- Modify: `packages/web/src/supernova_web/components/scan_manager.py:225`（resume）+ `:919`（cancel）
- Test: `packages/web/tests/test_combined_resume_cancel.py`

**Interfaces:**
- Consumes: session `combined` + `bb_phase`。

- [ ] **Step 1: 写失败测试**

```python
# packages/web/tests/test_combined_resume_cancel.py
import json, pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from supernova_web.components.scan_manager import ScanManager


def _seed(scan_dir, bb_phase):
    scan_dir.mkdir(parents=True)
    (scan_dir / "session.json").write_text(json.dumps(
        {"combined": True, "bb_phase": bb_phase, "status": "running",
         "repo_path": str(scan_dir), "web_url": "http://t"}))


async def test_resume_combined_pending_resumes_whitebox(tmp_path):
    scan_dir = tmp_path / "ws" / "scans" / "repo-ts"; _seed(scan_dir, "pending")
    mgr = ScanManager.__new__(ScanManager)
    mgr._store = MagicMock(); mgr._store.get_scan_dir.return_value = scan_dir
    mgr._workspaces_dir = tmp_path / "ws"; mgr._handles = {}; mgr._tasks = {}
    mgr._active_reqs = {}; mgr._max_concurrent = 5; mgr._scan_timeout = 0
    with patch.object(mgr, "_check_temporal", new=AsyncMock()), \
         patch.object(mgr, "_submit_whitebox", new=AsyncMock(return_value=MagicMock())) as sw, \
         patch.object(mgr, "_spawn_combined_orchestrator", new=AsyncMock()), \
         patch("supernova_web.components.scan_manager._compute_status", return_value="interrupted"), \
         patch("supernova_web.components.scan_manager.SessionManager"):
        await mgr.resume("ws", "repo-ts")
        sw.assert_awaited()  # resume 白盒


async def test_resume_combined_running_resumes_blackbox(tmp_path):
    scan_dir = tmp_path / "ws" / "scans" / "repo-ts"; _seed(scan_dir, "running")
    mgr = ScanManager.__new__(ScanManager)
    mgr._store = MagicMock(); mgr._store.get_scan_dir.return_value = scan_dir
    mgr._workspaces_dir = tmp_path / "ws"; mgr._handles = {}; mgr._tasks = {}
    mgr._active_reqs = {}; mgr._max_concurrent = 5; mgr._scan_timeout = 0
    with patch.object(mgr, "_check_temporal", new=AsyncMock()), \
         patch.object(mgr, "_submit_blackbox", new=AsyncMock(return_value=MagicMock())) as sb, \
         patch.object(mgr, "_generate_combined_report", new=AsyncMock()), \
         patch("supernova_web.components.scan_manager._compute_status", return_value="interrupted"), \
         patch("supernova_web.components.scan_manager.SessionManager"):
        await mgr.resume("ws", "repo-ts")
        sb.assert_awaited()  # resume 黑盒 + 附编排 task 仅做报告
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/web/tests/test_combined_resume_cancel.py -v`
Expected: FAIL

- [ ] **Step 3: resume 加组合分支（scan_manager.py:225 resume 内）**

读 session 后，在现有 `if scan_type == "blackbox":` 之前加：
```python
        combined = data.get("combined")
        bb_phase = data.get("bb_phase")
        if combined and bb_phase == "running":
            # 黑盒阶段中断：resume 黑盒 workflow_id {ws}-{scan_id}-bb + 附编排 task 仅做报告
            self._strip_trailing_scan_end(event_file)
            handle = await self._submit_blackbox(
                repo_path or str(scan_dir), ws, scan_id, scan_dir, event_file,
                web_url, str(scan_dir / "scan-config.yaml") if (scan_dir / "scan-config.yaml").exists() else None)
            # 附一个仅「等黑盒完成 → 生成融合报告」的编排 task（不重复 submit）
            asyncio.create_task(self._combined_orchestrator_resume_bb(scan_key, handle, scan_dir))
        elif combined and bb_phase == "pending":
            # 白盒阶段中断：resume 白盒 + 重启完整编排 task
            self._strip_trailing_scan_end(event_file)
            handle = await self._submit_whitebox(repo_path, ws, scan_id, scan_dir, event_file, web_url, combined=True)
            self._spawn_combined_orchestrator(scan_key, handle, scan_dir, self._rebuild_req_from_session(data, ws, scan_id))
        elif scan_type == "blackbox":
            ...（现有黑盒 resume 不变）
```
`_combined_orchestrator_resume_bb`：仅 `await bb_handle.result()` → `_generate_combined_report` → `_mark_bb(completed)` → `_write_final_scan_end`（不 submit，接力已发生）。

- [ ] **Step 4: cancel 加组合分支（scan_manager.py:919 cancel 内）**

读 session，按 `bb_phase` 决定 cancel 哪个 workflow_id：
```python
        data = SessionManager(scan_dir.parent).get_session_data(scan_dir) if scan_dir else {}
        if data.get("combined"):
            bb_phase = data.get("bb_phase")
            wf_id = f"{ws}-{scan_id}-bb" if bb_phase == "running" else f"{ws}-{scan_id}"
            # terminate 该 workflow_id（复用现有 cancel 的 temporal terminate 逻辑，换 wf_id）
        else:
            ...（现有 cancel 不变）
```

- [ ] **Step 5: 运行测试确认通过 + 回归**

Run: `uv run pytest packages/web/tests/test_combined_resume_cancel.py packages/web/tests/ -k "resume or cancel" -v 2>&1 | tail -25`
Expected: 新测试 PASS + 现有 resume/cancel 测试 PASS

- [ ] **Step 6: Commit**

```bash
git add packages/web/src/supernova_web/components/scan_manager.py packages/web/tests/test_combined_resume_cancel.py
git commit -m "feat(web): resume/cancel 按 bb_phase 分阶段（组合扫描）"
```

---

## Task 8: 融合报告 + combined track 识别

新增 `combined_report_renderer`（vuln_class 交叉 + 摘要），`DeliverablesReader`/`resolve_track_deliverable` 支持 `combined`。

**Files:**
- Create: `packages/web/src/supernova_web/components/combined_report_renderer.py`
- Modify: `packages/web/src/supernova_web/components/deliverables_reader.py:71`（`_infer_track` 加 combined）
- Modify: `packages/core/src/supernova_core/utils/paths.py:129`（`resolve_track_deliverable` track 含 combined）+ 加 `COMBINED_SUBDIR`
- Test: `packages/web/tests/test_combined_report_renderer.py`

**Interfaces:**
- Consumes: `deliverables/whitebox/*_exploitation_queue.json` + `deliverables/blackbox/` findings。
- Produces: `combined_report_renderer.render(scan_dir) -> Path`（写 `deliverables/combined/combined_report.md`）。

- [ ] **Step 1: 写失败测试**

```python
# packages/web/tests/test_combined_report_renderer.py
import json
from pathlib import Path
from supernova_web.components.combined_report_renderer import render_combined_report


def test_combined_report_vuln_class_cross_with_summary(tmp_path: Path):
    wb = tmp_path / "deliverables" / "whitebox"; wb.mkdir(parents=True)
    (wb / "injection_exploitation_queue.json").write_text(json.dumps(
        {"vulnerabilities": [{"id": "w1", "title": "SQLi in /login"}]}))
    (wb / "xss_exploitation_queue.json").write_text(json.dumps({"vulnerabilities": []}))
    bb = tmp_path / "deliverables" / "blackbox"; bb.mkdir()
    (bb / "injection_findings.json").write_text(json.dumps(
        {"findings": [{"id": "b1", "endpoint": "/login", "verdict": "confirmed"}]}))
    out = render_combined_report(tmp_path)
    text = out.read_text()
    assert "组合摘要" in text
    assert "injection" in text
    # vuln_class 交叉：白盒 + 黑盒并列
    assert "SQLi" in text and "/login" in text
    assert out.name == "combined_report.md"
    assert out.parent.name == "combined"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/web/tests/test_combined_report_renderer.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: paths.py 加 COMBINED_SUBDIR + combined_dir**

```python
COMBINED_SUBDIR: str = "combined"

def combined_dir(deliverables_dir: Path) -> Path:
    return deliverables_dir / COMBINED_SUBDIR
```
`resolve_track_deliverable` 的 `track` 参数注释扩为 `WHITEBOX_SUBDIR | BLACKBOX_SUBDIR | COMBINED_SUBDIR`（逻辑不变，track 已是参数）。

- [ ] **Step 4: 实现 combined_report_renderer.py**

```python
# packages/web/src/supernova_web/components/combined_report_renderer.py
"""组合扫描融合报告：按 vuln_class 交叉白盒代码证据 + 黑盒利用验证 + 顶部摘要。
对齐粒度=vuln_class（黑盒本就按类读白盒 queue）。"""
import json
from pathlib import Path
from supernova_core.utils.paths import whitebox_dir, blackbox_dir, combined_dir

_VULN_CLASSES = ["injection", "xss", "ssrf", "authz"]  # 对齐 ALL_VULN_CLASSES


def _load_queue(wb_dlv: Path, vt: str) -> list:
    f = wb_dlv / f"{vt}_exploitation_queue.json"
    if not f.exists(): return []
    try: return json.loads(f.read_text()).get("vulnerabilities", [])
    except Exception: return []


def _load_findings(bb_dlv: Path, vt: str) -> list:
    f = bb_dlv / f"{vt}_findings.json"
    if not f.exists(): return []
    try: return json.loads(f.read_text()).get("findings", [])
    except Exception: return []


def render_combined_report(scan_dir: Path) -> Path:
    dlv = scan_dir / "deliverables"
    wb, bb = whitebox_dir(dlv), blackbox_dir(dlv)
    rows = []
    body = []
    for vt in _VULN_CLASSES:
        wv, bv = _load_queue(wb, vt), _load_findings(bb, vt)
        if not wv and not bv: continue
        rows.append((vt, len(wv), len(bv)))
        body.append(f"### {vt}\n#### 白盒视角（代码证据）\n")
        body += [f"- {v.get('title', v.get('id'))} ({v.get('location','')})" for v in wv] or ["-（无）"]
        body.append("\n#### 黑盒视角（利用验证）\n")
        body += [f"- {v.get('endpoint','?')} verdict={v.get('verdict','?')}" for v in bv] or ["-（无）"]
        body.append("")
    summary = "| 漏洞类 | 白盒发现 | 黑盒验证 |\n|---|---|---|\n" + \
              "\n".join(f"| {r[0]} | {r[1]} | {r[2]} |" for r in rows)
    md = f"# 组合扫描融合报告\n\n## 组合摘要\n\n{summary}\n\n## 按漏洞类详述\n\n" + "\n".join(body)
    out_dir = combined_dir(dlv); out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "combined_report.md"
    out.write_text(md, encoding="utf-8")
    return out
```

- [ ] **Step 5: deliverables_reader._infer_track 支持 combined（deliverables_reader.py:71）**

在现有 `whitebox`/`blackbox` 推断旁加 `combined` 分支（读 `combined/combined_report.md` 存在 → track=combined）。`summary` / `read` 方法允许 `track="combined"` query（端点 `scans.py:186` 的 `track: str = "whitebox"` 已是任意 str，透传即可）。

- [ ] **Step 6: _generate_combined_report 接线（scan_manager.py，Task 5 引用的方法）**

```python
    async def _generate_combined_report(self, scan_dir):
        from .combined_report_renderer import render_combined_report
        render_combined_report(scan_dir)  # 同步 IO，包 async 入口对齐调用点
```

- [ ] **Step 7: 运行测试确认通过**

Run: `uv run pytest packages/web/tests/test_combined_report_renderer.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add packages/core/src/supernova_core/utils/paths.py packages/web/src/supernova_web/components/combined_report_renderer.py packages/web/src/supernova_web/components/deliverables_reader.py packages/web/src/supernova_web/components/scan_manager.py packages/web/tests/test_combined_report_renderer.py
git commit -m "feat(web): 融合报告 combined_report_renderer（vuln_class 交叉 + combined track）"
```

---

## Task 9: 前端——扫描页开关 + 共享认证 + buildBody

白盒扫描页加「同时发起黑盒扫描」开关，展开 URL + 认证（复用现有黑盒认证组件），buildBody 带 url+认证。

**Files:**
- Modify: `packages/web/frontend/src/pages/ScanNewPage.tsx`（开关 state + buildBody）
- Modify: `packages/web/frontend/src/components/ScanFormFields.tsx`（抽共享认证组件 + 白盒组合展开区）
- Test: `packages/web/frontend/src/components/__tests__/ScanNewPage.test.tsx`（若无则就近建）

**Interfaces:**
- Produces: 白盒 Tab 开关 ON → buildBody 输出 `{type:"whitebox", source, url, authentication/auth_profile_id}`；OFF → 纯白盒 body（回归零差异）。

- [ ] **Step 1: 写失败测试（vitest）**

```tsx
// packages/web/frontend/src/components/__tests__/ScanNewPageCombined.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ScanNewPage } from "../../pages/ScanNewPage";

describe("ScanNewPage combined toggle", () => {
  it("开关关时白盒提交 body 不含 url/认证", () => {
    // 选白盒 tab + repo + ws，提交 body 期望仅 type/source/workspace
    // （具体据 buildBody 实现：spy onSubmit，断言无 url/authentication 字段）
  });
  it("开关开时展开 URL+认证，body 含 url", () => {
    // 打开开关 → URL 输入出现 → 填值 → 提交 body 含 url
  });
});
```
> 测试细节据 ScanNewPage 现有受控 state 写法（参考 `ScanNewPage.tsx:228 setType` / `:162 buildBody`）。先读这两个函数确认 prop/state 结构再补全断言。

- [ ] **Step 2: 运行确认失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py/packages/web/frontend && ./node_modules/.bin/vitest run src/components/__tests__/ScanNewPageCombined.test.tsx`
Expected: FAIL

- [ ] **Step 3: 抽共享认证组件（ScanFormFields.tsx）**

把黑盒分支的认证 JSX（`RightAuthCore` `:126` / `BottomInlineBlock` `:326` / `BottomProfileBlock` `:385`）抽成 `<AuthFields value onChange />` 共享组件，白盒组合展开区与黑盒分支都复用。

- [ ] **Step 4: ScanNewPage 加开关 + 展开区 + buildBody 组合分支**

```tsx
// ScanNewPage.tsx 白盒分支
const [combined, setCombined] = useState(false);
// JSX：白盒表单下方加 <Switch checked={combined} onChange={setCombined}>同时发起黑盒扫描</Switch>
// {combined && <AuthFields .../> + <URLInput .../>}
// buildBody（:162）whitebox 分支：
if (type === "whitebox") {
  const body = { type, source: {...}, workspace };
  if (combined && url) { body.url = url; Object.assign(body, authPayload()); }
  return body;
}
```

- [ ] **Step 5: 运行测试确认通过 + 回归（开关关=纯白盒 body）**

Run: `cd packages/web/frontend && ./node_modules/.bin/vitest run src/components/__tests__/ScanNewPageCombined.test.tsx src/pages 2>&1 | tail -25`
Expected: PASS + 现有扫描页测试 PASS

- [ ] **Step 6: Commit**

```bash
git add packages/web/frontend/src/pages/ScanNewPage.tsx packages/web/frontend/src/components/ScanFormFields.tsx packages/web/frontend/src/components/__tests__/ScanNewPageCombined.test.tsx
git commit -m "feat(web): 扫描页组合开关 + 共享认证组件 + buildBody 组合分支"
```

---

## Task 10: 前端——详情页两段时间线（phase_boundary 切分）

白盒详情页 `combined==true` 时，单 events 流按 `phase_boundary`/`phase_end` 切两段渲染。

**Files:**
- Modify: `packages/web/frontend/src/routes/WorkspaceDetail/ScanDetail.tsx:29`
- Modify: events 解析 util（`utils/eventTs` 或 `LiveTab` 的事件 reducer）
- Test: `packages/web/frontend/src/routes/WorkspaceDetail/__tests__/ScanDetail.test.tsx`

**Interfaces:**
- Consumes: events.ndjson 的 `phase_end`/`phase_boundary` 行。

- [ ] **Step 1: 写失败测试**——combined scan 的 events 含 phase_end → 详情页渲染两段（白盒✓ / 黑盒进行中）。读 `ScanDetail.tsx` + live events reducer 确认事件流入口后写断言。
- [ ] **Step 2: 运行确认失败**——`cd packages/web/frontend && ./node_modules/.bin/vitest run ...` FAIL
- [ ] **Step 3: ScanDetail 检测 combined**——`GET /{ws}/scans/{id}` 详情返回 session `combined` 字段（`scans.py:_scan_detail` 透传）；ScanDetail 据 `combined` 渲染两段时间线。
- [ ] **Step 4: events 切分**——事件 reducer 遇 `type==="phase_end"||"phase_boundary"` 标记阶段边界，前端分段着色 + 阶段标签。
- [ ] **Step 5: 运行通过 + 回归**——纯白盒（无 phase_end）单段渲染不变。
- [ ] **Step 6: Commit**——`feat(web): 详情页组合扫描两段时间线（phase_end/boundary 切分）`

---

## Task 11: 前端——报告三子 tab + 产物三桶

报告 tab `combined==true` 时三子 tab（白盒/黑盒/融合）；产物 tab 三桶。

**Files:**
- Modify: `packages/web/frontend/src/routes/WorkspaceDetail/ReportTab.tsx:10`
- Modify: `packages/web/frontend/src/routes/WorkspaceDetail/DeliverablesTab.tsx:14`
- Test: `packages/web/frontend/src/routes/WorkspaceDetail/__tests__/ReportTabCombined.test.tsx`

- [ ] **Step 1: 写失败测试**——combined scan 报告 tab 渲染三个子 tab，融合 tab 拉 `?track=combined` 报告。
- [ ] **Step 2: 运行确认失败**——vitest FAIL
- [ ] **Step 3: ReportTab 三子 tab**——`combined` 时渲染 `[白盒|黑盒|融合]` 子 tab，各拉 `scanReportPath(ws,id,track)`（track=whitebox/blackbox/combined）。
- [ ] **Step 4: DeliverablesTab 三桶**——`summary` 返回的 deliverables 含 `combined` track 项（Task 8 已让 reader 识别），按桶分组展示。
- [ ] **Step 5: 运行通过 + 回归**——非 combined scan 报告 tab 单视图不变。
- [ ] **Step 6: Commit**——`feat(web): 报告三子 tab + 产物三桶（combined track）`

---

## Task 12: 端到端冒烟 + worker rebuild

**Files:**
- Test: 手动 / 容器冒烟（无自动化）

- [ ] **Step 1: rebuild worker 镜像**（改了 core/whitebox/web src）

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && docker compose build supernova-worker`（或项目对应命令；确认 core/whitebox/web 包都打进镜像）
- [ ] **Step 2: 跑一个真实小仓组合扫描**——前端白盒开关 ON，填 repo + URL + 认证，提交。观察：白盒阶段进度 → 自动接力黑盒 → 三报告齐。
- [ ] **Step 3: 验证单目录三桶**——`ls <ws>/scans/<id>/deliverables/` 含 `whitebox/ blackbox/ combined/`；只有一条 scan 记录。
- [ ] **Step 4: 回归冒烟**——纯白盒（开关关）跑同仓：零回归（仅 whitebox/，行为同今天）；纯黑盒入口对已有白盒追加：独立 scan_dir 不变。
- [ ] **Step 5: 失败路径冒烟**——白盒跑出 0 产物（空仓）→ 黑盒 skipped + 标注「白盒无可利用产物」+ scan_end 正常收尾（_watch 不假死）。
- [ ] **Step 6: Commit 冒烟记录**——把冒烟结果记到 plan 末尾或 PR 描述。

---

## Self-Review

**1. Spec 覆盖**：§3 不变量（Task 0/2 守白盒回归 + scan_end 不变量 Task 1）；§4 单目录三桶（Task 4/5/8 + Task 12 验证）；§5 接力机制（Task 5 `_submit_blackbox_chained`）；§6 数据模型（Task 3 validator + Task 4 session 字段）；§7 接力时序（Task 1 phase_end + Task 5 orchestrator + Task 6 reconciler）；§8 入口（Task 9）；§9 进度（Task 10）；§10 报告三视图（Task 8/11）；§11 失败/resume（Task 5 异常契约 + Task 7 resume/cancel）；§12 测试矩阵（每任务 TDD）；§13 风险（Task 0 验证 + Task 1/2 回归守卫 + Task 5 异常契约 + Task 6 reconciler）。**无遗漏**。

**2. 占位扫描**：Task 9-11 前端测试因需读受控 state 结构，Step 1 写了测试骨架 + 「先读 X 再补断言」指引（非 placeholder——明确要读的行号 + 断言意图）。其余任务代码均实际。

**3. 类型一致性**：`combined: bool`（PipelineInput/ActivityInput 一致，Task 2/4）；`bb_phase` 取值 `{pending,running,completed,failed,skipped}`（Task 4/5/6/7 一致）；`_combined_orchestrator` / `_submit_blackbox_chained` / `_write_final_scan_end` / `_mark_bb` / `_emit_phase_boundary` / `_generate_combined_report` 跨 Task 4-8 名称一致；workflow_id `{ws}-{scan_id}-bb`（Task 5/6/7 一致）；`PhaseEndEvent(phase,status)`（Task 1 定义，Task 2 间接经 log_phase_complete 用）。

**关键依赖顺序**：Task 0 → 1 → 2 →（3 ‖ 4）→ 5 →（6 ‖ 7 ‖ 8）→（9 ‖ 10 ‖ 11）→ 12。Task 1/2 是后端前置；Task 5 依赖 1/2/4；前端 9-11 依赖后端 3-8 的接口。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-13-combined-wb-bb-scan.md`. Two execution options:

**1. Subagent-Driven (recommended)** — 我每个任务派一个全新 subagent，任务间 review，快速迭代（适合这种 12 任务的大计划，隔离上下文）。

**2. Inline Execution** — 在当前 session 用 executing-plans 批量执行，检查点 review。

**哪种？**（选 1 我会从 Task 0 开始，逐任务 subagent + 两阶段 review）
