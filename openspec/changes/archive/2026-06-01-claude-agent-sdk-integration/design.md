## Context

当前 `shannon-py` 项目是 TypeScript 版本 Shannon 的 Python 重写。`run_claude_prompt` 函数是 AI Agent 执行的核心入口，当前仅抛出 `NotImplementedError`。

TypeScript 版本使用 `@anthropic-ai/claude-agent-sdk` 实现此功能，通过环境变量配置支持多种 Provider。Python 版本需要使用对应的 Python Claude Agent SDK 实现同等功能。

**约束条件**：
- 必须保持与 TypeScript 版本的功能对等
- 函数签名已固定，不能破坏兼容性
- Python SDK 的 API 与 TypeScript 版本略有不同，需要适配

## Goals / Non-Goals

**Goals:**
- 实现完整的 `run_claude_prompt` 函数，支持流式 AI 调用
- 支持 5 种 AI Provider：anthropic_api、bedrock、vertex、litellm_router、openai_compatible
- 提供完整的 token 使用统计（input、output、cache_creation、cache_read）
- 支持结构化输出（JSON Schema）
- 支持 adaptive thinking
- 完整的错误处理和重试逻辑
- 通过环境变量灵活配置

**Non-Goals:**
- 不实现流式响应的实时展示（由调用方处理）
- 不实现会话级别的成本聚合（每次调用独立计算）
- 不实现自定义缓存策略（使用 SDK 默认）

## Decisions

### 1. Provider 抽象层设计

**决策**：使用工厂模式 + 抽象基类设计 Provider 层。

**理由**：
- 不同 Provider 的 API 差异较大，需要统一接口
- 工厂模式便于扩展新的 Provider
- 与 TypeScript 版本的思路一致

**实现**：
```python
class BaseProvider(ABC):
    async def call(...) -> ClaudeRunResult: ...

class AnthropicProvider(BaseProvider):  # anthropic_api, bedrock, vertex
class OpenAIProvider(BaseProvider):     # openai_compatible, litellm_router

def create_provider(config: ProviderConfig) -> BaseProvider: ...
```

### 2. 模型映射策略

**决策**：使用硬编码的默认模型映射表，支持按 provider 和 tier 选择模型。

**理由**：
- TypeScript 版本使用硬编码映射
- 用户可以通过环境变量 `SHANNON_MODEL` 覆盖
- 简单可靠，无需外部配置

**映射表**：
```python
DEFAULT_MODELS = {
    "anthropic": {"small": "claude-haiku-4-5-20251001", "medium": "claude-sonnet-4-6", ...},
    "bedrock": {...},
    "vertex": {...},
    "openai_compatible": {"small": "gpt-4o-mini", ...},
}
```

### 3. 环境变量命名

**决策**：使用 `SHANNON_*` 前缀的环境变量，同时兼容 `ANTHROPIC_*`。

**理由**：
- `SHANNON_*` 命名空间避免冲突
- 兼容 `ANTHROPIC_*` 便于用户复用现有配置
- 与 TypeScript 版本保持一致

**优先级**：参数 > `SHANNON_*` > `ANTHROPIC_*`

### 4. Token 统计实现

**决策**：从 Python SDK 的 `ResultMessage` 对象中提取完整的 token 统计信息。

**理由**：
- Python SDK 的 `ResultMessage.usage` 字典包含所有 token 信息
- TypeScript 版本未暴露这些数据，Python 版本可以做得更好
- 这些数据对成本分析和优化至关重要

**数据结构**：
```python
@dataclass
class TokenUsage:
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
```

### 5. 错误处理策略

**决策**：Provider 层捕获异常，转换为 `ClaudeRunResult` 的错误字段，同时标记是否可重试。

**理由**：
- 与 TypeScript 版本一致
- 便于上层调用者统一处理错误
- 重试标记支持 Temporal 工作流的重试策略

**可重试错误类型**：
- 速率限制 (RateLimitError)
- 超时 (TimeoutError)
- 服务不可用 (ServiceUnavailable)

**不可重试错误类型**：
- 认证失败 (AuthenticationError)
- 权限不足 (PermissionDenied)

### 6. 依赖选择

