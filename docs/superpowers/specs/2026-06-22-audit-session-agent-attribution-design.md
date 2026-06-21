# AuditSession 并发 agent 归因坍缩修复设计

- 日期：2026-06-22
- 分支：`feat/fork-py`
- 状态：设计待审
- 前置 spec：
  - `2026-06-17-provider-agnostic-turn-logging-design.md`（修了 dispatch，让逐轮/工具事件能实时到达 display 管道——本次修的并发归因问题正是它**引入/暴露**的）
  - `2026-06-19-rich-log-visibility-design.md`（放开 phase/turn 前缀 + 状态栏多 agent，其"待真机冒烟"在本次冒烟中暴露了归因坍缩）
- 关系：**真机冒烟的回归**。前两次让逐轮事件"能到达、能显示归因标签"，但当 5 个 vuln agent 真并发时，归因标签坍缩到单个 agent。本 spec 修数据源的并发安全，不动 display 层。

---

## 1. 背景

真跑 `uv run shannon-whitebox start -r <NodeGoat>`，`vulnerability-analysis` 阶段 5 个 vuln agent（injection/xss/auth/ssrf/authz）并发后，live 屏与 `workflow.log` 的 agent 归因彻底错乱。

### 1.1 现象（运行时证据，非推测）

1. **逐轮前缀坍缩**：所有 `💭 [Auth] Turn N` 前缀全标 `[Auth]`，但思考内容明显来自不同 agent（SSRF needle / XSS autoescape / injection SQLi/eval）。`Auth` 是 5 个里第 5 个 started 的。
2. **前缀随 start/end 动态切换**：`03:02:13 XSS attempt-2 重试 start` → 前缀切 `[XSS]`；`03:07:18 Authz Completed(end)` → 前缀切 `[Agent]`（fallback）。
3. **状态栏 turn 计数坍缩**：4 个 agent 永远 `t0`，只有"最近 start 的"agent 的 turn 在涨（`xss t88`，吸收了多 agent 的 turn 事件）。
4. **但扫描结果可信**：每个 agent 的 deliverable 由它自己按 prompt 硬编码路径写入，不经 `AuditSession`，归因正确、内容未污染。

### 1.2 并发为真（时间戳铁证）

```
03:07:18  ✓ Authz      Completed  (11m 0s)
03:09:09  ✓ Injection  Completed  (12m 51s)
03:09:13  ✓ SSRF       Completed  (12m 55s)
```

三者各跑 11~13 分钟，却全部落在 13 分钟窗口内完成；Injection 与 SSRF 仅隔 4 秒完成。`workflows.py` 用 `asyncio.gather` 真并发派发（探索确认）。**这是 display/log 层的归因 bug，不是假并发。**

---

## 2. 根因（精确到行）

`AuditSession` 经 `get_audit_session()`（`session_registry.py:37`）是**进程级全局单例**，5 个并发 Temporal activity 拿到**同一个实例**。它有两个 per-agent 实例字段被并发 agent 互相覆写：

### 2.1 `_current_agent_name`（影响 workflow.log + live 屏归因）

| 行 | 代码 | 问题 |
|---|---|---|
| `session.py:35` | `self._current_agent_name: str \| None = None` | 单一共享字段 |
| `session.py:49` | `self._current_agent_name = agent_name`（`start_agent`） | 5 个并发 agent 互相覆写，最后写入者胜出 |
| `session.py:68,74` | `self._current_agent_name or "unknown"`（`log_event` 的 tool_start/llm_response 分支） | 读共享字段 → 所有 `[LLM]`/`[TOOL]` 行 agent_name 坍缩 |
| `session.py:104` | `self._current_agent_name = None`（`end_agent`） | 一个 agent 结束清空，污染其他在跑 agent 的后续事件 |

下游：`WorkflowLogger.log_tool_start/log_llm_response`（`workflow_logger.py:113,120`）**本就接收 `agent_name` 参数**，坏的是 session 传了共享变量。`agent_prefix()` 查表（`formatters.py`）与 `DashboardState.apply()` 按 `event.agent_name` 路由（`dashboard_state.py:121`）**本身没坏**——坏在上游 `agent_name` 已坍缩。

