"""锚点测试:vuln LLM 轨 exploitation queue 的结构化输出捕获管道。

背景:原始 TS 用 agent final structured output 捕获 exploitation queue
(agent-execution.ts:222 把 result.structuredOutput 写盘)。PY executor.py:132-135
移植了同款落盘分支,但 run_agent 历来没传 structured_output_schema →
result.structured_output 恒 None → ``{vt}_exploitation_queue.json`` 永不落盘 →
黑盒 preflight 永远报 "No whitebox results found"。本测试锁定补丁:
``_vuln_output_schema`` helper + run_agent 透传。
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
    def test_vuln_agent_returns_schema(self, agent):
        """vuln agent 返回**裸 JSON Schema**(业务层语义;不感知引擎)。SDK 信封契约
        ``{type:'json_schema', schema:{...}}`` 的包装由 providers_anthropic._build_options
        负责;openai 引擎直接用裸 schema。"""
        schema = activities._vuln_output_schema(agent)
        assert schema is not None
        assert schema["type"] == "object"
        assert "vulnerabilities" in schema["properties"]
        assert "vulnerabilities" in schema["required"]
        items = schema["properties"]["vulnerabilities"]["items"]
        assert set(items["required"]) == {
            "ID", "vulnerability_type", "externally_exploitable", "confidence",
        }

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
        """对齐 TS VULN_AGENT_QUEUE_FILENAMES:仅 *-vuln 映射 queue;*-exploit 排除,
        避免 exploit agent 的 structured_output 覆写 vuln 的 exploitation_queue.json。"""
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
async def test_run_agent_passes_schema_for_vuln(tmp_path):
    """回归锚点:run_agent 必须把 _vuln_output_schema 透传给 executor.execute。
    缺这一行 → result.structured_output 恒 None → exploitation_queue.json 不落盘。"""
    captured = await _run_agent_capturing("injection-vuln", tmp_path)
    schema = captured.get("structured_output_schema")
    assert schema is not None
    # 业务层透传裸 schema;SDK 信封包装在 providers_anthropic._build_options
    assert "vulnerabilities" in schema["properties"]


@pytest.mark.asyncio
async def test_run_agent_passes_none_for_recon(tmp_path):
    captured = await _run_agent_capturing("recon", tmp_path)
    assert captured.get("structured_output_schema") is None
