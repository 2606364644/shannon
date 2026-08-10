# 认证档案「测试登录」实时过程展示（步骤条 + 实时日志）

- 日期：2026-08-10
- 状态：设计已确认，待 plan
- 范围：`/p/__legacy__/auth-profiles`（认证档案页）单凭据「测试登录」验证的实时过程可见性
- 涉及包：`blackbox`（worker）、`core`（agent 集成层 / 事件体系）、`web`（前后端）

---

## 1. 问题与目标

认证档案页「测试登录」目前是一个**盲转圈 spinner**：点击后只能每 3s 轮询拿最终状态，测试完成才看得到结果。两个根因：

1. **后端 standalone 验证路径不落盘 agent 动作**：`run_auth_validation_probe`（`blackbox/pipeline/activities.py:707`）没接 `SessionToolAuditLogger`、没调 `start_agent/end_agent`，所以 agent 的导航/填表/提交/查会话动作**一条事件都没写进 `events.ndjson`**。对比扫描流程内的 `run_blackbox_auth_validation`（同文件 `:148`）是接全的。standalone 路径实际只写 4 行：`WorkflowHeader` / `PhaseEvent(空 steps)` / `SummaryEvent` / `scan_end`。
2. **前端把本次 run 的流没接给日志面板**：`CredentialRow.onTest`（`web/frontend/src/pages/CredentialRow.tsx:59`）拿到的本次 `{workflow_id, probe_dir}` 只喂给了状态轮询循环；日志面板绑的是 `credential.verify_status`（上一次 run 的旧数据），且 `start_auth_validation` 每次会删旧 probe 目录。

**目标**：测试登录时展示 **4 步进度条 + 下方 agent 实时日志**，复用扫描 live 页同款事件流（`DashboardPanel` + `LogStream` + `dashboardReducer`），不造新前端范式。

**非目标**：
- 多身份 preflight 登录循环的可观测性（走扫描 live 页，不在此范围）。
- 黑盒 inline 认证区、其它入口的验证过程展示（本次只做 auth-profiles 页）。

---

## 2. 4 个步骤定义

进度条的 4 步用**稳定英文 key** 作为事件层的 step name（对齐 whitebox `step_intents.py` 惯例、避免事件匹配的编码问题），前端按 key 显示本地化标签。

| step key | 本地化标签（zh） | intent（步骤语义） | milestone 触发时机 |
|---|---|---|---|
| `navigate` | 连接目标 | 导航到登录页 | agent 打开/到达登录页后 |
| `fill_credentials` | 填凭据 | 填写 username/password/captcha/TOTP | 表单字段填完后 |
| `submit` | 提交登录 | 提交登录表单 | 触发登录提交后 |
| `verify_session` | 校验会话 | 校验已登录会话 | 确认登录态（cookie/session）后 |

step key 同时是 `log_milestone` 工具的枚举值、`PhaseEvent.steps` 的元素、`StepEvent.name` 的值——三者一致，`dashboardReducer` 按 name 匹配推进步骤条，无需额外映射。

---

## 3. 后端：补 agent 动作落盘 + 步骤条事件（blackbox + core）

### 3.1 standalone probe 接入可观测性
`run_auth_validation_probe`（`blackbox/pipeline/activities.py:707`）改为照搬扫描内 `run_blackbox_auth_validation`（`:148-214`）的可观测接线：
- `ensure_audit_session(input)` 恢复 worker 重启后的 session；
- 建 `SessionToolAuditLogger(session, agent_name, attempt)` 并 `initialize()`；
- `session.start_agent(...)` / `end_agent(...)`（成功/失败两路径 + PentestError 路径的 cost 记账，对齐 `:185-211`）；
- 把 `tool_audit_logger` 透传给 `validate_authentication(...)`（该服务 `core/services/validate_authentication.py:170` 已支持该参数，只是 standalone 路径没传）。

完成后，agent 的每个 tool call / LLM turn 以 `ToolCallEvent`/`LlmTurnEvent`/`AgentEvent` 实时写进 `events.ndjson`。

### 3.2 声明 4 步 PhaseEvent
- 扩展 blackbox 的 `log_phase_start_activity`（`activities.py:522`）接受可选 `steps`/`intents` 参数，对齐 whitebox `whitebox/pipeline/activities.py:368` 的签名。
- 新增步骤注册表（镜像 whitebox `step_intents.py` 的 `PHASE_STEPS`），定义 `auth-validation` 阶段的 4 个 `StepSpec(key, intent)`。
- `AuthValidationWorkflow`（`blackbox/pipeline/workflows.py:616`）调用 `log_phase_start_activity` 时传入这 4 个 steps + intents，产出 `PhaseEvent(phase="auth-validation", event="start", steps=(...), step_intents=(...))`。`DashboardPanel` 据 `phase_units` 渲染 4 个待完成圈。

