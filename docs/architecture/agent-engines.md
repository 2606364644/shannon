# 双 Agent 引擎：Claude Agent SDK / openai-agents

supernova 的业务 workflow 不直接绑定某一家模型或 Agent SDK。所有 agent 调用收敛到 `run_claude_prompt`，再由 `BaseProvider` 分发到两套引擎：

| `SUPERNOVA_AI_PROVIDER` | Provider 类 | Agent SDK / 运行形态 |
|---|---|---|
| `anthropic_api` | `AnthropicProvider` | `claude-agent-sdk` → Claude Code CLI 子进程 |
| `bedrock` | `AnthropicProvider` | Claude Agent SDK 的 Bedrock deployment mode |
| `vertex` | `AnthropicProvider` | Claude Agent SDK 的 Vertex deployment mode |
| `openai_compatible` | `OpenAIProvider` | `openai-agents` + OpenAI Chat Completions 兼容端点 |
| `litellm_router` | `OpenAIProvider` | openai-agents 接 LiteLLM 路由 |

`run_claude_prompt` 是历史命名，当前并不表示只支持 Claude；返回类型 `ClaudeRunResult` 同样是兼容名。

## 统一抽象

### 调用入口

```python
await run_claude_prompt(
    prompt=...,
    repo_path=...,
    model_tier="small|medium|large",
    output_format=<JSON Schema>,
    max_turns=...,
    collector=<CollectorBase>,
    progress=<ProgressSpec>,
    proxy_url=...,
    usage_sink=...,
    tool_policy="default|readonly-code",
    allowed_roots=[...],
)
```

`run_claude_prompt` 负责：

1. 构建 `ProviderConfig`（显式 per-scan config 优先，否则读取 profile env）。
2. `create_provider(config)` 选择引擎。
3. 统一 tool audit logger 适配。
4. Temporal activity heartbeat，支持取消。
5. 统一 429 rate-limit retry。
6. spending-cap 语义归一。

### Provider 契约

`BaseProvider.call` 必须返回 `ClaudeRunResult`，核心不变量：

- `text`：最终文本。
- `structured_output`：调用方传入 schema 时，provider 必须尽力产出可解析对象；SDK 未解析时要有本地 JSON 提取/修复/轻量重输。
- `success` / `error` / `error_code` / `retryable`：统一错误分类。
- `duration` / `turns` / `model`。
- `cost` 与 `cost_currency`：由 usage × per-profile pricing 自算，不信任 SDK 的 `total_cost_usd`。
- cancel 中途已产生的 usage 通过 `UsageSink` 尽量保住，避免超时后账务黑洞。

provider 也必须支持：

- collector 注入（引擎原生 MCP/function tools）
- progress 注入（`log_milestone`）
- tool audit
- readonly-code policy
- per-scan proxy

## Profile 与模型分层

示例 profile：

- `glm-anthropic`
  - `SUPERNOVA_AI_PROVIDER=anthropic_api`
  - `ANTHROPIC_BASE_URL=https://open.bigmodel.cn/api/anthropic`
  - Claude Code CLI 子进程访问 Anthropic-compatible GLM 端点
- `glm-openai`
  - `SUPERNOVA_AI_PROVIDER=openai_compatible`
  - `openai-agents` 直接访问 OpenAI-compatible GLM 端点

`ProviderConfig` 支持全局 model 与 small/medium/large tier override。运行时调参可显式注入：

- `max_turns`
- `subagent_max_turns`
- `max_output_tokens`
- `call_timeout`
- `subagent_call_timeout`
- `adaptive_thinking`

不同 provider 的 env 前缀必须自洽，不做跨前缀 fallback；启动时 profile validator 会检查必填字段。

## Claude Agent SDK 引擎

`providers_anthropic.py` 使用：

```python
from claude_agent_sdk import ClaudeAgentOptions, query
```

关键行为：

- 通过 SDK 启动 Claude Code CLI 子进程。
- `cwd` 指向目标 repo/deliverables。
- 默认 `permission_mode="bypassPermissions"`，适配非交互扫描 worker。
- 默认 max turns 10000，对齐原始 shannon；成本由 spending cap 控制，turn 上限只作 runaway 兜底。
- root worker 环境设置 `IS_SANDBOX=1`，允许 bypass permissions 的容器运行方式。
- 内建工具由 CLI 提供：Read/Grep/Bash/Edit/Task(Agent) 等，supernova 不需要在 provider 里重写通用工具。
- collector/progress 通过 in-process MCP server 注入 `set_*` / `log_milestone`，并用 allowed tools 引导调用。
- `readonly-code` 时切换 default permission guard，只允许 Read/Glob/Grep 且路径必须在 selected roots 内。
- 结构化输出优先读 SDK result，缺失时从 final text 提取 JSON，必要时修复截断数组/对象。
- 取消时尽力 SIGTERM 本次新起 CLI 子进程。

