"""provider → 环境变量名的声明式映射(取代散落的 os.getenv + 跨前缀 fallback 链)。

每个 provider 显式声明它读取哪些环境变量; build_provider_config 按此表读取,
profile_validator 按此表的 required 字段校验。删除跨前缀 fallback 后,
profile 文件必须自洽地提供该 provider 的全部必填变量。

设计见 docs/superpowers/specs/2026-06-18-env-config-design.md。
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderFields:
    """某 provider 从环境读取的变量名。值为环境变量名; None 表示该 provider 不读此字段。

    required: 必填字段名(ProviderFields 的属性名, 不是环境变量名)。
              特殊值 "credential" 表示 api_key 与 auth_token 二选一。
              注意: model 不在任何 provider 的 required 里 —— 全局 model 是可选的,
              仅 tier 模型(small/medium/large_model)是必填的。这是设计如此, 非遗漏。
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
    # anthropic_api / bedrock / vertex 是 Claude Code CLI 的三种 deployment mode, 三者都经
    # claude_agent_sdk 起 CLI 子进程(见 providers_anthropic.AnthropicProvider), 区别仅在 CLI
    # 连哪个后端。"api" 指"走 Anthropic 第一方 messages API 协议"(非云厂商托管), 不是
    # "supernova 代码直连 HTTP"; ANTHROPIC_BASE_URL 可重定向到任意 anthropic 兼容端点(如智谱 GLM)。
    "anthropic_api": ProviderFields(
        base_url="ANTHROPIC_BASE_URL",
        api_key="ANTHROPIC_API_KEY",
        auth_token="ANTHROPIC_AUTH_TOKEN",
        model="SUPERNOVA_MODEL",
        small_model="SUPERNOVA_SMALL_MODEL",
        medium_model="SUPERNOVA_MEDIUM_MODEL",
        large_model="SUPERNOVA_LARGE_MODEL",
        required=("base_url", "credential", "small_model", "medium_model", "large_model"),
    ),
    "openai_compatible": ProviderFields(
        base_url="SUPERNOVA_OPENAI_BASE_URL",
        api_key="SUPERNOVA_OPENAI_API_KEY",
        model="SUPERNOVA_MODEL",
        small_model="SUPERNOVA_OPENAI_SMALL_MODEL",
        medium_model="SUPERNOVA_OPENAI_MEDIUM_MODEL",
        large_model="SUPERNOVA_OPENAI_LARGE_MODEL",
        required=("base_url", "api_key", "small_model", "medium_model", "large_model"),
    ),
    # 以下 provider 用户未使用, 保留现有读取行为, required 留空表示不做强校验。
    "litellm_router": ProviderFields(
        base_url="SUPERNOVA_BASE_URL",
        auth_token="SUPERNOVA_AUTH_TOKEN",
        model="SUPERNOVA_MODEL",
        small_model="SUPERNOVA_OPENAI_SMALL_MODEL",
        medium_model="SUPERNOVA_OPENAI_MEDIUM_MODEL",
        large_model="SUPERNOVA_OPENAI_LARGE_MODEL",
    ),
    "bedrock": ProviderFields(
        base_url=None,
        region="SUPERNOVA_REGION",
        model="SUPERNOVA_MODEL",
        small_model="SUPERNOVA_SMALL_MODEL",
        medium_model="SUPERNOVA_MEDIUM_MODEL",
        large_model="SUPERNOVA_LARGE_MODEL",
    ),
    "vertex": ProviderFields(
        base_url=None,
        region="SUPERNOVA_REGION",
        project_id="SUPERNOVA_PROJECT_ID",
        model="SUPERNOVA_MODEL",
        small_model="SUPERNOVA_SMALL_MODEL",
        medium_model="SUPERNOVA_MEDIUM_MODEL",
        large_model="SUPERNOVA_LARGE_MODEL",
    ),
}


def get_provider_fields(provider_type: str) -> ProviderFields | None:
    """返回 provider 的字段映射; 未知 provider 返回 None。"""
    return PROVIDER_SETTINGS.get(provider_type)


def present(env_name: str | None) -> str | None:
    """读环境变量, 空串视为未设置(返回 None)。

    统一"set 但空 = unset"的语义: profile_validator 与 build_provider_config
    都通过本函数读取, 避免一方把空串当缺失、另一方把空串当有效值的分歧。
    env_name 为 None 时该字段不被读取, 直接返回 None。
    """
    if env_name is None:
        return None
    value = os.getenv(env_name)
    if not value:  # None 或 ""
        return None
    return value
