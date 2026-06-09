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
import re
from dataclasses import dataclass, field
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


def seed_taints(
    block: FuncBlock,
    typed_params: list[TypedParameter],
) -> set[str]:
    """Determine the initial set of tainted variable names at function entry.

    Rules (spec §4.1.1):
      - 如果有 TypedParameter 信息：source != INTERNAL 即为 tainted。
      - 如果没有（Go/Java/PHP 入口、或未跑 enhanced_parameters）：把
        FuncBlock.parameters 全部视作 tainted（保守偏 recall），让 LLM 在
        Spec C 复核。
      - UNKNOWN（如 request 对象）视为 tainted — container 对象本身被标，
        过程内分析会把 request.x 一并视作 tainted（容器过近似）。
    """
    seed: set[str] = set()
    if typed_params:
        for tp in typed_params:
            if tp.source == ParameterSource.INTERNAL:
                continue
            seed.add(tp.name)
        return seed

    # Fallback: 保守 — 入口函数的全部位置参数都视作 tainted
    for name in block.parameters:
        seed.add(name)
    return seed


# === 过程内 dataflow ============================================

# 行级赋值识别：捕获 "LHS = RHS" / "LHS := RHS" (Go)。
# 用负向先行断言排除 == != >= <= 等。
_ASSIGN_RE = re.compile(r"^\s*([A-Za-z_][\w]*)\s*(?<![<>!=])=(?!=)\s*(.+?)\s*$")
_ASSIGN_GO_RE = re.compile(r"^\s*([A-Za-z_][\w]*)\s*:=(?!=)\s*(.+?)\s*$")

# 拼接检测：RHS 含 '+' 或 f-string 或 .format(...) 或模板字面量
_CONCAT_HINTS = ("+", ".format(", "f'", 'f"', "${")


@dataclass
class IntraHit:
    """单条 sink call site 命中的过程内结果。"""
    sink_id: str
    tainted_arg_index: int
    slot: SlotContext
    local_steps: list[PropagationStep] = field(default_factory=list)
    has_sanitizer_hint: bool = False


@dataclass
class IntraResult:
    """analyze_intra 的返回：命中的 sinks + 累计 steps（供跨函数用）。"""
    hits: dict[str, IntraHit] = field(default_factory=dict)
    local_steps_accumulated: list[PropagationStep] = field(default_factory=list)
    has_sanitizer_global: bool = False


def analyze_intra(
    block: FuncBlock,
    seed: set[str],
    sinks_in_func: list[SinkCallSite],
) -> IntraResult:
    """过程内顺序污点分析（spec §4.1.2）。

    单趟扫描 FuncBlock.source_code 的每一行，维护当前 tainted 变量集合，
    在遇到 sink call 时检查实参是否触达 dangerous_slot。

    简化（spec §4.1.4）：
      - 顺序语句、不动点 = 单趟。
      - 容器过近似：d tainted ⇒ d[k] tainted。
      - 分支保守：if/else 任一分支污染即视为 tainted。
      - sanitizer 仅打提示，不阻断 taint。
    """
    tainted = set(seed)
    # 按 line 排好序的 sinks，方便 O(N+M) 扫描
    sinks_by_line: dict[int, list[SinkCallSite]] = {}
    for s in sinks_in_func:
        sinks_by_line.setdefault(s.line, []).append(s)

    hits: dict[str, IntraHit] = {}
    accumulated_steps: list[PropagationStep] = []
    has_sanitizer_global = False

    for line_no, raw_line in enumerate(block.source_code.splitlines(), start=block.start_line):
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue

        # 1) 赋值：x = expr  /  x := expr (Go)
        lhs, rhs = _match_assignment(line)
        if lhs is not None:
            transformation = _detect_transformation(rhs)
            if _expr_references_tainted(rhs, tainted):
                tainted.add(lhs)
                accumulated_steps.append(PropagationStep(
                    step_id="",  # build_propagation_graph 在最终 flow 阶段统一编号
                    from_func_id=block.id,
                    from_param=_first_tainted_in(rhs, tainted) or "",
                    to_func_id=block.id, to_param=lhs,
                    transformation=transformation,
                    code_location=f"{block.file_path}:{line_no}",
                    confidence=0.8 if transformation else 1.0,
                ))
                if transformation and transformation.startswith("sanitize_hint:"):
                    has_sanitizer_global = True
            # 不管 LHS 是否被污染，sanitizer 名只要出现就记 has_sanitizer_hint
            if _has_sanitizer(rhs):
                has_sanitizer_global = True

        # 2) Sink call：检查 dangerous_slots 是否被 tainted 实参命中
        for sink in sinks_by_line.get(line_no, []):
            for slot in sink.dangerous_slots:
                if _expr_references_tainted(slot.expression, tainted):
                    if sink.id not in hits:
                        hits[sink.id] = IntraHit(
                            sink_id=sink.id,
                            tainted_arg_index=slot.arg_index,
                            slot=slot.slot,
                            local_steps=list(accumulated_steps),
                            has_sanitizer_hint=has_sanitizer_global,
                        )
                    break  # 同一 sink 的首个 dangerous 命中即停止扫 slot

    return IntraResult(
        hits=hits,
        local_steps_accumulated=accumulated_steps,
        has_sanitizer_global=has_sanitizer_global,
    )


def _match_assignment(line: str) -> tuple[str | None, str]:
    """识别 "LHS = RHS" / "LHS := RHS"。返回 (lhs, rhs)；不匹配返回 (None, "")。"""
    m = _ASSIGN_RE.match(line)
    if m:
        return m.group(1), m.group(2)
    m = _ASSIGN_GO_RE.match(line)
    if m:
        return m.group(1), m.group(2)
    return None, ""


def _expr_references_tainted(expr: str, tainted: frozenset[str] | set[str]) -> bool:
    """递归检查表达式是否引用 tainted 集合中的标识符。

    过近似：tainted 标识符 + 容器字段（d tainted ⇒ d[k] tainted）。
    """
    if not expr or not tainted:
        return False
    # 词法扫描：提取所有标识符（含点号 — request.x 整体作一个 token）
    for tok in re.findall(r"[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*", expr):
        if tok in tainted:
            return True
        # 容器过近似：tok 形如 "tainted_obj.field" — 头部在 tainted 即视命中
        head = tok.split(".", 1)[0]
        if head in tainted:
            return True
    return False


def _first_tainted_in(expr: str, tainted: set[str]) -> str | None:
    for tok in re.findall(r"[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*", expr):
        if tok in tainted:
            return tok
        head = tok.split(".", 1)[0]
        if head in tainted:
            return head
    return None


def _detect_transformation(rhs: str) -> str | None:
    """RHS 的 transformation 标签（spec §4.1.5）。"""
    if _has_sanitizer(rhs):
        # 取命中的第一个 sanitizer 名字
        for hint in SANITIZER_HINTS:
            if hint in rhs:
                return f"sanitize_hint:{hint.rstrip('.')}"
    if any(h in rhs for h in _CONCAT_HINTS):
        return "concat"
    if "%" in rhs and ("(" in rhs or rhs.count("'") >= 2 or rhs.count('"') >= 2):
        return "format"
    return None


def _has_sanitizer(expr: str) -> bool:
    return any(h in expr for h in SANITIZER_HINTS)


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
