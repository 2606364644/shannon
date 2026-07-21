"""recon 的 9 个 set_* section schema（对齐 TS recon-collector.ts）。

字段名/类型/必填/描述全部逐字段移植 TS TypeBox 定义；JSON Schema dict 直接喂
双引擎桥（openai params_json_schema / claude input_schema），与 pre_recon.py 同模式。

9 个工具中 8 个是 write-once ``set_*``（mode="set"，对齐 TS RECON_ONE_SHOT_TOOLS），
第 9 个 ``set_endpoints`` 是 append 工具（mode="append"，对齐 TS add_endpoints）——
多次调累积 endpoint，渲染 §4 (API Endpoint Inventory) 表格。命名沿用本仓 ``set_*``
风格，语义是 append（TS 叫 add_endpoints）。
"""
from __future__ import annotations

import copy

from supernova_core.collectors.base import CollectorBase, SectionSchema
from supernova_core.collectors.pre_recon import SINK_REF, _obj, _str_array, _str_field


def _sink_array(desc: str) -> dict:
    """SinkRef 数组（复用 pre-recon SINK_REF 形状）。"""
    return {"type": "array", "items": copy.deepcopy(SINK_REF), "description": desc}


def _enum(values: list[str], desc: str = "") -> dict:
    """string enum（对齐 TS stringEnum → {type:string, enum:[...]}）。"""
    s: dict = {"type": "string", "enum": list(values)}
    if desc:
        s["description"] = desc
    return s


def _nullable_str(desc: str) -> dict:
    """string | null（对齐 TS Type.Union([Type.String(), Type.Null()], {desc})）。"""
    return {"anyOf": [{"type": "string"}, {"type": "null"}], "description": desc}


def _plain_str(desc: str = "") -> dict:
    """无 minLength 的 string（对齐 TS Type.String({description}) 不带 minLength）。"""
    s: dict = {"type": "string"}
    if desc:
        s["description"] = desc
    return s


def _section(tool_name: str, key: str, desc: str, schema: dict) -> SectionSchema:
    return SectionSchema(tool_name=tool_name, section_key=key, description=desc, json_schema=schema)


# ── Section 1 — ExecutiveSummaryInputSchema ──────────────────────────────
EXECUTIVE_SUMMARY = _obj(
    {
        "text": _str_field(
            "A brief overview of the application's purpose, core technology stack "
            "(e.g., Next.js, Cloudflare), and the primary user-facing components that "
            "constitute the attack surface. Becomes Section 1 of the rendered deliverable."
        ),
    },
    ["text"],
)


# ── Section 2 — TechnologyStackInputSchema ───────────────────────────────
TECHNOLOGY_STACK = _obj(
    {
        "frontend": _str_field("Framework, key libraries, and authentication libraries used on the frontend."),
        "backend": _str_field("Language, framework, and key dependencies used on the backend."),
        "infrastructure": _str_field("Hosting provider, CDN, database type, and other infrastructure components."),
    },
    ["frontend", "backend", "infrastructure"],
)


