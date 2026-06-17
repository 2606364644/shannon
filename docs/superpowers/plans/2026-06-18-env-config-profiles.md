# 纯 .env profile 化配置 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把扁平 `.env` + 多前缀隐式 fallback 的配置,改成"共享 `.env` + 每引擎/账号一个 `.env.profiles/<name>.env`"的 profile 化结构,删除跨前缀 fallback 链,加启动校验。

**Architecture:** 三个 CLI 启动时依次调用 `env_loader.load_env()`(加载共享 + 当前 profile)与 `profile_validator.validate_active_profile()`(按 `PROVIDER_SETTINGS` 映射表校验必填变量)。`build_provider_config` 对 `anthropic_api` / `openai_compatible` 改为按 `PROVIDER_SETTINGS` 直接读取对应前缀变量(无跨前缀兜底);`bedrock` / `vertex` / `litellm_router` 保留现有读取行为(用户未使用,非本次范围)。

**Tech Stack:** Python 3、python-dotenv(已是依赖)、pytest(用 `monkeypatch` / `patch.dict`)、项目既有 `PentestError` + `ErrorCode`。

## Global Constraints

(摘自 spec,每个 task 隐式遵守)

- 依赖:`python-dotenv>=1.0` 已在 `packages/core/pyproject.toml`,**不新增依赖**。
- 错误统一用 `PentestError(message, category="config", error_code=ErrorCode.CONFIG_VALIDATION_FAILED, context=...)`,与 `config/parser.py` 既有风格一致。
- `.env.profiles/` 与 `.env` 都不进 git;本计划必须把 `.env.profiles/` 加进 `.gitignore`(当前只忽略了 `.env`)。
- 删除 fallback 的范围 = 仅 `anthropic_api` 与 `openai_compatible` 的 `api_key` / `base_url` / `auth_token` / `model` / tier-model 跨前缀兜底。`region` / `project_id` 的多 provider 兜底、`bedrock` / `vertex` / `litellm_router` 的读取、provider 类内部自身的兜底(如 `providers_openai.py:61` 的 `or os.getenv("OPENAI_API_KEY")`)**全部保留不动**。
- 测试只跑改动相关子集(用具体路径),**不要跑全量**(会卡 Temporal / 网络慢测试)。
- 参考设计:`docs/superpowers/specs/2026-06-18-env-config-design.md`。

---

## File Structure

| 文件 | 职责 | 动作 |
|---|---|---|
| `packages/core/src/shannon_core/config/env_loader.py` | 加载共享 `.env` + 当前 profile 文件 | 新建 |
| `packages/core/src/shannon_core/config/provider_settings.py` | `PROVIDER_SETTINGS` 映射:provider → 环境变量名 + 必填字段 | 新建 |
| `packages/core/src/shannon_core/config/profile_validator.py` | 按 `PROVIDER_SETTINGS` 校验当前 profile 必填变量 | 新建 |
| `packages/core/src/shannon_core/agents/providers.py` | `build_provider_config` 重构(anthropic/openai 走映射读取;其余保留) | 修改 |
| `packages/{combined,whitebox,blackbox}/src/.../cli/main.py` | 启动改用 `load_env` + `validate_active_profile` | 修改 |
| `packages/core/tests/test_env_loader.py` | env_loader 测试 | 新建 |
| `packages/core/tests/test_provider_settings.py` | 映射表测试 | 新建 |
| `packages/core/tests/test_profile_validator.py` | 校验测试 | 新建 |
| `packages/core/tests/agents/test_providers.py` | 改写 fallback 相关用例 | 修改 |
| `.env` / `.env.profiles/*.env` / `.env.example` / `.env.profiles.example/*.example` | 配置文件拆分 + 模板 | 本地迁移 + 模板入库 |
| `.gitignore` | 加 `.env.profiles/` | 修改 |

---

### Task 1: env_loader — 加载共享 .env + 当前 profile

**Files:**
- Create: `packages/core/src/shannon_core/config/env_loader.py`
- Test: `packages/core/tests/test_env_loader.py`

**Interfaces:**
- Produces: `load_env(base_path: str | Path = ".env", profiles_dir: str | Path = ".env.profiles") -> str`
  - 返回加载的 profile 名(`SHANNON_PROFILE` 的值)。
  - `SHANNON_PROFILE` 未设置 → raise `PentestError(CONFIG_VALIDATION_FAILED)`。
  - profile 文件不存在 → raise `PentestError(CONFIG_VALIDATION_FAILED)`。

- [ ] **Step 1: 写失败测试**

Create `packages/core/tests/test_env_loader.py`:

```python
"""env_loader: 共享 .env + 当前 profile 文件加载。"""
import os
from pathlib import Path

import pytest

from shannon_core.config.env_loader import load_env
from shannon_core.models.errors import ErrorCode, PentestError


def _write(path: Path, lines: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(f"{k}={v}" for k, v in lines.items()) + "\n")


def test_loads_shared_then_profile_with_override(tmp_path, monkeypatch):
    """先加载 .env(共享),再加载 profile 文件;profile 同名变量覆盖共享。"""
    for k in ("SHANNON_PROFILE", "SHARED_VAR", "PROFILE_VAR", "OVERRIDDEN"):
        monkeypatch.delenv(k, raising=False)

    _write(tmp_path / ".env", {
        "SHANNON_PROFILE": "glm-openai",
        "SHARED_VAR": "s",
        "OVERRIDDEN": "from-shared",
    })
    _write(tmp_path / ".env.profiles" / "glm-openai.env", {
        "PROFILE_VAR": "p",
        "OVERRIDDEN": "from-profile",
    })

    profile = load_env(base_path=tmp_path / ".env", profiles_dir=tmp_path / ".env.profiles")

    assert profile == "glm-openai"
    assert os.environ["SHARED_VAR"] == "s"
    assert os.environ["PROFILE_VAR"] == "p"
    assert os.environ["OVERRIDDEN"] == "from-profile"  # profile 覆盖共享


def test_missing_profile_env_raises(tmp_path, monkeypatch):
    """SHANNON_PROFILE 未设置 → PentestError。"""
    monkeypatch.delenv("SHANNON_PROFILE", raising=False)
    _write(tmp_path / ".env", {})

    with pytest.raises(PentestError) as exc:
        load_env(base_path=tmp_path / ".env", profiles_dir=tmp_path / ".env.profiles")

    assert exc.value.error_code == ErrorCode.CONFIG_VALIDATION_FAILED
    assert "SHANNON_PROFILE" in exc.value.message


def test_missing_profile_file_raises(tmp_path, monkeypatch):
    """profile 文件不存在 → PentestError,信息含 profile 名。"""
    monkeypatch.delenv("SHANNON_PROFILE", raising=False)
    _write(tmp_path / ".env", {"SHANNON_PROFILE": "nope"})

    with pytest.raises(PentestError) as exc:
        load_env(base_path=tmp_path / ".env", profiles_dir=tmp_path / ".env.profiles")

    assert exc.value.error_code == ErrorCode.CONFIG_VALIDATION_FAILED
    assert "nope" in exc.value.message
```