### 3.3 `log_milestone` 里程碑工具（细粒度步骤条的核心）
新增一个自定义工具，schema 为枚举 4 个 step key；agent 在对应阶段完成后调用它。

- **handler**：`from supernova_core.audit.session_registry import get_audit_session; await get_audit_session().log_step(step_key, "auth-validation", "complete", intent=...)`。`session.log_step`（`core/audit/session.py:132`）是公开方法，可直接手动发 `StepEvent`（不必用 `track_step` context manager，因为步骤边界发生在 agent 的一次 `executor.execute` 内部，没有 Python 钩子）。`NullAuditSession.log_step` 镜像 no-op，无 session 场景不崩。
- **双引擎对称注入**（走 collector 同款已验证通路）：
  - openai：`FunctionTool(name="log_milestone", params_json_schema={step: enum}, on_invoke_tool=handler)`，经 `build_agent` 的 `extra_tools` 拼进（`core/agents/providers_openai.py:205`）。
  - claude：`SdkMcpTool(name="log_milestone", input_schema=..., handler=handler)`，经 `create_sdk_mcp_server` 挂到 `options.mcp_servers`、名字加进 `allowed_tools`（`core/agents/providers_anthropic.py:109,341`）。
  - 模板：`core/collectors/bridge.py` 的 `build_openai_tools` / `build_claude_mcp_server`（collectors 的 `set_*` 工具即此通路的真机验证先例）。
- **注入来源**：仅 validate-authentication agent 获得。按 collector 工厂模式（`core/collectors/__init__.py:10` 的 `make_collector`），加一个按 `agent_name` 返回里程碑工具构建器（或工具集）的工厂，在 provider 组装工具时与 collector 工具一并注入。🔴 **待 plan 确认注入通道的具体接线**：是新增一个并行于 `collector` 的可选参数（如 `milestone_tools`）线程化穿过 `executor.execute → run_claude_prompt → provider.call`，还是扩 `make_collector` 体系。两者都成立；plan 选改动面更小、对齐现有架构者。

---

## 4. 后端：新增 SSE 端点（web）

新增 `GET /{ws}/auth-profiles/{pid}/credentials/{cid}/verify-events?workflow_id&probe_dir`：
- 复用 `EventTailer`（`web/src/supernova_web/components/event_tailer.py`）tail `probe_dir/events.ndjson`，逐事件编码成 SSE frame，遇 `type=="scan_end"` 关流。
- 端点结构镜像扫描 events 端点（`web/src/supernova_web/api/events.py:19` 的 `build_scan_events_response` / `scans.py:197` 的路由）。
- 支持 `Last-Event-ID` 断点续传（EventTailer 已有 byte offset 能力）。
- **安全（🔴 必做）**：`probe_dir` 必须收敛在 `workspaces/<ws>/auth-probes/` 下，防路径穿越（resolve 后校验前缀）。对齐既有 verify-log 端点的校验，并吸取历史 `probe_dir→rmtree` Critical 教训——任何接受 `probe_dir` 的新端点都要服务端校验。

---

## 5. 前端：接本次 run 的实时流（web/frontend）

改造 `web/frontend/src/pages/CredentialRow.tsx`：
- `onTest` 拿到 `testCredential` 返回的**本次** `{workflow_id, probe_dir}` 后，开 `EventSource` 连 verify-events 端点（用 `useEventSource` hook，`api/useEventSource.ts`，stopType 用 `scan_end`）。
- events 增量喂 `dashboardReducer`（`state/dashboardReducer.ts`），渲染：
  - **紧凑版步骤条**：复用 `DashboardPanel`（`components/DashboardPanel.tsx`）的 `phase_units` 行（4 步 ○/✓/✗），step key 经本地化映射显示中文标签；放进展开的 credential 面板内（行内紧凑布局，非整页 live 页）。
  - **实时日志**：复用 `LogStream`（`components/LogStream.tsx`）逐事件渲染 agent 的 tool call / LLM turn。
- 收到 `scan_end` → 调一次 `getVerifyStatus` 拉终态（顺带让后端 `set_verify_status` 落盘 + refresh 凭据行），取代现在的 3s 盲轮询循环。
- 修掉「查看过程」绑上一次 run 旧数据的 bug：本次 run 的 SSE 流即本次过程；测试结束后该流的历史日志留存可回看。
- URL helper：仿 `scanEventsUrl`（`api/client.ts:172`）加 `verifyEventsUrl(ws, pid, cid, workflow_id, probe_dir)`。

