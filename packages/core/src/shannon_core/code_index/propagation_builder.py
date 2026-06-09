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
            # （覆盖 RHS 含 sanitizer 但未引用 tainted 变量的情况）
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
    """Best-effort sanitizer detection using word boundaries.

    A plain substring match would falsely flag variable names like
    ``escaped_var`` or ``quote_str``. Word boundaries (\\b) ensure we match
    sanitizer *calls* (escape(...), htmlspecialchars(...), bleach.clean(...))
    without penalizing innocent identifiers that merely contain the substring.
    """
    return any(re.search(r"\b" + re.escape(h) + r"\b", expr) for h in SANITIZER_HINTS)


def build_propagation_graph(
    index: CodeIndex,
    typed_params_by_block: dict[str, list[TypedParameter]] | None = None,
) -> ParameterPropagationGraph:
    """Spec A 主入口。"""
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

    blocks_by_id = {b.id: b for b in index.blocks}
    sinks_by_caller: dict[str, list[SinkCallSite]] = {}
    for s in index.sink_call_sites:
        sinks_by_caller.setdefault(s.caller_id, []).append(s)

    flows: list[TaintFlow] = []

    for chain in index.chains:
        if not chain.path:
            continue
        entry_id = chain.path[0]
        entry_block = blocks_by_id.get(entry_id)
        if entry_block is None:
            continue
        seed = seed_taints(entry_block, typed_params_by_block.get(entry_id, []))
        if not seed:
            continue

        # spec §4.1.1：source_type 优先用 typed_params 提供的精确来源；
        # _infer_source_type 仅在没有 typed 信息时作为名字启发式回退。
        source_param = next(iter(seed), "")
        typed = typed_params_by_block.get(entry_id, [])
        typed_by_name = {tp.name: (tp.source or ParameterSource.UNKNOWN) for tp in typed}
        source_type = typed_by_name.get(source_param) or _infer_source_type(entry_block, source_param)

        for flow in _trace_chain(
            chain=chain,
            blocks_by_id=blocks_by_id,
            sinks_by_caller=sinks_by_caller,
            seed=seed,
            entry_block=entry_block,
            source_param=source_param,
            source_type=source_type,
        ):
            if not flow.flow_id:
                flow.flow_id = f"{entry_id}->{flow.sink_call_site_id}"
            # 给 propagation_steps 编号
            for n, step in enumerate(flow.propagation_steps, start=1):
                if not step.step_id:
                    step.step_id = f"{flow.flow_id}#s{n}"
            flows.append(flow)

    return ParameterPropagationGraph(
        taint_flows=flows,
        language_coverage=[language] if language else [],
        skipped_languages=[],
    )


