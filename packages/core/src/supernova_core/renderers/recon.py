"""recon deliverable renderer（纯函数，移植 prompts/recon.txt §0-9）。

输入 data = collector.get_all() 的子集（缺键 = skipped -> placeholder，不 fail）。
输出 md：标题 + §0 静态常量 + §1-9 数据驱动 section。9 个 set_* 工具的 payload
各自渲染对应 section；skipped -> placeholder("Section N","set_*")。不 import
GitNexus / 确定性层（守 §1）。

模式对齐 pre_recon.py / vuln.py：helper-per-section + ``"\n".join(parts).rstrip() + "\n"``。
"""
from __future__ import annotations

from ._helpers import as_dict, as_dict_list, as_list, placeholder, render_table

# ── §0 静态常量（移植 recon.txt line 164-175）──────────────────────────
HOW_TO_READ_THIS = """## 0) HOW TO READ THIS
This reconnaissance report provides a comprehensive map of the application's attack surface, with special emphasis on authorization and privilege escalation opportunities for the Authorization Analysis Specialist.

**Key Sections for Authorization Analysis:**
- **Section 4 (API Endpoint Inventory):** Contains authorization details for each endpoint - focus on "Required Role" and "Object ID Parameters" columns to identify IDOR candidates.
- **Section 6.4 (Guards Directory):** Catalog of authorization controls - understand what each guard means before analyzing vulnerabilities.
- **Section 7 (Role & Privilege Architecture):** Complete role hierarchy and privilege mapping - use this to understand the privilege lattice and identify escalation targets.
- **Section 8 (Authorization Vulnerability Candidates):** Pre-prioritized lists of endpoints for horizontal, vertical, and context-based authorization testing.

**How to Use the Network Mapping (Section 6):** The entity/flow mapping shows system boundaries and data sensitivity levels. Pay special attention to flows marked with authorization guards and entities handling PII/sensitive data.

**Priority Order for Testing:** Start with Section 8's High-priority horizontal candidates, then vertical escalation endpoints for each role level, finally context-based workflow bypasses."""


# ── helpers ────────────────────────────────────────────────────────────
def _kv(label: str, value) -> str:
    return f"- **{label}:** {value}"


def _section(n: int, title: str, body: str) -> str:
    return f"## {n}. {title}\n\n{body}"


def _sub(num: str, title: str, body: str) -> str:
    return f"### {num} {title}\n\n{body}"


# ── §1 Executive Summary ───────────────────────────────────────────────
def _render_executive_summary(data: dict) -> str:
    es = as_dict(data.get("executive_summary"))
    if not es:
        return _section(1, "Executive Summary", placeholder("Section 1", "set_executive_summary"))
    text = (es.get("text") or "").strip()
    return _section(1, "Executive Summary", text or placeholder("Section 1", "set_executive_summary"))


# ── §2 Technology & Service Map ────────────────────────────────────────
def _render_technology_stack(data: dict) -> str:
    ts = as_dict(data.get("technology_stack"))
    if not ts:
        return _section(2, "Technology & Service Map", placeholder("Section 2", "set_technology_stack"))
    body = "\n".join([
        _kv("Frontend", ts.get("frontend", "")),
        _kv("Backend", ts.get("backend", "")),
        _kv("Infrastructure", ts.get("infrastructure", "")),
    ])
    return _section(2, "Technology & Service Map", body)


# ── §3 Authentication & Session Management Flow ────────────────────────
def _render_role_assignment(auth: dict) -> str:
    ra = as_dict(auth.get("role_assignment"))
    body = "\n".join([
        _kv("Role Determination", ra.get("role_determination", "")),
        _kv("Default Role", ra.get("default_role", "")),
        _kv("Role Upgrade Path", ra.get("role_upgrade_path", "")),
        _kv("Code Implementation", ra.get("code_implementation", "")),
    ])
    return _sub("3.1", "Role Assignment Process", body)


def _render_privilege_storage(auth: dict) -> str:
    ps = as_dict(auth.get("privilege_storage"))
    body = "\n".join([
        _kv("Storage Location", ps.get("storage_location", "")),
        _kv("Validation Points", ps.get("validation_points", "")),
        _kv("Cache/Session Persistence", ps.get("cache_session_persistence", "")),
        _kv("Code Pointers", ps.get("code_pointers", "")),
    ])
    return _sub("3.2", "Privilege Storage & Validation", body)


def _render_role_switching(auth: dict) -> str:
    rsi = as_dict(auth.get("role_switching_impersonation"))
    if not rsi or rsi.get("applicable") is False:
        return _sub("3.3", "Role Switching & Impersonation", "[not applicable]")
    body = "\n".join([
        _kv("Impersonation Features", rsi.get("impersonation_features") or ""),
        _kv("Role Switching", rsi.get("role_switching") or ""),
        _kv("Audit Trail", rsi.get("audit_trail") or ""),
        _kv("Code Implementation", rsi.get("code_implementation") or ""),
    ])
    return _sub("3.3", "Role Switching & Impersonation", body)


