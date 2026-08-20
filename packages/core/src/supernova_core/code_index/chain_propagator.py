"""Deterministic cross-function taint propagation along GitNexus call chains.

Consumes CallChain objects (from gitnexus_call_graph) and IntraResult per
function (from llm_taint_analyzer or deterministic intra-procedural analysis),
then maps tainted parameters across function boundaries without any LLM calls.

Algorithm:
  For each CallChain:
    1. Seed the chain head with intra_results[head_id].tainted_params
    2. Walk the path; at each hop:
       a. If the callee has sink hits in intra_results → emit TaintFlow
       b. Find call args from caller → callee via _find_call_args_for_callee
       c. Map tainted args to callee params by position
       d. Conservative: if no call args found, pass all tainted to all callee params
    3. Respect max_depth to limit traversal depth
"""

import logging
import re
from collections import defaultdict

from supernova_core.code_index.models import CallChain, FuncBlock, ParameterSource
from supernova_core.code_index.parameter_models import (
    IntraResult,
    PropagationStep,
    SinkCallSite,
    SlotContext,
    SourcePoint,
    TaintFlow,
)

logger = logging.getLogger(__name__)


def _references_tainted(arg_expr: str, tainted: set[str]) -> bool:
    """Check if an argument expression references any tainted variable.

    Over-approximate: any tainted name appearing as substring of arg_expr → True.
    Uses word-boundary matching to avoid false positives on unrelated substrings,
    but also checks prefix (container over-approximation: ``request`` matches
    ``request.user_id``).
    """
    if not arg_expr or not tainted:
        return False
    for t in tainted:
        # Substring check — covers "request" matching "request.user_id"
        if t in arg_expr:
            return True
    return False


def _find_call_args_for_callee(
    caller: FuncBlock,
    callee_id: str,
) -> list[str]:
    """From caller source code, find arguments passed when calling callee.

    Uses regex to find ``callee_name(arg1, arg2, ...)`` in the caller source.
    Returns the list of argument expression strings.
    """
    # Extract callee function name from its id (format: "file:func_name:line")
    parts = callee_id.split(":")
    if len(parts) >= 3:
        callee_name = parts[1]
    else:
        callee_name = callee_id

    args_list: list[str] = []
    for line in caller.source_code.splitlines():
        # Look for callee_name( ... )
        pattern = re.escape(callee_name) + r"\s*\("
        m = re.search(pattern, line)
        if m is None:
            continue
        inside = _extract_first_call_args(line, callee_name)
        if inside is not None:
            args_list.extend(inside)
            return args_list
    return args_list


def _extract_first_call_args(line: str, callee: str) -> list[str] | None:
    """Extract argument text list from the first ``callee(...)`` in a line."""
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
    """Split by ',' but ignore commas inside parentheses/brackets/quotes."""
    out: list[str] = []
    cur: list[str] = []
    depth = 0
    quote: str | None = None
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


