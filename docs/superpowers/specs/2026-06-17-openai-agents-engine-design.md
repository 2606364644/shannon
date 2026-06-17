# OpenAI Agents SDK 引擎（双引擎 + provider 无关 + .env 切换）设计

- 日期：2026-06-17
- 分支：`feat/fork-py`
- 状态：待 review
- 关联：`docs/superpowers/specs/2026-06-17-provider-agnostic-turn-logging-design.md`（逐轮日志已落地，本设计复用其 audit 机制）

## 1. 背景与动机

shannon-py 当前 agent 执行**只有一个引擎**：`claude_agent_sdk`（`AnthropicProvider`，
`packages/core/src/shannon_core/agents/providers_anthropic.py`）。当前 `.env` 通过智谱 GLM 的
**anthropic 兼容接口**（`ANTHROPIC_BASE_URL=https://open.bigmodel.cn/api/anthropic`）喂给它，模型为
`GLM-5.2[1m]` / `GLM-4.5-Air`。

`claude_agent_sdk` 存在 bug，单引擎意味着一旦它抽风，整套渗透流水线就卡死。因此需要：

1. **双引擎并存**：`claude_agent_sdk`（保留）+ `openai-agents`（新增）两条腿。
2. **provider 无关**：上层（`AgentExecutor` / Temporal activities / 14 个 agent）不感知底层跑哪个引擎。
3. **.env 切换**：一个环境变量决定跑哪个引擎，方便随时切到没 bug 的那条腿做 fallback / 对照。
4. **同一个 GLM 模型两条路对照**：GLM 既暴露 anthropic 兼容接口也暴露 OpenAI 兼容接口，同一模型两条 SDK
   路径跑，便于定位是 SDK bug 还是模型 bug。

## 2. 目标 / 非目标

**目标**

- 新增基于 `openai-agents` 的引擎实现，能力对齐 `AnthropicProvider`（多轮 tool use agent loop）。
- 通过现有 `SHANNON_AI_PROVIDER` 工厂切换，上层接口 `BaseProvider.call()` 不变。
- OpenAI 侧工具套件对齐 claude code 内置工具，使 GLM(OpenAI 兼容) 模型在 loop 里能自发调用工具完成渗透任务。
- 复用已落地的 provider 无关逐轮 audit 机制。

**非目标（YAGNI）**