def _render_authentication(data: dict) -> str:
    auth = as_dict(data.get("authentication"))
    if not auth:
        return _section(3, "Authentication & Session Management Flow",
                        placeholder("Section 3", "set_authentication"))
    sf = as_dict(auth.get("session_flow"))
    session_body = "\n".join([
        _kv("Entry Points", sf.get("entry_points", "")),
        _kv("Mechanism", sf.get("mechanism", "")),
        _kv("Code Pointers", sf.get("code_pointers", "")),
    ])
    parts = [
        session_body, "",
        _render_role_assignment(auth), "",
        _render_privilege_storage(auth), "",
        _render_role_switching(auth),
    ]
    return _section(3, "Authentication & Session Management Flow", "\n".join(parts))


# ── §4 API Endpoint Inventory (set_endpoints append) ───────────────────
def _render_endpoints(data: dict) -> str:
    endpoints = as_dict_list(data.get("endpoints"))
    if not endpoints:
        return _section(4, "API Endpoint Inventory", placeholder("Section 4", "set_endpoints"))
    headers = ["Method", "Endpoint Path", "Required Role", "Object ID Parameters",
               "Authorization Mechanism", "Description & Code Pointer"]
    rows = [
        [e.get("method", ""), e.get("path", ""), e.get("required_role", ""),
         e.get("object_id_parameters", ""), e.get("authorization_mechanism", ""),
         e.get("description_code_pointer", "")]
        for e in endpoints
    ]
    # deterministic output (对齐 prompt 承诺 "sorts by (path, method)"): r[1]=path, r[0]=method
    rows.sort(key=lambda r: (r[1], r[0]))
    return _section(4, "API Endpoint Inventory", render_table(headers, rows))


# ── §5 Potential Input Vectors ─────────────────────────────────────────
def _render_input_vectors(data: dict) -> str:
    iv = as_dict(data.get("input_vectors"))
    if not iv:
        return _section(5, "Potential Input Vectors for Vulnerability Analysis",
                        placeholder("Section 5", "set_input_vectors"))

    def _bullets(items):
        items = as_list(items)
        if not items:
            return "*(none identified)*"
        return "\n".join(f"  - {item}" for item in items)

    body = "\n".join([
        f"- **URL Parameters:**\n{_bullets(iv.get('url_parameters'))}",
        f"- **POST Body Fields (JSON/Form):**\n{_bullets(iv.get('post_body_fields'))}",
        f"- **HTTP Headers:**\n{_bullets(iv.get('http_headers'))}",
        f"- **Cookie Values:**\n{_bullets(iv.get('cookie_values'))}",
    ])
    return _section(5, "Potential Input Vectors for Vulnerability Analysis", body)


# ── §6 Network & Interaction Map ───────────────────────────────────────
def _render_entities(nm: dict) -> str:
    entities = as_dict_list(nm.get("entities"))
    if not entities:
        return _sub("6.1", "Entities", "*None identified.*")
    headers = ["Title", "Type", "Zone", "Tech", "Data", "Notes"]
    rows = [
        [e.get("title", ""), e.get("type", ""), e.get("zone", ""), e.get("tech", ""),
         ", ".join(str(x) for x in as_list(e.get("data"))), e.get("notes", "")]
        for e in entities
    ]
    # deterministic output (对齐 prompt 承诺 "sorts each array deterministically"): r[0]=title
    rows.sort(key=lambda r: r[0])
    return _sub("6.1", "Entities", render_table(headers, rows))


def _render_entity_metadata(nm: dict) -> str:
    entities = as_dict_list(nm.get("entities"))
    rows = []
    for e in entities:
        meta = as_dict_list(e.get("metadata"))
        if not meta:
            continue
        meta_str = "; ".join(f"{m.get('key', '')}: {m.get('value', '')}" for m in meta)
        rows.append([e.get("title", ""), meta_str])
    if not rows:
        return _sub("6.2", "Entity Metadata", "*None identified.*")
    # deterministic output: r[0]=title
    rows.sort(key=lambda r: r[0])
    return _sub("6.2", "Entity Metadata", render_table(["Title", "Metadata"], rows))


