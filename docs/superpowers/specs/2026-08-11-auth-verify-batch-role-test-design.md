# 认证测试批量选角色验证（逐个独立验证）

- 日期：2026-08-11
- 状态：设计已确认，待 plan
- 范围：认证档案「测试登录」从单凭据扩展为**档案级多选角色 → 逐个独立验证**
- 涉及包：`blackbox`（worker / temporal workflow）、`web`（前后端）
- 演进自：`2026-08-10-auth-verify-live-progress-design.md`（实时过程）+ `2026-08-11` running 中间态恢复机制

---

## 1. 问题与目标

认证测试现状（`web/.../scan_manager.py:514 start_auth_validation`）：每个角色渲染成一个 chip，点 chip 在新标签打开 `VerifyProcessPage`（路由 `/p/:ws/auth-profiles/:pid/credentials/:cid`，**绑定单个 credential**），后端只取 `cid` 对应的单个 credential 走 `validate_authentication` Branch A 单次登录（`core/services/validate_authentication.py:201-238`）。**没有「一次测多个角色」的入口**——N 个角色要手动点 N 次 chip、开 N 个 tab。

黑盒扫描已有「选角色」：`auth_credential_ids` → `_expand_multi_identity`（`scan_manager.py:363-398`）→ primary + `accounts[]` → Branch B 多身份循环 → `build_comparison_matrix` 做 vertical/horizontal **越权对比**。

**目标**：认证测试也支持选多个角色，但语义是**逐个独立验证每个选中角色能否登录**（非对比、非越权）——每个角色各自一次 Branch A 单次登录，各自 `verify_status`，各自实时过程可回看。复用 `2026-08-10` 实时过程（步骤条 + 日志）+ `2026-08-11` running 恢复机制这两套基础设施。

**非目标**：
- 多角色越权对比（那是黑盒扫描 Branch B 的事，不走认证测试）。
- 改动单角色测试链路（chip → `VerifyProcessPage` → `testCredential` 保留不变）。
- 并行登录（资源重 + 状态并发，见 §5 取舍）。

---

## 2. 语义澄清（双轨对齐，重要）

| | 黑盒扫描「选角色」 | 认证测试「选角色」（本 spec） |
|---|---|---|
| 语义 | 多角色**对比**（越权矩阵） | **逐个独立**验证每个角色能否登录 |
| 后端 | `_expand_multi_identity` → `accounts[]` → Branch B 循环 + `build_comparison_matrix` | 串行 N 次 Branch A 单次登录，各 cred 独立 probe/events/verify_status |
| role 字段 | 进 `accounts[].role` + `tier`（驱动越权配对） | **不入登录 YAML**（`credential_to_authentication` 仍丢 role，保持 Branch A 纯单次登录） |

认证测试的 role 仅作前端展示元数据（`role·username`），不影响 core 登录链路——与单角色测试完全同构，只是串行跑多次。

---

## 3. 前端：档案级测试页（新组件，不动单角色页）

### 3.1 路由与入口
- 新增路由 `/p/:ws/auth-profiles/:pid` → 新组件 **`AuthProfileTestPage`**（档案级，多选 + 批量进度）。
- **保留** `/p/:ws/auth-profiles/:pid/credentials/:cid` → 现有 `VerifyProcessPage`（单角色回看/单测，chip 点进去）。二者共享 `VerifyLivePanel` / `DashboardPanel` / `LogStream` 子组件。
- 入口：`AuthProfilesPage.tsx` 档案行加「测试登录」按钮 → 进档案测试页。chip 行保留（状态色点 + 点进单角色回看，`AuthProfilesPage.tsx:131-150` 不动）。

> **取舍（已确认）**：新建 `AuthProfileTestPage` 而非把 `VerifyProcessPage` 升级为档案级——职责清晰、不破坏 chip → 单角色页入口、单角色页的回看/单测逻辑零改。

### 3.2 页面布局（对齐黑盒 `BottomProfileBlock`）
- **header**：档案名 + `login_url` + `login_type` + 整体徽章（`overall = success > running > failed > unverified`，复用 `ScanFormFields.tsx` 现有 overallState 逻辑）。
- **角色多选区**（样式直接对齐黑盒 `BottomProfileBlock`，`ScanFormFields.tsx:385-572`）：
  - 标题「以哪个角色登录（默认全选）」+ `[全选][取消全选]` 切换（`ScanFormFields.tsx:428-431, 508-514`）。
  - 每角色一个 toggle button：checkbox + `role·username` + 该 cred verify 徽章（`ScanFormFields.tsx:517-553`）。
  - 选档案默认全选（`ScanFormFields.tsx:457` 模式）；底部「已选 N/M 个角色」计数（`ScanFormFields.tsx:555`）。
- `[开始测试选中角色]` 按钮（0 选 或 整体 running 时 disabled）。
- **进度区**（发起后显示）：每选中角色一行（✓成功 / ⟳N·4 步 / ·待测 / ✗失败）；当前 `state=="running"` 那行展开挂 `VerifyLivePanel`（订阅该 cred `verify-events` SSE）；完成行可点「看详情」跳单角色过程页。

