# 白盒+黑盒一键组合扫描 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 白盒扫描页加开关，一键发起「t0 预验证 → 白盒 → 黑盒自动接力」组合扫描，单目录三桶产物，列表层分段进度（收起 x% / 展开步级），三视图报告，黑盒失败可换认证续跑。

**Architecture:** 方案 D（PhaseEvent + 独立编排 task + _watch 纯 tail + 幂等 scan_end）。单目录靠接力时把黑盒 `event_file`/`repo_path` 指向白盒 scan_dir。接力编排由 scan_manager 的 `_combined_orchestrator` 驱动；公共段抽 `_run_blackbox_phase`（编排 + 续跑共用）；复用现有 `_submit_blackbox`（加 `workflow_id_suffix`）；白盒 `finalize_summary` 组合模式调**现有** `log_phase_complete` 写 `PhaseEvent`（不新增事件类型）；`_ensure_scan_end` 幂等收尾（成功路径黑盒已写 scan_end 不重复写）；`_watch` 回归纯 tail。

**Tech Stack:** Python 3.11 / temporalio / pydantic / pytest（后端）；React + TypeScript + TanStack + vitest（前端）。双引擎（claude-agent-sdk / openai-agents）流程一致。

**Spec:** `docs/superpowers/specs/2026-08-12-combined-wb-bb-scan-design.md`（定稿，含 D1-D5 + G1/进度）

## Global Constraints

- **纯白盒/纯黑盒零回归**：开关关 / 黑盒独立入口行为一字不改，每个相关任务带回归测试。
- **scan_end 语义不变量**：全场景 events 只有一个 `scan_end` = 真结束。组合模式阶段边界用现有 `PhaseEvent`（不新增事件）；编排收尾用 `_ensure_scan_end`（幂等，先 `_has_scan_end`）。
- **黑盒 workflow 零代码改动**：单目录仅靠接力时控制 `event_file`/`repo_path`；复用 `_submit_blackbox`（加 suffix），不手写 `_submit_blackbox_chained`。
- **认证明文不进 session.json**：认证快照 = `scan-config.yaml`（t0 dump）；`bb_auth_ref` 只存 `profile_id`。
- **双引擎一致**：流程层抽象，业务侧不感知 claude/openai。
- **预存陷阱**：前端命令 `cd packages/web/frontend`（cwd 不持久），用 `./node_modules/.bin/vitest|tsc|vite`（别用 `pnpm test`）；改 web/worker src 须 rebuild `supernova-worker` 镜像（`uv sync --all-packages`）；pytest 只跑改动相关子集。
- **路径基准**：`/root/shannon-py`（Linux）。
- **计费不变量**：`cost_usd` 字段名留（值=cost_currency 金额），组合扫描 cost = 白盒 + 黑盒各自 metrics 累积，不重复计。

## File Structure

**core（无新增事件，复用 PhaseEvent）**——本计划不改 core 事件层（D1：`PhaseEvent`/`log_phase_complete` 已存在且够用）。

**whitebox（finalize 组合分支）**
- `packages/whitebox/src/supernova_whitebox/pipeline/shared.py` — `PipelineInput` + `ActivityInput` 加 `combined: bool = False`
- `packages/whitebox/src/supernova_whitebox/pipeline/activities.py` — `finalize_summary` 组合分支调现有 `log_phase_complete`
- `packages/whitebox/src/supernova_whitebox/pipeline/workflows.py` — 构造 finalize 的 ActivityInput 透传 `combined`

**web（编排 + 数据模型 + 透传）**
- `packages/web/src/supernova_web/models.py` — `_whitebox_combined_optional` validator
- `packages/web/src/supernova_web/components/scan_manager.py` — start 组合分支（t0 预验证 + session 字段）；`_submit_blackbox` 加 `workflow_id_suffix`；新增 `_combined_orchestrator`/`_run_blackbox_phase`/`_ensure_scan_end`/`_compute_expected_agents`/`_snapshot_auth_ref`/`_rerun_blackbox`；扩展 `orphan_reconciler`/`resume`/`cancel`
- `packages/web/src/supernova_web/components/scan_store.py` — `ScanSummary` + `as_dict` 加 `combined`/`bb_phase`/`progress_pct`
- `packages/web/src/supernova_web/api/scans.py` — `_scan_detail` 白名单加组合字段；新增 `rerun-blackbox` 端点
- `packages/web/src/supernova_web/components/combined_report_renderer.py` — **新增**，融合报告
- `packages/web/src/supernova_web/components/deliverables_reader.py` — `_infer_track` + `resolve_track_deliverable` 支持 `combined`
- `packages/core/src/supernova_core/utils/paths.py` — `COMBINED_SUBDIR` + `combined_dir`

**前端**
- `packages/web/frontend/src/pages/ScanNewPage.tsx` — 开关 + 预验证态 + buildBody
- `packages/web/frontend/src/components/ScanFormFields.tsx` — 抽共享认证组件 `<AuthFields>` + 共享 HOST 组件 `<HostFields>`
- `packages/web/frontend/src/routes/WorkspaceDetail/ScanList.tsx` — 卡片收起% / 展开步级
- `packages/web/frontend/src/routes/WorkspaceDetail/ScanDetail.tsx` — 两段时间线
- `packages/web/frontend/src/routes/WorkspaceDetail/ReportTab.tsx` — 三子 tab
- `packages/web/frontend/src/routes/WorkspaceDetail/DeliverablesTab.tsx` — 三桶

---

## Task 0: 验证关键假设（复用前提 + 路径基准）

