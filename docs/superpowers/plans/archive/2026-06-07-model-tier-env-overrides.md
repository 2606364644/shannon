# Model Tier Environment Variable Overrides Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add `SHANNON_SMALL_MODEL`, `SHANNON_MEDIUM_MODEL`, `SHANNON_LARGE_MODEL` environment variables so users can override model names per tier independently, instead of using the single global `SHANNON_MODEL` which disables tier-based model selection.

**Architecture:** Extend `ProviderConfig` with three optional tier-specific model fields. `build_provider_config()` reads the three new env vars. Both `_get_model()` methods check tier-specific overrides first, then fall back to the global `model` field, then to `DEFAULT_MODELS`. This preserves backward compatibility — existing `SHANNON_MODEL` usage continues to work unchanged.

**Tech Stack:** Python 3.12+, pytest, pytest-asyncio

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `packages/core/src/shannon_core/agents/runner.py` | Add `small_model`, `medium_model`, `large_model` fields to `ProviderConfig` |
| Modify | `packages/core/src/shannon_core/agents/providers.py` | Read `SHANNON_SMALL_MODEL`, `SHANNON_MEDIUM_MODEL`, `SHANNON_LARGE_MODEL` env vars in `build_provider_config()` |
| Modify | `packages/core/src/shannon_core/agents/providers_anthropic.py` | Update `_get_model()` to check tier-specific overrides before global fallback |
| Modify | `packages/core/src/shannon_core/agents/providers_openai.py` | Update `_get_model()` to check tier-specific overrides before global fallback |
| Modify | `packages/core/tests/agents/test_providers.py` | Add tests for tier-specific model resolution |
| Modify | `.env.example` | Document the three new env vars |

---

### Task 1: Add tier-specific model fields to ProviderConfig

**Files:**
- Modify: `packages/core/src/shannon_core/agents/runner.py:22-36`
- Test: `packages/core/tests/agents/test_providers.py`

- [x] **Step 1: Write the failing test for new ProviderConfig fields**

Add to `TestProviderConfig` class in `packages/core/tests/agents/test_providers.py`:

```python
def test_tier_specific_model_fields_default_to_none(self):
    """Tier-specific model fields default to None"""
    config = ProviderConfig()
    assert config.small_model is None
    assert config.medium_model is None
    assert config.large_model is None

def test_tier_specific_model_fields_can_be_set(self):
    """Tier-specific model fields can be explicitly set"""
    config = ProviderConfig(
        small_model="claude-haiku-4-5-20251001",
        medium_model="claude-sonnet-4-6",
        large_model="claude-opus-4-8",
    )
    assert config.small_model == "claude-haiku-4-5-20251001"
    assert config.medium_model == "claude-sonnet-4-6"
    assert config.large_model == "claude-opus-4-8"
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/agents/test_providers.py::TestProviderConfig::test_tier_specific_model_fields_default_to_none -v`
Expected: FAIL — `ProviderConfig.__init__()` got unexpected keyword arguments

- [x] **Step 3: Add the three fields to ProviderConfig**

In `packages/core/src/shannon_core/agents/runner.py`, add after line 36 (`auth_token: str | None = None`):

```python
    small_model: str | None = None
    medium_model: str | None = None
    large_model: str | None = None
```

The full `ProviderConfig` dataclass becomes:

