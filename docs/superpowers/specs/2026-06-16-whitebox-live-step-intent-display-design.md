# 白盒 Live 显示：对齐原版的逐轮过程 + 步骤意图

- 日期：2026-06-16
- 分支：`feat/fork-py`
- 状态：设计待审（v2，含对原版对比的纠错）
- 前置 spec：`2026-06-16-whitebox-display-clarity-design.md`、`2026-06-16-rich-display-layout-fix-design.md`、`2026-06-10-realtime-console-logging-design.md`
- 关系：`whitebox-display-clarity-design` 的增量后续；**显式反转**其在 rich 模式抑制 `STEP` 行的门控决策（见 §10）。

---

## 1. 背景与问题（含对原版对比的纠错）

### 1.1 原版 `/root/shannon` 实际展示了什么

原版（TypeScript）跑 `./shannon start -r <repo>` 时，pre-recon 阶段会**逐 assistant 轮**打印模型的当轮文本：

```
[INFO] Running Claude Code: pre-recon...
    Turn 2 (pre-recon):   🔄 Check git status and understand repository structure
    Turn 33 (pre-recon):  🔄 Read server/app/router.ts - map all routes
    Turn 92 (pre-recon):  ✅ Read auth middleware (passportExpires.ts)
    Turn 452 (pre-recon): ✅ Save deliverable with save-deliverable CLI tool
```

机制：`claude-executor.ts:370` 遍历 Claude SDK 的 `query()` 流，每个 `assistant` 消息 `turnCount++`（`:379-381`），经 `formatAssistantOutput`（`output-formatters.ts:279`）把**该轮 assistant 清洗后文本**打成 `Turn N (agent): <文本>`。原版**不单列工具调用**——靠模型自己在轮文本里口述（"🔄 Read X"），所以滚屏天然平静（每轮一行、轮间隔数秒）。

> **纠错**：本 spec 的 v1 曾基于一次不完整探查（只看到 `progress-indicator.ts` 那个 `\r⠋ Working...` 傻 spinner）断言"原版更简单、用户前提是反的"。**那是错的。** 用户贴出的原版实跑输出证明原版过程信息远比 shannon-py 当前丰富。用户一直是对的。

### 1.2 shannon-py 其实已产出等价数据，只是没渲染好

把 shannon-py 对应链路追到底，**逐轮文本是完整产出且会流到 live 屏的**：

```
SDK 流式 query()
 → message_dispatcher._handle_assistant (message_dispatcher.py:69-80)
     每个 assistant 轮：turn_count++，拼 turn_text
     → audit_logger.log_assistant_turn(turn, turn_text)
 → SessionToolAuditLogger.log_assistant_turn (session_tool_audit_logger.py:31-33)
     → session.log_event("llm_response", {turn, content})
 → workflow_logger.log_llm_response → LlmTurnEvent
 → RichConsoleRenderer._render_llm → 💭 Turn N: 内容
```

provider 侧也透传到位（`providers_anthropic.py:279` 建 `MessageDispatcher(audit_logger=…)`、`:282` `async for event in query()`）。所以 `💭 Turn N: …` 就是原版 `Turn N (agent): …` 的等价物。

### 1.3 真正的缺口（全是渲染侧 + 文案，非埋点）

逐轮数据既有，问题在于**没渲染好 + 杂讯 + 没钉住**：

1. **`💭` 被暴力砍到 200 字符**（`rich_renderer.py:118` `content[:200]+"..."`），原版打整段清洗文本。
2. **`🔧` 工具调用行喧宾夺主**：shannon-py 额外打 `🔧 read_file(...)`，原版根本不单列（靠模型口述）。这些行最吵，是"滚太快"的主因。
3. **`STEP` 行在 rich 模式被压制**：`workflow_logger.py:63` `show_phase=not use_rich`，`rich_renderer.py:41` 的 `_render_step` 与 phase 共用此开关 → 确定性步骤（`code-index` 等 7 步不走 Agent SDK、无 💭/🔧）几分钟零反馈。
4. **没钉住"当前状态"**：`dashboard_state.py:108-118` 已追踪 `last_action_detail`/`turn`，但 `live_dashboard.py` 从不渲染 → 轮文本一旦滚走，"此刻在干嘛"就离屏。
5. **step 名是 slug**（`code-index`/`adjudication`…），无人类意图；`StepEvent` 也无意图字段。
6. **刷新过快**：`refresh_per_second=10`（`display_lifecycle.py:30`）。

