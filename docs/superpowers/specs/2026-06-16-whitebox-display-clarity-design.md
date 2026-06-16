# 白盒扫描显示层清晰度重做 · 设计文档

- **日期**: 2026-06-16
- **分支**: feat/fork-py
- **状态**: 待复审
- **相关**: 受原始项目 `/root/shannon`（TypeScript 版 `start.ts` 的启动信息 + Temporal Web UI 引导）启发

---

## 1. 背景与问题

运行 `shannon-whitebox start -r /root/code/ground_push_prize_web` 时，用户看到的输出令人困惑，"不知道具体步骤"，且不如原始 `/root/shannon` 清晰。逐一定位后，根因都是确定的（已读代码核实）：

### 1.1 `Target: N/A` —— 小 bug
- `cli/main.py:39` 的 `start` 命令只把 `-r` 解析进 `repo_path`，**从不设置 `web_url`**。
- `worker.py:79` 把 `web_url=None` 透传给 `SessionMetadata`。
- `rich_renderer.py:50` banner 用 `target_url or 'N/A'` 渲染 → 白盒扫描（无 web_url）永远是 `N/A`。
- 对比原始 `shannon/apps/cli/src/commands/start.ts:251-252`：`Target: {url ?? '(offline — source code analysis only)'}` + 独立 `Repository: {repoPath}` 行。

### 1.2 "不知道具体步骤" —— 状态条只显示一个 phase 名
- 状态条 `live_dashboard.py:57-65` 只渲染 `{phase} · {completed_count} done · {elapsed} · ${total_cost}`。
- 但 pre-recon 这个 phase 内部有 **8 个单元**（`workflows.py:110-181`：code-index ∥ pre-recon → merge-sinks → entry-point-fusion → adjudication → framework-analysis ∥ frontend-mapping → route-chain-building），状态条全都不展示。
- pre-recon **之前**还有 preflight / credential-check / auth-validation 三个 activity（`workflows.py:53-71`），它们**不发任何显示事件** → 开头 20+ 秒只能干等。
- rich 模式还抑制了 `PHASE` 行（`workflow_logger.py:49` 传 `show_phase=not use_rich`），所以用户连 `Starting pre-recon` 都看不到，只剩 `AGENT ▶ pre-recon started`。

### 1.3 `0 done · $0.0000` 持续数分钟不变 —— 没卡死
- pre-recon 并行跑 `run_code_index`（GitNexus 索引，大仓库数分钟）+ PRE_RECON agent（LLM，timeout 2h）。两者完成前 `completed_count`（`dashboard_state.py:40-41`，只统计 `agents` dict）和 `total_cost` 都不动 → 看着像卡死，实际在干活。

### 1.4 phase 完成无感知
- `log_phase_complete_activity`（`activities.py:141-147`）已定义，但 `workflows.py` 从不调用 → 没有 phase 结束事件。

### 1.5 缺少监控引导
- shannon-py 也跑 Temporal（`docker-compose.yml:12` 绑定 `127.0.0.1:8233:8233 # Web UI (built-in)`），但 CLI 从不告诉用户能开 Web UI，logs 命令也埋到扫描结束才说。原始项目在启动时（`start.ts:265-272`）就打印 Web UI 地址 + logs 命令。

---

## 2. 目标 / 非目标

### 目标
1. 启动信息清晰：banner 显示真实仓库路径、扫描模式、监控入口（Web UI + logs），彻底消除 `Target: N/A`。
2. 进行中可见：状态条显示当前 phase 内的单元进度（`step N/M`）与正在运行的单元名；开头 setup 步骤也可见。
3. phase 生命周期完整：每个 phase 有 start/complete 事件。

### 非目标
- **不改 Temporal workflow 编排顺序**，不改 LLM agent 业务逻辑。
- **不重写事件分发架构**（方案 B 的 `workflow.query` 结构化进度模型，留作未来演进，见 §10）。
- 不新增 Web UI 功能（复用现成 Temporal Web UI）。