`log_agent()`（start/end）**不受影响**，因为它直接收 `agent_name` 参数（`session.py:55,97`），不读共享字段。这解释了为何 5 行 running agent 显示正确、`[AGENT] start/end` 归因正确，只有中间的 `[LLM]`/`[TOOL]` 坏。

### 2.2 `_agent_logger`（影响 per-agent JSON 审计日志归因）—— 同一 race，更宽

| 行 | 代码 | 问题 |
|---|---|---|
| `session.py:31` | `self._agent_logger: AgentLogger \| None = None` | 单一共享字段 |
| `session.py:50` | `self._agent_logger = AgentLogger(self._meta, agent_name, attempt)`（`start_agent`） | 并发覆写 |
| `session.py:63-64` | `if self._agent_logger: await self._agent_logger.log_event(...)`（`log_event`） | 用当前共享 logger 写 JSON → injection 的事件可能写进 auth 的 JSON log 文件 |
| `session.py:81-87` | `end_agent` 里 `log_event("agent_end")` + `close()` + 置 `None` | 一个 agent end 可能 close 掉另一个 agent 还在用的 logger |

### 2.3 桥接器不持有身份

`SessionToolAuditLogger.__init__(self, session)`（`session_tool_audit_logger.py:19`）**不持有 agent_name**，所有调用转发给 `session.log_event`，身份完全依赖 session 的共享字段。而 `run_agent`（`activities.py:79,82`）此刻**完全知道** `agent_name.value`——修复点就在此。

### 2.4 为何测试没抓到

探索确认：**全仓库无任何"多 agent 并发"测试**。`test_audit_session.py::test_full_lifecycle`、`test_session_tool_audit_logger.py`、`test_activity_display_wiring.py` 全是单 agent 串行。串行下"最后 start 的"就是"唯一的"，race 永不触发——同款"测试绿、生产坏"（见 memory `provider-agnostic-turn-logging`）。

---

## 3. 目标 / 非目标

### 目标
1. **per-agent 状态下沉**：`agent_name` 与 `AgentLogger` 实例从 `AuditSession` 共享字段移到 per-agent 的 `SessionToolAuditLogger`，让每个并发 agent 的事件自带正确身份。
2. **一次修两个 race**：`_current_agent_name`（workflow/live 归因）+ `_agent_logger`（per-agent JSON 归因）同时根治，不留半修。
3. **whitebox + blackbox 统一**：两边 `SessionToolAuditLogger` 构造点同步改造，避免用法分叉。
4. **并发回归测试**：新增 `asyncio.gather` 多 agent 测试，作为 TDD 锚点（修复前红、修复后绿），堵住测试盲区。

### 非目标
- **不改 display 层**（`rich_renderer` / `dashboard_state` / `live_dashboard` 已正确，本 spec 不碰）。
- **不改 `WorkflowLogger`**（方法本就接收 `agent_name`，没坏）。
- **不改 `MessageDispatcher` / `StreamCollector` / provider 接口**（`SessionToolAuditLogger` 对外方法签名 `log_assistant_turn`/`log_tool_start`/`log_tool_end`/`log_error` 不变，调用方零影响）。
- **不改 `workflow.log` 文件格式**（沿用 `rich-log-visibility` spec 的约束：`[LLM]`/`[TOOL]` 行格式不变，只是 `agent_name` 取值变正确）。
- **不改 dispatch / Temporal 接线 / 事件类型**（`provider-agnostic-turn-logging` 已修 dispatch）。
- **不改 deliverable 落盘**（本就正确）。

---

## 4. 已锁定决策

| 维度 | 决定 |
|---|---|
| per-agent 状态归属 | `SessionToolAuditLogger` 持有 `agent_name` + 自有 `AgentLogger`；`AuditSession` 删 `_current_agent_name`/`_agent_logger`，退化为纯 facade（workflow_logger + metrics + phase/step，这些**本应全局共享**） |
| 桥接器对外接口 | `log_assistant_turn`/`log_tool_start`/`log_tool_end`/`log_error` 签名**不变**（调用方零影响）；只改 `__init__` + 新增 `initialize`/`close` |
| `__init__` 新签名 | `SessionToolAuditLogger(session, agent_name, attempt=1)` |
| workflow 事件路由 | `AuditSession` 暴露 `log_llm_turn(agent_name, turn, content)` / `log_tool_call(agent_name, tool, params)` 两个语义方法，委托 `WorkflowLogger`（取代万能的 `log_event`） |
| `session.log_event` | **删除**（其"同时写 agent JSON + workflow"组合职责已拆分到桥接器）；相关测试迁移到测桥接器 |
| 范围 | core（`SessionToolAuditLogger` + `AuditSession`）+ whitebox 1 处 + blackbox 4 处构造点 + 测试；display/provider 不动 |
| 修复深度 | 两个 race 一次修（既然要动 session，一次修干净） |

