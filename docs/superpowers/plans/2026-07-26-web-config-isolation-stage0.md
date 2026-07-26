# P3c 阶段 0：配置抽象地基 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `ProviderConfig` 升级为 provider 配置的**唯一**载体——收编引擎内部 5 处 `os.getenv`（`CLAUDE_MAX_TURNS` / `CLAUDE_CODE_MAX_OUTPUT_TOKENS` / `CLAUDE_ADAPTIVE_THINKING` / `SUPERNOVA_OPENAI_MAX_TURNS` / `SUPERNOVA_OPENAI_SUBAGENT_MAX_TURNS` / `SUPERNOVA_OPENAI_CALL_TIMEOUT`），让它们可经 `ProviderConfig` 字段覆盖，为 P3c 阶段 1（穿线）+ 阶段 2（per-ws 配置）铺路。**纯重构，零行为改变。**

**Architecture:** 采用「字段 `None` 回落 env」语义——`ProviderConfig` 新增 5 个运行时调参字段（默认 `None`）；`build_provider_config` 接受对应参数（默认 `None`，**不主动读 env**，只透传）；引擎读 `self.config.<字段>`，`None` 时回落原 `os.getenv`。这样：阶段 0 调用点不传新参数 → 字段 `None` → 引擎回落 env → **行为逐字节不变**；阶段 2 ws 填字段 → 字段非 `None` → 引擎用字段（ws 覆盖 + 全局回落的语义天然成立）。

**Tech Stack:** Python 3.11+ / dataclasses / pytest / monkeypatch（env 隔离）/ 现有 `supernova_core.agents` 模块。

## Global Constraints

- **纯重构，零行为改变**：任何调用点不传新参数时，引擎输出与改造前逐字节一致（由现有 provider/pricing/runner 回归 + 新增回落测试共同保证）。
- **方案 Y（字段 None 回落 env）**：`build_provider_config` **不主动读**新字段对应的 env，只透传显式参数；引擎负责 `None` 时回落 env。**不要**改成「build 从 env 读填入字段」（那是方案 X，会破坏阶段 2 的 ws 覆盖语义）。
- **范围仅 5 个引擎调参**：`max_turns` / `subagent_max_turns` / `max_output_tokens` / `call_timeout` / `adaptive_thinking`。**不**收编 `SUPERNOVA_PRICING_OVERRIDE` / `SUPERNOVA_MODEL_CONTEXT_OVERRIDE`（它们是工具模块读文件路径，per-profile 已够，per-ws 隔离收益低，YAGNI；本计划明确推迟）。
- **回落而非删除 env 读取**：每个收编点的 `os.getenv` 必须保留在回落分支（`if self.config.X is not None: ... else: os.getenv(...)`），不得删除。
- **不动 `PASSTHROUGH_VARS`**（`providers_anthropic.py:223-244`）：那是 CLI 子进程 env 透传白名单（运行时环境，非 provider 配置语义），本阶段不碰。
- **不动 `_get_client` 的 `OPENAI_API_KEY` 兜底**（`providers_openai.py:63`）：那是 client 构造的 key 兜底，已半对（`self.config.api_key or env`），非本次 5 字段范围。
- **测试隔离**：env 读取测试一律用 `monkeypatch.setenv/delenv`，不得污染全局 env；按 CLAUDE.md「只跑改动相关测试文件，勿广跑全套」——每个 task 只跑该 task 涉及的测试文件。

---

## File Structure

| 文件 | 职责 | 本计划改动 |
|---|---|---|
| `packages/core/src/supernova_core/agents/runner.py` | `ProviderConfig` dataclass 定义 | 扩 5 字段（Task 1） |
| `packages/core/src/supernova_core/agents/providers.py` | `build_provider_config` + `_build_from_settings` + `_build_legacy` | 扩 5 参数透传（Task 1） |
| `packages/core/src/supernova_core/agents/providers_anthropic.py` | `AnthropicProvider`（CLI 子进程引擎） | `_build_sdk_env` / `_build_options` / `_is_adaptive_thinking_enabled` 改读 `self.config` + 提取 `_resolve_max_turns`（Task 2） |
| `packages/core/src/supernova_core/agents/providers_openai.py` | `OpenAIProvider`（in-process SDK 引擎） | `_max_turns` / `_subagent_max_turns` / `_call_timeout` 改读 `self.config`（Task 3） |
| `packages/core/tests/agents/test_provider_config_stage0.py` | 新建：ProviderConfig 字段 + build 透传测试 | Task 1 |
| `packages/core/tests/agents/test_providers_anthropic_stage0.py` | 新建：anthropic 引擎读 config + 回落测试 | Task 2 |
| `packages/core/tests/agents/test_providers_openai_stage0.py` | 新建：openai 引擎读 config + 回落测试 | Task 3 |
| `packages/core/tests/agents/test_stage0_env_fallback_guard.py` | 新建：env 回落保留的防回退守卫 | Task 4 |

