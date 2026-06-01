## 1. 依赖配置

- [x] 1.1 在 `packages/core/pyproject.toml` 中添加 `claude-agent-sdk>=0.5.0` 依赖
- [x] 1.2 在 `packages/core/pyproject.toml` 中添加 `anthropic>=0.40` 依赖
- [x] 1.3 在 `packages/core/pyproject.toml` 中添加 `openai>=1.50` 依赖
- [x] 1.4 运行 `uv sync` 安装新依赖并验证无冲突

## 2. 数据模型实现

- [x] 2.1 在 `runner.py` 中实现 `TokenUsage` 数据类
- [x] 2.2 在 `runner.py` 中扩展 `ClaudeRunResult` 添加 `tokens` 字段
- [x] 2.3 在 `runner.py` 中添加 `ProviderConfig` 数据类
- [x] 2.4 在 `runner.py` 中添加 `DEFAULT_MODELS` 模型映射表
- [x] 2.5 在 `metrics.py` 中添加 `cache_read_tokens` 和 `cache_creation_tokens` 字段到 `AgentMetrics`

## 3. Provider 抽象层

- [x] 3.1 创建 `providers.py` 文件
- [x] 3.2 实现 `BaseProvider` 抽象基类
- [x] 3.3 实现 `AnthropicProvider` 类（支持 anthropic_api、bedrock、vertex）
- [x] 3.4 实现 `OpenAIProvider` 类（支持 openai_compatible、litellm_router）
- [x] 3.5 实现 `create_provider()` 工厂函数
- [x] 3.6 实现 `build_provider_config()` 环境变量构建函数
- [x] 3.7 添加错误类型：`ProviderError`、`RateLimitError`、`AuthenticationError`、`SpendingCapError`

## 4. AnthropicProvider 实现

- [x] 4.1 实现 `_get_model()` 方法根据 tier 获取模型名称
- [x] 4.2 实现 `call()` 方法调用 Claude Agent SDK
- [x] 4.3 实现流式消息处理逻辑
- [x] 4.4 从 `ResultMessage` 提取 token 统计信息
- [x] 4.5 从 `ResultMessage` 提取成本信息
- [x] 4.6 实现结构化输出提取
- [x] 4.7 实现 adaptive thinking 支持
- [x] 4.8 实现 `_is_retryable_error()` 错误分类逻辑

## 5. OpenAIProvider 实现

- [x] 5.1 实现 `call()` 方法调用 OpenAI SDK
- [x] 5.2 实现结构化输出（JSON Mode）支持
- [x] 5.3 从 API 响应提取 token 统计
- [x] 5.4 实现 `_estimate_cost()` 成本估算
- [x] 5.5 实现 `_is_retryable_error()` 错误分类逻辑

## 6. run_claude_prompt 主函数

- [x] 6.1 实现环境变量 / provider_config 读取逻辑
- [x] 6.2 实现 Provider 实例创建
- [x] 6.3 实现 adaptive thinking 环境变量检查
- [x] 6.4 调用 provider.call() 执行 AI 调用
- [x] 6.5 实现 `_is_spending_cap_behavior()` 花费上限检测
- [x] 6.6 实现异常捕获和错误处理

## 7. AgentExecutor 集成

- [x] 7.1 在 `executor.py` 中调用 `run_claude_prompt()`
- [x] 7.2 将 `ClaudeRunResult` 转换为 `AgentMetrics`
- [x] 7.3 确保所有 token 字段正确映射
- [x] 7.4 传递 `deliverables_subdir` 参数

## 8. 单元测试

- [x] 8.1 创建 `tests/agents/test_providers.py` 测试 Provider 抽象层
- [x] 8.2 测试 `create_provider()` 工厂函数
- [x] 8.3 测试 `build_provider_config()` 环境变量构建
- [x] 8.4 测试 `AnthropicProvider.call()`（需要 mock）
- [x] 8.5 测试 `OpenAIProvider.call()`（需要 mock）
- [x] 8.6 测试错误分类逻辑
- [x] 8.7 创建 `tests/agents/test_runner.py` 测试 `run_claude_prompt()`
- [x] 8.8 测试花费上限检测逻辑
- [x] 8.9 测试 `ClaudeRunResult` 到 `AgentMetrics` 转换

## 9. 集成测试

- [x] 9.1 使用 anthropic_api provider 进行真实 API 测试（需要 API key）
- [x] 9.2 测试结构化输出功能
- [x] 9.3 测试 adaptive thinking 功能
- [x] 9.4 测试环境变量优先级

## 10. 文档更新

- [x] 10.1 更新 README.md 添加环境变量配置说明
- [x] 10.2 添加 .env.example 文件示例
- [x] 10.3 记录各 Provider 的配置方法
- [x] 10.4 说明成本估算的准确性说明

## 11. 代码审查与优化

- [x] 11.1 运行 `ruff` 检查代码风格
- [x] 11.2 运行 `mypy` 检查类型注解
- [x] 11.3 运行 `pytest` 确保所有测试通过
- [x] 11.4 检查异常处理覆盖度
