"""锚点测试:vuln LLM 轨 exploitation queue 的结构化输出捕获管道。

背景:原始 TS 用 agent final structured output 捕获 exploitation queue
(agent-execution.ts:222 把 result.structuredOutput 写盘)。PY executor.py:132-135
移植了同款落盘分支,但 run_agent 历来没传 structured_output_schema →
result.structured_output 恒 None → ``{vt}_exploitation_queue.json`` 永不落盘 →
黑盒 preflight 永远报 "No whitebox results found"。本测试曾锁定该补丁
(``_vuln_output_schema`` helper + run_agent 透传)。

Phase 2 B 拓扑(spec 2026-08-19 §3.5)起:queue 数据切换到 collector 通道
(submit_finding 单条上交 + finding_roster 对账),末条大 JSON 通道停用——
``_vuln_output_schema`` 恒返 None,vuln agent 不再收 structured_output_schema。
"""
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from supernova_core.models.agents import AgentName
from supernova_core.models.metrics import AgentMetrics
from supernova_whitebox.pipeline import activities


VULN_AGENTS = [
    AgentName.INJECTION_VULN,
    AgentName.XSS_VULN,
    AgentName.AUTH_VULN,
    AgentName.SSRF_VULN,
    AgentName.AUTHZ_VULN,
]


class TestVulnOutputSchema:
    """单元:_vuln_output_schema helper 行为。"""

    @pytest.mark.parametrize("agent", VULN_AGENTS)
    def test_vuln_agent_returns_none(self, agent):
        """Phase 2 B 拓扑(spec 2026-08-19 §3.5):vuln agent 恒返 None——queue 走
        collector 通道(submit_finding 单条上交),末条大 JSON 通道停用,CLI
        --json-schema 与 collected_text 兜底对 vuln 不再激活。"""
        assert activities._vuln_output_schema(agent) is None

    @pytest.mark.parametrize("agent", [
        AgentName.PRE_RECON, AgentName.RECON, AgentName.REPORT,
        AgentName.RECON_BLACKBOX, AgentName.VALIDATE_AUTH,
    ])
    def test_non_vuln_agent_returns_none(self, agent):
        assert activities._vuln_output_schema(agent) is None

    @pytest.mark.parametrize("agent", [
        AgentName.INJECTION_EXPLOIT, AgentName.XSS_EXPLOIT,
        AgentName.AUTH_EXPLOIT, AgentName.SSRF_EXPLOIT, AgentName.AUTHZ_EXPLOIT,
    ])
    def test_exploit_agent_excluded(self, agent):
        """历史:对齐 TS VULN_AGENT_QUEUE_FILENAMES 排除 *-exploit,避免 exploit 的
        structured_output 覆写 vuln queue。Phase 2 起恒返 None,vuln/exploit 均无 schema。"""
        assert activities._vuln_output_schema(agent) is None


# ---- 集成:run_agent 真把 schema 透传给 executor.execute ----

def _runtime_patches(captured: dict):
    session = MagicMock()
    session.start_agent = AsyncMock()
    session.end_agent = AsyncMock()
    session.log_error = AsyncMock()
    logger = MagicMock()
    logger.initialize = AsyncMock()
    logger.close = AsyncMock()

    async def fake_execute(**kwargs):
        captured.update(kwargs)
        return AgentMetrics(duration_ms=1, cost_usd=0.0, num_turns=1, model="test")

    executor = MagicMock()
    executor.execute = fake_execute
    return (
        patch.object(activities.activity, "info", return_value=MagicMock(attempt=1)),
        patch("supernova_whitebox.audit.session_registry.get_audit_session", return_value=session),
        patch("supernova_whitebox.audit.session_tool_audit_logger.SessionToolAuditLogger", return_value=logger),
        patch.object(activities, "AgentExecutor", return_value=executor),
    )


class _FakeInput:
    web_url = None
    config_path = None
    api_key = None
    pipeline_testing_mode = False
    prompt_override = None
    provider_config = None   # P3c 阶段 1:run_agent 透传 provider_config

    def __init__(self, agent_name, repo_path):
        self.agent_name = agent_name
        self.repo_path = str(repo_path)


async def _run_agent_capturing(agent_name: str, tmp_path):
    captured: dict = {}
    with patch.object(activities, "_get_paths", return_value=(tmp_path, tmp_path, tmp_path)):
        with ExitStack() as stack:
            for p in _runtime_patches(captured):
                stack.enter_context(p)
            await activities.run_agent(_FakeInput(agent_name, tmp_path))
    return captured


@pytest.mark.asyncio
async def test_run_agent_passes_none_for_vuln(tmp_path):
    """回归锚点(Phase 2 B 拓扑):run_agent 对 vuln agent 透传 None——
    structured_output_schema 通道停用,queue 由 collector(submit_finding)接管。"""
    captured = await _run_agent_capturing("injection-vuln", tmp_path)
    assert captured.get("structured_output_schema") is None


@pytest.mark.asyncio
async def test_run_agent_passes_none_for_recon(tmp_path):
    captured = await _run_agent_capturing("recon", tmp_path)
    assert captured.get("structured_output_schema") is None
