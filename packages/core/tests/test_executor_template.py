from supernova_core.agents.executor import resolve_template_name
from supernova_core.models.agents import AgentName


def test_recon_offline_uses_recon_static():
    """recon + 无 web_url → recon-static(离线模式)。"""
    result = resolve_template_name(
        agent_name=AgentName.RECON,
        prompt_override=None,
        default_template="recon",
        web_url="",
    )
    assert result == "recon-static"


def test_recon_live_uses_default():
    """recon + 有 web_url → 默认 recon(live 模式不变)。"""
    result = resolve_template_name(
        agent_name=AgentName.RECON,
        prompt_override=None,
        default_template="recon",
        web_url="https://target.com",
    )
    assert result == "recon"


def test_prompt_override_wins_over_offline_logic():
    """显式 prompt_override 优先,不被离线逻辑覆盖。"""
    result = resolve_template_name(
        agent_name=AgentName.RECON,
        prompt_override="custom-recon",
        default_template="recon",
        web_url="",
    )
    assert result == "custom-recon"


def test_non_recon_agent_unaffected():
    """非 recon agent 不受离线逻辑影响。"""
    result = resolve_template_name(
        agent_name=AgentName.AUTHZ_VULN,
        prompt_override=None,
        default_template="vuln-authz",
        web_url="",
    )
    assert result == "vuln-authz"


def test_recon_string_value_also_matches():
    """agent_name 传字符串 value(如 workflows 的 AgentName.RECON.value)也能匹配。"""
    result = resolve_template_name(
        agent_name="recon",
        prompt_override=None,
        default_template="recon",
        web_url="",
    )
    assert result == "recon-static"