锁死方案依赖的几个现状事实，后续任务据此放心。

**Files:** Test: `packages/web/tests/test_combined_assumptions.py`

- [ ] **Step 1: 写探查测试**

```python
# packages/web/tests/test_combined_assumptions.py
"""锁死组合扫描依赖的现状不变量。"""
from pathlib import Path


def test_blackbox_web_path_uses_event_file_parent():
    """黑盒 web 路径：workspace_path = event_file.parent（组合接力把黑盒 event_file
    指向白盒 scan_dir → 黑盒产物自动落白盒目录/deliverables/blackbox/）。"""
    wf = Path("packages/blackbox/src/supernova_blackbox/pipeline/workflows.py").read_text()
    assert "Path(input.event_file).parent" in wf


def test_phase_event_and_log_phase_complete_already_exist():
    """D1：PhaseEvent + log_phase_complete(phase) 已存在，组合模式直接复用，不新增事件类型。
    _serialize 通用路径写 type=类名 → PhaseEvent 写出 type:'PhaseEvent'（≠scan_end）。"""
    import supernova_core.display.events as ev
    import supernova_core.audit.session as sess
    assert hasattr(ev, "PhaseEvent")
    assert hasattr(sess.AuditSession, "log_phase_complete")  # 签名 (phase: str)，不覆盖


def test_has_scan_end_only_matches_scan_end():
    """_ensure_scan_end 幂等前提：_has_scan_end 只认 type=='scan_end'，PhaseEvent 天然忽略。"""
    sm = Path("packages/web/src/supernova_web/components/scan_manager.py").read_text()
    assert '"scan_end"' in sm and "_has_scan_end" in sm


def test_completed_agents_in_session_toplevel():
    """进度分母分子：completed_agents 在 session.json 顶层（list_scans 可读）。"""
    s = Path("packages/core/src/supernova_core/session.py").read_text()
    assert "completed_agents" in s and "mark_agent_completed" in s


def test_auth_validation_workflow_exists_for_precheck():
    """D4 t0 预验证复用 AuthValidationWorkflow（独立 auth 段，不依赖白盒产物）。"""
    wf = Path("packages/blackbox/src/supernova_blackbox/pipeline/workflows.py").read_text()
    assert "class AuthValidationWorkflow" in wf
```

- [ ] **Step 2: 运行确认通过**

Run: `cd /root/shannon-py && uv run pytest packages/web/tests/test_combined_assumptions.py -v`
Expected: PASS（锁死现状；若 FAIL 说明假设破裂，回 spec 调整）

- [ ] **Step 3: Commit** — `test(combined): 锁死组合扫描依赖的现状不变量`

---

## Task 1: ScanRequest validator + session 字段 + 后端透传（G1）+ expected_agents

组合请求校验 + session 字段定义 + 后端接口透传组合/进度字段（前端前置）。

**Files:**
- Modify: `packages/web/src/supernova_web/models.py`（`_whitebox_combined_optional`）
- Modify: `packages/web/src/supernova_web/components/scan_store.py`（`ScanSummary` + `as_dict` 加组合字段 + `progress_pct`）
- Modify: `packages/web/src/supernova_web/api/scans.py`（`_scan_detail` 白名单加组合字段）
- Modify: `packages/web/src/supernova_web/components/scan_manager.py`（`_snapshot_auth_ref` + `_compute_expected_agents`）
- Test: `packages/web/tests/test_scan_request_combined.py`, `packages/web/tests/test_combined_session_fields.py`

- [ ] **Step 1: 写失败测试（validator）** — `test_whitebox_with_url_is_combined`（带 url 合法）/ `test_whitebox_without_url_rejects_auth`（纯白盒禁认证）/ `test_whitebox_combined_auth_xor_enforced`（profile+inline 互斥）。参考 spec §6.1。
- [ ] **Step 2: 运行确认失败** — `uv run pytest packages/web/tests/test_scan_request_combined.py -v` FAIL
- [ ] **Step 3: 抽共享认证校验 + 加 validator** — 把 `_auth_profile_xor_inline` 的 blackbox 分支校验体抽成 `_validate_auth_fields(self)`，blackbox validator 和新 `_whitebox_combined_optional` 都调。`_whitebox_combined_optional`：`type=="whitebox"` 且 `url` → 组合（调 `_validate_auth_fields`）；无 url 且有认证字段 → raise。
- [ ] **Step 4: 写失败测试（session 字段 + 透传）** — 断言 `_snapshot_auth_ref` inline 模式返回 `{"profile_id": None}`（**不存明文**），profile 模式返回 `{"profile_id": ..., "cred_id":..., "cred_ids":...}`；断言 `_compute_expected_agents(req)` 返回 `{"whitebox": N}`；断言 `ScanSummary.as_dict()` 含 `combined`/`bb_phase`/`progress_pct`；断言 `_scan_detail` payload 含这些字段。
- [ ] **Step 5: 实现 `_snapshot_auth_ref`（D2，只存 profile_id）**

```python
def _snapshot_auth_ref(self, req: ScanRequest) -> dict:
    """认证明文不进 session.json（D2）。只存 profile_id（非敏感引用）；
    inline 模式存 None——认证明文已在 scan-config.yaml（t0 dump），不重复存。"""
    if req.auth_profile_id:
        return {"profile_id": req.auth_profile_id,
                "cred_id": req.auth_credential_id,
                "cred_ids": req.auth_credential_ids}
    return {"profile_id": None}  # inline 认证在 scan-config.yaml
```

