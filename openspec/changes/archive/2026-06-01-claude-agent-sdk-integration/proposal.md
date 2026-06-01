## Why

当前 `run_claude_prompt` 函数抛出 `NotImplementedError`，需要集成 Python Claude Agent SDK 来启用 AI Agent 执行。同时需要支持多种 AI Provider（Anthropic API、Bedrock、Vertex、OpenAI 兼容接口），以便用户可以灵活选择模型服务提供商。

## What Changes

### 核心实现

- **实现 `run_claude_prompt` 函数**：使用 Python Claude Agent SDK (`claude-agent-sdk`) 进行流式 AI 调用，支持结构化输出、adaptive thinking、错误处理
- **创建 Provider 抽象层**：设计统一接口支持多种 AI Provider（anthropic_api、bedrock、vertex、litellm_router、openai_compatible）
- **扩展 `AgentMetrics` 模型**：添加完整的 token 统计（input_tokens、output_tokens、cache_creation_tokens、cache_read_tokens），这些信息在 Python SDK 中完全暴露
- **环境变量配置**：通过 `.env` 文件或环境变量配置 provider 类型、API Key、Base URL 等

### 依赖变更

- **新增**：
  - `claude-agent-sdk>=0.5.0` - 官方 Claude Agent SDK
  - `anthropic>=0.40` - Anthropic API SDK（用于 Bedrock/Vertex 支持）
  - `openai>=1.50` - OpenAI SDK（用于 OpenAI 兼容接口）

### 文件变更

- **新增**：`packages/core/src/shannon_core/agents/providers.py`
- **修改**：
  - `packages/core/src/shannon_core/agents/runner.py`
  - `packages/core/src/shannon_core/models/metrics.py`
  - `packages/core/src/shannon_core/agents/executor.py`
  - `packages/core/pyproject.toml`

## Capabilities

### New Capabilities

- `claude-agent-sdk`: Claude Agent SDK 集成，提供流式 AI 调用、结构化输出、adaptive thinking 支持
- `ai-provider-support`: 多 AI Provider 支持，包括 anthropic_api、bedrock、vertex、litellm_router、openai_compatible
- `token-usage-tracking`: 完整的 token 使用追踪，包括输入/输出/缓存统计

### Modified Capabilities

- `agent-metrics`: 扩展指标模型，添加 cache_read_tokens、cache_creation_tokens 字段

## Impact

### 代码影响

- `shannon-core` 包新增对 Claude Agent SDK 的依赖
- `AgentExecutor` 将能够正确执行 AI Agent
- `AgentMetrics` 将提供更详细的成本分析数据

### API 兼容性

- **向后兼容**：新增字段均为可选，现有代码无需修改
- `run_claude_prompt` 函数签名保持不变

### 配置影响

用户需要在 `.env` 文件中配置 AI Provider 相关环境变量：

```bash
# 示例：使用 OpenAI 兼容接口
SHANNON_AI_PROVIDER=openai_compatible
SHANNON_API_KEY=your-key
SHANNON_BASE_URL=https://llm-proxy.futuoa.com
SHANNON_MODEL=gpt-4o-mini
```

### 依赖影响

- 新增 3 个 Python 包依赖
- 需要在构建/部署环境中安装这些依赖
