# Spec 1: 模型分级环境变量覆盖

**日期**: 2026-06-07
**状态**: 已批准

## 背景

`shannon-py` 已有完整的 `DEFAULT_MODELS` 三级模型映射（small/medium/large），`_get_model()` 也支持按 tier 选择模型。但当前 `SHANNON_MODEL` / `ANTHROPIC_MODEL` 是全局覆盖——一旦设置，所有 tier 都使用同一个模型，等于废掉了分级功能。

原始项目通过 `ANTHROPIC_SMALL_MODEL` / `ANTHROPIC_MEDIUM_MODEL` / `ANTHROPIC_LARGE_MODEL` 三个独立变量解决此问题。

## 目标

新增 `SHANNON_SMALL_MODEL` / `SHANNON_MEDIUM_MODEL` / `SHANNON_LARGE_MODEL` 三个环境变量，允许用户按 tier 独立覆盖模型名称。

## 环境变量命名

采用 `SHANNON_` 前缀命名，与新项目的 `SHANNON_*` 命名体系保持一致。

## 解析优先级

```
对于 tier=small:
  SHANNON_SMALL_MODEL > SHANNON_MODEL > DEFAULT_MODELS[provider].small

对于 tier=medium:
  SHANNON_MEDIUM_MODEL > SHANNON_MODEL > DEFAULT_MODELS[provider].medium

对于 tier=large:
  SHANNON_LARGE_MODEL > SHANNON_MODEL > DEFAULT_MODELS[provider].large
```

`SHANNON_MODEL` 作为 fallback：当某个 tier 没有设置专属变量时，使用 `SHANNON_MODEL`（如果设置了），最后才用默认值。

## 改动文件

### 1. `packages/core/src/shannon_core/agents/runner.py` → `ProviderConfig`

新增三个可选字段：

```python
@dataclass
class ProviderConfig:
    # ... 现有字段 ...
    small_model: str | None = None
    medium_model: str | None = None
    large_model: str | None = None
```

### 2. `packages/core/src/shannon_core/agents/providers.py` → `build_provider_config()`

新增三个环境变量的读取：

```python
# Tier-specific model overrides
if small_model is None:
    small_model = os.getenv("SHANNON_SMALL_MODEL")
if medium_model is None:
    medium_model = os.getenv("SHANNON_MEDIUM_MODEL")
if large_model is None:
    large_model = os.getenv("SHANNON_LARGE_MODEL")
```

### 3. `packages/core/src/shannon_core/agents/providers_anthropic.py` → `_get_model()`

修改解析逻辑：

```python
def _get_model(self, model_tier: str) -> str:
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

### 4. `packages/core/src/shannon_core/agents/providers_openai.py` → `_get_model()`

同上，使用相同的 tier 优先级逻辑。

### 5. `.env.example`

新增：

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

## 测试要点

- 设置 `SHANNON_MEDIUM_MODEL=gpt-4o`，验证 medium tier 使用 gpt-4o，small/large 仍用默认值
- 设置 `SHANNON_MODEL=fallback-model` 和 `SHANNON_LARGE_MODEL=custom-large`，验证 large 用 custom-large，其余 tier 用 fallback-model
- 不设置任何覆盖变量，验证所有 tier 使用 `DEFAULT_MODELS` 中的默认值
- Bedrock / Vertex provider 下的 tier 覆盖行为