- [ ] **Step 6: 实现 `_compute_expected_agents`（进度分母，spec §9.5）** — 按 req 的 vuln 类 + `SUPERNOVA_LLM_TRACK_ENABLED`（双轨系数）+ `AGENT_DEFINITIONS` 枚举白盒预期 agent 数。返回 `{"whitebox": N}`（blackbox 部分在黑盒 submit 时补，见 Task 4）。够准即可（收起态精度门槛低）。
- [ ] **Step 7: ScanSummary + as_dict + _scan_detail 透传** — `ScanSummary` 加 `combined`/`bb_phase`/`bb_reason`/`progress_pct` 字段（从 session.json 读）；`as_dict` 输出；`_scan_detail` 白名单加这些字段 + `expected_agents`/`completed_agents`。`progress_pct` 在 list_scans 构建（按 spec §9.2 三阶段加权算）。
- [ ] **Step 8: 运行通过 + 黑盒回归** — `uv run pytest packages/web/tests/test_scan_request_combined.py packages/web/tests/test_combined_session_fields.py packages/web/tests/ -k "scan_request or blackbox or auth" -v`
- [ ] **Step 9: Commit** — `feat(web): ScanRequest 组合 validator + session 字段 + 后端透传 combined/progress_pct`

---

## Task 2: 白盒 finalize 组合分支调现有 log_phase_complete（D1，不新增事件）

白盒 finalize 组合模式调现有 `log_phase_complete("whitebox")`（写 PhaseEvent，非 scan_end），纯白盒不变。**不新增 PhaseEndEvent / 不覆盖 log_phase_complete 签名。**

**Files:**
- Modify: `packages/whitebox/src/supernova_whitebox/pipeline/shared.py`（PipelineInput + ActivityInput 加 `combined`）
- Modify: `packages/whitebox/src/supernova_whitebox/pipeline/workflows.py`（构造 finalize ActivityInput 透传 `combined`）
- Modify: `packages/whitebox/src/supernova_whitebox/pipeline/activities.py`（`finalize_summary` 组合分支）
- Test: `packages/whitebox/tests/test_finalize_combined_phase_event.py`

- [ ] **Step 1: 写失败测试** — mock `get_audit_session` 返回非 NullAuditSession 的 MagicMock（绕 isinstance，补全 `ensure_audit_session`/`stop_heartbeat`/`clear_audit_session` mock，见 Global 预存陷阱）。`combined=True` → 断言 `session.log_phase_complete.assert_awaited` 且 `log_workflow_complete.assert_not_awaited`；`combined=False` → 反之。
- [ ] **Step 2: 运行确认失败** — `uv run pytest packages/whitebox/tests/test_finalize_combined_phase_event.py -v` FAIL
- [ ] **Step 3: ActivityInput + PipelineInput 加 `combined: bool = False`**（shared.py）。
- [ ] **Step 4: workflows.py 构造 finalize 的 ActivityInput 处透传 `combined=input.combined`**（grep `finalize_summary` 调用点）。
- [ ] **Step 5: finalize_summary 组合分支（activities.py）**

```python
# 现有：await session.log_workflow_complete(ws)
# 改为：
if input.combined:
    # D1：组合扫描白盒阶段——调现有 log_phase_complete（写 PhaseEvent，非 scan_end），不写终态 status
    await session.log_phase_complete("whitebox")
else:
    await session.log_workflow_complete(ws)
```

- [ ] **Step 6: 运行通过 + 纯白盒回归** — `uv run pytest packages/whitebox/tests/test_finalize_combined_phase_event.py packages/whitebox/tests/ -k "finalize" -v`
- [ ] **Step 7: Commit** — `feat(whitebox): finalize 组合分支调现有 log_phase_complete（纯白盒零回归）`

---

## Task 3: t0 认证预验证（D4）

提交时先用 scan-config.yaml 登一次目标站（复用 `AuthValidationWorkflow`），pass 才 submit 白盒，fail fail-fast。

**Files:**
- Modify: `packages/web/src/supernova_web/components/scan_manager.py`（start 组合分支插预验证链 + `_run_precheck`）
- Test: `packages/web/tests/test_combined_precheck.py`

- [ ] **Step 1: 写失败测试** — mock `AuthValidationWorkflow`：pass → 断言后续 `_submit_whitebox` 被调；fail（AuthValidationResult 失败）→ 断言 `_submit_whitebox` 未调、session `bb_phase=failed`/`bb_reason=auth_failed`。
- [ ] **Step 2: 运行确认失败** — `uv run pytest packages/web/tests/test_combined_precheck.py -v` FAIL
- [ ] **Step 3: 实现 `_run_precheck`**

```python
async def _run_precheck(self, scan_dir, ws, scan_id, web_url, config_path) -> bool:
    """D4 t0 预验证：复用 AuthValidationWorkflow 登一次，pass 返 True。
    event_file 用独立文件（不写主 events——预验证 workflow finalize 可能写 scan_end，
    混入主 events 会提前触发 _watch 退出）。"""
    from supernova_blackbox.pipeline.workflows import AuthValidationWorkflow
    from supernova_blackbox.pipeline.shared import BlackboxAuthValidationInput
    from supernova_core.services.temporal_infra import WEB_TASK_QUEUE_BLACKBOX
    client = await Client.connect(self._temporal_address())
    inp = BlackboxAuthValidationInput(
        web_url=web_url, config_path=config_path,
        workspace_path=str(scan_dir),
        event_file=str(scan_dir / "authcheck-events.ndjson"),  # 独立 events
        api_key=self._resolve_provider_config(ws).get("api_key"))
    handle = await client.start_workflow(
        AuthValidationWorkflow.run, inp,
        id=f"{ws}-{scan_id}-authcheck", task_queue=WEB_TASK_QUEUE_BLACKBOX)
    result = await handle.result()  # AuthValidationResult
    return bool(result and getattr(result, "success", False))
```

