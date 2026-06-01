# agent-metrics Specification

## Purpose
TBD - created by archiving change claude-agent-sdk-integration. Update Purpose after archive.
## Requirements
### Requirement: 扩展 AgentMetrics 模型

系统 SHALL 扩展 `AgentMetrics` 数据模型以包含更详细的 token 统计信息。

#### Scenario: 新增缓存统计字段

- **WHEN** 创建或更新 `AgentMetrics` 模型定义
- **THEN** 添加以下字段：
  - `cache_read_tokens`: 缓存读取 token 数量，类型为 `int | None`，默认为 `None`
  - `cache_creation_tokens`: 缓存创建 token 数量，类型为 `int | None`，默认为 `None`

#### Scenario: 保持向后兼容性

- **WHEN** 现有代码创建 `AgentMetrics` 实例
- **THEN** 新增字段为可选，不影响现有代码
- **AND** 不提供新增字段时使用默认值 `None`

---

### Requirement: AgentMetrics 字段语义

系统 SHALL 确保 `AgentMetrics` 各字段具有明确的语义。

#### Scenario: input_tokens 语义

- **WHEN** `input_tokens` 有值
- **THEN** 表示 AI 模型接收的输入 token 总数
- **AND** 不包含缓存读取的 token

#### Scenario: output_tokens 语义

- **WHEN** `output_tokens` 有值
- **THEN** 表示 AI 模型生成的输出 token 总数

#### Scenario: cache_read_tokens 语义

- **WHEN** `cache_read_tokens` 有值
- **THEN** 表示从缓存读取的 token 数量
- **AND** 这些 token 以降低的价格计费

#### Scenario: cache_creation_tokens 语义

- **WHEN** `cache_creation_tokens` 有值
- **THEN** 表示创建缓存条目消耗的 token 数量
- **AND** 这些 token 以提高的价格计费

---

### Requirement: ClaudeRunResult 到 AgentMetrics 转换

系统 SHALL 正确将 `ClaudeRunResult` 转换为 `AgentMetrics`。

#### Scenario: 完整转换

- **WHEN** `ClaudeRunResult` 包含完整数据
- **THEN** 转换后的 `AgentMetrics` 包含所有字段：
  - `duration_ms` ← `result.duration`
  - `input_tokens` ← `result.tokens.input_tokens`
  - `output_tokens` ← `result.tokens.output_tokens`
  - `cache_read_tokens` ← `result.tokens.cache_read_input_tokens`
  - `cache_creation_tokens` ← `result.tokens.cache_creation_input_tokens`
  - `cost_usd` ← `result.cost`
  - `num_turns` ← `result.turns`
  - `model` ← `result.model`
  - `structured_output` ← `result.structured_output`

#### Scenario: 处理 None 值

- **WHEN** `ClaudeRunResult.tokens` 为 `None` 或未设置
- **THEN** 所有 token 相关字段设为 `None`

---

### Requirement: Metrics 序列化

系统 SHALL 确保 `AgentMetrics` 可以正确序列化为 JSON。

#### Scenario: JSON 序列化

- **WHEN** 使用 `model_dump()` 或类似方法序列化 `AgentMetrics`
- **THEN** 输出包含所有字段及其值
- **AND** `None` 值正确表示为 JSON null
- **AND** 可在 Temporal 工作流中正确传递

---

### Requirement: Metrics 在 Pipeline State 中的存储

系统 SHALL 在 Pipeline State 中存储每个 Agent 的 metrics。

#### Scenario: 存储 Agent Metrics

- **WHEN** Agent 执行完成
- **THEN** metrics 存储在 `PipelineState.agent_metrics[agent_name]` 中
- **AND** 包含完整的 token 统计信息

#### Scenario: 聚合 Pipeline 总成本

- **WHEN** Pipeline 执行完成
- **THEN** 可以通过遍历 `agent_metrics` 计算总成本
- **AND** 可以分别统计输入、输出、缓存 token

