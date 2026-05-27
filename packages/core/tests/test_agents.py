from shannon_core.models.agents import AgentName, AgentDefinition, AGENTS, VulnType

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
