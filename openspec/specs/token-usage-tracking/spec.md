# token-usage-tracking Specification

## Purpose
TBD - created by archiving change claude-agent-sdk-integration. Update Purpose after archive.
## Requirements
### Requirement: TokenUsage 数据模型

系统 SHALL 定义 `TokenUsage` 数据类来存储 token 使用统计。

#### Scenario: TokenUsage 结构完整

- **WHEN** 创建 `TokenUsage` 实例
- **THEN** 包含以下字段：
  - `input_tokens`: 输入 token 数量
  - `output_tokens`: 输出 token 数量
  - `cache_creation_input_tokens`: 缓存创建 token 数量
  - `cache_read_input_tokens`: 缓存读取 token 数量

#### Scenario: 计算 total_tokens

- **WHEN** `TokenUsage` 实例包含 `input_tokens=1000`, `output_tokens=500`
- **THEN** `total_tokens` 属性返回 `1500`

---

### Requirement: ClaudeRunResult 包含 Token 统计

系统 SHALL 在 `ClaudeRunResult` 中包含完整的 token 使用统计。

#### Scenario: 成功调用返回 Token 统计

- **WHEN** `run_claude_prompt()` 成功执行
- **THEN** `result.tokens` 为 `TokenUsage` 实例
- **AND** `result.tokens.input_tokens` 反映输入 token 数量
- **AND** `result.tokens.output_tokens` 反映输出 token 数量
- **AND** `result.tokens.cache_creation_input_tokens` 反映缓存创建数量
- **AND** `result.tokens.cache_read_input_tokens` 反映缓存读取数量

#### Scenario: SDK 未返回 Token 信息时

- **WHEN** SDK 的 `ResultMessage.usage` 为空
- **THEN** `result.tokens` 为默认的 `TokenUsage()`（所有字段为 0）

---

### Requirement: 从 SDK 提取 Token 信息

系统 SHALL 从 Python Claude Agent SDK 的 `ResultMessage` 对象中提取 token 信息。

#### Scenario: 提取标准 token 统计

- **WHEN** SDK 返回的 `ResultMessage.usage` 包含 `input_tokens` 和 `output_tokens`
- **THEN** 系统将这些值映射到 `TokenUsage` 对应字段

#### Scenario: 提取缓存 token 统计

- **WHEN** SDK 返回的 `ResultMessage.usage` 包含缓存相关字段
- **THEN** 系统将这些值映射到 `TokenUsage.cache_*` 字段
- **AND** 包括 `cache_creation_input_tokens`
- **AND** 包括 `cache_read_input_tokens`

---

### Requirement: AgentMetrics 包含 Token 统计

系统 SHALL 扩展 `AgentMetrics` 模型以包含完整的 token 使用统计。

#### Scenario: AgentMetrics 新增字段

- **WHEN** 创建 `AgentMetrics` 实例
- **THEN** 包含以下 token 相关字段：
  - `input_tokens`: 输入 token 数量（可选）
  - `output_tokens`: 输出 token 数量（可选）
  - `cache_read_tokens`: 缓存读取 token 数量（可选）
  - `cache_creation_tokens`: 缓存创建 token 数量（可选）

#### Scenario: 从 ClaudeRunResult 转换

- **WHEN** 从 `ClaudeRunResult` 创建 `AgentMetrics`
- **THEN** 所有 token 字段正确映射
- **AND** 原有字段（`duration_ms`, `cost_usd`, `num_turns`, `model`）保持不变

---

### Requirement: 成本计算

系统 SHALL 根据 token 使用情况计算 API 调用成本。

#### Scenario: Claude SDK 返回成本

- **WHEN** 使用 Claude SDK Provider
- **THEN** `result.cost` 等于 SDK 返回的 `total_cost_usd`

#### Scenario: OpenAI 兼容 Provider 估算成本

- **WHEN** 使用 OpenAI 兼容 Provider
- **THEN** `result.cost` 根据模型定价和 token 数量估算
- **AND** 估算公式：`(input_tokens * input_price + output_tokens * output_price) / 1000`

---

### Requirement: 缓存统计追踪

系统 SHALL 单独追踪缓存相关的 token 使用。

#### Scenario: 区分缓存创建和读取

- **WHEN** SDK 返回缓存统计
- **THEN** `cache_creation_input_tokens` 和 `cache_read_input_tokens` 分别记录
- **AND** 不计入 `input_tokens`

#### Scenario: 缓存节省分析

- **WHEN** `cache_read_input_tokens > 0`
- **THEN** 表示本次调用使用了缓存
- **AND** 可以通过对比 `cache_read_input_tokens` 和 `input_tokens` 分析节省量

