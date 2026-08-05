# 认证验证可观测性 + 结果可信 设计

- 日期：2026-08-06
- 分支：feat/fork-py
- 关联：auth-profile-vault（子项目1）、memory `blackbox-auth-validation-two-root-causes`、memory `auth-profile-vault-spec-pending`

## 背景

认证档案库"测试登录"功能（`AuthValidationWorkflow`）存在三个叠加问题，导致用户**看不到验证过程、结果也不可信**。2026-08-06 实测（NodeGoat，admin/Admin_123）：

1. **过程不落盘**：`run_auth_validation_probe`（`packages/blackbox/src/supernova_blackbox/pipeline/activities.py:715`）不接 audit/events 日志——不调 `setup_display`、不传 `audit_logger`、不设 `event_file`。对比扫描类 activity 都经 `setup_display`（同文件 :54）挂 LogBus + StructuredEventRenderer 把 agent 每步写进 `events.ndjson`。验证 probe 的 agent 浏览器操作（导航/填表/提交/HTTP 响应/LLM 判定）**根本没记录**。
2. **结果不可信（前端轮询超时误判）**：`get_auth_validation_result`（`packages/web/src/supernova_web/components/scan_manager.py:543-545`）用 Temporal `result()` **阻塞**等 workflow 完成。验证 workflow 实测跑 88s–153s，而前端 `CredentialRow` 轮询上限 120s（40×3s）。workflow 超过 120s 时 HTTP 请求挂死 → 前端判 failed，**即使 Temporal 里 workflow 实际 COMPLETED + success**（实测 `authval-__legacy__-probe-61a66360`：Temporal `success:true` RunTime 114s，UI 却 failed）。
3. **诊断产物被删**：`get_auth_validation_result` 的 `finally shutil.rmtree(probe_dir)`（scan_manager.py:563）立即删整个 probe 目录（含 `auth-state.json`），事后无法排查。

凭据本身没问题（实测 curl 直登 NodeGoat → 302 `/benefits`）。GLM 结构化输出偶发误判 `login_success`（memory 根因 B）是另一条线；D3（2026-08-05）移除 cookie override 是有意决策，本 spec **不动 D3**。

## 目标

- **过程可见**："测试登录"时 agent 的完整登录过程（浏览器每步 + LLM 判定）落盘并在 UI 可见——实时观看 + 事后回看。
- **结果可信**：UI 显示的成功/失败与 Temporal workflow 真实结果一致，不再因轮询超时误判。
- **可诊断**：验证失败时能回看过程，搞清是 GLM 误判 / 真没登上 / 流程问题。

## 非目标

- 不改 D3（纯信 `login_success` 字段判定）、不恢复 cookie override（扫描场景假阳性危险）。
- 不改 GLM 结构化输出稳定性（根因 B 的模型层；靠可观测性暴露 + 重试应对）。
- 不动 `BlackboxScanWorkflow`（扫描主流程）。

## 设计

### 1. 后端 · 验证过程落盘（核心）

复用扫描的 `setup_display` → LogBus → StructuredEventRenderer → `events.ndjson` 链路。

- **`BlackboxAuthValidationInput`**（`packages/blackbox/src/supernova_blackbox/pipeline/shared.py:26`）加 `event_file: str | None = None` 字段（对齐 `BlackboxPipelineInput:22`）。基类 `BasePipelineInput`（`packages/core/src/supernova_core/models/base.py`）无 `event_file`，需显式加。
- **`AuthValidationWorkflow.run`**（`packages/blackbox/src/supernova_blackbox/pipeline/workflows.py:588`）在现有 `log_phase_start` 之前跑 `setup_display` activity（注入 AuditSession + 挂 LogBus + StructuredEventRenderer + heartbeat），`run_auth_validation_probe` 之后跑 `finalize_summary` 收尾（drain + `log_workflow_complete` + 停 heartbeat）。构造的 `BlackboxActivityInput` 透传 `event_file` + `workspace_name`（供 setup_display 定位 ws_path）。若 `BlackboxActivityInput` 无 `event_file`/`workspace_name` 字段，一并补齐。
- **`start_auth_validation`**（scan_manager.py:495）把 `event_file = str(probe_dir / "events.ndjson")` 塞进 `BlackboxAuthValidationInput`。
- **效果**：agent 每步 → LogBus → dispatcher → `events.ndjson` 落 `probe_dir`，与扫描 live 同机制。

