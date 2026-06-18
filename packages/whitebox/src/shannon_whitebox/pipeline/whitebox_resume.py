"""Whitebox scan resume: rebuild completed_agents from disk and activate the
existing empty-shell guards in WhiteboxScanWorkflow.

对账决策表（"完成" = G ∧ F）：
  G = git `deliverable: {agent}` commit（权威正信号）
  J = session.json metrics.agents[agent].success == True
  F = 产出物文件在磁盘存在
  - G ∧ ¬F -> 中止（文件丢失，不静默重跑）
  - ¬G     -> 不算完成，重跑（J/F 顶多发 warning）
See spec §3.2 and Implementation Notes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class WhiteboxResumeState:
    mode: Literal["auto", "rewind", "fresh"]
    completed_agents: list[str] = field(default_factory=list)
    interrupted_agent: str | None = None
    warnings: list[str] = field(default_factory=list)
    resume_attempt: int = 0
    aborted: bool = False
    abort_reason: str | None = None


def reconcile(
    *,
    git_completed: set[str],
    session_completed: set[str],
    file_exists: dict[str, bool],
    agent: str,
) -> WhiteboxResumeState:
    """对单个 agent 应用决策表，返回只含该 agent 判定的临时 state。"""
    state = WhiteboxResumeState(mode="auto")
    g = agent in git_completed
    j = agent in session_completed
    f = file_exists.get(agent, False)

    if g and not f:
        state.aborted = True
        state.abort_reason = (
            f"resume 中止：{agent} 有 deliverable commit 但产出物文件缺失 "
            f"（可能被误删）。请检查后重试，或用 --fresh 全新扫描。"
        )
        return state

    if g and f:
        if not j:
            state.warnings.append(
                f"{agent}: git 有 deliverable commit 但 session 未记录 success，以 git 为准"
            )
        state.completed_agents.append(agent)
        return state

    # ¬G：不算完成
    if j:
        state.warnings.append(
            f"{agent}: session 标记 success 但无 deliverable commit，将重跑"
        )
    elif f:
        state.warnings.append(
            f"{agent}: 产出物文件存在但无 deliverable commit（半成品/旧残留），将重跑"
        )
    return state