---

## 3. 方案选择

| 方案 | 进度数据来源 | 工作量 | 风险 | 取舍 |
|---|---|---|---|---|
| **A 事件补全（采用）** | 在现有事件流里给静默 activity 补发事件 | 中 | 低 | 纯增量，不动控制流，可增量交付，天然演进到 B |
| B 结构化进度模型 | `workflow.query("PipelineProgress")` 轮询 | 大 | 中 | 进度单一权威源、可多消费者复用，但要改控制流/query/轮询 |
| C 借力 Web UI | CLI 只修 banner+引导，细节交浏览器 | 小 | 低 | 最快，但终端本身仍粗 |

**采用 A**：它同时覆盖"启动信息 + 进行中进度"两部分（用户选择"两者都做"），改动集中在事件层 + 各 activity 的少量补发，不触碰 Temporal 编排，风险可控。

---

## 4. 设计

### 4.1 事件生产链路（现状）

```
activity (run_*)
  └─ get_audit_session() → AuditSession            [session.py]
       └─ session.log_phase_start / start_agent / …
            └─ WorkflowLogger.log_phase / log_agent / …   [workflow_logger.py]
                 └─ dispatcher.dispatch(DisplayEvent)
                      ├─ FileLogRenderer      → workflow.log 文件
                      ├─ RichConsoleRenderer  → 滚动行（show_phase=not use_rich）
                      └─ LiveDashboardRenderer → 状态条（rich 模式）
```

所有 `DisplayEvent` 在 `WorkflowLogger` 单点构造；`DashboardState.apply` 是纯不可变状态机。方案 A 的全部改动都挂在这条链路上。

### 4.2 事件层（`events.py`）

新增一个轻量步骤事件，并给 PhaseEvent / WorkflowHeader 扩展字段：

```python
@dataclass(frozen=True)
class StepEvent(DisplayEvent):
    name: str                                  # 单元名，如 "code-index"
    phase: str                                 # 所属 phase，便于跨 phase 归属
    event: Literal["start", "complete"]
    duration_ms: int | None = None
    error: str | None = None

@dataclass(frozen=True)
class PhaseEvent(DisplayEvent):
    phase: str
    event: Literal["start", "complete"]
    steps: tuple[str, ...] = ()                # 新增：该 phase 的单元名清单（start 时带）

@dataclass(frozen=True)
class WorkflowHeader(DisplayEvent):
    workflow_id: str | None
    target_url: str | None                     # 保留（web_url 非空时用）
    repo_path: str | None = None               # 新增
    mode: str | None = None                    # 新增："offline (source code analysis)" | target_url
    web_ui_url: str | None = None              # 新增
    logs_cmd: str | None = None                # 新增
    workspace: str | None = None               # 新增
```

**为何不复用 `AgentEvent`**：code-index / merge-sinks / fusion / adjudication / framework / route-chain 等是确定性计算，不是 LLM agent。混用会污染状态条的 `completed_count`（`dashboard_state.py:40-41`）和 `total_cost`（这两项只该统计真 agent）。新增 `StepEvent` 把"步骤进度"与"agent 统计"分离，统计干净。

### 4.3 生产层

**`workflow_logger.py`**
- `initialize()`：构造 `WorkflowHeader` 时填入新字段（`repo_path=self._meta.repo_path`，`mode` 由 `web_url` 推导，`web_ui_url`/`logs_cmd`/`workspace` 计算，见 §4.5）。
- `log_phase(phase, event, steps=())`：新增可选 `steps` 参数，透传进 `PhaseEvent`。
- 新增 `log_step(name, phase, event, duration_ms=None, error=None)` → `dispatch(StepEvent(...))`。