- [ ] **Step 4: start 组合分支串预验证**（在 `_submit_whitebox` 之前）：create_scan → `_resolve_blackbox_inputs`/dump scan-config.yaml → `_run_precheck` → pass 继续 / fail `self._mark_bb(scan_dir,"failed","auth_failed")` + `_ensure_scan_end` + return。预验证用独立 events，主 events 流从白盒段开始。
- [ ] **Step 5: 运行通过** — `uv run pytest packages/web/tests/test_combined_precheck.py -v`
- [ ] **Step 6: Commit** — `feat(web): t0 认证预验证（复用 AuthValidationWorkflow，fail-fast）`

---

## Task 4: scan_manager 接力编排（_combined_orchestrator + _run_blackbox_phase + 幂等 scan_end + 复用 _submit_blackbox）

核心接力：`_combined_orchestrator` await 白盒 → `_run_blackbox_phase`（预检 + 复用 `_submit_blackbox` + await 黑盒 + 融合报告）；`_ensure_scan_end` 幂等收尾。

**Files:**
- Modify: `packages/web/src/supernova_web/components/scan_manager.py`（`_submit_blackbox` 加 `workflow_id_suffix`；新增 `_combined_orchestrator`/`_run_blackbox_phase`/`_ensure_scan_end`/`_whitebox_deliverables_ready`/`_mark_bb`/`_generate_combined_report`）
- Test: `packages/web/tests/test_combined_orchestrator.py`

- [ ] **Step 1: 写失败测试（接力成功路径 + 幂等 scan_end 核心守卫）**

```python
async def test_orchestrator_success_does_not_write_second_scan_end(mgr, tmp_path):
    """核心守卫（修原 bug）：成功路径黑盒 finalize 已写 scan_end，
    _ensure_scan_end 必须 no-op（不写第二条）。"""
    scan_dir = tmp_path / "repo-ts"; scan_dir.mkdir()
    (scan_dir / "deliverables" / "whitebox").mkdir(parents=True)
    (scan_dir / "deliverables" / "whitebox" / "recon_deliverable.md").write_text("x")
    (scan_dir / "deliverables" / "whitebox" / "injection_exploitation_queue.json").write_text(
        '{"vulnerabilities":[{"id":1}]}')
    (scan_dir / "events.ndjson").write_text('{"type":"scan_end","status":"completed"}\n')  # 黑盒已写
    wb_handle = AsyncMock(); wb_handle.result = AsyncMock(return_value=None)
    bb_handle = MagicMock(); bb_handle.result = AsyncMock(return_value=None)
    with patch.object(mgr, "_submit_blackbox", new=AsyncMock(return_value=bb_handle)) as sb, \
         patch.object(mgr, "_generate_combined_report", new=AsyncMock()), \
         patch.object(mgr, "_write_scan_end", new=AsyncMock()) as ws_end, \
         patch.object(mgr, "_mark_bb", new=AsyncMock()):
        await mgr._run_blackbox_phase(scan_dir, "ws", "repo-ts", {"profile_id": None})
        sb.assert_awaited()                      # 提交黑盒（带 -bb suffix）
        assert sb.call_args.kwargs.get("workflow_id_suffix") == "-bb"
        ws_end.assert_not_awaited()              # 关键：scan_end 已在，不重复写
```

另加：`test_orchestrator_skips_when_no_deliverables`（无产物 → skipped + `_ensure_scan_end` 补写）；`test_orchestrator_exception_ensures_scan_end`（异常 → `_ensure_scan_end` 补写）。

- [ ] **Step 2: 运行确认失败** — `uv run pytest packages/web/tests/test_combined_orchestrator.py -v` FAIL
- [ ] **Step 3: `_submit_blackbox` 加 `workflow_id_suffix`**（复用，不手写 chained 版）

```python
async def _submit_blackbox(self, repo_path, ws, scan_id, scan_dir, event_file,
                           web_url, config_path, host_mappings=None,
                           workflow_id_suffix: str = "") -> Any:
    ...
    workflow_id = self._resolve_workflow_id(ws, scan_id) + workflow_id_suffix
    # 其余 BlackboxPipelineInput 字段（含 host_mappings）原样——零功能遗漏
    ...
```

- [ ] **Step 4: 实现 `_ensure_scan_end`（幂等，spec §7.4）**

```python
async def _ensure_scan_end(self, scan_dir, status="completed"):
    """幂等收尾：events 无 scan_end 才补写。成功路径黑盒已写→no-op；
    异常/跳过/提交失败→补写防 _watch 永久 tail。"""
    if self._has_scan_end(scan_dir / "events.ndjson"):
        return
    await self._write_scan_end(scan_dir / "events.ndjson", status, 0, f"combined {status}", scan_dir=scan_dir)
```

- [ ] **Step 5: 实现 `_run_blackbox_phase`（spec §7.3）** — 预检 `_whitebox_deliverables_ready`（recon_deliverable.md + 非空 queue）→ 不全 `_mark_bb(skipped)` + return；OK → 复用 `_submit_blackbox(workflow_id_suffix="-bb", repo_path=str(scan_dir), event_file=scan_dir/"events.ndjson", config_path=scan_dir/"scan-config.yaml")` + 补 `expected_agents.blackbox`（按 queue 发现的 vuln 类）→ `_mark_bb(running)` → `await bb_handle.result()` → `_generate_combined_report` → `_mark_bb(completed)`。ws 用 `scan_key[0]`（修 #4，别 `scan_dir.parent.name`）。
- [ ] **Step 6: 实现 `_combined_orchestrator`（spec §7.2）**

