"""验双引擎从 collector 注入工具的构造缝（不跑真模型）。

Task 5：collector 参数贯通 runner + 双引擎 provider。本测试只验「collector 在
provider 内被正确翻译成本引擎原生工具（claude→MCP server、openai→extra tools）」，
不实际跑模型——构造层断言。

夹具对齐说明（brief §「Known uncertainty」）：
- AnthropicProvider._build_options 内部调 self._build_sdk_env()，读 self.type /
  self.config。用 __new__ 裸实例会 AttributeError；故经 __init__ 构造
  （ProviderConfig(type="anthropic_api") 无需 API key——_build_sdk_env 仅在
  config.api_key 为真值时才设 ANTHROPIC_API_KEY），对齐同目录现有
  test_providers_anthropic_output_format.py 的构造模式。
- OpenAIProvider.build_agent 仅触 self._get_client() 与 self._instructions()
  （后者只用 narration_directive()，无实例属性依赖）。__new__ + monkeypatch
  _get_client 即足够（brief 指定路径）。
"""
from supernova_core.agents.runner import ProviderConfig
from supernova_core.collectors.bridge import build_claude_mcp_server, build_openai_tools
from supernova_core.collectors.pre_recon import PreReconCollector


def _collector():
    return PreReconCollector()


def _make_anthropic_provider():
    """对齐 test_providers_anthropic_output_format.py 构造模式（无 API key）。"""
    from supernova_core.agents.providers_anthropic import AnthropicProvider

    return AnthropicProvider(ProviderConfig(type="anthropic_api"))


# ---------- claude: _build_options 注入 mcp_server + allowed_tools ----------

def test_anthropic_build_options_injects_mcp_server_and_allowed_tools():
    provider = _make_anthropic_provider()
    collector = _collector()
    mcp = build_claude_mcp_server(collector)
    allowed = collector.tool_names()
    options = provider._build_options(
        cwd="/tmp", model="claude-sonnet-5", mcp_server=mcp, allowed_tools=allowed,
    )
    assert "shannon-collector" in options.mcp_servers
    assert options.mcp_servers["shannon-collector"] is mcp
    assert "set_executive_summary" in options.allowed_tools
    assert len(options.allowed_tools) == 7


def test_anthropic_build_options_without_collector_leaves_mcp_empty():
    provider = _make_anthropic_provider()
    options = provider._build_options(cwd="/tmp", model="claude-sonnet-5")
    assert not options.mcp_servers
    assert not options.allowed_tools


# ---------- openai: build_agent 注入 extra_tools ----------

def test_openai_build_agent_includes_extra_tools(monkeypatch):
    from supernova_core.agents.providers_openai import OpenAIProvider

    provider = OpenAIProvider.__new__(OpenAIProvider)
    monkeypatch.setattr(provider, "_get_client", lambda: object())
    extra = build_openai_tools(_collector())
    agent = provider.build_agent("glm-4.6", None, extra_tools=extra)
    tool_names = [t.name for t in agent.tools]
    assert "set_executive_summary" in tool_names
    assert "set_ssrf_sinks" in tool_names
    assert "read_file" in tool_names or "bash" in tool_names   # 原有工具仍在


def test_openai_build_agent_without_extra_tools_keeps_builtin_only(monkeypatch):
    from supernova_core.agents.providers_openai import OpenAIProvider

    provider = OpenAIProvider.__new__(OpenAIProvider)
    monkeypatch.setattr(provider, "_get_client", lambda: object())
    agent = provider.build_agent("glm-4.6", None)
    assert not any(t.name.startswith("set_") for t in agent.tools)
