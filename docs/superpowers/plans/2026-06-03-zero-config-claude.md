# 零配置 Claude 体验 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 shannon-py 只需设置 `ANTHROPIC_API_KEY` 即可运行，无需任何 `SHANNON_*` 环境变量。

**Architecture:** 修改 `providers_anthropic.py` 的 `_build_options()` 方法，在 `anthropic_api` provider 类型下，只有当用户显式设置了 `SHANNON_API_KEY` / `SHANNON_BASE_URL` 时才覆盖 SDK 的环境变量，否则让 `claude_agent_sdk` 自动从进程环境中读取 `ANTHROPIC_API_KEY`。

**Tech Stack:** Python, pytest, claude_agent_sdk

---

## File Structure

| 文件 | 操作 | 职责 |
|------|------|------|
| `packages/core/tests/agents/test_providers.py` | 修改 | 添加 `_build_options` 零配置行为测试 |
| `packages/core/src/shannon_core/agents/providers_anthropic.py` | 修改 | `_build_options()` 区分显式覆盖与 fallback |
| `.env.example` | 修改 | 以 `ANTHROPIC_API_KEY` 为主入口 |
| `packages/core/src/shannon_core/agents/providers.py` | 修改 | 更新 `build_provider_config()` 注释 |

---

### Task 1: 为 `_build_options` 零配置行为写测试

**Files:**
- Modify: `packages/core/tests/agents/test_providers.py`

- [ ] **Step 1: 在 `TestAnthropicProvider` 类中添加测试方法**

在 `test_call_success` 方法之后（约第 278 行），添加以下测试：

```python
class TestAnthropicProviderBuildOptions:
    """测试 AnthropicProvider._build_options 的零配置行为"""

    def test_no_env_override_with_anthropic_key_only(self):
        """当只有 ANTHROPIC_API_KEY 时，不应设置 options.env（SDK 自动读取）"""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}, clear=True):
            options = provider._build_options(
                cwd="/tmp",
                model="claude-sonnet-4-6",
            )

        assert options.env is None or options.env == {}

    def test_env_override_with_shannon_api_key(self):
        """当 SHANNON_API_KEY 显式设置时，应传入 options.env"""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {
            "SHANNON_API_KEY": "shannon-key",
            "ANTHROPIC_API_KEY": "anthropic-key",
        }):
            options = provider._build_options(
                cwd="/tmp",
                model="claude-sonnet-4-6",
            )

        assert options.env is not None
        assert options.env["ANTHROPIC_API_KEY"] == "shannon-key"

    def test_env_override_with_shannon_base_url(self):
        """当 SHANNON_BASE_URL 显式设置时，应传入 options.env"""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "sk-ant-test",
            "SHANNON_BASE_URL": "https://custom.example.com",
        }):
            options = provider._build_options(
                cwd="/tmp",
                model="claude-sonnet-4-6",
            )

        assert options.env is not None
        assert options.env["ANTHROPIC_BASE_URL"] == "https://custom.example.com"

    def test_both_shannon_overrides(self):
        """当 SHANNON_API_KEY 和 SHANNON_BASE_URL 同时设置时"""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {
            "SHANNON_API_KEY": "shannon-key",
            "SHANNON_BASE_URL": "https://custom.example.com",
        }):
            options = provider._build_options(
                cwd="/tmp",
                model="claude-sonnet-4-6",
            )

        assert options.env is not None
        assert options.env["ANTHROPIC_API_KEY"] == "shannon-key"
        assert options.env["ANTHROPIC_BASE_URL"] == "https://custom.example.com"

    def test_bedrock_env_still_set(self):
        """Bedrock provider 仍应设置 options.env（不受改动影响）"""
        config = ProviderConfig(type="bedrock", region="us-west-2")
        provider = AnthropicProvider(config)

        options = provider._build_options(
            cwd="/tmp",
            model="us.anthropic.claude-sonnet-4-6",
        )

        assert options.env is not None
        assert options.env["AWS_REGION"] == "us-west-2"

    def test_vertex_env_still_set(self):
        """Vertex provider 仍应设置 options.env（不受改动影响）"""
        config = ProviderConfig(
            type="vertex",
            region="us-central1",
            project_id="test-project",
        )
        provider = AnthropicProvider(config)

        options = provider._build_options(
            cwd="/tmp",
            model="claude-sonnet-4-6@latest",
        )

        assert options.env is not None
        assert options.env["CLOUD_ML_REGION"] == "us-central1"
        assert options.env["ANTHROPIC_VERTEX_PROJECT_ID"] == "test-project"
```