def propagate_across_chains(
    chains: list[CallChain],
    blocks: list[FuncBlock],
    intra_results: dict[str, IntraResult],
    *,
    max_depth: int = 20,
) -> list[TaintFlow]:
    """Walk each chain, maintaining current_tainted set, produce TaintFlows.

    At each step:
      - For chain head: seed from intra_results[head_id].tainted_params
      - For each hop: find call args, map tainted args to callee params
      - If no call args found: conservative — pass all tainted to all callee params
      - When callee has hits: produce TaintFlow

    Returns a list of TaintFlow objects. Uses ParameterSource.QUERY_PARAM for
    source_type as a default (real typed info should come from enhanced_parameters).
    """
    if not chains:
        return []

    blocks_by_id: dict[str, FuncBlock] = {b.id: b for b in blocks}
    flows: list[TaintFlow] = []

    for chain in chains:
        if not chain.path:
            continue

        # Seed from chain head
        head_id = chain.path[0]
        head_intra = intra_results.get(head_id)
        if head_intra is None or not head_intra.tainted_params:
            continue

        current_tainted: dict[str, set[str]] = {
            head_id: set(head_intra.tainted_params),
        }
        accumulated_steps: list[PropagationStep] = []
        source_param = next(iter(head_intra.tainted_params), "")

        for i, func_id in enumerate(chain.path):
            if i > max_depth:
                break

            block = blocks_by_id.get(func_id)
            if block is None:
                continue

            func_intra = intra_results.get(func_id)
            if func_intra is None:
                continue

            # Collect local steps
            if func_intra.local_steps:
                accumulated_steps.extend(func_intra.local_steps)

            # If this function has sink hits, produce TaintFlow(s)
            for sink_id, sink_confidence in func_intra.hits.items():
                steps_total = list(accumulated_steps)
                chain_confidence = min(
                    (s.confidence for s in steps_total),
                    default=sink_confidence,
                )
                # Use sink confidence as floor if no steps
                if not steps_total:
                    chain_confidence = sink_confidence

                # Forward path intentionally does NOT set sink_slot (defaults to
                # SlotContext.GENERIC). It is consumed only by authz
                # `_source_reaches_sink`, which does NOT route through `_route_for`
                # — so GENERIC is harmless here. Do NOT route forward flows
                # through inj/ssrf `_route_for` without first threading sink_slot
                # (see backward path / T3-fix in propagate_backward_across_chains).
                flow_id = f"{head_id}->{sink_id}"
                flows.append(TaintFlow(
                    flow_id=flow_id,
                    entry_point_id=head_id,
                    source_param=source_param,
                    source_type=ParameterSource.QUERY_PARAM,
                    propagation_steps=steps_total,
                    sink_call_site_id=sink_id,
                    confidence=chain_confidence,
                    notes="forward: no sink_slot (authz _source_reaches_sink only)",
                ))

            # Prepare next hop: map tainted through call site to callee params
            if i + 1 >= len(chain.path):
                break
            if i + 1 > max_depth:
                break

            callee_id = chain.path[i + 1]
            callee_block = blocks_by_id.get(callee_id)
            if callee_block is None:
                continue

            caller_tainted = current_tainted.get(func_id, set())
            callee_seed = _map_call_site_params(
                caller_block=block,
                caller_tainted=caller_tainted,
                callee_block=callee_block,
            )

            if callee_seed:
                current_tainted[callee_id] = callee_seed
                # Record the cross-function step
                accumulated_steps.append(PropagationStep(
                    from_func_id=func_id,
                    from_param=next(iter(caller_tainted), source_param),
                    to_func_id=callee_id,
                    to_param=next(iter(callee_seed), ""),
                    code_location=f"{block.file_path}:{block.start_line}",
                    confidence=0.9,
                ))
            else:
                # Conservative: even with no mapping, don't stop — propagate
                # all tainted as all callee params (only if callee has params)
                if callee_block.parameters:
                    current_tainted[callee_id] = set(callee_block.parameters)

    return flows


def _map_call_site_params(
    caller_block: FuncBlock,
    caller_tainted: set[str],
    callee_block: FuncBlock,
) -> set[str]:
    """Map tainted caller variables to callee parameters via call-site analysis.

    Finds the call to callee in caller source, extracts positional args,
    checks which are tainted, and maps them to callee parameter names.
    """
    callee_params = callee_block.parameters
    if not callee_params:
        return set()

    call_args = _find_call_args_for_callee(caller_block, callee_block.id)

    result: set[str] = set()

    if call_args:
        # Map by position: arg[i] → callee_params[i] if tainted
        for idx, arg_text in enumerate(call_args):
            if idx >= len(callee_params):
                break
            if _references_tainted(arg_text, caller_tainted):
                result.add(callee_params[idx])
    else:
        # Conservative: no call args found → pass all tainted to all callee params
        result = set(callee_params)

    return result


# Literal-expression filter for backward param mapping: a purely literal arg
# (quoted string or bare number) is not a real taint source, so it must not be
# added to caller-side tainted. Variable/expression args pass through.
_LITERAL_RE = re.compile(r'^(["\']).*\1$|^-?\d+(\.\d+)?$')


def _is_literal_arg(arg_expr: str) -> bool:
    """Return True if arg_expr is a pure literal (quoted string or number)."""
    return bool(_LITERAL_RE.match(arg_expr.strip()))