---

## 5. 设计

### 5.1 `SessionToolAuditLogger` 持有 per-agent 状态

文件：`packages/core/src/shannon_core/audit/session_tool_audit_logger.py`

```python
class SessionToolAuditLogger(ToolAuditLogger):
    def __init__(self, session: "AuditSession", agent_name: str, attempt: int = 1) -> None:
        self._session = session
        self._agent_name = agent_name
        self._agent_logger = AgentLogger(session._meta, agent_name, attempt)

    async def initialize(self) -> None:
        """写 per-agent JSON log 的 header + agent_start（原 session.start_agent 的职责）。"""
        await self._agent_logger.initialize()

    async def log_tool_start(self, tool_name: str, parameters: Any) -> None:
        await self._agent_logger.log_event("tool_start", {"toolName": tool_name, "parameters": parameters})
        await self._session.log_tool_call(self._agent_name, tool_name, parameters)

    async def log_tool_end(self, result: Any) -> None:
        # tool_end 无 workflow surface，只进 per-agent JSON
        await self._agent_logger.log_event("tool_end", {"result": str(result)[:200]})

    async def log_assistant_turn(self, turn: int, content: str) -> None:
        await self._agent_logger.log_event("llm_response", {"turn": turn, "content": content})
        await self._session.log_llm_turn(self._agent_name, turn, content)

    async def log_error(self, error: str, *, turn_count: int = 0, duration_ms: int = 0) -> None:
        await self._session.log_error(RuntimeError(error), context=f"turn={turn_count}, {duration_ms}ms")

    async def close(self, success: bool, duration_ms: int) -> None:
        """写 agent_end + 关闭 per-agent JSON stream（原 session.end_agent 的职责）。"""
        await self._agent_logger.log_event("agent_end", {"success": success, "duration_ms": duration_ms})
        await self._agent_logger.close()
```

- 每个事件**自带 `self._agent_name`**，不再读 session 共享字段 → 彻底消除 `_current_agent_name` race。
- per-agent JSON 由**自有的 `self._agent_logger`** 写 → 彻底消除 `_agent_logger` race。
- `log_tool_start`/`log_assistant_turn` 同时写 per-agent JSON（自己）+ 推 workflow（经 session 的语义方法）—— 组合职责现在落在 per-agent 实例上，天然隔离。

### 5.2 `AuditSession` 瘦身为纯 facade

文件：`packages/core/src/shannon_core/audit/session.py`

删除字段（`session.py:31,35`）：`_agent_logger`、`_current_agent_name`。

`start_agent`（`session.py:47`）职责收窄——不再创建/持有 AgentLogger：
```python
async def start_agent(self, agent_name: str, prompt: str, attempt: int = 1) -> None:
    await AgentLogger.save_prompt(self._meta, agent_name, prompt)
    if self._workflow_logger:
        await self._workflow_logger.log_agent(agent_name, "start", AgentLogDetails(attempt_number=attempt))
    if self._metrics_tracker:
        self._metrics_tracker.start_agent(agent_name, attempt)
```

`end_agent`（`session.py:79`）职责收窄——不再 close AgentLogger、不清 `_current_agent_name`：
```python
async def end_agent(self, agent_name: str, result: AgentEndResult) -> None:
    if self._workflow_logger:
        details = AgentLogDetails(attempt_number=result.attempt_number, duration_ms=result.duration_ms,
                                  cost_usd=result.cost_usd, success=result.success, error=result.error)
        await self._workflow_logger.log_agent(agent_name, "end", details)
    if self._metrics_tracker:
        async with self._lock:
            await self._metrics_tracker.reload()
            await self._metrics_tracker.end_agent(agent_name, result)
```

