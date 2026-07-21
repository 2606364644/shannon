"""Env-driven concurrency limit shared by whitebox/blackbox scans."""

import logging
import os

_DEFAULT = 3
_PER_CALL_TIMEOUT_DEFAULT = 60.0
_CHUNK_MAX_CALLS_DEFAULT = 100
_log = logging.getLogger(__name__)


def get_max_concurrent() -> int:
    """Read SUPERNOVA_MAX_CONCURRENT.

    Returns the env value when it is an int >= 1; otherwise falls back to the
    default (3) and logs a warning. A malformed value must NOT crash a scan.
    """
    raw = os.environ.get("SUPERNOVA_MAX_CONCURRENT")
    if raw is None:
        return _DEFAULT
    try:
        val = int(raw)
    except ValueError:
        _log.warning("SUPERNOVA_MAX_CONCURRENT=%r not an int; falling back to %d", raw, _DEFAULT)
        return _DEFAULT
    if val < 1:
        _log.warning("SUPERNOVA_MAX_CONCURRENT=%d must be >=1; falling back to %d", val, _DEFAULT)
        return _DEFAULT
    return val


def _is_truthy_env(name: str, default: bool) -> bool:
    """读布尔 env: '0'/'false'/'no' → False, 其余非空 → True, 未设 → default。"""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def is_llm_track_enabled() -> bool:
    """SUPERNOVA_LLM_TRACK_ENABLED: 关 LLM 轨开关(默认开 True)。

    =0 时只关 inj/xss/ssrf vuln agent(DEGRADABLE_VULN_CLASSES, taint GitNexus
    chain_verdict 主干兜底); pre-recon/recon/authz/auth 的 LLM 全保留(GitNexus
    做不了 authz Vertical/Context + recon 角色模型/工作流, 关了会降安全效果).
    详见 plan smooth-wandering-dolphin + CLAUDE.md §1.
    """
    return _is_truthy_env("SUPERNOVA_LLM_TRACK_ENABLED", default=True)


def is_gitnexus_llm_enabled() -> bool:
    """SUPERNOVA_GITNEXUS_LLM_ENABLED: 是否接通 GitNexus 轨 LLM
    (discover_sinks_llm / analyze_taint_llm / chain_verdict). 默认开.
    关闭 → llm_client 走 raise → 各处降级到纯规则 + is_entry_hint(spec §3.3 边界)."""
    return _is_truthy_env("SUPERNOVA_GITNEXUS_LLM_ENABLED", default=True)


def get_chunk_max_calls() -> int:
    """Read SUPERNOVA_CHUNK_MAX_CALLS (chunk 内 suspicious/source call 数上限, spec 2026-07-10).

    跨文件贪心合并后, 一个 chunk 可含多文件多 call; 此值是 call 数软上限 —— 任一 chunk
    超 token_threshold 或 call 数 > 此值即开新 chunk(双上限: token 防 context 爆, call 数
    防 LLM 疲劳漏判 + 失败粒度)。默认 100(kol 631 calls → ~7 chunk 的量级)。

    返回 env 值(int>=1); 未设 / 畸形 / <=0 回退默认(100)并 warning。
    畸形值绝不崩 scan(对齐 get_max_concurrent / get_per_call_timeout 的容错契约)。
    """
    raw = os.environ.get("SUPERNOVA_CHUNK_MAX_CALLS")
    if raw is None:
        return _CHUNK_MAX_CALLS_DEFAULT
    try:
        val = int(raw)
    except ValueError:
        _log.warning("SUPERNOVA_CHUNK_MAX_CALLS=%r not an int; falling back to %d",
                     raw, _CHUNK_MAX_CALLS_DEFAULT)
        return _CHUNK_MAX_CALLS_DEFAULT
    if val < 1:
        _log.warning("SUPERNOVA_CHUNK_MAX_CALLS=%d must be >=1; falling back to %d",
                     val, _CHUNK_MAX_CALLS_DEFAULT)
        return _CHUNK_MAX_CALLS_DEFAULT
    return val


def get_per_call_timeout() -> float:
    """Read SUPERNOVA_LLM_PER_CALL_TIMEOUT (单次 GitNexus 轨 LLM 调用上限秒数).

    discover_sinks_llm / discover_sources_llm / analyze_taint_llm 共用
    map_llm_with_bounds, 每个函数一次 LLM 调用; 超过此值即降级跳过该函数(治本 2,
    防大仓 N 个函数串行累加拖垮 activity 的 start_to_close_timeout)。

    返回 env 值(float>0); 未设 / 畸形 / <=0 回退默认(60s)并 warning。
    畸形值绝不崩 scan(对齐 get_max_concurrent 的容错契约)。

    提高此值(如 180)给单次调用更多时间(含 provider 内部 retry), 代价是大仓
    N 个函数的总耗时上升——需与 activity 的 start_to_close_timeout 平衡。
    """
    raw = os.environ.get("SUPERNOVA_LLM_PER_CALL_TIMEOUT")
    if raw is None:
        return _PER_CALL_TIMEOUT_DEFAULT
    try:
        val = float(raw)
    except ValueError:
        _log.warning(
            "SUPERNOVA_LLM_PER_CALL_TIMEOUT=%r not a float; falling back to %s",
            raw, _PER_CALL_TIMEOUT_DEFAULT)
        return _PER_CALL_TIMEOUT_DEFAULT
    if val <= 0:
        _log.warning(
            "SUPERNOVA_LLM_PER_CALL_TIMEOUT=%s must be >0; falling back to %s",
            val, _PER_CALL_TIMEOUT_DEFAULT)
        return _PER_CALL_TIMEOUT_DEFAULT
    return val