def _map_call_site_params_reverse(
    callee_block: FuncBlock,
    callee_tainted: set[str],
    caller_block: FuncBlock,
) -> set[str]:
    """反向参数映射(backward):已知 callee 的 tainted params,反推 caller 调用时
    传的哪些实参被污染 → 返回 caller 端被污染的变量名/表达式集合。

    与 forward 版 ``_map_call_site_params`` 对称:forward 已知 caller tainted 推
    callee params,backward 已知 callee tainted params 反推 caller 实参。
    复用 ``_find_call_args_for_callee``(找 caller 里调用 callee 的实参列表)。

    对 callee 的每个 tainted param(按位置 i)看 caller 的 call_args[i],
    若该实参是变量/表达式(非纯字面量)→ 加入结果(作为 caller 端 tainted)。
    找不到调用实参 → 保守回退:caller 所有 params 视为 tainted(对齐 forward 保守)。
    """
    callee_params = callee_block.parameters
    if not callee_params:
        return set()

    call_args = _find_call_args_for_callee(caller_block, callee_block.id)
    if not call_args:
        # 保守:无法定位调用 → caller 所有参数视为 tainted
        return set(caller_block.parameters)

    tainted_indices = {
        i for i, p in enumerate(callee_params) if p in callee_tainted
    }
    result: set[str] = set()
    for idx in tainted_indices:
        if idx >= len(call_args):
            break
        arg_expr = call_args[idx].strip()
        if not arg_expr:
            continue
        # arg_expr 是 caller 端表达式(如 req.query.x / userId / getVal(x))。
        # 纯字面量(引号字符串 / 纯数字)不是真实污染源 → 跳过;
        # 变量/表达式 → 作为 caller 端 tainted(下游 _source_points_matching 用 substring 匹配)。
        if _is_literal_arg(arg_expr):
            continue
        result.add(arg_expr)
    return result


def _tainted_params_reaching_sink(
    sink: "SinkCallSite",
    intra: "IntraResult",
) -> set[str]:
    """sink 所在函数的哪些参数 tainted(到达 sink)。

    优先用 intra.tainted_params(LLM/确定性 intra 分析);回退:dangerous_slots
    的 expression 反推(若 intra 缺失)。
    """
    if intra and intra.tainted_params:
        return set(intra.tainted_params)
    # 回退:从 dangerous_slots.expression 提取参数名(浅)
    out: set[str] = set()
    for slot in getattr(sink, "dangerous_slots", []) or []:
        expr = (slot.expression or "").strip()
        if expr:
            out.add(expr)
    return out


def _source_points_matching(
    entry_id: str,
    tainted_in_entry: set[str],
    source_points: list["SourcePoint"],
) -> list["SourcePoint"]:
    """entry 的 tainted 变量命中哪些 SourcePoint(substring 匹配,过近似)。"""
    out = []
    for sp in source_points:
        if sp.entry_point_id != entry_id:
            continue
        # SourcePoint.expression(如 req.query.x)或 param_name(x)出现在 entry 的 tainted 集合
        for t in tainted_in_entry:
            if sp.param_name in t or sp.expression in t or t in sp.expression:
                out.append(sp)
                break
    return out