---

## Task 1: ProviderConfig 扩 5 字段 + build_provider_config 透传

**Files:**
- Modify: `packages/core/src/supernova_core/agents/runner.py:44`（`ProviderConfig` 末尾追加字段）
- Modify: `packages/core/src/supernova_core/agents/providers.py:187-338`（`build_provider_config` + `_build_from_settings` + `_build_legacy` 加参数）
- Test: `packages/core/tests/agents/test_provider_config_stage0.py`（新建）

**Interfaces:**
- Consumes: 现有 `ProviderConfig`（10 字段）/ `build_provider_config(provider_type, api_key, ...)` 签名
- Produces: `ProviderConfig` 新增 5 字段（`max_turns` / `subagent_max_turns` / `max_output_tokens` / `call_timeout` / `adaptive_thinking`，全 `None` 默认）；`build_provider_config` 新增 5 同名 keyword 参数（全 `None` 默认，透传不读 env）。下游 Task 2/3 的引擎读这些字段。

- [ ] **Step 1: 写失败测试** — 新建 `packages/core/tests/agents/test_provider_config_stage0.py`

```python
"""P3c 阶段 0：ProviderConfig 运行时调参字段 + build_provider_config 透传。

方案 Y：字段 None = 未覆盖（引擎回落 env）；build 不主动读 env，只透传显式参数。
"""
from supernova_core.agents.runner import ProviderConfig
from supernova_core.agents.providers import build_provider_config


def test_provider_config_new_fields_default_none():
    """5 个新字段默认 None（= 未覆盖，引擎将回落 env）。"""
    cfg = ProviderConfig()
    assert cfg.max_turns is None
    assert cfg.subagent_max_turns is None
    assert cfg.max_output_tokens is None
    assert cfg.call_timeout is None
    assert cfg.adaptive_thinking is None


def test_build_provider_config_passes_runtime_params_openai():
    """openai_compatible：build 接受运行时调参并透传（不读 env）。"""
    cfg = build_provider_config(
        provider_type="openai_compatible",
        max_turns=999,
        subagent_max_turns=88,
        max_output_tokens=32000,
        call_timeout=600.0,
        adaptive_thinking=False,
    )
    assert cfg.max_turns == 999
    assert cfg.subagent_max_turns == 88
    assert cfg.max_output_tokens == 32000
    assert cfg.call_timeout == 600.0
    assert cfg.adaptive_thinking is False


def test_build_provider_config_passes_runtime_params_anthropic():
    """anthropic_api：同样透传。"""
    cfg = build_provider_config(
        provider_type="anthropic_api",
        max_turns=777,
        adaptive_thinking=True,
    )
    assert cfg.max_turns == 777
    assert cfg.adaptive_thinking is True
    # 未传的仍 None
    assert cfg.max_output_tokens is None


def test_build_provider_config_runtime_params_default_none_even_if_env_set(monkeypatch):
    """关键不变量：build 不主动读 env —— 即使 env 设了，不传参数 → 字段仍 None。

    引擎负责 None 时回落 env；build 只透传。这是阶段 2 ws 覆盖语义的前提。
    """
    monkeypatch.setenv("CLAUDE_MAX_TURNS", "777")
    monkeypatch.setenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "99999")
    monkeypatch.setenv("SUPERNOVA_OPENAI_MAX_TURNS", "555")
    cfg_anthropic = build_provider_config(provider_type="anthropic_api")
    cfg_openai = build_provider_config(provider_type="openai_compatible")
    # build 不读这些 env；字段 None，引擎稍后回落
    assert cfg_anthropic.max_turns is None
    assert cfg_anthropic.max_output_tokens is None
    assert cfg_openai.max_turns is None


def test_build_provider_config_legacy_path_passes_runtime_params():
    """bedrock/vertex/litellm_router（_build_legacy 分支）也透传新字段（一致性）。"""
    cfg = build_provider_config(
        provider_type="litellm_router",
        max_turns=432,
        call_timeout=300.0,
    )
    assert cfg.max_turns == 432
    assert cfg.call_timeout == 300.0
```

