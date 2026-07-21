from supernova_core.collectors.base import (
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
    - Plan 4: 5 个 exploit agent（``<vc>-exploit``）共用 append collector（mode='append'）。
    - 其余 agent 返 None（无 collector 通道，走 self-Write 路径）。

    vc 派生用 ``AgentName.XXX_VULN.value == "<vc>-vuln"`` 的不变量：
    ``endswith("-vuln")`` 守门 + ``removesuffix("-vuln")`` 还原 vc（无字典、无漂移、
    无跨模块 import）。非 vuln / exploit / recon 的 agent 不命中此分支。
    """
    from supernova_core.models.agents import AgentName

    if agent_name == AgentName.PRE_RECON:
        from supernova_core.collectors.pre_recon import PreReconCollector

        return PreReconCollector()
    if agent_name == AgentName.RECON:
        from supernova_core.collectors.recon import ReconCollector

        return ReconCollector()
    if isinstance(agent_name, AgentName) and agent_name.value.endswith("-vuln"):
        vc = agent_name.value.removesuffix("-vuln")
        from supernova_core.collectors.vuln import make_vuln_collector

        return make_vuln_collector(vc)
    if isinstance(agent_name, AgentName) and agent_name.value.endswith("-exploit"):
        vc = agent_name.value.removesuffix("-exploit")
        from supernova_core.collectors.exploit import make_exploit_collector

        return make_exploit_collector(vc)
    return None