```python
@dataclass
class ProviderConfig:
    """AI Provider 配置"""
    type: Literal[
        "anthropic_api",
        "bedrock",
        "vertex",
        "openai_compatible",
        "litellm_router"
    ] = "anthropic_api"
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    region: str | None = None
    project_id: str | None = None
    auth_token: str | None = None
    small_model: str | None = None
    medium_model: str | None = None
    large_model: str | None = None
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/agents/test_providers.py::TestProviderConfig -v`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/agents/runner.py packages/core/tests/agents/test_providers.py
git commit -m "feat(core): add tier-specific model fields to ProviderConfig"
```

---

### Task 2: Read tier-specific env vars in build_provider_config()

**Files:**
- Modify: `packages/core/src/shannon_core/agents/providers.py:135-200`
- Test: `packages/core/tests/agents/test_providers.py`

- [x] **Step 1: Write the failing tests for tier-specific env var reading**

Add to `TestBuildProviderConfig` class in `packages/core/tests/agents/test_providers.py`:

```python
def test_tier_specific_env_vars(self):
    """测试 SHANNON_*_MODEL 环境变量"""
    with patch.dict(os.environ, {
        "SHANNON_SMALL_MODEL": "custom-small",
        "SHANNON_MEDIUM_MODEL": "custom-medium",
        "SHANNON_LARGE_MODEL": "custom-large",
    }):
        config = build_provider_config()
        assert config.small_model == "custom-small"
        assert config.medium_model == "custom-medium"
        assert config.large_model == "custom-large"

def test_tier_specific_env_vars_partial(self):
    """测试只设置部分 tier 变量"""
    with patch.dict(os.environ, {
        "SHANNON_MEDIUM_MODEL": "custom-medium",
    }):
        config = build_provider_config()
        assert config.small_model is None
        assert config.medium_model == "custom-medium"
        assert config.large_model is None

def test_tier_specific_env_vars_default_to_none(self):
    """测试不设置 tier 变量时默认为 None"""
    with patch.dict(os.environ, {}, clear=True):
        config = build_provider_config()
        assert config.small_model is None
        assert config.medium_model is None
        assert config.large_model is None
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/agents/test_providers.py::TestBuildProviderConfig::test_tier_specific_env_vars -v`
Expected: FAIL — `assert None == "custom-small"`

- [x] **Step 3: Add env var reading to build_provider_config()**

In `packages/core/src/shannon_core/agents/providers.py`, add the `small_model`, `medium_model`, `large_model` parameters to the function signature and the env var reading logic. The function signature (line 135-143) becomes:

```python
def build_provider_config(
    provider_type: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    region: str | None = None,
    project_id: str | None = None,
    auth_token: str | None = None,
    small_model: str | None = None,
    medium_model: str | None = None,
    large_model: str | None = None,
) -> ProviderConfig:
```

Add the env var reading after the `auth_token` block (after line 190) and before the `return` statement:

```python
    # Tier-specific model overrides
    if small_model is None:
        small_model = os.getenv("SHANNON_SMALL_MODEL")
    if medium_model is None:
        medium_model = os.getenv("SHANNON_MEDIUM_MODEL")
    if large_model is None:
        large_model = os.getenv("SHANNON_LARGE_MODEL")
```

Update the `return ProviderConfig(...)` call (line 192-200) to include the new fields:

```python
    return ProviderConfig(
        type=provider_type,  # type: ignore
        api_key=api_key,
        base_url=base_url,
        model=model,
        region=region,
        project_id=project_id,
        auth_token=auth_token,
        small_model=small_model,
        medium_model=medium_model,
        large_model=large_model,
    )
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/agents/test_providers.py::TestBuildProviderConfig -v`
Expected: PASS

- [x] **Step 5: Run the full existing test suite to verify no regressions**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/agents/test_providers.py -v`
Expected: All tests PASS

