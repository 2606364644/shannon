"""Phase 2 B 拓扑（spec 2026-08-19 §3.5）：vuln agent 停传 structured_output_schema。

queue 数据走 collector（submit_finding），末条大 JSON 通道停用——CLI --json-schema
与 collected_text 兜底对 vuln 不再激活，断流面消灭。
"""
from supernova_core.models.agents import AgentName
from supernova_whitebox.pipeline.activities import _vuln_output_schema

VULN_AGENTS = [
    AgentName.INJECTION_VULN, AgentName.XSS_VULN, AgentName.AUTH_VULN,
    AgentName.SSRF_VULN, AgentName.AUTHZ_VULN,
]


def test_all_vuln_agents_get_no_output_schema():
    for a in VULN_AGENTS:
        assert _vuln_output_schema(a) is None, a


def test_non_vuln_agents_unchanged_none():
    """exploit / 非 vuln agent 原行为就是 None（排除 *-exploit 覆写 queue）。"""
    assert _vuln_output_schema(AgentName.AUTH_EXPLOIT) is None