# ── Section 3 — AuthenticationInputSchema（含 3.1/3.2/3.3 子节）──────────
AUTHENTICATION = _obj(
    {
        "session_flow": _obj(
            {
                "entry_points": _str_field(
                    "Authentication entry points (e.g., /login, /register, /auth/sso)."
                ),
                "mechanism": _str_field(
                    "Describe the step-by-step authentication process: credential submission, "
                    "token generation, cookie setting, redirects, etc."
                ),
                "code_pointers": _str_field(
                    "Pointers to the primary files and functions in the codebase that manage "
                    "authentication and session logic."
                ),
            },
            ["entry_points", "mechanism", "code_pointers"],
            desc=(
                "Authentication & Session Management Flow — overall entry points, mechanism, "
                "and code pointers. Becomes Section 3 of the rendered deliverable."
            ),
        ),
        "role_assignment": _obj(
            {
                "role_determination": _str_field(
                    "How roles are assigned post-authentication — database lookup, JWT claims, "
                    "external service, etc."
                ),
                "default_role": _str_field("What role new users get by default."),
                "role_upgrade_path": _str_field(
                    "How users can gain higher privileges — admin approval, self-service, automatic, etc. "
                    "If no upgrade path exists, state that."
                ),
                "code_implementation": _str_field(
                    "Where role assignment logic is implemented (file paths and functions)."
                ),
            },
            ["role_determination", "default_role", "role_upgrade_path", "code_implementation"],
            desc="Role Assignment Process — how roles are determined post-authentication. Becomes Section 3.1.",
        ),
        "privilege_storage": _obj(
            {
                "storage_location": _str_field(
                    "Where user privileges are stored — JWT claims, session data, database, external service."
                ),
                "validation_points": _str_field(
                    "Where role checks happen — middleware, decorators, inline checks."
                ),
                "cache_session_persistence": _str_field(
                    "How long privileges are cached, and when they are refreshed."
                ),
                "code_pointers": _str_field("Files that handle privilege validation."),
            },
            ["storage_location", "validation_points", "cache_session_persistence", "code_pointers"],
            desc=(
                "Privilege Storage & Validation — where privileges live and where they are checked. "
                "Becomes Section 3.2."
            ),
        ),
        "role_switching_impersonation": _obj(
            {
                "applicable": {
                    "type": "boolean",
                    "description": (
                        "False only if the application has no impersonation, sudo-mode, or role-switching "
                        "features at all. When false, the other fields in this object may be null."
                    ),
                },
                "impersonation_features": _nullable_str(
                    "Any ability for admins or higher-privilege users to impersonate other users. "
                    "Pass null when applicable is false."
                ),
                "role_switching": _nullable_str(
                    'Temporary privilege elevation mechanisms like "sudo mode". '
                    "Pass null when applicable is false."
                ),
                "audit_trail": _nullable_str(
                    "Whether role switches or impersonation events are logged, and where. "
                    "Pass null when applicable is false."
                ),
                "code_implementation": _nullable_str(
                    "Where these features are implemented (file paths and functions). "
                    "Pass null when applicable is false."
                ),
            },
            [
                "applicable",
                "impersonation_features",
                "role_switching",
                "audit_trail",
                "code_implementation",
            ],
            desc=(
                "Role Switching & Impersonation — impersonation, sudo mode, audit trails. "
                "Becomes Section 3.3. Set applicable=false if no such features exist; "
                "the other fields may be null in that case."
            ),
        ),
    },
    ["session_flow", "role_assignment", "privilege_storage", "role_switching_impersonation"],
)


# ── Section 4 — API Endpoint Inventory（set_endpoints，append 语义） ──────
# 对齐 prompts/recon.txt §4 表头 6 列 + TS recon-collector.ts EndpointSchema。
# set_endpoints 是 append 工具（mode="append"）：每次调传一个 endpoint item，
# 多次调累积成 endpoints list（renderer Task B 渲染成 §4 表格）。
ENDPOINTS = _obj(
    {
        "method": _plain_str(
            'HTTP method (e.g. "GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"). '
            'Use "WS" for WebSocket upgrade endpoints.'
        ),
        "path": _str_field(
            'Endpoint path with parameter placeholders, e.g. "/api/users/{user_id}".'
        ),
        "required_role": _plain_str(
            'Minimum role needed to access this endpoint (e.g. "anon", "user", "admin"). '
            'Use "None" if no role is required.'
        ),
        "object_id_parameters": _plain_str(
            'Parameters that identify specific objects (e.g. "user_id", "order_id"). '
            'Multiple parameters comma-separated. Use "None" if no object ID parameters exist.'
        ),
        "authorization_mechanism": _plain_str(
            'How access is controlled — middleware, decorator, inline check. '
            'E.g. "Bearer Token + ownership check", "requireAuth() + requireAdmin()", "None".'
        ),
        "description_code_pointer": _str_field(
            'Brief description of the endpoint\'s purpose + file path and (where possible) line '
            'number of the handler. E.g. "Fetches user profile. See users.controller.ts:45".'
        ),
    },
    [
        "method",
        "path",
        "required_role",
        "object_id_parameters",
        "authorization_mechanism",
        "description_code_pointer",
    ],
)


# ── Section 5 — InputVectorsInputSchema ──────────────────────────────────
INPUT_VECTORS = _obj(
    {
        "url_parameters": _str_array(
            "URL parameter input vectors — each entry should identify the parameter and (where possible) "
            'the file:line of the handler. E.g. "?redirect_url= @ auth.controller.ts:88".'
        ),
        "post_body_fields": _str_array(
            "POST/PUT body field input vectors (JSON or form). E.g. "
            '"username @ login.handler.ts:34", "profile.description @ users.controller.ts:120".'
        ),
        "http_headers": _str_array(
            "HTTP header input vectors. Include both standard headers consumed by app code "
            "(e.g., X-Forwarded-For) and custom application headers."
        ),
        "cookie_values": _str_array(
            'Cookie-based input vectors. E.g. "preferences_cookie @ middleware/prefs.ts:22".'
        ),
    },
    ["url_parameters", "post_body_fields", "http_headers", "cookie_values"],
)