- [ ] **Step 2: 跑测试确认失败** — `cd packages/core && uv run pytest tests/agents/test_provider_config_stage0.py -v`
  - 预期：FAIL（`ProviderConfig` 无 `max_turns` 等属性 → `AttributeError` / `TypeError: unexpected keyword argument`）

- [ ] **Step 3: 扩展 ProviderConfig 字段** — 编辑 `packages/core/src/supernova_core/agents/runner.py`，在 `large_model: str | None = None`（:44）之后追加：

```python
    large_model: str | None = None
    # —— P3c 阶段 0：运行时调参，收编引擎内部 os.getenv。
    # 语义：None = 未覆盖（引擎回落 env）；非 None = 显式覆盖（阶段 2 per-ws 配置填充）。
    # 注意：build_provider_config 不主动从 env 读这些字段，只透传显式参数（方案 Y）。——
    max_turns: int | None = None              # CLAUDE_MAX_TURNS / SUPERNOVA_OPENAI_MAX_TURNS
    subagent_max_turns: int | None = None     # SUPERNOVA_OPENAI_SUBAGENT_MAX_TURNS
    max_output_tokens: int | None = None      # CLAUDE_CODE_MAX_OUTPUT_TOKENS
    call_timeout: float | None = None         # SUPERNOVA_OPENAI_CALL_TIMEOUT（秒）
    adaptive_thinking: bool | None = None     # CLAUDE_ADAPTIVE_THINKING
```

- [ ] **Step 4: 扩展 build_provider_config + 两个内部构造函数** — 编辑 `packages/core/src/supernova_core/agents/providers.py`

  4a. `build_provider_config`（:187-198）签名加 5 参数：

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
    # —— P3c 阶段 0：运行时调参透传（None=未覆盖，引擎回落 env；build 不读 env）——
    max_turns: int | None = None,
    subagent_max_turns: int | None = None,
    max_output_tokens: int | None = None,
    call_timeout: float | None = None,
    adaptive_thinking: bool | None = None,
) -> ProviderConfig:
```

  4b. 两个 dispatch 分支（:210-234）把新参数透传给 `_build_from_settings` / `_build_legacy`：

```python
    if provider_type in ("anthropic_api", "openai_compatible"):
        return _build_from_settings(
            provider_type,
            api_key=api_key,
            base_url=base_url,
            model=model,
            auth_token=auth_token,
            small_model=small_model,
            medium_model=medium_model,
            large_model=large_model,
            max_turns=max_turns,
            subagent_max_turns=subagent_max_turns,
            max_output_tokens=max_output_tokens,
            call_timeout=call_timeout,
            adaptive_thinking=adaptive_thinking,
        )

    return _build_legacy(
        provider_type,
        api_key=api_key,
        base_url=base_url,
        model=model,
        region=region,
        project_id=project_id,
        auth_token=auth_token,
        small_model=small_model,
        medium_model=medium_model,
        large_model=large_model,
        max_turns=max_turns,
        subagent_max_turns=subagent_max_turns,
        max_output_tokens=max_output_tokens,
        call_timeout=call_timeout,
        adaptive_thinking=adaptive_thinking,
    )
```

  4c. `_build_from_settings`（:245-269）签名 + 返回值加新字段：

```python
def _build_from_settings(
    provider_type: str,
    *,
    api_key: str | None,
    base_url: str | None,
    model: str | None,
    auth_token: str | None,
    small_model: str | None,
    medium_model: str | None,
    large_model: str | None,
    max_turns: int | None,
    subagent_max_turns: int | None,
    max_output_tokens: int | None,
    call_timeout: float | None,
    adaptive_thinking: bool | None,
) -> ProviderConfig:
    f = PROVIDER_SETTINGS[provider_type]
    return ProviderConfig(
        type=provider_type,  # type: ignore
        api_key=_read(api_key, f.api_key),
        base_url=_read(base_url, f.base_url),
        model=_read(model, f.model),
        region=None,
        project_id=None,
        auth_token=_read(auth_token, f.auth_token),
        small_model=_read(small_model, f.small_model),
        medium_model=_read(medium_model, f.medium_model),
        large_model=_read(large_model, f.large_model),
        # P3c 阶段 0：透传运行时调参（不读 env；None=引擎回落 env）
        max_turns=max_turns,
        subagent_max_turns=subagent_max_turns,
        max_output_tokens=max_output_tokens,
        call_timeout=call_timeout,
        adaptive_thinking=adaptive_thinking,
    )