---

## 2. 目标 / 非目标

### 目标

1. **对齐原版的逐轮过程视图**：pre-recon 等 agent 阶段每轮 assistant 文本以**可读、不截断**的 `💭 Turn N: <首行>` 滚出（每轮一行，天然平静）。
2. **确定性子步骤有反馈**：每个 `track_step` 步骤开始/完成滚出**带中文意图**的 `STEP` 行。
3. **live 屏隐藏 `🔧`**：工具调用不单列（仍写入 `workflow.log`，`logs --follow` 可见）——消除"滚太快"主因，贴合原版。
4. **钉住当前状态**：底部状态行多一行，常驻显示"当前步骤意图 + 最新一轮文本"，关键信息不随滚屏丢失。
5. **刷新平稳**：`refresh_per_second` 10 → 3。
6. 全部 phase 覆盖；pre-recon 作首批重点。

### 非目标

- **不做节流/合并**：一旦隐藏 `🔧`、只留每轮 💭，滚屏天然平静，无需节流机制（v1 讨论中的"节流"已废弃，见 §10）。
- **不做持久活动面板**（用户已选"滚动追加 + 钉住状态行"）。
- **不做子步骤进度条 / 百分比 / ETA**。
- **不改 Agent SDK / Temporal 接线 / 不引入 `workflow.query` 结构化进度**（`clarity-design §10` 未来演进）。
- **不新增 `PHASE` 行**（rich 模式仍压住，phase 名由状态行承载）。
- **不动黑盒**。

---

## 3. 已锁定决策

| 维度 | 决定 |
|---|---|
| 过程主信号 | **逐轮 💭 文本**（可读渲染、永不节流）——原版 `Turn N (agent):` 的等价物 |
| 工具调用 🔧 | **live 屏隐藏**（仅写入 `workflow.log`） |
| 确定性步骤 | 每步滚 `STEP ▸ 中文意图` / `STEP ✓ slug (耗时)` |
| 信息留存 | 底部**钉两行**：状态行 + 当前步骤意图 & 最新一轮文本 |
| 滚屏节奏 | 不节流（隐藏 🔧 + 每轮一行即平静） |
| 刷新 | `refresh_per_second` 10 → 3（可配） |
| 范围 | 全部 phase；pre-recon 作首批重点 |
| 意图挂载 | `StepEvent` 新增可选 `intent`，whitebox 侧解析随事件走（方案 B，无 core→whitebox 依赖） |
| 埋点 | **无需新埋点**——逐轮链路已就绪（§1.2） |

---

## 4. 设计

### 4.1 步骤意图注册表（单一真相源）

`packages/whitebox/.../pipeline/workflows.py` 的 `PHASE_STEPS` 从纯 slug 元组升级为 `StepSpec{name, intent}`，一处定义、两处消费（phase-start 的 step 列表 + track_step 的意图）：

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class StepSpec:
    name: str
    intent: str

PHASE_STEPS: dict[str, tuple[StepSpec, ...]] = {
    "setup": (
        StepSpec("preflight",          "预检（环境 / 依赖就绪性）"),
        StepSpec("credential-check",   "校验 API 凭证"),
        StepSpec("auth-validation",    "验证目标鉴权链路"),
    ),
    "pre-recon": (
        StepSpec("code-index",         "构建调用图与代码索引"),
        StepSpec("pre-recon",          "扫描应用架构、入口点与 sink"),
        StepSpec("merge-sinks",        "合并确定性 sink 与 LLM 发现"),
        StepSpec("entry-point-fusion", "融合确定性入口点与 LLM 发现"),
        StepSpec("adjudication",       "按置信度裁决入口点"),
        StepSpec("framework-analysis", "检测 REST 框架并推断端点"),
        StepSpec("frontend-mapping",   "映射前端路由到 API、识别 XSS 链"),
        StepSpec("route-chain-building","构建攻击路由链"),
    ),
    "recon":        (StepSpec("recon", "侦察目标运行时与外部信息"),),
    "risk-scoring": (StepSpec("risk-scoring", "打分与风险排序"),
                     StepSpec("dataflow-hints", "生成数据流提示")),
    "attack-chain": (StepSpec("attack-chain-assembly", "组装攻击链"),),
    "reporting":    (StepSpec("render-findings", "渲染最终报告"),),
}

