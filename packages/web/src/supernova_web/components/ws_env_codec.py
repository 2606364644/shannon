"""env 文本 ↔ WsConfig 字段转换（parse / render）。

env 文本是工作区设置的表现层；config.yaml 仍是存储 SSOT（ws_config_store）。
本模块只做纯转换，不碰 IO / 加密。

spec: docs/superpowers/specs/2026-08-10-ws-config-env-textarea-design.md
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field

from supernova_core.config.provider_settings import get_provider_fields


_STR = "str"
_INT = "int"
_BOOL = "bool"

# env key → (config field, 类型)。反向映射，parse 用；key 名唯一，不依赖 provider。
# 同一字段多 env 变体（base_url / api_key / tier model）是因为 env key 是 per-provider 的。
ENV_TO_FIELD: dict[str, tuple[str, str]] = {
    "SUPERNOVA_AI_PROVIDER": ("ai_provider", _STR),
    # base_url（per-provider 变体）
    "ANTHROPIC_BASE_URL": ("base_url", _STR),
    "SUPERNOVA_OPENAI_BASE_URL": ("base_url", _STR),
    "SUPERNOVA_BASE_URL": ("base_url", _STR),
    # api_key（per-provider 凭据变体）
    "ANTHROPIC_AUTH_TOKEN": ("api_key", _STR),
    "ANTHROPIC_API_KEY": ("api_key", _STR),
    "SUPERNOVA_OPENAI_API_KEY": ("api_key", _STR),
    "SUPERNOVA_AUTH_TOKEN": ("api_key", _STR),
    # model
    "SUPERNOVA_MODEL": ("model", _STR),
    # tier models（anthropic / openai 变体）
    "SUPERNOVA_SMALL_MODEL": ("small_model", _STR),
    "SUPERNOVA_MEDIUM_MODEL": ("medium_model", _STR),
    "SUPERNOVA_LARGE_MODEL": ("large_model", _STR),
    "SUPERNOVA_OPENAI_SMALL_MODEL": ("small_model", _STR),
    "SUPERNOVA_OPENAI_MEDIUM_MODEL": ("medium_model", _STR),
    "SUPERNOVA_OPENAI_LARGE_MODEL": ("large_model", _STR),
    # 调参（config.yaml 独有，约定 env key）
    "SUPERNOVA_MAX_TURNS": ("max_turns", _INT),
    "SUPERNOVA_ADAPTIVE_THINKING": ("adaptive_thinking", _BOOL),
    # git
    "GITLAB_USER": ("gitlab_user", _STR),
    "GITLAB_TOKEN": ("gitlab_token", _STR),
}

# 进程级开关（worker 共享 os.environ，ws 覆盖会踩并发扫描）→ 警告不阻塞，不进 fields。
INEFFECTIVE_KEYS: frozenset[str] = frozenset({
    "SUPERNOVA_MAX_CONCURRENT",
    "SUPERNOVA_PRICING_OVERRIDE",
    "SUPERNOVA_LLM_TRACK_ENABLED",
    "SUPERNOVA_GITNEXUS_LLM_ENABLED",
    "SUPERNOVA_AGENT_NARRATION_LANG",
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
})

# 凭据字段（render 时掩码、PUT 时智能保留）
CREDENTIAL_FIELDS: frozenset[str] = frozenset({"api_key", "gitlab_token"})


@dataclass
class ParsedEnv:
    """parse_env_text 的结果。

    fields: config 字段 → 强类型值（仅含文本里出现的生效字段）。
    ineffective: 进程级 key（警告，不存 config）。
    unknown: 未知 key（警告，可能拼写错误）。
    """
    fields: dict[str, str | int | bool] = dc_field(default_factory=dict)
    ineffective: list[str] = dc_field(default_factory=list)
    unknown: list[str] = dc_field(default_factory=list)


def _convert(value: str, kind: str, key: str) -> str | int | bool:
    if kind == _INT:
        try:
            return int(value)
        except ValueError:
            raise ValueError(f"invalid int for {key}: {value!r}")
    if kind == _BOOL:
        low = value.lower()
        if low in ("true", "1"):
            return True
        if low in ("false", "0"):
            return False
        raise ValueError(f"invalid bool for {key}: {value!r}")
    return value


def parse_env_text(text: str) -> ParsedEnv:
    """把 env 文本（KEY=value）解析成 config 字段 + warnings。

    Raises:
        ValueError: 某行无 '='，或 int/bool 字段值非法（API 层转 422）。
    """
    fields: dict[str, str | int | bool] = {}
    ineffective: list[str] = []
    unknown: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"invalid env line (no '='): {raw_line!r}")
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key in ENV_TO_FIELD:
            fld, kind = ENV_TO_FIELD[key]
            fields[fld] = _convert(value, kind, key)
        elif key in INEFFECTIVE_KEYS:
            ineffective.append(key)
        else:
            unknown.append(key)
    return ParsedEnv(fields=fields, ineffective=ineffective, unknown=unknown)


# ---- render: WsConfig → env 文本 ----

MASKED = "••••"

# provider 段渲染顺序（git 段单独处理，置末）
_RENDER_ORDER_PROVIDER = [
    "ai_provider", "base_url", "api_key", "model",
    "small_model", "medium_model", "large_model",
    "max_turns", "adaptive_thinking",
]

# 约定 env key（无对应 ProviderFields 属性，单独列）
_CONST_ENV_NAME = {
    "ai_provider": "SUPERNOVA_AI_PROVIDER",
    "max_turns": "SUPERNOVA_MAX_TURNS",
    "adaptive_thinking": "SUPERNOVA_ADAPTIVE_THINKING",
    "gitlab_user": "GITLAB_USER",
    "gitlab_token": "GITLAB_TOKEN",
}


def _env_name_for(field_name: str, pf) -> str | None:
    """config field → 该 provider 的 env key 名；None 表示 provider 不读此字段。"""
    if field_name in _CONST_ENV_NAME:
        return _CONST_ENV_NAME[field_name]
    if field_name == "api_key":  # 凭据：优先 auth_token（anthropic），回落 api_key
        return pf.auth_token or pf.api_key
    # base_url / model / small_model / medium_model / large_model：取 ProviderFields 同名属性
    return getattr(pf, field_name, None)


def render_env_text(cfg, ai_provider: str = "anthropic_api") -> str:
    """把 WsConfig 渲染成 env 文本（凭据掩码 ••••；仅渲染非 None 字段）。

    ai_provider 决定 env key 名模板（anthropic→ANTHROPIC_*，openai→SUPERNOVA_OPENAI_*）。
    调用方负责传 ws ?? 全局 ?? 默认 的 ai_provider。
    """
    pf = get_provider_fields(ai_provider) or get_provider_fields("anthropic_api")
    lines: list[str] = []
    for fld in _RENDER_ORDER_PROVIDER:
        val = getattr(cfg.provider, fld, None)
        if val is None:
            continue
        env_name = _env_name_for(fld, pf)
        if env_name is None:
            continue
        if fld in CREDENTIAL_FIELDS:
            lines.append(f"{env_name}={MASKED}")
        elif isinstance(val, bool):  # bool 先于 int（bool 是 int 子类）
            lines.append(f"{env_name}={'true' if val else 'false'}")
        else:
            lines.append(f"{env_name}={val}")
    g = cfg.git
    if g.gitlab_user is not None:
        lines.append(f"GITLAB_USER={g.gitlab_user}")
    if g.gitlab_token is not None:
        lines.append(f"GITLAB_TOKEN={MASKED}")
    return "\n".join(lines) + ("\n" if lines else "")
