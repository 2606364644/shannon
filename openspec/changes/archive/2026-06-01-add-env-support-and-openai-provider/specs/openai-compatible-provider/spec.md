# Spec: OpenAI 兼容 Provider

## ADDED Requirements

### Requirement: openai_compatible provider 类型

系统 SHALL 支持 `openai_compatible` provider 类型，用于验证 OpenAI 兼容接口的凭据。

#### Scenario: 验证有效凭据

- **WHEN** 用户设置 `SHANNON_AI_PROVIDER=openai_compatible`
- **AND** 提供有效的 `SHANNON_API_KEY` 和 `SHANNON_BASE_URL`
- **THEN** 凭据验证成功通过
- **AND** 不抛出错误

#### Scenario: 验证无效凭据

- **WHEN** API 密钥无效（HTTP 401 或 403）
- **THEN** 系统抛出 `AUTH_FAILED` 错误
- **AND** 错误信息包含 HTTP 状态码
- **AND** 错误标记为不可重试

#### Scenario: 验证连接失败

- **WHEN** 无法连接到 SHANNON_BASE_URL
- **THEN** 系统抛出 `AUTH_FAILED` 错误
- **AND** 错误信息包含连接失败详情
- **AND** 错误标记为可重试

---

### Requirement: 必需参数验证

系统 SHALL 在使用 `openai_compatible` provider 时验证必需参数。

#### Scenario: 缺少 api_key 时报错

- **WHEN** `SHANNON_AI_PROVIDER=openai_compatible`
- **AND** 未提供 `SHANNON_API_KEY` 或 `ANTHROPIC_API_KEY`
- **THEN** 系统抛出 `AUTH_FAILED` 错误
- **AND** 错误信息说明需要 api_key

#### Scenario: 缺少 base_url 时报错

- **WHEN** `SHANNON_AI_PROVIDER=openai_compatible`
- **AND** 未提供 `SHANNON_BASE_URL`
- **THEN** 系统抛出 `AUTH_FAILED` 错误
- **AND** 错误信息说明需要 base_url

---

### Requirement: 健康检查端点

系统 SHALL 使用 OpenAI 兼容的 `/v1/models` 端点进行健康检查。

#### Scenario: 调用 /v1/models 端点

- **WHEN** 验证 `openai_compatible` provider
- **THEN** 系统向 `{SHANNON_BASE_URL}/v1/models` 发送 GET 请求
- **AND** 请求头包含 `Authorization: Bearer {SHANNON_API_KEY}`
- **AND** 请求超时时间为 15 秒

#### Scenario: 处理成功响应

- **WHEN** `/v1/models` 返回 HTTP 200
- **THEN** 凭据验证通过

#### Scenario: 处理认证失败

- **WHEN** `/v1/models` 返回 HTTP 401 或 403
- **THEN** 系统抛出 `AUTH_FAILED` 错误
- **AND** 错误不可重试

#### Scenario: 处理连接错误

- **WHEN** 请求无法连接（网络错误、DNS 解析失败等）
- **THEN** 系统抛出 `AUTH_FAILED` 错误
- **AND** 错误可重试

---

### Requirement: URL 处理

系统 SHALL 正确处理 base_url 的尾部斜杠。

#### Scenario: 自动移除尾部斜杠

- **WHEN** `SHANNON_BASE_URL` 设置为 `https://example.com/`（带尾部斜杠）
- **THEN** 系统自动移除尾部斜杠后构建完整 URL
- **AND** 最终 URL 为 `https://example.com/v1/models`

#### Scenario: 无尾部斜杠时正常处理

- **WHEN** `SHANNON_BASE_URL` 设置为 `https://example.com`（无尾部斜杠）
- **THEN** 系统直接构建完整 URL
- **AND** 最终 URL 为 `https://example.com/v1/models`

---

### Requirement: 未知 provider 处理

系统 SHALL 对未知的 provider 类型静默跳过验证。

#### Scenario: 未知 provider 不报错

- **WHEN** `SHANNON_AI_PROVIDER` 设置为未知类型（如 `custom_provider`）
- **THEN** 系统静默跳过凭据验证
- **AND** 不抛出错误（优雅降级）
