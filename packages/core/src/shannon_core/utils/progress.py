"""Pipeline progress formatting utilities for exploit agent results."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AgentOutcome:
    """Captures per-agent execution result for progress reporting."""

    agent_name: str
    vuln_type: str
    status: str  # "completed" | "failed" | "skipped"
    duration_s: float = 0.0
    cost_usd: float = 0.0
    turns: int = 0
    error: str = ""


def exploit_result_to_outcome(result: dict, agent_name: str, vuln_type: str) -> AgentOutcome:
    """把 exploit activity 返回的 ``AgentMetrics.model_dump()`` dict 映射为展示用 ``AgentOutcome``。

    字段对齐原始 TS ``AgentMetrics``（``durationMs``/``costUsd``/``numTurns``）：
    ``duration_ms``（毫秒）→ ``duration_s``（秒，``/1000``）；``num_turns``/``cost_usd``
    nullable → 0 兜底。

    修复 getattr(dict, …) 永返 default 的归零 bug（``run_exploit_agent`` 返回 dict，
    workflow completed 分支曾用属性访问取字段）。
    """
    return AgentOutcome(
        agent_name=agent_name,
        vuln_type=vuln_type,
        status="completed",
        duration_s=(result.get("duration_ms") or 0) / 1000,
        cost_usd=result.get("cost_usd") or 0.0,
        turns=result.get("num_turns") or 0,
    )


_STATUS_ICONS = {
    "completed": "✅",
    "failed": "❌",
    "skipped": "⏭️",
}


def _format_duration(seconds: float) -> str:
    """Format a duration in seconds to a human-readable string.

    Examples:
        45  -> "45s"
        272 -> "4m 32s"
        3725 -> "1h 02m"
    """
    if seconds < 60:
        return f"{int(seconds)}s"
    total_minutes = int(seconds) // 60
    remaining_seconds = int(seconds) % 60
    if total_minutes < 60:
        return f"{total_minutes}m {remaining_seconds:02d}s"
    hours = total_minutes // 60
    minutes = total_minutes % 60
    return f"{hours}h {minutes:02d}m"


def format_exploit_summary(outcomes: list[AgentOutcome]) -> str:
    """Format a list of agent outcomes into a human-readable table.

    Returns a multi-line string containing:
    - A header line with completion/failure counts
    - Columnar layout with agent name, status icon, turns, duration, cost
    - A totals line with aggregate metrics
    - Failure detail lines for any failed outcomes
    """
    if not outcomes:
        return "No exploit outcomes to report."

    completed = [o for o in outcomes if o.status == "completed"]
    failed = [o for o in outcomes if o.status == "failed"]
    skipped = [o for o in outcomes if o.status == "skipped"]
    total = len(outcomes)

    # Header
    parts = [f"{len(completed)}/{total} completed"]
    if failed:
        parts.append(f"{len(failed)} failed")
    header = "Exploit summary: " + ", ".join(parts)

    lines = [header]

    # Columnar data rows
    for outcome in outcomes:
        icon = _STATUS_ICONS.get(outcome.status, "?")
        dur = _format_duration(outcome.duration_s) if outcome.status != "skipped" else "-"
        cost = f"${outcome.cost_usd:.4f}" if outcome.status != "skipped" else "-"
        turns = str(outcome.turns) if outcome.status != "skipped" else "-"
        lines.append(
            f"  {icon} {outcome.agent_name:<30s} turns={turns:<4s}  dur={dur:<10s}  cost={cost}"
        )

    # Totals line
    total_duration = sum(o.duration_s for o in completed)
    total_cost = sum(o.cost_usd for o in completed)
    total_turns = sum(o.turns for o in completed)
    lines.append(
        f"  {'Totals':<33s} turns={total_turns:<4d}  "
        f"dur={_format_duration(total_duration):<10s}  cost=${total_cost:.4f}"
    )

    # Failure detail lines
    for outcome in failed:
        lines.append(f"  ❌ {outcome.agent_name}: {outcome.error}")

    return "\n".join(lines)