# ── Section 6 — NetworkMapInputSchema（entities / flows / guards）────────
# endpoints 是独立 append section（set_endpoints），不在 set_network_map payload
_ENTITY_TYPE_VALUES = ["ExternAsset", "Service", "Identity", "DataStore", "AdminPlane", "ThirdParty"]
_ENTITY_ZONE_VALUES = ["Internet", "Edge", "App", "Data", "Admin", "BuildCI", "ThirdParty"]
_DATA_LABEL_VALUES = ["PII", "Tokens", "Payments", "Secrets", "Public"]
_FLOW_CHANNEL_VALUES = ["HTTP", "HTTPS", "TCP", "Message", "File", "Token"]
_GUARD_CATEGORY_VALUES = [
    "Auth", "Network", "Protocol", "Env", "RateLimit", "Authorization", "ObjectOwnership",
]

_ENTITY_METADATA_PAIR = _obj(
    {
        "key": _str_field('Metadata key (e.g., "Hosts", "Endpoints", "Engine", "Issuer").'),
        "value": _str_field("Metadata value for this key."),
    },
    ["key", "value"],
)

_ENTITY = _obj(
    {
        "title": _str_field(
            'Unique short name for the entity (e.g., "ExampleWebApp", "PostgreSQL-DB", "IdentityProvider").'
        ),
        "type": _enum(
            _ENTITY_TYPE_VALUES,
            "Entity type. ExternAsset = client-side asset; Service = backend service; "
            "Identity = identity provider; DataStore = database / cache / object store; "
            "AdminPlane = admin/control surface; ThirdParty = external integration.",
        ),
        "zone": _enum(
            _ENTITY_ZONE_VALUES,
            "Trust zone. Internet = public; Edge = CDN/WAF/reverse-proxy tier; "
            "App = application/business logic; Data = persistent storage; "
            "Admin = administrative surface; BuildCI = build/CI/CD infrastructure; "
            "ThirdParty = external trust domain.",
        ),
        "tech": _str_field(
            'Short technology/framework description (e.g., "Node/Express", "Postgres 14", "AWS S3").'
        ),
        "data": {
            "type": "array",
            "items": _enum(_DATA_LABEL_VALUES),
            "description": "Data labels handled by this entity. Empty array if the entity handles only Public data.",
        },
        "notes": _plain_str(
            'Freeform context (e.g., "public-facing", "stores sensitive user data"). Empty string if none.'
        ),
        "metadata": {
            "type": "array",
            "items": _ENTITY_METADATA_PAIR,
            "description": (
                "Ordered key/value pairs of technical metadata for this entity. Becomes the Section 6.2 row "
                'rendered as "Key: Value; Key: Value; …". Example pairs for a service: Hosts, Endpoints, '
                "Auth, Dependencies; for a datastore: Engine, Exposure, Consumers, Credentials."
            ),
        },
    },
    ["title", "type", "zone", "tech", "data", "notes", "metadata"],
)

_FLOW = _obj(
    {
        "from": _str_field("Source entity title — must match a title from the entities array."),
        "to": _str_field("Destination entity title — must match a title from the entities array."),
        "channel": _enum(_FLOW_CHANNEL_VALUES, "Transport channel for this flow."),
        "path_port": _str_field(
            'Path and/or port for this flow. E.g. ":443 /api/users/me", ":5432", "queue: orders".'
        ),
        "guards": _str_array(
            "Guard names that gate this flow. Each should match a name from the guards array. "
            "Empty array means no guards apply (publicly accessible)."
        ),
        "touches": {
            "type": "array",
            "items": _enum(_DATA_LABEL_VALUES),
            "description": "Data labels this flow carries. Empty array if only Public data flows.",
        },
    },
    ["from", "to", "channel", "path_port", "guards", "touches"],
)

_GUARD = _obj(
    {
        "name": _str_field(
            'Short guard identifier (e.g., "auth:user", "ownership:user", "vpc-only", "mtls").'
        ),
        "category": _enum(
            _GUARD_CATEGORY_VALUES,
            "Guard category. Auth = authentication identity; Authorization = role/scope check; "
            "ObjectOwnership = ownership-based check; Network = network-level restriction; "
            "Protocol = protocol-level requirement; Env = environment-bound restriction; "
            "RateLimit = throttling.",
        ),
        "statement": _str_field("One-sentence description of what this guard enforces."),
    },
    ["name", "category", "statement"],
)