**`session.py`（AuditSession 门面）**
- `log_phase_start(phase, steps=())` → `workflow_logger.log_phase(phase, "start", steps)`。
- 新增 `log_step(name, phase, event, duration_ms=None, error=None)` → `workflow_logger.log_step(...)`。
- 新增 async context manager `track_step(phase, name)`：`__aenter__` 发 `start`，`__aexit__` 在 `finally` 发 `complete(duration_ms, error)`——**保证异常路径也必发 complete**，状态条不会卡在 running。

**`activities.py`**
- 确定性 activity（`run_code_index` / `run_merge_sink_reports` / `run_entry_point_fusion` / `run_save_adjudication` / `run_framework_analysis` / `run_frontend_mapping` / `run_route_chain_building` / `run_risk_scoring` / `run_render_dataflow_hints` / `run_attack_chain_assembly` / `render_findings`）：用 `async with get_audit_session().track_step(phase, "<name>"):` 包裹核心逻辑。phase 由所属 workflow phase 决定。
- 早期 activity（`run_preflight` / `run_credential_check` / `run_auth_validation`）：同样包 `track_step("setup", "<name>")`。
- `log_phase_start_activity(input, steps=None)`：新增可选 `steps: list[str] | None = None` 参数 → `session.log_phase_start(phase, steps or [])`。`phase` 仍取自 `input.workspace_name`（保持向后兼容，不破坏现有调用签名）。

**`workflows.py`**
- preflight/credential/auth 三步前包 setup phase：`log_phase_start_activity(workspace_name="setup", steps=["preflight","credential-check","auth-validation"])`，结尾 `log_phase_complete_activity`。
- 各大 phase 的 `log_phase_start_activity` 携带 `steps` 清单：
  - `pre-recon` → `["code-index","pre-recon","merge-sinks","entry-point-fusion","adjudication","framework-analysis","frontend-mapping","route-chain-building"]`
  - `recon` → `["recon"]`
  - `risk-scoring`（新增 phase 声明）→ `["risk-scoring","dataflow-hints"]`
  - `vulnerability-analysis` → `[f"{vt}-vuln" for vt in selected_classes]`
  - `attack-chain`（新增 phase 声明）→ `["attack-chain-assembly"]`
  - `reporting` → `["render-findings"]`
- 每个 phase 结尾补 `log_phase_complete_activity`。

### 4.4 显示层

**`dashboard_state.py`**
新增字段统一跟踪 phase 内"单元"（step 与 agent 同等计入进度）：

```python
@dataclass(frozen=True)
class DashboardState:
    current_phase: str | None = None
    agents: dict[str, AgentRow] = field(default_factory=dict)
    phase_units: tuple[str, ...] = ()                 # 新增：来自 PhaseEvent.steps
    unit_status: dict[str, str] = field(default_factory=dict)  # 新增：name -> running|done|failed
```

`apply` 扩展：
- `PhaseEvent(event="start")`：`replace(current_phase=event.phase, phase_units=event.steps, unit_status={})`（新 phase 重置）。`event.steps` 为空（兼容旧 phase 无清单）时不重置 `phase_units`，状态条回退为只显示 phase 名。
- `PhaseEvent(event="complete")`：仅记录，不改 unit（可选保留一个 `phase_completed` 标记）。
- `StepEvent`：`unit_status[name] = running|done|failed`。
- `AgentEvent`：**保留**现有 `agents` dict 更新（cost/统计不变）；若 `agent_name in phase_units`，同步 `unit_status[agent_name] = running|done|failed`，使 LLM agent 也推进进度。

新增 property：`completed_units`（done|failed 计数）、`total_units`（`len(phase_units)`）、`running_units`（status=="running" 的 name 列表）。

> 注：`completed_count` / `total_cost` 保持只算 `agents` dict——StepEvent 不污染这两项，agent 统计语义不变。

**`live_dashboard.py` `_render`** 状态条新布局：

```
{phase} · step {completed_units}/{total_units} · {running_units} ⠹ · {elapsed} · ${total_cost}
```

