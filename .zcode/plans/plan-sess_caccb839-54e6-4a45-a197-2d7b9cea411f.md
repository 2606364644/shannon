# 根因修：黑盒 run 运行态在任务级 status / 判活 / 收尾 / 取消全链路如实呈现

## 已核实的根因链（含两个连带 bug）

1. **status 不反映**：`_mark_run`（scan_manager.py:2168 起，现约 :2210）只写 run 级 session + `bb_runs[]` 条目，不写任务级 `status`。白盒已完成的任务手动加 run 后任务级停在 `completed` → 前端 `is_running=false` → 无取消按钮、无轮询（原始 bug）。
2. **判活盲区（坑）**：黑盒 run 的 heartbeat 落在 `blackbox-runs/run-K/heartbeat`（worker 按 `event_file.parent` 推 workspace_path，blackbox workflows.py:96-97），而 `is_scan_alive`（scan_liveness.py:102-108）只看 `<scan_dir>/heartbeat` + `submitted_at` 120s 宽限。若只把任务级 status 写回 running，宽限一过 `_compute_status`（workspaces_indexer.py:37）就误判 `interrupted`。**现存连带 bug**：首次组合扫描的黑盒阶段 >120s 同样会显示 interrupted（任务级 status 本就是 running、heartbeat 已移入 run 目录）。
3. **收尾缺口**：手动 run-2 结束时任务 events.ndjson 尾部已有 run-1 时代的 scan_end → `_ensure_scan_end`（scan_manager.py:2110）no-op → 任务级终态无人写。且旧 scan_end 被 SSE 回放、并让 orphan_reconciler 的 gate 5（`_has_scan_end` → no-op，orphan_reconciler.py:177-179）短路掉组合恢复，web 重启后 run 状态永久卡死。
4. **取消半截**：`_cancel_combined`（scan_manager.py:1628-1678）只在 latest run `bb_phase=="running"` 时取消 run workflow 并标 run cancelled；pending/precheck 段：authcheck workflow（`{ws}-{scan_id}-authcheck`）不取消、run 不标终态（永久 pending → 删除/新增永久禁用）；手动 run 的 precheck 在 `_add_blackbox_run`（:1959-2024）内联执行、orchestrator task 在 precheck 之后才注册 → precheck 期间取消完全无效（cancel 后黑盒仍会被提交）。
5. **delete 防御缺失**：`delete()`（:1336-1356）只查任务级 running；run 在跑 + 任务级 completed 时可整目录删除正在跑的 run。
6. 前端缺 cancelled 语义：任务级 `TERMINAL`（ScanList.tsx:31）与 run 级 `isRunTerminal`/`runStatusLabelKey`（runStatus.tsx）都不含 `cancelled`，而 `_cancel_combined` 会产出 cancelled run/任务。

工作树已有并行改动（`_add_blackbox_run` 已有空 URL 守卫 + 在跑守卫；ScanDetail 已有 `addBbBlockedBy` 门控）——本计划在其上叠加，实现时以当下文件为准。

## 改动清单

### 后端 supernova_web

**A1. 判活扩展（解坑）** — `scan_liveness.py`
`is_scan_recently_active` 从单文件改为多候选：`<dir>/heartbeat`、`<dir>/blackbox-runs/*/heartbeat`、`<dir>/.authcheck/heartbeat`（任一 mtime fresh 即活；glob 目录小、成本可忽略）。`is_scan_alive`（宽限门 OR）自动受益；docstring 注明 run 级 heartbeat 语义。非组合扫描无这些子目录 → 零回归；cancel 轨②对组合任务不可达。同时修复现存"首跑组合黑盒阶段显示 interrupted"bug。

**A2. run 启动时任务级进入 running** — `_add_blackbox_run`，在 `create_blackbox_run` 之后、precheck 之前：
```python
self._strip_trailing_scan_end(scan_dir / "events.ndjson")   # 剥旧 scan_end（resume 同款 :326）
mgr.update_session(scan_dir, {"status": "running", "completed_at": None})
self._mark_submitted_at(scan_dir)                            # 刷新宽限锚点盖 precheck 冷启动
```
效果：列表/详情/Hero 立即 running（取消按钮出现、10s 轮询恢复、Dashboard 计入）；旧 scan_end 剥除后 run 结束时 `_ensure_scan_end` 能写新 scan_end + 任务终态（收尾缺口自愈），SSE 不再回放旧 scan_end，reconciler gate 5 不再短路。