```

  4d. `_build_legacy`（:272-338）同样：签名末尾加 5 个 keyword 参数，返回的 `ProviderConfig(...)` 末尾加 5 字段透传（与 4c 同模式）。

- [ ] **Step 5: 跑测试确认通过** — `cd packages/core && uv run pytest tests/agents/test_provider_config_stage0.py -v`
  - 预期：5 个测试全 PASS

- [ ] **Step 6: 跑现有 provider 回归确认零行为破坏** — `cd packages/core && uv run pytest tests/agents/test_providers.py tests/agents/test_providers_tier_model.py tests/test_runner.py -v`
  - 预期：全 PASS（现有 build_provider_config 调用不传新参数 → 字段 None → 行为不变）

- [ ] **Step 7: Commit**

```bash
git add packages/core/src/supernova_core/agents/runner.py \
        packages/core/src/supernova_core/agents/providers.py \
        packages/core/tests/agents/test_provider_config_stage0.py
git commit -m "feat(core/agents): P3c 阶段0 ProviderConfig 扩运行时调参字段 + build 透传

收编引擎内部 os.getenv 的第一阶段：ProviderConfig 加 max_turns/
subagent_max_turns/max_output_tokens/call_timeout/adaptive_thinking 5 字段
(默认 None)，build_provider_config 接受对应参数透传(不读 env，方案 Y)。
引擎改造(Task 2/3)跟进。零行为改变——字段 None 时引擎回落 env。"
```

---

## Task 2: AnthropicProvider 引擎读 self.config（3 字段 + 回落）

**Files:**
- Modify: `packages/core/src/supernova_core/agents/providers_anthropic.py:193-255`（`_build_sdk_env`）、`:257-326`（`_build_options`）、`:327-330`（`_is_adaptive_thinking_enabled`）
- Test: `packages/core/tests/agents/test_providers_anthropic_stage0.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `ProviderConfig.max_output_tokens` / `max_turns` / `adaptive_thinking`
- Produces: `AnthropicProvider._resolve_max_turns(override)` 纯函数（供 `_build_options` 调用）；`_build_sdk_env` / `_is_adaptive_thinking_enabled` 读 `self.config` 字段，`None` 回落 env。

- [ ] **Step 1: 写失败测试** — 新建 `packages/core/tests/agents/test_providers_anthropic_stage0.py`

```python
"""P3c 阶段 0：AnthropicProvider 读 self.config 运行时调参，None 回落 env。"""
import pytest

from supernova_core.agents.runner import ProviderConfig
from supernova_core.agents.providers_anthropic import AnthropicProvider


def _make(cfg_overrides: dict | None = None) -> AnthropicProvider:
    return AnthropicProvider(ProviderConfig(type="anthropic_api", **(cfg_overrides or {})))


# —— max_output_tokens（_build_sdk_env → CLAUDE_CODE_MAX_OUTPUT_TOKENS）——

def test_max_output_tokens_from_config(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", raising=False)
    p = _make({"max_output_tokens": 12345})
    assert p._build_sdk_env()["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "12345"


def test_max_output_tokens_falls_back_to_env(monkeypatch):
    """字段 None → 回落 env（阶段 0 行为不变）。"""
    monkeypatch.setenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "99999")
    p = _make()  # max_output_tokens=None
    assert p._build_sdk_env()["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "99999"


def test_max_output_tokens_default_when_unset(monkeypatch):
    """字段 None + env 未设 → 默认 64000（与改造前一致）。"""
    monkeypatch.delenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", raising=False)
    p = _make()
    assert p._build_sdk_env()["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "64000"


def test_max_output_tokens_not_set_when_zero_and_no_default(monkeypatch):
    """env 未设且无默认时，原逻辑不写入该 key（保改造前空串语义）。"""
    # 改造前 :198-200 max_tokens 默认 "64000" 总是非空，故总会写入。
    # 这里验证字段 0 时仍写入 "0"（显式覆盖），不混淆"未覆盖"。
    p = _make({"max_output_tokens": 0})
    assert p._build_sdk_env()["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "0"


# —— adaptive_thinking（_is_adaptive_thinking_enabled → CLAUDE_ADAPTIVE_THINKING）——

def test_adaptive_thinking_from_config_true():
    p = _make({"adaptive_thinking": True})
    assert p._is_adaptive_thinking_enabled() is True


def test_adaptive_thinking_from_config_false():
    p = _make({"adaptive_thinking": False})
    assert p._is_adaptive_thinking_enabled() is False


def test_adaptive_thinking_falls_back_to_env_false(monkeypatch):
    monkeypatch.setenv("CLAUDE_ADAPTIVE_THINKING", "false")
    p = _make()
    assert p._is_adaptive_thinking_enabled() is False


def test_adaptive_thinking_falls_back_to_env_default(monkeypatch):
    monkeypatch.delenv("CLAUDE_ADAPTIVE_THINKING", raising=False)
    p = _make()
    assert p._is_adaptive_thinking_enabled() is True  # 默认 true


# —— max_turns（_resolve_max_turns → CLAUDE_MAX_TURNS，提取纯函数便于测试）——

def test_resolve_max_turns_override_wins():
    p = _make({"max_turns": 100})
    # max_turns_override（vuln 外部）优先级最高
    assert p._resolve_max_turns(200) == 200


def test_resolve_max_turns_from_config(monkeypatch):
    monkeypatch.delenv("CLAUDE_MAX_TURNS", raising=False)
    p = _make({"max_turns": 333})
    assert p._resolve_max_turns(None) == 333


def test_resolve_max_turns_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("CLAUDE_MAX_TURNS", "250")
    p = _make()  # max_turns=None
    assert p._resolve_max_turns(None) == 250


def test_resolve_max_turns_default(monkeypatch):
    monkeypatch.delenv("CLAUDE_MAX_TURNS", raising=False)
    p = _make()
    assert p._resolve_max_turns(None) == 200  # 默认 200
```

