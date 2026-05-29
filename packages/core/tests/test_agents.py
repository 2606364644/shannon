from shannon_core.models.agents import AgentName, AgentDefinition, AGENTS, VulnType, PLAYWRIGHT_SESSION_MAPPING

def test_agent_name_values():
    assert AgentName.PRE_RECON == "pre-recon"
    assert AgentName.RECON == "recon"
    assert AgentName.INJECTION_VULN == "injection-vuln"
    assert AgentName.XSS_VULN == "xss-vuln"
    assert AgentName.AUTH_VULN == "auth-vuln"
    assert AgentName.SSRF_VULN == "ssrf-vuln"
    assert AgentName.AUTHZ_VULN == "authz-vuln"

def test_agent_definition_frozen():
    defn = AgentDefinition(
        name=AgentName.PRE_RECON,
        display_name="Pre-recon",
        prerequisites=[],
        prompt_template="pre-recon-code",
        deliverable_filename="pre_recon_deliverable.md",
        model_tier="large",
    )
    assert defn.name == AgentName.PRE_RECON
    assert defn.model_tier == "large"

def test_agents_registry_has_all_whitebox_agents():
    expected = [AgentName.PRE_RECON, AgentName.RECON, AgentName.INJECTION_VULN,
                AgentName.XSS_VULN, AgentName.AUTH_VULN, AgentName.SSRF_VULN,
                AgentName.AUTHZ_VULN]
    for name in expected:
        assert name in AGENTS, f"Missing agent: {name}"

def test_agents_prerequisites_valid():
    for name, defn in AGENTS.items():
        for prereq in defn.prerequisites:
            assert prereq in AGENTS, f"Agent {name} has invalid prerequisite: {prereq}"

def test_pre_recon_has_no_prerequisites():
    assert AGENTS[AgentName.PRE_RECON].prerequisites == []

def test_recon_depends_on_pre_recon():
    assert AgentName.PRE_RECON in AGENTS[AgentName.RECON].prerequisites

def test_vuln_agents_depend_on_recon():
    for agent_name in [AgentName.INJECTION_VULN, AgentName.XSS_VULN,
                        AgentName.AUTH_VULN, AgentName.SSRF_VULN, AgentName.AUTHZ_VULN]:
        assert AgentName.RECON in AGENTS[agent_name].prerequisites, f"{agent_name} missing recon prereq"

def test_vuln_type():
    vt: VulnType = "injection"
    assert vt == "injection"

def test_blackbox_agent_name_values():
    assert AgentName.RECON_BLACKBOX == "recon-blackbox"
    assert AgentName.INJECTION_EXPLOIT == "injection-exploit"
    assert AgentName.XSS_EXPLOIT == "xss-exploit"
    assert AgentName.AUTH_EXPLOIT == "auth-exploit"
    assert AgentName.SSRF_EXPLOIT == "ssrf-exploit"
    assert AgentName.AUTHZ_EXPLOIT == "authz-exploit"
    assert AgentName.REPORT == "report"

def test_blackbox_agents_in_registry():
    expected_blackbox = [
        AgentName.RECON_BLACKBOX, AgentName.INJECTION_EXPLOIT,
        AgentName.XSS_EXPLOIT, AgentName.AUTH_EXPLOIT,
        AgentName.SSRF_EXPLOIT, AgentName.AUTHZ_EXPLOIT,
        AgentName.REPORT,
    ]
    for name in expected_blackbox:
        assert name in AGENTS, f"Missing blackbox agent: {name}"

def test_exploit_agents_have_correct_prerequisites():
    for agent_name in [AgentName.INJECTION_EXPLOIT, AgentName.XSS_EXPLOIT,
                        AgentName.AUTH_EXPLOIT, AgentName.SSRF_EXPLOIT,
                        AgentName.AUTHZ_EXPLOIT]:
        defn = AGENTS[agent_name]
        assert AgentName.RECON in defn.prerequisites or AgentName.RECON_BLACKBOX in defn.prerequisites

def test_recon_blackbox_has_no_prerequisites():
    assert AGENTS[AgentName.RECON_BLACKBOX].prerequisites == []

def test_report_agent_prerequisites():
    defn = AGENTS[AgentName.REPORT]
    assert len(defn.prerequisites) > 0

def test_misconfig_vuln_agent_name():
    assert AgentName.MISCONFIG_VULN == "misconfig-vuln"

def test_misconfig_exploit_agent_name():
    assert AgentName.MISCONFIG_EXPLOIT == "misconfig-exploit"

def test_misconfig_vuln_in_registry():
    assert AgentName.MISCONFIG_VULN in AGENTS

def test_misconfig_exploit_in_registry():
    assert AgentName.MISCONFIG_EXPLOIT in AGENTS

def test_misconfig_vuln_prerequisites():
    defn = AGENTS[AgentName.MISCONFIG_VULN]
    assert AgentName.RECON in defn.prerequisites

def test_misconfig_exploit_prerequisites():
    defn = AGENTS[AgentName.MISCONFIG_EXPLOIT]
    assert AgentName.MISCONFIG_VULN in defn.prerequisites

def test_report_includes_misconfig_exploit():
    defn = AGENTS[AgentName.REPORT]
    assert AgentName.MISCONFIG_EXPLOIT in defn.prerequisites

def test_playwright_session_mapping_exists():
    assert len(PLAYWRIGHT_SESSION_MAPPING) > 0

def test_playwright_session_mapping_all_agents_mapped():
    for name in AGENTS:
        assert name.value in PLAYWRIGHT_SESSION_MAPPING, f"Missing session mapping for {name.value}"

def test_session_mapping_values_unique():
    values = list(PLAYWRIGHT_SESSION_MAPPING.values())
    for v in values:
        assert v.startswith("agent"), f"Unexpected session name: {v}"