def _render_flows(nm: dict) -> str:
    flows = as_dict_list(nm.get("flows"))
    if not flows:
        return _sub("6.3", "Flows (Connections)", "*None identified.*")
    headers = ["FROM → TO", "Channel", "Path/Port", "Guards", "Touches"]
    rows = [
        [f"{f.get('from', '')} → {f.get('to', '')}", f.get("channel", ""),
         f.get("path_port", ""), ", ".join(str(x) for x in as_list(f.get("guards"))),
         ", ".join(str(x) for x in as_list(f.get("touches")))]
        for f in flows
    ]
    # deterministic output: r[0]="from → to" string
    rows.sort(key=lambda r: r[0])
    return _sub("6.3", "Flows (Connections)", render_table(headers, rows))


def _render_guards(nm: dict) -> str:
    guards = as_dict_list(nm.get("guards"))
    if not guards:
        return _sub("6.4", "Guards Directory", "*None identified.*")
    headers = ["Guard Name", "Category", "Statement"]
    rows = [[g.get("name", ""), g.get("category", ""), g.get("statement", "")] for g in guards]
    return _sub("6.4", "Guards Directory", render_table(headers, rows))


def _render_network_map(data: dict) -> str:
    nm = as_dict(data.get("network_map"))
    if not nm:
        return _section(6, "Network & Interaction Map",
                        placeholder("Section 6", "set_network_map"))
    parts = [
        _render_entities(nm), "",
        _render_entity_metadata(nm), "",
        _render_flows(nm), "",
        _render_guards(nm),
    ]
    return _section(6, "Network & Interaction Map", "\n".join(parts))


# ── §7 Role & Privilege Architecture ──────────────────────────────────
def _render_discovered_roles(ra: dict) -> str:
    roles = as_dict_list(ra.get("roles"))
    if not roles:
        return _sub("7.1", "Discovered Roles", "*None identified.*")
    headers = ["Role Name", "Privilege Level", "Scope/Domain", "Code Implementation"]
    rows = [
        [r.get("name", ""), str(r.get("privilege_level", "")), r.get("scope_domain", ""),
         r.get("code_implementation", "")]
        for r in roles
    ]
    # deterministic output: r[0]=role name
    rows.sort(key=lambda r: r[0])
    return _sub("7.1", "Discovered Roles", render_table(headers, rows))


def _render_privilege_lattice(ra: dict) -> str:
    pl = as_dict(ra.get("privilege_lattice"))
    ordering = pl.get("ordering_diagram", "")
    parallel = pl.get("parallel_isolation_notes", "")
    switching = pl.get("role_switching_notes")
    code_block = "\n".join([
        "```",
        'Privilege Ordering (→ means "can access resources of"):',
        ordering,
        "",
        'Parallel Isolation (|| means "not ordered relative to each other"):',
        parallel,
        "```",
    ])
    parts = [code_block]
    if switching:
        parts.append("")
        parts.append(f"**Role Switching Notes:** {switching}")
    return _sub("7.2", "Privilege Lattice", "\n".join(parts))


def _render_role_entry_points(ra: dict) -> str:
    roles = as_dict_list(ra.get("roles"))
    if not roles:
        return _sub("7.3", "Role Entry Points", "*None identified.*")
    headers = ["Role", "Default Landing Page", "Accessible Route Patterns", "Authentication Method"]
    rows = [
        [r.get("name", ""), r.get("default_landing_page", ""),
         ", ".join(str(x) for x in as_list(r.get("accessible_route_patterns"))),
         r.get("authentication_method", "")]
        for r in roles
    ]
    # deterministic output: r[0]=role
    rows.sort(key=lambda r: r[0])
    return _sub("7.3", "Role Entry Points", render_table(headers, rows))


def _render_role_to_code(ra: dict) -> str:
    roles = as_dict_list(ra.get("roles"))
    if not roles:
        return _sub("7.4", "Role-to-Code Mapping", "*None identified.*")
    headers = ["Role", "Middleware/Guards", "Permission Checks", "Storage Location"]
    rows = [
        [r.get("name", ""), r.get("middleware_guards", ""),
         r.get("permission_checks", ""), r.get("storage_location", "")]
        for r in roles
    ]
    # deterministic output: r[0]=role
    rows.sort(key=lambda r: r[0])
    return _sub("7.4", "Role-to-Code Mapping", render_table(headers, rows))


def _render_role_architecture(data: dict) -> str:
    ra = as_dict(data.get("role_architecture"))
    if not ra:
        return _section(7, "Role & Privilege Architecture",
                        placeholder("Section 7", "set_role_architecture"))
    parts = [
        _render_discovered_roles(ra), "",
        _render_privilege_lattice(ra), "",
        _render_role_entry_points(ra), "",
        _render_role_to_code(ra),
    ]
    return _section(7, "Role & Privilege Architecture", "\n".join(parts))


