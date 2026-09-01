from __future__ import annotations

import json

from pathlib import Path

import pytest
from pathlib import Path

from supernova_core.agents.providers import ProviderConfig
from supernova_core.agents.providers_anthropic import AnthropicProvider
from supernova_core.agents.providers_openai import OpenAIProvider
from supernova_core.models.agents import AGENTS, AGENT_PHASE_MAP, AgentName
from supernova_core.models.topology import TOPOLOGY_PROTOCOLS
from supernova_core.topology.schema import TOPOLOGY_DISCOVERY_SCHEMA

PROMPTS = Path(__file__).resolve().parents[4] / "prompts"
PROBE = PROMPTS.parents[0] / "scripts" / "validate_cross_repo_topology_probe.py"


def test_topology_agent_and_prompt_contract():
    definition = AGENTS[AgentName.CROSS_REPO_TOPOLOGY_DISCOVERY]
    assert definition.prompt_template == "cross-repo-topology-discovery"
    assert definition.prerequisites == []
    assert AGENT_PHASE_MAP[AgentName.CROSS_REPO_TOPOLOGY_DISCOVERY.value] == "correlation"

    prompt = (PROMPTS / "cross-repo-topology-discovery.txt").read_text(encoding="utf-8")
    for anchor in (
        "{{REPOSITORIES_JSON}}", "{{NAVIGATION_MANIFEST_JSON}}",
        "read_file", "glob", "grep", "client_evidence", "handler_evidence",
        "coverage", "uncertain", "roles", "entrypoint", "backend",
        "Do not infer vulnerability", "empty graph",
    ):
        assert anchor in prompt

    assert (PROMPTS / "pipeline-testing" / "cross-repo-topology-discovery.txt").exists()
    assert TOPOLOGY_DISCOVERY_SCHEMA["required"] == ["nodes", "edges", "uncertain", "coverage"]
    for protocol in TOPOLOGY_PROTOCOLS:
        assert protocol in TOPOLOGY_DISCOVERY_SCHEMA["properties"]["edges"]["items"]["properties"]["protocol"]["enum"]


def test_openai_readonly_policy_exposes_only_read_glob_grep(monkeypatch, tmp_path):
    provider = OpenAIProvider(ProviderConfig(type="openai_compatible", model="m"))
    monkeypatch.setattr(OpenAIProvider, "_get_client", lambda self: object())
    agent = provider.build_agent("m", None, tool_policy="readonly-code", allowed_roots=[tmp_path])
    names = {tool.name for tool in agent.tools}
    assert names == {"read_file", "glob", "grep"}
    with pytest.raises(ValueError, match="cannot add extra tools"):
        provider.build_agent("m", None, extra_tools=[object()], tool_policy="readonly-code", allowed_roots=[tmp_path])


def test_anthropic_readonly_policy_restricts_builtin_tools_and_mcp(tmp_path):
    provider = AnthropicProvider(ProviderConfig(type="anthropic_api", model="m"))
    options = provider._build_options("/tmp", "m", tool_policy="readonly-code", allowed_roots=[tmp_path])
    assert options.tools == ["Read", "Glob", "Grep"]
    assert options.strict_mcp_config is True
    assert options.mcp_servers == {}
    with pytest.raises(ValueError, match="cannot inject collector"):
        provider._build_options(
            "/tmp", "m", mcp_servers={"x": {}}, allowed_tools=["set_x"],
            tool_policy="readonly-code", allowed_roots=[tmp_path],
        )


def test_runner_forwards_readonly_policy_to_provider(monkeypatch):
    from supernova_core.agents import runner

    class Provider:
        def __init__(self):
            self.kwargs = None

        async def call(self, **kwargs):
            self.kwargs = kwargs
            return runner.ClaudeRunResult(success=True, text="{}")

    provider = Provider()
    monkeypatch.setattr(runner, "ProviderConfig", dict)
    from supernova_core.agents import providers
    monkeypatch.setattr(
        providers, "create_provider", lambda config: provider
    )
    import asyncio
    asyncio.run(runner.run_claude_prompt(
        prompt="p", repo_path="/tmp", provider_config={"type": "openai_compatible"}, tool_policy="readonly-code"
    ))
    assert provider.kwargs["tool_policy"] == "readonly-code"


def test_probe_script_hard_codes_policy_and_readonly_whitelist():
    source = PROBE.read_text(encoding="utf-8")
    assert 'tool_policy="readonly-code"' in source
    assert '"read_file"' in source and '"glob"' in source and '"grep"' in source
    assert "structured_output" in source