- [ ] **Step 2: 跑测试确认失败** — `cd packages/core && uv run pytest tests/agents/test_providers_anthropic_stage0.py -v`
  - 预期：FAIL（`_resolve_max_turns` 不存在 → `AttributeError`；`_build_sdk_env` 不读 `self.config.max_output_tokens` → 字段覆盖测试失败）

- [ ] **Step 3: 提取 `_resolve_max_turns` 纯函数 + 改 `_build_options`** — 编辑 `providers_anthropic.py`

  3a. 在 `_build_options` 之前（约 :256）加纯函数：

```python
    def _resolve_max_turns(self, max_turns_override: int | None) -> int:
        """解析 max_turns，优先级：vuln 外部 override > P3c config > CLAUDE_MAX_TURNS env > 200。

        提取为纯函数便于阶段 0 测试（原内联在 _build_options:276）。
        """
        if max_turns_override is not None:
            return max_turns_override
        if self.config.max_turns is not None:
            return self.config.max_turns
        return int(os.getenv("CLAUDE_MAX_TURNS", "200"))
```

  3b. `_build_options`（:276）改用 helper：

```python
        # max_turns: high "runaway" ceiling. 优先级见 _resolve_max_turns。
        max_turns = self._resolve_max_turns(max_turns_override)
        options.max_turns = max_turns
```

- [ ] **Step 4: 改 `_build_sdk_env` 读 `max_output_tokens`** — 编辑 `providers_anthropic.py:197-200`

```python
        # Base config —— P3c 阶段 0：self.config.max_output_tokens 优先（None 回落 env）
        if self.config.max_output_tokens is not None:
            sdk_env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(self.config.max_output_tokens)
        else:
            max_tokens = os.getenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "64000")
            if max_tokens:
                sdk_env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = max_tokens
```

- [ ] **Step 5: 改 `_is_adaptive_thinking_enabled` 读 `adaptive_thinking`** — 编辑 `providers_anthropic.py:327-330`

```python
    def _is_adaptive_thinking_enabled(self) -> bool:
        """检查是否启用 adaptive thinking。

        P3c 阶段 0：self.config.adaptive_thinking 优先（None 回落 CLAUDE_ADAPTIVE_THINKING env）。
        """
        if self.config.adaptive_thinking is not None:
            return self.config.adaptive_thinking
        return os.getenv("CLAUDE_ADAPTIVE_THINKING", "true").lower() != "false"
```

- [ ] **Step 6: 跑新测试确认通过** — `cd packages/core && uv run pytest tests/agents/test_providers_anthropic_stage0.py -v`
  - 预期：12 个测试全 PASS

- [ ] **Step 7: 跑现有 anthropic 回归** — `cd packages/core && uv run pytest tests/agents/test_providers_anthropic_output_format.py tests/agents/test_providers_collector_injection.py -v`
  - 预期：全 PASS（这些测试构造 ProviderConfig 不传新字段 → 字段 None → 回落 env → 行为不变）