# ── §8 Authorization Vulnerability Candidates ──────────────────────────
def _render_horizontal(ac: dict) -> str:
    items = as_dict_list(ac.get("horizontal"))
    if not items:
        return _sub("8.1", "Horizontal Privilege Escalation Candidates", "*None identified.*")
    headers = ["Priority", "Endpoint Pattern", "Object ID Parameter", "Data Type", "Sensitivity"]
    rows = [
        [h.get("priority", ""), h.get("endpoint_pattern", ""), h.get("object_id_parameter", ""),
         h.get("data_type", ""), h.get("sensitivity", "")]
        for h in items
    ]
    # deterministic output: r[0]=priority
    rows.sort(key=lambda r: r[0])
    return _sub("8.1", "Horizontal Privilege Escalation Candidates", render_table(headers, rows))


def _render_vertical(ac: dict) -> str:
    items = as_dict_list(ac.get("vertical"))
    if not items:
        return _sub("8.2", "Vertical Privilege Escalation Candidates", "*None identified.*")
    headers = ["Target Role", "Endpoint Pattern", "Functionality", "Risk Level"]
    rows = [
        [v.get("target_role", ""), v.get("endpoint_pattern", ""), v.get("functionality", ""),
         v.get("risk_level", "")]
        for v in items
    ]
    # deterministic output: r[0]=target_role
    rows.sort(key=lambda r: r[0])
    return _sub("8.2", "Vertical Privilege Escalation Candidates", render_table(headers, rows))


def _render_context(ac: dict) -> str:
    items = as_dict_list(ac.get("context"))
    if not items:
        return _sub("8.3", "Context-Based Authorization Candidates", "*None identified.*")
    headers = ["Workflow", "Endpoint", "Expected Prior State", "Bypass Potential"]
    rows = [
        [c.get("workflow", ""), c.get("endpoint", ""), c.get("expected_prior_state", ""),
         c.get("bypass_potential", "")]
        for c in items
    ]
    # deterministic output: r[0]=workflow
    rows.sort(key=lambda r: r[0])
    return _sub("8.3", "Context-Based Authorization Candidates", render_table(headers, rows))


def _render_authz_candidates(data: dict) -> str:
    ac = as_dict(data.get("authz_candidates"))
    if not ac:
        return _section(8, "Authorization Vulnerability Candidates",
                        placeholder("Section 8", "set_authz_candidates"))
    parts = [
        _render_horizontal(ac), "",
        _render_vertical(ac), "",
        _render_context(ac),
    ]
    return _section(8, "Authorization Vulnerability Candidates", "\n".join(parts))


# ── §9 Injection Sources ───────────────────────────────────────────────
_INJECTION_NA = "*N/A — no network-accessible code paths to dangerous sinks.*"

_INJECTION_LABELS = [
    ("command_injection", "Command Injection"),
    ("sql_injection", "SQL Injection"),
    ("lfi_rfi", "LFI/RFI"),
    ("path_traversal", "Path Traversal"),
    ("ssti", "SSTI"),
    ("deserialization", "Deserialization"),
]


def _render_sink_list(sinks) -> str:
    sinks = as_dict_list(sinks)
    if not sinks:
        return "*(scanned, no sources of this kind found)*"
    return "\n".join(
        f"- `{s.get('sink_function', '?')}` — {s.get('location', '?')}"
        + (f" ({s['notes']})" if s.get("notes") else "")
        for s in sinks
    )


def _render_injection_sources(data: dict) -> str:
    inj = as_dict(data.get("injection_sources"))
    if not inj:
        return _section(9, "Injection Sources", placeholder("Section 9", "set_injection_sources"))
    if inj.get("applicable") is False:
        return _section(9, "Injection Sources", _INJECTION_NA)
    lines = [
        f"- **{label}:**\n  {_render_sink_list(inj.get(key) or [])}"
        for key, label in _INJECTION_LABELS
    ]
    return _section(9, "Injection Sources", "\n".join(lines))


# ── main ───────────────────────────────────────────────────────────────
def render_recon(data: dict) -> str:
    """渲染完整 recon deliverable md：标题 + §0 静态常量 + §1-9 数据驱动 section。

    data = collector.get_all() 子集（缺键 = skipped → placeholder，不 fail）。
    结构对齐 prompts/recon.txt §0-9；下游 vuln agent 读 recon_deliverable.md
    的契约不变。
    """
    parts = [
        "# Reconnaissance Deliverable:", "",
        HOW_TO_READ_THIS, "",
        _render_executive_summary(data), "",
        _render_technology_stack(data), "",
        _render_authentication(data), "",
        _render_endpoints(data), "",
        _render_input_vectors(data), "",
        _render_network_map(data), "",
        _render_role_architecture(data), "",
        _render_authz_candidates(data), "",
        _render_injection_sources(data), "",
    ]
    return "\n".join(parts).rstrip() + "\n"
