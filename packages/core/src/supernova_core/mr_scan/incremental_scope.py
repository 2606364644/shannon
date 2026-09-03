"""三来源增量范围合成（spec 2026-09-03 §4.2-4.5）。

输入：DiffManifest（git 派生）+ head 索引（CodeIndex）+ 污点图（pgraph）
     + 删防护判定产物（RemovedProtection[]，LLM，可缺席=降级）。
输出：IncrementalScope——GitNexus 轨 verdict 候选过滤集 + 报告呈现用的来源明细。

来源 A（§4.2）：新增代码引入漏洞——sink / 传播步 / source_point 落 added 行。
来源 B（§4.3）：新增入口点 = 新攻击面，其全部链路。
来源 C（§4.4）：删防护 → 函数定位 → 直接级∪扩展级反向链。
"""

import re

from pydantic import BaseModel

from supernova_core.code_index.models import CodeIndex
from supernova_core.code_index.parameter_models import ParameterPropagationGraph
from supernova_core.mr_scan.diff_manifest import DiffManifest

ALL_VULN_CLASSES = ["injection", "xss", "ssrf", "authz", "auth"]

# --- §4.5 vuln 类启发式（确定性小表，非 prompt）---

_FRONTEND_FILE = re.compile(
    r"\.(tsx|jsx|vue|svelte|html)$|(^|/)(components?|pages?|views?|templates?)/",
    re.IGNORECASE,
)
_AUTH_PATH = re.compile(r"(^|/)(auth|login|session|middleware)s?/|\b(permission|role)s?\b",
                        re.IGNORECASE)
_BACKEND_FILE = re.compile(
    r"\.(py|go|java|php|rb|ts|js)$", re.IGNORECASE,
)
_ROUTE_HINT = re.compile(
    r"(^|/)(routes?|controllers?|handlers?|api|endpoints?|servlets?)/", re.IGNORECASE,
)


def select_vuln_classes(diff: DiffManifest) -> list[str]:
    """按 diff 触及的文件特征选 vuln 类（并集，按 ALL_VULN_CLASSES 顺序）；无信号 → 全类。"""
    selected: set[str] = set()
    for hunk in diff.hunks:
        path = hunk.file_path
        if _AUTH_PATH.search(path):
            selected |= {"auth", "authz"}
        elif _ROUTE_HINT.search(path) or _BACKEND_FILE.search(path):
            selected |= {"injection", "ssrf", "authz"}
        elif _FRONTEND_FILE.search(path):
            selected |= {"xss"}
    if not selected:
        return list(ALL_VULN_CLASSES)
    return [c for c in ALL_VULN_CLASSES if c in selected]


class RemovedProtection(BaseModel):
    """diff 删除行中被 LLM 判定为安全防护的条目（spec §4.1）。"""

    file_path: str                        # LLM 报出的路径（base 或 head 侧，经 rename 归一）
    base_line_no: int
    removed_text: str
    function_name: str | None = None      # LLM 从 hunk 上下文推断；None=推断不出
    protection_kind: str = ""             # sanitize / authz_check / input_validation / ...
    rationale: str = ""
    confidence: float = 0.0


class RemovedProtectionFlow(BaseModel):
    """来源 C 单条：被删防护 → 定位到的 head 函数 → 关联 flow 集。"""

    protection: RemovedProtection
    func_block_id: str | None = None      # None=函数被整体删除/无法定位，不追链
    flow_ids: list[str] = []


class IncrementalScope(BaseModel):
    selected_vuln_classes: list[str] = []
    new_entry_point_ids: list[str] = []            # 来源 B：新增入口（报告呈现攻击面明细）
    verdict_flow_ids: list[str] = []               # 三来源并集（GitNexus 轨候选过滤集）
    removed_protection_flows: list[RemovedProtectionFlow] = []
    # 三来源各自明细（spec §6 报告层）：trigger_source 归并依据（C > B > A）。
    # 并集字段 verdict_flow_ids 语义不变（GN 轨过滤集），明细仅供报告反查打标。
    source_a_flow_ids: list[str] = []              # A：新增代码引入漏洞
    source_b_flow_ids: list[str] = []              # B：新增入口 = 新攻击面
    source_c_flow_ids: list[str] = []              # C：删防护反向链


def trigger_source_of(flow_id: str | None, scope: "IncrementalScope") -> str | None:
    """flow → trigger_source（spec §6）：命中来源集按 C > B > A 归并取一。

    - XSS 存储型复合 flow_id（``stored#<write>#<read>``）拆子 flow 判，
      任一子 flow 命中即标（读/写侧任一增量即该卡是增量发现）。
    - 未命中 → None（非增量 flow / LLM 轨卡不标——调用方先按 merge_source 过滤）。
    """
    if not flow_id:
        return None
    parts = flow_id.split("#") if "#" in flow_id else [flow_id]
    for ids, label in (
        (scope.source_c_flow_ids, "removed_protection"),
        (scope.source_b_flow_ids, "new_entry"),
        (scope.source_a_flow_ids, "new_code"),
    ):
        wanted = set(ids)
        if any(p in wanted for p in parts):
            return label
    return None


def _line_added(diff: DiffManifest, file_path: str, line: int | None) -> bool:
    if line is None:
        return False
    return line in diff.added_line_set(file_path)


def _location_added(diff: DiffManifest, code_location: str) -> bool:
    """"{file}:{line}" 落 added 行（rsplit 防 Windows 盘符）。"""
    if not code_location or ":" not in code_location:
        return False
    file_part, line_part = code_location.rsplit(":", 1)
    if not line_part.isdigit():
        return False
    return _line_added(diff, file_part, int(line_part))


