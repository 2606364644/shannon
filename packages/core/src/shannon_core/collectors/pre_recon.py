"""pre-recon 的 7 个 set_* section schema（对齐 TS pre-recon-collector.ts）。

字段名/类型移植 TS TypeBox 定义；JSON Schema dict 直接喂双引擎桥（openai
params_json_schema / claude input_schema）。application_intelligence 是复合工具，
喂 renderer 的 section 2/4/5/6 四个 section。
"""
from __future__ import annotations

import copy

from shannon_core.collectors.base import CollectorBase, SectionSchema


def _str_field(desc: str, min_length: int = 1) -> dict:
    return {"type": "string", "minLength": min_length, "description": desc}


# SinkRef（XSS/SSRF 数组元素，对齐 TS SinkRefSchema）
SINK_REF: dict = {
    "type": "object",
    "properties": {
        "location": _str_field(
            "File path with line number (e.g. 'templates/render.js:34') or richer prose. "
            "Must let a downstream agent find the exact location."
        ),
        "sink_function": _str_field("The sink function or property name (e.g. 'innerHTML', 'eval')."),
        "notes": {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "description": "Optional context — render-context, attribute, scope hints. Omit when not needed.",
        },
    },
    "required": ["location", "sink_function"],
}


def _sink_array(desc: str) -> dict:
    return {"type": "array", "items": copy.deepcopy(SINK_REF), "description": desc}


def _str_array(desc: str = "") -> dict:
    s = {"type": "array", "items": {"type": "string", "minLength": 1}}
    if desc:
        s["description"] = desc
    return s


def _obj(props: dict, required: list[str], desc: str = "") -> dict:
    schema: dict = {"type": "object", "properties": props, "required": required}
    if desc:
        schema["description"] = desc
    return schema


# --- 各 section schema（移植 TS）---

EXECUTIVE_SUMMARY = _obj({"text": {"type": "string", "minLength": 1}}, ["text"])

APPLICATION_INTELLIGENCE = _obj(
    {
        "architecture": _obj(
            {
                "framework_and_language": _str_field("Framework & language with security implications."),
                "architectural_pattern": _str_field("Architectural pattern with trust boundary analysis."),
                "critical_security_components": _str_field("Focus on auth, authz, data protection."),
            },
            ["framework_and_language", "architectural_pattern", "critical_security_components"],
        ),
        "data_security": _obj(
            {
                "database_security": _str_field("Encryption, access controls, query safety."),
                "data_flow_security": _str_field("Sensitive data paths and protection mechanisms."),
                "multi_tenant_isolation": _str_field("Tenant separation effectiveness."),
            },
            ["database_security", "data_flow_security", "multi_tenant_isolation"],
        ),
        "attack_surface": _obj(
            {
                "external_entry_points": _str_field("Publicly exposed web pages and API endpoints."),
                "internal_service_communication": _str_field("Service-to-service calls and trust."),
                "input_validation_patterns": _str_field("Where/how input is validated."),
                "background_processing": _str_field("Queues, schedulers, webhooks."),
            },
            ["external_entry_points", "internal_service_communication",
             "input_validation_patterns", "background_processing"],
        ),
        "infrastructure": _obj(
            {
                "secrets_management": _str_field("How secrets are stored/loaded."),
                "configuration_security": _str_field("Config hardening, debug flags."),
                "external_dependencies": _str_field("Notable deps with known risk surface."),
                "monitoring_and_logging": _str_field("What is logged; sensitive data leakage."),
            },
            ["secrets_management", "configuration_security", "external_dependencies", "monitoring_and_logging"],
        ),
    },
    ["architecture", "data_security", "attack_surface", "infrastructure"],
    desc="Composite of architecture (Section 2), data security (4), attack surface (5), infrastructure (6).",
)

AUTH_DEEP_DIVE = _obj(
    {
        "authentication_mechanisms": _str_field("Auth mechanisms + exhaustive list of auth endpoints."),
        "session_management": _str_field("Session/token security; cookie flags (HttpOnly/Secure/SameSite) with file:line."),
        "authz_model": _str_field("Authorization model and bypass scenarios."),
        "multi_tenancy": _str_field("Multi-tenancy security implementation."),
        "sso_oauth_oidc": {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "description": "SSO/OAuth/OIDC flows; null if none at all.",
        },
    },
    ["authentication_mechanisms", "session_management", "authz_model", "multi_tenancy", "sso_oauth_oidc"],
)