- [x] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/agents/providers.py packages/core/tests/agents/test_providers.py
git commit -m "feat(core): read SHANNON_*_MODEL tier env vars in build_provider_config"
```

---

### Task 3: Update AnthropicProvider._get_model() with tier priority

**Files:**
- Modify: `packages/core/src/shannon_core/agents/providers_anthropic.py:29-43`
- Test: `packages/core/tests/agents/test_providers.py`

- [x] **Step 1: Write the failing tests for AnthropicProvider tier-specific model resolution**

Add a new test class in `packages/core/tests/agents/test_providers.py`:

```python
class TestAnthropicProviderTierModelResolution:
    """测试 AnthropicProvider tier-specific 模型解析优先级"""

    def test_tier_specific_override_takes_priority(self):
        """Tier-specific override 优先于 global model 和默认值"""
        config = ProviderConfig(
            type="anthropic_api",
            model="global-model",
            medium_model="custom-medium",
        )
        provider = AnthropicProvider(config)
        assert provider._get_model("medium") == "custom-medium"

    def test_tier_specific_small_model(self):
        """small_model 覆盖 small tier"""
        config = ProviderConfig(
            type="anthropic_api",
            small_model="custom-small",
        )
        provider = AnthropicProvider(config)
        assert provider._get_model("small") == "custom-small"

    def test_tier_specific_large_model(self):
        """large_model 覆盖 large tier"""
        config = ProviderConfig(
            type="anthropic_api",
            large_model="custom-large",
        )
        provider = AnthropicProvider(config)
        assert provider._get_model("large") == "custom-large"

    def test_global_model_used_when_no_tier_override(self):
        """没有 tier override 时使用 global model"""
        config = ProviderConfig(
            type="anthropic_api",
            model="global-model",
            small_model="custom-small",
        )
        provider = AnthropicProvider(config)
        # medium 没有设置专属覆盖，应使用 global model
        assert provider._get_model("medium") == "global-model"
        # small 有专属覆盖
        assert provider._get_model("small") == "custom-small"

    def test_default_used_when_no_overrides(self):
        """没有覆盖时使用 DEFAULT_MODELS"""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)
        assert provider._get_model("small") == "claude-haiku-4-5-20251001"
        assert provider._get_model("medium") == "claude-sonnet-4-6"
        assert provider._get_model("large") == "claude-opus-4-8"

    def test_tier_override_for_bedrock(self):
        """Bedrock provider 的 tier override"""
        config = ProviderConfig(
            type="bedrock",
            medium_model="custom-bedrock-medium",
        )
        provider = AnthropicProvider(config)
        assert provider._get_model("medium") == "custom-bedrock-medium"
        # small 没有 tier override，使用 Bedrock 默认值
        assert provider._get_model("small") == "us.anthropic.claude-haiku-4-5"

    def test_tier_override_for_vertex(self):
        """Vertex provider 的 tier override"""
        config = ProviderConfig(
            type="vertex",
            large_model="custom-vertex-large",
        )
        provider = AnthropicProvider(config)
        assert provider._get_model("large") == "custom-vertex-large"
        # medium 没有 tier override，使用 Vertex 默认值
        assert provider._get_model("medium") == "claude-sonnet-4-6@latest"
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/agents/test_providers.py::TestAnthropicProviderTierModelResolution -v`
Expected: FAIL — `assert "claude-sonnet-4-6" == "custom-medium"` (current code ignores tier-specific fields)

- [x] **Step 3: Update AnthropicProvider._get_model()**

Replace the `_get_model()` method in `packages/core/src/shannon_core/agents/providers_anthropic.py` (lines 29-43) with:

```python
    def _get_model(self, model_tier: str) -> str:
        """根据 tier 获取模型名称

        优先级: tier-specific override > global model > DEFAULT_MODELS
        """
        # 1. Tier-specific override (最高优先级)
        tier_models = {
            "small": self.config.small_model,
            "medium": self.config.medium_model,
            "large": self.config.large_model,
        }
        tier_model = tier_models.get(model_tier)
        if tier_model:
            return tier_model

        # 2. Global model fallback
        if self.config.model:
            return self.config.model

        # 3. DEFAULT_MODELS (最低优先级)
        provider_key = "anthropic_api"
        if self.type == "bedrock":
            provider_key = "bedrock"
        elif self.type == "vertex":
            provider_key = "vertex"

        models = DEFAULT_MODELS.get(provider_key, DEFAULT_MODELS["anthropic_api"])
        return models.get(model_tier, models.get("medium", "claude-sonnet-4-6"))
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/agents/test_providers.py::TestAnthropicProviderTierModelResolution -v`
Expected: PASS

- [x] **Step 5: Run existing AnthropicProvider tests to verify no regressions**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/agents/test_providers.py::TestAnthropicProvider -v`
Expected: All tests PASS (existing `test_get_model_default` and `test_get_model_explicit` still pass because the new logic is additive — tier-specific fields are `None` by default)

