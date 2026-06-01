# Design: 添加 .env 文件支持和 OpenAI 兼容 Provider

## Context

### 当前状态

Shannon-py 目前通过环境变量配置 AI provider，支持的 provider 类型包括：
- `anthropic_api` - Anthropic 官方 API
- `bedrock` - AWS Bedrock
- `vertex` - Google Vertex AI
- `litellm_router` - LiteLLM 路由器

用户需要手动设置环境变量（如 `export SHANNON_AI_PROVIDER=...`），这种方式存在以下问题：
1. 不方便管理，每次打开新终端都需要重新设置
2. 容易在 shell 历史中泄露敏感信息
3. 无法通过文件版本控制配置模板（.env 可以 gitignore）

### 约束条件

- 必须保持向后兼容，现有用户的环境变量配置继续有效
- `run_claude_prompt` 函数目前是 `NotImplementedError`，本次变更不实现该函数
- 不依赖额外的配置文件格式（使用标准的 .env 格式）
- .env 文件位置固定在项目根目录

### 相关代码

- 凭据验证：`packages/core/src/shannon_core/utils/credential_validator.py`
- CLI 入口：`packages/whitebox/src/shannon_whitebox/cli/main.py`
- Pipeline activities：`packages/whitebox/src/shannon_whitebox/pipeline/activities.py`

---

## Goals / Non-Goals

### Goals

1. 支持 .env 文件配置，用户可以在项目根目录创建 `.env` 文件来配置 AI provider
2. 添加 `openai_compatible` provider 类型，支持 OpenAI 兼容接口的凭据验证
3. 新增环境变量 `SHANNON_API_KEY`、`SHANNON_BASE_URL`、`SHANNON_MODEL`
4. 保持向后兼容，现有环境变量和配置方式继续有效

### Non-Goals

1. 不实现 `run_claude_prompt` 函数（留待阶段 2）
2. 不添加 CLI 参数配置方式（仅支持 .env）
3. 不支持多 .env 文件或配置文件切换
4. 不实现完整的 API 调用功能

---

## Decisions

### 1. 使用 python-dotenv 而非手动解析

**决策**：使用 `python-dotenv` 库加载 .env 文件。

**理由**：
- `python-dotenv` 是标准方案，被广泛使用（Django、FastAPI 等）
- 自动处理变量展开、注释、引号等边界情况
- 代码简洁，只需一行 `load_dotenv()`

**替代方案**：手动解析 .env 文件
- 被拒绝：需要处理更多边界情况，维护成本高

### 2. 环境变量优先级设计

**决策**：`PipelineInput.api_key > SHANNON_API_KEY > ANTHROPIC_API_KEY > 默认值`

**理由**：
- 程序化传递的参数（PipelineInput）优先级最高，满足临时覆盖需求
- 新环境变量（SHANNON_API_KEY）优先级高于旧环境变量（ANTHROPIC_API_KEY）
- 保持向后兼容，现有用户的配置不受影响

**代码实现**：
```python
api_key = input.api_key or os.environ.get("SHANNON_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
```

### 3. openai_compatible 验证方式

**决策**：调用 `/v1/models` 端点进行健康检查。

**理由**：
- OpenAI 兼容接口通常支持 `/v1/models` 端点
- 不消耗 token（vs 调用 /chat/completions）
- 返回模型列表，验证连接有效

**替代方案**：调用 `/chat/completions` 发送测试消息
- 被拒绝：会消耗 token，成本更高

### 4. 错误处理策略

**决策**：
- 401/403：抛出 `AUTH_FAILED` 错误，不可重试
- 连接错误：抛出 `AUTH_FAILED` 错误，可重试
- 未知 provider：静默跳过（现有行为）

**理由**：
- 保持与现有 provider 一致的错误处理
- 静默跳过未知 provider 避免阻塞用户自定义扩展

---

## Risks / Trade-offs

### Risk 1: .env 文件路径问题

**风险**：用户从子目录运行 CLI 时，.env 可能不会被加载。

**缓解措施**：
- `load_dotenv()` 默认从当前工作目录向上搜索 .env 文件
- 在文档中说明 .env 应放在项目根目录

### Risk 2: 敏感信息泄露

**风险**：.env 文件可能被意外提交到代码仓库。

**缓解措施**：
- 确保 .env 在 .gitignore 中
- 在文档中提醒用户不要提交 .env
- 提供示例 .env 模板（可提交）

### Risk 3: 依赖冲突

**风险**：`python-dotenv` 可能与现有依赖冲突。

**缓解措施**：
- `python-dotenv` 是轻量级库，依赖很少
- 在 PR 中充分测试

### Trade-off: 不实现 API 调用

**权衡**：本次变更只做配置和凭据验证，不实现实际 API 调用。

**影响**：
- 正面：降低变更范围，减少风险
- 负面：用户无法立即看到完整效果

**缓解**：明确文档说明这是阶段 1，阶段 2 会实现 API 调用。

---

## Migration Plan

### 部署步骤

1. **依赖更新**：用户运行 `uv sync` 安装 `python-dotenv`
2. **.env 配置**：用户在项目根目录创建 `.env` 文件（可选）
3. **验证**：运行测试确认凭据验证正常工作

### 回滚策略

- 移除 `python-dotenv` 依赖
- 删除 .env 相关代码
- 恢复环境变量读取逻辑

---

## Open Questions

1. **是否需要支持自定义 .env 路径？**
   - 当前决定：不支持，固定使用项目根目录的 .env
   - 理由：简化实现，符合 12-factor app 最佳实践

2. **是否需要添加 CLI 参数覆盖？**
   - 当前决定：不添加，仅支持 .env 和环境变量
   - 理由：降低复杂度，后续有需求再添加

3. **blackbox 是否需要相同的凭据验证？**
   - 当前决定：暂不实现，focus on whitebox
   - 理由：blackbox 的使用场景可能不同，需要单独评估
