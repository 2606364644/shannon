# 日志标签列对齐 + rich/file 正文统一

- **日期**: 2026-06-23
- **状态**: Draft（待实现）
- **分支**: feat/fork-py
- **范围**: `packages/core/src/shannon_core/display/`

## 背景与问题

终端 live 日志当前形如（`shannon-whitebox start` 实跑）：

```
[2026-06-23 00:42:39] PHASE  Starting setup ──────────────────────
[2026-06-23 00:42:39] STEP  ○ 预检（环境 / 依赖就绪性）
[2026-06-23 00:42:39] STEP  ✓ 预检（环境 / 依赖就绪性）  0ms
[2026-06-23 00:42:40] AGENT  ▶ pre-recon started (attempt 1)
[2026-06-23 00:43:30] 💭 [Agent] Turn 2: I'll start by ...
```

**根因**：`PHASE`/`AGENT` 各 5 字符，`STEP` 只有 4 字符。三个 renderer 把标签硬编码在 f-string、后跟固定 2 空格、**没有任何统一的标签宽度补齐 helper**，导致 STEP 行正文比 PHASE/AGENT 少 1 列 ——「对不上、不美观」。

涉及 renderer：
- `RichConsoleRenderer`（`rich_renderer.py`）—— 终端输出，主目标
- `FileLogRenderer`（`file_renderer.py`）—— workflow.log，STEP 同样对不齐
- `LiveDashboardRenderer`（`live_dashboard.py`）—— 底部状态栏，无逐行标签列，**不涉及**

## 关键约束：Turn 行的方括号冲突（设计演进）

最初考虑「事件标签也用方括号 `[PHASE]/[STEP ]/[AGENT]`」以与 file 统一。实跑后发现冲突：

- Turn 行的 `[Agent]` 来自 `agent_prefix()`（`formatters.py:60`），是 **agent 身份标识**的既有视觉约定，返回 `[Injection]/[XSS]/[Auth]/[SSRF]/[Agent]`（默认），被 rich / file / dashboard **三处共享**。
- 若终端事件标签也用 `[AGENT]`，方括号就同时承载两种语义（事件类型 + agent 身份），且 `AGENT` 事件行的 `[AGENT]` 与 Turn 行 `💭 [Agent]` 视觉撞车，扫读时难区分。

**决策**：
- **终端事件标签列不用方括号**，方括号继续专属 agent 身份（Turn 行 `💭 [Agent]` 不动）。
- **file 保留方括号**（`[AGENT]`/`[LLM]` 不同标签本就区分事件类型，且方括号是 grep 友好的纯文本日志传统）。
- **对齐靠 `tag()` 补齐，与方括号无关**。方括号只是装饰；方括号方案里 `[STEP ]` 内的尾空格和无方括号方案里 `STEP  ` 标签尾的空格是同一个补齐，body 起点对齐效果完全相同。

附带收益：终端不出现字面方括号，**绕开 Rich markup 吞括号的难题**，rich renderer 改动比方括号方案更小。

## 目标

1. PHASE/STEP/AGENT 三标签列对齐（正文起点同列）。
2. 终端标签无方括号、`tag()` 补齐；file 保留方括号、`tag()` 在方括号内补齐。
3. rich 与 file 的这三类行**正文逐字完全一致**（符号 ○✓▶ + 中文意图 + suffix）。
4. 单一来源：`tag()` 锁对齐、共享正文函数锁一致性，未来改格式只改一处。

## 设计

### 组件改动

| 文件 | 改动 |
|---|---|
| `display/formatters.py` | 新增 `LABEL_WIDTH` 常量、`tag()` helper、3 个共享正文函数 `step_body` / `phase_body` / `agent_body`（纯文本、无颜色、无换行） |
| `display/rich_renderer.py` | step/phase/agent 三类行标签列改用 `tag()` 补齐（仍走 `[cyan]{tag}[/]` markup 上色），正文改调共享函数 |
| `display/file_renderer.py` | step/phase/agent 三类行标签列套 `[{tag()}]`、正文改调同一批共享函数、末尾 `\n` |
| `display/live_dashboard.py` | **不动** |

### 集中点（`formatters.py` 新增）

```python
LABEL_WIDTH = 5  # PHASE/AGENT=5，STEP 补齐到 5

def tag(label: str, width: int = LABEL_WIDTH) -> str:
    """补齐到固定宽度的标签内容：tag("STEP") -> "STEP \"。"""
    return label.ljust(width)

def step_body(e) -> str:
    """○/✓/✗ + 中文意图 + duration/error suffix —— rich 与 file 共用，纯文本。"""
    # start    -> STEP_PENDING + intent
    # complete -> STEP_DONE    + intent + ("  {dur}" 若有 duration_ms)
    # error    -> STEP_FAIL    + intent + "  — {error}"

def phase_body(e) -> str:
    """verb + phase，如 'Starting setup'。"""

def agent_body(e) -> str:
    """▶/✗/✓ + title + (attempt) + metrics。title 复用 _prefixed 等价逻辑
    （pfx == "[Agent]" 时取 name，否则 f"{pfx} {name}"）。"""
```