- [x] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/agents/providers_anthropic.py packages/core/tests/agents/test_providers.py
git commit -m "feat(core): AnthropicProvider uses tier-specific model overrides"
```

---

### Task 4: Update OpenAIProvider._get_model() with tier priority

**Files:**
- Modify: `packages/core/src/shannon_core/agents/providers_openai.py:37-49`
- Test: `packages/core/tests/agents/test_providers.py`

- [x] **Step 1: Write the failing tests for OpenAIProvider tier-specific model resolution**

Add a new test class in `packages/core/tests/agents/test_providers.py`:

```python
class TestOpenAIProviderTierModelResolution:
    """测试 OpenAIProvider tier-specific 模型解析优先级"""

    def test_tier_specific_override_takes_priority(self):
        """Tier-specific override 优先于 global model 和默认值"""
        config = ProviderConfig(
            type="openai_compatible",
            model="global-model",
            medium_model="custom-medium",
        )
        provider = OpenAIProvider(config)
        assert provider._get_model("medium") == "custom-medium"

    def test_tier_specific_small_model(self):
        """small_model 覆盖 small tier"""
        config = ProviderConfig(
            type="openai_compatible",
            small_model="custom-small",
        )
        provider = OpenAIProvider(config)
        assert provider._get_model("small") == "custom-small"

    def test_tier_specific_large_model(self):
        """large_model 覆盖 large tier"""
        config = ProviderConfig(
            type="openai_compatible",
            large_model="custom-large",
        )
        provider = OpenAIProvider(config)
        assert provider._get_model("large") == "custom-large"

    def test_global_model_used_when_no_tier_override(self):
        """没有 tier override 时使用 global model"""
        config = ProviderConfig(
            type="openai_compatible",
            model="global-model",
            small_model="custom-small",
        )
        provider = OpenAIProvider(config)
        assert provider._get_model("medium") == "global-model"
        assert provider._get_model("small") == "custom-small"

    def test_default_used_when_no_overrides(self):
        """没有覆盖时使用 DEFAULT_MODELS"""
        config = ProviderConfig(type="openai_compatible")
        provider = OpenAIProvider(config)
        assert provider._get_model("small") == "gpt-4o-mini"
        assert provider._get_model("medium") == "gpt-4o"
        assert provider._get_model("large") == "o1"

    def test_tier_override_for_litellm_router(self):
        """LiteLLM router 的 tier override"""
        config = ProviderConfig(
            type="litellm_router",
            medium_model="custom-litellm-medium",
        )
        provider = OpenAIProvider(config)
        assert provider._get_model("medium") == "custom-litellm-medium"
        # small 没有 tier override，使用 litellm_router 默认值
        assert provider._get_model("small") == "anthropic/claude-haiku-4-5"
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/agents/test_providers.py::TestOpenAIProviderTierModelResolution -v`
Expected: FAIL — `assert "gpt-4o" == "custom-medium"`

- [x] **Step 3: Update OpenAIProvider._get_model()**

Replace the `_get_model()` method in `packages/core/src/shannon_core/agents/providers_openai.py` (lines 37-49) with:

```python
    def _get_model(self, model_tier: str) -> str:
        """根据 tier 获取模型名称

        优先级: tier-specific override > global model > DEFAULT_MODELS
        """
        # 1. Tier-specific override (最高优先级)
        tier_models = {
            "small": self.config.small_model,
            "medium": self.config.medium_model,
            "large": self.config.large_model,
        }
        tier_model = tier_models.get(model_tier)
        if tier_model:
            return tier_model

        # 2. Global model fallback
        if self.config.model:
            return self.config.model

        # 3. DEFAULT_MODELS (最低优先级)
        if self.type == "litellm_router":
            models = DEFAULT_MODELS.get("litellm_router", DEFAULT_MODELS["anthropic_api"])
        else:
            models = DEFAULT_MODELS.get("openai_compatible", DEFAULT_MODELS["openai_compatible"])

        return models.get(model_tier, models.get("medium", "gpt-4o"))
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/agents/test_providers.py::TestOpenAIProviderTierModelResolution -v`
Expected: PASS

- [x] **Step 5: Run existing OpenAIProvider tests to verify no regressions**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/agents/test_providers.py::TestOpenAIProvider -v`
Expected: All tests PASS