```python
async def _combined_orchestrator(self, scan_key, wb_handle, scan_dir, req):
    ws, scan_id = scan_key
    try:
        await wb_handle.result()
        await self._run_blackbox_phase(scan_dir, ws, scan_id, self._snapshot_auth_ref(req))
    except Exception as exc:
        await self._mark_bb(scan_dir, "failed", str(exc))
    finally:
        await self._ensure_scan_end(scan_dir)   # 幂等：成功 no-op，异常补写
        self._orchestrator_tasks.pop(scan_key, None)
```

- [ ] **Step 7: start 末尾起编排 task**（`req.type=="whitebox" and req.url` → `asyncio.create_task(self._combined_orchestrator(scan_key, handle, scan_dir, req))`，登记 `_orchestrator_tasks`）。
- [ ] **Step 8: 运行通过 + 回归** — `uv run pytest packages/web/tests/test_combined_orchestrator.py packages/web/tests/ -k "scan_manager or submit" -v`
- [ ] **Step 9: Commit** — `feat(web): _combined_orchestrator 接力编排（复用 _submit_blackbox + 幂等 scan_end）`

---

## Task 5: orphan_reconciler 扩展（崩溃恢复）

进程重启后，对 `combined=true` 的中断 session 按 `bb_phase` 补接力/补报告/补 scan_end。

**Files:**
- Modify: `packages/web/src/supernova_web/components/scan_manager.py`（`_reconcile_combined_scan`）+ `packages/web/src/supernova_web/components/orphan_reconciler.py`（接入调用点）
- Test: `packages/web/tests/test_combined_reconcile.py`

- [ ] **Step 1: 定位 reconciler** — `grep -n "reconcil\|async def.*recover\|startup" packages/web/src/supernova_web/components/scan_manager.py packages/web/src/supernova_web/components/orphan_reconciler.py`
- [ ] **Step 2: 写失败测试** — seed session `{combined:True, bb_phase:"pending", status:"running"}` + 白盒产物在 + mock 白盒 workflow describe COMPLETED → 断言 `_run_blackbox_phase` 被调（补接力）。另：`bb_phase:"running"` + 黑盒 workflow COMPLETED → 断言 `_generate_combined_report` 被调。
- [ ] **Step 3: 运行确认失败** — `uv run pytest packages/web/tests/test_combined_reconcile.py -v` FAIL
- [ ] **Step 4: 实现 `_reconcile_combined_scan`（spec §7.5）** — 读 session；非 combined return；按 `bb_phase` 分支：precheck/pending/running 查对应 workflow（`{ws}-{scan_id}` / `{ws}-{scan_id}-bb` / `-bb-rerun-N`）status，completed 则补下一步（预验证/接力/报告），running 则重建 handle 续跑；任意状态 events 无 scan_end → `_ensure_scan_end`。
- [ ] **Step 5: 接入 orphan_reconciler**（per-scan 恢复后调 `await self._reconcile_combined_scan(scan_dir)`）。
- [ ] **Step 6: 运行通过** — `uv run pytest packages/web/tests/test_combined_reconcile.py -v`
- [ ] **Step 7: Commit** — `feat(web): orphan_reconciler 扩展——组合接力崩溃恢复`

---

## Task 6: resume / cancel 按 bb_phase + bb_rerun_attempts 分阶段

组合扫描 resume/cancel 按 session `bb_phase` + `bb_rerun_attempts` 定位对应 workflow_id。

**Files:**
- Modify: `packages/web/src/supernova_web/components/scan_manager.py`（resume + cancel）
- Test: `packages/web/tests/test_combined_resume_cancel.py`

- [ ] **Step 1: 写失败测试** — `bb_phase=pending` → resume 白盒 `{ws}-{scan_id}` + 重启编排 task；`bb_phase=running` + `bb_rerun_attempts=0` → resume 黑盒 `{ws}-{scan_id}-bb`；`bb_rerun_attempts=2` → resume `{ws}-{scan_id}-bb-rerun-2`。cancel 同理按 bb_phase/attempt 定位 workflow_id。
- [ ] **Step 2: 运行确认失败** — `uv run pytest packages/web/tests/test_combined_resume_cancel.py -v` FAIL
- [ ] **Step 3: resume 加组合分支（spec §11.4）** — 读 session `combined`/`bb_phase`/`bb_rerun_attempts`：running → resume 黑盒 workflow_id `{ws}-{scan_id}-bb` + （若 rerun）`-bb-rerun-{N}` + 附仅做报告的编排 task；pending → resume 白盒 + 重启完整编排 task。resume 前 `_strip_trailing_scan_end`。
- [ ] **Step 4: cancel 加组合分支（spec §11.5）** — 按 `bb_phase`/`bb_rerun_attempts` 算 workflow_id terminate。
- [ ] **Step 5: 运行通过 + 回归** — `uv run pytest packages/web/tests/test_combined_resume_cancel.py packages/web/tests/ -k "resume or cancel" -v`
- [ ] **Step 6: Commit** — `feat(web): resume/cancel 按 bb_phase + bb_rerun_attempts 分阶段`

---

