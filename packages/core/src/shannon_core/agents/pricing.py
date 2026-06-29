"""openai 引擎 GLM 成本换算（¥→$）——纯函数，无副作用。

openai-agents SDK 不像 claude-agent-sdk 给 total_cost_usd，GLM 端点也不返回成本，
故按内置 GLM 价目表 + token 用量自算。未知模型回落 0.0（守「不假估算」，
spec §4.3/§4.5）。spending-cap 文本检测对 cost>0 引擎失效是已接受的不变量
（spec §4.6）——真正限额检测靠结构化错误码（executor.api_error_status），不靠 cost 猜。

usage 参数 duck-typed：只读 .input_tokens / .output_tokens / .cache_read_input_tokens
（通常是 shannon_core.agents.runner.TokenUsage）。
"""
from __future__ import annotations

import json
import logging
import os
import re

_log = logging.getLogger(__name__)

# 单位：¥ / 百万 token。示例数值——执行时按智谱官网核对调整（spec §8）。
# 测试动态引用本常量断言，故数值变化不会让测试失效。
GLM_PRICING_CNY: dict[str, dict[str, float]] = {
    "glm-4.6": {"input": 50.0, "output": 50.0, "cache_read": 12.5},
    "glm-5.2": {"input": 50.0, "output": 50.0, "cache_read": 12.5},
}

# 默认 ¥→$ 汇率；可经 SHANNON_USD_CNY_RATE 覆盖。
USD_CNY_RATE: float = 7.2

# 模型名后缀归一化：去 [xxx] / -YYYYMMDD / --xxx 等。
_MODEL_SUFFIX_RE = re.compile(r"\[.*?\]|-\d{8}.*$|--.*$", re.IGNORECASE)

# 别名 → 归一化 key（实现时按需补）。
_MODEL_ALIASES: dict[str, str] = {}


def normalize_model(name: str) -> str:
    """模型名归一化：小写 + 去后缀 + 别名映射（GLM-5.2[1m] → glm-5.2）。"""
    if not name:
        return ""
    key = name.strip().lower()
    key = _MODEL_SUFFIX_RE.sub("", key).strip()
    return _MODEL_ALIASES.get(key, key)


def _rate() -> float:
    raw = os.environ.get("SHANNON_USD_CNY_RATE")
    if raw:
        try:
            v = float(raw)
            if v > 0:
                return v
        except ValueError:
            _log.warning("SHANNON_USD_CNY_RATE=%r 非法，落回默认 %s", raw, USD_CNY_RATE)
    return USD_CNY_RATE


def _load_override() -> dict:
    path = os.environ.get("SHANNON_PRICING_OVERRIDE")
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        _log.warning("SHANNON_PRICING_OVERRIDE 顶层非 object，忽略覆盖")
    except (OSError, json.JSONDecodeError) as e:
        _log.warning("SHANNON_PRICING_OVERRIDE 解析失败（%s），忽略覆盖", e)
    return {}


def _price_table() -> dict:
    merged = dict(GLM_PRICING_CNY)
    merged.update(_load_override())  # override 同 key 覆盖内置（spec §5）
    return merged


def is_model_priced(model: str) -> bool:
    return normalize_model(model) in _price_table()


def compute_cost_usd(model: str, usage) -> float:
    """按 GLM 价目表 + token 用量算 cost（¥→$）。未知模型或无用量 → 0.0。

    计费公式（spec §4.1）：
        billable_input = input_tokens - cached_tokens   # 按 input 价
        cache_hit      = cached_tokens                  # 按 cache_read 折价
        output         = output_tokens                 # 按 output 价
        cost_cny = (billable_input*P_in + cache_hit*P_cache + output*P_out) / 1_000_000
    reasoning_tokens 已包含在 output_tokens 内（OpenAI 语义），不重复计费。
    """
    key = normalize_model(model)
    table = _price_table()
    if key not in table:
        return 0.0
    p = table[key]
    inp = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    cached = getattr(usage, "cache_read_input_tokens", 0) or 0
    billable_input = max(inp - cached, 0)
    cost_cny = (
        billable_input * p["input"]
        + cached * p["cache_read"]
        + out * p["output"]
    ) / 1_000_000
    return cost_cny / _rate()