- [x] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/agents/providers_openai.py packages/core/tests/agents/test_providers.py
git commit -m "feat(core): OpenAIProvider uses tier-specific model overrides"
```

---

### Task 5: Integration tests for end-to-end priority chain

**Files:**
- Modify: `packages/core/tests/agents/test_providers.py`

- [x] **Step 1: Write integration tests for the full priority chain**

Add a new test class in `packages/core/tests/agents/test_providers.py`:

```python
class TestTierModelEnvVarIntegration:
    """端到端测试：环境变量 → build_provider_config → Provider._get_model()"""

    def test_single_tier_override_others_use_defaults(self):
        """设置 SHANNON_MEDIUM_MODEL 后，只有 medium tier 被覆盖"""
        with patch.dict(os.environ, {
            "SHANNON_MEDIUM_MODEL": "gpt-4o",
        }, clear=True):
            config = build_provider_config()
            provider = AnthropicProvider(config)

        assert provider._get_model("medium") == "gpt-4o"
        assert provider._get_model("small") == "claude-haiku-4-5-20251001"
        assert provider._get_model("large") == "claude-opus-4-8"

    def test_tier_override_plus_global_fallback(self):
        """SHANNON_MODEL + SHANNON_LARGE_MODEL：large 用 tier override，其余用 global"""
        with patch.dict(os.environ, {
            "SHANNON_MODEL": "fallback-model",
            "SHANNON_LARGE_MODEL": "custom-large",
        }, clear=True):
            config = build_provider_config()
            provider = AnthropicProvider(config)

        assert provider._get_model("large") == "custom-large"
        assert provider._get_model("medium") == "fallback-model"
        assert provider._get_model("small") == "fallback-model"

    def test_no_overrides_all_defaults(self):
        """不设置任何覆盖变量，所有 tier 使用 DEFAULT_MODELS"""
        with patch.dict(os.environ, {}, clear=True):
            config = build_provider_config()
            provider = AnthropicProvider(config)

        assert provider._get_model("small") == "claude-haiku-4-5-20251001"
        assert provider._get_model("medium") == "claude-sonnet-4-6"
        assert provider._get_model("large") == "claude-opus-4-8"

    def test_bedrock_tier_override_with_env(self):
        """Bedrock provider 通过环境变量覆盖 tier"""
        with patch.dict(os.environ, {
            "SHANNON_AI_PROVIDER": "bedrock",
            "SHANNON_SMALL_MODEL": "custom-bedrock-small",
        }, clear=True):
            config = build_provider_config()
            provider = AnthropicProvider(config)

        assert provider._get_model("small") == "custom-bedrock-small"
        assert provider._get_model("medium") == "us.anthropic.claude-sonnet-4-6"

    def test_vertex_tier_override_with_env(self):
        """Vertex provider 通过环境变量覆盖 tier"""
        with patch.dict(os.environ, {
            "SHANNON_AI_PROVIDER": "vertex",
            "SHANNON_LARGE_MODEL": "custom-vertex-large",
        }, clear=True):
            config = build_provider_config()
            provider = AnthropicProvider(config)

        assert provider._get_model("large") == "custom-vertex-large"
        assert provider._get_model("small") == "claude-haiku-4-5@latest"

    def test_openai_tier_override_with_env(self):
        """OpenAI compatible provider 通过环境变量覆盖 tier"""
        with patch.dict(os.environ, {
            "SHANNON_AI_PROVIDER": "openai_compatible",
            "SHANNON_MEDIUM_MODEL": "gpt-4o-turbo",
        }, clear=True):
            config = build_provider_config()
            provider = OpenAIProvider(config)

        assert provider._get_model("medium") == "gpt-4o-turbo"
        assert provider._get_model("small") == "gpt-4o-mini"

    def test_litellm_tier_override_with_env(self):
        """LiteLLM router 通过环境变量覆盖 tier"""
        with patch.dict(os.environ, {
            "SHANNON_AI_PROVIDER": "litellm_router",
            "SHANNON_LARGE_MODEL": "anthropic/claude-opus-4-8-custom",
        }, clear=True):
            config = build_provider_config()
            provider = OpenAIProvider(config)

        assert provider._get_model("large") == "anthropic/claude-opus-4-8-custom"
        assert provider._get_model("medium") == "anthropic/claude-sonnet-4-6"

    def test_all_three_tiers_overridden(self):
        """三个 tier 全部覆盖"""
        with patch.dict(os.environ, {
            "SHANNON_SMALL_MODEL": "my-small",
            "SHANNON_MEDIUM_MODEL": "my-medium",
            "SHANNON_LARGE_MODEL": "my-large",
        }, clear=True):
            config = build_provider_config()
            provider = AnthropicProvider(config)

        assert provider._get_model("small") == "my-small"
        assert provider._get_model("medium") == "my-medium"
        assert provider._get_model("large") == "my-large"

    def test_tier_override_beats_shannon_model(self):
        """SHANNON_*_MODEL 优先级高于 SHANNON_MODEL"""
        with patch.dict(os.environ, {
            "SHANNON_MODEL": "global-model",
            "SHANNON_MEDIUM_MODEL": "tier-medium",
        }, clear=True):
            config = build_provider_config()
            provider = AnthropicProvider(config)

        assert provider._get_model("medium") == "tier-medium"
        assert provider._get_model("small") == "global-model"