- [ ] **Step 2: 跑测试,确认失败**

Run: `cd packages/core && python -m pytest tests/test_env_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shannon_core.config.env_loader'`

- [ ] **Step 3: 实现 env_loader**

Create `packages/core/src/shannon_core/config/env_loader.py`:

```python
"""加载共享 .env + 当前 profile 的 .env.profiles/<name>.env。

加载顺序: 先 .env(共享), 再按 SHANNON_PROFILE 加载
.env.profiles/<profile>.env(override, 覆盖共享)。同一时刻只有
"共享 + 一个 profile" 进环境, 杜绝两套引擎配置并存。

设计见 docs/superpowers/specs/2026-06-18-env-config-design.md。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from shannon_core.models.errors import ErrorCode, PentestError

PROFILE_ENV = "SHANNON_PROFILE"


def load_env(
    base_path: str | Path = ".env",
    profiles_dir: str | Path = ".env.profiles",
) -> str:
    """加载共享 .env 与当前 profile 文件, 返回 profile 名。

    Raises:
        PentestError: SHANNON_PROFILE 未设置, 或 profile 文件不存在。
    """
    load_dotenv(base_path, override=True)

    profile = os.getenv(PROFILE_ENV)
    if not profile:
        raise PentestError(
            f"环境变量 {PROFILE_ENV} 未设置: 请在 {base_path} 中指定当前 profile"
            f"(对应 {profiles_dir}/<name>.env)",
            category="config",
            error_code=ErrorCode.CONFIG_VALIDATION_FAILED,
        )

    profile_path = Path(profiles_dir) / f"{profile}.env"
    if not profile_path.exists():
        raise PentestError(
            f"profile 文件不存在: {profile_path}(SHANNON_PROFILE={profile})。"
            f"请在该路径创建文件, 或参考 .env.profiles.example/",
            category="config",
            error_code=ErrorCode.CONFIG_VALIDATION_FAILED,
        )

    load_dotenv(profile_path, override=True)
    return profile
```

- [ ] **Step 4: 跑测试,确认通过**

Run: `cd packages/core && python -m pytest tests/test_env_loader.py -v`
Expected: PASS(3 passed)

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/config/env_loader.py packages/core/tests/test_env_loader.py
git commit -m "feat(config): 加 env_loader — 共享 .env + profile 文件加载"
```

---

### Task 2: provider_settings — provider → 环境变量映射表

**Files:**
- Create: `packages/core/src/shannon_core/config/provider_settings.py`
- Test: `packages/core/tests/test_provider_settings.py`

**Interfaces:**
- Consumes: 无。
- Produces:
  - `ProviderFields`(frozen dataclass):`base_url` / `api_key` / `auth_token` / `model` / `region` / `project_id` / `small_model` / `medium_model` / `large_model`(均 `str | None`,值为环境变量名)+ `required: tuple[str, ...]`(必填字段名,`"credential"` 表示 `api_key` / `auth_token` 二选一)。
  - `PROVIDER_SETTINGS: dict[str, ProviderFields]`:5 个 provider 的映射。
  - `get_provider_fields(provider_type: str) -> ProviderFields | None`。
- 被 Task 3(validator)与 Task 6(build_provider_config)消费。

- [ ] **Step 1: 写失败测试**

Create `packages/core/tests/test_provider_settings.py`:

```python
"""provider_settings: provider → 环境变量名映射表。"""
from shannon_core.config.provider_settings import (
    PROVIDER_SETTINGS,
    ProviderFields,
    get_provider_fields,
)


def test_anthropic_reads_anthropic_prefixed_vars():
    f = PROVIDER_SETTINGS["anthropic_api"]
    assert f.base_url == "ANTHROPIC_BASE_URL"
    assert f.api_key == "ANTHROPIC_API_KEY"
    assert f.auth_token == "ANTHROPIC_AUTH_TOKEN"
    assert f.medium_model == "SHANNON_MEDIUM_MODEL"


def test_openai_reads_openai_prefixed_vars():
    f = PROVIDER_SETTINGS["openai_compatible"]
    assert f.base_url == "SHANNON_OPENAI_BASE_URL"
    assert f.api_key == "SHANNON_OPENAI_API_KEY"
    assert f.medium_model == "SHANNON_OPENAI_MEDIUM_MODEL"


def test_anthropic_requires_credential_either_of():
    """anthropic 的 credential 是 api_key/auth_token 二选一。"""
    assert "credential" in PROVIDER_SETTINGS["anthropic_api"].required
    assert "base_url" in PROVIDER_SETTINGS["anthropic_api"].required


def test_openai_requires_api_key():
    req = PROVIDER_SETTINGS["openai_compatible"].required
    assert "api_key" in req
    assert "credential" not in req  # openai 用 api_key, 不是二选一


