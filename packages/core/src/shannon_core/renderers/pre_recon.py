"""pre-recon deliverable renderer（纯函数，对齐 TS services/pre-recon-renderer.ts::renderPreRecon）。

输入 data = collector.get_all() 的子集（缺键 = skipped → placeholder，不 fail）。
输出 md：preamble + 10 section。application_intelligence 喂 section 2/4/5/6。
"""
from __future__ import annotations

SCOPE_AND_BOUNDARIES = """# Penetration Test Scope & Boundaries

**Primary Directive:** Your analysis is strictly limited to the **network-accessible attack surface** of the application. All subsequent tasks must adhere to this scope. Before reporting any finding (e.g., an entry point, a vulnerability sink), you must first verify it meets the "In-Scope" criteria.

### In-Scope: Network-Reachable Components
A component is considered **in-scope** if its execution can be initiated, directly or indirectly, by a network request that the deployed application server is capable of receiving. This includes:
- Publicly exposed web pages and API endpoints.
- Endpoints requiring authentication via the application's standard login mechanisms.
- Any developer utility, debug console, or script that has been mistakenly exposed through a route or is otherwise callable from other in-scope, network-reachable code.

### Out-of-Scope: Locally Executable Only
A component is **out-of-scope** if it **cannot** be invoked through the running application's network interface and requires an execution context completely external to the application's request-response cycle. This includes tools that must be run via:
- A command-line interface (e.g., `go run ./cmd/...`, `python scripts/...`).
- A development environment's internal tooling (e.g., a "run script" button in an IDE).
- CI/CD pipeline scripts or build tools (e.g., Dagger build definitions).
- Database migration scripts, backup tools, or maintenance utilities.
- Local development servers, test harnesses, or debugging utilities.
- Static files or scripts that require manual opening in a browser (not served by the application).
"""


def _placeholder(n: int, tool: str) -> str:
    return f"_[Section {n}: not provided — `{tool}` was not called]_"


def _kv(label: str, value: str) -> str:
    return f"- **{label}:** {value}"


def _section(n: int, title: str, body: str) -> str:
    return f"## {n}. {title}\n\n{body}"


def _render_executive_summary(data) -> str:
    es = data.get("executive_summary")
    if not es:
        return _section(1, "Executive Summary", _placeholder(1, "set_executive_summary"))
    return _section(1, "Executive Summary", es.get("text", "").strip() or _placeholder(1, "set_executive_summary"))


def _render_architecture(ai) -> str:
    if not ai:
        return _section(2, "Architecture & Technology Stack", _placeholder(2, "set_application_intelligence"))
    a = ai.get("architecture", {})
    body = "\n".join([
        _kv("Framework & Language", a.get("framework_and_language", "")),
        _kv("Architectural Pattern", a.get("architectural_pattern", "")),
        _kv("Critical Security Components", a.get("critical_security_components", "")),
    ])
    return _section(2, "Architecture & Technology Stack", body)


def _render_auth(data) -> str:
    ad = data.get("auth_deep_dive")
    if not ad:
        return _section(3, "Authentication & Authorization Deep Dive", _placeholder(3, "set_auth_deep_dive"))
    sso = ad.get("sso_oauth_oidc")
    body = "\n".join([
        _kv("Authentication Mechanisms", ad.get("authentication_mechanisms", "")),
        _kv("Session Management", ad.get("session_management", "")),
        _kv("Authorization Model", ad.get("authz_model", "")),
        _kv("Multi-tenancy", ad.get("multi_tenancy", "")),
        _kv("SSO/OAuth/OIDC", sso if sso else "(none identified)"),
    ])
    return _section(3, "Authentication & Authorization Deep Dive", body)


def _render_data_security(ai) -> str:
    if not ai:
        return _section(4, "Data Security & Storage", _placeholder(4, "set_application_intelligence"))
    d = ai.get("data_security", {})
    body = "\n".join([
        _kv("Database Security", d.get("database_security", "")),
        _kv("Data Flow Security", d.get("data_flow_security", "")),
        _kv("Multi-tenant Data Isolation", d.get("multi_tenant_isolation", "")),
    ])
    return _section(4, "Data Security & Storage", body)


def _render_attack_surface(ai) -> str:
    if not ai:
        return _section(5, "Attack Surface Analysis", _placeholder(5, "set_application_intelligence"))
    a = ai.get("attack_surface", {})
    body = "\n".join([
        _kv("External Entry Points", a.get("external_entry_points", "")),
        _kv("Internal Service Communication", a.get("internal_service_communication", "")),
        _kv("Input Validation Patterns", a.get("input_validation_patterns", "")),
        _kv("Background Processing", a.get("background_processing", "")),
    ])
    return _section(5, "Attack Surface Analysis", body)