def step_names(phase: str) -> tuple[str, ...]:
    return tuple(s.name for s in PHASE_STEPS[phase])

_INTENT_BY_NAME = {s.name: s.intent for specs in PHASE_STEPS.values() for s in specs}

def intent_for(name: str) -> str | None:
    return _INTENT_BY_NAME.get(name)
```

- `intent_for` 只服务 `track_step` 站点。动态 `{vt}-vuln` 走 `run_agent`（AGENT 事件、不经 track_step），不需进表；漏配一律回退 slug。
- 文案为草案，可在实现期微调；不变式："每条声明的 step 都有意图"（见 §8）。
- 现有 `list(PHASE_STEPS[phase])` 消费点改用 `step_names(phase)`。

### 4.2 `StepEvent.intent`（方案 B，加性、向后兼容）

- `packages/core/.../display/events.py`：`StepEvent` 增 `intent: str | None = None`。
- `packages/core/.../audit/workflow_logger.py`：`log_step(...)` 增 `intent: str | None = None`，写入 `StepEvent(intent=intent)`。
- `packages/core/.../audit/session.py`：`log_step` / `track_step` 各增 `intent` 透传；start 与 complete 两次都带。

### 4.3 解耦 step / phase 门控（反转 clarity-design 的抑制决策）

`packages/core/.../display/rich_renderer.py`：

- `__init__` 在 `show_phase` 外新增 `show_steps: bool = True`。
- `render()`：`PhaseEvent` 仍受 `show_phase` 门控；`StepEvent` 改受 `show_steps` 门控（不再共用 `show_phase`）。
- `workflow_logger.py:63` 构造改为：
  ```python
  renderers.append(RichConsoleRenderer(
      self._console, show_phase=not self._use_rich, show_steps=True))
  ```
  rich → `show_phase=False`（仍压 `PHASE` 行）、`show_steps=True`（**放开 `STEP` 行**）；非 rich → 两者皆 `True`（无回归）。

> 显式 supersede `whitebox-display-clarity-design` 第 175 行"step 与 show_phase 同开关、rich 下抑制以避免噪音"——用户反馈已从"怕噪音"转为"要看到过程"。

### 4.4 live 屏隐藏 `🔧`（消除"滚太快"主因）

- `rich_renderer.py` 新增 `show_tools: bool = True` 开关；`_render_tool` 受其门控。
- `workflow_logger.py:63` 构造再传 `show_tools=not self._use_rich`：
  rich → `show_tools=False`（**live 屏不显示 🔧**）；非 rich / CI / pipe → `True`（行为不变）。
- **`FileLogRenderer` 永远渲染 `🔧`**（不受影响）→ `logs --follow` 仍可见全部工具调用，**无信息丢失**。

### 4.5 可读的逐轮 💭（不再 200 字符截断）

`rich_renderer._render_llm`（现 `content[:200]+"..."`）改为渲染**首条非空行**（trim、按终端宽度收口），整段文本仍由 `FileLogRenderer` 保留：

```python
def _render_llm(self, e) -> None:
    line = _first_nonempty_line(e.content) or "(无文本)"
    self._console.print(
        f"[{e.timestamp}] [magenta]💭 Turn {e.turn}: {line}[/]", highlight=False)