- [ ] **Step 8: Commit**

```bash
git add packages/core/src/supernova_core/agents/providers_anthropic.py \
        packages/core/tests/agents/test_providers_anthropic_stage0.py
git commit -m "feat(core/agents): P3c 阶段0 AnthropicProvider 读 self.config 调参

_build_sdk_env(max_output_tokens)/_is_adaptive_thinking_enabled/
_resolve_max_turns(新纯函数) 改读 self.config.<字段>，None 回落 env。
_build_options 改用 _resolve_max_turns。零行为改变（字段 None 走原 env 路径）。"
```

---

## Task 3: OpenAIProvider 引擎读 self.config（3 字段 + 回落）

**Files:**
- Modify: `packages/core/src/supernova_core/agents/providers_openai.py:73-93`（`_max_turns` / `_subagent_max_turns` / `_call_timeout`）
- Test: `packages/core/tests/agents/test_providers_openai_stage0.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `ProviderConfig.max_turns` / `subagent_max_turns` / `call_timeout`
- Produces: 三个方法的 `self.config` 优先 + env 回落。

- [ ] **Step 1: 写失败测试** — 新建 `packages/core/tests/agents/test_providers_openai_stage0.py`

```python
"""P3c 阶段 0：OpenAIProvider 读 self.config 运行时调参，None 回落 env。"""
from supernova_core.agents.runner import ProviderConfig
from supernova_core.agents.providers_openai import OpenAIProvider


def _make(cfg_overrides: dict | None = None) -> OpenAIProvider:
    return OpenAIProvider(ProviderConfig(type="openai_compatible", **(cfg_overrides or {})))


# —— _max_turns（SUPERNOVA_OPENAI_MAX_TURNS）——

def test_max_turns_from_config():
    assert _make({"max_turns": 333})._max_turns() == 333


def test_max_turns_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("SUPERNOVA_OPENAI_MAX_TURNS", "250")
    assert _make()._max_turns() == 250


def test_max_turns_default(monkeypatch):
    monkeypatch.delenv("SUPERNOVA_OPENAI_MAX_TURNS", raising=False)
    assert _make()._max_turns() == 200


# —— _subagent_max_turns（SUPERNOVA_OPENAI_SUBAGENT_MAX_TURNS）——

def test_subagent_max_turns_from_config():
    assert _make({"subagent_max_turns": 77})._subagent_max_turns() == 77


def test_subagent_max_turns_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("SUPERNOVA_OPENAI_SUBAGENT_MAX_TURNS", "60")
    assert _make()._subagent_max_turns() == 60


def test_subagent_max_turns_default(monkeypatch):
    monkeypatch.delenv("SUPERNOVA_OPENAI_SUBAGENT_MAX_TURNS", raising=False)
    assert _make()._subagent_max_turns() == 40


# —— _call_timeout（SUPERNOVA_OPENAI_CALL_TIMEOUT）——

def test_call_timeout_from_config():
    assert _make({"call_timeout": 120.0})._call_timeout() == 120.0


def test_call_timeout_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("SUPERNOVA_OPENAI_CALL_TIMEOUT", "900")
    assert _make()._call_timeout() == 900.0


def test_call_timeout_default(monkeypatch):
    monkeypatch.delenv("SUPERNOVA_OPENAI_CALL_TIMEOUT", raising=False)
    assert _make()._call_timeout() == 1800.0
```

- [ ] **Step 2: 跑测试确认失败** — `cd packages/core && uv run pytest tests/agents/test_providers_openai_stage0.py -v`
  - 预期：FAIL（`from_config` 测试失败——引擎仍读 env，忽略 `self.config`）

- [ ] **Step 3: 改三个方法读 `self.config`** — 编辑 `providers_openai.py:73-93`

```python
    def _max_turns(self) -> int:
        # P3c 阶段 0：self.config.max_turns 优先（None 回落 env）
        if self.config.max_turns is not None:
            return self.config.max_turns
        return int(os.getenv("SUPERNOVA_OPENAI_MAX_TURNS", "200"))

    def _subagent_max_turns(self) -> int:
        # 子代理（Task 委派）max_turns。结构层已硬限单层（子代理无 subagent_run
        # + 只读工具集），调大无递归风险。B2: 20→40。
        # P3c 阶段 0：self.config.subagent_max_turns 优先（None 回落 env）
        if self.config.subagent_max_turns is not None:
            return self.config.subagent_max_turns
        return int(os.getenv("SUPERNOVA_OPENAI_SUBAGENT_MAX_TURNS", "40"))

    def _call_timeout(self) -> float:
        """call() stream 消费的 wall-clock 超时（秒）—— openai 引擎自补的超时兜底。

        （原 docstring 保留，见 git 历史：deepseek stall → worker hang 兜底。）
        P3c 阶段 0：self.config.call_timeout 优先（None 回落 env，默认 1800s）。
        """
        if self.config.call_timeout is not None:
            return self.config.call_timeout
        return float(os.getenv("SUPERNOVA_OPENAI_CALL_TIMEOUT", "1800"))
