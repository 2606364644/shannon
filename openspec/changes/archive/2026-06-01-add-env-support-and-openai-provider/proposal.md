# Proposal: 添加 .env 文件支持和 OpenAI 兼容 Provider

## Why

Shannon-py 目前只支持通过环境变量（`SHANNON_AI_PROVIDER`、`ANTHROPIC_API_KEY`）配置 AI provider，用户需要手动设置环境变量，使用不便且容易在 shell 历史中泄露敏感信息。同时，项目不支持 OpenAI 兼容的私有模型服务（如 llm-proxy.futuoa.com），限制了用户对私有模型的选择。

## What Changes

- 添加 `.env` 文件加载支持，允许用户在项目根目录的 `.env` 文件中配置 AI provider
- 新增环境变量：
  - `SHANNON_API_KEY` - 统一的 API 密钥配置
  - `SHANNON_BASE_URL` - 自定义 API 基础 URL
  - `SHANNON_MODEL` - 可选的模型名称覆盖
- 添加 `openai_compatible` provider 类型，支持 OpenAI 兼容接口的凭据验证
- 更新凭据验证逻辑，优先读取新的环境变量，同时保持向后兼容

## Capabilities

### New Capabilities

- `env-config`: 支持 .env 文件配置 AI provider，包括 provider 类型、API 密钥、base URL 等参数

- `openai-compatible-provider`: 添加 `openai_compatible` provider 类型，支持 OpenAI 兼容接口（如 llm-proxy.futuoa.com）的凭据验证

### Modified Capabilities

无。现有能力的需求级别行为不变，仅增加新的配置方式。

## Impact

### Dependencies

- 新增依赖：`python-dotenv>=1.0`

### Affected Code

- `packages/core/pyproject.toml` - 添加 python-dotenv 依赖
- `packages/whitebox/src/shannon_whitebox/cli/main.py` - 添加 load_dotenv() 调用
- `packages/blackbox/src/shannon_blackbox/cli/main.py` - 添加 load_dotenv() 调用
- `packages/core/src/shannon_core/utils/credential_validator.py` - 添加 openai_compatible provider 验证
- `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` - 更新环境变量读取逻辑
- `packages/core/tests/test_credential_validator.py` - 添加 openai_compatible 测试用例

### Backward Compatibility

**保持向后兼容**：
- 现有环境变量 `ANTHROPIC_API_KEY` 继续有效
- 新环境变量 `SHANNON_API_KEY` 优先级更高
- 未配置 .env 时行为与之前完全一致
- 现有 provider（anthropic_api, bedrock, vertex, litellm_router）不受影响

### Limitations

本次变更不包含实际的 API 调用实现：
- `run_claude_prompt` 函数仍然抛出 `NotImplementedError`
- 完整的扫描功能需要在后续变更中实现（阶段 2）

### Example .env Configuration

```bash
# Shannon-py AI Provider 配置
SHANNON_AI_PROVIDER=openai_compatible
SHANNON_API_KEY=your-futuoa-unified-key
SHANNON_BASE_URL=https://llm-proxy.futuoa.com
SHANNON_MODEL=gpt-4o-mini
```