- 无 `phase_units`（旧 phase）时回退为 ` · {completed_count} done `。
- 示例：`pre-recon · step 2/8 · code-index ∥ pre-recon ⠹ · 4m49s · $0.0120`

**`rich_renderer.py`**
- `_render_header`：改用 `WorkflowHeader` 新字段渲染（见 §4.5 的 banner 布局）。
- `render` match 分支新增 `case StepEvent(): self._render_step(event)`，rich 模式下打印一行步骤开始/完成（与 `show_phase` 同开关，避免噪音）。非 rich 模式（CI/pipe）每步一行，便于追溯。
- `file_renderer.py`（FileLogRenderer）：新增 `StepEvent` 分支，写入 workflow.log，保证持久化日志含步骤。

### 4.5 banner 与 worker 时序

**banner 新布局**（`rich_renderer.py` `_render_header`）：

```
╭─ Shannon Pentest ──────────────────────────────╮
│ Repository: /root/code/ground_push_prize_web     │
│ Mode:      offline (source code analysis)        │   # web_url 非空时显示 url
│ Started:   2026-06-16 13:49:44                   │
│                                                  │
│ Monitor:   Web UI http://localhost:8233/         │
│                namespaces/default/workflows/<id> │
│            Logs  shannon-whitebox logs <ws>      │
│                --follow                          │
╰──────────────────────────────────────────────────╯
```

`web_url` 为空时**不显示 Target 行**（彻底消除 `N/A` 困惑）。

**workflow_id 时序修正（`worker.py`）**

当前 `meta.id = input.workspace_name or "whitebox-scan"`（`worker.py:78`），但真实 Temporal workflow id 是 `input.workspace_name or f"whitebox-{ts}"`（`worker.py:98`）。二者在 `workspace_name=None` 时不一致 → banner 的 Web UI URL 会指向不存在的 workflow。

修正：在 `run_with_display` 之前**统一计算** `workflow_id`，用于 `meta.id`、`session.initialize(workflow_id=...)` 和 `client.start_workflow(..., id=workflow_id)` 三处。banner 的 `web_ui_url` 由该真实 id 拼接：
`http://localhost:{WEB_UI_PORT}/namespaces/default/workflows/{workflow_id}`，`WEB_UI_PORT` 默认 `8233`（与 `docker-compose.yml:12` 一致）。

`logs_cmd` = `shannon-whitebox logs {workspace} --follow`，`workspace` 取 `input.workspace_name or workflow_id`。

### 4.6 完整事件流（权威，可回放）

```
WorkflowHeader(repo_path, mode=offline, web_ui_url, logs_cmd, workspace)
PhaseEvent("setup", start, steps=[preflight, credential-check, auth-validation])
  StepEvent(preflight, start→complete)
  StepEvent(credential-check, start→complete)
  StepEvent(auth-validation, start→complete)
PhaseEvent("setup", complete)
PhaseEvent("pre-recon", start, steps=[code-index, pre-recon, merge-sinks,
        entry-point-fusion, adjudication, framework-analysis,
        frontend-mapping, route-chain-building])
  StepEvent(code-index) ∥ AgentEvent(pre-recon)        # 并行
  StepEvent(merge-sinks)
  StepEvent(entry-point-fusion)
  StepEvent(adjudication)
  StepEvent(framework-analysis) ∥ StepEvent(frontend-mapping)
  StepEvent(route-chain-building)
PhaseEvent("pre-recon", complete)
PhaseEvent("recon", start, steps=[recon]) → AgentEvent(recon) → PhaseEvent("recon", complete)
PhaseEvent("risk-scoring", steps=[risk-scoring, dataflow-hints]) → …
PhaseEvent("vulnerability-analysis", steps=[<5 vuln agents>])
  AgentEvent ×5（并行） → PhaseEvent(complete)
PhaseEvent("attack-chain", steps=[attack-chain-assembly]) → …
PhaseEvent("reporting", steps=[render-findings]) → SummaryEvent
```

### 4.7 单元名映射（unit name ↔ activity ↔ phase）