def _render_infrastructure(ai) -> str:
    if not ai:
        return _section(6, "Infrastructure & Operational Security", _placeholder(6, "set_application_intelligence"))
    i = ai.get("infrastructure", {})
    body = "\n".join([
        _kv("Secrets Management", i.get("secrets_management", "")),
        _kv("Configuration Security", i.get("configuration_security", "")),
        _kv("External Dependencies", i.get("external_dependencies", "")),
        _kv("Monitoring & Logging", i.get("monitoring_and_logging", "")),
    ])
    return _section(6, "Infrastructure & Operational Security", body)


def _render_codebase_indexing(data) -> str:
    ci = data.get("codebase_indexing")
    if not ci:
        return _section(7, "Overall Codebase Indexing", _placeholder(7, "set_codebase_indexing"))
    return _section(7, "Overall Codebase Indexing", ci.get("text", "").strip() or _placeholder(7, "set_codebase_indexing"))


_PATH_LABELS = [
    ("configuration", "Configuration"),
    ("authentication_and_authorization", "Authentication & Authorization"),
    ("api_and_routing", "API & Routing"),
    ("data_models_and_db", "Data Models & DB"),
    ("dependency_manifests", "Dependency Manifests"),
    ("sensitive_data_and_secrets", "Sensitive Data & Secrets"),
    ("middleware_and_input_validation", "Middleware & Input Validation"),
    ("logging_and_monitoring", "Logging & Monitoring"),
    ("infrastructure_and_deployment", "Infrastructure & Deployment"),
]


def _render_critical_file_paths(data) -> str:
    cfp = data.get("critical_file_paths")
    if not cfp:
        return _section(8, "Critical File Paths", _placeholder(8, "set_critical_file_paths"))
    lines = []
    for key, label in _PATH_LABELS:
        paths = cfp.get(key, [])
        if paths:
            bullets = "\n".join(f"  - {p}" for p in paths)
            lines.append(f"- **{label}:**\n{bullets}")
        else:
            lines.append(f"- **{label}:** *(none identified)*")
    return _section(8, "Critical File Paths", "\n".join(lines))


def _render_sink_list(sinks) -> str:
    if not sinks:
        return "*(scanned, no sinks of this kind found)*"
    return "\n".join(
        f"- `{s.get('sink_function', '?')}` — {s.get('location', '?')}"
        + (f" ({s['notes']})" if s.get("notes") else "")
        for s in sinks
    )


def _render_sinks(n, title, tool, payload, labels, na_text) -> str:
    if not payload:
        return _section(n, title, _placeholder(n, tool))
    if payload.get("applicable") is False:
        return _section(n, title, na_text)
    lines = [f"- **{label}:**\n  {_render_sink_list(payload.get(key, []))}" for key, label in labels]
    return _section(n, title, "\n".join(lines))


_XSS_LABELS = [("html_body", "HTML Body"), ("html_attribute", "HTML Attribute"),
               ("javascript", "JavaScript"), ("css", "CSS"), ("url", "URL")]
_XSS_NA = "*(N/A — the application has no web frontend; XSS sink analysis does not apply.)*"

_SSRF_LABELS = [
    ("http_clients", "HTTP Clients"), ("raw_sockets", "Raw Sockets"), ("url_openers", "URL Openers"),
    ("redirect_handlers", "Redirect Handlers"), ("headless_browsers", "Headless Browsers"),
    ("media_processors", "Media Processors"), ("link_preview", "Link Preview"),
    ("webhook_testers", "Webhook Testers"), ("sso_oidc_discovery", "SSO/OIDC Discovery"),
    ("importers", "Importers"), ("package_installers", "Package Installers"),
    ("monitoring_and_health", "Monitoring & Health"), ("cloud_metadata", "Cloud Metadata"),
]
_SSRF_NA = "*(N/A — the application makes no outbound requests; SSRF sink analysis does not apply.)*"


def render_pre_recon(data: dict) -> str:
    """data = collector.get_all() 子集（缺键=skipped）。返回完整 md（preamble + 10 section）。"""
    ai = data.get("application_intelligence")
    sections = [
        SCOPE_AND_BOUNDARIES,
        "---",
        "",
        _render_executive_summary(data),
        "",
        _render_architecture(ai),
        "",
        _render_auth(data),
        "",
        _render_data_security(ai),
        "",
        _render_attack_surface(ai),
        "",
        _render_infrastructure(ai),
        "",
        _render_codebase_indexing(data),
        "",
        _render_critical_file_paths(data),
        "",
        _render_sinks(9, "XSS Sinks and Render Contexts", "set_xss_sinks",
                      data.get("xss_sinks"), _XSS_LABELS, _XSS_NA),
        "",
        _render_sinks(10, "SSRF Sinks", "set_ssrf_sinks",
                      data.get("ssrf_sinks"), _SSRF_LABELS, _SSRF_NA),
        "",
    ]
    return "\n".join(sections).rstrip() + "\n"