环境变量按 provider 显式注入，并有受限 passthrough（凭证、base URL、部署模式、HOME/PATH 等）。per-scan HOST proxy 会写入 `HTTP_PROXY/HTTPS_PROXY/NO_PROXY`。

## openai-agents 引擎

`providers_openai.py` 使用：

```python
from agents import Agent, Runner, OpenAIChatCompletionsModel
```

它是纯 in-process SDK，没有 Claude CLI 自带通用工具，因此 supernova 自己维护：

```text
packages/core/src/supernova_core/agents/tools_openai/
  bash, read_file, write_file, edit_file
  grep, glob
  web_fetch, web_search
  task
```

工具通过 `RunContextWrapper[ToolContext]` 共享 cwd、allowed roots、proxy 与 subagent runner。

关键加固：

- `set_tracing_disabled(True)`，避免第三方 base_url trace 上传 401。
- AsyncOpenAI HTTP 默认 300s timeout、1 次 retry，防止第三方端点 stall。
- stream 消费有整体 wall-clock timeout。
- 请求发送前修复 assistant tool call 的非法 JSON object arguments，避免第三方端点 400。
- final JSON 无效时做一次轻量结构化重输，并计入真实 token/cost。
- MaxTurnsExceeded 时从 stream collector 恢复已有文本、从 SDK context 恢复 usage。
- cancel/timeout 不吞 `CancelledError`，usage sink 尽量记录部分消耗。

`readonly-code` 时只暴露 `read_file/glob/grep`，不注入 bash/write/task。

## Task / 子代理对齐

同一份 vuln prompt 要求“应用源码分析必须委派 Task Agent”。

- **Claude 引擎**：CLI 内建 Task/Agent 工具，SDK 子进程原生提供子代理委派。
- **openai 引擎**：`tools_openai/task.py` 暴露 `task` function tool。
  - `_make_subagent_runner` 创建 `shannon-task-subagent`。
  - 子代理只有 `read_file/glob/grep`，无 bash/write/task，结构上禁止递归。
  - 默认 100 turns，可由 `SUPERNOVA_OPENAI_SUBAGENT_MAX_TURNS` 或 ProviderConfig 覆盖。
  - 子代理共享 cwd 和 proxy，但不继承 parent narration directive。

这使同一 prompt 能在两套引擎上执行相同工作流；openai 侧维护子代理工具是必要成本，不是能力退化。

## 成本与币种

两套引擎都调用 `agents/pricing.py::compute_cost(model, usage)`：

```text
(input × P_in + cache_creation × P_cc
 + cache_read × P_cr + output × P_out) / 1e6
```

- 价目来自内置 GLM 表 ∪ `SUPERNOVA_PRICING_OVERRIDE` JSON。
- 单 session 金额的币种由 `cost_currency` 表示。
- 字段名 `cost_usd`/`total_cost_usd` 保留兼容，但值不一定是美元。
- openai mapper 会把 raw input 与 cached tokens 归一，避免 cache 命中重复计费。
- 未知模型 cost 为 0 并 warning，不做假估算。

## 引擎差异总结

| 维度 | Claude Agent SDK | openai-agents |
|---|---|---|
| 运行形态 | CLI 子进程 | in-process framework |
| 通用工具 | CLI 内建 | supernova 自维护 |
| 子代理 | CLI Task/Agent | 自定义 `task` + read-only subagent |
| 权限 | `bypassPermissions` / readonly guard | 工具 surface + allowed roots |
| 结构化输出 | SDK result +本地提取/修复 | L0 mapper +轻量重输 |
| 超时兜底 | CLI HTTP/进程生命周期 | AsyncOpenAI timeout + stream wall-clock |
| 代理 | 子进程 env | ToolContext / AsyncOpenAI |
| 计费 | usage 自算 | usage 自算 |

业务层不得直接 import 其中一个 SDK；新增能力应先扩展 `BaseProvider`/统一工具契约，再分别实现。

## 探针与验证

真实模型行为需要用 profile 探针验证，不应只靠单测 mock：

- `scripts/validate_glm_task_probe.py`：glm-anthropic / Claude Code CLI Task 委派。
- `scripts/validate_openai_task_probe.py`：glm-openai / openai-agents `task` 委派。

定向测试：

- `packages/core/tests/agents/test_dual_engine_alignment.py`
- `packages/core/tests/agents/test_providers.py`
- `packages/core/tests/agents/test_providers_anthropic_*`
- `packages/core/tests/agents/test_providers_openai_*`
- `packages/core/tests/agents/tools_openai/test_task.py`
- `packages/core/tests/agents/test_pricing.py`