**A3. precheck 异步化进编排 task** — `_add_blackbox_run` 重构（镜像 `_combined_kickoff` 模式）
新方法 `_add_run_kickoff`：precheck fail → 现有失败标记逻辑原样搬入（`_mark_run(failed)` + `_ensure_scan_end(failed)`）；pass → `await self._rerun_orchestrator(...)`。`_add_blackbox_run` 在 run 创建 + A2 后立即 `asyncio.create_task(_add_run_kickoff)` 注册进 `_orchestrator_tasks` 并返回 run_id（kickoff finally 幂等 pop）。端点从阻塞数分钟变为立即返回（202 语义成立，前端已兼容：toast + 选中新 run + load）。precheck 期间取消从此有效。

**A4. `_cancel_combined` 补 pending/precheck 段**
- active run 判定：`bb_phase=="running"` → `latest run status ∉ _RUN_TERMINAL_STATUSES`。
- 取消目标：phase running → run workflow id（现状）；否则 best-effort cancel `{ws}-{scan_id}-authcheck`（顺带修首跑 kickoff precheck 期间的 authcheck 泄漏）。
- 该 run 标 `cancelled`（status+phase）——解除"取消后 run 永久 pending → 删除/新增永久禁用"。
- `_mark_cancelled` 因 scan_end 已剥会写新 scan_end(cancelled) ✓。

**A5. `delete()` 防御** — 任务级 running 检查后追加：`bb_runs` 任一非终态 → `ScanRunning`（409）。取消（A4）后可删，链路闭环。

**A6. `_reconcile_combined_scan` 收尾** — 非终态 run 的 workflow 查询返回 None → `_mark_run(failed, reason="编排中断（web 重启），run 未完成")`，防 bb_runs 永久卡非终态（A5 + 在跑守卫会放大该卡死）；口径与既有 finally 默认 completed 的激进性一致。

### 前端

**A7.**
1. `runStatus.tsx`：`isRunTerminal` + `runStatusLabelKey` 加 `cancelled`。
2. `ScanList.tsx`：任务级 `TERMINAL` 加 `cancelled`（取消后的任务显 查看/重跑/删除，恢复按钮后端本就 422）。
3. `ScanDetail.tsx`：`whiteboxTerminal` 扩为 `["completed","done","cancelled"]`——取消过手动 run 的任务（白盒产物完好）仍可再加黑盒（后端 `_whitebox_deliverables_ready` 兜底）；更新注释（"任务级 status 停留 completed"前提已被推翻，run 在跑时按钮随 status=running 自然隐藏，`addBbBlockedBy` 降级为兜底）。
4. locales：`workspaceDetail.scans.runs.statusCancelled`（zh 已取消 / en Cancelled）。

### 测试

- 后端（pytest）：新增/扩展 `test_add_blackbox_run.py` 等——add-run 后任务级 status=running + scan_end 剥除；run heartbeat fresh/stale → `_compute_status` running/interrupted（scan_liveness 单测含 `.authcheck`）；run 完成 → 新 scan_end + 任务 completed；cancel pending 段（authcheck cancel + run 标 cancelled）与 running 段；delete 非终态 run → ScanRunning；reconcile workflow None → run 标 failed。适配既有用例到异步 kickoff（`await mgr._orchestrator_tasks[...]`）。
- 前端（vitest）：`ScanDetail.test.tsx` "run 进行中" 用例改双场景（新口径 status=running → 加黑盒按钮隐藏；legacy status=completed + running run → 禁用）；`ScanList.test.tsx` cancelled → 重跑按钮；其余既有 mock 已按新口径（status=running + bb_runs）无需动。

### 验证

- `pytest packages/web/tests/ -k "blackbox or combined or cancel or delete or liveness or reconcile"`
- `npx vitest run`（frontend 相关文件）

### 明确不做

- 不加 run 级 cancel API / 嵌套子行停止按钮（主行取消经 A4 已完整覆盖 active run）。
- 不动 worker / whitebox / blackbox 包（纯 web 侧修复，不受在跑 worker 版本漂移影响）。
- 不处理历史遗留卡死数据（旧 scan_end + run 卡 running 的任务可用"取消→删除 run"手工解开，A2 之后的新任务不会再产生）。
