"""render_recon 渲染测试（TDD）。

验证 render_recon(data) 把 ReconCollector 的 9-section payload 渲染成
recon_deliverable.md，结构对齐 prompts/recon.txt §0-9。
"""
from shannon_core.renderers.recon import render_recon


# ── §0 + all-skipped ────────────────────────────────────────────────────
def test_all_missing_renders_placeholders():
    md = render_recon({})
    assert md.startswith("# Reconnaissance Deliverable:")
    assert "## 0) HOW TO READ THIS" in md
    for n in range(1, 10):
        assert f"## {n}." in md
    for tool in [
        "set_executive_summary", "set_technology_stack", "set_authentication",
        "set_endpoints", "set_input_vectors", "set_network_map",
        "set_role_architecture", "set_authz_candidates", "set_injection_sources",
    ]:
        assert f"`{tool}` was not called" in md


def test_how_to_read_this_static():
    md = render_recon({})
    assert "HOW TO READ THIS" in md
    assert "Section 4 (API Endpoint Inventory)" in md
    assert "Section 6.4 (Guards Directory)" in md
    assert "Section 7 (Role & Privilege Architecture)" in md
    assert "Section 8 (Authorization Vulnerability Candidates)" in md
    assert "Priority Order for Testing" in md


# ── §1 Executive Summary ────────────────────────────────────────────────
def test_executive_summary_rendered():
    md = render_recon({"executive_summary": {"text": "The app is a Next.js storefront."}})
    assert "## 1. Executive Summary" in md
    assert "The app is a Next.js storefront." in md


# ── §2 Technology & Service Map ─────────────────────────────────────────
def test_technology_stack_three_fields():
    md = render_recon({"technology_stack": {
        "frontend": "Next.js + react-query",
        "backend": "Node/Express + Prisma",
        "infrastructure": "AWS ECS + RDS Postgres",
    }})
    assert "## 2. Technology & Service Map" in md
    assert "**Frontend:** Next.js + react-query" in md
    assert "**Backend:** Node/Express + Prisma" in md
    assert "**Infrastructure:** AWS ECS + RDS Postgres" in md


# ── §3 Authentication & Session Management Flow ─────────────────────────
def test_authentication_subsections():
    md = render_recon({"authentication": {
        "session_flow": {
            "entry_points": "/login, /register",
            "mechanism": "POST credentials → JWT in cookie",
            "code_pointers": "auth.controller.ts:45",
        },
        "role_assignment": {
            "role_determination": "JWT claim role",
            "default_role": "user",
            "role_upgrade_path": "admin approval",
            "code_implementation": "auth/roles.ts:12",
        },
        "privilege_storage": {
            "storage_location": "JWT claims",
            "validation_points": "requireAuth middleware",
            "cache_session_persistence": "15min access token",
            "code_pointers": "middleware/auth.ts:8",
        },
        "role_switching_impersonation": {
            "applicable": False,
            "impersonation_features": None,
            "role_switching": None,
            "audit_trail": None,
            "code_implementation": None,
        },
    }})
    assert "## 3. Authentication & Session Management Flow" in md
    assert "### 3.1 Role Assignment Process" in md
    assert "### 3.2 Privilege Storage & Validation" in md
    assert "### 3.3 Role Switching & Impersonation" in md
    assert "[not applicable]" in md
    assert "POST credentials → JWT in cookie" in md
    assert "admin approval" in md
    assert "JWT claims" in md
    assert "requireAuth middleware" in md


def test_authentication_role_switching_applicable_true():
    md = render_recon({"authentication": {
        "session_flow": {"entry_points": "/login", "mechanism": "session", "code_pointers": "a.ts"},
        "role_assignment": {"role_determination": "db", "default_role": "user",
                            "role_upgrade_path": "n/a", "code_implementation": "b.ts"},
        "privilege_storage": {"storage_location": "session", "validation_points": "middleware",
                              "cache_session_persistence": "session", "code_pointers": "c.ts"},
        "role_switching_impersonation": {
            "applicable": True,
            "impersonation_features": "admin impersonate user",
            "role_switching": "sudo mode",
            "audit_trail": "logged in audit.log",
            "code_implementation": "impersonate.ts:5",
        },
    }})
    assert "admin impersonate user" in md
    assert "sudo mode" in md
    assert "logged in audit.log" in md
    assert "impersonate.ts:5" in md
    assert "[not applicable]" not in md