新增两个语义方法，取代原 `log_event` 的 llm/tool 分支：
```python
async def log_llm_turn(self, agent_name: str, turn: int, content: str) -> None:
    if self._workflow_logger:
        await self._workflow_logger.log_llm_response(agent_name, turn, content)

async def log_tool_call(self, agent_name: str, tool_name: str, parameters: Any) -> None:
    if self._workflow_logger:
        await self._workflow_logger.log_tool_start(agent_name, tool_name, parameters)
```

**删除** `log_event`（`session.py:61-77`）——其组合职责已拆分。

### 5.3 activity 调用时序

文件：`packages/whitebox/src/shannon_whitebox/pipeline/activities.py`（`run_agent`，行 79-125）

```python
agent_name = AgentName(input.agent_name or input.workspace_name)
attempt = activity.info().attempt
session = get_audit_session()
tool_audit_logger = SessionToolAuditLogger(session, agent_name.value, attempt)   # 新签名
agent_start = time.monotonic()
try:
    ...
    await session.start_agent(agent_name.value, f"agent={agent_name.value}", attempt=attempt)
    await tool_audit_logger.initialize()        # 新：per-agent JSON header + agent_start
    metrics = await executor.execute(..., tool_audit_logger=tool_audit_logger)
    await tool_audit_logger.close(success=True, duration_ms=metrics.duration_ms)  # 新：agent_end + close
    await session.end_agent(agent_name.value, AgentEndResult(success=True, ...))
    return metrics.model_dump()
except PentestError as e:
    duration_ms = int((time.monotonic() - agent_start) * 1000)
    await tool_audit_logger.close(success=False, duration_ms=duration_ms)   # 失败路径也要 close
    await session.end_agent(agent_name.value, AgentEndResult(success=False, ...))
    await session.log_error(e, context=agent_name.value)
    raise ApplicationFailure(...) from e
except Exception as e:
    ...（同上失败路径）
```

- `initialize` / `close` 与 `start_agent` / `end_agent` 配对：session 管 workflow 级（start/end 事件 + metrics），桥接器管 per-agent JSON（agent_start/agent_end + stream 生命周期）。
- **失败路径必须 `close`**（try/except 都要），否则 per-agent JSON stream 泄漏。

### 5.4 blackbox 同步改造

`packages/blackbox/src/shannon_blackbox/pipeline/activities.py` 4 处构造点（`run_blackbox_auth_validation:78`、`run_recon:135`、`run_exploit_agent:191`、`run_report_agent:281`）同样改为 `SessionToolAuditLogger(session, agent_name, attempt)`，并补 `initialize`/`close` 配对调用。blackbox agent 多串行（race 较少触发），但修复零边际成本，且保持两边桥接器用法一致、消除潜在隐患。

---

## 6. 数据流（修复后）

```
claude_agent_sdk query() → AssistantMessage(content=[TextBlock|ToolUseBlock])
  → MessageDispatcher.dispatch (isinstance，provider-agnostic spec 已修)
    → TextBlock:   tool_audit_logger.log_assistant_turn(turn, text)
    → ToolUseBlock: tool_audit_logger.log_tool_start(name, input)
  → SessionToolAuditLogger（per-agent 实例，自带 agent_name + AgentLogger）
    ├─ self._agent_logger.log_event(...)        → 该 agent 自己的 JSON log 文件（归因正确）
    └─ session.log_llm_turn(agent_name, ...)    → WorkflowLogger.log_llm_response(agent_name, ...)
                                                  → LlmTurnEvent(agent_name=正确) → FileLogRenderer([LLM]) + RichConsoleRenderer(💭 [正确前缀]) + DashboardState（turn 计入正确 agent）
```

每个 agent 的事件**从产生那一刻起就携带正确 `agent_name`**，全程无共享可变状态。

---

## 7. 受影响文件