NETWORK_MAP = _obj(
    {
        "entities": {
            "type": "array",
            "items": _ENTITY,
            "description": (
                "All major components of the system. Becomes Section 6.1 (Entities) and Section 6.2 "
                "(Entity Metadata, split per-entity from the metadata field)."
            ),
        },
        "flows": {
            "type": "array",
            "items": _FLOW,
            "description": (
                "How entities communicate. Becomes Section 6.3. The from/to fields cross-reference "
                "entities by title; the guards field cross-references guards by name."
            ),
        },
        "guards": {
            "type": "array",
            "items": _GUARD,
            "description": "Catalog of guards referenced by flows. Becomes Section 6.4.",
        },
    },
    ["entities", "flows", "guards"],
)


# ── Section 7 — RoleArchitectureInputSchema ──────────────────────────────
_ROLE = _obj(
    {
        "name": _str_field('Role name (e.g., "anon", "user", "admin", "team_admin").'),
        "privilege_level": {
            "type": "integer",
            "minimum": 0,
            "maximum": 10,
            "description": "Privilege rank from 0 (lowest, anonymous) to 10 (highest, full admin).",
        },
        "scope_domain": _str_field("Scope of this role: Global, Org, Team, Project, etc."),
        "code_implementation": _str_field(
            "Where this role is defined or checked (middleware, decorator, file:line, etc.)."
        ),
        "default_landing_page": _str_field(
            'Default landing page or route after authentication. Use "N/A" for roles without a UI.'
        ),
        "accessible_route_patterns": _str_array(
            "Route patterns this role can access. Empty array if the role has no UI access."
        ),
        "authentication_method": _str_field(
            'How this role authenticates: "None" (anon), "Session/JWT", "Session/JWT + role claim", etc.'
        ),
        "middleware_guards": _str_field(
            'Middleware and guards that enforce this role (e.g., "requireAuth() + requireAdmin()").'
        ),
        "permission_checks": _str_field(
            "How permission checks are expressed in code (e.g., \"req.user.role === 'admin'\")."
        ),
        "storage_location": _str_field(
            "Where this role is stored at runtime (JWT claims, session data, etc.)."
        ),
    },
    [
        "name",
        "privilege_level",
        "scope_domain",
        "code_implementation",
        "default_landing_page",
        "accessible_route_patterns",
        "authentication_method",
        "middleware_guards",
        "permission_checks",
        "storage_location",
    ],
)

ROLE_ARCHITECTURE = _obj(
    {
        "roles": {
            "type": "array",
            "items": _ROLE,
            "description": (
                "All distinct privilege levels found in the application. Becomes Sections 7.1 "
                "(Discovered Roles), 7.3 (Role Entry Points), and 7.4 (Role-to-Code Mapping), "
                "split by the renderer per-role."
            ),
        },
        "privilege_lattice": _obj(
            {
                "ordering_diagram": _str_field(
                    'ASCII diagram showing role ordering. Use → for "can access resources of". '
                    'E.g. "anon → user → admin".'
                ),
                "parallel_isolation_notes": _plain_str(
                    "Notes on parallel isolation between roles using ||. E.g. "
                    '"team_admin || dept_admin (both > user, but isolated from each other)". '
                    "Empty string if no parallel isolation exists."
                ),
                "role_switching_notes": _nullable_str(
                    "Optional pointer to impersonation, sudo mode, or role-switching mechanisms documented "
                    "in set_authentication.role_switching_impersonation. Null/omitted if no such mechanisms exist."
                ),
            },
            # role_switching_notes 是 TS Type.Optional → 不在 required
            ["ordering_diagram", "parallel_isolation_notes"],
            desc="The role hierarchy showing dominance and parallel isolation. Becomes Section 7.2.",
        ),
    },
    ["roles", "privilege_lattice"],
)


# ── Section 8 — AuthzCandidatesInputSchema ───────────────────────────────
_PRIORITY_VALUES = ["High", "Medium", "Low"]