## Task 7: 黑盒续跑（D5，可多次换认证）

黑盒 failed 后换认证续跑，复用白盒产物，起新黑盒 workflow `-bb-rerun-N`。

**Files:**
- Modify: `packages/web/src/supernova_web/api/scans.py`（`POST /{ws}/scans/{id}/combined/rerun-blackbox`）
- Modify: `packages/web/src/supernova_web/components/scan_manager.py`（`rerun_blackbox`）
- Test: `packages/web/tests/test_combined_rerun.py`

- [ ] **Step 1: 写失败测试** — seed `bb_phase=failed` + 白盒产物在 → 调 `rerun_blackbox(ws, scan_id, new_auth=None)` → 断言 `bb_rerun_attempts` 递增、`_submit_blackbox` 用 suffix `-bb-rerun-1`、`_run_blackbox_phase` 被调（跳过白盒）。换认证场景：传 new_auth → 断言 scan-config.yaml 重 dump。多次续跑 → N 递增 `-bb-rerun-1`/`-bb-rerun-2`。
- [ ] **Step 2: 运行确认失败** — `uv run pytest packages/web/tests/test_combined_rerun.py -v` FAIL
- [ ] **Step 3: 实现 `rerun_blackbox`（spec §11.3）**

```python
async def rerun_blackbox(self, ws, scan_id, new_auth: ScanRequest | None = None):
    scan_dir = self._store.get_scan_dir(ws, scan_id)
    data = SessionManager(scan_dir.parent).get_session_data(scan_dir)
    assert data.get("combined") and data.get("bb_phase") == "failed", "仅黑盒 failed 可续跑"
    assert self._whitebox_deliverables_ready(scan_dir), "白盒产物需完好"
    if new_auth:  # 换认证：重 dump scan-config.yaml
        await self._resolve_blackbox_inputs(new_auth, ws, scan_dir, ...)
    # 预验证新认证（复用 _run_precheck，pass 才继续）
    if not await self._run_precheck(scan_dir, ws, scan_id, data["bb_url"], str(scan_dir/"scan-config.yaml")):
        await self._mark_bb(scan_dir, "failed", "auth_failed"); return
    data["bb_rerun_attempts"] = data.get("bb_rerun_attempts", 0) + 1
    SessionManager(scan_dir.parent).update_session(scan_dir, {"bb_rerun_attempts": data["bb_rerun_attempts"], "bb_phase": "running"})
    suffix = f"-bb-rerun-{data['bb_rerun_attempts']}"
    asyncio.create_task(self._run_blackbox_phase_with_suffix(scan_dir, ws, scan_id, suffix, ...))
```

- [ ] **Step 4: `_run_blackbox_phase` 支持 suffix 参数**（首跑 `-bb`、续跑 `-bb-rerun-N`）。
- [ ] **Step 5: API 端点** — `POST /{ws}/scans/{id}/combined/rerun-blackbox`（body 可选认证）。
- [ ] **Step 6: 运行通过** — `uv run pytest packages/web/tests/test_combined_rerun.py -v`
- [ ] **Step 7: Commit** — `feat(web): 黑盒 failed 后换认证续跑（D5，-bb-rerun-N 可多次）`

---

## Task 8: 融合报告 + combined track 识别

新增 `combined_report_renderer`（vuln_class 交叉 + 摘要），`DeliverablesReader`/`resolve_track_deliverable` 支持 `combined`。

**Files:**
- Create: `packages/web/src/supernova_web/components/combined_report_renderer.py`
- Modify: `packages/web/src/supernova_web/components/deliverables_reader.py`（`_infer_track` 加 combined）
- Modify: `packages/core/src/supernova_core/utils/paths.py`（`COMBINED_SUBDIR` + `combined_dir`）
- Modify: `packages/web/src/supernova_web/components/scan_manager.py`（`_generate_combined_report`）
- Test: `packages/web/tests/test_combined_report_renderer.py`

- [ ] **Step 1: 写失败测试** — 给定白盒+黑盒 queue 样例 → 断言 `render_combined_report(scan_dir)` 输出含「组合摘要」表 + vuln_class 交叉 + 落 `deliverables/combined/combined_report.md`。
- [ ] **Step 2: 运行确认失败** — `uv run pytest packages/web/tests/test_combined_report_renderer.py -v` FAIL
- [ ] **Step 3: paths.py 加 `COMBINED_SUBDIR="combined"` + `combined_dir()`**；`resolve_track_deliverable` track 注释扩含 combined（逻辑不变，track 已是参数）。
- [ ] **Step 4: 实现 `combined_report_renderer.py`（spec §10.2）** — 按 `_VULN_CLASSES`（injection/xss/ssrf/authz）读白盒 `{vt}_exploitation_queue.json` + 黑盒 `{vt}_findings.json`，交叉输出 + 顶部摘要表，写 `combined/combined_report.md`。
- [ ] **Step 5: deliverables_reader `_infer_track` 加 combined 分支**（`combined/combined_report.md` 存在 → track=combined）。
- [ ] **Step 6: `_generate_combined_report` 接线**（`render_combined_report(scan_dir)`，包 async 入口）。
- [ ] **Step 7: 运行通过** — `uv run pytest packages/web/tests/test_combined_report_renderer.py -v`
- [ ] **Step 8: Commit** — `feat(web): 融合报告 combined_report_renderer（vuln_class 交叉 + combined track）`

---

## Task 9: 前端--扫描页开关 + 共享认证 + 共享 HOST + 预验证态

白盒扫描页加开关，展开 URL + 认证（复用黑盒组件），提交后预验证态。