| 文件 | 改动 |
|---|---|
| `packages/core/.../audit/session_tool_audit_logger.py` | `__init__` 加 `agent_name`/`attempt` + 持有 `AgentLogger`；新增 `initialize`/`close`；各 log 方法自带 agent_name |
| `packages/core/.../audit/session.py` | 删 `_current_agent_name`/`_agent_logger`；`start_agent`/`end_agent` 瘦身；新增 `log_llm_turn`/`log_tool_call`；删 `log_event` |
| `packages/whitebox/.../pipeline/activities.py` | `run_agent` 改新签名 + `initialize`/`close` 配对（含失败路径） |
| `packages/blackbox/.../pipeline/activities.py` | 4 处构造点 + `initialize`/`close` 配对 |
| 测试（见 §9） | 5 处桥接器构造点改签名；`test_audit_session.py` 迁移 `log_event` 相关断言；新增并发回归测试 |

**不动**：`WorkflowLogger`、`DashboardState`、`rich_renderer`、`live_dashboard`、`MessageDispatcher`、`StreamCollector`、providers、DisplayEvent、`workflows.py`。

> 注：`packages/whitebox/src/shannon_whitebox/audit/session_tool_audit_logger.py` 是 re-export shim（`from shannon_core.audit.session_tool_audit_logger import *`），无需改。

---

## 8. 错误处理与回退

- **失败路径 close**：`run_agent` 两个 except 分支都调 `tool_audit_logger.close(success=False, ...)`，确保 per-agent JSON stream 不泄漏（原 `end_agent` 隐式 close 的语义保留）。
- **`initialize` 幂等性**：`AgentLogger.initialize` 打开 stream + 写 header；若重复调用会重开 stream。activity 严格 `initialize` 一次，无需额外防护（与原 `start_agent` 调一次的契约一致）。
- **`log_llm_turn`/`log_tool_call` 在 session 未 `initialize` 时**：`self._workflow_logger` 为 `None` 则 no-op（与原 `log_event` 守卫一致）。
- **全部为行为修正**：修 bug（归因正确化）+ 内部重构（职责迁移），无新增 public 行为，无配置/接口变更。回滚 = revert 该 commit。

---

## 9. 测试计划

> 按 memory `pytest-whitebox-hang`：只跑改动相关子集（audit / session / display），**不跑全量**（全量卡 Temporal/网络慢测试）。

### 9.1 并发回归测试（TDD 锚点——核心）

新增测试（放 `packages/whitebox/tests/test_session_tool_audit_logger_concurrency.py`，紧邻现有 `test_session_tool_audit_logger.py`）：

断言走 DisplayEvent 层（不依赖 `[LLM]` 行渲染格式），用一个 recording dispatcher 包住 `WorkflowLogger`，收集 `LlmTurnEvent`/`ToolCallEvent` 按 `agent_name` 分组：

```python
async def test_concurrent_agents_keep_correct_attribution(recording_dispatcher):
    """5 个 agent 并发，各自用自己的 SessionToolAuditLogger。
    修复前：所有 LlmTurnEvent.agent_name 坍缩到最后-start 的 agent。
    修复后：每个 agent 的事件归因正确。"""
    session = AuditSession(meta, use_rich=False, dashboard=None)  # 共享单例，模拟生产
    # 把 session 的 workflow_logger dispatcher 换成 recording_dispatcher（收集事件）
    await session.initialize()
    names = ["injection-vuln", "xss-vuln", "auth-vuln", "ssrf-vuln", "authz-vuln"]

    async def run_one(name):
        lg = SessionToolAuditLogger(session, name, attempt=1)
        await session.start_agent(name, f"prompt-{name}", attempt=1)
        await lg.initialize()
        for turn in range(1, 6):                       # 交错上报，放大 race 窗口
            await lg.log_assistant_turn(turn, f"{name} t{turn}")
            await lg.log_tool_start("Read", {"path": f"{name}.js"})
        await lg.close(success=True, duration_ms=100)
        await session.end_agent(name, AgentEndResult(
            success=True, duration_ms=100, cost_usd=0.0, attempt_number=1))

    await asyncio.gather(*(run_one(n) for n in names))

    # 断言 1：每个 agent 名下各有 5 条 LlmTurnEvent（不坍缩到一个 agent）
    llm_by_agent = group_by_agent_name(recording_dispatcher.llm_turns)
    for name in names:
        assert len(llm_by_agent[name]) == 5
        assert all(ev.content.startswith(name) for ev in llm_by_agent[name])  # 内容与归属一致
    # 断言 2：ToolCallEvent 同理，每 agent 5 条
    tool_by_agent = group_by_agent_name(recording_dispatcher.tool_calls)
    for name in names:
        assert len(tool_by_agent[name]) == 5
```

