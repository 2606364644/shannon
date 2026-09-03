"""删防护 LLM 判定（spec 2026-09-03 §5.1，GitNexus 轨侧组成步骤）。

diff 删除 hunk 喂轻量 LLM 判「删的是否安全防护」→ RemovedProtection[]。
产物只流向 IncrementalScope（来源 C）；不进 LLM 轨 prompt（双轨铁律）。
LLM 失败/超时/解析失败 → degraded=True + 空列表，来源 C 降级不阻塞 A/B。
"""

import json
import logging
from typing import Awaitable, Callable

from pydantic import BaseModel, Field

from supernova_core.agents.llm_json import _extract_json_payload
from supernova_core.mr_scan.incremental_scope import RemovedProtection

logger = logging.getLogger(__name__)

# 对齐 llm_taint_analyzer 先例：async callable (prompt, **kwargs) -> raw string
LLMClient = Callable[..., Awaitable[str]]

# 手写 schema dict（output_format 通道；str|null 用 ["string","null"] 规避 anyOf 坑）
PROTECTION_REMOVAL_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "removed_protections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "base_line_no": {"type": "integer"},
                    "removed_text": {"type": "string"},
                    "function_name": {"type": ["string", "null"]},
                    "protection_kind": {"type": "string"},
                    "rationale": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["file_path", "base_line_no", "removed_text",
                             "protection_kind"],
            },
        },
    },
    "required": ["removed_protections"],
}


class ProtectionRemovalOutcome(BaseModel):
    protections: list[RemovedProtection] = []
    degraded: bool = False   # True = LLM 判定缺席（失败/超时/解析失败），报告需标注降级


def _degraded_outcome(reason: str) -> ProtectionRemovalOutcome:
    logger.warning("protection-removal analysis degraded: %s", reason)
    return ProtectionRemovalOutcome(protections=[], degraded=True)


def build_protection_removal_prompt(diff_text: str) -> str:
    """删除 hunk → 防护判定 prompt（内联构建，对齐 llm_taint_analyzer 先例）。"""
    return (
        "你是代码安全评审专家。下面是一个合并请求的 unified diff（base..head）。"
        "请逐段检查其中**被删除的行**（- 前缀），判断是否存在**被移除的安全防护**。\n"
        "安全防护包括但不限于：输入清洗/转义（sanitize/escape/encode）、参数化查询、"
        "输出编码、认证/授权检查（login_required/权限判断/角色校验）、CSRF 防护、"
        "速率限制、路径规范化、schema 校验。\n"
        "注意：仅报告「删除防护后相关数据流/入口失去保护」的行；纯重构改名、"
        "删除无用代码、防护被等价替换（如换成更强的清洗函数）不算。\n"
        "对每个判定的防护，给出：file_path（该删除行所在文件）、base_line_no"
        "（删除行在 base 侧的行号，见 hunk 头与 - 行序）、removed_text（删除行原文）、"
        "function_name（从 hunk 上下文推断的所在函数名，推断不出给 null）、"
        "protection_kind（sanitize/authz_check/input_validation/csrf/rate_limit/其他）、"
        "rationale（一句话理由）、confidence（0-1）。\n"
        "只输出 JSON 对象：{\"removed_protections\": [...]};无命中给空数组。\n\n"
        f"```diff\n{diff_text}\n```"
    )


def _parse_protections(raw: str) -> list[RemovedProtection] | None:
    payload_text = _extract_json_payload(raw)
    if payload_text is None:
        return None
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    items = payload.get("removed_protections")
    if not isinstance(items, list):
        return None
    out: list[RemovedProtection] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            out.append(RemovedProtection(
                file_path=str(item.get("file_path", "")),
                base_line_no=int(item.get("base_line_no", 0)),
                removed_text=str(item.get("removed_text", "")),
                function_name=item.get("function_name") or None,
                protection_kind=str(item.get("protection_kind", "")),
                rationale=str(item.get("rationale", "")),
                confidence=float(item.get("confidence", 0.0)),
            ))
        except (TypeError, ValueError):
            logger.debug("skip malformed protection item: %r", item)
    return out


async def detect_removed_protections(
    diff_text: str,
    *,
    llm_client: LLMClient | None = None,
    retry_count: int = 1,
) -> ProtectionRemovalOutcome:
    """删除 hunk → RemovedProtection[]（spec §5.1）。

    LLM 缺席/失败/超时/解析失败 → degraded 降级（来源 C 静默缺席，A/B 不受影响）。
    """
    if llm_client is None:
        return _degraded_outcome("no llm client")

    prompt = build_protection_removal_prompt(diff_text)
    raw: str | None = None
    last_exc: Exception | None = None
    for attempt in range(retry_count + 1):
        try:
            raw = await llm_client(prompt, output_format=PROTECTION_REMOVAL_SCHEMA)
            break
        except Exception as exc:   # noqa: BLE001 —— 降级语义：任何 LLM 失败都不阻塞
            last_exc = exc
            logger.debug("protection-removal LLM attempt %d failed: %s",
                         attempt + 1, exc)

    if raw is None:
        return _degraded_outcome(f"llm failed after retries: {last_exc}")

    protections = _parse_protections(raw)
    if protections is None:
        return _degraded_outcome("unparseable llm response")
    return ProtectionRemovalOutcome(protections=protections, degraded=False)