**Files:**
- Modify: `packages/web/frontend/src/pages/ScanNewPage.tsx`（开关 state + buildBody + 预验证态）
- Modify: `packages/web/frontend/src/components/ScanFormFields.tsx`（抽 `<AuthFields>` 共享组件） + 抽 `<HostFields>` 共享组件（HOST 区段搬家，黑盒/白盒组合复用）
- Test: `packages/web/frontend/src/components/__tests__/ScanNewPageCombined.test.tsx`

- [ ] **Step 1: 写失败测试（vitest）** — 开关关 → buildBody 不含 url/认证；开关开 → 展开填值 → body 含 url + 认证。先读 `ScanNewPage.tsx` 的 setType/buildBody 确认受控 state 结构再补断言。
- [ ] **Step 2: 运行确认失败** — `cd packages/web/frontend && ./node_modules/.bin/vitest run src/components/__tests__/ScanNewPageCombined.test.tsx` FAIL
- [ ] **Step 3: 抽共享认证组件**（ScanFormFields.tsx）— 黑盒认证 JSX（RightAuthCore/BottomInlineBlock/BottomProfileBlock）抽成 `<AuthFields value onChange />`，黑盒分支与白盒组合展开区复用。同理把黑盒 HOST section（segmented + HostProfilePicker/URL 输入）抽成 `<HostFields value onChange workspace />`，供白盒组合展开区复用。
- [ ] **Step 4: ScanNewPage 加开关 + buildBody 组合分支** - `<Switch>同时发起黑盒扫描</Switch>`；展开区 `{combined && <URLInput/> + <AuthFields/> + <HostFields/>}`；buildBody whitebox 组合分支：`if (combined && url) { body.url = url; Object.assign(body, authPayload()); assignHostToBody(body, f.host); }`（assignHostToBody 黑盒/组合共享）。提交后据响应 `bb_phase=precheck` 显示「预验证中」态（spec §8.2）。
- [ ] **Step 5: 运行通过 + 回归** — `./node_modules/.bin/vitest run src/components/__tests__/ScanNewPageCombined.test.tsx src/pages`
- [ ] **Step 6: Commit** - `feat(web): 扫描页组合开关 + 共享认证 + 共享 HOST + 预验证态`

---

## Task 10: 前端——列表进度卡片（收起 x% / 展开步级）

列表卡片支持展开/收起：收起显示 `progress_pct` + 阶段名；展开按需读 events 推步级。

**Files:**
- Modify: `packages/web/frontend/src/routes/WorkspaceDetail/ScanList.tsx`（卡片展开/收起 + 进度条 + 步级展开）
- Modify: types/api（scan summary 加 combined/bb_phase/progress_pct 字段）
- Test: `packages/web/frontend/src/routes/WorkspaceDetail/__tests__/ScanListCombined.test.tsx`

- [ ] **Step 1: 写失败测试** — 组合 scan summary（combined=true, progress_pct=62, bb_phase=pending）→ 收起显示「62% + 白盒中 + 进度条」；点击展开 → 拉 events 显示步级（recon 4/6 步）。纯白盒 → 单段进度（回归）。
- [ ] **Step 2: 运行确认失败** — `./node_modules/.bin/vitest run src/routes/WorkspaceDetail/__tests__/ScanListCombined.test.tsx` FAIL
- [ ] **Step 3: ScanSummary 类型加字段** — `combined?: boolean; bb_phase?: string; progress_pct?: number; bb_reason?: string`。
- [ ] **Step 4: 卡片收起态** — `combined` 时渲染 `progress_pct%` + 进度条 + 阶段名（bb_phase 映射：precheck/pending/running/completed）；非 combined 走原单段渲染（回归）。
- [ ] **Step 5: 卡片展开态（按需步级）** — 展开时拉该 scan 的 events（复用 live events SSE 或新端点），用 `PhaseEvent`(declared steps) + `StepEvent`(complete) 推步级进度（spec §9.3）。分段靠 `bb_phase`（不依赖 phase_boundary 事件）。
- [ ] **Step 6: 运行通过 + 回归** — `./node_modules/.bin/vitest run src/routes/WorkspaceDetail/__tests__/ScanListCombined.test.tsx src/routes/WorkspaceDetail`
- [ ] **Step 7: Commit** — `feat(web): 列表进度卡片（收起 progress_pct / 展开步级）`

---

## Task 11: 前端——详情两段时间线 + 报告三子 tab + 产物三桶 + 续跑入口

详情页组合视图 + 报告三视图 + 失败续跑入口。

**Files:**
- Modify: `packages/web/frontend/src/routes/WorkspaceDetail/ScanDetail.tsx`（两段时间线 + 续跑按钮）
- Modify: `packages/web/frontend/src/routes/WorkspaceDetail/ReportTab.tsx`（三子 tab）
- Modify: `packages/web/frontend/src/routes/WorkspaceDetail/DeliverablesTab.tsx`（三桶）
- Test: `packages/web/frontend/src/routes/WorkspaceDetail/__tests__/ReportTabCombined.test.tsx`