CODEBASE_INDEXING = _obj({"text": {"type": "string", "minLength": 1}}, ["text"])

CRITICAL_FILE_PATHS = _obj(
    {k: _str_array() for k in [
        "configuration", "authentication_and_authorization", "api_and_routing",
        "data_models_and_db", "dependency_manifests", "sensitive_data_and_secrets",
        "middleware_and_input_validation", "logging_and_monitoring", "infrastructure_and_deployment",
    ]},
    [],  # 全可选：某类没有就给空数组
)

XSS_SINKS = _obj(
    {
        "applicable": {"type": "boolean"},
        "html_body": _sink_array("Sinks rendered into HTML body context."),
        "html_attribute": _sink_array("Sinks rendered into HTML attribute context."),
        "javascript": _sink_array("Sinks rendered into JavaScript context."),
        "css": _sink_array("Sinks rendered into CSS context."),
        "url": _sink_array("Sinks rendered into URL context."),
    },
    ["applicable", "html_body", "html_attribute", "javascript", "css", "url"],
)

_SSRF_KEYS = [
    "http_clients", "raw_sockets", "url_openers", "redirect_handlers",
    "headless_browsers", "media_processors", "link_preview", "webhook_testers",
    "sso_oidc_discovery", "importers", "package_installers",
    "monitoring_and_health", "cloud_metadata",
]
SSRF_SINKS = _obj(
    {
        "applicable": {"type": "boolean"},
        **{k: _sink_array(v) for k, v in {
            "http_clients": "HTTP client sinks.", "raw_sockets": "Raw socket sinks.",
            "url_openers": "URL opener sinks.", "redirect_handlers": "Redirect handler sinks.",
            "headless_browsers": "Headless browser sinks.", "media_processors": "Media processor sinks.",
            "link_preview": "Link preview sinks.", "webhook_testers": "Webhook tester sinks.",
            "sso_oidc_discovery": "SSO/OIDC discovery sinks.", "importers": "Importer sinks.",
            "package_installers": "Package installer sinks.", "monitoring_and_health": "Monitoring/health sinks.",
            "cloud_metadata": "Cloud metadata sinks.",
        }.items()},
    },
    ["applicable"] + _SSRF_KEYS,
)


def _section(tool_name: str, key: str, desc: str, schema: dict) -> SectionSchema:
    return SectionSchema(tool_name=tool_name, section_key=key, description=desc, json_schema=schema)


# 顺序对齐 TS PRE_RECON_ONE_SHOT_TOOLS
PRE_RECON_SECTIONS: list[SectionSchema] = [
    _section("set_executive_summary", "executive_summary",
             "Application's overall security posture (Section 1).", EXECUTIVE_SUMMARY),
    _section("set_application_intelligence", "application_intelligence",
             "Composite of architecture, data security, attack surface, infrastructure (Sections 2,4,5,6).",
             APPLICATION_INTELLIGENCE),
    _section("set_auth_deep_dive", "auth_deep_dive",
             "Authentication & authorization deep dive (Section 3).", AUTH_DEEP_DIVE),
    _section("set_codebase_indexing", "codebase_indexing",
             "Directory structure narrative (Section 7).", CODEBASE_INDEXING),
    _section("set_critical_file_paths", "critical_file_paths",
             "Categorized catalog of critical file paths (Section 8).", CRITICAL_FILE_PATHS),
    _section("set_xss_sinks", "xss_sinks",
             "XSS sinks grouped by render context (Section 9). Set applicable=false only if no web frontend.",
             XSS_SINKS),
    _section("set_ssrf_sinks", "ssrf_sinks",
             "SSRF sinks grouped by sink category (Section 10). Set applicable=false only if no outbound requests.",
             SSRF_SINKS),
]


class PreReconCollector(CollectorBase):
    """pre-recon 的 7-section collector（无参构造，自带 PRE_RECON_SECTIONS）。"""

    def __init__(self) -> None:
        super().__init__(PRE_RECON_SECTIONS)