def test_unused_providers_have_no_required():
    """bedrock/vertex/litellm 用户未使用, 不强校验。"""
    for p in ("bedrock", "vertex", "litellm_router"):
        assert PROVIDER_SETTINGS[p].required == ()


def test_get_provider_fields_unknown_returns_none():
    assert get_provider_fields("nope") is None
    assert get_provider_fields("anthropic_api") is not None


def test_provider_fields_is_frozen():
    import dataclasses
    assert dataclasses.is_dataclass(ProviderFields)
    f = ProviderFields(base_url="X")
    try:
        f.base_url = "Y"  # frozen
        assert False, "应不可变"
    except dataclasses.FrozenInstanceError:
        pass
```

- [ ] **Step 2: 跑测试,确认失败**

Run: `cd packages/core && python -m pytest tests/test_provider_settings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shannon_core.config.provider_settings'`

- [ ] **Step 3: 实现 provider_settings**

Create `packages/core/src/shannon_core/config/provider_settings.py`:

```python
"""provider → 环境变量名的声明式映射(取代散落的 os.getenv + 跨前缀 fallback 链)。

每个 provider 显式声明它读取哪些环境变量; build_provider_config 按此表读取,
profile_validator 按此表的 required 字段校验。删除跨前缀 fallback 后,
profile 文件必须自洽地提供该 provider 的全部必填变量。

设计见 docs/superpowers/specs/2026-06-18-env-config-design.md。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderFields:
    """某 provider 从环境读取的变量名。值为环境变量名; None 表示该 provider 不读此字段。

    required: 必填字段名(ProviderFields 的属性名, 不是环境变量名)。
              特殊值 "credential" 表示 api_key 与 auth_token 二选一。
    """
    base_url: str | None
    api_key: str | None = None
    auth_token: str | None = None
    model: str | None = None
    region: str | None = None
    project_id: str | None = None
    small_model: str | None = None
    medium_model: str | None = None
    large_model: str | None = None
    required: tuple[str, ...] = ()


PROVIDER_SETTINGS: dict[str, ProviderFields] = {
    "anthropic_api": ProviderFields(
        base_url="ANTHROPIC_BASE_URL",
        api_key="ANTHROPIC_API_KEY",
        auth_token="ANTHROPIC_AUTH_TOKEN",
        model="SHANNON_MODEL",
        small_model="SHANNON_SMALL_MODEL",
        medium_model="SHANNON_MEDIUM_MODEL",
        large_model="SHANNON_LARGE_MODEL",
        required=("base_url", "credential", "small_model", "medium_model", "large_model"),
    ),
    "openai_compatible": ProviderFields(
        base_url="SHANNON_OPENAI_BASE_URL",
        api_key="SHANNON_OPENAI_API_KEY",
        model="SHANNON_MODEL",
        small_model="SHANNON_OPENAI_SMALL_MODEL",
        medium_model="SHANNON_OPENAI_MEDIUM_MODEL",
        large_model="SHANNON_OPENAI_LARGE_MODEL",
        required=("base_url", "api_key", "small_model", "medium_model", "large_model"),
    ),
    # 以下 provider 用户未使用, 保留现有读取行为, required 留空表示不做强校验。
    "litellm_router": ProviderFields(
        base_url="SHANNON_BASE_URL",
        auth_token="SHANNON_AUTH_TOKEN",
        model="SHANNON_MODEL",
        small_model="SHANNON_OPENAI_SMALL_MODEL",
        medium_model="SHANNON_OPENAI_MEDIUM_MODEL",
        large_model="SHANNON_OPENAI_LARGE_MODEL",
    ),
    "bedrock": ProviderFields(
        base_url=None,
        region="SHANNON_REGION",
        model="SHANNON_MODEL",
        small_model="SHANNON_SMALL_MODEL",
        medium_model="SHANNON_MEDIUM_MODEL",
        large_model="SHANNON_LARGE_MODEL",
    ),
    "vertex": ProviderFields(
        base_url=None,
        region="SHANNON_REGION",
        project_id="SHANNON_PROJECT_ID",
        model="SHANNON_MODEL",
        small_model="SHANNON_SMALL_MODEL",
        medium_model="SHANNON_MEDIUM_MODEL",
        large_model="SHANNON_LARGE_MODEL",
    ),
}


def get_provider_fields(provider_type: str) -> ProviderFields | None:
    """返回 provider 的字段映射; 未知 provider 返回 None。"""
    return PROVIDER_SETTINGS.get(provider_type)
```

- [ ] **Step 4: 跑测试,确认通过**

Run: `cd packages/core && python -m pytest tests/test_provider_settings.py -v`
Expected: PASS(7 passed)

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/config/provider_settings.py packages/core/tests/test_provider_settings.py
git commit -m "feat(config): 加 provider_settings — provider→环境变量映射表"
```

---

### Task 3: profile_validator — 启动校验当前 profile

**Files:**
- Create: `packages/core/src/shannon_core/config/profile_validator.py`
- Test: `packages/core/tests/test_profile_validator.py`

**Interfaces:**
- Consumes: `PROVIDER_SETTINGS`、`get_provider_fields`(Task 2)。
- Produces: `validate_active_profile() -> None`。从环境读 `SHANNON_AI_PROVIDER`,按 `PROVIDER_SETTINGS[provider].required` 校验;不满足 raise `PentestError(CONFIG_VALIDATION_FAILED)`。`bedrock` / `vertex` / `litellm_router`(`required` 为空)与未知 provider 跳过强校验。

- [ ] **Step 1: 写失败测试**

Create `packages/core/tests/test_profile_validator.py`:

```python
"""profile_validator: 按 PROVIDER_SETTINGS 校验当前 profile 必填变量。"""
import pytest

from shannon_core.config.profile_validator import validate_active_profile
from shannon_core.models.errors import ErrorCode, PentestError

