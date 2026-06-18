"""启动校验: 当前 profile 的变量与声明的 SHANNON_AI_PROVIDER 是否自洽。

按 PROVIDER_SETTINGS[provider].required 校验必填变量; 不满足则启动即失败
(PentestError, CONFIG_VALIDATION_FAILED), 不再静默 fallback 到错变量。

设计见 docs/superpowers/specs/2026-06-18-env-config-design.md 第 6 节。
"""
from __future__ import annotations

import os

from shannon_core.config.provider_settings import get_provider_fields, present
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
            # api_key / auth_token 二选一; 字段名固定, 直接取属性(便于静态分析)。
            credential_found = False
            for f in _CREDENTIAL_FIELDS:
                env_name = getattr(fields, f)
                if env_name and present(env_name):
                    credential_found = True
                    break
            if not credential_found:
                missing.append("credential (api_key 或 auth_token)")
            continue
        # 普通 required 字段: 用 _env_of 解析属性名 → 环境变量名, present 统一空串语义。
        env_name = _env_of(fields, req)
        if env_name is None or not present(env_name):
            missing.append(env_name or req)

    if missing:
        raise PentestError(
            f"profile(provider={provider}) 缺少必填变量: {', '.join(missing)}。"
            f"请在 .env.profiles/${{SHANNON_PROFILE}}.env 补齐",
            category="config",
            error_code=ErrorCode.CONFIG_VALIDATION_FAILED,
            context={"provider": provider, "missing": missing},
        )


def _env_of(fields, field_name: str) -> str | None:
    """取 ProviderFields 某字段对应的环境变量名(动态 required 循环用)。"""
    return getattr(fields, field_name, None)
