from supernova_core.models.agents import AGENT_PHASE_MAP, AgentName


def test_all_agent_names_have_phase_mapping():
    """Every AgentName enum value should have a phase mapping."""
    for agent in AgentName:
        assert agent.value in AGENT_PHASE_MAP, f"Missing phase mapping for {agent.value}"


def test_phase_mapping_values():
    assert AGENT_PHASE_MAP["pre-recon"] == "pre-recon"
    assert AGENT_PHASE_MAP["recon"] == "recon"
    assert AGENT_PHASE_MAP["recon-blackbox"] == "recon"
    assert AGENT_PHASE_MAP["injection-vuln"] == "vulnerability-analysis"
    assert AGENT_PHASE_MAP["injection-exploit"] == "exploitation"
    assert AGENT_PHASE_MAP["report"] == "reporting"


def test_validate_auth_mapped():
    """validate-authentication is a preflight activity, maps to pre-recon."""
    assert AGENT_PHASE_MAP["validate-authentication"] == "pre-recon"




def test_lightweight_llm_agents_have_phase_mapping():
    """spec 2026-08-27 §8：轻量单次调用记账 agent_name 进 phase 汇总
    （AccountedLlmClient 的 finalize 记账名）。"""
    for name, phase in (
        ("track-parity", "vulnerability-analysis"),
        ("poc-gapfill", "exploitation"),
        ("expected-response", "exploitation"),
        ("report-summary", "reporting"),
        ("recon-summary", "recon"),
    ):
        assert AGENT_PHASE_MAP.get(name) == phase, name