```

- [ ] **Step 4: 跑新测试确认通过** — `cd packages/core && uv run pytest tests/agents/test_providers_openai_stage0.py -v`
  - 预期：9 个测试全 PASS

- [ ] **Step 5: 跑现有 openai 回归** — `cd packages/core && uv run pytest tests/agents/test_providers_openai_call_timeout.py tests/agents/test_providers_openai_call_l1.py tests/agents/test_providers_openai_reparse.py -v`
  - 预期：全 PASS（现有测试不传新字段 → 回落 env → 行为不变）

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/supernova_core/agents/providers_openai.py \
        packages/core/tests/agents/test_providers_openai_stage0.py
git commit -m "feat(core/agents): P3c 阶段0 OpenAIProvider 读 self.config 调参

_max_turns/_subagent_max_turns/_call_timeout 改读 self.config.<字段>，
None 回落 env。零行为改变（字段 None 走原 env 路径）。"
```

---

## Task 4: env 回落防回退守卫 + 全回归

**Files:**
- Test: `packages/core/tests/agents/test_stage0_env_fallback_guard.py`（新建）

**Interfaces:**
- Consumes: Task 1-3 的引擎改造
- Produces: 文本级防回退守卫——确保收编点的 env 回落分支未被删除（防止未来误删 `os.getenv` 回落，破坏"字段 None 回落 env"不变量）。

- [ ] **Step 1: 写守卫测试** — 新建 `packages/core/tests/agents/test_stage0_env_fallback_guard.py`

```python
"""P3c 阶段 0 防回退守卫：收编点的 env 回落分支必须保留。

不变量：引擎读 self.config.<字段>，且保留 os.getenv(...) 回落（字段 None 时走 env）。
若有人误删回落分支（让字段 None 时无默认），这些断言会失败。
"""
from pathlib import Path

ANTHROPIC = Path("packages/core/src/supernova_core/agents/providers_anthropic.py").read_text()
OPENAI = Path("packages/core/src/supernova_core/agents/providers_openai.py").read_text()


def test_anthropic_reads_config_fields():
    """新路径（self.config）已接入。"""
    assert "self.config.max_output_tokens" in ANTHROPIC
    assert "self.config.max_turns" in ANTHROPIC
    assert "self.config.adaptive_thinking" in ANTHROPIC


def test_anthropic_keeps_env_fallback():
    """env 回落分支保留（不得删除）。"""
    assert 'os.getenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS"' in ANTHROPIC
    assert 'os.getenv("CLAUDE_MAX_TURNS"' in ANTHROPIC
    assert 'os.getenv("CLAUDE_ADAPTIVE_THINKING"' in ANTHROPIC


def test_anthropic_resolve_max_turns_extracted():
    """_resolve_max_turns 纯函数已提取（_build_options 不再内联）。"""
    assert "def _resolve_max_turns" in ANTHROPIC


def test_openai_reads_config_fields():
    assert "self.config.max_turns" in OPENAI
    assert "self.config.subagent_max_turns" in OPENAI
    assert "self.config.call_timeout" in OPENAI


def test_openai_keeps_env_fallback():
    assert 'os.getenv("SUPERNOVA_OPENAI_MAX_TURNS"' in OPENAI
    assert 'os.getenv("SUPERNOVA_OPENAI_SUBAGENT_MAX_TURNS"' in OPENAI
    assert 'os.getenv("SUPERNOVA_OPENAI_CALL_TIMEOUT"' in OPENAI


def test_pricing_and_model_caps_NOT_refactored_this_stage():
    """范围守卫：pricing_override / model_context_override 本阶段明确不收编
    （推迟——per-profile env 已够）。确保没人提前动它们进 ProviderConfig。"""
    runner = Path("packages/core/src/supernova_core/agents/runner.py").read_text()
    assert "pricing_override" not in runner  # 未进 ProviderConfig
    assert "model_context_override" not in runner
```

