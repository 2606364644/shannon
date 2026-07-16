from shannon_core.renderers.pre_recon import render_pre_recon

__all__ = ["render_pre_recon", "render_deliverable"]


def render_deliverable(agent_name, data: dict) -> "str | None":
    """按 agent 分发 renderer。

    - Plan 1: pre-recon 走 render_pre_recon。
    - Plan 3: 5 个 vuln agent（``<vc>-vuln``）共用 vuln renderer，按 vc branching。
    - 其余 agent 返 None（无 collector 通道，走 self-Write 路径）。

    vc 派生与 ``collectors/__init__.py::make_collector`` 同源（无字典、无漂移、
    无跨模块 import）；executor.py 依赖此一致性：collector 通道开 ⇒ renderer 必配套。
    """
    from shannon_core.models.agents import AgentName

    if agent_name == AgentName.PRE_RECON:
        return render_pre_recon(data)
    if isinstance(agent_name, AgentName) and agent_name.value.endswith("-vuln"):
        vc = agent_name.value.removesuffix("-vuln")
        from shannon_core.renderers.vuln import render_vuln

        return render_vuln(vc, data)
    return None