# anthropic_api 完整 profile 的基准变量
ANTHROPIC_OK = {
    "SHANNON_AI_PROVIDER": "anthropic_api",
    "ANTHROPIC_BASE_URL": "https://x/anthropic",
    "ANTHROPIC_AUTH_TOKEN": "tok",
    "SHANNON_SMALL_MODEL": "GLM-4.5-Air",
    "SHANNON_MEDIUM_MODEL": "GLM-5.2[1m]",
    "SHANNON_LARGE_MODEL": "GLM-5.2[1m]",
}
OPENAI_OK = {
    "SHANNON_AI_PROVIDER": "openai_compatible",
    "SHANNON_OPENAI_BASE_URL": "https://x/v4",
    "SHANNON_OPENAI_API_KEY": "key",
    "SHANNON_OPENAI_SMALL_MODEL": "glm-4.5-air",
    "SHANNON_OPENAI_MEDIUM_MODEL": "glm-5.2",
    "SHANNON_OPENAI_LARGE_MODEL": "glm-5.2",
}


def test_anthropic_full_passes(monkeypatch):
    monkeypatch.delenv("SHANNON_AI_PROVIDER", raising=False)
    for k, v in ANTHROPIC_OK.items():
        monkeypatch.setenv(k, v)
    # 不抛即通过
    validate_active_profile()


def test_anthropic_api_key_accepted_instead_of_token(monkeypatch):
    """credential 二选一: 有 ANTHROPIC_API_KEY 也行。"""
    monkeypatch.delenv("SHANNON_AI_PROVIDER", raising=False)
    env = {**ANTHROPIC_OK}
    del env["ANTHROPIC_AUTH_TOKEN"]
    env["ANTHROPIC_API_KEY"] = "sk"
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    validate_active_profile()


def test_anthropic_missing_credential_raises(monkeypatch):
    monkeypatch.delenv("SHANNON_AI_PROVIDER", raising=False)
    env = {**ANTHROPIC_OK}
    del env["ANTHROPIC_AUTH_TOKEN"]  # 既无 token 也无 api_key
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(PentestError) as exc:
        validate_active_profile()
    assert exc.value.error_code == ErrorCode.CONFIG_VALIDATION_FAILED
    assert "credential" in exc.value.message or "api_key" in exc.value.message


def test_anthropic_missing_base_url_raises(monkeypatch):
    monkeypatch.delenv("SHANNON_AI_PROVIDER", raising=False)
    env = {**ANTHROPIC_OK}
    del env["ANTHROPIC_BASE_URL"]
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(PentestError) as exc:
        validate_active_profile()
    assert "ANTHROPIC_BASE_URL" in exc.value.message


def test_anthropic_missing_medium_model_raises(monkeypatch):
    monkeypatch.delenv("SHANNON_AI_PROVIDER", raising=False)
    env = {**ANTHROPIC_OK}
    del env["SHANNON_MEDIUM_MODEL"]
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(PentestError) as exc:
        validate_active_profile()
    assert "SHANNON_MEDIUM_MODEL" in exc.value.message


def test_openai_full_passes(monkeypatch):
    monkeypatch.delenv("SHANNON_AI_PROVIDER", raising=False)
    for k, v in OPENAI_OK.items():
        monkeypatch.setenv(k, v)
    validate_active_profile()


def test_openai_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("SHANNON_AI_PROVIDER", raising=False)
    env = {**OPENAI_OK}
    del env["SHANNON_OPENAI_API_KEY"]
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(PentestError) as exc:
        validate_active_profile()
    assert "SHANNON_OPENAI_API_KEY" in exc.value.message


def test_openai_missing_base_url_raises(monkeypatch):
    monkeypatch.delenv("SHANNON_AI_PROVIDER", raising=False)
    env = {**OPENAI_OK}
    del env["SHANNON_OPENAI_BASE_URL"]
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(PentestError) as exc:
        validate_active_profile()
    assert "SHANNON_OPENAI_BASE_URL" in exc.value.message


def test_bedrock_skips_strict_validation(monkeypatch):
    """bedrock required 为空, 不做强校验, 不抛。"""
    monkeypatch.setenv("SHANNON_AI_PROVIDER", "bedrock")
    validate_active_profile()


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("SHANNON_AI_PROVIDER", "bogus")
    with pytest.raises(PentestError) as exc:
        validate_active_profile()
    assert exc.value.error_code == ErrorCode.CONFIG_VALIDATION_FAILED
```

- [ ] **Step 2: 跑测试,确认失败**

Run: `cd packages/core && python -m pytest tests/test_profile_validator.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shannon_core.config.profile_validator'`

- [ ] **Step 3: 实现 profile_validator**

Create `packages/core/src/shannon_core/config/profile_validator.py`:

```python
"""启动校验: 当前 profile 的变量与声明的 SHANNON_AI_PROVIDER 是否自洽。

按 PROVIDER_SETTINGS[provider].required 校验必填变量; 不满足则启动即失败
(PentestError, CONFIG_VALIDATION_FAILED), 不再静默 fallback 到错变量。

设计见 docs/superpowers/specs/2026-06-18-env-config-design.md 第 6 节。
"""
from __future__ import annotations

import os

from shannon_core.config.provider_settings import get_provider_fields
from shannon_core.models.errors import ErrorCode, PentestError

_PROVIDER_ENV = "SHANNON_AI_PROVIDER"
# required 中的特殊标记 → 对应的字段名(二选一)
_CREDENTIAL_FIELDS = ("api_key", "auth_token")


def validate_active_profile() -> None:
    """校验当前 SHANNON_AI_PROVIDER 的必填变量齐全。

    Raises:
        PentestError: provider 未知, 或必填变量缺失。
    """
    provider = os.getenv(_PROVIDER_ENV)
    if not provider:
        raise PentestError(
            f"{_PROVIDER_ENV} 未设置: profile 文件必须声明 provider 类型",
            category="config",
            error_code=ErrorCode.CONFIG_VALIDATION_FAILED,
        )

    fields = get_provider_fields(provider)
    if fields is None:
        raise PentestError(
            f"不支持的 provider: {provider}",
            category="config",
            error_code=ErrorCode.CONFIG_VALIDATION_FAILED,
            context={"provider": provider},
        )

    missing: list[str] = []
    for req in fields.required:
        if req == "credential":
            # api_key / auth_token 二选一
            if not any(_env_of(fields, f) and os.getenv(_env_of(fields, f)) for f in _CREDENTIAL_FIELDS):
                missing.append("credential (api_key 或 auth_token)")
            continue
        env_name = _env_of(fields, req)
        if env_name is None or not os.getenv(env_name):
            missing.append(env_name or req)

    if missing:
        raise PentestError(
            f"profile( provider={provider}) 缺少必填变量: {', '.join(missing)}。"
            f"请在 .env.profiles/${{SHANNON_PROFILE}}.env 补齐",
            category="config",
            error_code=ErrorCode.CONFIG_VALIDATION_FAILED,
            context={"provider": provider, "missing": missing},
        )


def _env_of(fields, field_name: str) -> str | None:
    """取 ProviderFields 某字段对应的环境变量名。"""
    return getattr(fields, field_name, None)
```

- [ ] **Step 4: 跑测试,确认通过**

Run: `cd packages/core && python -m pytest tests/test_profile_validator.py -v`
Expected: PASS(11 passed)

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/config/profile_validator.py packages/core/tests/test_profile_validator.py
git commit -m "feat(config): 加 profile_validator — 启动校验 profile 必填变量"
```

---

### Task 4: 接通三个 CLI — load_env + validate_active_profile

**Files:**
- Modify: `packages/combined/src/shannon_combined/cli/main.py:6,14`
- Modify: `packages/whitebox/src/shannon_whitebox/cli/main.py:8,25`
- Modify: `packages/blackbox/src/shannon_blackbox/cli/main.py:8,25`

**Interfaces:**
- Consumes: `load_env`(Task 1)、`validate_active_profile`(Task 3)。
- 产出:三个 CLI 的 `cli()` group 在启动时加载 profile 并校验;校验失败 → CLI 启动即报 `PentestError`。
- 验证方式:本 task 为机械接线(逻辑已由 Task 1/3 覆盖单测),用**冒烟验证**(下方 Step 4)。

- [ ] **Step 1: 改 combined CLI**

Edit `packages/combined/src/shannon_combined/cli/main.py`。

替换 import 段(第 6 行 `from dotenv import load_dotenv`):

```python
from shannon_core.config.env_loader import load_env
from shannon_core.config.profile_validator import validate_active_profile
from shannon_core.services.temporal_infra import ensure_infra
```

替换 `cli()` group 内的 `load_dotenv(override=True)`(第 14 行):

```python
@click.group()
def cli():
    """Shannon — unified security scanning (whitebox + blackbox)."""
    load_env()
    validate_active_profile()
```

- [ ] **Step 2: 改 whitebox CLI**

Edit `packages/whitebox/src/shannon_whitebox/cli/main.py`。

替换 import(第 8 行 `from dotenv import load_dotenv`):

```python
from shannon_core.config.env_loader import load_env
from shannon_core.config.profile_validator import validate_active_profile
```

替换 `cli()` group 内的 `load_dotenv(override=True)`(第 25 行):

```python
@click.group()
def cli():
    """Shannon White-Box Scanner - Source code vulnerability analysis."""
    load_env()
    validate_active_profile()
```

- [ ] **Step 3: 改 blackbox CLI**

Edit `packages/blackbox/src/shannon_blackbox/cli/main.py`。

替换 import(第 8 行 `from dotenv import load_dotenv`):

```python
from shannon_core.config.env_loader import load_env
from shannon_core.config.profile_validator import validate_active_profile
```

替换 `cli()` group 内的 `load_dotenv(override=True)`(第 25 行):

```python
@click.group()
def cli():
    """Shannon Black-Box Scanner - Runtime vulnerability verification."""
    load_env()
    validate_active_profile()