# ── §4 API Endpoint Inventory (set_endpoints append) ────────────────────
def test_endpoints_table_from_append():
    md = render_recon({"endpoints": [
        {"method": "POST", "path": "/api/auth/login", "required_role": "anon",
         "object_id_parameters": "None", "authorization_mechanism": "None",
         "description_code_pointer": "Login handler. See auth.controller.ts."},
        {"method": "GET", "path": "/api/users/{user_id}", "required_role": "user",
         "object_id_parameters": "user_id", "authorization_mechanism": "Bearer + ownership",
         "description_code_pointer": "Fetch user. See users.controller.ts:45."},
    ]})
    assert "## 4. API Endpoint Inventory" in md
    assert "| Method | Endpoint Path | Required Role |" in md
    assert "| Object ID Parameters | Authorization Mechanism | Description & Code Pointer |" in md
    assert "/api/auth/login" in md
    assert "/api/users/{user_id}" in md
    assert "Bearer + ownership" in md
    assert "users.controller.ts:45" in md


def test_endpoints_empty_placeholder():
    md = render_recon({})
    assert "## 4. API Endpoint Inventory" in md
    assert "`set_endpoints` was not called" in md


# ── §5 Potential Input Vectors ──────────────────────────────────────────
def test_input_vectors_four_arrays():
    md = render_recon({"input_vectors": {
        "url_parameters": ["?redirect_url= @ auth.ts:88", "?user_id= @ users.ts:12"],
        "post_body_fields": ["username @ login.ts:34"],
        "http_headers": ["X-Forwarded-For"],
        "cookie_values": ["preferences_cookie @ prefs.ts:22"],
    }})
    assert "## 5. Potential Input Vectors for Vulnerability Analysis" in md
    assert "**URL Parameters:**" in md
    assert "?redirect_url= @ auth.ts:88" in md
    assert "**POST Body Fields (JSON/Form):**" in md
    assert "username @ login.ts:34" in md
    assert "**HTTP Headers:**" in md
    assert "X-Forwarded-For" in md
    assert "**Cookie Values:**" in md
    assert "preferences_cookie @ prefs.ts:22" in md


def test_input_vectors_empty_arrays_none_identified():
    md = render_recon({"input_vectors": {
        "url_parameters": [], "post_body_fields": [], "http_headers": [], "cookie_values": [],
    }})
    assert "*(none identified)*" in md


# ── §6 Network & Interaction Map ────────────────────────────────────────
def test_network_map_subtables():
    md = render_recon({"network_map": {
        "entities": [
            {"title": "ExampleWebApp", "type": "Service", "zone": "App", "tech": "Node/Express",
             "data": ["PII", "Tokens"], "notes": "Main backend",
             "metadata": [{"key": "Hosts", "value": "localhost:3000"},
                          {"key": "Auth", "value": "Bearer"}]},
        ],
        "flows": [
            {"from": "User Browser", "to": "ExampleWebApp", "channel": "HTTPS",
             "path_port": ":443 /api/login", "guards": ["auth:user"], "touches": ["PII"]},
        ],
        "guards": [
            {"name": "auth:user", "category": "Auth", "statement": "Requires valid user session."},
        ],
    }})
    assert "### 6.1 Entities" in md
    assert "| Title | Type | Zone | Tech | Data | Notes |" in md
    assert "ExampleWebApp" in md
    assert "PII, Tokens" in md  # data list joined with ", "
    assert "### 6.2 Entity Metadata" in md
    assert "| Title | Metadata |" in md
    assert "Hosts: localhost:3000; Auth: Bearer" in md  # metadata joined with "; "
    assert "### 6.3 Flows (Connections)" in md
    assert "| FROM → TO | Channel | Path/Port | Guards | Touches |" in md
    assert "User Browser → ExampleWebApp" in md  # from → to
    assert "auth:user" in md  # guards joined
    assert "PII" in md  # touches joined
    assert "### 6.4 Guards Directory" in md
    assert "| Guard Name | Category | Statement |" in md
    assert "Requires valid user session." in md