- [ ] **Step 1: 写失败测试** — combined 详情渲染两段时间线（靠 bb_phase 切段）；报告 tab 三子 tab，融合 tab 拉 `?track=combined`；黑盒 failed 时显示「续扫黑盒」按钮（spec §11.3）。
- [ ] **Step 2: 运行确认失败** — vitest FAIL
- [ ] **Step 3: ScanDetail 两段** — `combined` 时按 `bb_phase` 渲染白盒段/黑盒段（PhaseEvent 辅助时间线）；`bb_phase=failed` 显示「续扫黑盒」按钮 → 调 `rerun-blackbox`（可弹认证重选）。
- [ ] **Step 4: ReportTab 三子 tab** — combined 时渲染 `[白盒|黑盒|融合]`，各拉 `scanReportPath(ws,id,track)`（track=whitebox/blackbox/combined）。
- [ ] **Step 5: DeliverablesTab 三桶** — summary 含 combined track 项，按桶分组。
- [ ] **Step 6: 运行通过 + 回归** — 非 combined 单视图不变。
- [ ] **Step 7: Commit** — `feat(web): 详情两段时间线 + 报告三子 tab + 续跑入口`

---

## Task 12: 端到端冒烟 + worker rebuild

- [ ] **Step 1: rebuild worker** — `cd /root/shannon-py && docker compose build supernova-worker`（确认 core/whitebox/web 包都打进镜像）
- [ ] **Step 2: 正常组合扫描** — 前端开关 ON，填 repo + URL + 认证，提交。观察：预验证✓ → 白盒阶段进度 → 自动接力黑盒 → 三报告齐。验证列表卡片收起%/展开步级。
- [ ] **Step 3: 验证单目录三桶** — `ls <ws>/scans/<id>/deliverables/` 含 `whitebox/ blackbox/ combined/`；只有一条 scan 记录。
- [ ] **Step 4: t0 预验证失败路径** — 填错认证 → fail-fast，不跑白盒，前端弹「认证失败」。
- [ ] **Step 5: 黑盒续跑路径（D5）** — 构造黑盒认证失败（白盒完成后改目标密码）→ bb_phase=failed → 点「续扫黑盒」换正确认证 → 黑盒✓。多次续跑 N 递增。
- [ ] **Step 6: 回归冒烟** — 纯白盒（开关关）跑同仓：零回归（仅 whitebox/）；纯黑盒入口追加：独立 `<wb>~N/` 不变。
- [ ] **Step 7: scan_end 唯一性核查** — 成功路径 events.ndjson `grep -c '"type":"scan_end"'` = 1。
- [ ] **Step 8: Commit 冒烟记录** — 记到 plan 末尾或 PR 描述。

---

## Self-Review

**1. Spec 覆盖**：§3 不变量（Task 0 锁死 + Task 2 回归 + Task 4 幂等 scan_end）；§4 单目录三桶（Task 4/8 + Task 12）；§5 复用 _submit_blackbox（Task 4 Step 3）；§6 数据模型（Task 1 validator + session 字段 + Task 1 透传）；§7 接力时序（Task 2 PhaseEvent + Task 3 预验证 + Task 4 orchestrator + Task 5 reconciler）；§8 入口（Task 9）；§9 进度分层（Task 10）；§10 报告（Task 8/11）；§11 失败/续跑/resume（Task 3 fail-fast + Task 4 异常契约 + Task 6 resume/cancel + Task 7 续跑）；§12 测试矩阵（每任务 TDD）；§13 风险（Task 0 验证 + 幂等 scan_end 守卫 + reconciler）。**无遗漏**。

**2. 占位扫描**：Task 9-11 前端测试 Step 1 给了测试骨架 + 「先读受控 state/类型再补断言」指引（明确行号 + 断言意图，非 placeholder）。其余任务代码均实际。

**3. 类型一致性**：`combined: bool`（PipelineInput/ActivityInput 一致，Task 2）；`bb_phase` 取值 `{precheck,pending,running,completed,failed,skipped}`（Task 1/3/4/5/6/7 一致）；`workflow_id_suffix`（`-bb` / `-bb-rerun-N`，Task 4/6/7 一致）；`bb_auth_ref` 只存 `profile_id`（Task 1 定义，Task 4/7 用）；`_ensure_scan_end` 幂等（Task 4 定义，Task 3/5 用）；`_run_blackbox_phase`（Task 4 定义，Task 5/7 用）。

**关键依赖顺序**：Task 0 → 1 → 2 →（3 ‖ 4 的 _submit_blackbox 改动）→ 4 →（5 ‖ 6 ‖ 7 ‖ 8）→（9 ‖ 10 ‖ 11）→ 12。Task 0/1/2 是后端前置；Task 4 依赖 1/2/3；前端 9-11 依赖后端 1/4/8 的透传接口。

**4. 已修原草案缺陷**：① 砍 Task 1（原 PhaseEndEvent 新增，改复用现有 PhaseEvent，D1）；② 幂等 scan_end（原 finally 无条件写致双重 scan_end，D-#1）；③ 复用 _submit_blackbox（原手写 _submit_blackbox_chained 漏 host_mappings，D-#3）；④ ws 用 scan_key[0]（原 scan_dir.parent.name 传错，D-#4）；⑤ bb_auth_ref 只存 profile_id（原存明文，D2）；⑥ 加 t0 预验证（D4）；⑦ 加黑盒续跑（D5）；⑧ 加进度透传+分层（G1）；⑨ 砍 _emit_phase_boundary（D3）；⑩ 路径 /root/shannon-py（D-#8）。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-13-combined-wb-bb-scan.md`. Two execution options:

**1. Subagent-Driven (recommended)** — 每个任务派一个全新 subagent，任务间 review，快速迭代（13 任务大计划，隔离上下文）。

**2. Inline Execution** — 当前 session 用 executing-plans 批量执行，检查点 review。

**哪种？**（选 1 从 Task 0 开始，逐任务 subagent + 两阶段 review）
