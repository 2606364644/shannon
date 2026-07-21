"""Task A (Plan 2): recon 的 9 个 set_* section schema 移植自 TS recon-collector.ts。

对齐 test_pre_recon.py 风格：tool_name 顺序、section_key 派生、JSON Schema 合法性、
关键 schema spot-check。set_endpoints 是第 9 个工具（append 语义，对齐 TS add_endpoints），
多次调累积 endpoint，渲染 §4 (API Endpoint Inventory)。
"""
from supernova_core.collectors.recon import RECON_SECTIONS, ReconCollector

EXPECTED_TOOLS = [
    "set_executive_summary",
    "set_technology_stack",
    "set_authentication",
    "set_input_vectors",
    "set_network_map",
    "set_role_architecture",
    "set_authz_candidates",
    "set_injection_sources",
    "set_endpoints",
]

EXPECTED_SECTION_KEYS = [
    "executive_summary",
    "technology_stack",
    "authentication",
    "input_vectors",
    "network_map",
    "role_architecture",
    "authz_candidates",
    "injection_sources",
    "endpoints",
]


def test_nine_sections_present():
    names = {s.tool_name for s in RECON_SECTIONS}
    assert names == {
        "set_executive_summary", "set_technology_stack", "set_authentication",
        "set_input_vectors", "set_network_map", "set_role_architecture",
        "set_authz_candidates", "set_injection_sources", "set_endpoints",
    }


def test_recon_sections_in_ts_order():
    # 顺序对齐 TS RECON_ONE_SHOT_TOOLS（8 个 set_*）+ 末尾 set_endpoints（append 工具）
    assert [s.tool_name for s in RECON_SECTIONS] == EXPECTED_TOOLS


def test_section_keys_derived_without_set_prefix():
    assert [s.section_key for s in RECON_SECTIONS] == EXPECTED_SECTION_KEYS


def test_section_keys_distinct():
    keys = [s.section_key for s in RECON_SECTIONS]
    assert len(keys) == len(set(keys))


def test_every_schema_is_valid_json_schema_object():
    for s in RECON_SECTIONS:
        assert s.json_schema["type"] == "object"
        assert "properties" in s.json_schema
        assert "required" in s.json_schema


def test_recon_collector_knows_nine_sections():
    c = ReconCollector()
    assert len(c.section_schemas) == 9
    assert c.tool_names() == EXPECTED_TOOLS
    # 初始未调用：所有工具 skipped
    assert c.get_call_status() == {t: "skipped" for t in EXPECTED_TOOLS}


# ── 各 section schema 字段全量校验（移植 TS *InputSchema）── ────────────────

def test_executive_summary_schema():
    s = next(s for s in RECON_SECTIONS if s.tool_name == "set_executive_summary")
    assert s.json_schema["properties"] == {"text": {"type": "string", "minLength": 1, "description": s.json_schema["properties"]["text"]["description"]}}
    assert s.json_schema["required"] == ["text"]


def test_technology_stack_has_three_string_fields():
    s = next(s for s in RECON_SECTIONS if s.tool_name == "set_technology_stack")
    props = s.json_schema["properties"]
    assert set(props) == {"frontend", "backend", "infrastructure"}
    for k in props:
        assert props[k]["type"] == "string"
        assert props[k]["minLength"] == 1
    assert s.json_schema["required"] == ["frontend", "backend", "infrastructure"]


def test_authentication_has_four_nested_groups():
    s = next(s for s in RECON_SECTIONS if s.tool_name == "set_authentication")
    props = s.json_schema["properties"]
    assert set(props) == {"session_flow", "role_assignment", "privilege_storage", "role_switching_impersonation"}
    # 每个组都是 object
    for k in props:
        assert props[k]["type"] == "object"
    # 3.1 role_assignment 四字段
    ra = props["role_assignment"]["properties"]
    assert {"role_determination", "default_role", "role_upgrade_path", "code_implementation"} <= set(ra)
    # 3.3 role_switching_impersonation: applicable 必填 boolean + 4 nullable string
    rsi = props["role_switching_impersonation"]["properties"]
    assert rsi["applicable"] == {"type": "boolean", "description": rsi["applicable"]["description"]}
    for k in ("impersonation_features", "role_switching", "audit_trail", "code_implementation"):
        assert rsi[k]["anyOf"] == [{"type": "string"}, {"type": "null"}]
    # 全部 5 字段在 required（TS applicable+4 nullable 全在 required，nullable 通过 anyOf 表达）
    assert set(props["role_switching_impersonation"]["required"]) == {
        "applicable", "impersonation_features", "role_switching", "audit_trail", "code_implementation",
    }


