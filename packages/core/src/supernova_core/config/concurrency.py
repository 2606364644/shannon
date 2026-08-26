"""Env-driven concurrency limit shared by whitebox/blackbox scans."""

import logging
import os

from .scan_env import ws_getenv

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
    """读布尔 env: '0'/'false'/'no' → False, 其余非空 → True, 未设 → default。

    经 ws_getenv 支持 per-workspace 覆盖（is_llm_track_enabled / is_gitnexus_llm_enabled）。
    """
    raw = ws_getenv(name)
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


# 双轨呈现一致性层（track-parity 配对归并 + GN-only 富化，spec 2026-08-26 §6）
# 的模式档位。独立于 SUPERNOVA_GITNEXUS_LLM_ENABLED——用户关 chain-verdict 判定
# 省 token 时，双轨一致性层仍按本档位工作（2026-08-26 用户口径：判定关、富化开）。
_GN_ENRICH_MODES = ("off", "light", "deep")


def gn_enrich_mode() -> str:
    """SUPERNOVA_GN_ENRICH_MODE（默认 "deep"，经 ws_getenv 支持 per-workspace）：

    - off:   track-parity 整层关闭（确定性 merge 结果直出，无任何 LLM 调用）。
    - light: 配对归并（每 class 一次单次）+ merge activity 内逐卡轻量补全
             （title/notes/impact/remediation/cvss/owasp/severity，不读码）。
    - deep:  配对归并 + 独立深度富化 step（多轮 agent 读码追链，产全字段含
             dataflow_steps/witness_payload）——merge 内轻量补全跳过避免双重花费。
    畸形值回退 deep 并 warning（不 crash 扫描）。"""
    raw = ws_getenv("SUPERNOVA_GN_ENRICH_MODE")
    if raw is None:
        return "deep"
    val = raw.strip().lower()
    if val in _GN_ENRICH_MODES:
        return val
    _log.warning("SUPERNOVA_GN_ENRICH_MODE=%r not in %s; falling back to deep",
                 raw, _GN_ENRICH_MODES)
    return "deep"


def endpoint_enrich_enabled() -> bool:
    """SUPERNOVA_ENDPOINT_ENRICH_ENABLED（默认 "1"，经 ws_getenv 支持 per-workspace）：

    全卡接口表富化（spec 2026-08-26-report-generation-agent §5.2）——独立于
    SUPERNOVA_GN_ENRICH_MODE（GN-only 叙事富化档位）：接口富化对两轨全部卡
    生效（LLM 卡补行号链、GN 卡补接口），关闭时 builder 走确定性 endpoint
    兜底（无行号）。"""
    raw = ws_getenv("SUPERNOVA_ENDPOINT_ENRICH_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() not in ("0", "false", "no", "off")


def get_chunk_max_calls() -> int:
    """Read SUPERNOVA_CHUNK_MAX_CALLS (chunk 内 suspicious/source call 数上限, spec 2026-07-10).

    跨文件贪心合并后, 一个 chunk 可含多文件多 call; 此值是 call 数软上限 —— 任一 chunk
    超 token_threshold 或 call 数 > 此值即开新 chunk(双上限: token 防 context 爆, call 数
    防 LLM 疲劳漏判 + 失败粒度)。默认 100(kol 631 calls → ~7 chunk 的量级)。

    返回 env 值(int>=1); 未设 / 畸形 / <=0 回退默认(100)并 warning。
    畸形值绝不崩 scan(对齐 get_max_concurrent / get_per_call_timeout 的容错契约)。
    """
    raw = ws_getenv("SUPERNOVA_CHUNK_MAX_CALLS")
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
    raw = ws_getenv("SUPERNOVA_LLM_PER_CALL_TIMEOUT")
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
