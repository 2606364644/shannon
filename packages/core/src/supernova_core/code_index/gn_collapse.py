# packages/core/src/supernova_core/code_index/gn_collapse.py
"""GN 轨条目按漏洞单位收敛（spec §3）：同 (vuln_class, 接口, sink 函数) 多链 →
一条主记录 + affected_entries 入口列表。不同接口绝不合并。"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_METHOD_PATH = re.compile(
    r"\b(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(/\S*)")
# 剥尾标点：闭括号/逗号/分号/引号（含全角），URL 合法尾字符（. 数字）不动
_TRAILING_PUNCT_RE = re.compile(r"[),.;'\"）]+$")
_PARAM_PLACEHOLDER_RE = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")


def _normalize_placeholders(path: str) -> str:
    """路由占位符归一：:userId → {userId}（Express :param ↔ OpenAPI {param} 同义路由）。"""
    return _PARAM_PLACEHOLDER_RE.sub(r"{\1}", path)


# 漏洞类级归一（仅 key 计算用，不动卡上展示字段）。authz 原样返回——
# _finding_key 的 Horizontal endpoint-only 特判依赖其原始形态。
# 定义在此、dual_track_merger 反向 import（merger → gn_collapse 方向已存在，无环）。
_VTYPE_CLASS_MAP = {
    "CommandInjection": "injection", "RCE": "injection", "OSCommandInjection": "injection",
    "SQLi": "injection", "Eval": "injection", "NoSQL": "injection",
    "URL_Manipulation": "ssrf", "SSRF": "ssrf",
    "Reflected": "xss", "Stored": "xss",
}


def _canonical_vtype(vtype: object) -> str:
    if vtype is None:
        return None
    return _VTYPE_CLASS_MAP.get(str(vtype), str(vtype))

def _is_gn_id_parts(parts: list[str]) -> bool:
    """GN id 形态判定（Spec A：file:caller:callee:line:col，行号为整数）：
    ≥4 段且倒数第二段（行号）纯数字。LLM 轨 sink_call 富文本形（多行号枚举
    '...:32 (preTax)、:33...'、含 URL 多冒号）行号段非纯数字——不当 GN id
    解析（误解析会把 sink key 维变成行号碎片 '32'，跨轨永不相交，20260826
    实证），让 merger 走自然语言回退归一出真函数名。"""
    return len(parts) >= 4 and parts[-2].isdigit()

def parse_sink_call_site_id(s: str) -> tuple[str | None, str | None]:
    parts = s.split(":")
    if not _is_gn_id_parts(parts):
        return (None, None)
    return (parts[-3], f"{parts[0]}:{parts[-2]}")

def _sink_file(sink_call: str) -> str | None:
    """sink_call_site_id 的文件段（不含行号）——_unit_key 文件级回退用。
    与 parse_sink_call_site_id 同口径拒非 GN 形态。"""
    parts = (sink_call or "").split(":")
    return parts[0] if _is_gn_id_parts(parts) else None

def extract_endpoint(path_or_endpoint: str | None) -> str | None:
    if not isinstance(path_or_endpoint, str):
        return None
    m = _METHOD_PATH.search(path_or_endpoint)
    if not m:
        return None
    route = m.group(2).split("?", 1)[0].rstrip("/") or "/"
    route = _TRAILING_PUNCT_RE.sub("", route).rstrip("/") or "/"
    return f"{m.group(1).upper()} {route}"

def extract_param(source: str | None) -> str | None:
    if not isinstance(source, str):
        return None
    head = source.split("(", 1)[0].strip()
    return head or None

def _unit_key(f):
    sink_call = getattr(f, "sink_call", "") or ""
    sink_func, _loc = parse_sink_call_site_id(sink_call)
    endpoint = (getattr(f, "endpoint", None)
                and extract_endpoint(f.endpoint)) or extract_endpoint(getattr(f, "path", None))
    if endpoint:
        return (_canonical_vtype(getattr(f, "vulnerability_type", None)), endpoint, sink_func)
    # 文件级回退（spec 2026-08-26 §7/F1）：path 无路由前缀时（XSS 常态——
    # http_route_label join miss），按 文件+sink 函数 折叠——同文件同函数不同行
    # 是同一漏洞单元的多调用点（对齐 endpoint 分支的折叠粒度；含行号的 file:line
    # 会让每行各成一组，15 条参数×行笛卡尔积链一条不折）。跨文件绝不合并。
    sink_file = _sink_file(sink_call)
    if sink_func and sink_file:
        return (_canonical_vtype(getattr(f, "vulnerability_type", None)), sink_file, sink_func)
    return ("__strict__", id(f))

def collapse_gn_entries(findings: list) -> list:
    # 延迟 import 打破 code_index → services eager 环（fix round 1 H1）：
    # services/__init__ eager import findings_renderer，而 findings_renderer
    # 又 import 本模块（T5 回边）——顶层 import 会在 fresh collection 时
    # ImportError（partially initialized module）。
    from supernova_core.services.severity_rules import SEVERITY_ORDER, effective_severity

    groups: dict[tuple, list] = {}
    order: list[tuple] = []
    for f in findings:
        key = _unit_key(f)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(f)
    merged: list = []
    for key in order:
        group = groups[key]
        if len(group) == 1 and getattr(group[0], "affected_entries", None):
            merged.append(group[0])
            continue
        primary = group[0]
        entries = []
        params: list[str] = []
        for f in group:
            # affected_parameters 优先（builder 透传的 placement 注记原样保留，
            # 如 'preTax (body)'——PoC 参数位显式信号）；无则 source 提裸名。
            aps = [a for a in (getattr(f, "affected_parameters", None) or [])
                   if isinstance(a, str) and a.strip()]
            noted = aps[0] if aps else None
            param = extract_param(getattr(f, "source", None)) or (
                noted.rsplit(" (", 1)[0] if noted else None)
            _, loc = parse_sink_call_site_id(getattr(f, "sink_call", "") or "")
            # entries[].parameter 裸名（行内定位用）；params 列表带注记。
            entries.append({"parameter": param, "sink_location": loc,
                            "chain_id": getattr(f, "ID", None), "track": "gitnexus"})
            value = noted or param
            if value and value not in params:
                params.append(value)
        data = primary.model_dump()
        data["affected_entries"] = entries
        data["affected_parameters"] = params or None
        # path 提取不出路由时保留原 endpoint 字段（authz 无 path；覆写 None 会
        # 抹掉 Horizontal endpoint-only dedup 依赖的字段——Task 3 接线踩出）。
        data["endpoint"] = (extract_endpoint(getattr(primary, "path", None))
                            or getattr(primary, "endpoint", None))
        best = max((effective_severity(f) for f in group),
                   key=lambda s: SEVERITY_ORDER.get(s, 0))
        data["severity"] = best
        merged.append(type(primary).model_validate(data))
    # 分叉率体温计（spec 2026-09-03 §3 F5）：endpoint 组占比 vs 文件回退组——
    # 回退占比高 = http_route_label join miss 多 = endpoint 回填（F6）该上场。
    n_endpoint = sum(
        1 for key in order
        if key[0] != "__strict__" and isinstance(key[1], str)
        and _METHOD_PATH.match(key[1]))
    n_fallback = sum(
        1 for key in order
        if key[0] != "__strict__" and not (
            isinstance(key[1], str) and _METHOD_PATH.match(key[1])))
    logger.info("gn-collapse: %d groups (%d endpoint, %d file-fallback)",
                len(order), n_endpoint, n_fallback)
    return merged
