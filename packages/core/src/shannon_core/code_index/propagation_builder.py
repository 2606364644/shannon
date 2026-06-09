"""Spec A: parameter / taint propagation graph builder.

消费 Spec B 的 SinkCallSite 列表 + CodeIndex.blocks/edges/chains，产出非空的
ParameterPropagationGraph，写入 parameter_graph.json。被 risk_scorer /
tiered_audit / Spec C LLM 消费。

三阶段算法（详见 spec §4.1）：
  1. seed:  入口函数的 TypedParameter 中 source != INTERNAL 即为 tainted。
  2. intra: 过程内顺序语句分析 — 赋值 / 拼接 / 调用，确定 tainted 变量集。
  3. chain: 沿 CallChain 跨函数传播，命中 SinkCallSite.dangerous_slots 即
            产出 TaintFlow。

明确的不完备边界（spec §4.1.4）：
  - 无不动点：循环内的 def-use 不处理。
  - 容器过近似：d tainted ⇒ d[k] tainted。
  - 分支保守：if/else 任一分支可能污染即视为 tainted。
  - sanitizer 仅提示（SANITIZER_HINTS），不阻断 taint；有效性交给 LLM。
"""

import logging
from typing import Iterable

from shannon_core.code_index.models import (
    CallChain, CodeIndex, FuncBlock, ParameterSource, TypedParameter,
)
from shannon_core.code_index.parameter_models import (
    ParameterPropagationGraph,
    PropagationStep,
    SinkCallSite,
    SlotContext,
    TaintFlow,
)

logger = logging.getLogger(__name__)


# Spec §4.1.5 — best-effort, 非判定
SANITIZER_HINTS: frozenset[str] = frozenset({
    "escape",
    "escapeHtml",
    "encodeURIComponent",
    "htmlentities",
    "htmlspecialchars",
    "sanitize",
    "validator.",
    "bleach.clean",
    "markupsafe",
    "shlex.quote",
    "quote",
    "parameterize",
})


# Spec A §4.3: 这三门语言的 typed param 提取是 _extract_generic → 空，
# 跳过传播并显式记录，让 Spec C 提示 LLM。
_UNSUPPORTED_LANGUAGES: frozenset[str] = frozenset({"go", "java", "php"})


def build_propagation_graph(
    index: CodeIndex,
    typed_params_by_block: dict[str, list[TypedParameter]] | None = None,
) -> ParameterPropagationGraph:
    """Build a ParameterPropagationGraph from a CodeIndex.

    Args:
        index: CodeIndex 含 blocks / edges / chains / sink_call_sites。
        typed_params_by_block: 可选的 {FuncBlock.id → [TypedParameter]}。
            若 None，传播将退化为只用 FuncBlock.parameters 推断 seed（语义略弱，
            但本骨架阶段足够；Task 4 会加上 typed param 提取）。

    Returns:
        ParameterPropagationGraph with taint_flows / language_coverage /
        skipped_languages 填充。
    """
    if typed_params_by_block is None:
        typed_params_by_block = {}

    language = index.language or ""
    if language in _UNSUPPORTED_LANGUAGES:
        logger.info(
            "propagation: language %s has no typed-param extractor; skipping",
            language,
        )
        return ParameterPropagationGraph(
            taint_flows=[],
            language_coverage=[],
            skipped_languages=[language],
        )

    # Task 3-6 会填充这里的实际算法。
    flows: list[TaintFlow] = []
    # _trace_all_chains(index, typed_params_by_block, flows)   # Task 5

    return ParameterPropagationGraph(
        taint_flows=flows,
        language_coverage=[language] if language else [],
        skipped_languages=[],
    )