def test_input_vectors_has_four_string_arrays():
    s = next(s for s in RECON_SECTIONS if s.tool_name == "set_input_vectors")
    props = s.json_schema["properties"]
    assert set(props) == {"url_parameters", "post_body_fields", "http_headers", "cookie_values"}
    for k in props:
        assert props[k]["type"] == "array"
        assert props[k]["items"]["type"] == "string"
        assert props[k]["items"]["minLength"] == 1
    assert s.json_schema["required"] == ["url_parameters", "post_body_fields", "http_headers", "cookie_values"]


def test_network_map_has_entities_flows_guards_no_endpoints():
    # ★ endpoints 是独立 append section（set_endpoints），不在 set_network_map payload
    s = next(s for s in RECON_SECTIONS if s.tool_name == "set_network_map")
    props = s.json_schema["properties"]
    assert set(props) == {"entities", "flows", "guards"}
    assert "endpoints" not in props  # 显式断言：endpoints 不在此（独立 set_endpoints section）
    assert s.json_schema["required"] == ["entities", "flows", "guards"]
    # Entity 必填字段
    entity = props["entities"]["items"]
    assert set(entity["properties"]) >= {
        "title", "type", "zone", "tech", "data", "notes", "metadata",
    }
    assert set(entity["required"]) == {"title", "type", "zone", "tech", "data", "notes", "metadata"}
    # type/zone 是 enum（TS stringEnum）
    assert entity["properties"]["type"]["enum"] == [
        "ExternAsset", "Service", "Identity", "DataStore", "AdminPlane", "ThirdParty",
    ]
    # Flow 必填字段
    flow = props["flows"]["items"]
    assert set(flow["properties"]) == {"from", "to", "channel", "path_port", "guards", "touches"}
    # Guard 必填字段
    guard = props["guards"]["items"]
    assert set(guard["properties"]) == {"name", "category", "statement"}


def test_role_architecture_has_roles_and_privilege_lattice():
    s = next(s for s in RECON_SECTIONS if s.tool_name == "set_role_architecture")
    props = s.json_schema["properties"]
    assert set(props) == {"roles", "privilege_lattice"}
    role = props["roles"]["items"]
    # Role 全字段（TS RoleSchema）
    assert set(role["properties"]) == {
        "name", "privilege_level", "scope_domain", "code_implementation",
        "default_landing_page", "accessible_route_patterns", "authentication_method",
        "middleware_guards", "permission_checks", "storage_location",
    }
    # privilege_level 是 integer 0..10
    pl = role["properties"]["privilege_level"]
    assert pl["type"] == "integer"
    assert pl["minimum"] == 0
    assert pl["maximum"] == 10
    # privilege_lattice 子结构
    lat = props["privilege_lattice"]["properties"]
    assert set(lat) == {"ordering_diagram", "parallel_isolation_notes", "role_switching_notes"}
    # role_switching_notes 是 Optional（不在 required）
    assert "role_switching_notes" not in props["privilege_lattice"]["required"]


def test_authz_candidates_has_three_arrays():
    s = next(s for s in RECON_SECTIONS if s.tool_name == "set_authz_candidates")
    props = s.json_schema["properties"]
    assert set(props) == {"horizontal", "vertical", "context"}
    h = props["horizontal"]["items"]["properties"]
    assert set(h) == {"priority", "endpoint_pattern", "object_id_parameter", "data_type", "sensitivity"}
    assert h["priority"]["enum"] == ["High", "Medium", "Low"]
    v = props["vertical"]["items"]["properties"]
    assert set(v) == {"target_role", "endpoint_pattern", "functionality", "risk_level"}
    c = props["context"]["items"]["properties"]
    assert set(c) == {"workflow", "endpoint", "expected_prior_state", "bypass_potential"}