- [ ] **Step 2: 跑守卫测试** — `cd packages/core && uv run pytest tests/agents/test_stage0_env_fallback_guard.py -v`
  - 预期：全 PASS（Task 1-3 已完成，文本特征都在）

- [ ] **Step 3: 跑阶段 0 全套新测试** — `cd packages/core && uv run pytest tests/agents/test_provider_config_stage0.py tests/agents/test_providers_anthropic_stage0.py tests/agents/test_providers_openai_stage0.py tests/agents/test_stage0_env_fallback_guard.py -v`
  - 预期：全 PASS（约 31 个测试）

- [ ] **Step 4: 跑广义 provider/runner 回归确认零行为破坏** — `cd packages/core && uv run pytest tests/agents/test_providers.py tests/agents/test_providers_tier_model.py tests/agents/test_providers_anthropic_output_format.py tests/agents/test_providers_collector_injection.py tests/agents/test_providers_openai_call_timeout.py tests/agents/test_dual_engine_alignment.py tests/test_runner.py tests/agents/test_pricing.py tests/agents/test_model_caps.py -v`
  - 预期：全 PASS。**若任何测试 FAIL，说明某处行为变了——必须修复到绿才能进阶段 1**（阶段 0 的硬验收 = 零行为改变）。

- [ ] **Step 5: grep 确认引擎内 os.getenv 仅剩回落 + passthrough** — 人工核验（不写测试，记录在 commit message）：
  - `grep -n "os.getenv" packages/core/src/supernova_core/agents/providers_anthropic.py` 应只见 `_build_sdk_env` 回落分支 + `PASSTHROUGH_VARS` 循环 + `_resolve_max_turns` 回落 + `_is_adaptive_thinking_enabled` 回落。
  - `grep -n "os.getenv" packages/core/src/supernova_core/agents/providers_openai.py` 应只见三方法回落 + `_get_client` 的 `OPENAI_API_KEY` 兜底（本阶段不动）。

- [ ] **Step 6: Commit**

```bash
git add packages/core/tests/agents/test_stage0_env_fallback_guard.py
git commit -m "test(core/agents): P3c 阶段0 env 回落防回退守卫 + 范围守卫

锁定收编点的 env 回落分支不得删除 + pricing/model_caps 本阶段未收编。
阶段 0 完成：ProviderConfig 为引擎调参唯一载体，5 处 os.getenv 收编，
零行为改变（全回归绿）。"
```

---

## Self-Review（plan 作者自检）

**1. Spec 覆盖**：spec §5（阶段 0）的 5.2.1（ProviderConfig 扩字段）、5.2.2（引擎改读 self.config）、5.2.3（build 扩参）→ Task 1-3 覆盖。spec §5.3 行为不变量 → Task 4 回归 + 守卫覆盖。spec §5.2.4（passthrough 不动）→ Global Constraints 明确。spec §5.2.2 提到的 pricing/model_caps → Global Constraints + Task 4 守卫明确推迟（与 spec 的细微收窄，已在 plan Architecture + Global Constraints 说明）。

**2. 占位符扫描**：无 TBD/TODO；所有 code step 有完整代码；测试有真实断言。

**3. 类型一致性**：`max_turns: int | None` / `subagent_max_turns: int | None` / `max_output_tokens: int | None` / `call_timeout: float | None` / `adaptive_thinking: bool | None`——Task 1 定义，Task 2/3 消费，类型一致。`_resolve_max_turns` 在 Task 2 定义、Task 2 消费、Task 4 守卫，命名一致。

**4. 方案 Y 一致性**：所有 task 严守"字段 None 回落 env，build 不读 env"——Task 1 Step 4c 的注释、Task 2/3 的回落分支、Task 4 的守卫，三处一致。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-26-web-config-isolation-stage0.md`. Two execution options:

1. **Subagent-Driven（推荐）** — 每个 task 派 fresh subagent，task 间 review，快速迭代
2. **Inline Execution** — 本 session 用 executing-plans 批量执行 + 检查点

Which approach?

---

**后续阶段**（本 plan 不含，各自单独 plan）：
- 阶段 1：配置穿线（PipelineInput/ActivityInput 加 `provider_config` 字段 + 调用点传参）
- 阶段 2：per-ws 配置（config.yaml + CredentialVault + WsConfigStore + admin API + 前端）
- 阶段 3：并发解锁（AuditSession/LogBus/heartbeat contextvar + worker 放宽）
- 阶段 4：clone 凭据 per-ws
