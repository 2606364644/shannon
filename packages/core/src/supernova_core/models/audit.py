from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

if TYPE_CHECKING:
    from supernova_core.models.errors import PentestError


class AgentEndResult(BaseModel):
    success: bool
    duration_ms: int
    cost_usd: float
    cost_currency: str = "USD"
    attempt_number: int = 1
    model: str | None = None
    error: str | None = None
    is_final_attempt: bool = True
    checkpoint: str | None = None
    num_turns: int | None = None  # B2 观测:agent turn 消耗(来自 AgentMetrics.num_turns)
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None


def end_result_from_pentest_error(
    e: "PentestError", duration_ms: int, attempt_number: int,
) -> AgentEndResult:
    """失败路径构造 AgentEndResult：从 PentestError.context 取 executor 携带的 cost
    （修 error path cost 归 0），取不到回落 0（非 executor raise，如纯 IO probe 异常）。

    executor（core AgentExecutor / 黑盒 ExploitExecutor）失败 raise PentestError 时，
    _result_cost_context 把 result.cost/tokens 塞 context；activities 的 except
    PentestError 经此 helper 构造 end_agent 的 AgentEndResult，让失败 agent 也记真实消耗。
    """
    ctx = getattr(e, "context", None) or {}
    return AgentEndResult(
        success=False,
        duration_ms=duration_ms,
        cost_usd=ctx.get("cost_usd", 0.0),
        cost_currency=ctx.get("cost_currency", "USD"),
        attempt_number=attempt_number,
        model=ctx.get("model"),
        num_turns=ctx.get("num_turns"),
        input_tokens=ctx.get("input_tokens"),
        output_tokens=ctx.get("output_tokens"),
        cache_read_tokens=ctx.get("cache_read_tokens"),
        cache_creation_tokens=ctx.get("cache_creation_tokens"),
        error=str(e),
    )


class AgentLogDetails(BaseModel):
    attempt_number: int = 1
    duration_ms: int | None = None
    cost_usd: float | None = None
    cost_currency: str = "USD"
    success: bool | None = None
    error: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None


class AgentMetricsSummary(BaseModel):
    duration_ms: int
    cost_usd: float | None = None
    cost_currency: str = "USD"
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None


class WorkflowSummary(BaseModel):
    status: Literal["completed", "failed", "cancelled"]
    total_duration_ms: int
    total_cost_usd: float
    cost_currency: str = "USD"
    completed_agents: list[str]
    agent_metrics: dict[str, AgentMetricsSummary]
    error: str | None = None
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_creation_tokens: int = 0


class ResumeInfo(BaseModel):
    previous_workflow_id: str
    new_workflow_id: str
    checkpoint_hash: str
    completed_agents: list[str]


class PhaseMetrics(BaseModel):
    duration_ms: int = 0
    duration_percentage: float = 0.0
    cost_usd: float = 0.0
    agent_count: int = 0
    # 最终态失败的 unique agent 数（2026-09-01 聚合含失败 agent；写盘路径
    # metrics_tracker 手写 dict，此模型仅类型文档）。
    failed_agent_count: int = 0