- [ ] **Step 2: 运行测试确认新增的零配置测试失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/agents/test_providers.py::TestAnthropicProviderBuildOptions -v`

预期: `test_no_env_override_with_anthropic_key_only` **FAIL** — 因为当前代码在 `config.api_key` 有值时会设置 `options.env`（即使 key 来自 `ANTHROPIC_API_KEY` fallback）。其他测试可能 PASS 或 FAIL 取决于当前行为。

- [ ] **Step 3: Commit 测试**

```bash
git add packages/core/tests/agents/test_providers.py
git commit -m "test: add zero-config _build_options tests for AnthropicProvider"
```

---

### Task 2: 实现 `_build_options` 零配置逻辑

**Files:**
- Modify: `packages/core/src/shannon_core/agents/providers_anthropic.py:104-123`

- [ ] **Step 1: 替换 `_build_options` 中 `anthropic_api` 分支的逻辑**

将 `providers_anthropic.py` 第 104-110 行：

```python
        # 添加 Provider 特定配置
        if self.type == "anthropic_api":
            if self.config.api_key:
                options.env = {"ANTHROPIC_API_KEY": self.config.api_key}
            if self.config.base_url:
                options.env = options.env or {}
                options.env["ANTHROPIC_BASE_URL"] = self.config.base_url
```

替换为：

```python
        # 添加 Provider 特定配置
        if self.type == "anthropic_api":
            # 零配置: SDK 自动从进程环境读取 ANTHROPIC_API_KEY
            # 只有 SHANNON_* 显式覆盖时才传递给 SDK
            explicit_env = {}
            shannon_api_key = os.getenv("SHANNON_API_KEY")
            shannon_base_url = os.getenv("SHANNON_BASE_URL")

            if shannon_api_key:
                explicit_env["ANTHROPIC_API_KEY"] = shannon_api_key
            if shannon_base_url:
                explicit_env["ANTHROPIC_BASE_URL"] = shannon_base_url

            if explicit_env:
                options.env = explicit_env
```

Bedrock（第 112-115 行）和 Vertex（第 117-121 行）分支保持不变。

- [ ] **Step 2: 运行所有 provider 测试确认通过**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/agents/test_providers.py -v`

预期: ALL PASS，包括 Task 1 新增的零配置测试。

- [ ] **Step 3: Commit 实现**

```bash
git add packages/core/src/shannon_core/agents/providers_anthropic.py
git commit -m "feat: enable zero-config Claude usage via ANTHROPIC_API_KEY

Only override SDK env when SHANNON_* vars are explicitly set.
When only ANTHROPIC_API_KEY is present, let claude_agent_sdk
auto-detect it from the process environment."
```

---

### Task 3: 重写 `.env.example`

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: 替换整个 `.env.example` 文件**

将 `.env.example` 替换为以下内容（对齐原始 shannon 的结构，以 `ANTHROPIC_API_KEY` 为主入口）：

```bash
# Shannon-py Environment Configuration
# Copy this file to .env and fill in your credentials

# =============================================================================
# 快速开始 - 设置 API Key 即可运行
# =============================================================================

ANTHROPIC_API_KEY=your-api-key-here

# =============================================================================
# Claude Agent 配置
# =============================================================================

# Adaptive thinking（默认启用）
# CLAUDE_ADAPTIVE_THINKING=true

# =============================================================================
# 高级配置（可选）
# =============================================================================
# 以下配置仅在需要覆盖默认行为时使用

# SHANNON_AI_PROVIDER=anthropic_api      # Provider 类型（默认 anthropic_api）
# SHANNON_API_KEY=...                     # 覆盖 ANTHROPIC_API_KEY
# SHANNON_BASE_URL=...                    # 覆盖 API 端点
# SHANNON_MODEL=...                       # 覆盖默认模型

# =============================================================================
# OPTION 2: AWS Bedrock
# =============================================================================
# SHANNON_AI_PROVIDER=bedrock
# AWS_REGION=us-east-1
# AWS_ACCESS_KEY_ID=your-access-key
# AWS_SECRET_ACCESS_KEY=your-secret-key

# =============================================================================
# OPTION 3: Google Cloud Vertex AI
# =============================================================================
# SHANNON_AI_PROVIDER=vertex
# SHANNON_PROJECT_ID=your-project-id
# SHANNON_REGION=us-central1
# GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json

# =============================================================================
# OPTION 4: OpenAI 兼容接口
# =============================================================================
# SHANNON_AI_PROVIDER=openai_compatible
# SHANNON_API_KEY=your-provider-key
# SHANNON_BASE_URL=https://your-provider-endpoint
# SHANNON_MODEL=gpt-4o

# =============================================================================
# OPTION 5: LiteLLM Router
# =============================================================================
# SHANNON_AI_PROVIDER=litellm_router
# SHANNON_BASE_URL=https://your-litellm-endpoint
# SHANNON_AUTH_TOKEN=your-litellm-token

# =============================================================================
# Temporal 配置
# =============================================================================

# Temporal Server 地址（默认 localhost:7233）
# TEMPORAL_ADDRESS=localhost:7233

# =============================================================================
# 开发选项
# =============================================================================

# 调试日志
# SHANNON_DEBUG=false

# 每个 agent 运行的最大预算（美元，可选）
# SHANNON_MAX_BUDGET=1.0
```

