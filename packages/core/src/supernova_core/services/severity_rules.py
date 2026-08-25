"""Severity 兜底规则（spec §4）：LLM 未给 severity 时按确定性规则定档。"""
from __future__ import annotations

import re

SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
SEVERITY_ZH = {"critical": "严重", "high": "高危", "medium": "中危", "low": "低危"}
# RCE 级 sink 关键词（命令/代码执行）——命中即 critical。词边界匹配：裸子串
# "exec" 会误吞 "cursor.execute"（SQLi sink 应 high）、"eval" 误吞 "reevaluate"（F9c）。
_RCE_SINK_RE = re.compile(r"\b(?:eval|exec|system|popen|spawn|child_process)\b")

def derive_fallback_severity(vuln) -> str:
    sink = (getattr(vuln, "sink_function", None)
            or getattr(vuln, "sink_call", None) or "") or ""
    lowered = sink.lower()
    if _RCE_SINK_RE.search(lowered):
        return "critical"
    if getattr(vuln, "vulnerability_type", "") == "injection":
        return "high"
    if getattr(vuln, "externally_exploitable", False):
        return "high"
    return "medium"

def effective_severity(vuln) -> str:
    explicit = getattr(vuln, "severity", None)
    if isinstance(explicit, str) and explicit.strip().lower() in SEVERITY_ORDER:
        return explicit.strip().lower()
    return derive_fallback_severity(vuln)

def max_severity(a: str | None, b: str | None) -> str:
    ea = effective_severity_from_str(a)
    eb = effective_severity_from_str(b)
    return a if ea >= eb else b

def effective_severity_from_str(s: str | None) -> int:
    if isinstance(s, str) and s.strip().lower() in SEVERITY_ORDER:
        return SEVERITY_ORDER[s.strip().lower()]
    return 0