**决策**：
- `claude-agent-sdk` - 官方 SDK，与 TS 版本功能对等
- `anthropic>=0.40` - 用于 Bedrock/Vertex 支持（SDK 依赖）
- `openai>=1.50` - 用于 OpenAI 兼容接口

**理由**：
- 都是官方维护的成熟 SDK
- Python Claude Agent SDK 官方推荐

## Risks / Trade-offs

### [Risk] Python SDK API 稳定性

Python Claude Agent SDK 相对较新，API 可能有变化。

**缓解措施**：
- 固定版本范围 `claude-agent-sdk>=0.5.0,<1.0.0`
- 关注 SDK 的 GitHub issues 和 releases
- 编写完整的单元测试覆盖关键流程

### [Risk] Provider 实现复杂度

5 种 Provider 的实现差异较大，可能增加维护成本。

**缓解措施**：
- 优先实现 Claude SDK 相关的 3 种（anthropic_api、bedrock、vertex）
- OpenAI 兼容的 2 种使用统一实现
- 每种 Provider 有独立的单元测试

### [Trade-off] 成本估算精度

OpenAI 兼容 Provider 无法获取精确的 API 定价，只能估算。

**权衡**：
- 接受估算值，在 `AgentMetrics` 中标注
- 用户可以后续通过服务商账单获取精确值
- 这是 OpenAI 兼容模式的固有限制

### [Risk] SDK 权限要求

Claude Agent SDK 需要 `bypassPermissions` 模式才能在无交互环境中运行。

**缓解措施**：
- 文档中明确说明安全考虑
- 建议在受控环境中运行
- 与 TypeScript 版本保持一致的安全模型

## Migration Plan

### 开发阶段

1. **阶段 1**：实现 Claude SDK Provider（anthropic_api、bedrock、vertex）
2. **阶段 2**：实现 OpenAI 兼容 Provider（openai_compatible、litellm_router）
3. **阶段 3**：完善错误处理和测试
4. **阶段 4**：更新文档

### 部署阶段

1. 更新 `pyproject.toml` 依赖
2. 运行 `uv sync` 安装新依赖
3. 配置 `.env` 文件
4. 运行测试验证

### 回滚策略

如果发现问题：
1. 可以通过设置 `SHANNON_AI_PROVIDER` 切换回之前的实现（如果有的话）
2. 版本回滚：保持依赖版本范围，便于降级

## Open Questions

1. **Q**: 是否需要在 `AgentMetrics` 中添加成本估算的精度字段？
   - **A**: 暂不添加，可以在后续版本中根据需求补充

2. **Q**: OpenAI 兼容 Provider 是否需要支持流式响应？
   - **A**: 当前设计使用标准 API 调用，流式响应是未来优化方向

3. **Q**: 是否需要实现本地的 Claude Agent CLI fallback？
   - **A**: 不需要，Python SDK 已包含 CLI 功能

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    run_claude_prompt                         │
│  (packages/core/src/shannon_core/agents/runner.py)          │
├─────────────────────────────────────────────────────────────┤
│  1. 读取环境变量 / provider_config                           │
│  2. 构建 ProviderConfig                                      │
│  3. 创建 Provider 实例                                        │
│  4. 调用 provider.call()                                      │
│  5. 返回 ClaudeRunResult                                     │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    Provider 抽象层                            │
│         (packages/core/src/shannon_core/agents/providers.py)  │
├─────────────────────────────────────────────────────────────┤
│  BaseProvider (ABC)                                           │
│    ├── AnthropicProvider  ← claude-agent-sdk                 │
│    │    ├── anthropic_api                                     │
│    │    ├── bedrock                                           │
│    │    └── vertex                                            │
│    └── OpenAIProvider     ← openai SDK                       │
│         ├── openai_compatible                                  │
│         └── litellm_router                                    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    AgentMetrics 扩展                          │
│       (packages/core/src/shannon_core/models/metrics.py)      │
├─────────────────────────────────────────────────────────────┤
│  - input_tokens                                               │
│  - output_tokens                                              │
│  - cache_creation_tokens                                      │
│  - cache_read_tokens                                          │
│  - cost_usd                                                   │
│  - num_turns                                                  │
│  - model                                                      │
└─────────────────────────────────────────────────────────────┘
```