实现时 `PhaseEvent.steps` 清单、`track_step(phase, name)` 的 `name`、`AgentEvent.agent_name` 必须三处一致，否则状态条 N/M 与 running 名对不上。统一映射：

| activity 函数 | phase | unit name（事件名） | 事件类型 |
|---|---|---|---|
| `run_preflight` | setup | `preflight` | StepEvent |
| `run_credential_check` | setup | `credential-check` | StepEvent |
| `run_auth_validation` | setup | `auth-validation` | StepEvent |
| `run_code_index` | pre-recon | `code-index` | StepEvent |
| `run_agent`(PRE_RECON) | pre-recon | `pre-recon` | AgentEvent |
| `run_merge_sink_reports` | pre-recon | `merge-sinks` | StepEvent |
| `run_entry_point_fusion` | pre-recon | `entry-point-fusion` | StepEvent |
| `run_save_adjudication` | pre-recon | `adjudication` | StepEvent |
| `run_framework_analysis` | pre-recon | `framework-analysis` | StepEvent |
| `run_frontend_mapping` | pre-recon | `frontend-mapping` | StepEvent |
| `run_route_chain_building` | pre-recon | `route-chain-building` | StepEvent |
| `run_agent`(RECON) | recon | `recon` | AgentEvent |
| `run_risk_scoring` | risk-scoring | `risk-scoring` | StepEvent |
| `run_render_dataflow_hints` | risk-scoring | `dataflow-hints` | StepEvent |
| `run_vuln_agent` | vulnerability-analysis | `{vt}-vuln` | AgentEvent |
| `run_attack_chain_assembly` | attack-chain | `attack-chain-assembly` | StepEvent |
| `render_findings` | reporting | `render-findings` | StepEvent |

每个 phase 的 `steps` 清单即取该 phase 下所有行的 unit name。AgentEvent 行的 `agent_name` 直接等于 unit name，故 §4.4 的"agent_name ∈ phase_units → 同步 unit_status"成立。

> 注：`risk-scoring` 与 `attack-chain` 两个 phase 当前在 `workflows.py` 中**没有** `log_phase_start_activity` 调用，本期需新增声明（含 start/complete），否则它们的单元无法归入任何 phase。

---

## 5. 错误处理

- `track_step` 用 `try/finally`（context manager 的 `__aexit__`）：即使 activity 抛错也必发 `StepEvent(complete, error=...)`，状态条 `unit_status` 标 failed，不会卡在 running。
- 现有 `ErrorEvent`（`workflow_logger.log_error`）与 `AgentEvent(success=False)` 机制不变。
- `run_attack_chain_assembly` 在 workflow 中本就是非致命 `try/except`（`workflows.py:252-260`）：`track_step` 标 failed + re-raise，workflow 的 except 照常吞掉，不影响整体流程。

---

## 6. 测试策略

事件为纯数据、状态机为纯函数，便于单测：

- **`DashboardState`**：`apply(PhaseEvent start with steps)` 后 `phase_units` 正确；连续 `apply(StepEvent start/complete)` 与 `apply(AgentEvent)` 的 `unit_status` 转移、`completed_units`/`total_units`/`running_units` 计数正确；replay §4.6 整条事件序列，断言 pre-recon 阶段中段 snapshot 为 `step 2/8`。回退路径：`steps=()` 时状态条不显示 `N/M`。
- **renderer**：给定 snapshot，断言状态条文本含 `step 2/8` + running 单元名；给定 `WorkflowHeader(repo_path=..., target_url=None)`，断言渲染含 `Repository:` 与 `Mode: offline`、含 `Monitor:`、**不含** `Target: N/A`；`target_url` 非空时含该 url。
- **activity**：mock `get_audit_session()`，断言 `run_code_index` 等入口/出口各发 `StepEvent(start/complete)`，且在抛 `ApplicationFailure` 的异常路径下 `complete(error=...)` 仍被发出（finally 生效）。
- **workflow_logger**：`log_phase(phase, "start", steps=[...])` 派发的 `PhaseEvent.steps` 正确透传；`log_step` 派发 `StepEvent`。
- **避开已知挂起的集成测试**（feat/fork-py 现状：`test_worker_progress` / CLI `follow` / `test_audit_injection` / integration 挂起）。新增测试聚焦纯函数与单 activity，广跑用 `--ignore` 隔离。

