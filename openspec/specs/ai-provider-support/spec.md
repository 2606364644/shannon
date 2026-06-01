# ai-provider-support Specification

## Purpose
TBD - created by archiving change claude-agent-sdk-integration. Update Purpose after archive.
## Requirements
### Requirement: Provider 抽象层

系统 SHALL 实现 Provider 抽象层，统一不同 AI Provider 的调用接口。

#### Scenario: 创建 Anthropic Provider

- **WHEN** 创建 `AnthropicProvider` 实例
- **THEN** 实现支持 anthropic_api、bedrock、vertex 三种类型
- **AND** 提供统一的 `call()` 方法接口

#### Scenario: 创建 OpenAI 兼容 Provider

- **WHEN** 创建 `OpenAIProvider` 实例
- **THEN** 实现支持 openai_compatible、litellm_router 两种类型
- **AND** 提供统一的 `call()` 方法接口

#### Scenario: 使用工厂函数创建 Provider

- **WHEN** 调用 `create_provider(config)`
- **THEN** 根据 `config.type` 返回对应的 Provider 实例
- **AND** 不支持的 type 抛出 `ValueError`

---

### Requirement: Anthropic API Provider 支持

系统 SHALL 支持 Anthropic 官方 API 作为 AI Provider。

#### Scenario: 配置 Anthropic API

- **WHEN** 设置 `SHANNON_AI_PROVIDER=anthropic_api`
- **AND** 设置 `SHANNON_API_KEY` 或 `ANTHROPIC_API_KEY`
- **THEN** 系统使用 Anthropic 官方 API 进行调用
- **AND** 优先使用 `SHANNON_API_KEY`

#### Scenario: 使用自定义 Base URL

- **WHEN** 设置 `SHANNON_BASE_URL` 或 `ANTHROPIC_BASE_URL`
- **THEN** SDK 使用该 URL 作为 API 基础地址
- **AND** 优先使用 `SHANNON_BASE_URL`

---

### Requirement: Bedrock Provider 支持

系统 SHALL 支持 AWS Bedrock 作为 AI Provider。

#### Scenario: 配置 Bedrock

- **WHEN** 设置 `SHANNON_AI_PROVIDER=bedrock`
- **AND** 设置 `AWS_REGION`
- **AND** 设置 AWS 凭据（`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`）
- **THEN** 系统使用 AWS Bedrock 上的 Claude 模型
- **AND** 模型名称使用 Bedrock ARN 格式（如 `us.anthropic.claude-sonnet-4-6`）

#### Scenario: Bedrock 使用默认区域

- **WHEN** 未设置 `AWS_REGION`，只设置了 `SHANNON_AI_PROVIDER=bedrock`
- **THEN** 系统使用默认区域 `us-east-1`

---

### Requirement: Vertex Provider 支持

系统 SHALL 支持 Google Cloud Vertex AI 作为 AI Provider。

#### Scenario: 配置 Vertex

- **WHEN** 设置 `SHANNON_AI_PROVIDER=vertex`
- **AND** 设置 `CLOUD_ML_REGION` 或 `SHANNON_REGION`
- **AND** 设置 `ANTHROPIC_VERTEX_PROJECT_ID` 或 `SHANNON_PROJECT_ID`
- **THEN** 系统使用 Google Cloud Vertex AI 上的 Claude 模型

#### Scenario: Vertex 使用服务账户认证

- **WHEN** 设置 `GOOGLE_APPLICATION_CREDENTIALS` 指向服务账户 JSON 文件
- **THEN** SDK 使用该文件进行认证

---

### Requirement: OpenAI 兼容 Provider 支持

系统 SHALL 支持 OpenAI 兼容接口作为 AI Provider。

#### Scenario: 配置 OpenAI 兼容接口

- **WHEN** 设置 `SHANNON_AI_PROVIDER=openai_compatible`
- **AND** 设置 `SHANNON_BASE_URL`
- **AND** 设置 `SHANNON_API_KEY`
- **THEN** 系统使用 OpenAI SDK 进行调用
- **AND** 请求发送到指定的 `base_url`

#### Scenario: 覆盖默认模型

- **WHEN** 设置 `SHANNON_MODEL`
- **THEN** 系统使用该模型名称而非默认的 `gpt-4o-mini`

---

### Requirement: LiteLLM Router 支持

系统 SHALL 支持 LiteLLM 路由器作为 AI Provider。

#### Scenario: 配置 LiteLLM Router

- **WHEN** 设置 `SHANNON_AI_PROVIDER=litellm_router`
- **AND** 设置 `SHANNON_BASE_URL` 为 LiteLLM 服务地址
- **AND** 设置 `SHANNON_AUTH_TOKEN` 或 `ANTHROPIC_AUTH_TOKEN`
- **THEN** 系统通过 LiteLLM 路由器进行调用

---

### Requirement: 模型映射

系统 SHALL 根据Provider 类型和模型层级自动选择合适的模型。

#### Scenario: Anthropic 默认模型

- **WHEN** 使用 anthropic_api 且 `model_tier="medium"`
- **THEN** 默认使用 `claude-sonnet-4-6`

#### Scenario: Bedrock 默认模型

- **WHEN** 使用 bedrock 且 `model_tier="medium"`
- **THEN** 默认使用 `us.anthropic.claude-sonnet-4-6`

#### Scenario: Vertex 默认模型

- **WHEN** 使用 vertex 且 `model_tier="medium"`
- **THEN** 默认使用 `claude-sonnet-4-6`

#### Scenario: OpenAI 兼容默认模型

- **WHEN** 使用 openai_compatible 且 `model_tier="medium"`
- **THEN** 默认使用 `gpt-4o`

#### Scenario: 使用 SHANNON_MODEL 覆盖

- **WHEN** 设置了 `SHANNON_MODEL=custom-model`
- **THEN** 系统使用 `custom-model` 而非默认模型

---

### Requirement: Provider 环境变量优先级

系统 SHALL 按以下优先级读取 Provider 配置。

#### Scenario: provider_config 参数优先级最高

- **WHEN** 调用 `run_claude_prompt(provider_config={...})`
- **THEN** 使用 `provider_config` 中的配置
- **AND** 忽略环境变量

#### Scenario: SHANNON_* 前缀优先级次之

- **WHEN** 未提供 `provider_config`
- **THEN** 优先读取 `SHANNON_*` 前缀的环境变量

#### Scenario: ANTHROPIC_* 向后兼容

- **WHEN** `SHANNON_*` 变量未设置
- **THEN** 回退读取 `ANTHROPIC_*` 前缀的环境变量

---

### Requirement: 未支持 Provider 处理

系统 SHALL 对不支持的 Provider 类型返回明确的错误。

#### Scenario: Provider 类型不支持

- **WHEN** 设置 `SHANNON_AI_PROVIDER=unsupported_type`
- **THEN** `create_provider()` 抛出 `ValueError`
- **AND** 错误消息说明不支持的类型