def _trace_chain(
    *,
    chain: CallChain,
    blocks_by_id: dict[str, FuncBlock],
    sinks_by_caller: dict[str, list[SinkCallSite]],
    seed: set[str],
    entry_block: FuncBlock,
    source_param: str,
    source_type: ParameterSource,
) -> Iterable[TaintFlow]:
    """沿 CallChain.path 走 cross-function 传播。

    spec §4.1.3：
      current_tainted = {entry_func: seed}
      for i, func_id in enumerate(chain.path):
          intra = analyze_intra(func, current_tainted[func_id], sinks_in_func)
          for sink_hit in intra.hits:
              yield TaintFlow(...)
          if i+1 < len(chain.path):
              callee_id = chain.path[i+1]
              callee_tainted = map_params_to_callee(...)
              current_tainted[callee_id] = callee_tainted
    """
    current_tainted: dict[str, set[str]] = {entry_block.id: set(seed)}
    accumulated_steps: list[PropagationStep] = []
    has_sanitizer = False

    for i, func_id in enumerate(chain.path):
        block = blocks_by_id.get(func_id)
        if block is None:
            return
        sinks_in_func = sinks_by_caller.get(func_id, [])
        intra = analyze_intra(
            block=block,
            seed=current_tainted.get(func_id, set()),
            sinks_in_func=sinks_in_func,
        )

        # 命中 sink → 产出 flow
        for sink_id, hit in intra.hits.items():
            steps_total = list(accumulated_steps) + list(hit.local_steps)
            # confidence = 整条链最弱步（spec §4.1.5）：
            # 跨累积 steps（含跨函数映射步）+ 本函数命中 steps 取 min。
            chain_confidence = min(
                (s.confidence for s in steps_total),
                default=1.0,
            )
            yield TaintFlow(
                flow_id="",  # build_propagation_graph 统一编号
                entry_point_id=entry_block.id,
                source_param=source_param,
                source_type=source_type,
                propagation_steps=steps_total,
                sink_call_site_id=sink_id,
                sink_slot=hit.slot,
                tainted_arg_index=hit.tainted_arg_index,
                confidence=chain_confidence,
                has_sanitizer_hint=has_sanitizer or hit.has_sanitizer_hint,
                notes="",
            )

        # 把本函数的 local_steps 累入 accumulated_steps
        accumulated_steps.extend(intra.local_steps_accumulated or [])
        # 如果有 sanitizer 提示，传染到下游
        if intra.has_sanitizer_global:
            has_sanitizer = True

        # spec §4.1.2/§4.1.3：过程内分析扩展了本函数的 tainted 变量集（如
        # q = '...' + user_id 把 q 染污）。把这份扩展后的 tainted 集折叠回
        # current_tainted[func_id]，供下一跳 call-site 实参映射使用——否则
        # process(q) 里的 q 永远不被识别为 tainted，链路即断。
        local_tainted = set(current_tainted.get(func_id, set()))
        for s in intra.local_steps_accumulated or []:
            if s.to_func_id == func_id and s.to_param:
                local_tainted.add(s.to_param)
        current_tainted[func_id] = local_tainted

        # 准备下一跳：把当前 tainted 通过 call-site 实参映射到 callee 形参
        if i + 1 >= len(chain.path):
            return
        callee_id = chain.path[i + 1]
        callee_block = blocks_by_id.get(callee_id)
        if callee_block is None:
            return
        callee_seed = _map_call_site_to_callee_params(
            caller_block=block,
            caller_tainted=current_tainted.get(func_id, set()),
            callee_block=callee_block,
        )
        if not callee_seed:
            return
        current_tainted[callee_id] = callee_seed
        # 把跨函数这一步加进 accumulated_steps（informational）
        accumulated_steps.append(PropagationStep(
            step_id="",
            from_func_id=func_id,
            from_param=next(iter(current_tainted.get(func_id, {source_param})), source_param),
            to_func_id=callee_id,
            to_param=next(iter(callee_seed), ""),
            transformation=None,
            code_location=f"{block.file_path}:{block.start_line}",
            confidence=0.9,
        ))


def _map_call_site_to_callee_params(
    *,
    caller_block: FuncBlock,
    caller_tainted: set[str],
    callee_block: FuncBlock,
) -> set[str]:
    """从 caller 的源码里找出对 callee 的调用位置，按位置把 tainted 实参映射
    到 callee 的形参名。

    简化：扫 caller.source_code 里包含 callee_block.function_name 后跟 '(' 的行，
    提取括号内实参，按位置匹配 callee_block.parameters。
    """
    callee_name = callee_block.function_name
    callee_params = callee_block.parameters
    if not callee_params:
        return set()

    result: set[str] = set()
    for line in caller_block.source_code.splitlines():
        if callee_name + "(" not in line:
            continue
        # 抽出第一个 callee_name( ... ) 的实参列表（粗略、保守）
        inside = _extract_first_call_args(line, callee_name)
        if inside is None:
            continue
        for idx, arg_text in enumerate(inside):
            if idx >= len(callee_params):
                break
            if _expr_references_tainted(arg_text, caller_tainted):
                result.add(callee_params[idx])
    return result


def _extract_first_call_args(line: str, callee: str) -> list[str] | None:
    """从一行代码里提取 callee(...) 的实参文本列表。粗略实现：取 callee 后第一个
    '(' 到对应 ')' 之间的文本，按 ',' 拆。"""
    idx = line.find(callee + "(")
    if idx < 0:
        return None
    inside_start = idx + len(callee) + 1
    depth = 1
    inside_end = -1
    for j in range(inside_start, len(line)):
        ch = line[j]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                inside_end = j
                break
    if inside_end < 0:
        return None
    inside = line[inside_start:inside_end]
    if not inside.strip():
        return []
    return [a.strip() for a in _split_args_respecting_parens(inside)]


def _split_args_respecting_parens(s: str) -> list[str]:
    """按 ',' 拆分但忽略括号/引号内的逗号。"""
    out: list[str] = []
    cur = []
    depth = 0
    quote = None
    for ch in s:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            cur.append(ch)
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    if cur:
        out.append("".join(cur))
    return out


def _infer_source_type(block: FuncBlock, source_param: str) -> ParameterSource:
    """没有 typed_params 时给 source_param 一个保守的 source。"""
    # 真实 typed 信息由 typed_params_by_block 提供；这里只是回退
    if source_param in ("request", "req"):
        return ParameterSource.UNKNOWN
    if "user" in source_param.lower() or "id" in source_param.lower():
        return ParameterSource.QUERY_PARAM
    return ParameterSource.UNKNOWN
