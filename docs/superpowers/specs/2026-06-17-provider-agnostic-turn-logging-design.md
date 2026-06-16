# Provider 无关的逐轮日志 + workspace 落盘修正

- 日期：2026-06-17
- 分支：`feat/fork-py`
- 状态：设计待审
- 前置 spec：`2026-06-16-whitebox-live-step-intent-display-design.md`（其渲染/状态机已实现且正确，本 spec 只补它依赖的上游数据源）
- 关系：**证伪并修正**前置 spec §1.2「shannon-py 已产出等价数据，只欠渲染」——在当前 Temporal activity + provider 架构下，逐轮数据根本没产出；本 spec 修复数据源。

---

## 1. 背景：前置 spec 的前提被证伪

### 1.1 现象
真跑 `uv run shannon-whitebox start -r <repo>`，pre-recon 等 agent 阶段**全程空白**：`AGENT ▶ pre-recon started` 之后十几分钟既无逐轮 `💭`、底部钉住行也不更新（只 fallback 显示 agent 名），直到 agent 完成、后续确定性 STEP 才集中涌出。setup / code-index 等非 agent 步骤的 `STEP` 行却实时正常。

### 1.2 根因（已用运行时证据三角定位，非推测）

`whitebox-36084`（shannon-py 真实 workspace，落盘于 `vuln-range/workspaces/`）的 `workflow.log`：pre-recon 阶段 `[LLM]` / `[TOOL]` **各 0 条**；per-agent JSON 仅 `agent_start` + `agent_end`，无任何 `llm_response`/`tool_start`。原版 TS 同阶段是 274 条 `[LLM]`、连续分散。

断点在 `MessageDispatcher.dispatch`（`packages/core/.../agents/message_dispatcher.py:49-67`）：它按 `getattr(event, "type", None) == "assistant"/"tool_use"/"tool_result"` 的**字符串比较**分派。而 `claude_agent_sdk` 的消息类（`AssistantMessage`/`SystemMessage`/`ResultMessage`/`HookEventMessage`）**根本没有 `type` 字段**——靠 Python 类（`isinstance`）区分（见 `.venv/.../claude_agent_sdk/types.py:1014-1254`）。诊断实测：流里前 40 个事件 `type=None`、`log_tool_start`/`log_assistant_turn` **0 次调用**。

> 唯独 `ResultMessage` 因为 dispatch 用了 `isinstance`（`:51`）而**正确处理**——这正好解释了 agent 能跑完、有 cost/turn_count，但中间逐轮/工具全丢。

### 1.3 第二层问题：逐轮上报焊死在 AnthropicProvider 内部
- `BaseProvider.call`（`providers.py:64`）抽象签名**没有** `audit_logger`；
- 只有 `AnthropicProvider.call`（`providers_anthropic.py:80`）偷偷加了 `audit_logger` 并在 `_execute_query` 内部 `new MessageDispatcher`；
- `OpenAIProvider.call`（`providers_openai.py:86`）**完全不接** `audit_logger`——`runner.py:153` 传过去是废参数，且若真用 openai provider 会 `TypeError`（latent bug）。

即"逐轮记录"与具体 provider 强耦合：换/加 provider 即失效。

### 1.4 第三层问题：workspace 落盘到 repo 父目录
`resolve_workspaces_dir`（`utils/paths.py:25-38`）优先级 1 = `repo_path.parent / "workspaces"`。跑 `-r .../vuln-range/NodeGoat` → workspace 落 `vuln-range/workspaces/whitebox-36084`，而非 shannon-py 项目内。且 `start`（用 `resolve_workspaces_dir`）与 `logs`（`main.py:106` 用相对 `Path("workspaces")`）**根不一致**——写在一处、读在另一处。

---

## 2. 目标 / 非目标

### 目标
1. **provider 无关的逐轮上报**：`BaseProvider` 提供统一通道，agent 每出一轮/一次工具调用即实时上报，流到**已实现**的 display 管道（`LlmTurnEvent`/`ToolCallEvent` → live 💭 + workflow.log `[LLM]`/`[TOOL]`）。
2. **修 dispatch**：按 `claude_agent_sdk` 真实事件模型（`isinstance` + 遍历 content blocks）分派。
3. **OpenAI 接入**：单次 completion 也上报（单 turn），消除 latent TypeError。
4. **workspace 落盘归位**：统一到 shannon-py 项目根，`start`/`logs`/`blackbox` 一致。