---

## 7. 增量交付

1. **阶段 1 — banner + Monitor 引导**：`events.WorkflowHeader` 扩展 + `rich_renderer._render_header` + `worker.py` workflow_id 统一 + Web UI/logs 引导。立即消除 `N/A`，最低风险。
2. **阶段 2 — 状态条 step N/M**：`DashboardState` 扩展（phase_units/unit_status）+ `live_dashboard._render`。可先用现有事件验证渲染。
3. **阶段 3 — 补全事件**：`StepEvent` + `PhaseEvent.steps` + `track_step` + 各 activity/workflow 补发 + phase complete + setup phase。

每阶段独立可测、可提交。

---

## 8. 改动文件清单

| 层 | 文件 | 改动 |
|---|---|---|
| 事件 | `packages/core/src/shannon_core/display/events.py` | +`StepEvent`；`PhaseEvent.steps`；`WorkflowHeader` 字段 |
| 生产 | `…/audit/workflow_logger.py` | `log_phase(+steps)`；+`log_step`；`initialize` 填 banner |
| 生产 | `…/audit/session.py` | `log_phase_start(+steps)`；+`log_step`；+`track_step` |
| 生产 | `…/whitebox/pipeline/activities.py` | 各确定性/早期 activity 包 `track_step`；`log_phase_start_activity(+steps)` |
| 生产 | `…/whitebox/pipeline/workflows.py` | setup phase + 各 phase steps 清单 + phase complete |
| 运行时 | `…/whitebox/worker.py` | 统一 `workflow_id`，传 banner 字段 |
| 显示 | `…/display/dashboard_state.py` | `phase_units`/`unit_status`；`apply(StepEvent)`；AgentEvent 同步 unit |
| 显示 | `…/display/live_dashboard.py` | 状态条 `step N/M · running units` |
| 显示 | `…/display/rich_renderer.py` | `_render_header` 新布局；`_render_step` |
| 显示 | `…/display/file_renderer.py` | `StepEvent` 写 workflow.log |

---

## 9. 风险与权衡

- **`PhaseEvent.steps` 向后兼容**：新增字段默认 `()`，旧代码不传也安全；`DashboardState` 在 `steps` 为空时回退到原 `completed_count done` 展示。
- **agent 计入 unit 进度**：pre-recon 的 LLM agent 是大头单元，必须计入否则进度卡住；通过"agent_name ∈ phase_units 时同步 unit_status"实现，不改 `agents` dict 的 cost 语义。
- **Temporal activity 参数序列化**：`log_phase_start_activity(input, steps)` 的 `steps: list[str]` 可序列化，安全。
- **Web UI 端口假设**：默认 8233（`docker-compose.yml` 固定）。若部署改动端口，banner URL 需随之调整——可通过环境变量 `TEMPORAL_WEB_UI_PORT` 覆盖（实现期决定是否引入）。
- **workflow_id 时序**：统一计算点要保证 `start_workflow` 与 `meta.id` 用同一字符串，避免 Web UI 跳转落空。

---

## 10. 未来演进（非本期）

方案 B（结构化进度模型）：把 `PipelineProgress`（`shared.py:48` 已有雏形 + `workflows.py:295` 已有 `@workflow.query("PipelineProgress")`）扩充为 `phase → [unit, status]` 权威状态，CLI 轮询 query 渲染。方案 A 的 `StepEvent` 序列天然能驱动该模型——届时 dashboard 既可吃事件也可吃 query，平滑迁移。本期不做。