def test_network_map_empty_subarrays_none_identified():
    md = render_recon({"network_map": {
        "entities": [], "flows": [], "guards": [],
    }})
    assert "*None identified.*" in md


# ── §7 Role & Privilege Architecture ────────────────────────────────────
def test_role_architecture_subsections():
    md = render_recon({"role_architecture": {
        "roles": [
            {"name": "anon", "privilege_level": 0, "scope_domain": "Global",
             "code_implementation": "No authentication required",
             "default_landing_page": "/", "accessible_route_patterns": ["/", "/login"],
             "authentication_method": "None", "middleware_guards": "none",
             "permission_checks": "none", "storage_location": "n/a"},
            {"name": "admin", "privilege_level": 5, "scope_domain": "Global",
             "code_implementation": "requireAdmin()",
             "default_landing_page": "/admin", "accessible_route_patterns": ["/admin/*"],
             "authentication_method": "Session/JWT + role claim",
             "middleware_guards": "requireAuth(), requireAdmin()",
             "permission_checks": "req.user.role === 'admin'",
             "storage_location": "JWT claims"},
        ],
        "privilege_lattice": {
            "ordering_diagram": "anon → user → admin",
            "parallel_isolation_notes": "none",
            "role_switching_notes": "See impersonation in §3.3",
        },
    }})
    assert "### 7.1 Discovered Roles" in md
    assert "| Role Name | Privilege Level | Scope/Domain | Code Implementation |" in md
    assert "anon" in md and "admin" in md
    assert "### 7.2 Privilege Lattice" in md
    assert "```" in md  # fenced code block
    assert "anon → user → admin" in md
    assert "See impersonation in §3.3" in md  # role_switching_notes
    assert "### 7.3 Role Entry Points" in md
    assert "| Role | Default Landing Page | Accessible Route Patterns | Authentication Method |" in md
    assert "/, /login" in md  # route patterns joined
    assert "### 7.4 Role-to-Code Mapping" in md
    assert "| Role | Middleware/Guards | Permission Checks | Storage Location |" in md
    assert "requireAuth(), requireAdmin()" in md


def test_role_architecture_empty_roles_none_identified():
    md = render_recon({"role_architecture": {
        "roles": [],
        "privilege_lattice": {"ordering_diagram": "anon → user", "parallel_isolation_notes": ""},
    }})
    assert "*None identified.*" in md


# ── §8 Authorization Vulnerability Candidates ───────────────────────────
def test_authz_candidates_three_arrays():
    md = render_recon({"authz_candidates": {
        "horizontal": [
            {"priority": "High", "endpoint_pattern": "/api/orders/{order_id}",
             "object_id_parameter": "order_id", "data_type": "financial",
             "sensitivity": "User can access others' orders"},
        ],
        "vertical": [
            {"target_role": "admin", "endpoint_pattern": "/admin/*",
             "functionality": "Admin functions", "risk_level": "High"},
        ],
        "context": [
            {"workflow": "Checkout", "endpoint": "/api/checkout/confirm",
             "expected_prior_state": "Cart populated",
             "bypass_potential": "Direct confirmation"},
        ],
    }})
    assert "### 8.1 Horizontal Privilege Escalation Candidates" in md
    assert "| Priority | Endpoint Pattern | Object ID Parameter | Data Type | Sensitivity |" in md
    assert "/api/orders/{order_id}" in md
    assert "User can access others' orders" in md
    assert "### 8.2 Vertical Privilege Escalation Candidates" in md
    assert "| Target Role | Endpoint Pattern | Functionality | Risk Level |" in md
    assert "/admin/*" in md
    assert "### 8.3 Context-Based Authorization Candidates" in md
    assert "| Workflow | Endpoint | Expected Prior State | Bypass Potential |" in md
    assert "Checkout" in md
    assert "Direct confirmation" in md


def test_authz_candidates_empty_arrays_none_identified():
    md = render_recon({"authz_candidates": {
        "horizontal": [], "vertical": [], "context": [],
    }})
    assert "*None identified.*" in md


# ── §9 Injection Sources ────────────────────────────────────────────────
def test_injection_sources_applicable_false_na():
    md = render_recon({"injection_sources": {"applicable": False}})
    assert "## 9. Injection Sources" in md
    assert "N/A — no network-accessible code paths to dangerous sinks." in md


