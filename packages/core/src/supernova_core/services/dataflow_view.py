"""数据流视图组装器（方案 B 写时组装，spec 2026-08-20 §3/§6）。

纯函数：读 5 类 intermediate 产物 + LLM 兜底 queue，组装
``dataflow_view.json`` schema（schema_version=1）。失败由调用方（whitebox
活动）兜——本函数不抛扫描级异常；全部产物缺 → 返回 None（不产文件）。

聚合规则（spec §3）与辅助函数对照：
1. 树粒度=sink —— ``_build_taint_trees``：GitNexus 枝按 ``sink_call_site_id``
   精确聚合；chain_verdicts 的 safe 枝同样进树（safe-only 树 ``findings: []``）。
2. LLM finding 挂树 —— 取 ``dataflow_steps`` 末节点为 sink 位，按
   (vuln_class, sink file:line 规范化) 与 GitNexus sink 对齐。location 规范化
   复用 ``dual_track_merger._finding_key`` 思路（多字段/多层匹配，非严格
   file:line）：① sink 位置精确（含 basename 兜底）→ ② LLM **终点位**落在
   GN 枝中间节点集（索引剔 source——source 是共享入口；只比终点、不比中间
   节点，sink 位不同的树绝不因中间节点共享而合并，Fix round 1）→ ③
   merge_source="both" 且该类 GitNexus 树唯一；全对不上则自立 ``track=llm``
   树（sink 只有位置无 rule_id）。挂靠枝保留终点步进 nodes（树 sink 是 GN
   侧位置，前端画枝条终点需要 LLM 自己的终点位）。LLM 枝 verdict 取
   finding.verdict（缺/非法才 unknown）。LLM safe 枝来自 safe_vectors：
   ``_attach_safe_vectors`` 匹配 sink 树挂单节点 safe 枝，匹配不上进顶层
   ``_collect_safe_vectors_top`` 区。
3. 代码片段体积控制 —— ``_code_snippet`` 从 code_index.blocks 按
   code_location 截 ±5 行（≤10 行）；只给有故事的节点存 code
   （source/sink/transformation 非空/sanitizer 所在步），纯透传步进 nodes 但
   ``has_code:false``；LLM 枝节点无源码 → ``has_code:false``。
4. 二阶链（``2ND-GN-*``）—— 挂 read-side sink 树，``source.type="storage"``
   （write 侧 file:line 并入 source.label）。
5. auth/authz —— ``_build_control_findings`` 从 exploitation_queue 取
   endpoint/guard_evidence/missing_defense/mismatch_reason +
   vulnerable_code_location 组关卡链（chain[].status ∈ {ok,missing,ineffective}）。

降级矩阵（spec §6）：parameter_graph 缺 → GitNexus 枝保留无中间节点；
code_index 缺/纯透传 → ``has_code:false``；LLM finding 无 steps → source→sink
直连枝；全部产物缺 → None。
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from supernova_core.utils.paths import resolve_intermediate

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
_TAINT_CLASSES = ("injection", "xss", "ssrf")
_CONTROL_CLASSES = ("auth", "authz")
_VULN_CLASSES = _TAINT_CLASSES + _CONTROL_CLASSES
_VALID_VERDICTS = ("vulnerable", "safe")
_SNIPPET_RADIUS = 5     # ±5 行
_SNIPPET_MAX_LINES = 10  # ≤10 行/节点
# 2ND finding combined_sources 形如 "write:w.py:7 (users.bio) + read:r.js:3"
_WRITE_LOC_RE = re.compile(r"write:(\S+?):(\d+)")


# ---------------------------------------------------------------------------
# 产物读取
# ---------------------------------------------------------------------------

def _load_json(whitebox_dir: Path, name: str) -> dict | None:
    """经 resolve_intermediate 读 intermediate 产物；缺失/损坏 → None 降级。"""
    path = resolve_intermediate(Path(whitebox_dir), name)
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("dataflow_view: %s 解析失败，按缺失降级（%s）", name, exc)
        return None
    return data if isinstance(data, dict) else None


def _queue_findings(whitebox_dir: Path, vc: str) -> list[dict]:
    """{vc}_exploitation_queue.json（SSOT 合并 queue），兜底 {vc}_llm_queue.json。"""
    queue = (_load_json(whitebox_dir, f"{vc}_exploitation_queue.json")
             or _load_json(whitebox_dir, f"{vc}_llm_queue.json")
             or {})
    items = queue.get("vulnerabilities")
    if not isinstance(items, list):
        return []
    return [f for f in items if isinstance(f, dict)]


def _verdict_rows(whitebox_dir: Path, vc: str) -> list[dict]:
    """{vc}_chain_verdicts.json 的 verdicts 行（safe 链也进）。"""
    payload = _load_json(whitebox_dir, f"{vc}_chain_verdicts.json") or {}
    rows = payload.get("verdicts")
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


# ---------------------------------------------------------------------------
# location 规范化（复用 dual_track_merger._finding_key 的多字段思路，自实现简化版）
# ---------------------------------------------------------------------------

def _norm_path(p: object) -> str | None:
    """file path 规范化：统一分隔符、去 "./" 前缀——跨轨 file:line 对齐用。"""
    if not isinstance(p, str):
        return None
    p = p.replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    return p or None


def _parse_loc(s: object) -> tuple[str | None, int | None]:
    """"file:line" → (file, line)；无行号 → (原文, None)；非 str → (None, None)。"""
    if not isinstance(s, str) or not s.strip():
        return (None, None)
    s = s.strip()
    file_part, _, line_part = s.rpartition(":")
    if file_part and line_part.isdigit():
        return (file_part, int(line_part))
    return (s, None)


def _loc_key(file_: object, line: object) -> tuple[str, int] | None:
    """(规范化 file, line) 对齐键；line 非 int → None（无法按行对齐）。"""
    norm = _norm_path(file_)
    if norm is None or not isinstance(line, int) or isinstance(line, bool):
        return None
    return (norm, line)


def _basename(p: object) -> str | None:
    norm = _norm_path(p)
    if norm is None:
        return None
    return norm.rsplit("/", 1)[-1]


# ---------------------------------------------------------------------------
# code_index 索引 / 代码片段（规则 3：体积控制）
# ---------------------------------------------------------------------------

def _index_blocks(code_index: dict) -> dict[str, list[dict]]:
    """blocks 按 file_path 分桶（按 start_line 排序）——snippet 查找用。"""
    buckets: dict[str, list[dict]] = {}
    for b in code_index.get("blocks") or []:
        if not isinstance(b, dict):
            continue
        fp = _norm_path(b.get("file_path"))
        if fp:
            buckets.setdefault(fp, []).append(b)
    for blocks in buckets.values():
        blocks.sort(key=lambda b: b.get("start_line") or 0)
    return buckets


def _block_at(blocks_by_file: dict[str, list[dict]], file_: object, line: object) -> dict | None:
    """找覆盖 (file, line) 的 FuncBlock；line 缺失时取该文件首个 block。"""
    norm = _norm_path(file_)
    blocks = blocks_by_file.get(norm) if norm else None
    if not blocks:
        return None
    if not isinstance(line, int):
        return blocks[0]
    for b in blocks:
        start, end = b.get("start_line"), b.get("end_line")
        if isinstance(start, int) and isinstance(end, int) and start <= line <= end:
            return b
    return None


def _code_snippet(blocks_by_file: dict[str, list[dict]], file_: object, line: object) -> str | None:
    """±5 行（≤10 行）代码片段；找不到覆盖 block → None（has_code:false 降级）。"""
    if not isinstance(line, int):
        return None
    block = _block_at(blocks_by_file, file_, line)
    if block is None:
        return None
    start, end = block.get("start_line"), block.get("end_line")
    if not isinstance(start, int) or not isinstance(end, int):
        return None
    lo = max(start, line - _SNIPPET_RADIUS)
    hi = min(end, line + _SNIPPET_RADIUS)
    if hi - lo + 1 > _SNIPPET_MAX_LINES:
        hi = lo + _SNIPPET_MAX_LINES - 1
    lines = (block.get("source_code") or "").splitlines()
    seg = lines[lo - start: hi - start + 1]
    return "\n".join(seg) if seg else None


def _func_label(blocks_by_file: dict[str, list[dict]], step: dict, fallback: object) -> str | None:
    """节点 func：优先 code_location 所在 block 的 function_name，兜底 func id。"""
    file_, line = _parse_loc(step.get("code_location"))
    block = _block_at(blocks_by_file, file_, line) if file_ else None
    if block is not None and block.get("function_name"):
        return block["function_name"]
    return (step.get("to_func_id") or step.get("from_func_id") or fallback or None)


def _parse_sink_id(sink_id: str) -> tuple[str | None, int | None, str | None]:
    """SinkCallSite.id "{file}:{caller}:{callee}:{line}:{col}" → (file, line, callee)。

    解析不出（LLM 合成/异常 id）→ (None, None, None)，调用方保持 sink_id 兜底。
    """
    parts = sink_id.split(":")
    if len(parts) >= 5 and parts[3].isdigit():
        return (parts[0], int(parts[3]), parts[2])
    if len(parts) >= 4 and parts[-2].isdigit():
        return (parts[0], int(parts[-2]), parts[-3])
    return (None, None, None)


def _sink_label(sink_meta: dict, sink_id: str) -> str | None:
    """sink label：receiver.callee（如 cursor.execute）；meta 缺则从 id 解 callee。"""
    callee = sink_meta.get("callee_name")
    receiver = sink_meta.get("callee_receiver")
    if callee:
        return f"{receiver}.{callee}" if receiver else callee
    _, _, parsed = _parse_sink_id(sink_id)
    return parsed or sink_id


# ---------------------------------------------------------------------------
# taint 树构建（规则 1/2/4 + §6 降级）
# ---------------------------------------------------------------------------

def _sanitizer_from_annotation(ann: object, branch_verdict: str | None) -> dict:
    """SanitizerAnnotation（dict 或遗留 dataclass）→ schema sanitizers 元素。

    effective 语义（本组装器解释，spec 未细化）：枝 verdict=safe → 防护有效；
    vulnerable → 无效（被绕过/未覆盖）；unknown → None（未判定）。
    """
    if isinstance(ann, dict):
        name = ann.get("matched_text") or ann.get("rule_id")
        defense_type = ann.get("defense_type")
        file_, line = _parse_loc(ann.get("code_location"))
    else:
        name = getattr(ann, "matched_text", None) or getattr(ann, "rule_id", None)
        defense_type = getattr(ann, "defense_type", None)
        file_, line = _parse_loc(getattr(ann, "code_location", None))
    if branch_verdict == "safe":
        effective = True
    elif branch_verdict == "vulnerable":
        effective = False
    else:
        effective = None
    return {"name": name, "defense_type": defense_type,
            "file": file_, "line": line, "effective": effective}


def _normalize_verdict(raw: object) -> str:
    v = str(raw or "").strip().lower()
    return v if v in _VALID_VERDICTS else "unknown"


def _flow_steps(flow: dict | None) -> list[dict]:
    steps = (flow or {}).get("propagation_steps")
    if not isinstance(steps, list):
        return []
    return [s for s in steps if isinstance(s, dict)]


def _source_from_flow(
    flow: dict | None,
    source_points: list[dict],
    entry_labels: dict[str, str],
) -> dict:
    """GitNexus 枝 source：flow + source_points + entry_points 联查。"""
    empty = {"label": None, "type": None, "entry": None, "file": None, "line": None}
    if not flow:
        return empty
    entry_id = flow.get("entry_point_id")
    sp = next((sp for sp in source_points
               if sp.get("entry_point_id") == entry_id
               and sp.get("param_name") == flow.get("source_param")), None)
    return {
        "label": (sp or {}).get("expression") or flow.get("source_param"),
        "type": flow.get("source_type"),
        "entry": entry_labels.get(entry_id) or entry_id,
        "file": (sp or {}).get("file_path"),
        "line": (sp or {}).get("line"),
    }


def _nodes_from_steps(
    steps: list[dict],
    *,
    sanitizer_locs: set[tuple[str | None, int | None]],
    blocks_by_file: dict[str, list[dict]],
) -> list[dict]:
    """propagation_steps → 中间节点（规则 3 体积控制：有故事才存 code）。

    有故事 = transformation 非空 或 sanitizer 所在步；纯透传步只存位置。
    """
    nodes = []
    for step in steps:
        file_, line = _parse_loc(step.get("code_location"))
        story = bool(step.get("transformation")) or (file_, line) in sanitizer_locs
        snippet = _code_snippet(blocks_by_file, file_, line) if story else None
        nodes.append({
            "func": _func_label(blocks_by_file, step, None),
            "file": file_,
            "line": line,
            "transformation": step.get("transformation") or None,
            "intermediate_vars": list(step.get("intermediate_vars") or []),
            "code": snippet,
            "has_code": snippet is not None,
        })
    return nodes


def _annotation_locs(annotations: list) -> set[tuple[str | None, int | None]]:
    locs = set()
    for ann in annotations or []:
        if isinstance(ann, dict):
            locs.add(_parse_loc(ann.get("code_location")))
        else:
            locs.add(_parse_loc(getattr(ann, "code_location", None)))
    return locs


def _finding_view(f: dict) -> dict:
    """queue finding → tree.findings 元素（spec §3 findings 字段）。"""
    return {
        "id": f.get("ID"),
        "merge_source": f.get("merge_source"),
        "title": f.get("title"),
        "confidence": f.get("confidence"),
        "witness_payload": f.get("witness_payload"),
        "mismatch_reason": f.get("mismatch_reason") or f.get("missing_defense"),
    }


def _llm_entry_hint(f: dict) -> str | None:
    return (f.get("endpoint") or f.get("source_endpoint")
            or f.get("accessible_routes") or None)


def _llm_source(f: dict, first_step: dict | None) -> dict:
    """LLM 枝 source：优先 dataflow_steps 首节点，兜底 finding 自述字段。"""
    if first_step:
        return {
            "label": first_step.get("label"),
            "type": None,
            "entry": _llm_entry_hint(f),
            "file": first_step.get("file"),
            "line": first_step.get("line"),
        }
    return {
        "label": (f.get("source") or f.get("vulnerable_parameter") or None),
        "type": None,
        "entry": _llm_entry_hint(f),
        "file": None,
        "line": None,
    }


def _llm_branch(f: dict, steps: list[dict], *, keep_terminal: bool = False) -> dict:
    """LLM finding → 枝：source 单列 + 中间节点 +（可选）终点节点。

    - 挂靠进 GN 树（keep_terminal=True）：终点步保留进 nodes——树 sink 是
      GN 侧位置，前端画枝条终点需要 LLM 自己的终点位（Fix round 1 修复项 2）。
    - 自立 llm 树（默认）：终点即树 sink（tree.sink），nodes 不重复存。
    - steps<2：source 取 finding 自述字段（source→sink 直连降级）；keep_terminal
      时单步（终点）进 nodes。

    LLM 枝节点无源码 → has_code:false（spec §3 规则 3）。verdict 取
    finding.verdict（缺/非法才 unknown——LLM 只交 vulnerable finding，
    finding 自带判定；Fix round 1 修复项 3）。steps[].protection 非空视为
    该步有防护 → sanitizers（本组装器对 spec 的补全解释）。
    """
    sink_step = steps[-1] if steps else None
    first_step = steps[0] if len(steps) >= 2 else None
    if keep_terminal:
        node_steps = steps[1:] if len(steps) >= 2 else steps
    else:
        node_steps = steps[1:-1]
    sanitizers = [
        {"name": st.get("protection"), "defense_type": None,
         "file": st.get("file"), "line": st.get("line"), "effective": None}
        for st in steps if st.get("protection")
    ]
    nodes = [{
        "func": st.get("label"),
        "file": st.get("file"),
        "line": st.get("line"),
        "transformation": None,
        "intermediate_vars": [],
        "code": None,
        "has_code": False,
    } for st in node_steps]
    return {
        "branch_id": f.get("ID"),
        "track": "llm",
        "verdict": _normalize_verdict(f.get("verdict")),
        "verdict_reason": f.get("mismatch_reason") or f.get("missing_defense") or None,
        "source": _llm_source(f, first_step),
        "nodes": nodes,
        "sanitizers": sanitizers,
    }


def _second_order_branch(
    f: dict,
    flows: dict[str, dict],
    blocks_by_file: dict[str, list[dict]],
) -> dict:
    """规则 4：2ND-GN-* 枝挂 read-side sink 树，source.type="storage"。

    write 侧 file:line 从 combined_sources 的 "write:file:line" 段解析，
    并入 source.label（spec §3 规则 4 原文语义）；read 侧传播步照常成节点。
    """
    flow = flows.get(f.get("flow_id") or "")
    wfile, wline = None, None
    m = _WRITE_LOC_RE.search(f.get("combined_sources") or "")
    if m:
        wfile, wline = m.group(1), int(m.group(2))
    label = f.get("source") or "stored data"
    if wfile:
        label = f"{label} (write {wfile}:{wline})"
    annotations = f.get("sanitizer_annotations") or []
    verdict = _normalize_verdict(f.get("verdict"))
    nodes = _nodes_from_steps(
        _flow_steps(flow), sanitizer_locs=_annotation_locs(annotations),
        blocks_by_file=blocks_by_file)
    return {
        "branch_id": f.get("ID"),
        "track": "gitnexus",
        "verdict": verdict,
        "verdict_reason": f.get("mismatch_reason") or f.get("missing_defense") or None,
        "source": {
            "label": label,
            "type": "storage",
            "entry": None,
            "file": wfile,
            "line": wline,
        },
        "nodes": nodes,
        "sanitizers": [_sanitizer_from_annotation(a, verdict) for a in annotations],
    }


def _build_taint_trees(
    vc: str,
    findings: list[dict],
    verdict_rows: list[dict],
    flows: dict[str, dict],
    sinks: dict[str, dict],
    source_points: list[dict],
    entry_labels: dict[str, str],
    blocks_by_file: dict[str, list[dict]],
) -> list[dict]:
    """一个 taint 类的树集（规则 1：树粒度=sink；safe 枝也进树）。"""
    trees: list[dict] = []
    by_sink: dict[str, dict] = {}
    sink_key_index: dict[tuple, dict] = {}   # (norm file, line) → tree（sink 对齐）
    loc_key_index: dict[tuple, dict] = {}    # (norm file, line) → tree（枝路径对齐）

    def _register_sink_keys(tree: dict) -> None:
        file_, line = tree["sink"].get("file"), tree["sink"].get("line")
        norm = _norm_path(file_)
        if norm and isinstance(line, int):
            sink_key_index.setdefault((norm, line), tree)
            sink_key_index.setdefault((_basename(file_), line), tree)

    def _ensure_tree(sink_id: str) -> dict:
        if sink_id in by_sink:
            return by_sink[sink_id]
        meta = sinks.get(sink_id) or {}
        file_ = meta.get("file_path")
        line = meta.get("line")
        if not file_:
            pf, pl, _ = _parse_sink_id(sink_id)
            file_, line = file_ or pf, line or pl
        tree = {
            "tree_id": sink_id,
            "vuln_class": vc,
            "sink": {
                "label": _sink_label(meta, sink_id),
                "file": file_,
                "line": line,
                "rule_id": meta.get("rule_id"),
                "category": meta.get("category"),
                "code": _code_snippet(blocks_by_file, file_, line),
            },
            "findings": [],
            "branches": [],
        }
        trees.append(tree)
        by_sink[sink_id] = tree
        _register_sink_keys(tree)
        return tree

    def _register_branch_locs(tree: dict, branch: dict) -> None:
        """枝**中间节点**位注册进重叠索引（layer 2 判据）——**剔除 source**。

        source 是入口位，多树共享属正常（spec §5 跨树 source 提示：同一入口
        流向多个 sink），拿它当重叠判据会把 sink 不同的树连起来；sink 位由
        layer 1 精确比对，不入本索引。（Fix round 1 修复项 1）
        """
        for loc in branch["nodes"]:
            key = _loc_key(loc.get("file"), loc.get("line"))
            if key is None:
                continue
            loc_key_index.setdefault(key, tree)
            loc_key_index.setdefault((_basename(loc.get("file")), key[1]), tree)

    # --- GitNexus 枝：chain_verdicts（safe 链也进） ---
    seen_rows: set[tuple] = set()
    for row in verdict_rows:
        flow_id = row.get("flow_id") or ""
        sink_id = (row.get("sink_call_site_id")
                   or (flows.get(flow_id) or {}).get("sink_call_site_id") or "")
        if not sink_id:
            logger.debug("dataflow_view: verdict 行缺 sink 锚点，跳过（vc=%s flow=%s）", vc, flow_id)
            continue
        dedup = (flow_id, row.get("verdict"), row.get("reason"))
        if dedup in seen_rows:
            continue
        seen_rows.add(dedup)
        verdict = _normalize_verdict(row.get("verdict"))
        flow = flows.get(flow_id) if flow_id else None
        tree = _ensure_tree(sink_id)
        annotations = row.get("sanitizer_annotations") or []
        branch = {
            "branch_id": flow_id or f"{sink_id}#{len(seen_rows)}",
            "track": "gitnexus",
            "verdict": verdict,
            "verdict_reason": row.get("reason") or None,
            "source": _source_from_flow(flow, source_points, entry_labels),
            "nodes": _nodes_from_steps(
                _flow_steps(flow), sanitizer_locs=_annotation_locs(annotations),
                blocks_by_file=blocks_by_file),
            "sanitizers": [_sanitizer_from_annotation(a, verdict) for a in annotations],
        }
        tree["branches"].append(branch)
        _register_branch_locs(tree, branch)

    # --- findings 挂树（GitNexus-track / 2ND / LLM） ---
    for f in findings:
        is_2nd = str(f.get("ID") or "").startswith("2ND-GN-")
        if f.get("source_track") == "gitnexus" or is_2nd:
            flow = flows.get(f.get("flow_id") or "")
            sink_id = ((flow or {}).get("sink_call_site_id")
                       or f.get("sink_call") or f.get("vulnerable_code_location") or "")
            if not sink_id:
                continue
            tree = _ensure_tree(sink_id)
            tree["findings"].append(_finding_view(f))
            if is_2nd:
                tree["branches"].append(_second_order_branch(f, flows, blocks_by_file))
            elif not any(b.get("track") == "gitnexus" and b.get("branch_id") == f.get("flow_id")
                         for b in tree["branches"]):
                # gitnexus queue 有 finding 但 chain_verdicts 缺该 flow → unknown 枝
                verdict = _normalize_verdict(f.get("verdict"))
                annotations = f.get("sanitizer_annotations") or []
                branch = {
                    "branch_id": f.get("flow_id") or f.get("ID"),
                    "track": "gitnexus",
                    "verdict": verdict,
                    "verdict_reason": f.get("mismatch_reason") or f.get("missing_defense") or None,
                    "source": _source_from_flow(flow, source_points, entry_labels),
                    "nodes": _nodes_from_steps(
                        _flow_steps(flow), sanitizer_locs=_annotation_locs(annotations),
                        blocks_by_file=blocks_by_file),
                    "sanitizers": [_sanitizer_from_annotation(a, verdict) for a in annotations],
                }
                tree["branches"].append(branch)
                _register_branch_locs(tree, branch)
            continue

        # --- LLM finding 挂树（规则 2）---
        steps = f.get("dataflow_steps")
        steps = [s for s in steps if isinstance(s, dict)] if isinstance(steps, list) else []
        sink_step = steps[-1] if steps else None
        tree = None
        if sink_step is not None:
            key = _loc_key(sink_step.get("file"), sink_step.get("line"))
            if key is not None:
                tree = (sink_key_index.get(key)
                        or sink_key_index.get((_basename(sink_step.get("file")), key[1])))
        if tree is None and sink_step is not None:
            # layer 2：LLM **终点位**落在某 GN 枝中间节点集（索引已剔 source）
            # → 视为同一数据流（LLM 对 sink 归属常不精确）。只比终点位、绝不
            # 比中间节点——sink 位不同的树不因中间节点共享而合并（Fix round 1
            # 修复项 1）。
            key = _loc_key(sink_step.get("file"), sink_step.get("line"))
            if key is not None:
                tree = (loc_key_index.get(key)
                        or loc_key_index.get((_basename(sink_step.get("file")), key[1])))
        if tree is None and f.get("merge_source") == "both":
            # merger 已判 both（同一漏洞两轨各述）但位置对不上：该类 GitNexus
            # 树唯一时可安全挂靠，多树无法消歧 → 自立（保守）。
            gn_trees = [t for t in trees
                        if any(b.get("track") == "gitnexus" for b in t["branches"])]
            if len(gn_trees) == 1:
                tree = gn_trees[0]
        if tree is not None:
            tree["branches"].append(_llm_branch(f, steps, keep_terminal=True))
            tree["findings"].append(_finding_view(f))
        else:
            # 自立 track=llm 树：sink 只有位置无 rule_id（spec §3 规则 2）
            if sink_step is not None:
                s_file, s_line = sink_step.get("file"), sink_step.get("line")
                s_label = sink_step.get("label")
            else:
                s_file, s_line = _parse_loc(f.get("vulnerable_code_location"))
                s_label = None
            tree = {
                "tree_id": f"llm:{f.get('ID') or len(trees) + 1}",
                "vuln_class": vc,
                "sink": {
                    "label": s_label or f.get("sink_call") or f.get("sink_function"),
                    "file": s_file,
                    "line": s_line,
                    "rule_id": None,
                    "category": None,
                    "code": None,
                },
                "findings": [_finding_view(f)],
                "branches": [_llm_branch(f, steps)],
            }
            trees.append(tree)
    return trees


# ---------------------------------------------------------------------------
# safe_vectors（规则 2 后半：匹配挂枝 / 顶层区）
# ---------------------------------------------------------------------------

def _attach_safe_vectors(trees: list[dict], sv_payload: dict, vc: str) -> list[dict]:
    """匹配 sink 树（同 vc，规范化 file:line）→ 挂单节点 safe 枝；返回未匹配向量。"""
    vectors = sv_payload.get("vectors") if isinstance(sv_payload, dict) else None
    if not isinstance(vectors, list):
        return []
    sink_index: dict[tuple, dict] = {}
    for t in trees:
        if t.get("vuln_class") != vc:
            continue
        key = _loc_key(t["sink"].get("file"), t["sink"].get("line"))
        if key:
            sink_index.setdefault(key, t)
            sink_index.setdefault((_basename(t["sink"].get("file")), key[1]), t)
    unmatched: list[dict] = []
    for v in vectors:
        if not isinstance(v, dict):
            continue
        vf, vl = _parse_loc(v.get("location"))
        tree = None
        key = _loc_key(vf, vl)
        if key is not None:
            tree = sink_index.get(key) or sink_index.get((_basename(vf), key[1]))
        if tree is None:
            unmatched.append(v)
            continue
        tree["branches"].append({
            "branch_id": f"safe:{v.get('subject')}@{v.get('location')}",
            "track": "llm",
            "verdict": "safe",
            "verdict_reason": v.get("defense_mechanism"),
            "source": {"label": v.get("subject"), "type": None, "entry": None,
                       "file": vf, "line": vl},
            "nodes": [],
            "sanitizers": [{
                "name": v.get("defense_mechanism"), "defense_type": None,
                "file": vf, "line": vl, "effective": True,
            }],
        })
    return unmatched


def _collect_safe_vectors_top(unmatched: list[dict]) -> list[dict]:
    """顶层 safe_vectors 区：未匹配到 sink 树的 LLM 安全向量（去重、按 spec 字段）。"""
    out: list[dict] = []
    seen: set[tuple] = set()
    for v in unmatched:
        key = (v.get("subject"), v.get("location"))
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "subject": v.get("subject"),
            "location": v.get("location"),
            "defense_mechanism": v.get("defense_mechanism"),
            "render_context": v.get("render_context"),
        })
    return out


# ---------------------------------------------------------------------------
# auth/authz 关卡链（规则 5：control_findings，非树）
# ---------------------------------------------------------------------------

def _build_control_findings(vc: str, findings: list[dict]) -> list[dict]:
    """auth/authz finding → 防护位关卡链（status ∈ {ok,missing,ineffective}）。

    单 finding 只能组出它指控的那个防护位：missing_defense 非空 → missing
    （缺防）；否则 guard_evidence 指证防护存在但被绕过/不足 → ineffective。
    detail 保留 guard_evidence 原文（spec §3 示例语义）。
    """
    out: list[dict] = []
    for f in findings:
        file_, line = _parse_loc(f.get("vulnerable_code_location"))
        missing = f.get("missing_defense")
        guard = f.get("guard_evidence")
        status = "missing" if missing else "ineffective"
        label = missing or guard or f"{vc} guard"
        detail = (guard or f.get("mismatch_reason") or f.get("reason")
                  or f.get("exploitation_hypothesis") or None)
        out.append({
            "id": f.get("ID"),
            "vuln_class": vc,
            "endpoint": f.get("endpoint") or f.get("source_endpoint"),
            "chain": [{
                "label": label,
                "status": status,
                "detail": detail,
                "file": file_,
                "line": line,
            }],
        })
    return out


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------

def _summarize(trees: list[dict]) -> dict:
    """total/vulnerable/safe_only：vulnerable=findings 非空；safe_only=branches
    非空但 findings 空（brief 口径）。"""
    vulnerable = sum(1 for t in trees if t.get("findings"))
    safe_only = sum(1 for t in trees if t.get("branches") and not t.get("findings"))
    return {"total_sinks": len(trees), "vulnerable_sinks": vulnerable,
            "safe_only_sinks": safe_only}


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def assemble_dataflow_view(whitebox_dir: Path) -> dict | None:
    """读 5 类 intermediate 产物组装 dataflow_view（spec §3 schema）。全缺 → None。"""
    whitebox_dir = Path(whitebox_dir)
    code_index = _load_json(whitebox_dir, "code_index.json") or {}
    pgraph = _load_json(whitebox_dir, "parameter_graph.json") or {}

    blocks_by_file = _index_blocks(code_index)
    sinks = {}
    for s in code_index.get("sink_call_sites") or []:
        if isinstance(s, dict) and s.get("id"):
            sinks[s["id"]] = s
    source_points = [sp for sp in code_index.get("source_points") or []
                     if isinstance(sp, dict)]
    entry_labels: dict[str, str] = {}
    for ep in code_index.get("entry_points") or []:
        if not isinstance(ep, dict):
            continue
        route, method = ep.get("route"), ep.get("http_method")
        label = f"{method} {route}".strip() if route and method else (route or ep.get("entry_type"))
        if ep.get("func_block_id") and label:
            entry_labels[ep["func_block_id"]] = label
    flows = {}
    for f in pgraph.get("taint_flows") or []:
        if isinstance(f, dict) and f.get("flow_id"):
            flows[f["flow_id"]] = f

    trees: list[dict] = []
    control_findings: list[dict] = []
    unmatched_safe_vectors: list[dict] = []

    for vc in _TAINT_CLASSES:
        rows = _verdict_rows(whitebox_dir, vc)
        findings = _queue_findings(whitebox_dir, vc)
        trees += _build_taint_trees(
            vc, findings, rows, flows, sinks, source_points, entry_labels,
            blocks_by_file)
        sv = _load_json(whitebox_dir, f"{vc}_safe_vectors.json")
        if sv:
            unmatched_safe_vectors += _attach_safe_vectors(trees, sv, vc)

    for vc in _CONTROL_CLASSES:
        control_findings += _build_control_findings(vc, _queue_findings(whitebox_dir, vc))

    safe_vectors_top = _collect_safe_vectors_top(unmatched_safe_vectors)

    if not trees and not control_findings and not safe_vectors_top:
        return None

    return {
        "schema_version": SCHEMA_VERSION,
        "summary": _summarize(trees),
        "trees": trees,
        "control_findings": control_findings,
        "safe_vectors": safe_vectors_top,
    }
