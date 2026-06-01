# Spec: .env 文件配置支持

## ADDED Requirements

### Requirement: 加载 .env 文件

系统 SHALL 在 CLI 启动时自动加载项目根目录的 .env 文件。

#### Scenario: CLI 启动时加载 .env

- **WHEN** 用户运行 `shannon-whitebox` 或 `shannon-blackbox` 命令
- **THEN** 系统自动加载项目根目录的 .env 文件（如果存在）
- **AND** .env 中的变量可通过 `os.environ.get()` 访问

#### Scenario: .env 不存在时不报错

- **WHEN** 项目根目录不存在 .env 文件
- **THEN** 系统正常运行，不抛出错误
- **AND** 可继续使用环境变量配置

---

### Requirement: SHANNON_API_KEY 环境变量

系统 SHALL 支持 `SHANNON_API_KEY` 环境变量用于配置 API 密钥。

#### Scenario: 使用 SHANNON_API_KEY

- **WHEN** 用户在 .env 中设置 `SHANNON_API_KEY=your-key`
- **THEN** 凭据验证使用该密钥进行验证

#### Scenario: SHANNON_API_KEY 优先级高于 ANTHROPIC_API_KEY

- **WHEN** 同时设置了 `SHANNON_API_KEY` 和 `ANTHROPIC_API_KEY`
- **THEN** 系统优先使用 `SHANNON_API_KEY`

#### Scenario: 向后兼容 ANTHROPIC_API_KEY

- **WHEN** 只设置了 `ANTHROPIC_API_KEY`，未设置 `SHANNON_API_KEY`
- **THEN** 系统使用 `ANTHROPIC_API_KEY`（向后兼容）

---

### Requirement: SHANNON_BASE_URL 环境变量

系统 SHALL 支持 `SHANNON_BASE_URL` 环境变量用于配置自定义 API 基础 URL。

#### Scenario: 使用 SHANNON_BASE_URL

- **WHEN** 用户在 .env 中设置 `SHANNON_BASE_URL=https://llm-proxy.futuoa.com`
- **THEN** 凭据验证使用该 URL 进行连接测试

#### Scenario: 未设置 SHANNON_BASE_URL

- **WHEN** 未设置 `SHANNON_BASE_URL`
- **THEN** 系统使用 provider 的默认 URL（如果需要）

---

### Requirement: SHANNON_MODEL 环境变量

系统 SHALL 支持 `SHANNON_MODEL` 环境变量用于覆盖默认模型名称。

#### Scenario: 使用 SHANNON_MODEL

- **WHEN** 用户在 .env 中设置 `SHANNON_MODEL=gpt-4o-mini`
- **THEN** 系统（在阶段 2 实现时）使用该模型名称

#### Scenario: SHANNON_MODEL 可选

- **WHEN** 未设置 `SHANNON_MODEL`
- **THEN** 系统使用 provider 的默认模型

---

### Requirement: 环境变量优先级

系统 SHALL 按以下优先级读取配置：`PipelineInput.api_key > SHANNON_API_KEY > ANTHROPIC_API_KEY > 默认值`

#### Scenario: PipelineInput.api_key 优先级最高

- **WHEN** PipelineInput.api_key 有值
- **THEN** 系统使用该值，忽略环境变量

#### Scenario: SHANNON_API_KEY 次优先

- **WHEN** PipelineInput.api_key 为空，SHANNON_API_KEY 有值
- **THEN** 系统使用 SHANNON_API_KEY

#### Scenario: ANTHROPIC_API_KEY 向后兼容

- **WHEN** 前两者都为空，ANTHROPIC_API_KEY 有值
- **THEN** 系统使用 ANTHROPIC_API_KEY
