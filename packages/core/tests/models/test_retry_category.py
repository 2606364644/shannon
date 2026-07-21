from supernova_core.models.retry import agent_retry_category


def test_vuln_agents_map_to_vuln():
    for name in ("injection-vuln", "xss-vuln", "auth-vuln", "ssrf-vuln", "authz-vuln"):
        assert agent_retry_category(name) == "vuln"


def test_non_vuln_agents_map_to_standard():
    for name in ("pre-recon", "recon", "report", "validate-authentication"):
        assert agent_retry_category(name) == "standard"
