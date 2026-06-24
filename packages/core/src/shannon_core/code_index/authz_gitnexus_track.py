"""vuln-authz GitNexus deterministic track (spec §5.7, ⭐ net-progress direction).

Produces IDOR candidate chains: handler→sink call paths where no ownership
guard dominates the sink. The "guard must dominate the sink" is approximated
heuristically (spec §8: not a full dominance proof) — we flag a path as an
IDOR candidate when the handler segment carries no ownership predicate (ORM
`where { userId }` / `findByOwner` etc.) AND the path reaches a side-effect
sink (DB write / ORM mutation / file / state). This is conservative: we
over-report candidates (宁过报不漏报, spec §2 principle 4) and let the LLM
chain-judgement pass (Task 4) confirm or reject each.

Ownership/auth detection reuses Plan 6's scan_endpoint_security machinery
(_OWNERSHIP_PREDICATE_RE etc.) — imported, not reimplemented.

This is the GitNexus TRACK of the dual-track merge. The LLM track (authz
agent, vuln-authz.txt) is untouched (spec principle 2: no anchoring). The
two tracks merge via Plan 3's merge_dual_track_queues (verdict OR by
endpoint).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

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


@dataclass(frozen=True)
class IDORCandidateChain:
    """A handler→sink path flagged as a potential IDOR (no ownership guard)."""
    endpoint_id: str          # EntryPoint.func_block_id of the handler
    handler_id: str           # FuncBlock.id of the handler (= endpoint_id here)
    sink_id: str              # FuncBlock.id of the side-effect sink
    path: tuple[str, ...]     # ordered FuncBlock.id list, handler→sink
    guard_nodes_on_path: tuple[str, ...]  # ownership-guard node ids on path (empty=none)


def _is_side_effect_sink(block: FuncBlock | None) -> bool:
    """True if the block performs a DB/ORM/file/state side-effect."""
    if block is None:
        return False
    if _SIDE_EFFECT_SINK_RE.search(block.source_code):
        return True
    return bool(_SIDE_EFFECT_SINK_RE.search(block.function_name + "("))


def _handler_has_ownership_guard(handler: FuncBlock) -> bool:
    """Reuse Plan 6's ownership predicate detection (in-tree hard dependency)."""
    from shannon_core.code_index.recon_gitnexus_track import _OWNERSHIP_PREDICATE_RE
    return _OWNERSHIP_PREDICATE_RE.search(handler.source_code) is not None


def find_unguarded_sink_paths(
    index: CodeIndex,
    *,
    max_paths_per_endpoint: int = 20,
) -> list[IDORCandidateChain]:
    """Find handler→sink paths lacking an ownership guard (IDOR candidates).

    Heuristic (spec §8): for each HTTP EntryPoint's handler, walk the
    CallChains rooted at it; for any chain whose tail is a side-effect sink
    AND whose handler carries no ownership predicate, emit a candidate.
    Conservative — flags any path reaching a sink without an ownership check
    in the handler, even if some OTHER path has one (dominance under-approx:
    a guard that doesn't cover every path still yields a candidate).

    Dedup by (endpoint_id, sink_id). Capped per endpoint to bound the
    judge-LLM cost.
    """
    blocks_by_id: dict[str, FuncBlock] = {b.id: b for b in index.blocks}
    http_eps = [ep for ep in index.entry_points
                if ep.entry_type == "http_route" and ep.route is not None]

    candidates: list[IDORCandidateChain] = []
    seen: set[tuple[str, str]] = set()  # (endpoint_id, sink_id)

    for ep in http_eps:
        handler = blocks_by_id.get(ep.func_block_id)
        if handler is None:
            continue  # unresolved handler — Plan 6 surfaces "unknown"; skip here
        # Dominance short-circuit: if the handler itself has an ownership
        # predicate, it dominates the sink for all paths through it — no IDOR
        # candidate from this handler (guard present at the entry).
        if _handler_has_ownership_guard(handler):
            continue
        count_for_ep = 0
        for chain in index.chains:
            if chain.entry_point_id != ep.func_block_id or not chain.path:
                continue
            sink_id = chain.path[-1]
            key = (ep.func_block_id, sink_id)
            if key in seen:
                continue
            if not _is_side_effect_sink(blocks_by_id.get(sink_id)):
                continue
            # Path reached a side-effect sink with no ownership guard in the
            # handler → IDOR candidate.
            seen.add(key)
            candidates.append(IDORCandidateChain(
                endpoint_id=ep.func_block_id,
                handler_id=ep.func_block_id,
                sink_id=sink_id,
                path=tuple(chain.path),
                guard_nodes_on_path=(),  # handler guard absent → no guards
            ))
            count_for_ep += 1
            if count_for_ep >= max_paths_per_endpoint:
                break

    logger.info(
        "authz GitNexus track: %d HTTP endpoints, %d IDOR candidate chains",
        len(http_eps), len(candidates),
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
    lines: list[str] = ["## Authz GitNexus Track — IDOR 候选链（确定性，待 LLM 判定）", ""]

    # ----- dominance candidates -----
    if dominance_cands:
        lines.append("### 1) 调用图 dominance 候选（handler→sink 无 ownership 守卫）")
        lines.append("")
        lines.append("| Endpoint | Handler | Sink | 调用路径 | Handler 片段 | Sink 片段 |")
        lines.append("|---|---|---|---|---|---|")
        for c in dominance_cands:
            label = _endpoint_label(c.endpoint_id, entry_points)
            handler_src = _snippet(blocks_by_id[c.handler_id].source_code, max_snippet) \
                if c.handler_id in blocks_by_id else "—"
            sink_src = _snippet(blocks_by_id[c.sink_id].source_code, max_snippet) \
                if c.sink_id in blocks_by_id else "—"
            path_str = " → ".join(c.path)
            lines.append(
                f"| `{label}` | `{c.handler_id}` | `{c.sink_id}` | `{path_str}` "
                f"| `{handler_src}` | `{sink_src}` |"
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
) -> tuple[str, list[IDORCandidateChain], list[FrameworkIDORCandidate]]:
    """Read code_index.json + framework_analysis.json, build IDOR candidates.

    Returns (markdown, dominance_candidates, framework_candidates):
    - markdown: rendered candidates for the judge-LLM prompt (Task 5).
    - dominance_candidates / framework_candidates: raw lists for test asserts.

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

    if index is None:
        index = CodeIndex(
            repository="", language="", total_blocks=0, total_entry_points=0,
            total_chains=0, blocks=[], edges=[], entry_points=[], chains=[],
        )
    entry_points = list(index.entry_points)

    md = render_authz_gitnexus_candidates(
        dominance_cands, framework_cands, index=index, entry_points=entry_points,
    )
    logger.info(
        "authz GitNexus track built: %d dominance + %d framework candidates",
        len(dominance_cands), len(framework_cands),
    )
    return md, dominance_cands, framework_cands
