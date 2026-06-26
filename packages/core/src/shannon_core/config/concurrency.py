"""Env-driven concurrency limit shared by whitebox/blackbox scans."""

import logging
import os

_DEFAULT = 3
_log = logging.getLogger(__name__)


def get_max_concurrent() -> int:
    """Read SHANNON_MAX_CONCURRENT.

    Returns the env value when it is an int >= 1; otherwise falls back to the
    default (3) and logs a warning. A malformed value must NOT crash a scan.
    """
    raw = os.environ.get("SHANNON_MAX_CONCURRENT")
    if raw is None:
        return _DEFAULT
    try:
        val = int(raw)
    except ValueError:
        _log.warning("SHANNON_MAX_CONCURRENT=%r not an int; falling back to %d", raw, _DEFAULT)
        return _DEFAULT
    if val < 1:
        _log.warning("SHANNON_MAX_CONCURRENT=%d must be >=1; falling back to %d", val, _DEFAULT)
        return _DEFAULT
    return val


def _is_truthy_env(name: str, default: bool) -> bool:
    """读布尔 env: '0'/'false'/'no' → False, 其余非空 → True, 未设 → default。"""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def is_llm_track_enabled() -> bool:
    """SHANNON_LLM_TRACK_ENABLED: 是否跑 LLM 轨(重型 vuln agent). 默认开(True)."""
    return _is_truthy_env("SHANNON_LLM_TRACK_ENABLED", default=True)


def is_gitnexus_llm_enabled() -> bool:
    """SHANNON_GITNEXUS_LLM_ENABLED: 是否接通 GitNexus 轨 LLM
    (discover_sinks_llm / analyze_taint_llm / chain_verdict). 默认开.
    关闭 → llm_client 走 raise → 各处降级到纯规则 + is_entry_hint(spec §3.3 边界)."""
    return _is_truthy_env("SHANNON_GITNEXUS_LLM_ENABLED", default=True)
