"""模型 context window 配置层 + chunk token threshold 派生(spec 2026-07-10)。

GitNexus 轨 sink/source LLM 补召回的 chunk 切分 threshold 按当前模型 context 自适应:
默认/未知 128K, glm-5.2 走 1M(经 normalize_model 归一化)。threshold = context × 0.75
(留 25% 给 output + system prompt + token 估算误差), 结构上防 prompt 爆 context。

context 来源优先级(spec §4):
  1. SUPERNOVA_MODEL_CONTEXT_OVERRIDE JSON({"models": {model: ctx}}), 经 env_loader 天然
     per-profile(.profiles 覆盖 .env)
  2. 内置 MODEL_CONTEXT_WINDOWS 表
  3. DEFAULT_CONTEXT_WINDOW = 128_000(未知模型)

threshold 最终值优先级:
  1. SUPERNOVA_CHUNK_TOKEN_THRESHOLD env(hard override, 调试/止血用)
  2. get_model_context_window(model) × CHUNK_RESERVE_RATIO(派生)

容错契约(对齐 concurrency.get_max_concurrent / pricing._load_override):
override 解析失败 / 畸形 env -> 回落默认 + warning, 绝不 raise, 绝不崩 scan。
"""
from __future__ import annotations

import json
import logging
import os

from .pricing import normalize_model

_log = logging.getLogger(__name__)

# 内置已知模型真实 context window(2026-07-10; 待官网核对, 见 spec §10)。
# 数值错 -> threshold 估错; 可经 SUPERNOVA_MODEL_CONTEXT_OVERRIDE 纠正。
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "glm-5.2": 1_000_000,
    "glm-4.5-air": 128_000,
}

# 未知模型保守回落(= 用户「默认 128K」)。
DEFAULT_CONTEXT_WINDOW = 128_000

# 留 25% 给 output + system prompt + token 估算误差。sink discovery output 小
# (结构化 verdict), 本可更激进(如 0.85); 但取 0.75 兼顾 taint/未来场景 output 较大时更稳。
CHUNK_RESERVE_RATIO = 0.75


def _load_context_override() -> dict[str, int]:
    """加载 SUPERNOVA_MODEL_CONTEXT_OVERRIDE JSON, 返回 {归一化 model: ctx}。

    schema: {"models": {model: ctx_int}}。顶层非 object / models 非 dict / 值非正 int
    -> 返回 {} + warning(容错, 不崩)。未设 env -> {}。
    """
    path = os.environ.get("SUPERNOVA_MODEL_CONTEXT_OVERRIDE")
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        _log.warning("SUPERNOVA_MODEL_CONTEXT_OVERRIDE 解析失败（%s），忽略覆盖", e)
        return {}
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, dict):
        _log.warning("SUPERNOVA_MODEL_CONTEXT_OVERRIDE 顶层非 object 或 models 非 dict，忽略覆盖")
        return {}
    out: dict[str, int] = {}
    for name, ctx in models.items():
        if not isinstance(ctx, int) or ctx <= 0:
            _log.warning("SUPERNOVA_MODEL_CONTEXT_OVERRIDE model %r 的 ctx=%r 非正 int，跳过", name, ctx)
            continue
        out[normalize_model(name)] = ctx
    return out


def get_model_context_window(model: str | None) -> int:
    """模型 -> context window token 数。未知/None -> DEFAULT_CONTEXT_WINDOW。"""
    if not model:
        return DEFAULT_CONTEXT_WINDOW
    key = normalize_model(model)
    override = _load_context_override()
    if key in override:
        return override[key]
    return MODEL_CONTEXT_WINDOWS.get(key, DEFAULT_CONTEXT_WINDOW)


def _hard_override_threshold() -> int | None:
    """读 SUPERNOVA_CHUNK_TOKEN_THRESHOLD env(hard override, 调试用)。非法 -> None。"""
    raw = os.environ.get("SUPERNOVA_CHUNK_TOKEN_THRESHOLD")
    if raw is None:
        return None
    try:
        val = int(raw)
    except ValueError:
        _log.warning("SUPERNOVA_CHUNK_TOKEN_THRESHOLD=%r 非 int，回落派生值", raw)
        return None
    if val <= 0:
        _log.warning("SUPERNOVA_CHUNK_TOKEN_THRESHOLD=%d 须 >0，回落派生值", val)
        return None
    return val


def get_chunk_token_threshold(model: str | None) -> int:
    """chunk token threshold: hard override env > context × CHUNK_RESERVE_RATIO。"""
    hard = _hard_override_threshold()
    if hard is not None:
        return hard
    return int(get_model_context_window(model) * CHUNK_RESERVE_RATIO)
