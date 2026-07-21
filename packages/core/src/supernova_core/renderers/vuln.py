"""vuln deliverable renderer(纯函数,对齐 TS services/vuln-renderer.ts::renderVulnDeliverable)。

5 class(injection / xss / auth / ssrf / authz)共用 render_vuln,按 vuln_class branching。
4 张 per-class 映射表 + 5 个 section 渲染函数。输入 data = collector.get_all() 子集
(缺键 = skipped -> placeholder,不 fail)。本模块不 import GitNexus / 确定性层(守 §1)。
"""
from __future__ import annotations

from ._helpers import as_dict, as_dict_list, as_list, placeholder, render_table

# ── per-class 映射表(对照 TS vuln-renderer.ts:26-110 逐字移植) ──────────
TITLES: dict[str, str] = {
    "injection": "Injection Analysis Report",
    "xss": "Cross-Site Scripting (XSS) Analysis Report",
    "auth": "Authentication Analysis Report",
    "ssrf": "SSRF Analysis Report",
    "authz": "Authorization Analysis Report",
}

SECTION_FOUR_HEADING: dict[str, str] = {
    "injection": "4. Vectors Analyzed and Confirmed Secure",
    "xss": "4. Vectors Analyzed and Confirmed Secure",
    "auth": "4. Secure by Design: Validated Components",
    "ssrf": "4. Secure by Design: Validated Components",
    "authz": "4. Vectors Analyzed and Confirmed Secure",
}

# [field_name, friendly_header] per class -- field_name 与 collectors/vuln.py schema 一致
STRATEGIC_INTEL_SUBHEADERS: dict[str, list[tuple[str, str]]] = {
    "injection": [
        ("defensive_evasion_waf", "Defensive Evasion (WAF Analysis)"),
        ("error_based_potential", "Error-Based Injection Potential"),
        ("confirmed_database_technology", "Confirmed Database Technology"),
    ],
    "xss": [
        ("csp_analysis", "Content Security Policy (CSP) Analysis"),
        ("cookie_security", "Cookie Security"),
    ],
    "auth": [
        ("authentication_method", "Authentication Method"),
        ("session_token_details", "Session Token Details"),
        ("password_policy", "Password Policy"),
    ],
    "ssrf": [
        ("http_client_library", "HTTP Client Library"),
        ("request_architecture", "Request Architecture"),
        ("internal_services", "Internal Services"),
    ],
    "authz": [
        ("session_management_architecture", "Session Management Architecture"),
        ("role_permission_model", "Role/Permission Model"),
        ("resource_access_patterns", "Resource Access Patterns"),
        ("workflow_implementation", "Workflow Implementation"),
    ],
}

# §4 列形状:XSS 多 Render Context 列;subject/location 列名 per class
SECTION_FOUR_COLUMNS: dict[str, dict] = {
    "injection": {"subject": "Source", "location": "Endpoint/File Location",
                  "include_render_context": False},
    "xss": {"subject": "Source", "location": "Endpoint/File Location",
            "include_render_context": True},
    "auth": {"subject": "Component/Flow", "location": "Endpoint/File Location",
             "include_render_context": False},
    "ssrf": {"subject": "Component/Flow", "location": "Endpoint/File Location",
             "include_render_context": False},
    "authz": {"subject": "Endpoint", "location": "Guard Location",
              "include_render_context": False},
}


# ── section 渲染函数(5 个,纯函数) ─────────────────────────────────────
def _executive_summary(summary: dict | None) -> str:
    summary = as_dict(summary)
    if not summary:
        return f"## 1. Executive Summary\n\n{placeholder('Section 1', 'set_findings_summary')}"
    return f"## 1. Executive Summary\n\n{summary.get('key_outcome', '')}"


def _dominant_patterns(summary: dict | None) -> str:
    summary = as_dict(summary)
    head = "## 2. Dominant Vulnerability Patterns"
    if not summary:
        return f"{head}\n\n{placeholder('Section 2', 'set_findings_summary')}"
    patterns = as_dict_list(summary.get("patterns"))
    if not patterns:
        return f"{head}\n\n*No dominant patterns identified.*"
    blocks = []
    for i, p in enumerate(patterns, 1):
        ids = ", ".join(str(x) for x in as_list(p.get("representative_finding_ids")))
        blocks.append("\n".join([
            f"### Pattern {i}: {p.get('name', '')}", "",
            p.get("description", ""), "",
            f"**Implication:** {p.get('implication', '')}", "",
            f"**Findings:** {ids}",
        ]))
    return f"{head}\n\n" + "\n\n".join(blocks)


def _strategic_intel(vuln_class: str, intel: dict | None) -> str:
    intel = as_dict(intel)
    head = "## 3. Strategic Intelligence for Exploitation"
    if not intel:
        return f"{head}\n\n{placeholder('Section 3', 'set_strategic_intelligence')}"
    subheaders = STRATEGIC_INTEL_SUBHEADERS.get(vuln_class, [])
    blocks = []
    for field_name, header in subheaders:
        val = intel.get(field_name)
        if val is not None:
            blocks.append(f"### {header}\n\n{val}")
    if not blocks:
        return f"{head}\n\n{placeholder('Section 3', 'set_strategic_intelligence')}"
    return f"{head}\n\n" + "\n\n".join(blocks)


def _safe_vectors(vuln_class: str, data: dict | None) -> str:
    data = as_dict(data)
    cols = SECTION_FOUR_COLUMNS[vuln_class]
    head = f"## {SECTION_FOUR_HEADING[vuln_class]}"
    if not data:
        return f"{head}\n\n{placeholder('Section 4', 'set_safe_vectors')}"
    vectors = as_dict_list(data.get("vectors"))
    if not vectors:
        return f"{head}\n\n*No vectors confirmed secure during analysis.*"
    headers = [cols["subject"], cols["location"], "Defense Mechanism"]
    if cols["include_render_context"]:
        headers.append("Render Context")
    rows = []
    for v in vectors:
        row = [v.get("subject", ""), v.get("location", ""), v.get("defense_mechanism", "")]
        if cols["include_render_context"]:
            row.append(v.get("render_context") or "")
        rows.append(row)
    return f"{head}\n\n{render_table(headers, rows)}"


def _blind_spots(data: dict | None) -> str:
    data = as_dict(data)
    head = "## 5. Analysis Constraints and Blind Spots"
    if not data:
        return f"{head}\n\n{placeholder('Section 5', 'set_blind_spots')}"
    items = as_dict_list(data.get("items"))
    if not items:
        return f"{head}\n\n*No analysis constraints or blind spots identified.*"
    blocks = [f"### {it.get('heading', '')}\n\n{it.get('description', '')}" for it in items]
    return f"{head}\n\n" + "\n\n".join(blocks)


def render_vuln(vuln_class: str, data: dict) -> str:
    """渲染完整 vuln deliverable md:标题 + 5 section。data = collector.get_all() 子集。"""
    summary = as_dict(data.get("findings_summary"))
    parts = [
        f"# {TITLES[vuln_class]}", "",
        _executive_summary(summary), "",
        _dominant_patterns(summary), "",
        _strategic_intel(vuln_class, data.get("strategic_intelligence")), "",
        _safe_vectors(vuln_class, data.get("safe_vectors")), "",
        _blind_spots(data.get("blind_spots")), "",
    ]
    return "\n".join(parts).rstrip() + "\n"