- [ ] **Step 2: Commit**

```bash
git add .env.example
git commit -m "docs: simplify .env.example to zero-config ANTHROPIC_API_KEY entry"
```

---

### Task 4: 更新 `build_provider_config` 注释

**Files:**
- Modify: `packages/core/src/shannon_core/agents/providers.py:135-197`

- [ ] **Step 1: 更新 `build_provider_config` 函数的文档字符串**

将 `providers.py` 第 135-145 行的函数签名和文档字符串：

```python
def build_provider_config(
    provider_type: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    region: str | None = None,
    project_id: str | None = None,
    auth_token: str | None = None,
) -> ProviderConfig:
    """
    从环境变量和参数构建 ProviderConfig

    环境变量优先级: SHANNON_* > ANTHROPIC_*

    Args:
        provider_type: Provider 类型
        api_key: API Key
        base_url: Base URL
        model: 模型名称
        region: 区域
        project_id: 项目 ID
        auth_token: 认证 Token

    Returns:
        ProviderConfig: 配置对象
    """
```

替换为：

```python
def build_provider_config(
    provider_type: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    region: str | None = None,
    project_id: str | None = None,
    auth_token: str | None = None,
) -> ProviderConfig:
    """
    从环境变量和参数构建 ProviderConfig

    零配置用法: 只需设置 ANTHROPIC_API_KEY 环境变量即可。
    SHANNON_* 变量用于覆盖默认行为。

    环境变量优先级: 参数 > SHANNON_* > ANTHROPIC_*

    Args:
        provider_type: Provider 类型（默认 anthropic_api）
        api_key: API Key（默认从 SHANNON_API_KEY > ANTHROPIC_API_KEY 读取）
        base_url: Base URL（默认从 SHANNON_BASE_URL > ANTHROPIC_BASE_URL 读取）
        model: 模型名称（默认从 SHANNON_MODEL > ANTHROPIC_MODEL 读取）
        region: 区域（用于 Bedrock / Vertex）
        project_id: 项目 ID（用于 Vertex）
        auth_token: 认证 Token（用于 LiteLLM）

    Returns:
        ProviderConfig: 配置对象
    """
```

- [ ] **Step 2: 运行全量 provider 测试确认无回归**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/agents/test_providers.py packages/core/tests/test_runner.py -v`

预期: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add packages/core/src/shannon_core/agents/providers.py
git commit -m "docs: update build_provider_config docstring for zero-config clarity"
```

---

### Task 5: 端到端验证

**Files:**
- 无新文件

- [ ] **Step 1: 运行全部核心测试套件**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/ -v --tb=short`

预期: ALL PASS

- [ ] **Step 2: 验证只有 `ANTHROPIC_API_KEY` 时 `build_provider_config` 产生正确配置**

手动确认逻辑路径：
1. `build_provider_config()` 在只有 `ANTHROPIC_API_KEY` 时 → `config.api_key` 有值，`config.type` = `"anthropic_api"`
2. `AnthropicProvider._build_options()` → `SHANNON_API_KEY` 和 `SHANNON_BASE_URL` 均为 None → `explicit_env` 为空 → 不设 `options.env`
3. SDK 从进程环境自动读取 `ANTHROPIC_API_KEY`

- [ ] **Step 3: Final commit（如有格式调整）**

```bash
git add -A
git commit -m "chore: zero-config Claude experience complete"
```