### 非目标
- **不做 token 级打字机流式**（YAGNI；粒度 = turn / 工具级，对齐原版 `Turn N:`）。
- **不改 display 层**（rich_renderer / dashboard_state / live_dashboard 已正确，本 spec 不碰）。
- **不改 Temporal 接线 / activity 边界**（worker 与 starter 同进程，`session` 为进程单例，activity 内 `session.log_event` 已能实时到 dispatcher——setup 的 STEP 实时即证）。
- **不重命名 `ToolAuditLogger`**（它已含 turn 方法；重命名是独立清理，留后续）。
- **🔧 在 live 屏仍隐藏**（沿用前置 spec §4.4），只进 `workflow.log`。

---

## 3. 已锁定决策

| 维度 | 决定 |
|---|---|
| 抽象形状 | 复用现有 `ToolAuditLogger` 接口（已含 `log_assistant_turn`/`log_tool_start`/`log_tool_end`），提升为 `BaseProvider.call` 的统一参数——**不新造 TurnSink 协议** |
| dispatch 分派 | `isinstance(AssistantMessage/UserMessage/ResultMessage)` + 遍历 `content` blocks；`SystemMessage`/`HookEventMessage` 忽略 |
| OpenAI 粒度 | 单 turn（完成后 `log_assistant_turn(1, text)`），不流式 |
| 工具调用 🔧 | live 隐藏（§4.4），workflow.log 记录 |
| workspace 根 | `find_project_root()/workspaces`（可经 `SHANNON_WORKER_ROOT` 覆盖）；去掉 `repo_path.parent` 优先级 |
| 改动范围 | core（provider/dispatcher/paths）+ whitebox worker；display 层不动 |

---

## 4. 设计

### 4.1 `BaseProvider.call` 统一接收 `tool_audit_logger`
`packages/core/.../agents/providers.py`：
- `BaseProvider.call` 抽象签名增 `tool_audit_logger: ToolAuditLogger | None = None`。
- `AnthropicProvider.call`：把现有 `audit_logger` 参数**改名**为 `tool_audit_logger`（语义不变，只是回归基类统一签名），透传给 `_execute_query`/`MessageDispatcher`。
- `OpenAIProvider.call`：签名增 `tool_audit_logger`，完成后调 `await tool_audit_logger.log_assistant_turn(1, text)`（`text` 为 `response.choices[0].message.content`）；无工具循环故无 `log_tool_start`。

> 顺带消除 latent bug：当前 `runner.py:153` 对任何 provider 都传 `audit_logger=...`，OpenAI 会 TypeError。统一后所有 provider 都合法接收。

### 4.2 修 `MessageDispatcher`：按 SDK 真实事件模型分派
`claude_agent_sdk` 的事件**不是** `type=="assistant"/"tool_use"` 的顶层事件，而是：
- `AssistantMessage`：`content: list[ContentBlock]`，block 为 `TextBlock(text)`（→💭）/ `ThinkingBlock`（忽略）/ `ToolUseBlock(id,name,input)`（→🔧）。
- `UserMessage`：`content` 含 `ToolResultBlock`（→ tool_end）。
- `ResultMessage`：终态（已用 `isinstance`，保持）。
- `SystemMessage` / `HookEventMessage`：元信息/钩子，忽略。