**不变量（已确认）**：`AuditSession` 经 `contextvars.ContextVar` 隔离（`packages/core/src/supernova_core/audit/session_registry.py:14-22`，`set_audit_session(session, workflow_id)` / `get_audit_session_for(workflow_id)`）。验证 probe 接 setup_display **不与并发扫描冲突**（各自 context，memory `audit-session-current-agent-race` 已修）。

### 2. 后端 · 修轮询误判（结果可信）

`get_auth_validation_result`（scan_manager.py:543-545）把阻塞的 `client.get_workflow_handle(wid).result()` 改为：

1. `handle.describe()` 非阻塞查状态；
2. 未到终态（`RUNNING` 等）→ 抛 503（秒级返回，前端 continue 轮询）；
3. 终态（`COMPLETED`/`FAILED`/`CANCELED` 等）→ `result()` 取 `AuthValidationResult`（此时已就绪，不阻塞）。

效果：每次 verify-status 秒级返回，workflow 跑多久前端都能轮询到真实终态。修掉"成功被显示成失败"。守护（scan_manager.py:531-542 的 probe_dir / workflow_id 越界校验）保留不动。

### 3. 后端 · 保留过程 + 读取端点

- `get_result` 的 `finally`（scan_manager.py:561-563）收窄：删明文 `scan-config.yaml`（密码卫生），**保留 `events.ndjson` + `auth-state.json`**。
- 新端点 `GET /api/workspaces/{ws}/auth-profiles/{pid}/credentials/{cid}/verify-log?workflow_id=...&probe_dir=...`（鉴权 `workspace_member`，同 verify-status）：
  - 复用 scan_manager 越界守护（probe_dir 须在 `workspaces/<ws>/auth-probes/` 下、workflow_id 须 `authval-<ws>-` 开头）；
  - 读 `probe_dir/events.ndjson`，支持 `?tail=N`（只取末尾 N 条，实时观看）+ 默认全量（事后回看）。
- **保留策略**：同一 (profile, credential) 下次验证覆盖旧 probe（删旧 probe 目录，不无限堆积）。`verify_status` 回填时一并记录当前 `probe_dir` / `workflow_id`（扩展 `VerifyStatus` dataclass 加这两个可选字段，复用现有 `set_verify_status` 回填路径），供 verify-log 读 + 下次验证覆盖时清理。

### 4. 前端 · 展示过程

- **`CredentialRow`**（`packages/web/frontend/src/pages/CredentialRow.tsx`）：
  - 修轮询：配合后端 503 非阻塞，现有 `503 continue` 逻辑即可；因每次 503 秒级非阻塞，把 `MAX_POLLS` 上调到覆盖 workflow 最长（~3min → ~60 次 × 3s = 180s），保留上限防无限轮询。
  - 展开：结果出来后（成功/失败），凭据行可展开调 verify-log 显示 agent 过程（逐事件：浏览器命令 + LLM 判定 + 最终 verdict）。失败时默认展开。
  - 实时观看：点"测试登录"后可选实时 tail verify-log（`?tail=N` 轮询），复用扫描 live 的 events 渲染机制。
- 失败显示：`failure_detail` 当前回落 "Login failed without diagnostic"（`validate_authentication.py:229`），配合过程记录可看到 agent 实际卡在哪。

### 5. 测试

- `run_auth_validation_probe` 接 setup_display 后：`probe_dir/events.ndjson` 非空（含 agent 事件）。
- `get_auth_validation_result` 非阻塞：workflow 运行中 → 503；终态 → 200 + 正确 state（覆盖 success/failed）。
- probe 保留：verify 后 `events.ndjson` + `auth-state.json` 存在、`scan-config.yaml` 已删；覆盖策略（下次验证清旧 probe）。
- verify-log 端点：越界守护 + tail/全量 + 鉴权。
- 前端：CredentialRow 展开/实时展示（组件测试）。
- 回归：现有 auth-profiles CRUD + 验证生命周期测试不破（`test_auth_validation_lifecycle.py` / `test_api_auth_profiles.py` / `test_auth_validation_probe.py` / `test_auth_validation_workflow.py`）。

## 风险

- AuditSession contextvars 隔离已确认（不冲突并发扫描）。
- `setup_display` 自身失败（极少）不阻塞验证：workflow 层 try/except 降级 NullAuditSession，验证照跑（只是没 events）。
- **events 含敏感字段**：agent 填密码的浏览器命令可能明文进 events.ndjson。verify-log 展示须审视脱敏（或接受暴露——是用户自己的凭据 + 诊断自己的验证，同 scan-config 取舍）。**plan 阶段定**。
- 改 blackbox/worker/web src 须 **rebuild supernova-worker + supernova-web 镜像**（memory 多处约束）。