```

- 轮文本常是单行（"🔄 Read X"），首行即可承载意图；多行轮取首行。
- **全文不丢**：`session.log_event` 会把完整 `llm_response` 写进 per-agent JSON 日志（`agent_logger`），与渲染无关。`FileLogRenderer._llm` 现行 `[:200]` 截断可顺带放宽为首行（实现期定），`logs --follow` 可读性同步提升。
- 不节流：每轮一条，天然平静。

### 4.6 钉住当前状态（两行 dashboard）

`live_dashboard.py` 在现有状态行下**多渲染一行**，常驻"当前步骤意图 + 最新一轮文本"：

```
pre-recon · step 2/8 · 2m 11s · $0.0012
⠋ 扫描架构与入口点 · Turn 33: 🔄 Read server/app/router.ts
```

- 数据来源（**均已被追踪、只是没渲染**）：
  - 当前运行步骤：`DashboardState.running_units`；其意图：需 `DashboardState` 记录 `unit_intent[name]`（由 `StepEvent.intent` 写入）。
  - 最新轮文本：`AgentRow` 现仅存 `turn`（`dashboard_state.py:113-118`），增 `last_turn_text`（由 `LlmTurnEvent.content` 写入首行）。
- 渲染规则：有 agent 在跑 → `⠋ <步骤意图> · Turn N: <最新轮首行>`；仅确定性步骤在跑（无 agent）→ `⠋ <步骤意图>`。
- 效果：即使上方滚屏，"此刻在干嘛"永远钉在底部。

### 4.7 刷新节流

`display_lifecycle.py:30`：`refresh_per_second=10 → 3`（可经 `SHANNON_LIVE_REFRESH_HZ` 覆盖）。

---

## 5. 数据流（无新埋点）

```
逐轮文本(原版等价):
  SDK query() → MessageDispatcher._handle_assistant → log_assistant_turn
    → SessionToolAuditLogger → session.log_event("llm_response")
    → LlmTurnEvent → FileLogRenderer._llm(全文) + RichConsoleRenderer._render_llm(首行, 永不节流)
    → 同时更新 DashboardState.last_turn_text → 钉住行

步骤意图:
  whitebox track_step(phase,name, intent=intent_for(name))
    → StepEvent(intent=…) → FileLogRenderer._step + RichConsoleRenderer._render_step(show_steps=True)
    → 同时更新 DashboardState.unit_intent[name] → 钉住行

工具调用 🔧:
  MessageDispatcher._handle_tool_use → log_tool_start → ToolCallEvent
    → FileLogRenderer._tool(写入 workflow.log) ; RichConsoleRenderer._render_step 受 show_tools=False 门控 → live 不显示