### 3.3 轮询 / 订阅逻辑（最大复用 running 机制）
- 发起 `POST .../test-batch` → 拿 `workflow_id`（batch）。
- 定期轮询 `profile`（`GET /workspaces/{ws}/auth-profiles`，含各 cred `verify_status`）→ 推导每行状态 + 定位 `state=="running"` 的那个。
- running 的 → 订阅其 `verify-events`（`probe_dir` 来自其 `verify_status.probe_dir`，`2026-08-11` 加的字段；复用 `verifyEventsUrl` + `useEventSource` + `dashboardReducer` + `VerifyLivePanel`）。
- 该 cred 终态 → 停订阅 → 等下一个变 running → 订阅。
- 全终态 → 停轮询 + 汇总。
- **关页面恢复**：重载档案页 → 轮询 profile → 找 running 的 cred → 订阅其 events。**完全复用 `2026-08-11` running 恢复机制**（`VerifyProcessPage.tsx:91-98` effect 模式），零新增恢复逻辑。

### 3.4 共享组件抽取
从黑盒 `BottomProfileBlock` 的 toggle 多选逻辑抽一个 **`RoleMultiSelect`** 共享组件（props：`credentials`、`selectedIds`、`onToggle`、`onToggleAll`、`renderBadge`），档案测试页与黑盒扫描同源消费——对齐 `2026-08-06-auth-profile-multi-role-inline-unify-design.md` 抽 `components/auth/` 共享子组件的方向。🔴 **待 plan 确认**：抽新 `RoleMultiSelect` vs 内联复用（改动面权衡）。

---

## 4. 后端

### 4.1 API 端点
新增 `POST /workspaces/{ws}/auth-profiles/{pid}/test-batch`：
- body：`{cred_ids?: string[]}`（空或省略 = 全选该档案所有角色）。
- 鉴权：`workspace_member`（对齐 `auth_profiles.py:144 test_credential`）。
- 返回：`{workflow_id}`（batch workflow id）。
- 现有 `.../credentials/{cid}/test`、`verify-status`、`verify-events`、`verify-log` **全保留不变**——批量里每个角色仍走 per-cred 的 `verify-events` 订阅实时日志、`verify-status`/`verify-log` 回看。

### 4.2 `scan_manager.start_batch_auth_validation(ws, pid, cred_ids)`
- `profile = self._auth_profile_store.get(ws, pid)`。
- `selected = [c for c in profile.credentials if c.id in set(cred_ids)]`（`cred_ids` 空 = `profile.credentials` 全量）；空选中抛 `ValueError`（对齐 `_resolve_blackbox_inputs:400-411` 子集过滤 + 守卫）。
- 为每个 cred 建独立 `probe_dir`（`workspaces/{ws}/auth-probes/probe-{uuid8}`，`:546-548` 模式）+ 写单认证 `scan-config.yaml = {"authentication": credential_to_authentication(profile, cred)}`（`:549-555`，role 不入 YAML）。
- 起 `BatchAuthValidationWorkflow`，传 `items=[{cred_id, ws, pid, config_path, event_file}, ...]`。
- 写第一个 cred 的 running `verify_status`（带 `probe_dir`/`workflow_id`，`:581-584` 模式）。
- 返回 `{workflow_id}`。

### 4.3 `BatchAuthValidationWorkflow`（temporal，新）
位置：`blackbox/pipeline/workflows.py`（`AuthValidationWorkflow:606-670` 旁）。
- **input**：`BlackboxAuthValidationBatchInput`（`shared.py` 新增）= `{items: [{cred_id, ws, pid, config_path, event_file}, ...]}`。🔴 **待 plan 确认**：新建 batch input dataclass vs 复用现有 `BlackboxAuthValidationInput`（`shared.py:26-37`）做列表。
- **run**：串行 `for item in items:` 逐个跑——
  - `setup_display`（独立 `AuditSession` + `StructuredEventRenderer` 写该 cred 的 `events.ndjson`）。
  - `log_phase_start_activity`（4 步 phase，`AUTH_VALIDATION_PROGRESS`，`progress_tool.py:63-71`）。
  - `run_auth_validation_probe`（`activities.py:736-801`）→ `validate_authentication` Branch A 单次登录。
  - `finalize_summary`（写 `scan_end`，关该 cred 的 SSE 流）。
  - 回填该 cred 终态 `verify_status`（见 §4.4）+ 删该 cred 的 `scan-config.yaml`（保留 `events.ndjson` 供回看，`:650-659` 模式）。
- **串行不并行**：同时只有一个 cred 在跑（running 唯一），与 per-cred running 恢复机制天然契合。某 cred 失败仅记 `available=False`/`failed`，不阻断下一个（对齐 Branch B 非 primary 失败不阻断语义，`validate_authentication.py:271-275`）。

### 4.4 verify_status 实时回填（关键决策）
**每个 cred 终态时立即回填其 `verify_status`**（success/failed + `failure_point`/`failure_detail` + `last_verified_at` + `probe_dir`），前端轮询 profile 才看得到逐个完成。

