"""vuln-authz GitNexus deterministic track (spec §5.7, ⭐ net-progress direction).

Produces IDOR candidate chains: handler→sink call paths where no ownership
guard dominates the sink. The "guard must dominate the sink" is approximated
heuristically (spec §8: not a full dominance proof) — we flag a path as an
IDOR candidate when the handler segment carries no ownership predicate (ORM
`where { userId }` / `findByOwner` etc.) AND the path reaches a side-effect
sink (DB write / ORM mutation / file / state). This is conservative: we
over-report candidates (宁过报不漏报, spec §2 principle 4) and let the LLM
chain-judgement pass (Task 4) confirm or reject each.

Ownership/auth detection reuses the shared `OWNERSHIP_PREDICATE_RE` pattern
(`shannon_core.code_index.patterns`) — imported, not reimplemented.

This is the GitNexus TRACK of the dual-track merge. The LLM track (authz
agent, vuln-authz.txt) is untouched (spec principle 2: no anchoring). The
two tracks merge via Plan 3's merge_dual_track_queues (verdict OR by
endpoint).
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from shannon_core.code_index.models import CodeIndex, EntryPoint, FuncBlock

logger = logging.getLogger(__name__)


# Side-effect sinks: DB writes, ORM mutations, file writes, state updates.
# Matched against a node's source_code OR function_name (some sinks are
# named update/save/remove/destroy directly). Cross-language.
_SIDE_EFFECT_SINK_RE = re.compile(
    r"(?i)("
    r"\b(update|save|create|destroy|delete|remove|insert|upsert|patch)\b\s*[(<]"
    r"|\.update\s*\(|\.save\s*\(|\.create\s*\(|\.remove\s*\(|\.destroy\s*\("
    r"|\.delete\s*\(|\.insert\s*\(|\.upsert\s*\(|\.patch\s*\("
    r"|\b(exec|query|run)\s*\(\s*['\"]?(update|insert|delete|drop)"
    r"|model\s*\.\s*(save|update|remove|destroy)\b"
    r"|repo\s*\.\s*(save|update|remove|destroy)\b"
    r")"
)

# IDOR-flavor user-controlled source reference: req.params / req.body / req.query
# (group 2 = the property name, e.g. userId in req.params.userId; optional because
# destructuring writes `const { userId } = req.params`). The injection-flavored
# SourcePoint detection (query/body) misses IDOR-flavor req.params.userId — this
# pattern closes that gap so a handler that takes the resource id from req.params
# is recognized as having a user-controlled source (spec §5.7, NodeGoat A4).
_REQ_REF_RE = re.compile(r"req\.(params|body|query)(?:\.(\w+))?")


@dataclass(frozen=True)
class IDORCandidateChain:
    """A handler→sink path flagged as a potential IDOR (no ownership guard)."""
    endpoint_id: str          # EntryPoint.func_block_id of the handler
    handler_id: str           # FuncBlock.id of the handler (= endpoint_id here)
    sink_id: str              # FuncBlock.id of the side-effect sink
    sink_step_idx: int        # sink 在 path 中的下标（spec §4.8 决策7，扫全链）
    path: tuple[str, ...]     # ordered FuncBlock.id list, handler→sink
    guard_nodes_on_path: tuple[str, ...]  # ownership-guard node ids on path (empty=none)
    source_point_ids: tuple[str, ...] = ()  # 命中的 SourcePoint（Phase C：source 证据）


class AuthzTrackBuildResult(NamedTuple):
    """build_authz_gitnexus_track 的返回：候选 + 入口点诊断（spec §3.1）。"""
    markdown: str
    dominance_candidates: list[IDORCandidateChain]
    framework_candidates: list[FrameworkIDORCandidate]
    http_route_count: int       # entry_type=="http_route" 且 route 非空的入口点数（dominance 直接输入）
    entry_point_total: int      # code_index entry_points 总数（含 gitnexus 合成项）


def _is_side_effect_sink(block: FuncBlock | None) -> bool:
    """True if the block performs a DB/ORM/file/state side-effect."""
    if block is None:
        return False
    if _SIDE_EFFECT_SINK_RE.search(block.source_code):
        return True
    return bool(_SIDE_EFFECT_SINK_RE.search(block.function_name + "("))


def _handler_has_ownership_guard(handler: FuncBlock) -> bool:
    """True if handler source carries an ownership predicate (OWNERSHIP_PREDICATE_RE from patterns.py)."""
    from shannon_core.code_index.patterns import OWNERSHIP_PREDICATE_RE
    return OWNERSHIP_PREDICATE_RE.search(handler.source_code) is not None


def _segment_has_ownership_guard(segment_ids: list[str], blocks_by_id: dict[str, FuncBlock]) -> bool:
    """entry→sink_step 段（含两端）任一 FuncBlock 源码含 ownership 谓词 → True（决策6）。"""
    from shannon_core.code_index.patterns import OWNERSHIP_PREDICATE_RE
    for sid in segment_ids:
        b = blocks_by_id.get(sid)
        if b is not None and OWNERSHIP_PREDICATE_RE.search(b.source_code):
            return True
    return False


def _is_route_registration_block(blk: FuncBlock, hr_count_for_fb: int) -> bool:
    """True if blk is an Express-style route-registration/wiring block (NOT a real
    handler). NodeGoat collapses 22 http_route entries onto one `index.js:index:11`
    wiring block — anchoring the IDOR judgement there breaks source propagation.

    Two signals (either): ① source has ≥2 route registrations
    (`(app|router).(get|post|…)(...)`, reuses entry_points._EXPRESS_ROUTE_PATTERN);
    ② the block's func_block_id is the collapse target of ≥3 http_route entries.
    """
    from shannon_core.code_index.entry_points import _EXPRESS_ROUTE_PATTERN
    route_count = len(_EXPRESS_ROUTE_PATTERN.findall(blk.source_code))
    return route_count >= 2 or hr_count_for_fb >= 3


def _resolve_real_handler(
    chain, blocks_by_id: dict[str, FuncBlock], is_reg: bool,
) -> tuple[int | None, FuncBlock | None]:
    """Re-anchor a chain to its real handler, returning (anchor_step, real_handler).

    Non-registration block → (0, head): the entry block IS the real handler.
    Registration block → first node in path[1:] that is resolvable, NOT a
    side-effect sink, and NOT itself a registration block (the real callback
    GitNexus already traced, e.g. chain path[1] = ProfileHandler:8). Returns
    (None, None) if no such node exists → caller skips the chain.
    """
    path = chain.path
    if not is_reg:
        head = blocks_by_id.get(path[0])
        return (0, head) if head is not None else (None, None)
    for step in range(1, len(path)):
        blk = blocks_by_id.get(path[step])
        if blk is None:
            continue
        if _is_side_effect_sink(blk):
            continue
        if _is_route_registration_block(blk, 0):
            continue
        return (step, blk)
    return (None, None)


def _idor_reaches_sink(
    real_handler: FuncBlock,
    ep_sources: list,
    segment_ids: list[str],
    blocks_by_id: dict[str, FuncBlock],
) -> bool:
    """IDOR-flavor forward reachability from the re-anchored real handler to the
    segment's terminal sink. Lenient (宁过报): a missing intermediate callee (a
    class node without a line number, e.g. AllocationsDAO) does NOT kill
    propagation — we keep the tainted set and continue.

    Seed = entry SourcePoint expressions ∪ req.params/body/query property refs
    extracted from the real handler's source. The req.* extraction closes the
    IDOR-flavor blind spot: a handler taking the resource id from req.params is
    user-controlled even when no injection-flavored SourcePoint was recorded.
    """
    from shannon_core.code_index.chain_propagator import _map_call_site_params

    seed: set[str] = {
        sp.expression or sp.param_name for sp in ep_sources if sp.expression or sp.param_name
    }
    for m in _REQ_REF_RE.finditer(real_handler.source_code):
        seed.add(m.group(0))           # e.g. req.params.userId
        if m.group(2):
            seed.add(m.group(2))       # e.g. userId (bare, matches destructured use)
    if not seed:
        return False
    if len(segment_ids) == 1:
        blk = blocks_by_id.get(segment_ids[0])
        if blk is None:
            return False
        return any(token and token in blk.source_code for token in seed)
    current_tainted: set[str] = set(seed)
    for i in range(len(segment_ids) - 1):
        caller = blocks_by_id.get(segment_ids[i])
        callee = blocks_by_id.get(segment_ids[i + 1])
        if caller is None or callee is None:
            continue  # lenient: missing intermediate → keep propagating
        current_tainted = _map_call_site_params(
            caller_block=caller, caller_tainted=current_tainted, callee_block=callee)
        if not current_tainted:
            return False
    return bool(current_tainted)


# authz 判定的 entry 类型白名单（spec §4.8 改1）。gitnexus_process 放宽 route 守卫。
_AUTHZ_ENTRY_TYPES = ("http_route", "rpc", "gitnexus_process")


def _source_reaches_sink(
    ep_sources: list,
    segment_ids: list[str],
    blocks_by_id: dict[str, FuncBlock],
) -> bool:
    """SourcePoint 参数值是否正向流到 segment 末端的 sink 函数（复用 forward 工具）。

    从 entry 的 SourcePoint 表达式集合（seed）正向沿 segment 传播，用
    chain_propagator._map_call_site_params（forward）逐跳映射，看 tainted 能否
    到达 segment 末端的 sink 函数参数。过近似（substring），宁过报。
    """
    from shannon_core.code_index.chain_propagator import _map_call_site_params

    if not ep_sources or len(segment_ids) < 2:
        # 单节点 segment（entry 自身是 sink）→ 看 SourcePoint 表达式是否直接在该函数体
        if len(segment_ids) == 1:
            blk = blocks_by_id.get(segment_ids[0])
            if blk is None:
                return False
            return any(
                (sp.expression or sp.param_name) and
                (sp.expression in blk.source_code or sp.param_name in blk.source_code)
                for sp in ep_sources
            )
        return False

    # seed: entry 的 SourcePoint 表达式/参数名集合
    current_tainted: set[str] = {
        sp.expression or sp.param_name for sp in ep_sources if sp.expression or sp.param_name
    }
    for i in range(len(segment_ids) - 1):
        caller = blocks_by_id.get(segment_ids[i])
        callee = blocks_by_id.get(segment_ids[i + 1])
        if caller is None or callee is None:
            continue
        current_tainted = _map_call_site_params(
            caller_block=caller, caller_tainted=current_tainted, callee_block=callee)
        if not current_tainted:
            return False
    return bool(current_tainted)


def find_unguarded_sink_paths(
    index: CodeIndex,
    *,
    max_paths_per_endpoint: int = 20,
) -> list[IDORCandidateChain]:
    """Find handler→sink paths lacking an ownership guard (IDOR candidates).

    三重过滤（Phase C，source-anchored + 注册块 re-anchor）：
      ① entry 接收用户可控输入：有至少一个 SourcePoint，**或** re-anchor 后的真实
         handler 引 req.params/body/query（IDOR 风味源，补注入风味的 SourcePoint 检测
         盲区 —— NodeGoat A4：`const { userId } = req.params`）。无任一 → 跳过。
      ② 参数实际正向流到 side-effect sink（`_idor_reaches_sink`，req.* seed + 宽松容忍
         缺失中间块，宁过报）。
      ③ anchor→sink_step 段（含两端）无 ownership 谓词（决策6）。
    注册块（Express `(app|router).(get|…)` wiring 块，或 ≥3 http_route 塌缩目标）→
    re-anchor 到 GitNexus 已追的 chain path[1] 真实 handler；非注册块 → anchor 在 head。
    sink 扫 anchor 之后全链任意步 side-effect（决策7）；真实 handler 自身含 ownership
    谓词 → 短路（dominance）。命中时收集 source_point_ids 作为 source 证据。

    Dedup by (endpoint_id, sink_id). Capped per endpoint to bound the
    judge-LLM cost.
    """
    blocks_by_id: dict[str, FuncBlock] = {b.id: b for b in index.blocks}
    # SourcePoint 按 entry 分组
    sources_by_ep: dict[str, list] = defaultdict(list)
    for sp in (index.source_points or []):
        sources_by_ep[sp.entry_point_id].append(sp)

    # 改1: entry 过滤 + gitnexus_process 放宽 route 守卫（断点①）
    entry_eps = [
        ep for ep in index.entry_points
        if ep.entry_type in _AUTHZ_ENTRY_TYPES
        and (ep.entry_type == "gitnexus_process" or ep.route is not None)
    ]

    # http_route entry 数 per func_block_id —— 注册块塌缩信号（NodeGoat 22 路由
    # 全指 index:11 wiring 块）。用于判该 entry 是否注册块（需 re-anchor）。
    hr_count_by_fb: dict[str, int] = defaultdict(int)
    for ep in entry_eps:
        if ep.entry_type == "http_route":
            hr_count_by_fb[ep.func_block_id] += 1

    candidates: list[IDORCandidateChain] = []
    seen: set[tuple[str, str]] = set()  # (endpoint_id, sink_id)

    for ep in entry_eps:
        ep_sources = sources_by_ep.get(ep.func_block_id, [])
        ep_block = blocks_by_id.get(ep.func_block_id)
        is_reg = ep_block is not None and _is_route_registration_block(
            ep_block, hr_count_by_fb.get(ep.func_block_id, 0))
        count_for_ep = 0
        for chain in index.chains:
            if chain.entry_point_id != ep.func_block_id or not chain.path:
                continue
            # re-anchor：注册块 → path[1:] 真实 handler；非注册块 → head。
            anchor_step, real_handler = _resolve_real_handler(chain, blocks_by_id, is_reg)
            if real_handler is None:
                continue  # 无可 anchor 的真实 handler（注册块但 path[1:] 全缺失/是 sink）
            # ① IDOR 源门：entry 有 SourcePoint，或真实 handler 引 req.params/body/query
            #    （IDOR 风味源不被注入风味的 SourcePoint 检测识别，这里补认 —— NodeGoat A4）。
            if not (ep_sources or _REQ_REF_RE.search(real_handler.source_code)):
                continue
            # dominance 短路：判真实 handler（非注册块）的 ownership 谓词。
            if _handler_has_ownership_guard(real_handler):
                continue
            # 改2: 扫 anchor 之后全链找 side-effect sink（决策7）。sink 必须在 anchor 之后
            # （被调用的 callee），anchor 自身源码里的 side-effect 不算"到达 sink"。
            for step_idx in range(anchor_step + 1, len(chain.path)):
                sid = chain.path[step_idx]
                if not _is_side_effect_sink(blocks_by_id.get(sid)):
                    continue
                key = (ep.func_block_id, sid)
                if key in seen:
                    continue
                # segment 从 anchor 起（剔除注册块）：entry→sink_step 段 ownership 扫描（决策6）。
                segment = list(chain.path[anchor_step: step_idx + 1])
                if _segment_has_ownership_guard(segment, blocks_by_id):
                    continue
                # ② IDOR 风味正向可达（req.* seed，宽松容忍缺失中间块）。
                if not _idor_reaches_sink(real_handler, ep_sources, segment, blocks_by_id):
                    continue
                # ③ 已通过 ownership 检查；收集命中的 SourcePoint 作为 source 证据
                hit_sp_ids = tuple(sp.id for sp in ep_sources)
                seen.add(key)
                candidates.append(IDORCandidateChain(
                    endpoint_id=ep.func_block_id,
                    handler_id=real_handler.id,   # re-anchored 真实 handler（非注册块）
                    sink_id=sid,
                    sink_step_idx=step_idx,
                    path=tuple(chain.path),
                    guard_nodes_on_path=(),  # segment guard absent → no guards
                    source_point_ids=hit_sp_ids,
                ))
                count_for_ep += 1
                if count_for_ep >= max_paths_per_endpoint:
                    break
            if count_for_ep >= max_paths_per_endpoint:
                break

    gn_process_with_sources = sum(
        1 for ep in entry_eps
        if ep.entry_type == "gitnexus_process" and sources_by_ep.get(ep.func_block_id)
    )
    logger.info(
        "authz GitNexus track: %d entry endpoints with sources "
        "(gitnexus_process=%d), %d IDOR candidates",
        len([ep for ep in entry_eps if sources_by_ep.get(ep.func_block_id)]),
        gn_process_with_sources,
        len(candidates),
    )
    return candidates


@dataclass(frozen=True)
class FrameworkIDORCandidate:
    """A framework auto-generated endpoint (default no ownership) → IDOR candidate."""
    method: str
    path: str
    framework: str
    model: str | None
    vulnerability_indicators: tuple[str, ...]


def find_framework_idor_candidates(fa_path: Path) -> list[FrameworkIDORCandidate]:
    """Read framework_analysis.json (Plan 2); auto-generated endpoints are IDOR candidates.

    finale-rest/epilogue auto-generate CRUD with isAuthenticated only and no
    ownership validation by default (framework_analyzer.py:84-99). These are
    direct IDOR candidates. Manual endpoints (source="manual") are excluded —
    they're analyzed via the dominance heuristic (Task 1).

    Lenient: missing/invalid framework_analysis.json → empty list (Plan 2 not
    landed is a soft dependency).
    """
    if not fa_path.exists():
        logger.info("authz GitNexus track: framework_analysis.json missing → no framework candidates")
        return []
    try:
        data = json.loads(fa_path.read_text())
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        logger.warning("authz GitNexus track: framework_analysis.json parse failed (%s) → empty", exc)
        return []

    framework_name = ""
    fw = data.get("detected_framework")
    if isinstance(fw, dict):
        framework_name = str(fw.get("name", ""))

    out: list[FrameworkIDORCandidate] = []
    for ep in data.get("inferred_endpoints", []):
        if not isinstance(ep, dict):
            continue
        if ep.get("source") != "framework-auto-generated":
            continue
        indicators = ep.get("vulnerability_indicators", []) or []
        out.append(FrameworkIDORCandidate(
            method=str(ep.get("method", "")),
            path=str(ep.get("path", "")),
            framework=framework_name,
            model=ep.get("model"),
            vulnerability_indicators=tuple(str(i) for i in indicators),
        ))
    logger.info("authz GitNexus track: %d framework auto-generated IDOR candidates", len(out))
    return out


def _endpoint_label(func_block_id: str, entry_points: list[EntryPoint]) -> str:
    """Render an endpoint as 'METHOD /path' (fallback to handler id)."""
    for ep in entry_points:
        if ep.func_block_id == func_block_id and ep.route:
            return f"{ep.http_method or '—'} {ep.route}"
    return func_block_id


def _snippet(source: str, max_len: int = 200) -> str:
    s = source.strip().replace("\n", " ")
    return s[:max_len] + ("…" if len(s) > max_len else "")


def render_authz_gitnexus_candidates(
    dominance_cands: list[IDORCandidateChain],
    framework_cands: list[FrameworkIDORCandidate],
    *,
    index: CodeIndex,
    entry_points: list[EntryPoint],
    max_snippet: int = 200,
) -> str:
    """Render GitNexus-track IDOR candidates as markdown for the judge prompt.

    Two sections: (1) dominance candidates (handler→sink path, no ownership
    guard), (2) framework auto-generated endpoints (default no ownership).
    Plus a verdict directive telling the judge LLM to emit one
    AuthzVulnerability per candidate with externally_exploitable + reason.
    """
    if not dominance_cands and not framework_cands:
        return "（无确定性 IDOR 候选。GitNexus 索引或 framework 分析可能未就绪。）"

    blocks_by_id = {b.id: b for b in index.blocks}
    source_by_id = {sp.id: sp for sp in index.source_points}
    lines: list[str] = ["## Authz GitNexus Track — IDOR 候选链（确定性，待 LLM 判定）", ""]

    # ----- dominance candidates -----
    if dominance_cands:
        lines.append("### 1) 调用图 dominance 候选（handler→sink 无 ownership 守卫）")
        lines.append("")
        lines.append("| Endpoint | Handler | Sink | 调用路径 | Params | Handler 片段 | Sink 片段 |")
        lines.append("|---|---|---|---|---|---|---|")
        for c in dominance_cands:
            label = _endpoint_label(c.endpoint_id, entry_points)
            handler_src = _snippet(blocks_by_id[c.handler_id].source_code, max_snippet) \
                if c.handler_id in blocks_by_id else "—"
            sink_src = _snippet(blocks_by_id[c.sink_id].source_code, max_snippet) \
                if c.sink_id in blocks_by_id else "—"
            path_str = " → ".join(c.path)
            sps = [source_by_id[sid] for sid in c.source_point_ids if sid in source_by_id]
            params = "; ".join(
                f"{sp.param_name}({sp.source_type}): {sp.expression}" for sp in sps
            ) or "—"
            lines.append(
                f"| `{label}` | `{c.handler_id}` | `{c.sink_id}` | `{path_str}` "
                f"| `{params}` | `{handler_src}` | `{sink_src}` |"
            )
        lines.append("")
        lines.append(
            "> ⚠️ 这些路径的 handler 段未检出 ORM ownership 谓词（`where { userId }` 等）。"
            "守卫缺失仅为启发式（dominance 非数学证明），须你语义确认：该 sink 是否真无"
            " ownership/role 守卫，或守卫在调用路径的其它节点上。"
        )
        lines.append("")

    # ----- framework candidates -----
    if framework_cands:
        lines.append("### 2) Framework 自动生成端点（默认无 ownership validation）")
        lines.append("")
        lines.append("| Endpoint | Framework | Model | Vulnerability Indicators |")
        lines.append("|---|---|---|---|")
        for f in framework_cands:
            indicators = "; ".join(f.vulnerability_indicators) if f.vulnerability_indicators else "—"
            model = f.model or "—"
            lines.append(
                f"| `{f.method} {f.path}` | {f.framework} | {model} | {indicators} |"
            )
        lines.append("")
        lines.append(
            "> ⚠️ finale-rest/epilogue 等 ORM-to-REST 框架默认 isAuthenticated 但无 ownership。"
            "除非框架的 create.end/update.end/destroy.end hook 显式加了 ownership 校验，"
            "否则默认 IDOR。须你确认是否有 hook 覆盖默认行为。"
        )
        lines.append("")

    # ----- verdict directive -----
    lines.extend([
        "### 判定指令（每条候选产一条 AuthzVulnerability）",
        "",
        "对上方**每一条**候选，判 IDOR verdict：",
        "- **vulnerable**：该端点到达 side-effect sink 且**无** ownership/role 守卫 dominate 该 sink",
        "- **safe / not-exploitable**：存在覆盖所有路径的 ownership 守卫（如 hook、middleware、ORM 谓词），或 sink 非敏感资源",
        "- 输出 JSON 数组，每元素含：`endpoint`（METHOD /path）、`vulnerability_type`（Horizontal）、",
        "  `externally_exploitable`（bool）、`vulnerable_code_location`、`guard_evidence`（缺失守卫描述）、",
        "  `side_effect`、`reason`、`minimal_witness`、`confidence`（high/med/low）、`notes`",
        "- **保守**：不确定时判 vulnerable（宁过报不漏报）。",
    ])
    return "\n".join(lines)


def build_authz_gitnexus_track(
    deliverables_dir: str,
) -> AuthzTrackBuildResult:
    """Read code_index.json + framework_analysis.json, build IDOR candidates.

    Returns AuthzTrackBuildResult:
    - markdown: rendered candidates for the judge-LLM prompt (Task 5).
    - dominance_candidates / framework_candidates: raw lists for test asserts.
    - http_route_count / entry_point_total: 入口点诊断（Task 2 可观测性消费，
      spec §3.1）。

    Lenient: missing/invalid code_index.json → empty dominance candidates
    (framework candidates may still come from framework_analysis.json).
    Missing framework_analysis.json → empty framework candidates. Never raises
    (spec §6 graceful degradation: when GitNexus index is absent, only the LLM
    track runs).
    """
    out = Path(deliverables_dir)
    ci_path = out / "code_index.json"

    index: CodeIndex | None = None
    if ci_path.exists():
        try:
            index = CodeIndex.model_validate_json(ci_path.read_text())
        except Exception as exc:  # invalid JSON / schema drift
            logger.warning("authz GitNexus track: code_index.json parse failed (%s)", exc)
            index = None
    else:
        logger.info("authz GitNexus track: code_index.json missing")

    dominance_cands: list[IDORCandidateChain] = []
    if index is not None:
        dominance_cands = find_unguarded_sink_paths(index)

    framework_cands = find_framework_idor_candidates(out / "framework_analysis.json")

    # 三重过滤可观测性：各阶段存活计数（source/entry/candidate），便于定位空壳根因。
    sp_list = index.source_points if index is not None else []
    sources_total = len(sp_list)
    entry_with_sources = len({sp.entry_point_id for sp in sp_list})
    logger.info(
        "authz build: source_points=%d, entries_with_sources=%d, "
        "dominance_candidates=%d, framework_candidates=%d",
        sources_total, entry_with_sources,
        len(dominance_cands), len(framework_cands),
    )

    if index is None:
        index = CodeIndex(
            repository="", language="", total_blocks=0, total_entry_points=0,
            total_chains=0, blocks=[], edges=[], entry_points=[], chains=[],
        )
    entry_points = list(index.entry_points)

    md = render_authz_gitnexus_candidates(
        dominance_cands, framework_cands, index=index, entry_points=entry_points,
    )
    entry_point_total = len(index.entry_points)
    http_route_count = sum(
        1 for ep in index.entry_points
        if ep.entry_type == "http_route" and ep.route is not None
    )
    gn_process_count = sum(
        1 for ep in index.entry_points if ep.entry_type == "gitnexus_process"
    )
    logger.info(
        "authz GitNexus track built: %d dominance + %d framework candidates "
        "(entry points: http_route=%d, gitnexus_process=%d, total=%d)",
        len(dominance_cands), len(framework_cands),
        http_route_count, gn_process_count, entry_point_total,
    )
    return AuthzTrackBuildResult(
        markdown=md,
        dominance_candidates=dominance_cands,
        framework_candidates=framework_cands,
        http_route_count=http_route_count,
        entry_point_total=entry_point_total,
    )
