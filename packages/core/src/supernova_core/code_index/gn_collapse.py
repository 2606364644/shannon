# packages/core/src/supernova_core/code_index/gn_collapse.py
"""GN 轨条目按漏洞单位收敛（spec §3）：同 (vuln_class, 接口, sink 函数) 多链 →
一条主记录 + affected_entries 入口列表。不同接口绝不合并。"""
from __future__ import annotations

import re

_METHOD_PATH = re.compile(
    r"\b(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(/\S*)")

def parse_sink_call_site_id(s: str) -> tuple[str | None, str | None]:
    parts = s.split(":")
    if len(parts) < 4:
        return (None, None)
    return (parts[-3], f"{parts[0]}:{parts[-2]}")

def extract_endpoint(path_or_endpoint: str | None) -> str | None:
    if not isinstance(path_or_endpoint, str):
        return None
    m = _METHOD_PATH.search(path_or_endpoint)
    if not m:
        return None
    route = m.group(2).split("?", 1)[0].rstrip("/") or "/"
    return f"{m.group(1).upper()} {route}"

def extract_param(source: str | None) -> str | None:
    if not isinstance(source, str):
        return None
    head = source.split("(", 1)[0].strip()
    return head or None

def _unit_key(f):
    sink_func, _loc = parse_sink_call_site_id(getattr(f, "sink_call", "") or "")
    endpoint = (getattr(f, "endpoint", None)
                and extract_endpoint(f.endpoint)) or extract_endpoint(getattr(f, "path", None))
    if endpoint:
        return (getattr(f, "vulnerability_type", None), endpoint, sink_func)
    if sink_func and _loc:
        return (getattr(f, "vulnerability_type", None), _loc, sink_func)  # 文件级回退
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
            param = (extract_param(getattr(f, "source", None))
                     or (getattr(f, "affected_parameters", None) or [None])[0])
            _, loc = parse_sink_call_site_id(getattr(f, "sink_call", "") or "")
            entries.append({"parameter": param, "sink_location": loc,
                            "chain_id": getattr(f, "ID", None), "track": "gitnexus"})
            if param and param not in params:
                params.append(param)
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
    return merged