_HORIZONTAL_CANDIDATE = _obj(
    {
        "priority": _enum(
            _PRIORITY_VALUES,
            "Priority: High, Medium, or Low, based on data sensitivity (title-case literals).",
        ),
        "endpoint_pattern": _str_field(
            'Endpoint pattern with the object identifier. E.g. "/api/orders/{order_id}".'
        ),
        "object_id_parameter": _str_field(
            'The parameter name that identifies the target object (e.g., "order_id", "user_id").'
        ),
        "data_type": _str_field(
            "Type of data exposed: user_data, financial, admin_config, user_files, etc."
        ),
        "sensitivity": _str_field(
            "One-line description of what is at risk (e.g., \"User can access other users' orders\")."
        ),
    },
    ["priority", "endpoint_pattern", "object_id_parameter", "data_type", "sensitivity"],
)

_VERTICAL_CANDIDATE = _obj(
    {
        "target_role": _str_field(
            "Role required to access this endpoint (the role being escalated to)."
        ),
        "endpoint_pattern": _str_field(
            'Endpoint pattern that requires elevated privileges. E.g. "/admin/*", "/api/admin/users".'
        ),
        "functionality": _str_field(
            "What the endpoint does (e.g., \"Administrative functions\", \"User management\")."
        ),
        "risk_level": _enum(
            _PRIORITY_VALUES, "Risk level: High, Medium, or Low (title-case literals)."
        ),
    },
    ["target_role", "endpoint_pattern", "functionality", "risk_level"],
)

_CONTEXT_CANDIDATE = _obj(
    {
        "workflow": _str_field(
            'Multi-step workflow name (e.g., "Checkout", "Onboarding", "Password Reset").'
        ),
        "endpoint": _str_field(
            'Endpoint that assumes a prior workflow state. E.g. "/api/checkout/confirm".'
        ),
        "expected_prior_state": _str_field(
            "What state should already exist before this endpoint is called."
        ),
        "bypass_potential": _str_field(
            "What an attacker could achieve by skipping the prior state."
        ),
    },
    ["workflow", "endpoint", "expected_prior_state", "bypass_potential"],
)

AUTHZ_CANDIDATES = _obj(
    {
        "horizontal": {
            "type": "array",
            "items": _HORIZONTAL_CANDIDATE,
            "description": (
                "Endpoints with object identifiers that could allow horizontal access to other users' "
                "resources. Becomes Section 8.1."
            ),
        },
        "vertical": {
            "type": "array",
            "items": _VERTICAL_CANDIDATE,
            "description": (
                "Endpoints that require higher privileges and could be targets for vertical escalation. "
                "Becomes Section 8.2. Exclude endpoints intentionally shared across roles."
            ),
        },
        "context": {
            "type": "array",
            "items": _CONTEXT_CANDIDATE,
            "description": "Multi-step workflow endpoints that assume prior steps were completed. Becomes Section 8.3.",
        },
    },
    ["horizontal", "vertical", "context"],
)


# ── Section 9 — InjectionSourcesInputSchema ──────────────────────────────
INJECTION_SOURCES = _obj(
    {
        "applicable": {
            "type": "boolean",
            "description": (
                "False only if the application has no network-accessible code paths reaching dangerous "
                "sinks at all. Otherwise true, even if no sources were found in a given category — "
                'empty arrays mean "scanned this category, no sources found".'
            ),
        },
        "command_injection": _sink_array(
            "Command injection sources: data flowing from a user-controlled origin into a program variable "
            "that is eventually interpolated into a shell or system command string "
            "(within network-accessible code paths)."
        ),
        "sql_injection": _sink_array(
            "SQL injection sources: user-controllable input that reaches a database query string "
            "(within network-accessible code paths)."
        ),
        "lfi_rfi": _sink_array(
            "Local/Remote File Inclusion sources: user-controllable input passed to include/require/load "
            "functions that resolve to filesystem or remote paths (within network-accessible code paths)."
        ),
        "path_traversal": _sink_array(
            "Path traversal sources: user-controllable input that influences file paths in read/write "
            "operations (fopen, readFile, etc.) within network-accessible code paths."
        ),
        "ssti": _sink_array(
            "Server-Side Template Injection sources: user-controllable input embedded in template "
            "expressions or template content within network-accessible code paths."
        ),
        "deserialization": _sink_array(
            "Insecure deserialization sources: user-controllable input passed to deserialization functions "
            "within network-accessible code paths."
        ),
    },
    [
        "applicable",
        "command_injection",
        "sql_injection",
        "lfi_rfi",
        "path_traversal",
        "ssti",
        "deserialization",
    ],
)


