# claude-agent-sdk Specification

## Purpose
TBD - created by archiving change claude-agent-sdk-integration. Update Purpose after archive.
## Requirements
### Requirement: run_claude_prompt 函数实现

系统 SHALL 使用 Python Claude Agent SDK 实现 `run_claude_prompt` 函数。

#### Scenario: 成功调用并返回结果

- **WHEN** 调用 `run_claude_prompt(prompt="测试提示", repo_path="/path/to/repo")`
- **THEN** 返回 `ClaudeRunResult` 对象
- **AND** `result.success` 为 `True`
- **AND** `result.text` 包含 AI 响应内容
- **AND** `result.cost` 为有效数值
- **AND** `result.turns` 大于 0

#### Scenario: 处理 API 错误

- **WHEN** API 调用失败（如认证错误、速率限制）
- **THEN** 返回 `ClaudeRunResult` 对象
- **AND** `result.success` 为 `False`
- **AND** `result.error` 包含错误描述
- **AND** `result.retryable` 标记是否可重试

---

### Requirement: 流式消息处理

系统 SHALL 使用 Claude Agent SDK 的流式 API 处理消息。

#### Scenario: 处理消息流

- **WHEN** SDK 返回消息流
- **THEN** 系统迭代处理每条消息
- **AND** 统计对话轮次
- **AND** 提取成本和 token 信息
- **AND** 检测结构化输出

---

### Requirement: 结构化输出支持

系统 SHALL 支持 JSON Schema 格式的结构化输出。

#### Scenario: 使用 output_format 参数

- **WHEN** 调用时提供 `output_format` 参数（JSON Schema）
- **THEN** SDK 返回符合 schema 的结构化数据
- **AND** `result.structured_output` 包含解析后的数据

#### Scenario: 兼容 structured_output_schema 参数

- **WHEN** 使用 `structured_output_schema` 参数（别名）
- **THEN** 系统将其视为 `output_format`
- **AND** 行为与直接使用 `output_format` 一致

---

### Requirement: Adaptive Thinking 支持

系统 SHALL 支持 Claude 的 adaptive thinking 模式。

#### Scenario: 启用 adaptive thinking

- **WHEN** 环境变量 `CLAUDE_ADAPTIVE_THINKING` 未设置或为 `true`
- **AND** 使用的模型支持 adaptive thinking
- **THEN** SDK 调用时启用 `thinking: {type: "adaptive"}` 选项

#### Scenario: 禁用 adaptive thinking

- **WHEN** 环境变量 `CLAUDE_ADAPTIVE_THINKING` 设置为 `false`
- **THEN** SDK 调用时不包含 thinking 选项

---

### Requirement: 工作目录配置

系统 SHALL 将 `repo_path` 作为 SDK 的工作目录（cwd）。

#### Scenario: 设置工作目录

- **WHEN** 调用 `run_claude_prompt(repo_path="/path/to/repo")`
- **THEN** SDK 使用该路径作为 `cwd` 参数
- **AND** SDK 的文件操作工具（如 save-deliverable）在该目录下工作

---

### Requirement: 花费上限检测

系统 SHALL 检测 AI 服务的花费上限响应。

#### Scenario: 检测花费上限

- **WHEN** AI 返回包含 "spending limit"、"credit limit"、"quota exceeded" 等关键词
- **AND** `turns <= 2` 且 `cost == 0`
- **THEN** `result.success` 设置为 `False`
- **AND** `result.retryable` 设置为 `True`
- **AND** `result.error` 描述花费上限问题

#### Scenario: 正常执行不受影响

- **WHEN** AI 返回正常响应
- **THEN** 不触发花费上限检测
- **AND** 结果正常返回