def test_injection_sources_has_applicable_and_six_sink_arrays():
    s = next(s for s in RECON_SECTIONS if s.tool_name == "set_injection_sources")
    props = s.json_schema["properties"]
    assert props["applicable"] == {"type": "boolean", "description": props["applicable"]["description"]}
    expected = ["command_injection", "sql_injection", "lfi_rfi", "path_traversal", "ssti", "deserialization"]
    for k in expected:
        assert props[k]["type"] == "array"
        # items 是 SinkRef（对齐 pre-recon SINK_REF：location + sink_function）
        item = props[k]["items"]
        assert item["type"] == "object"
        assert "location" in item["properties"]
        assert "sink_function" in item["properties"]
    # required = applicable + 6 sink 类
    assert s.json_schema["required"] == ["applicable"] + expected


def test_sink_ref_items_reuse_pre_recon_sink_ref_shape():
    # injection_sources 的 sink 数组 item 必须与 pre-recon SINK_REF 同构
    from supernova_core.collectors.pre_recon import SINK_REF
    s = next(s for s in RECON_SECTIONS if s.tool_name == "set_injection_sources")
    item = s.json_schema["properties"]["command_injection"]["items"]
    # location/sink_function 必填，notes 可选 anyOf string|null
    assert item["properties"]["location"] == SINK_REF["properties"]["location"]
    assert item["properties"]["sink_function"] == SINK_REF["properties"]["sink_function"]
    assert item["required"] == ["location", "sink_function"]


# ── Section 4 — set_endpoints（append 语义，对齐 TS add_endpoints） ────────

def test_endpoints_is_append_mode():
    # set_endpoints 的 SectionSchema.mode == "append"（其余 8 个 == "set"）
    endpoints = next(s for s in RECON_SECTIONS if s.tool_name == "set_endpoints")
    assert endpoints.mode == "append"
    assert endpoints.section_key == "endpoints"
    others = [s for s in RECON_SECTIONS if s.tool_name != "set_endpoints"]
    for s in others:
        assert s.mode == "set", f"{s.tool_name} should be mode=set (default)"


def test_endpoints_item_schema_six_columns():
    # ENDPOINTS item schema 6 字段对齐 recon.txt §4 表头：
    # Method | Endpoint Path | Required Role | Object ID Parameters |
    # Authorization Mechanism | Description & Code Pointer
    s = next(s for s in RECON_SECTIONS if s.tool_name == "set_endpoints")
    props = s.json_schema["properties"]
    assert set(props) == {
        "method",
        "path",
        "required_role",
        "object_id_parameters",
        "authorization_mechanism",
        "description_code_pointer",
    }
    # 全 6 字段都是 string type
    for k in props:
        assert props[k]["type"] == "string", f"{k} should be string"
    # 全 6 字段 required（对齐 §4 表格每行都填）
    assert set(s.json_schema["required"]) == set(props)


def test_recon_collector_append_endpoints():
    c = ReconCollector()
    item1 = {
        "method": "GET",
        "path": "/api/users/{user_id}",
        "required_role": "user",
        "object_id_parameters": "user_id",
        "authorization_mechanism": "Bearer Token + ownership check",
        "description_code_pointer": "Fetches user profile. See users.controller.ts:45.",
    }
    item2 = {
        "method": "POST",
        "path": "/api/auth/login",
        "required_role": "anon",
        "object_id_parameters": "None",
        "authorization_mechanism": "None",
        "description_code_pointer": "Handles login. See auth.controller.ts:12.",
    }
    c.append_section("set_endpoints", item1)
    c.append_section("set_endpoints", item2)
    out = c.get_all()
    assert "endpoints" in out
    assert len(out["endpoints"]) == 2
    assert out["endpoints"][0]["method"] == "GET"
    assert out["endpoints"][1]["method"] == "POST"
    # get_call_status 报 called
    assert c.get_call_status()["set_endpoints"] == "called"
    # write-once set_* 工具仍可正常调（共存）
    c.set_section("set_executive_summary", {"text": "hi"})
    assert c.get_call_status()["set_executive_summary"] == "called"