```

- [ ] **Step 4: 冒烟验证(需先完成 Task 5 的本地配置迁移)**

> 本 step 依赖 Task 5 把 `.env` / `.env.profiles/` 拆好。若按顺序执行,先做 Task 5 再回来验证;若想先验证接线,可临时建一个最小 `.env`(含 `SHANNON_PROFILE=demo`)与 `.env.profiles/demo.env`(含完整 anthropic 变量)。

验证加载成功(正常路径):
```bash
SHANNON_PROFILE=glm-anthropic python -c "from shannon_whitebox.cli.main import cli; cli(['--help'], standalone_mode=False)"
```
Expected: 打印 help,无 `PentestError`。

验证校验生效(错误路径):临时把 `.env.profiles/glm-anthropic.env` 里 `ANTHROPIC_BASE_URL` 注释掉,重跑上方命令。
Expected: 抛 `PentestError: profile(provider=anthropic_api) 缺少必填变量: ANTHROPIC_BASE_URL`。

- [ ] **Step 5: Commit**

```bash
git add packages/combined/src/shannon_combined/cli/main.py packages/whitebox/src/shannon_whitebox/cli/main.py packages/blackbox/src/shannon_blackbox/cli/main.py
git commit -m "feat(cli): 三 CLI 启动改用 load_env + validate_active_profile"
```

---

### Task 5: 配置文件迁移 + .gitignore + 模板

**Files:**
- Modify(local,不进 git): `.env`
- Create(local,不进 git): `.env.profiles/glm-anthropic.env`、`.env.profiles/glm-openai.env`、`.env.profiles/deepseek.env`
- Modify(进 git): `.gitignore`、`.env.example`
- Create(进 git): `.env.profiles.example/glm-anthropic.env.example`、`.env.profiles.example/glm-openai.env.example`、`.env.profiles.example/deepseek.env.example`

**Interfaces:** 无代码接口。本 task 把现有单 `.env` 的三套并存配置拆成 profile 文件,补 `.gitignore`,并入库占位模板。无单测;验证靠文件结构与 Task 4 冒烟。

> 安全提示:`.env` 与 `.env.profiles/*.env` 含真实密钥,**确认它们不在 `git add` 范围内**。只有 `.gitignore` / `.env.example` / `.env.profiles.example/*` 入库。

- [ ] **Step 1: 补 .gitignore**

Edit `.gitignore`,在 `.env` 行下方加:

```
.env
.env.profiles/
```

- [ ] **Step 2: 拆本地 .env(从现有内容)**

把现有 `.env` 改为只剩共享项 + profile 选择。基于当前真实 `.env`(智谱 anthropic 当前生效、智谱 openai 并存、DeepSeek 注释)拆分:

`.env`(共享 + 选择):
```bash
# Shannon-py 环境配置
# 当前引擎切换: 改 SHANNON_PROFILE, 对应 .env.profiles/<name>.env
SHANNON_PROFILE=glm-anthropic

# 共享配置(引擎无关)
SHANNON_BROWSER_ENGINE=agent-browser
```

`.env.profiles/glm-anthropic.env`(智谱走 anthropic 兼容接口):
```bash
SHANNON_AI_PROVIDER=anthropic_api
ANTHROPIC_BASE_URL=https://open.bigmodel.cn/api/anthropic
ANTHROPIC_AUTH_TOKEN=<沿用现有 Pro token>
SHANNON_LARGE_MODEL=GLM-5.2[1m]
SHANNON_MEDIUM_MODEL=GLM-5.2[1m]
SHANNON_SMALL_MODEL=GLM-4.5-Air
```

`.env.profiles/glm-openai.env`(智谱走 OpenAI 兼容接口):
```bash
SHANNON_AI_PROVIDER=openai_compatible
SHANNON_OPENAI_BASE_URL=https://open.bigmodel.cn/api/coding/paas/v4
SHANNON_OPENAI_API_KEY=<沿用现有 key>
SHANNON_OPENAI_LARGE_MODEL=glm-5.2
SHANNON_OPENAI_MEDIUM_MODEL=glm-5.2
SHANNON_OPENAI_SMALL_MODEL=glm-4.5-air
```

`.env.profiles/deepseek.env`(DeepSeek,anthropic 兼容接口):
```bash
SHANNON_AI_PROVIDER=anthropic_api
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
ANTHROPIC_AUTH_TOKEN=<沿用现有 DeepSeek token>
SHANNON_LARGE_MODEL=deepseek-v4-pro
SHANNON_MEDIUM_MODEL=deepseek-v4-pro
SHANNON_SMALL_MODEL=deepseek-v4-flash
```

> Max 账号 token、CLAUDE_ADAPTIVE_THINKING 等可按需放进对应 profile 文件或共享 `.env`(引擎无关项放共享)。

- [ ] **Step 3: 重写入库模板 .env.example**

Overwrite `.env.example` 为共享模板:

```bash
# Shannon-py 环境配置(模板)
# 复制为 .env 并填入共享项 + 当前 SHANNON_PROFILE。
# 引擎/账号配置放在 .env.profiles/<profile>.env(见 .env.profiles.example/)。

# 当前引擎/账号: 改这一行切换, 对应 .env.profiles/<name>.env
SHANNON_PROFILE=glm-anthropic

# 共享配置(引擎无关)
SHANNON_BROWSER_ENGINE=playwright            # playwright(默认) | agent-browser
# TEMPORAL_ADDRESS=localhost:7233
# SHANNON_DELIVERABLES_SUBDIR=.shannon/deliverables
# SHANNON_WORKER_ROOT=/path/to/worker/root
```

- [ ] **Step 4: 建 profile 模板(入库)**

Create `.env.profiles.example/glm-anthropic.env.example`:
```bash
# 智谱 GLM 走 Anthropic 兼容接口。复制为 .env.profiles/glm-anthropic.env 并填 token。
SHANNON_AI_PROVIDER=anthropic_api
ANTHROPIC_BASE_URL=https://open.bigmodel.cn/api/anthropic
ANTHROPIC_AUTH_TOKEN=your-glm-token
SHANNON_LARGE_MODEL=GLM-5.2[1m]
SHANNON_MEDIUM_MODEL=GLM-5.2[1m]
SHANNON_SMALL_MODEL=GLM-4.5-Air
```

Create `.env.profiles.example/glm-openai.env.example`:
```bash
# 智谱 GLM 走 OpenAI 兼容接口(coding 通道)。复制为 .env.profiles/glm-openai.env 并填 key。
# 注意: 模型名与 anthropic 接口不同(小写、无 [1m])。
SHANNON_AI_PROVIDER=openai_compatible
SHANNON_OPENAI_BASE_URL=https://open.bigmodel.cn/api/coding/paas/v4
SHANNON_OPENAI_API_KEY=your-glm-key
SHANNON_OPENAI_LARGE_MODEL=glm-5.2
SHANNON_OPENAI_MEDIUM_MODEL=glm-5.2
SHANNON_OPENAI_SMALL_MODEL=glm-4.5-air
```

Create `.env.profiles.example/deepseek.env.example`:
```bash
# DeepSeek 走 Anthropic 兼容接口。复制为 .env.profiles/deepseek.env 并填 token。
SHANNON_AI_PROVIDER=anthropic_api
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
ANTHROPIC_AUTH_TOKEN=your-deepseek-token
SHANNON_LARGE_MODEL=deepseek-v4-pro
SHANNON_MEDIUM_MODEL=deepseek-v4-pro
SHANNON_SMALL_MODEL=deepseek-v4-flash
```

- [ ] **Step 5: 验证文件结构 + Commit(只入库模板与 .gitignore)**

验证真实配置文件确实未被跟踪:
```bash
git status --porcelain
```
Expected: 列表里**只有** `.gitignore`、`.env.example`、`.env.profiles.example/*` 三类;`.env` 与 `.env.profiles/*.env` **不出现**(被忽略)。

确认 `.env.profiles/` 被忽略:
```bash
git check-ignore .env .env.profiles/glm-anthropic.env
```
Expected: 两行都被打印(表示被忽略)。

```bash
git add .gitignore .env.example .env.profiles.example/
git commit -m "chore(config): 拆 .env 为 profile 结构 + 模板入库 + gitignore .env.profiles/"
```

---

### Task 6: 重构 build_provider_config — 删除跨前缀 fallback

**Files:**
- Modify: `packages/core/src/shannon_core/agents/providers.py:138-245`(`build_provider_config`)
- Modify: `packages/core/tests/agents/test_providers.py`(改写 fallback 用例)

**Interfaces:**
- Consumes: `PROVIDER_SETTINGS`、`get_provider_fields`(Task 2)。
- 产出:`build_provider_config` 对 `anthropic_api` / `openai_compatible` 按 `PROVIDER_SETTINGS` 直接读对应前缀变量(无跨前缀兜底);`bedrock` / `vertex` / `litellm_router` 保留现有逻辑。`ProviderConfig` 字段与签名**不变**(保持向后兼容)。

- [ ] **Step 1: 改写受影响的测试(先红再绿——先定义新行为)**

> 删除 fallback 后,下列用例语义改变:openai 不再回退 `SHANNON_*`,anthropic 不再读 `SHANNON_API_KEY`/`SHANNON_BASE_URL`(只读 `ANTHROPIC_*`)。

Edit `packages/core/tests/agents/test_providers.py`。

**6.1a** 删除 `TestBuildProviderConfig.test_anthropic_env_vars_fallback`(整段),替换为新语义用例:

```python
    def test_anthropic_reads_anthropic_prefixed_vars(self):
        """anthropic_api 直接读 ANTHROPIC_*(无跨前缀 fallback)。"""
        with patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "anthropic-key",
            "ANTHROPIC_BASE_URL": "https://anthropic.example.com",
        }, clear=True):
            config = build_provider_config()
            assert config.api_key == "anthropic-key"
            assert config.base_url == "https://anthropic.example.com"
```

**6.1b** 删除 `test_shannon_priority_over_anthropic`(整段),替换为:

```python
    def test_anthropic_ignores_shannon_credential_vars(self):
        """anthropic_api 不再读 SHANNON_API_KEY(删 fallback);只认 ANTHROPIC_*。"""
        with patch.dict(os.environ, {
            "SHANNON_API_KEY": "should-be-ignored",
        }, clear=True):
            config = build_provider_config()
            assert config.api_key is None
```

**6.1c** 改 `TestBuildProviderConfigOpenAI`:

- 删除 `test_openai_falls_back_to_shannon_vars`(整段),替换为:

```python
    def test_openai_no_fallback_to_shannon_vars(self, monkeypatch):
        """openai_compatible 缺 SHANNON_OPENAI_* 时不再回退 SHANNON_*(删 fallback)。"""
        from shannon_core.agents.providers import build_provider_config
        monkeypatch.setenv("SHANNON_AI_PROVIDER", "openai_compatible")
        monkeypatch.delenv("SHANNON_OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("SHANNON_OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("SHANNON_BASE_URL", "https://shared/v4")
        monkeypatch.setenv("SHANNON_API_KEY", "shared-key")
        cfg = build_provider_config()
        assert cfg.base_url is None
        assert cfg.api_key is None
```

- 删除 `test_openai_tier_models_fallback`(整段),替换为:

```python
    def test_openai_tier_models_no_fallback(self, monkeypatch):
        """openai_compatible 缺 SHANNON_OPENAI_*_MODEL 时不再回退 SHANNON_*_MODEL。"""
        from shannon_core.agents.providers import build_provider_config
        monkeypatch.setenv("SHANNON_AI_PROVIDER", "openai_compatible")
        monkeypatch.delenv("SHANNON_OPENAI_LARGE_MODEL", raising=False)
        monkeypatch.delenv("SHANNON_OPENAI_MEDIUM_MODEL", raising=False)
        monkeypatch.delenv("SHANNON_OPENAI_SMALL_MODEL", raising=False)
        monkeypatch.setenv("SHANNON_MEDIUM_MODEL", "shared-model")
        cfg = build_provider_config()
        assert cfg.medium_model is None  # 不回退 SHANNON_MEDIUM_MODEL
```

- `test_openai_env_precedence`、`test_anthropic_unchanged_by_openai_vars`、`test_openai_tier_models_precedence`、`test_anthropic_tier_models_ignore_openai`:**保留**(新行为下仍成立)。

**6.1d** 改 `TestTierModelEnvVarIntegration.test_tier_override_plus_global_fallback`:该用例设 `SHANNON_MODEL` + `SHANNON_LARGE_MODEL`,anthropic 路径下 `model` 读 `SHANNON_MODEL`、`large_model` 读 `SHANNON_LARGE_MODEL`,**仍成立,保留**。

- [ ] **Step 2: 跑测试,确认新用例失败(红)**

Run: `cd packages/core && python -m pytest tests/agents/test_providers.py::TestBuildProviderConfig tests/agents/test_providers.py::TestBuildProviderConfigOpenAI -v`
Expected: 新增的 `test_anthropic_ignores_shannon_credential_vars`、`test_openai_no_fallback_to_shannon_vars`、`test_openai_tier_models_no_fallback` FAIL(当前实现仍 fallback)。

- [ ] **Step 3: 重构 build_provider_config**

Edit `packages/core/src/shannon_core/agents/providers.py`。在文件顶部 import 区(`from .runner import ...` 附近)加:

```python
from shannon_core.config.provider_settings import PROVIDER_SETTINGS
```

把现有 `build_provider_config`(第 138–245 行整段)替换为:

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
    """从环境变量和参数构建 ProviderConfig。

    anthropic_api / openai_compatible: 按 PROVIDER_SETTINGS 直接读取对应前缀变量,
    不做跨前缀 fallback(profile 文件须自洽, profile_validator 启动时兜底校验)。
    bedrock / vertex / litellm_router: 保留现有读取行为(用户未使用, 非本次范围)。

    显式参数优先于环境变量。
    """
    if provider_type is None:
        provider_type = os.getenv("SHANNON_AI_PROVIDER", "anthropic_api")

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
        )

    # bedrock / vertex / litellm_router: 现有 fallback 读取(非本次范围, 保持不变)
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
    )


def _read(param: str | None, env_name: str | None) -> str | None:
    """显式参数优先; 否则读环境变量; env_name 为 None 时该字段不读。"""
    if param is not None:
        return param
    if env_name is None:
        return None
    return os.getenv(env_name)


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
) -> ProviderConfig:
    """anthropic_api / openai_compatible: 按 PROVIDER_SETTINGS 读取, 无跨前缀 fallback。"""
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
    )


def _build_legacy(
    provider_type: str,
    *,
    api_key: str | None,
    base_url: str | None,
    model: str | None,
    region: str | None,
    project_id: str | None,
    auth_token: str | None,
    small_model: str | None,
    medium_model: str | None,
    large_model: str | None,
) -> ProviderConfig:
    """bedrock / vertex / litellm_router: 保留删除 fallback 前的读取行为。"""
    is_openai_family = provider_type in ("openai_compatible", "litellm_router")

    if api_key is None:
        if is_openai_family:
            api_key = os.getenv("SHANNON_OPENAI_API_KEY")
        if api_key is None:
            api_key = (
                os.getenv("SHANNON_API_KEY")
                or os.getenv("ANTHROPIC_API_KEY")
                or os.getenv("OPENAI_API_KEY")
            )
    if base_url is None:
        if is_openai_family:
            base_url = os.getenv("SHANNON_OPENAI_BASE_URL")
        if base_url is None:
            base_url = os.getenv("SHANNON_BASE_URL") or os.getenv("ANTHROPIC_BASE_URL")
    if model is None:
        model = os.getenv("SHANNON_MODEL") or os.getenv("ANTHROPIC_MODEL")
    if region is None:
        region = os.getenv("SHANNON_REGION") or os.getenv("AWS_REGION") or os.getenv("CLOUD_ML_REGION")
    if project_id is None:
        project_id = os.getenv("SHANNON_PROJECT_ID") or os.getenv("ANTHROPIC_VERTEX_PROJECT_ID")
    if auth_token is None:
        auth_token = os.getenv("SHANNON_AUTH_TOKEN") or os.getenv("ANTHROPIC_AUTH_TOKEN")
    if small_model is None:
        small_model = (
            os.getenv("SHANNON_OPENAI_SMALL_MODEL") if is_openai_family else None
        ) or os.getenv("SHANNON_SMALL_MODEL")
    if medium_model is None:
        medium_model = (
            os.getenv("SHANNON_OPENAI_MEDIUM_MODEL") if is_openai_family else None
        ) or os.getenv("SHANNON_MEDIUM_MODEL")
    if large_model is None:
        large_model = (
            os.getenv("SHANNON_OPENAI_LARGE_MODEL") if is_openai_family else None
        ) or os.getenv("SHANNON_LARGE_MODEL")

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

- [ ] **Step 4: 跑 providers 全量测试,确认全绿**

Run: `cd packages/core && python -m pytest tests/agents/test_providers.py -v`
Expected: PASS(所有用例,含改写后的)。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/agents/providers.py packages/core/tests/agents/test_providers.py
git commit -m "refactor(providers): build_provider_config 按 PROVIDER_SETTINGS 读取, 删跨前缀 fallback"
```

---

## Self-Review(写计划后自检)

**1. Spec 覆盖**

| spec 要求 | 覆盖 task |
|---|---|
| 文件布局(共享 .env + .env.profiles/ + 模板) | Task 5 |
| 加载顺序(.env → profile,override) | Task 1 |
| profile 未设置 / 文件不存在报错 | Task 1 |
| 启动校验(provider↔变量匹配) | Task 2(数据)+ Task 3(逻辑) |
| Settings 收敛(provider→变量映射) | Task 2 + Task 6 |
| 删除跨前缀 fallback | Task 6 |
| 三 CLI 接通 loader + validator | Task 4 |
| .gitignore 补 .env.profiles/ | Task 5 |
| 迁移现有三套配置 | Task 5 |
| bedrock/vertex/litellm 保留现状 | Task 2(required 空)+ Task 6(_build_legacy) |
| 测试只跑子集 | Global Constraints + 各 task 的 `pytest <具体路径>` |

无遗漏。

**2. 占位符扫描** — 无 TBD/TODO;`<沿用现有 ... token>` 是本地迁移指引(指向用户既有密钥,非计划占位),已说明。

**3. 类型/签名一致性**
- `load_env` → Task 1 定义,Task 4 调用(无参,用默认 `.env` / `.env.profiles`)✓
- `validate_active_profile() -> None` → Task 3 定义,Task 4 调用 ✓
- `PROVIDER_SETTINGS` / `get_provider_fields` / `ProviderFields` → Task 2 定义,Task 3 与 Task 6 消费;字段名(`base_url`/`api_key`/`auth_token`/`model`/`small_model`/`medium_model`/`large_model`/`region`/`project_id`/`required`)在三处一致 ✓
- `_read` / `_build_from_settings` / `_build_legacy` 同在 Task 6 内定义并使用 ✓
- `ProviderConfig` 字段未改,`build_provider_config` 签名未改 ✓

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-18-env-config-profiles.md`.