`packages/core/.../agents/message_dispatcher.py` 改 `dispatch` + `_handle_assistant`：
```python
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock, UserMessage, ToolResultBlock

async def dispatch(self, event) -> str:
    if isinstance(event, ResultMessage):
        await self._handle_result_message(event); return "complete"
    if isinstance(event, AssistantMessage):
        return await self._handle_assistant(event)
    if isinstance(event, UserMessage):
        for b in getattr(event, "content", []) or []:
            if isinstance(b, ToolResultBlock):
                await self.audit_logger.log_tool_end(getattr(b, "content", ""))
        return "continue"
    # SystemMessage / HookEventMessage / StreamEvent / ...：忽略
    return "continue"

async def _handle_assistant(self, event) -> str:
    self.turn_count += 1
    turn_text = ""
    for block in getattr(event, "content", []) or []:
        if isinstance(block, TextBlock):
            turn_text += block.text
            self.text_parts.append(block.text)
            if self._is_spending_cap_in_text(block.text):
                self.spending_cap_detected = True
        elif isinstance(block, ToolUseBlock):
            await self.audit_logger.log_tool_start(block.name, block.input)
    if turn_text:
        await self.audit_logger.log_assistant_turn(self.turn_count, turn_text)
    err = getattr(event, "error", None)
    if err and self._on_error: self._on_error(str(err))
    return "continue"
```
- 保留 `turn_count` / `collected_text` / `spending_cap_detected` / `result_*` 元数据收集逻辑（`_execute_query` 仍读它们）。
- 删除基于 `event.type` 字符串的分支（`"assistant"/"tool_use"/"tool_result"/"text"`）。

### 4.3 `MessageDispatcher` 不再 provider 专属
`MessageDispatcher` 本身已与 provider 解耦（只认 SDK 事件类）。本次只是**修正它的分派**。它仍由 `AnthropicProvider._execute_query` 构造——因为只有走 SDK 流的 provider 需要"事件→turn"翻译；OpenAI 不经过 dispatcher，直接调 `log_assistant_turn`。这是合理的：dispatcher 是"SDK 事件适配器"，不是所有 provider 的必经之路。

> 这保证了解耦的本质：**统一的是上报接口（`ToolAuditLogger`）**，而非强制所有 provider 走同一个 dispatcher。新 provider 只要"在产出 turn/tool 时调 `tool_audit_logger` 的方法"即可。

### 4.4 workspace 落盘修正
`packages/core/.../utils/paths.py` `resolve_workspaces_dir`：
```python
def resolve_workspaces_dir(repo_path: str | None = None) -> Path:
    worker_root = os.getenv("SHANNON_WORKER_ROOT")
    if worker_root:
        return Path(worker_root) / "workspaces"
    return find_project_root() / "workspaces"   # shannon-py 项目根
```
- **去掉** `repo_path.parent / "workspaces"` 优先级（它是落错地的根因）；`repo_path` 参数保留签名兼容（不再用于定位 workspace 根，仅历史调用方不破）。
- `logs`/`workspace show`/`delete`/`clean` 等 CLI 子命令（`main.py:106` 等相对 `Path("workspaces")` 处）改用 `resolve_workspaces_dir()`，与 `start` 一致。
- `deliverables` 仍落在 `repo_path/.shannon/deliverables`（不变，`resolve_deliverables_path` 不动）。

---

## 5. 数据流（修复后）

```
逐轮(anthropic):
  claude_agent_sdk query() → AssistantMessage(content=[TextBlock|ToolUseBlock])
    → MessageDispatcher.dispatch (isinstance) 
      → TextBlock:  tool_audit_logger.log_assistant_turn(turn, text)
      → ToolUseBlock: tool_audit_logger.log_tool_start(name, input)
    → SessionToolAuditLogger → session.log_event("llm_response"/"tool_start")
    → LlmTurnEvent/ToolCallEvent → FileLogRenderer([LLM]/[TOOL]) + RichConsoleRenderer(💭; 🔧 受 §4.4 门控隐藏)
    → DashboardState.last_turn_text → 钉住第二行 "Turn N: ..."

逐轮(openai):
  chat.completions.create() → response
    → tool_audit_logger.log_assistant_turn(1, text)
    → 同上管道（单 turn）
```

---

## 6. 受影响文件

