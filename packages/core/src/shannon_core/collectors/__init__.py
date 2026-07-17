from shannon_core.collectors.base import (
    CollectorBase,
    DuplicateCallError,
    SectionSchema,
)

__all__ = ["CollectorBase", "DuplicateCallError", "SectionSchema", "make_collector"]


def make_collector(agent_name) -> "CollectorBase | None":
    """按 agent 分发 collector。

    - Plan 1: pre-recon 走 PreReconCollector。
    - Plan 2: recon 走 ReconCollector/render_recon。
    - Plan 3: 5 个 vuln agent（``<vc>-vuln``）共用 vuln collector，按 vc branching。
    - 其余 agent 返 None（无 collector 通道，走 self-Write 路径）。

    vc 派生用 ``AgentName.XXX_VULN.value == "<vc>-vuln"`` 的不变量：
    ``endswith("-vuln")`` 守门 + ``removesuffix("-vuln")`` 还原 vc（无字典、无漂移、
    无跨模块 import）。非 vuln / exploit / recon 的 agent 不命中此分支。
    """
    from shannon_core.models.agents import AgentName

    if agent_name == AgentName.PRE_RECON:
        from shannon_core.collectors.pre_recon import PreReconCollector

        return PreReconCollector()
    if agent_name == AgentName.RECON:
        from shannon_core.collectors.recon import ReconCollector

        return ReconCollector()
    if isinstance(agent_name, AgentName) and agent_name.value.endswith("-vuln"):
        vc = agent_name.value.removesuffix("-vuln")
        from shannon_core.collectors.vuln import make_vuln_collector

        return make_vuln_collector(vc)
    return None
