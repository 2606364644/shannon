# 零配置 Claude 体验设计

**日期**: 2026-06-03
**状态**: 已批准
**范围**: shannon-py provider 层配置体验优化

## 问题

shannon-py 的 provider 抽象层在"直接用 Anthropic API"这个最常见场景下增加了不必要的配置负担。

`claude_agent_sdk` 本身能自动检测 `ANTHROPIC_API_KEY` 环境变量。但当前 `providers_anthropic.py` 的 `_build_options()` 方法通过 `options.env` 手动传递 key，**绕过了 SDK 的自动检测机制**。

### 对比

| 操作 | 原始 shannon (TypeScript) | shannon-py (当前) |
|------|--------------------------|-------------------|
| 启动所需最小配置 | `ANTHROPIC_API_KEY=xxx` | `SHANNON_AI_PROVIDER=anthropic_api` + `SHANNON_API_KEY=xxx` |
| 配置文件行数 | 1 行 | 3-5 行 |
| SDK 自动检测 | 正常工作 | 被绕过 |

## 目标

用户只需设置一个环境变量即可运行：

```bash
export ANTHROPIC_API_KEY=sk-ant-xxx
uv run shannon scan ...
```

无需配置任何 `SHANNON_*` 环境变量。

## 改动范围

### 1. `packages/core/src/shannon_core/agents/providers_anthropic.py`

**当前行为** (`_build_options` 方法):

```python
if self.type == "anthropic_api":
    if self.config.api_key:
        options.env = {"ANTHROPIC_API_KEY": self.config.api_key}
    if self.config.base_url:
        options.env = options.env or {}
        options.env["ANTHROPIC_BASE_URL"] = self.config.base_url
```

问题：当 `api_key` 从 `ANTHROPIC_API_KEY` 环境变量 fallback 获取后，代码仍然把它塞进 `options.env`，绕过了 SDK 的原生检测。

**改为**:

- 当 provider 类型为 `anthropic_api` 且**没有**通过 `SHANNON_*` 变量显式覆盖时，**不设置 `options.env`**
- 让 SDK 自己从进程环境变量中读取 `ANTHROPIC_API_KEY`
- 只有当用户通过 `SHANNON_API_KEY` 或 `SHANNON_BASE_URL` 显式设置了非空值时，才覆盖 `options.env`
- Bedrock / Vertex 路径不受影响（它们有自己的配置逻辑）

判断"用户是否显式覆盖"的方法：检查 `SHANNON_API_KEY` 和 `SHANNON_BASE_URL` 环境变量是否被设置（而不是检查 fallback 后的 `config.api_key` 值）。

### 2. `.env.example`

**改为**以 `ANTHROPIC_API_KEY` 为主要入口，结构对齐原始 shannon：

```bash
# 基本配置 - 设置 API Key 即可运行
ANTHROPIC_API_KEY=your-api-key-here

# 高级配置（可选）
# SHANNON_AI_PROVIDER=anthropic_api      # 默认 anthropic_api
# SHANNON_API_KEY=...                     # 覆盖 ANTHROPIC_API_KEY
# SHANNON_BASE_URL=...                    # 覆盖 API 端点
# SHANNON_MODEL=...                       # 覆盖默认模型
```

Provider 特定配置（Bedrock、Vertex、OpenAI、LiteLLM）保留在文档中，但放在 "Provider 特定配置" 部分。

### 3. `providers.py` — `build_provider_config()` 注释更新

`build_provider_config()` 的 fallback 逻辑已正确（`SHANNON_API_KEY` > `ANTHROPIC_API_KEY`），不需要改逻辑。更新注释以反映"零配置优先"的设计意图。

## 不改的部分

- **不删除** provider 抽象层 — 保留 Bedrock / Vertex / OpenAI 兼容能力
- **不改** Bedrock / Vertex / OpenAI 的代码路径
- **不改** 模型 tier 默认映射
- **不改** `build_provider_config()` 的环境变量优先级逻辑

## 成功标准

1. `export ANTHROPIC_API_KEY=xxx && uv run shannon scan ...` 无需其他配置即可运行
2. `SHANNON_*` 环境变量仍然可以正确覆盖默认行为
3. Bedrock / Vertex / OpenAI 兼容 provider 的配置方式不受影响
4. `.env.example` 的"快速开始"部分只需 1 行配置

## 实现计划

1. 修改 `providers_anthropic.py` 的 `_build_options()` — 区分显式配置和 fallback
2. 重写 `.env.example` — 对齐原始 shannon 的结构
3. 更新 `providers.py` 中 `build_provider_config()` 的注释
4. 手动验证：只设 `ANTHROPIC_API_KEY` 能否正常工作