def _source_a_flow_ids(
    diff: DiffManifest, index: CodeIndex, pgraph: ParameterPropagationGraph,
) -> list[str]:
    sink_by_id = {s.id: s for s in index.sink_call_sites}
    # source_point 落 added 行的 entry 集合（entry 级命中）
    entries_with_added_source = {
        sp.entry_point_id for sp in index.source_points
        if _line_added(diff, sp.file_path, sp.line)
    }
    hits: list[str] = []
    for flow in pgraph.taint_flows:
        sink = sink_by_id.get(flow.sink_call_site_id)
        if sink is not None and _line_added(diff, sink.file_path, sink.line):
            hits.append(flow.flow_id)
            continue
        if any(_location_added(diff, st.code_location) for st in flow.propagation_steps):
            hits.append(flow.flow_id)
            continue
        if flow.entry_point_id in entries_with_added_source:
            hits.append(flow.flow_id)
    return hits


def _new_entry_point_ids(diff: DiffManifest, index: CodeIndex) -> list[str]:
    """来源 B（§4.3）：func_block 行范围与 added 相交、或所在文件为新文件的 entry。

    新入口的一切链路都是增量攻击面——不判定 base 侧可达性（轻量单索引语义）。
    """
    new_files = {h.file_path for h in diff.hunks if h.is_new_file}
    out: list[str] = []
    for entry in index.entry_points:
        block = next((b for b in index.blocks if b.id == entry.func_block_id), None)
        if block is None:
            continue
        if block.file_path in new_files:
            out.append(entry.func_block_id)
            continue
        added = diff.added_line_set(block.file_path)
        if any(block.start_line <= n <= block.end_line for n in added):
            out.append(entry.func_block_id)
    return out


def _flows_of_entries(pgraph: ParameterPropagationGraph, entry_ids: list[str]) -> list[str]:
    wanted = set(entry_ids)
    return [f.flow_id for f in pgraph.taint_flows if f.entry_point_id in wanted]


def _map_base_line_to_head(diff: DiffManifest, base_line_no: int) -> int:
    """base 行号 → head 行号（spec §4.4 线性区间映射；未变更区域行号不变）。"""
    for h in diff.hunks:
        if h.old_lines > 0 and h.old_start <= base_line_no < h.old_start + h.old_lines:
            return base_line_no + (h.new_start - h.old_start)
    return base_line_no


def _locate_protection_block(
    diff: DiffManifest, index: CodeIndex, protection: RemovedProtection,
) -> str | None:
    """被删防护 → head FuncBlock.id：函数名精确匹配优先，hunk 区间映射兜底。

    返回 None = 函数被整体删除/无法定位 → 不追链（spec §4.4 第 3 分支）。
    """
    head_path = diff.resolve_head_path(protection.file_path)
    if protection.function_name:
        for b in index.blocks:
            if b.file_path == head_path and b.function_name == protection.function_name:
                return b.id
    head_line = _map_base_line_to_head(diff, protection.base_line_no)
    for b in index.blocks:
        if b.file_path == head_path and b.start_line <= head_line <= b.end_line:
            return b.id
    return None


def _source_c_flows(
    diff: DiffManifest,
    index: CodeIndex,
    pgraph: ParameterPropagationGraph,
    protections: list[RemovedProtection],
) -> list[RemovedProtectionFlow]:
    """来源 C（§4.4）：直接级（传播步命中）∪ 扩展级（CallChain.path 命中 → entry 全量）。"""
    out: list[RemovedProtectionFlow] = []
    for p in protections:
        block_id = _locate_protection_block(diff, index, p)
        if block_id is None:
            out.append(RemovedProtectionFlow(protection=p, func_block_id=None, flow_ids=[]))
            continue
        direct = {
            f.flow_id for f in pgraph.taint_flows
            if any(s.from_func_id == block_id or s.to_func_id == block_id
                   for s in f.propagation_steps)
        }
        chain_entries = {c.entry_point_id for c in index.chains if block_id in c.path}
        extended = {f.flow_id for f in pgraph.taint_flows if f.entry_point_id in chain_entries}
        out.append(RemovedProtectionFlow(
            protection=p, func_block_id=block_id, flow_ids=list(direct | extended),
        ))
    return out


def build_incremental_scope(
    diff: DiffManifest,
    index: CodeIndex,
    pgraph: ParameterPropagationGraph,
    removed_protections: list[RemovedProtection] | None = None,
) -> IncrementalScope:
    """三来源合成（spec §4.2-4.5）。B/C 来源后续接入；本函数是唯一组装点。"""
    removed_protections = removed_protections or []
    # 来源 B（§4.3）：新入口 → 其全部链路
    new_entry_ids = _new_entry_point_ids(diff, index)
    a_ids = _source_a_flow_ids(diff, index, pgraph)
    b_ids = _flows_of_entries(pgraph, new_entry_ids)
    rp_flows = _source_c_flows(diff, index, pgraph, removed_protections)
    c_ids: list[str] = []
    for rp in rp_flows:
        c_ids.extend(rp.flow_ids)
    # 去重保序（并集 = GN 轨候选过滤集）
    flow_ids = list(dict.fromkeys(a_ids + b_ids + c_ids))
    return IncrementalScope(
        selected_vuln_classes=select_vuln_classes(diff),
        new_entry_point_ids=new_entry_ids,
        verdict_flow_ids=flow_ids,
        removed_protection_flows=rp_flows,
        source_a_flow_ids=list(dict.fromkeys(a_ids)),
        source_b_flow_ids=list(dict.fromkeys(b_ids)),
        source_c_flow_ids=list(dict.fromkeys(c_ids)),
    )
