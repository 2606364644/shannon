from supernova_core.agents.executor import resolve_template_name
from supernova_core.models.agents import AGENTS, AgentName


def test_recon_always_static_whitebox_dropped_dynamic():
    """spec 2026-08-03 白盒去动态:RECON agent 不论有无 web_url 永远 recon-static。

    白盒只要仓库就开扫(纯静态);动态 live 侦察职责移交黑盒端点验证 agent。
    守两道门:(1) AGENTS[RECON].prompt_template == "recon-static"(配置层);
    (2) resolve_template_name 不论 web_url 都返回该 default(逻辑层不再按 url 分叉)。
    """
    # (1) 配置层:RECON 默认 template 就是 recon-static(不再是动态 "recon")
    assert AGENTS[AgentName.RECON].prompt_template == "recon-static"
    default = AGENTS[AgentName.RECON].prompt_template
    # (2) 逻辑层:不论有无 web_url 都返回 recon-static(不再有 url→动态分叉)
    assert resolve_template_name(
        agent_name=AgentName.RECON, prompt_override=None,
        default_template=default, web_url="",
    ) == "recon-static"
    assert resolve_template_name(
        agent_name=AgentName.RECON, prompt_override=None,
        default_template=default, web_url="https://target.com",
    ) == "recon-static"


def test_prompt_override_wins():
    """显式 prompt_override 优先于 default。"""
    result = resolve_template_name(
        agent_name=AgentName.RECON,
        prompt_override="custom-recon",
        default_template="recon-static",
        web_url="",
    )
    assert result == "custom-recon"


def test_non_recon_agent_unaffected():
    """非 recon agent 返回其 default_template。"""
    result = resolve_template_name(
        agent_name=AgentName.AUTHZ_VULN,
        prompt_override=None,
        default_template="vuln-authz",
        web_url="",
    )
    assert result == "vuln-authz"
