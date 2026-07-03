from shannon_core.models.agents import AgentName, AGENTS, AGENT_PHASE_MAP


def test_attack_chain_agent_registered():
    assert hasattr(AgentName, "ATTACK_CHAIN")
    defn = AGENTS[AgentName.ATTACK_CHAIN]
    assert defn.prompt_template == "attack-chain"
    # 依赖 vuln 产出（attack chain 在 vuln 后跑，吃 vuln queue）
    prereq_names = {p.name for p in defn.prerequisites}
    assert "INJECTION_VULN" in prereq_names or "XSS_VULN" in prereq_names


def test_attack_chain_phase_mapped():
    assert AgentName.ATTACK_CHAIN in AGENT_PHASE_MAP or "attack-chain" in str(AGENT_PHASE_MAP.values())