正文函数从 `symbols.py` 取符号、从事件取意图/duration/cost，返回**纯文本**。这是「rich/file 逐字一致」的单一来源。

### 对齐原理

标签列固定宽 5 + 统一 2 空格分隔 → body 起点同列：

```
终端:  [..] PHASE  Starting      PHASE(5) + 2空格
       [..] STEP   ○ 预检         STEP (5,含1补齐) + 2空格  ← STEP 后看起来3空格
       [..] AGENT  ▶ agent        AGENT(5) + 2空格
                          ↑ body 都落在这列

file:  [..] [PHASE] Starting      方括号内 5 字符 + 1空格
       [..] [STEP ] ○ 预检         方括号内 5 字符(含1补齐) + 1空格
       [..] [AGENT] ▶ agent        方括号内 5 字符 + 1空格
                            ↑ 方括号右边界 + body 都在这列
```

终端与 file 的标签-正文间距不必相同（终端沿用现 2 空格、file 沿用现 1 空格）；对齐要求是**各自 renderer 内**三类行 body 同列，不要求 rich 与 file 落在同一绝对列。

### 数据流

```
StepEvent / PhaseEvent / AgentEvent
   → DisplayDispatcher 并行转发
   → RichConsoleRenderer: tag() + 共享正文() → markup 上色行 → 终端
   → FileLogRenderer:     tag() + 共享正文() → 纯文本行 + \n   → workflow.log
两者标签列等宽（body 同列）、正文逐字一致
```

## 范围边界

- **本次做**：PHASE/STEP/AGENT 三类行（rich + file）。
- **不动**：
  - `LiveDashboardRenderer`（状态栏，无逐行标签列）。
  - Turn / Tool / Error / Resume 行 —— 它们以 `💭`/`🔧` emoji 开头，不在 PHASE/STEP/AGENT 的标签列体系内；其 `agent_prefix` 方括号（agent 身份）保留不动。
- **列为 follow-up、本次不做**：file 独有的 `[TOOL]`/`[LLM]`/`[ERROR]`/`[RESUME]` 标签列对齐。理由：只在 file 出现、不影响终端美观（本次诉求是终端），纳入会扩大范围、牵动 `_tool`/`_llm` 正文。

## 已接受的代价

- file 的 step/phase/agent 正文从 `name: verb`（如 `preflight: Starting`）改为符号 + 中文意图（如 `○ 预检（环境 / 依赖就绪性）`），**丢失 name 机器可读字段**（用户已确认）。
- file 的 `summary` block、`COMPLETION_PATTERN`（`shannon_core.cli.logs`）**不受影响** —— 那些匹配的是 summary 行，不在本次改动范围。

## 测试策略

- `tests/display/test_formatters.py`：新增 `tag()`、`step_body` / `phase_body` / `agent_body` 断言。
- `tests/display/test_rich_renderer.py`：step/phase/agent 断言更新为标签补齐后形态（STEP 后 3 空格、body 与 PHASE/AGENT 同列）、正文来自共享函数。
- `tests/display/test_file_renderer.py`：正文断言从 `name: verb` 改为符号 + 中文意图；标签断言为 `[STEP ]`（方括号内补齐）。
- whitebox/blackbox L2 集成测试（`test_display_integration.py`）：同步更新相关断言。
- **memory 坑提醒**：
  - `rich-markup-single-char-tag-swallow`：`STEP`/`PHASE`/`AGENT` 都是多字母，不触发吞括号；测试断言里勿引入 `[X]` 单字母片段。
  - 测试时间戳用真实 `YYYY-MM-DD HH:MM:SS`，别用 `"t"`（涉及完整行匹配时）。
- **memory 坑提醒**：`pytest-whitebox-hang` —— 跑测试只跑改动相关子集，别跑全套（会卡 Temporal/网络慢测试）。

## 验收标准

1. 终端 PHASE/STEP/AGENT 行 body 落在同一列（STEP 后 3 空格 = PHASE/AGENT 后 2 空格的 body 起点）。
2. file `[PHASE]`/`[STEP ]`/`[AGENT]` 方括号右边界对齐、body 同列。
3. 抓取同一事件的 rich 输出与 file 输出，去掉标签列、颜色、换行后**正文逐字相等**。
4. Turn 行 `💭 [Agent]` 保持不变，与 AGENT 事件行（无方括号 `AGENT`）视觉可清晰区分。
5. 改动相关单元测试 + L2 集成测试全绿。

## 风险

- **agent_prefix 方括号是三 renderer 共享约定**，本次不动它（终端事件标签无方括号，正是不动它的前提）。若将来要让终端事件标签也带方括号，必须先解决与 agent_prefix 的语义/视觉冲突。
- **共享正文函数改变 file 行格式**：需确认无外部解析依赖 step 的 `name` 字段。已查 `COMPLETION_PATTERN` 只匹配 summary 行；step name（如 `preflight`）目前无硬解析契约，改为符号 + 意图风险可控。
