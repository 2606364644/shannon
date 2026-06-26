"""LLM sink discovery for GitNexus track (spec §3.1, 方案 A 半 sink 精准).

规则库没命中的可疑 call(callee/receiver 命中 sink-ish 模式)→ 送 LLM 判定 →
软 SinkCallSite(rule_id="llm-discovered")。与 detect_sinks 独立遍历, 复用
parser.iter_calls / destructure_call / extract_arg_expressions, 接受双遍历
开销换 detect_sinks 零改动。LLM 不可用时 discover_sinks_llm 返回空(降级)。
"""
import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Awaitable

from shannon_core.code_index.parameter_models import (
    DangerousSlot,
    SinkCallSite,
    SinkCategory,
    SlotContext,
)
from shannon_core.code_index.sink_detector import (
    _RULE_INDEX,
    _make_id,
    _rule_matches,
    is_entry_hint,
)

if TYPE_CHECKING:
    from shannon_core.code_index.models import FuncBlock
    from shannon_core.code_index.parsers.base import BaseParser

logger = logging.getLogger(__name__)

LLMClient = Callable[..., Awaitable[str]]


# sink-ish callee/receiver 模式(spec §3.1 初稿): 比规则库宽松, 精确判定交 LLM。
_SUSPICIOUS_CALLEE_RE = re.compile(
    r"(query|exec(ute)?|render|redirect|include|require|unserialize|"
    r"pickle|loads|system|popen|raw|where|format|template|open|fetch)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SuspiciousCall:
    block: "FuncBlock"
    callee: str
    receiver: str | None
    arg_exprs: list[str]
    file_path: str
    line: int
    column: int


def _is_rule_hit(language: str, callee: str, receiver: str | None) -> bool:
    """该 call 是否已被 detect_sinks 规则库命中(避免与规则 sink 重复)。"""
    candidates = _RULE_INDEX.get((language, callee), [])
    return any(_rule_matches(rule, receiver) for rule in candidates)


def collect_suspicious_calls(
    blocks: "list[FuncBlock]",
    parser: "BaseParser",
    *,
    source_provider: "Callable[[FuncBlock], bytes | None]",
) -> list[SuspiciousCall]:
    """遍历所有函数的 call, 收集『sink-ish 但规则未命中』的可疑 call。"""
    out: list[SuspiciousCall] = []
    for block in blocks:
        source = source_provider(block)
        if source is None:
            continue
        try:
            call_nodes = list(parser.iter_calls(block, source))
        except Exception:
            logger.debug("suspicious scan: iter_calls failed for %s", block.id, exc_info=True)
            continue
        for call in call_nodes:
            try:
                callee, receiver = parser.destructure_call(call)
            except Exception:
                continue
            if not callee:
                continue
            if _is_rule_hit(block.language, callee, receiver):
                continue  # 规则已命中, detect_sinks 会产 SinkCallSite, 不重复
            target = callee if receiver is None else f"{receiver}.{callee}"
            if not _SUSPICIOUS_CALLEE_RE.search(target):
                continue
            try:
                arg_exprs = parser.extract_arg_expressions(call, source)
            except Exception:
                arg_exprs = []
            out.append(SuspiciousCall(
                block=block, callee=callee, receiver=receiver, arg_exprs=arg_exprs,
                file_path=block.file_path, line=call.line, column=call.column,
            ))
    return out


# === 后续 task 占位(方案 A Task 2+ 将填充) ===================================
# discover_sinks_llm / RuleGap 在后续 task 中实现; 此处仅提供可导入的占位,
# 使本模块的测试可一次性 import 全部符号。Task 1 的契约只覆盖 collect_suspicious_calls。

@dataclass(frozen=True)
class RuleGap:
    """规则库覆盖缺口(后续 task 填充)。占位以保证 import 可用。"""
    callee: str
    receiver: str | None = None


async def discover_sinks_llm(  # noqa: D401 (placeholder, 后续 task 实现)
    *args, **kwargs,
) -> list[SinkCallSite]:
    """LLM sink 补召回入口(后续 task 实现)。当前为降级占位, 返回空。"""
    return []