**分层问题**：`auth_profile_store`（web 层，`set_verify_status:249-265`）不应被 blackbox activity 直接调用。机制：**web 层起一个轻量 watcher**——
- `start_batch_auth_validation` 起 workflow 后，`asyncio.create_task` 一个 watcher（web 层，与 `scan_manager` 同层）。
- watcher 周期（~2s）query batch workflow 的 temporal query handler `batch_progress` → `{current_cred_id, completed:[...], results_so_far:{cred_id: {success, failure_point, ...}}}`。
- 发现任一 cred 刚终态 → 回填该 cred `verify_status`（web 层调 store，分层干净）。
- 全部终态 → watcher 退出。
- 前端**只轮询 `profile`**（现有 `GET /auth-profiles`），不需新进度端点——verify_status 即实时进度源。

🔴 **待 plan 确认**：watcher 的生命周期/重启/多副本策略（可借现有 `orphan_reconciler` 模式，`web/.../components/` 下）；query handler vs 读各 probe `result.json` 的取舍。

### 4.5 越界守护（🔴 必做，吸取 probe_dir rmtree Critical 教训）
- `test-batch` 端点 `cred_ids` 校验：必须属于该 `pid` profile（防注入任意 id）。
- 各 cred 的 `probe_dir` 必须收敛在 `workspaces/<ws>/auth-probes/` 下（resolve 后校验前缀，对齐 `get_auth_validation_result:613-616`）。
- `verify-events` / `verify-log` 端点对 batch 产出的 `probe_dir`/`workflow_id` 越界守护：`workflow_id` 前缀校验放宽到包含 batch 前缀（如 `authval-batch-{ws}-...`），🔴 待 plan 定 batch workflow id 命名。

---

## 5. 降级与边界

- **串行总时长**：N 角色各 90–150s 登录 → 5 角色约 8–12 分钟。串行是项目已有模式（黑盒 Branch B 亦 `for` 循环）；若未来要提速可改并行（引入 browser session 并发 + 多 events 流聚合，超本 spec 范围）。
- **关页面恢复**：复用 `2026-08-11` running 机制——重载轮询 profile 找 running 的订阅其 events；已完成的 cred verify_status 已落盘可见。
- **子集信息不持久化**（YAGNI）：重载后前端忘了「选了哪几个」，但各 cred `verify_status` 都在，用户看到所有角色当前结果；想重测重新选。换实现简单。
- **worker 挂**：某 cred 卡 running——既有风险（`2026-08-11` memory 已记），超范围（未来 TTL/orphan_reconciler 增强）。
- **取消批量**：MVP 不做（nice-to-have，未来可 `cancel` batch workflow + 清理各 probe）。
- **降级**：agent 偶发不调 `log_milestone` → 该 cred 步骤条不推进但日志照显（对齐 `2026-08-10` §8）；`scan_end` 缺失靠 `useEventSource` onerror 重连 + `verify-status` 兜底。

---

## 6. 部署

改动跨越 blackbox（worker：新 workflow + 可能的 batch input）+ web 前后端（新端点 + 新前端页 + watcher）→ **须 rebuild worker + web 镜像**才生效。

---

## 7. 测试策略（指导 plan，TDD）

- **blackbox**：`BatchAuthValidationWorkflow` 编排测试（新，仿 `test_auth_validation_workflow.py`）——断言串行跑 N 个 cred、各产出独立 `PhaseEvent(4 steps)`/`StepEvent`/`scan_end`、失败 cred 不阻断后续、query handler 返回正确进度。
- **web 后端**：`start_batch_auth_validation` 全选/子集/空选/cred_id 越界守护；`test-batch` 端点（鉴权 + body 校验）；watcher 回填时机（mock query 返回 → 断言各 cred verify_status 依序落盘）；🔴 `probe_dir` 路径穿越拒绝。
- **web 前端**：`AuthProfileTestPage` 多选交互（默认全选/全选切换/计数）、发起后轮询发现 running 订阅其 events、该 cred 终态切换到下一个、关页面恢复（复用 `VerifyProcessPage` running 恢复测试模式，`vi.mock useEventSource`）；`RoleMultiSelect` 共享组件单测（如抽离）。
- 仅跑改动相关测试文件（CLAUDE.md 测试陷阱：全套 pytest 有预存挂起/失败）。

---

## 8. 待 plan 确认项

- 🔴 §3.4 `RoleMultiSelect` 抽新共享组件 vs 内联复用。
- 🔴 §4.3 `BlackboxAuthValidationBatchInput` 新建 vs 复用现有 input 做列表；batch workflow id 命名规约。
- 🔴 §4.4 watcher 生命周期/重启/多副本策略（借 `orphan_reconciler` vs 其他）；query handler vs probe `result.json` 取舍。
- 🔴 §4.5 `verify-events`/`verify-log` 对 batch `workflow_id` 前缀守护的放宽实现。
- §5 并行化提速是否纳入后续迭代（默认本 spec 不做）。
