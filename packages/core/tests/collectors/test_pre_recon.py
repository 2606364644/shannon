from shannon_core.collectors.pre_recon import PRE_RECON_SECTIONS, PreReconCollector

EXPECTED_TOOLS = [
    "set_executive_summary",
    "set_application_intelligence",
    "set_auth_deep_dive",
    "set_codebase_indexing",
    "set_critical_file_paths",
    "set_xss_sinks",
    "set_ssrf_sinks",
]


def test_pre_recon_has_seven_tools_in_ts_order():
    assert [s.tool_name for s in PRE_RECON_SECTIONS] == EXPECTED_TOOLS


def test_section_key_is_tool_name_without_set_prefix():
    assert [s.section_key for s in PRE_RECON_SECTIONS] == [
        "executive_summary", "application_intelligence", "auth_deep_dive",
        "codebase_indexing", "critical_file_paths", "xss_sinks", "ssrf_sinks",
    ]


def test_every_schema_is_valid_json_schema_object():
    for s in PRE_RECON_SECTIONS:
        assert s.json_schema["type"] == "object"
        assert "properties" in s.json_schema


def test_executive_summary_schema():
    s = next(s for s in PRE_RECON_SECTIONS if s.tool_name == "set_executive_summary")
    assert s.json_schema["properties"] == {"text": {"type": "string", "minLength": 1}}
    assert s.json_schema["required"] == ["text"]


def test_application_intelligence_has_four_nested_groups():
    s = next(s for s in PRE_RECON_SECTIONS if s.tool_name == "set_application_intelligence")
    props = s.json_schema["properties"]
    assert set(props) == {"architecture", "data_security", "attack_surface", "infrastructure"}
    # attack_surface 必须有 4 字段（旧草稿漏了 2 个）
    attack = props["attack_surface"]["properties"]
    assert {"external_entry_points", "internal_service_communication",
            "input_validation_patterns", "background_processing"} <= set(attack)


def test_xss_sinks_schema_has_applicable_and_five_sink_arrays():
    s = next(s for s in PRE_RECON_SECTIONS if s.tool_name == "set_xss_sinks")
    props = s.json_schema["properties"]
    assert props["applicable"] == {"type": "boolean"}
    for ctx_name in ["html_body", "html_attribute", "javascript", "css", "url"]:
        assert props[ctx_name]["type"] == "array"


def test_ssrf_sinks_schema_has_thirteen_sink_arrays():
    s = next(s for s in PRE_RECON_SECTIONS if s.tool_name == "set_ssrf_sinks")
    props = s.json_schema["properties"]
    assert props["applicable"] == {"type": "boolean"}
    expected = [
        "http_clients", "raw_sockets", "url_openers", "redirect_handlers",
        "headless_browsers", "media_processors", "link_preview", "webhook_testers",
        "sso_oidc_discovery", "importers", "package_installers",
        "monitoring_and_health", "cloud_metadata",
    ]
    for k in expected:
        assert props[k]["type"] == "array"
    assert len(expected) == 13
    # Lockstep guard (final-review Finding 1):required 必须恰好是 applicable + 这 13 个
    # sink key(顺序也锁定),防 properties/required 双源漂移。
    assert s.json_schema["required"] == ["applicable"] + expected


def test_critical_file_paths_has_nine_categories():
    s = next(s for s in PRE_RECON_SECTIONS if s.tool_name == "set_critical_file_paths")
    props = s.json_schema["properties"]
    assert set(props) == {
        "configuration", "authentication_and_authorization", "api_and_routing",
        "data_models_and_db", "dependency_manifests", "sensitive_data_and_secrets",
        "middleware_and_input_validation", "logging_and_monitoring", "infrastructure_and_deployment",
    }


def test_pre_recon_collector_instance_has_seven_sections():
    c = PreReconCollector()
    assert c.tool_names() == EXPECTED_TOOLS
    assert c.get_call_status() == {t: "skipped" for t in EXPECTED_TOOLS}


def test_sink_ref_items_schema_has_location_and_sink_function():
    s = next(s for s in PRE_RECON_SECTIONS if s.tool_name == "set_xss_sinks")
    item = s.json_schema["properties"]["html_body"]["items"]
    assert item["type"] == "object"
    assert "location" in item["properties"]
    assert "sink_function" in item["properties"]


def test_make_collector_dispatches_pre_recon_and_returns_none_for_others():
    from shannon_core.collectors import make_collector
    from shannon_core.models.agents import AgentName

    pre = make_collector(AgentName.PRE_RECON)
    assert isinstance(pre, PreReconCollector)
    assert pre.tool_names() == EXPECTED_TOOLS

    assert make_collector(AgentName.RECON) is None