---

## 6. prompt 改动

`prompts/validate-authentication.txt` 增加一段 `<progress>`，指示 agent 在四个阶段完成后各调一次 `log_milestone`：
- 到达登录页后 → `log_milestone("navigate")`
- 填完凭据后 → `log_milestone("fill_credentials")`
- 提交登录后 → `log_milestone("submit")`
- 校验会话后 → `log_milestone("verify_session")`

与 CLAUDE.md 双轨铁律「不喂确定性产物给 LLM 轨」无冲突：本工具是 agent 主动回报自身进度，非 vuln 轨、不喂确定性结果。

---

## 7. 事件流时序

```
前端 onTest → POST .../test → 返回 {workflow_id, probe_dir}
                ↓ worker 起 AuthValidationWorkflow
   setup_display        → WorkflowHeader
   log_phase_start(4 steps) → PhaseEvent(start, steps=[navigate,fill_credentials,submit,verify_session])  ← 步骤条渲染 4 圈
   start_agent          → AgentEvent(start)
   agent 跑登录：       → ToolCallEvent / LlmTurnEvent（实时日志逐条）
     log_milestone("navigate")          → StepEvent(complete, navigate)    ← 圈1 ✓
     log_milestone("fill_credentials")  → StepEvent(complete, ...)         ← 圈2 ✓
     log_milestone("submit")            → StepEvent(complete, ...)         ← 圈3 ✓
     log_milestone("verify_session")    → StepEvent(complete, ...)         ← 圈4 ✓
   end_agent            → AgentEvent(end)
   finalize_summary     → SummaryEvent + scan_end(completed)   ← 前端关流 → getVerifyStatus 落终态
```

前端 EventSource 无 `Last-Event-ID` 时从 `events.ndjson` 文件头 replay（文件刚建、秒级，能 catch 到开头 PhaseEvent）。

---

## 8. 降级与边界

- **agent 偶发不调 `log_milestone`**：步骤条不推进，但下方实时日志照常显示每个 tool call（不再盲）。`scan_end` 时把剩余未完成步标 failed（若验证失败）或由前端兜底标完成。
- **EventTailer 重连/断线**：`useEventSource` 已带 `onerror` 自动重连（带 `Last-Event-ID`）；auth 验证 90–150s，重连可恢复。
- **scan_end 与 Temporal 终态的微小竞态**：`scan_end` 由 `finalize_summary` 写，可能略早于 Temporal 标 terminal；前端 `scan_end` 后调 `getVerifyStatus` 若遇 503（仍 RUNNING）则短暂重试一次。
- **多身份测试**：本次只覆盖单次登录（auth-profiles 页逐凭据测）。若 credential 带 accounts，仍按单次登录展示步骤条（多身份 preflight 属扫描 preflight，走扫描 live 页）。

---

## 9. 部署

改动跨越 blackbox（worker 代码）+ web 前后端 → **须 rebuild worker + web 镜像**才生效（与既有多次改动一致）。

---

## 10. 测试策略（指导 plan）

- **blackbox**：`AuthValidationWorkflow` 编排测试（既有 `test_auth_validation_workflow.py`）扩展——断言 standalone 路径产出 `AgentEvent`/`ToolCallEvent`/`PhaseEvent(4 steps)`/4 个 `StepEvent`/`scan_end`；`log_milestone` handler 单测（发对 StepEvent、无 session 不崩）。
- **core**：里程碑工具双引擎注入对称性测试（仿 collector bridge 测试）；`log_step` 手动发 StepEvent 单测。
- **web 后端**：verify-events SSE 端点测试（tail、scan_end 关流、🔴 probe_dir 路径穿越拒绝）。
- **web 前端**：CredentialRow 改造测试（本次 run 接 SSE、scan_end 后拉终态、紧凑步骤条/日志渲染）；step key→本地化标签映射。
- 仅跑改动相关测试文件（CLAUDE.md 测试陷阱：全套 pytest 有预存挂起/失败）。

---

## 11. 待 plan 确认项

- 🔴 `log_milestone` 工具注入通道的具体接线（并行可选参数 vs 扩 collector 工厂），见 §3.3。
- 🔴 verify-events 端点的 `probe_dir` 安全校验实现细节（与既有 verify-log 校验对齐复用 vs 新写），见 §4。
- DashboardPanel/LogStream 在 credential 行内紧凑布局的具体取舍（是否需要比扫描 live 页更窄的变体），见 §5。
- EventTailer 无 `Last-Event-ID` 从文件头 replay 的行为确认（见 §7），plan 阶段读 `event_tailer.py` 核实。