```

---

## 6. 受影响文件清单

| 文件 | 改动 |
|---|---|
| `packages/whitebox/.../pipeline/workflows.py` | `PHASE_STEPS`→`StepSpec`；新增 `step_names()`/`intent_for()`；消费点改用 `step_names()` |
| `packages/whitebox/.../pipeline/activities.py` | 14 处 `track_step` 透传 `intent=intent_for(name)` |
| `packages/core/.../display/events.py` | `StepEvent` 增 `intent: str \| None = None` |
| `packages/core/.../audit/workflow_logger.py` | `log_step` 增 `intent`；构造 renderer 传 `show_steps=True, show_tools=not use_rich` |
| `packages/core/.../audit/session.py` | `log_step`/`track_step` 增 `intent` 透传 |
| `packages/core/.../display/rich_renderer.py` | 新增 `show_steps`/`show_tools` 开关；`_render_step` 用意图；`_render_llm` 渲染首行；`_render_tool` 受 `show_tools` 门控 |
| `packages/core/.../display/file_renderer.py` | `_step` 吃 `intent`；`_llm` 可顺带放宽 `[:200]` 截断为首行（`_tool` 行为不变） |
| `packages/core/.../display/dashboard_state.py` | `StepEvent` 写 `unit_intent`；`LlmTurnEvent` 写 `last_turn_text`；`AgentRow` 增 `last_turn_text` |
| `packages/core/.../display/live_dashboard.py` | 多渲染一行钉住的"步骤意图 + 最新轮" |
| `packages/core/.../audit/display_lifecycle.py` | `refresh_per_second` 10 → 3（可配） |
| `packages/core/tests/display/*`、`packages/whitebox/tests/...` | 见 §8 |

---

## 7. 错误处理与回退

- **意图 miss / 无文本轮**：`intent_for`→`None` → 渲染回退 slug；`_first_nonempty_line`→空 → 显示 `(无文本)`。不崩。
- **纯工具轮（无 assistant 文本）**：`_handle_assistant` 的 `if turn_text:` 守卫使该轮不发 💭；live 又隐藏 🔧 → 该轮 live 无新行，但钉住行仍显示上一轮/当前步骤意图，不会全黑。用户已知并接受此取舍。
- **`track_step` 异常路径**：已有 try/finally 保 complete；`intent` 搭车不改语义。
- **回滚**：全部加性/向后兼容；开关默认值不破坏非 rich 调用方；`intent` 默认 `None` 不破坏现有测试。

---

## 8. 测试计划

1. **事件 schema**（`test_events.py`）：`StepEvent` 可带 `intent`，默认 `None`。
2. **`DashboardState.apply`**（`test_dashboard_state.py`）：`StepEvent.intent` 写入 `unit_intent`；`LlmTurnEvent` 写入 `last_turn_text`；均不扰动 `unit_status`/计数既有断言。
3. **`RichConsoleRenderer`**（`test_rich_renderer.py`）：
   - rich 门控组合：`show_phase=False, show_steps=True, show_tools=False` → `PHASE` 压住、`STEP` 放开带意图、`🔧` 压住、`💭` 放开且渲染首行。
   - `intent=None` 回退 slug；`_render_llm` 多行取首行。
4. **`FileLogRenderer`**（`test_file_renderer.py`）：`_step` 带意图；`_llm`/`_tool` 不受 rich 开关影响（全文/工具行照写）。
5. **`track_step`/`log_step` 透传**（audit 相关）：`intent` 一路到 `StepEvent`。
6. **防漂移**（whitebox 新增）：`PHASE_STEPS` 每条 `StepSpec.name` 都能被 `intent_for` 命中。
7. **门控回归**：非 rich（`show_phase=show_steps=show_tools=True`）与现状一致。
8. **钉住行**（`test_live_dashboard.py`）：agent 运行时第二行含步骤意图 + 最新轮；仅确定性步骤时只含步骤意图。

> 广跑注意：按 `feat-fork-py-test-gotchas` memory，全套 pytest 有预存挂起（`test_worker_progress`/`test_cli follow`/`test_audit_injection`/integration），广跑用 `--ignore`；display 单测目录可单独跑。

---

## 9. 验收 / 手动冒烟

`uv run shannon-whitebox start -r <repo>`，pre-recon 阶段应看到：

- agent 运行时**逐轮**滚 `💭 Turn N: 🔄/✅ …`（可读、不截断、每轮一行、平静）；
- `🔧` 工具调用**不在 live 屏**（但 `logs --follow` 有）；
- 确定性步骤滚 `STEP ▸ 构建调用图与代码索引` / `STEP ✓ code-index (耗时)`；
- 底部钉两行：`step X/8 …` + `⠋ <步骤意图> · Turn N: <最新轮>`；
- 刷新平稳。

另开 `shannon-whitebox logs <id> --follow`：`[STEP]` 带意图、`[TOOL]`/`[LLM]` 全文可见。与 memory `whitebox-display-clarity-redesign` 的"手动冒烟待做"一并完成并回填结论。

---

## 10. 对前置 spec / 中间讨论的修订说明

- **纠错 v1 §1**：原版**不是**更简单；它逐轮打印 assistant 文本（`formatAssistantOutput` + SDK 流循环），过程信息丰富。本 spec 以"对齐原版逐轮视图"为目标。
- **supersede `whitebox-display-clarity-design` 第 175 行**：step 行不再与 `show_phase` 共用开关；rich 模式放开 `STEP`（新增 `show_steps`）、隐藏 `🔧`（新增 `show_tools`），`PHASE` 仍压住。
- **废弃 v1 讨论中的"节流/合并"**：隐藏 `🔧` + 仅留每轮 💭 后滚屏天然平静，无需节流机制。
- **不冲突 `rich-display-layout-fix`**：其 `show_phase` 接线保留，本 spec 只在旁加并列开关。
- **不动 `clarity-design §10`** 结构化进度未来演进。

---

## 11. 未来演进（非本期）

- 持久活动面板（带框、保留最近 N 条原地更新）——本期用户选了"滚动追加 + 钉住状态行"。
- 子步骤进度（code-index 索引百分比、pre-recon 子 agent 完成数）——需 activity 内埋点。
- `workflow.query` 结构化进度模型（`clarity-design §10`）。