| 文件 | 改动 |
|---|---|
| `packages/core/.../agents/providers.py` | `BaseProvider.call` 增 `tool_audit_logger` 参数 |
| `packages/core/.../agents/providers_anthropic.py` | `call` 参数 `audit_logger`→`tool_audit_logger`（回归基类签名） |
| `packages/core/.../agents/providers_openai.py` | `call` 增 `tool_audit_logger`，完成后 `log_assistant_turn(1, text)` |
| `packages/core/.../agents/message_dispatcher.py` | `dispatch`/`_handle_assistant` 改 `isinstance` + 遍历 blocks；删 `.type` 字符串分支 |
| `packages/core/.../utils/paths.py` | `resolve_workspaces_dir` 去 `repo_path.parent` 优先级，统一 `find_project_root()/workspaces` |
| `packages/whitebox/.../cli/main.py` | `logs` 等子命令用 `resolve_workspaces_dir()` |
| 测试：`test_message_dispatcher.py`（新增）、`test_providers_*`、`test_paths.py`、display 回归 | 见 §8 |

> `runner.py`（`active_tool_logger` → `provider.call(tool_audit_logger=...)`）、`SessionToolAuditLogger`、display 层**无需改**（接口复用）。

---

## 7. 错误处理与回退
- **纯文本轮（无工具）**：`turn_text` 非空才 `log_assistant_turn`，空轮不发 💭（沿用现状）。
- **纯工具轮**：`ToolUseBlock` 无文本 → `turn_text=""` 不发 💭，但 `log_tool_start` 照发 → workflow.log 有 `[TOOL]`、live 隐藏（§4.4），钉住行仍显示上一轮文本（不黑屏）。
- **`tool_audit_logger=None`**：`MessageDispatcher` 已 `or NullToolAuditLogger()`（`:38`），OpenAI 在 `if tool_audit_logger:` 守卫下调，None 安全。
- **workspace 回退**：`SHANNON_WORKER_ROOT` 仍可覆盖；CI/容器场景可显式指定。
- **全部加性/向后兼容**：dispatch 修正是行为修正（修 bug），其余为新增可选参数。

---

## 8. 测试计划

> 按 memory `pytest-whitebox-hang`：广跑用 `--ignore` 避开 Temporal/网络慢测；core 的 agents/display 单测可单独跑。

1. **`MessageDispatcher`（新增 `test_message_dispatcher.py`）**：
   - 喂 `AssistantMessage(content=[TextBlock("hi"), ToolUseBlock(id,name,input)])` → 断言 `log_assistant_turn(1,"hi")` + `log_tool_start(name,input)` 被调（**当前失败、修后通过——TDD 锚点**）。
   - 喂 `UserMessage(content=[ToolResultBlock])` → `log_tool_end` 被调。
   - 喂 `SystemMessage`/`HookEventMessage` → 无 logger 调用、返回 continue。
   - 喂 `ResultMessage` → `_handle_result_message` 被调、返回 complete。
   - `turn_count` / `collected_text` / `spending_cap_detected` 随 TextBlock 累积正确。
2. **providers**：`OpenAIProvider.call(tool_audit_logger=fake)` → 完成后 `log_assistant_turn(1, text)`；`AnthropicProvider` 参数重命名不破现有调用。
3. **paths**：`resolve_workspaces_dir(repo_path=...)` 返回 `find_project_root()/workspaces`（不再 repo 父目录）；`SHANNON_WORKER_ROOT` 覆盖生效。
4. **CLI**：`logs` 与 `start` 用同一根（mock 同一目录）。
5. **回归**：display 单测（`test_rich_renderer`/`test_dashboard_state`/`test_live_dashboard`/`test_file_renderer`）不动、应全绿。

---

## 9. 验收 / 手动冒烟
`uv run shannon-whitebox start -r <repo>`（workspace 应回落到 `shannon-py/workspaces/`）：
- pre-recon 阶段**逐轮**滚 `💭 Turn N: …`（实时、不截断、每轮一行）；
- `logs <id> --follow` 可见连续 `[LLM]`/`[TOOL]`；
- 底部钉住第二行显示 `⠋ <步骤意图> · Turn N: <最新轮>`；
- setup/code-index 的 `STEP` 行仍实时（无回归）；
- `logs` 能读到本次 workspace（根一致）。

> 回填 memory `whitebox-display-clarity-redesign` 的"手动冒烟待做"结论。