- per-agent JSON 归因单独一条测试：并发跑完后，解析每个 agent 的 JSONL 文件，断言文件内**所有**事件的 agent 标识（header 的 `Agent:` 行 + `agent_start` 的 `agentName`）等于该文件对应的 agent，且 `llm_response`/`tool_start` 条数与上报数一致。
- **修复前应失败**（5 个 agent 的 `LlmTurnEvent` 全归到一个 `agent_name`，其余 4 个 `len==0`）、**修复后应通过**——这是堵住测试盲区的锚点。
- `recording_dispatcher` 是现有测试套路（见 `test_dashboard_state.py` 风格），不引入新依赖。

### 9.2 现有测试调整

- **5 处桥接器构造点**（`test_session_tool_audit_logger.py:21,32,41`、`test_activity_display_wiring.py:28`、blackbox `test_activity_display_wiring.py:23`）：`SessionToolAuditLogger(session)` → `SessionToolAuditLogger(session, "<agent_name>", 1)`，补 `initialize`/`close` 调用。
- **`test_audit_session.py`**：
  - 删除/迁移 `test_log_event_dispatches_to_both_loggers`、`test_log_event_dispatches_llm_response`（`log_event` 已删）——等价断言迁移到桥接器测试（`log_tool_start`/`log_assistant_turn` 同时落 per-agent JSON + workflow.log）。
  - `test_start_agent_creates_agent_log`、`test_end_agent_writes_agent_end_event`：AgentLogger 创建/close 移到桥接器后，断言点迁移（`start_agent` 不再创建 AgentLogger；改由 `SessionToolAuditLogger.initialize`/`close` 触发）。
  - `test_full_lifecycle`：改为 session + 桥接器协同的完整生命周期。
- **`test_agent_logger.py`**：不动（AgentLogger 本身没改）。
- **display 回归**（`test_rich_renderer`/`test_dashboard_state`/`test_live_dashboard`/`test_file_renderer`）：不动、应全绿（display 层未改）。

### 9.3 单 agent 行为不回归

现有单 agent 场景（串行 start→log→end）修复后行为等价：per-agent JSON 仍含 agent_start/llm/tool/agent_end；workflow.log 仍含 `[AGENT]`/`[LLM]`/`[TOOL]`，格式不变，仅归因从"碰巧正确（因为只有一个）"变为"结构上正确"。

---

## 10. 验收 / 手动冒烟

`uv run shannon-whitebox start -r <repo>`，`vulnerability-analysis` 阶段：

- 逐轮 `💭 [Injection]/[XSS]/[Auth]/[SSRF]/[Authz] Turn N` 前缀**各归各位**，不再全标同一个；
- 状态栏每个并发 agent 的 `tN` **各自递增**，不再 4 个停 `t0`；
- `logs <id> --follow` 的 `[LLM]`/`[TOOL]` 行 agent 归因正确；
- 5 个 agent 仍真并发完成（耗时不回归）；
- 每个 per-agent JSON log 只含该 agent 自己的事件。

> 回填 memory `audit-session-current-agent-race`（标记已修）、`rich-display-visibility-status` 与 `whitebox-display-clarity-redesign`（标记真机冒烟完成）。

---

## 11. 权衡与边界

- **删除 `session.log_event` 是较大的测试面改动**，但它是个"万能路由"（同时写 agent JSON + workflow），修复后这个组合职责天然落在 per-agent 桥接器上，session 保留它只会留下双重写入路径。一次拆干净优于保留兼容壳。
- **`SessionToolAuditLogger` 现在依赖 `session._meta`**（构造 AgentLogger 用）。`_meta` 是 session 的不可变初始化字段（`session.py:27`），并发读安全，非共享可变状态。若后续想彻底解耦，可让 activity 把 `SessionMetadata` 直接传给桥接器，但本次按 YAGNI 不做。
- **不引入 per-agent lock**：修复后桥接器实例之间无共享可变状态（各自持自己的 AgentLogger + agent_name），无需锁。session 的 `_lock` 仍只保护 metrics reload（`session.py:100,164`），职责不变。