```

- [x] **Step 2: Run the integration tests**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/agents/test_providers.py::TestTierModelEnvVarIntegration -v`
Expected: PASS

- [x] **Step 3: Commit**

```bash
git add packages/core/tests/agents/test_providers.py
git commit -m "test(core): add integration tests for tier model env var priority chain"
```

---

### Task 6: Update .env.example with new env vars

**Files:**
- Modify: `.env.example`

- [x] **Step 1: Add tier-specific model override documentation to .env.example**

Add the following section after the `SHANNON_MODEL` line (after line 25) in `.env.example`:

```bash
# =============================================================================
# 模型层级覆盖（可选）
# =============================================================================
# 覆盖各 tier 使用的默认模型。不设置则使用内置默认值。
# 优先级：SHANNON_*_MODEL > SHANNON_MODEL > 内置默认值

# SHANNON_SMALL_MODEL=claude-haiku-4-5-20251001
# SHANNON_MEDIUM_MODEL=claude-sonnet-4-6
# SHANNON_LARGE_MODEL=claude-opus-4-8
```

- [x] **Step 2: Verify the file looks correct**

Run: `cat /Users/mango/project/shannon-refactor/shannon-py/.env.example`
Expected: The new section appears between the advanced config section and the Bedrock section

- [x] **Step 3: Commit**

```bash
git add .env.example
git commit -m "docs: add SHANNON_*_MODEL tier override variables to .env.example"
```

---

### Task 7: Run full test suite and verify

**Files:**
- None (verification only)

- [x] **Step 1: Run the complete provider test suite**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/agents/test_providers.py -v`
Expected: All tests PASS (0 failures)

- [x] **Step 2: Run the full core test suite**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/ -v`
Expected: All tests PASS (0 failures)

- [x] **Step 3: Verify the priority chain with a quick manual check**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -c "
import os
os.environ.clear()
os.environ['SHANNON_MEDIUM_MODEL'] = 'test-medium'
os.environ['SHANNON_MODEL'] = 'test-global'

from shannon_core.agents.providers import build_provider_config
from shannon_core.agents.providers_anthropic import AnthropicProvider

config = build_provider_config()
provider = AnthropicProvider(config)

print(f'small: {provider._get_model(\"small\")}')   # test-global
print(f'medium: {provider._get_model(\"medium\")}') # test-medium
print(f'large: {provider._get_model(\"large\")}')   # test-global
"`
Expected output:
```
small: test-global
medium: test-medium
large: test-global
```
