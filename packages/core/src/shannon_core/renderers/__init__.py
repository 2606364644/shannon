from shannon_core.renderers.pre_recon import render_pre_recon

__all__ = ["render_pre_recon", "render_deliverable"]


def render_deliverable(agent_name, data: dict) -> "str | None":
    """按 agent 分发 renderer。Plan 1 仅 pre-recon；其余返 None（无 collector 通道）。"""
    from shannon_core.models.agents import AgentName

    if agent_name == AgentName.PRE_RECON:
        return render_pre_recon(data)
    return None