# ── RECON_SECTIONS（顺序对齐 TS RECON_ONE_SHOT_TOOLS 8 个 set_* + 末尾 set_endpoints）
RECON_SECTIONS: list[SectionSchema] = [
    _section(
        "set_executive_summary",
        "executive_summary",
        "Record the application's executive summary: purpose, core technology stack, and primary "
        "user-facing components. Call exactly once before terminating. Becomes Section 1 of the rendered "
        "deliverable. Duplicate calls are rejected.",
        EXECUTIVE_SUMMARY,
    ),
    _section(
        "set_technology_stack",
        "technology_stack",
        "Record the technology and service map: frontend, backend, and infrastructure. Call exactly once "
        "before terminating. Becomes Section 2 of the rendered deliverable. Duplicate calls are rejected.",
        TECHNOLOGY_STACK,
    ),
    _section(
        "set_authentication",
        "authentication",
        "Record the authentication and session management architecture: session flow, role assignment, "
        "privilege storage, and role switching/impersonation. Call exactly once before terminating. "
        "Becomes Sections 3, 3.1, 3.2, and 3.3 of the rendered deliverable. Set "
        "role_switching_impersonation.applicable=false (with the other fields null) if no such features "
        "exist. Duplicate calls are rejected.",
        AUTHENTICATION,
    ),
    _section(
        "set_input_vectors",
        "input_vectors",
        "Record potential input vectors grouped by source: URL parameters, POST body fields, HTTP headers, "
        "and cookie values. Call exactly once before terminating. Becomes Section 5 of the rendered "
        "deliverable. Drives downstream vulnerability analysis. Duplicate calls are rejected.",
        INPUT_VECTORS,
    ),
    _section(
        "set_network_map",
        "network_map",
        "Record the network and interaction map: entities, flows, and guards. Call exactly once before "
        "terminating. Becomes Sections 6.1 (Entities), 6.2 (Entity Metadata), 6.3 (Flows), and 6.4 "
        "(Guards Directory) of the rendered deliverable. The renderer splits the entities array into "
        "the 6.1 and 6.2 tables and sorts each array deterministically. Duplicate calls are rejected.",
        NETWORK_MAP,
    ),
    _section(
        "set_role_architecture",
        "role_architecture",
        "Record the role and privilege architecture: discovered roles and the privilege lattice. Call "
        "exactly once before terminating. Becomes Sections 7.1 (Discovered Roles), 7.2 (Privilege Lattice), "
        "7.3 (Role Entry Points), and 7.4 (Role-to-Code Mapping) of the rendered deliverable. The renderer "
        "splits the roles array into the per-section tables. Duplicate calls are rejected.",
        ROLE_ARCHITECTURE,
    ),
    _section(
        "set_authz_candidates",
        "authz_candidates",
        "Record authorization vulnerability candidates: horizontal escalation, vertical escalation, and "
        "context-based candidates. Call exactly once before terminating. Becomes Sections 8.1, 8.2, and "
        "8.3 of the rendered deliverable. Duplicate calls are rejected.",
        AUTHZ_CANDIDATES,
    ),
    _section(
        "set_injection_sources",
        "injection_sources",
        "Record discovered injection sources grouped by vulnerability class. Call exactly once before "
        "terminating. If the application has no network-accessible code paths to dangerous sinks, set "
        'applicable=false; otherwise populate each category array (empty arrays mean "scanned, no sources '
        'of this kind"). Becomes Section 9 of the rendered deliverable. Drives the vuln-injection '
        "agent's todos downstream. Duplicate calls are rejected.",
        INJECTION_SOURCES,
    ),
    SectionSchema(
        tool_name="set_endpoints",
        section_key="endpoints",
        description=(
            "Append one network-accessible API endpoint to the catalog. Call multiple times to build "
            "the full inventory — each call adds one endpoint row. Becomes Section 4 (API Endpoint "
            "Inventory) of the rendered deliverable; the renderer sorts by (path, method) before "
            "rendering, so emission order does not affect output. Exclude development/debug endpoints, "
            "local-only utilities, build tools, or any endpoints not reachable via network requests to "
            "the deployed application."
        ),
        json_schema=ENDPOINTS,
        mode="append",
    ),
]


class ReconCollector(CollectorBase):
    """recon 的 9-section collector（无参构造，自带 RECON_SECTIONS）。

    8 个 set_* 工具（mode="set"，write-once）+ 1 个 set_endpoints（mode="append"，
    多次调累积 endpoint，对齐 TS add_endpoints）。set_endpoints 渲染 §4 (API Endpoint
    Inventory)；其余 8 个分别渲染 §1/§2/§3/§5/§6/§7/§8/§9。
    """

    def __init__(self) -> None:
        super().__init__(RECON_SECTIONS)