def test_injection_sources_sink_lists():
    md = render_recon({"injection_sources": {
        "applicable": True,
        "command_injection": [{"location": "exec.ts:34", "sink_function": "exec", "notes": "user input"}],
        "sql_injection": [{"location": "db.ts:12", "sink_function": "query"}],
        "lfi_rfi": [],
        "path_traversal": [],
        "ssti": [],
        "deserialization": [],
    }})
    assert "## 9. Injection Sources" in md
    assert "**Command Injection:**" in md
    assert "`exec`" in md
    assert "exec.ts:34" in md
    assert "(user input)" in md
    assert "**SQL Injection:**" in md
    assert "`query`" in md
    assert "db.ts:12" in md
    assert "**LFI/RFI:**" in md
    assert "**Path Traversal:**" in md
    assert "**SSTI:**" in md
    assert "**Deserialization:**" in md
    assert "scanned, no sources of this kind found" in md  # empty array placeholder


# ── full payload stability ──────────────────────────────────────────────
def test_full_payload_byte_stability():
    md = render_recon({
        "executive_summary": {"text": "OVERVIEW."},
        "technology_stack": {"frontend": "F", "backend": "B", "infrastructure": "I"},
        "authentication": {
            "session_flow": {"entry_points": "/login", "mechanism": "jwt", "code_pointers": "a.ts"},
            "role_assignment": {"role_determination": "claim", "default_role": "user",
                                "role_upgrade_path": "n/a", "code_implementation": "b.ts"},
            "privilege_storage": {"storage_location": "jwt", "validation_points": "mw",
                                  "cache_session_persistence": "15m", "code_pointers": "c.ts"},
            "role_switching_impersonation": {"applicable": False, "impersonation_features": None,
                                             "role_switching": None, "audit_trail": None,
                                             "code_implementation": None},
        },
        "endpoints": [{"method": "GET", "path": "/api/me", "required_role": "user",
                       "object_id_parameters": "None", "authorization_mechanism": "Bearer",
                       "description_code_pointer": "me.ts"}],
        "input_vectors": {"url_parameters": ["?x="], "post_body_fields": ["y"],
                          "http_headers": ["H"], "cookie_values": ["c"]},
        "network_map": {"entities": [], "flows": [], "guards": []},
        "role_architecture": {"roles": [],
                              "privilege_lattice": {"ordering_diagram": "anon → user",
                                                    "parallel_isolation_notes": ""}},
        "authz_candidates": {"horizontal": [], "vertical": [], "context": []},
        "injection_sources": {"applicable": False},
    })
    assert md.startswith("# Reconnaissance Deliverable:")
    assert md.endswith("\n")
    assert md.count("# Reconnaissance Deliverable:") == 1
    assert "OVERVIEW." in md


# ── schema 违规止血:LLM 把 object 填成 str 时不崩(2026-07-20) ──────────────
# 复现 sentinel_dashboard recon attempt 1 崩溃(AttributeError: 'str' object has
# no attribute 'get'):collector set_section 不校验类型,renderer 兜底降级而非崩。
def test_authentication_session_flow_as_str_does_not_crash():
    md = render_recon({"authentication": {"session_flow": "prose instead of object"}})
    assert "## 3. Authentication & Session Management Flow" in md


def test_authentication_as_str_degrades_to_placeholder():
    md = render_recon({"authentication": "whole section is prose"})
    assert "## 3. Authentication & Session Management Flow" in md
    assert "`set_authentication` was not called" in md  # 降级 placeholder


def test_endpoints_str_elements_skipped():
    md = render_recon({"endpoints": ["not a dict", {"method": "GET", "path": "/x"}]})
    assert "/x" in md
    assert "not a dict" not in md  # str 元素被 as_dict_list 跳过


def test_network_map_as_str_does_not_crash():
    md = render_recon({"network_map": "should be object"})
    assert "## 6. Network & Interaction Map" in md


def test_injection_sources_sink_str_elements_skipped():
    md = render_recon({"injection_sources": {
        "sql_injection": [{"sink_function": "eval", "location": "a:1"}, "str elem"]}})
    assert "eval" in md
    assert "str elem" not in md
