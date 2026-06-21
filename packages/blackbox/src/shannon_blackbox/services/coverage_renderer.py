"""渲染 exploit 覆盖率闭环中"未动态验证"条目的 markdown 节。"""

from __future__ import annotations

from shannon_core.models.queue_schemas import Vulnerability, VulnerabilityQueue

from shannon_blackbox.services.exploitation_checker import CoverageResult

# vuln_class → 展示字段的 (标签, 属性名) 列表。属性名对齐 queue_schemas 各子类字段。
_CLASS_FIELDS: dict[str, list[tuple[str, str]]] = {
    "auth": [
        ("Source Endpoint", "source_endpoint"),
        ("Missing Defense", "missing_defense"),
        ("Suggested Exploit Technique", "suggested_exploit_technique"),
    ],
    "ssrf": [
        ("Source Endpoint", "source_endpoint"),
        ("Missing Defense", "missing_defense"),
        ("Suggested Exploit Technique", "suggested_exploit_technique"),
    ],
    "authz": [
        ("Endpoint", "endpoint"),
        ("Guard Evidence", "guard_evidence"),
    ],
    "injection": [
        ("Source", "source"),
        ("Path", "path"),
        ("Verdict", "verdict"),
    ],
    "xss": [
        ("Source", "source"),
        ("Path", "path"),
        ("Verdict", "verdict"),
    ],
}

# evidence 已含此标题则不再追加（幂等）。
_UNVERIFIED_HEADING = "## Unverified Findings (Not Dynamically Exploited)"


def render_unverified_section(result: CoverageResult, queue: VulnerabilityQueue) -> str:
    """生成未覆盖条目的 markdown 节。

    字段取自 queue（evidence 里没有这些条目的内容）。
    per-class 字段由 _CLASS_FIELDS 决定；缺失字段优雅跳过。
    """
    by_id: dict[str, Vulnerability] = {v.ID: v for v in queue.vulnerabilities}
    fields = _CLASS_FIELDS.get(result.vuln_class, [])
    lines: list[str] = [
        "",
        "---",
        "",
        _UNVERIFIED_HEADING,
        "",
        f"> 以下 {len(result.uncovered_ids)} 条漏洞经静态分析识别（见 "
        f"`{result.vuln_class}_exploitation_queue.json`），但 exploit 阶段未对其产出"
        "动态验证证据。可能原因：exploit 预算/轮次上限、agent 未穷尽、或条目被合并。"
        "**请人工复核。**",
        "",
    ]
    for vid in sorted(result.uncovered_ids):
        v = by_id.get(vid)
        lines.append(f"### {vid}")
        if v is not None:
            for label, attr in fields:
                val = getattr(v, attr, None)
                if val:
                    lines.append(f"- **{label}:** {val}")
        lines.append("")
    return "\n".join(lines)