def propagate_backward_across_chains(
    chains: list[CallChain],
    blocks: list[FuncBlock],
    intra_results: dict[str, IntraResult],
    sink_call_sites: list["SinkCallSite"],
    source_points: list["SourcePoint"],
    *,
    max_depth: int = 20,
) -> list[TaintFlow]:
    """backward(Sink→Source):从 SinkCallSite 反向沿 chain 回溯,终点用 SourcePoint 锚定。

    双向锚定:起点 SinkCallSite(sink 真实)+ 终点 SourcePoint(source 真实)。
    只有反向追到真实 SourcePoint 的链才成立(产 TaintFlow);否则丢弃。
    产出仍是 source→sink 语义的 TaintFlow(propagation_steps 正序化),下游零改动。
    """
    if not chains or not sink_call_sites:
        return []

    blocks_by_id: dict[str, FuncBlock] = {b.id: b for b in blocks}
    sinks_by_caller: dict[str, list["SinkCallSite"]] = defaultdict(list)
    for s in sink_call_sites:
        sinks_by_caller[s.caller_id].append(s)

    flows: list[TaintFlow] = []
    for chain in chains:
        if not chain.path:
            continue
        # 找 chain 上含 sink 的节点
        for sink_step, sid in enumerate(chain.path):
            sinks_here = sinks_by_caller.get(sid, [])
            if not sinks_here:
                continue
            sink_func = blocks_by_id.get(sid)
            if sink_func is None:
                continue
            for sink in sinks_here:
                seed = _tainted_params_reaching_sink(
                    sink, intra_results.get(sid))
                if not seed:
                    continue
                # 反向沿 path[sink_step → 0]
                current_tainted = set(seed)
                steps_rev: list[PropagationStep] = []
                anchored: list["SourcePoint"] = []
                for i in range(sink_step, -1, -1):
                    func_id = chain.path[i]
                    if i == 0:
                        # 到达 entry:终点锚定
                        anchored = _source_points_matching(
                            func_id, current_tainted, source_points)
                        break
                    callee = blocks_by_id.get(func_id)
                    caller = blocks_by_id.get(chain.path[i - 1])
                    if callee is None or caller is None:
                        continue
                    caller_tainted = _map_call_site_params_reverse(
                        callee_block=callee, callee_tainted=current_tainted,
                        caller_block=caller)
                    steps_rev.append(PropagationStep(
                        from_func_id=callee.id,
                        from_param=next(iter(current_tainted), ""),
                        to_func_id=caller.id,
                        to_param=next(iter(caller_tainted), ""),
                        code_location=f"{callee.file_path}:{callee.start_line}",
                        confidence=0.9,
                    ))
                    current_tainted = caller_tainted
                    if sink_step - (i - 1) > max_depth:
                        break
                for sp in anchored:
                    steps_fwd = list(reversed(steps_rev))
                    # 合并 sink 所在函数的 intra summary step(携带 sanitizer/transformation)。
                    # 统一覆盖单函数(sink_step=0,跨函数 hop 为空)与多函数场景——
                    # 之前 local_steps 不被消费导致 sanitizer 管道断链。
                    sink_intra = intra_results.get(sid)
                    if sink_intra:
                        steps_fwd.extend(
                            s for s in sink_intra.local_steps if s.to_param == sink.id
                        )
                    # 透传 sink 的危险槽位到 flow(否则 sink_slot=GENERIC 被
                    # _route_for 拒掉 inj/ssrf,sanitizer 信息到不了 verdict prompt)。
                    # TODO(multi-slot): a sink with >1 dangerous_slot only carries the
                    # primary slot here; secondary slots lose _route_for routing.
                    # Verdict LLM still sees ALL expressions via extract_candidate_chains
                    # so judgment is intact — only routing is single-slot. Per-slot
                    # fan-out is a larger redesign (deferred, see task-3-fix-report).
                    primary = sink.dangerous_slots[0] if sink.dangerous_slots else None
                    flows.append(TaintFlow(
                        flow_id=f"{sp.entry_point_id}->{sink.id}",
                        entry_point_id=sp.entry_point_id,
                        source_param=sp.param_name,
                        source_type=sp.source_type,  # 精确,非硬编码 QUERY_PARAM
                        propagation_steps=steps_fwd,
                        sink_call_site_id=sink.id,
                        sink_slot=primary.slot if primary else SlotContext.GENERIC,
                        tainted_arg_index=primary.arg_index if primary else -1,
                        confidence=min(
                            (s.confidence for s in steps_fwd),
                            default=0.9,
                        ),
                        notes="backward-anchored",
                    ))
    return flows


