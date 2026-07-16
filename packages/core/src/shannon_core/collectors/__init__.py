from shannon_core.collectors.base import (
    CollectorBase,
    DuplicateCallError,
    SectionSchema,
)

__all__ = ["CollectorBase", "DuplicateCallError", "SectionSchema", "make_collector"]


def make_collector(agent_name) -> "CollectorBase | None":
    """按 agent 分发 collector。Plan 1 仅 pre-recon；其余返 None（无 collector 通道）。"""
    from shannon_core.models.agents import AgentName

    if agent_name == AgentName.PRE_RECON:
        from shannon_core.collectors.pre_recon import PreReconCollector

        return PreReconCollector()
    return None