- **不重构 `AnthropicProvider`**：claude_agent_sdk 那条腿原样保留，本设计不碰它一行（不抽统一 loop、不脱离 SDK 自带 loop）。
- **不实现 Responses API 路径**：GLM/bigmodel 无 `/responses` 端点（社区 [GLM-5 issue #39](https://github.com/zai-org/GLM-5/issues/39) 在催），本设计仅走 Chat Completions 模式。
- **不改 GitNexus 调用层**：它已是 provider 无关的 pipeline 层，见 §6.8。
- **不做引擎间的会话/状态迁移**：两个引擎各自独立会话，不共享上下文。

## 3. 关键事实与约束（已核实）

| 事实 | 来源 / 影响 |
|---|---|
| `openai-agents` 支持 Chat Completions 模式 | `set_default_openai_api("chat_completions")` + `OpenAIChatCompletionsModel(openai_client=AsyncOpenAI(base_url=…))`，可指向任意 base_url 接第三方。[Agents SDK Config](https://openai.github.io/openai-agents-python/config/) |
| GLM `/chat/completions` + tool calling 已支持 | 改 Base URL + Key 即用。[bigmodel OpenAI 兼容文档](https://docs.bigmodel.cn/cn/guide/develop/openai/introduction) |
| GLM `/responses` 端点未实现 | 强制走 Chat Completions 模式。 |
| 现有工厂已就绪 | `create_provider()`（`providers.py:107-135`）、`SHANNON_AI_PROVIDER`（默认 `anthropic_api`，`providers.py:175`）、`provider_map` 已把 `openai_compatible`/`litellm_router` 映射到 `OpenAIProvider`。 |
| `OpenAIProvider` 现状是残的 | 单轮 `chat.completions.create`、无 tool calling、无 loop（`providers_openai.py:127`），需整体重写。 |
| shannon prompt 不显式引用任何 claude code 工具名 | agent 靠通用 agentic 能力自发用工具；OpenAI 侧须把这些工具做成 `@function_tool` 才有对等能力。 |
| GitNexus 在 agent loop 之外 | pipeline 层 `activities.py:258` 直接 `async with GitNexusMCPClient` 调用，provider 无关，本设计不碰。 |
| AnthropicProvider 配置 | `max_turns=200`、`permission_mode=bypassPermissions`、adaptive thinking、`output_format` 结构化输出（`providers_anthropic.py:222-256`）。OpenAI 引擎行为需对齐。 |

## 4. 设计概览

```
                    Temporal activities / AgentExecutor (14 agents)
                                     │
                                     ▼
                          run_claude_prompt() / runner.py
                                     │
                          build_provider_config()  ← 读 .env (SHANNON_AI_PROVIDER, *_BASE_URL, *_API_KEY, *_MODEL)
                                     │
                          create_provider(config)   ← provider_map 工厂选择
                                     │
                   ┌─────────────────┴──────────────────┐
                   ▼                                    ▼
         AnthropicProvider                  OpenAIProvider (本设计: 重写)
         (claude_agent_sdk, 不动)           (openai-agents, Chat Completions 模式)
                   │                                    │
                   ▼                                    ▼
              BaseProvider.call() —— 返回统一的 ClaudeRunResult ——
                                     │
                          ToolAuditLogger 逐轮上报 (provider 无关, 已落地)
```

核心：**新增/重写都在 `OpenAIProvider` 这一条腿上**，工厂、配置、上层调用点基本不动。

## 5. 详细设计

### 5.1 OpenAIProvider 重写（agents SDK Chat Completions 接入）

- **依赖**：`packages/core` 的 `pyproject.toml` 增 `openai-agents`（具体版本实现时锁定，见 §11.1）。
- **进程级初始化**：provider 模块加载时调用一次 `agents.set_default_openai_api("chat_completions")`，确保全链路走 Chat
  Completions（而非默认的 Responses）。
- **client 构造**：`AsyncOpenAI(base_url=SHANNON_OPENAI_BASE_URL, api_key=SHANNON_OPENAI_API_KEY)`，包进
  `OpenAIChatCompletionsModel(openai_client=client, model=<tier 映射的模型名>)`。
- **Agent 构造**：`agents.Agent(name=…, instructions=<system prompt>, tools=<工具套件>, model=<上面的 chatcompletions
  model>)`。system prompt 从现有 prompt 体系传入（与 AnthropicProvider 同源），保证两个引擎 prompt 一致。
- **调用入口**：`async def call(prompt, cwd, model_tier, output_format, deliverables_subdir, audit_logger)` —— 签名与
  `BaseProvider` 完全一致，内部用 `agents.Runner.run_streamed(agent, input=prompt, max_turns=…)` 驱动 loop。

### 5.2 工具套件（function_tool 对齐全集）

实现位置：新建 `packages/core/src/shannon_core/agents/tools_openai/`（或并入现有 tools 目录），每个工具一个
`@function_tool`。**cwd 通过闭包/上下文注入**（`permission_mode=bypassPermissions` 对应的无限制执行）。

**A. 核心套件（必做，构成能力对等的实质）**

| 工具 | 对齐 claude code | 实现要点 | 复杂度 |
|---|---|---|---|
| `bash` | Bash | `asyncio.subprocess` 执行，cwd=注入目录，超时（如 120s 默认 + 上限 600s），stdout/stderr 合并截断 | 中 |
| `read_file` | Read | 读文件，`cat -n` 式行号，`offset`/`limit` 支持，大文件截断 | 低 |
| `edit_file` | Edit | 精确字符串替换 `old_string`→`new_string`，唯一性校验，`replace_all` 选项；匹配失败报错让模型重试 | 中 |
| `write_file` | Write | 覆盖写文件，自动建父目录 | 低 |
| `grep` | Grep | 调 `rg`（ripgrep），`pattern`/`path`/`glob`/`output_mode`，无 rg 则回退 `re` 扫文件 | 中 |
| `glob` | Glob | `fnmatch` / `pathlib.Path.glob`，按修改时间排序 | 低 |
| `web_search` | WebSearch | claude 的 WebSearch 走 Anthropic 服务端；OpenAI 侧需自接搜索源（项目已有的 web-search MCP 可复用，见 §11 核实点） | 中 |
| `web_fetch` | WebFetch | 抓 URL → markdown；可复用项目 `webReader` MCP（`mcp__web-reader__webReader`） | 中 |

**B. 不建议实现（shannon prompt 不引用、渗透任务用不到，纯浪费）**

- `Task`（subagent）：shannon 的 14 agent 是 Temporal 上层调度的**独立 SDK 会话**，不靠 claude code 的 Task 机制；agents SDK 虽有 handoff，但 shannon 用不上。
- `TodoWrite`：shannon 任务编排由 Temporal workflow 承担，不需要 agent 内 todo。
- `MultiEdit` / `NotebookEdit`：shannon 不编辑 notebook、不批量编辑。

> **请 review 时拍板**：A 套件 8 个工具是否认可；B 类要不要补（强烈建议不补）。若 review 认为 web_search/web_fetch 的搜索源要指定，见 §11。

### 5.3 agent loop 与停止条件

- 由 `Runner.run_streamed` 内置 loop 驱动，循环 `模型 → tool_call → 执行 tool → 喂回 → 模型`，直到模型不再产出 tool_call 或到达 `max_turns`。
- **`max_turns`**：对齐 AnthropicProvider 的 `200`（可经环境变量覆盖，复用现有 `CLAUDE_MAX_TURNS` 语义或新增 `SHANNON_OPENAI_MAX_TURNS`）。
- **停止语义**：`RunResult` 的 `last_agent` / 完成事件用于判定正常结束；`max_turns` 触顶视为超限结束（写入 `ClaudeRunResult.stop_reason`）。

### 5.4 产出对齐（RunResult → ClaudeRunResult）

`agents.Runner` 的结果映射到现有 `ClaudeRunResult`（`runner.py:76-89`）：

| ClaudeRunResult 字段 | 来源 |
|---|---|
| `text` | `RunResult.final_output`（若 `output_format` 走结构化输出，则取结构化后的文本/JSON） |
| `success` | loop 正常结束（非 max_turns 触顶、非异常） |
| `turns` | 统计流中 assistant 轮次数 |
| `cost` | `RunResult.context_wrapper.usage` / `RunResult` 的 usage（agents SDK 提供 input/output tokens，按模型单价换算） |
| `model` | 实际使用的模型名（tier 映射后） |
| `structured_output` | `output_format` 指定时的结构化结果（见 §5.6） |
| `tokens` | usage input/output |
| `stop_reason` | 正常 / `max_turns` / 错误 |
| `error` / `retryable` / `error_code` | 异常分类，复用 `BaseProvider._is_retryable_error` |

### 5.5 逐轮 audit（复用已落地机制）

- agents SDK 的 `run_streamed` 产生事件流（`AgentResponseStream`，含 `RunItem` / `response` 等 event）。遍历事件流：
  - assistant 文本/输出 → `audit_logger.log_assistant_turn(turn_no, text)`
  - tool 调用 → 调 `ToolAuditLogger` 的工具调用上报接口（确切方法名实现时按接口核实，对齐 memory 记录的 `isinstance` dispatch 模式）
- **关键**：与 AnthropicProvider 共用同一个 `ToolAuditLogger` 接口，上层无感（这正是
  `provider-agnostic-turn-logging-design` 已落地的成果）。
- 实现时需核实 Chat Completions 模式下 event 类型的确切形态（见 §11）。

### 5.6 结构化输出

- 现有 `output_format` 是 JSON Schema dict。agents SDK 用 `output_type=`（Pydantic 模型）做结构化输出。
- 设计：把 `output_format`（JSON Schema）动态转成 Pydantic 模型（`pydantic.create_model` 或 `jsonschema→pydantic`），传给 `Agent(output_type=…)`；`RunResult.final_output` 即结构化对象，序列化进 `ClaudeRunResult.structured_output`。
- fallback：若 schema 转 pydantic 不可行，退化为 prompt 约束 + 正则解析 JSON（与现状容错一致）。

### 5.7 配置（.env）

复用现有 + 新增最小集：

| 变量 | 现状 | 说明 |
|---|---|---|
| `SHANNON_AI_PROVIDER` | 已有，默认 `anthropic_api` | 切 `openai_compatible` 即用新引擎 |
| `SHANNON_LARGE_MODEL` / `MEDIUM` / `SMALL_MODEL` | 已有（GLM 模型名） | 两引擎共用同一组模型名 |
| `SHANNON_API_KEY` | 已有 | OpenAI 引擎优先用 `SHANNON_OPENAI_API_KEY`，缺失时回退 `SHANNON_API_KEY` |
| `SHANNON_OPENAI_BASE_URL` | **新增** | 智谱 OpenAI 兼容端点，取值 `https://open.bigmodel.cn/api/paas/v4`（实现时以智谱文档为准核实） |
| `SHANNON_OPENAI_API_KEY` | **新增（可选）** | GLM key，缺失回退 `SHANNON_API_KEY` |
| `SHANNON_OPENAI_MAX_TURNS` | **新增（可选）** | 缺省 200，对齐 AnthropicProvider |

改动点集中在 `build_provider_config()`（`providers.py:138-220`）增加上述读取；`provider_map` 已就绪无需改。

### 5.8 GitNexus（provider 无关，零改动）

- GitNexus（query/cypher/impact）通过 `GitNexusMCPClient.call_tool()`（`gitnexus_mcp.py`）在 pipeline 层
  `activities.py:258` 直接调用，**不在 agent loop 内**。
- 该层不依赖任何 provider，OpenAI 引擎自动继承。本设计不触碰。

## 6. 数据流（一次 call 的时序）

1. `run_agent()` → `build_provider_config()` 读 `.env`（`SHANNON_AI_PROVIDER=openai_compatible` 等）。
2. `create_provider(config)` 经 `provider_map` 选中 `OpenAIProvider`。
3. `OpenAIProvider.call()`：构造 `AsyncOpenAI(base_url=…) → OpenAIChatCompletionsModel → Agent(tools=核心套件)`。
4. `Runner.run_streamed(agent, input=prompt, max_turns=200)` 启动 loop。
5. loop 内：模型输出 tool_call → 执行 `@function_tool`（bash/read/…）→ 结果回喂 → audit_logger 逐轮上报。
6. loop 结束 → `RunResult` 映射为 `ClaudeRunResult` 返回，上层无感。

## 7. 错误处理与重试

- **工具执行异常**：`@function_tool` 内捕获并返回结构化错误字符串给模型（让模型自行纠错重试），不抛穿 loop。
- **API 异常**（超时/限流/5xx）：复用 `BaseProvider._is_retryable_error` 分类，`retryable=True` 交由 Temporal activity
  重试策略处理（与 AnthropicProvider 一致）。
- **GLM 兼容性异常**（如不支持某字段）：见 §10 风险。
- **max_turns 触顶**：非异常，记 `stop_reason`，正常返回（与 AnthropicProvider 行为对齐）。

## 8. 测试策略

> 注意：pytest 全量在本仓会 hang（见 memory `pytest-whitebox-hang`），**只跑改动相关子集，不跑全包**。

- **单元测试（不打真实 API）**：
  - 工具：`bash`/`read_file`/`edit_file`/`grep`/`glob` 的 `@function_tool` 包装行为（用 agents SDK 的
    `TestModel` + `set_tracing_disabled`，或直接测工具函数本体）。
  - 映射：`RunResult → ClaudeRunResult` 字段映射。
  - 配置：`build_provider_config()` 对 `SHANNON_OPENAI_*` 的解析与回退。
  - 工厂：`create_provider()` 在 `openai_compatible` 时返回 `OpenAIProvider`。
- **集成/冒烟（手动，真 GLM）**：`SHANNON_AI_PROVIDER=openai_compatible` 跑一个简单 agent（如 recon 的子步骤），
  对照 `anthropic_api` 引擎的结果，验证 loop 跑通 + audit 落库。

## 9. 风险与缓解

| 风险 | 缓解 |
|---|---|
| `openai-agents` 版本演进快，API 可能变 | 锁定版本；Chat Completions 模式接入路径隔离在 `OpenAIProvider` 内部，变更局部化 |
| GLM 兼容接口对某些字段（`parallel_tool_calls`、strict json schema、流式 event）支持不全 | 冒烟验证；字段层做 graceful（关闭 strict、关闭 parallel_tool_calls） |
| Chat Completions 模式下 agents SDK 的 event/lifecycle 与 Responses 模式不完全一致（影响 audit 适配） | §5.5 实现时核实 event 类型，audit 适配加测试 |
| 全套工具实现工作量大 | §5.2 已剔除 shannon 用不到的工具；核心套件 8 个 |
| 工具的 `bypassPermissions` 语义在 OpenAI 侧是"无审批直执行"——安全面与 Anthropic 侧一致（shannon 本就是 bypass） | 维持现状，不新增审批层（非目标） |

## 10. 非目标 / YAGNI（再次明确）

- 不抽统一 loop 引擎、不迁出 claude_agent_sdk。
- 不实现 Responses 路径、不实现 Task/MultiEdit/NotebookEdit/TodoWrite。
- 不做两个引擎间的状态/会话迁移。
- 不新增权限审批层（维持 bypass）。

## 11. 实现时需核实的点

1. `openai-agents` 当前稳定版本号，锁进 `pyproject.toml`。
2. Chat Completions 模式下 `run_streamed` 的 event 类型确切形态（供 §5.5 audit 适配）。
3. `web_search` 的搜索源：复用项目已有 web-search MCP 还是另接（影响 §5.2-A）。
4. `SHANNON_OPENAI_BASE_URL` 智谱 OpenAI 兼容端点的确切取值（文档为准）。
5. `output_format`（JSON Schema）→ Pydantic 模型的转换在 agents SDK 当前版本的可用方式（§5.6）。
6. GLM 模型名（`GLM-5.2[1m]` 等）在 OpenAI 兼容接口下是否原样可用，冒烟确认。

## 12. 后续

本 spec review 通过后，进入 `superpowers:writing-plans` 生成实现计划，再按 TDD 实现。