def test_pipeline_fixture_matrix_and_normalizer_empty_graph(tmp_path):
    fixture = json.loads(
        (PROMPTS / "pipeline-testing" / "cross-repo-topology-discovery.txt").read_text(encoding="utf-8")
    )
    assert fixture["nodes"][0]["roles"] == ["entrypoint", "backend"]
    assert {(e["protocol"], e["to"]) for e in fixture["edges"]} == {
        ("grpc", "order-svc"), ("http", "user-svc")
    }
    assert fixture["uncertain"][0]["protocol_hint"] == "thrift"
    assert json.loads(
        (PROMPTS / "pipeline-testing" / "cross-repo-topology-discovery-empty.txt").read_text(encoding="utf-8")
    )["edges"] == []
    with pytest.raises(json.JSONDecodeError):
        json.loads((PROMPTS / "pipeline-testing" / "cross-repo-topology-discovery-malformed.txt").read_text(encoding="utf-8"))

    from supernova_core.topology.discovery import normalize_topology_result
    (tmp_path / "order").mkdir()
    empty = normalize_topology_result({}, {"gateway": tmp_path, "order": tmp_path / "order"})
    assert empty.nodes == []
    assert empty.edges == []
    assert {c.repo for c in empty.coverage} == {"gateway", "order"}


@pytest.mark.asyncio
async def test_readonly_policy_confines_openai_tools_to_allowed_roots(tmp_path):
    from supernova_core.agents.tools_openai import ToolContext
    from supernova_core.agents.tools_openai.exec import _grep_impl
    from supernova_core.agents.tools_openai.fs import _glob_impl, _read_file_impl

    allowed = tmp_path / "allowed"; outside = tmp_path / "outside"
    allowed.mkdir(); outside.mkdir()
    (allowed / "in.txt").write_text("selected secret\n", encoding="utf-8")
    (outside / "out.txt").write_text("host secret\n", encoding="utf-8")

    class Ctx:
        context = ToolContext(cwd=str(tmp_path), allowed_roots=(allowed.resolve(),))
    ctx = Ctx()

    assert "selected secret" in await _read_file_impl(ctx, str(allowed / "in.txt"))
    assert "outside allowed roots" in await _read_file_impl(ctx, str(outside / "out.txt"))
    assert "out.txt" not in await _glob_impl(ctx, "**/*.txt", path=str(tmp_path))
    assert "host secret" not in await _grep_impl(ctx, "secret", path=str(tmp_path))


@pytest.mark.asyncio
async def test_claude_permission_callback_confines_read_search_roots(tmp_path):
    from claude_agent_sdk.types import PermissionResultAllow
    from supernova_core.agents.providers_anthropic import make_readonly_permission_guard

    allowed = tmp_path / "allowed"; outside = tmp_path / "outside"
    allowed.mkdir(); outside.mkdir()
    guard = make_readonly_permission_guard([allowed], str(tmp_path))
    assert isinstance(await guard("Read", {"file_path": str(allowed / "a")}, None), PermissionResultAllow)
    denied = await guard("Read", {"file_path": str(outside / "secret")}, None)
    assert denied.behavior == "deny"
    assert (await guard("Grep", {"path": str(outside)}, None)).behavior == "deny"


@pytest.mark.asyncio
async def test_manager_passes_selected_repos_as_provider_allowed_roots(tmp_path):
    from supernova_web.components.topology_analysis import TopologyAnalysisManager
    seen = []
    async def runner(**kwargs):
        seen.append(kwargs)
        from supernova_core.agents.runner import ClaudeRunResult
        return ClaudeRunResult(success=True, structured_output={"nodes": [], "edges": [], "uncertain": [], "coverage": []})
    manager = TopologyAnalysisManager(tmp_path, repo_manager=None, runner=runner)
    for name in ("a", "b"):
        (tmp_path / "ws" / "repos" / name / ".git").mkdir(parents=True)
    analysis_id = await manager.start("ws", ["a", "b"])
    await manager.wait(analysis_id)
    assert [Path(root).name for root in seen[0]["allowed_roots"]] == ["a", "b"]


def test_real_provider_contracts_accept_root_and_pricing_overrides():
    import inspect
    from supernova_core.agents.providers_openai import OpenAIProvider
    from supernova_core.agents.runner import ProviderConfig

    openai_params = inspect.signature(OpenAIProvider.call).parameters
    assert "allowed_roots" in openai_params
    assert "tool_policy" in openai_params
    config = ProviderConfig(type="openai_compatible", model="m", pricing_override="/tmp/pricing.json")
    assert config.pricing_override == "/tmp/pricing.json"


def test_readonly_policy_fails_closed_without_roots():
    provider = AnthropicProvider(ProviderConfig(type="anthropic_api", model="m"))
    with pytest.raises(ValueError, match="requires allowed_roots"):
        provider._build_options("/tmp", "m", tool_policy="readonly-code")


def test_openai_result_mapper_uses_per_call_pricing(tmp_path, monkeypatch):
    from supernova_core.agents.openai_result_mapper import map_run_result

    override = tmp_path / "pricing.json"
    override.write_text(json.dumps({
        "currency": "USD", "models": {"model-x": {
            "input": 1, "output": 1, "cache_read": 0, "cache_creation": 0,
        }}
    }), encoding="utf-8")

    class Usage:
        input_tokens = 1_000_000
        output_tokens = 0
        cache_creation_input_tokens = 0
        cache_read_input_tokens = 0

    class Context:
        usage = Usage()

    class Run:
        final_output = "{}"
        context_wrapper = Context()

    result = map_run_result(
        Run(), duration_ms=1, model="model-x", turns=1,
        output_format={"type": "object"}, pricing_override=str(override),
    )
    assert result.cost == 1.0
    assert result.cost_currency == "USD"