def produce_intra_first_taint_flows(
    sink_call_sites: list["SinkCallSite"],
    intra_results: dict[str, IntraResult],
    source_points: list["SourcePoint"],
    blocks: list[FuncBlock],
) -> list[TaintFlow]:
    """intra-first(spec 2026-07-10 §3.2):不依赖 chain,对每个含 sink 函数直接产 TaintFlow。

    核心洞察:intra 已把同函数 source→sink 算好存进 ``local_steps``,source 也由补召回识别
    (SourcePoint.entry_point_id = 该函数 id),只差一条不经 chain 的直接产 TaintFlow 路径。

    对每个含 sink 函数:取 ``intra.local_steps`` + ``tainted_params``,
    ``_source_points_matching`` 推广到 sink 所在函数(当前 backward 只在 chain entry i==0
    调)→ 匹配 SourcePoint 直接产 TaintFlow(单步 intra,不经 chain)。覆盖 handler 不在
    chain → backward 丢弃 intra 结果 → taint_flows=0 的根因(spec §2)。

    source 是 ``llm-discovered-source`` → ``needs_review=True``(下游 chain_verdict 复核)。
    sink_slot / tainted_arg_index 透传(防 _route_for 拒 inj/ssrf,同 backward 契约)。

    spec 2026-08-21 修复点 A —— 表达式回退:intra 缺失/空判定/sink 未命中时,用
    ``dangerous_slots[].expression`` 直接匹配 SourcePoint 产 flow(对齐 backward 的
    ``_tainted_params_reaching_sink`` 回退)。NodeGoat 断链根因:参数提取不含嵌套
    arrow 的 req → intra LLM 对"db 到 eval(req.body.preTax)"返回合法空判定(不触发
    fallback)→ intra-first 双重门全断。回退 flow 一律 needs_review + 低置信,字面量
    表达式(常量 sink)不产。
    """
    from supernova_core.code_index.llm_taint_analyzer import _is_literal_expression

    _EXPR_FALLBACK_CONFIDENCE = 0.5  # 对齐 _INDIRECT_HIT_CONFIDENCE(间接命中档)

    sinks_by_caller: dict[str, list["SinkCallSite"]] = defaultdict(list)
    for s in sink_call_sites:
        sinks_by_caller[s.caller_id].append(s)

    flows: list[TaintFlow] = []
    for func_id, sinks in sinks_by_caller.items():
        intra = intra_results.get(func_id)
        produced: set[tuple[str, str]] = set()  # (source_param, sink.id) 主路径已产
        if intra is not None and intra.tainted_params:
            # _source_points_matching 推广到 sink 所在函数:source 补召回产的 SourcePoint
            # entry_point_id = 该函数 id,故 substring 匹配能命中。
            matching = _source_points_matching(func_id, intra.tainted_params, source_points)
            for sp in matching:
                for sink in sinks:
                    if sink.id not in intra.hits:
                        continue  # intra 没判定该 sink 命中 → 跳过
                    steps = [s for s in intra.local_steps if s.to_param == sink.id]
                    primary = sink.dangerous_slots[0] if sink.dangerous_slots else None
                    flows.append(TaintFlow(
                        flow_id=f"{sp.entry_point_id}->{sink.id}",
                        entry_point_id=sp.entry_point_id,
                        source_param=sp.param_name,
                        source_type=sp.source_type,
                        propagation_steps=steps,
                        sink_call_site_id=sink.id,
                        sink_slot=primary.slot if primary else SlotContext.GENERIC,
                        tainted_arg_index=primary.arg_index if primary else -1,
                        confidence=intra.hits.get(sink.id, 0.9),
                        needs_review=(sp.rule_id == "llm-discovered-source"),
                        notes="intra-first",
                    ))
                    produced.add((sp.param_name, sink.id))

        # ---- spec 2026-08-21 修复点 A: 表达式回退 ----
        # 仅当 intra 对该函数无有效信息(缺失/空判定,即"问错问题"场景)才回退;
        # intra 有非空 tainted_params 但该 sink 不在 hits = LLM 有依据的否定,
        # 尊重判定不回退(守 test_intra_first_skips_sink_not_in_intra_hits 语义)。
        intra_informative = intra is not None and bool(intra.tainted_params)
        if not intra_informative:
            for sink in sinks:
                exprs = [slot.expression for slot in (sink.dangerous_slots or [])
                         if slot.expression and not _is_literal_expression(slot.expression)]
                if not exprs:
                    continue  # 无非常量表达式(纯字面量 sink,如 redirect("/login"))→ 零噪音
                for sp in _source_points_matching(func_id, set(exprs), source_points):
                    if (sp.param_name, sink.id) in produced:
                        continue  # 主路径已产,不叠加
                    primary = sink.dangerous_slots[0] if sink.dangerous_slots else None
                    flows.append(TaintFlow(
                        flow_id=f"{sp.entry_point_id}->{sink.id}",
                        entry_point_id=sp.entry_point_id,
                        source_param=sp.param_name,
                        source_type=sp.source_type,
                        propagation_steps=[],
                        sink_call_site_id=sink.id,
                        sink_slot=primary.slot if primary else SlotContext.GENERIC,
                        tainted_arg_index=primary.arg_index if primary else -1,
                        confidence=_EXPR_FALLBACK_CONFIDENCE,
                        needs_review=True,  # 未经 intra 证实 → 一律复核(chain_verdict 轻判)
                        notes="intra-first-expr-fallback",
                    ))
    return flows


def merge_taint_flows(
    intra_first: list[TaintFlow],
    backward: list[TaintFlow],
) -> list[TaintFlow]:
    """合并 intra-first + backward,按 ``(entry_point_id, source_param, sink.id)`` 去重;
    intra-first 优先(同函数超集),backward 补跨函数的(spec §3.2)。

    intra-first 产同函数的(source 在 sink 所在函数),backward 产跨函数的(source 在
    chain entry);同函数场景两者重叠 → intra-first 优先,backward 的去重掉。
    """
    seen: set[tuple] = set()
    out: list[TaintFlow] = []
    for f in intra_first:
        key = (f.entry_point_id, f.source_param, f.sink_call_site_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    for f in backward:
        key = (f.entry_point_id, f.source_param, f.sink_call_site_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out
