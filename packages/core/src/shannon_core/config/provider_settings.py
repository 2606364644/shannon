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