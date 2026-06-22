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
from pathlib import Path
from typing import Literal

import json
import logging
import shutil

from shannon_core.git_manager import GitManager
from shannon_core.models.agents import AGENTS, AgentName

_logger = logging.getLogger(__name__)


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


# 编排顺序（用于中断定位 + rewind 过滤）。只列有守卫/有 deliverable 的 agent。
_AGENT_ORDER: list[str] = [
    AgentName.PRE_RECON.value,
    AgentName.RECON.value,
    AgentName.INJECTION_VULN.value,
    AgentName.XSS_VULN.value,
    AgentName.AUTH_VULN.value,
    AgentName.SSRF_VULN.value,
    AgentName.AUTHZ_VULN.value,
]

# rewind target 别名规范化。CLI 接受 --rewind vuln（语义：重跑全部 5 个并行
# vuln agent，即从 vuln 段起点 injection-vuln 开始），但 vuln 不在 _AGENT_ORDER。
# 这里在 builder 内部把别名翻成真实起点，避免 _before_rewind / cleanup 取
# _AGENT_ORDER.index 时 ValueError 导致 worker 崩溃。
_REWIND_ALIASES: dict[str, str] = {
    "vuln": AgentName.INJECTION_VULN.value,
}


def _normalize_rewind_target(target: str | None) -> str | None:
    """把 rewind 别名（如 vuln）翻译成 _AGENT_ORDER 里的真实起点 agent。None→None。"""
    if target is None:
        return None
    return _REWIND_ALIASES.get(target, target)


class WhiteboxResumeStateBuilder:
    """从磁盘重建 completed_agents，激活 WhiteboxScanWorkflow 的空壳守卫。"""

    def __init__(self) -> None:
        pass

    async def build(
        self,
        *,
        mode: Literal["auto", "rewind", "fresh"],
        workspace: Path,
        deliverables: Path,
        repo_path: Path,
        rewind_target: str | None = None,
    ) -> WhiteboxResumeState:
        if mode == "fresh":
            return WhiteboxResumeState(mode="fresh")

        # 规范化 rewind 别名（vuln -> injection-vuln），保证后续 index 查找不 ValueError
        rewind_target = _normalize_rewind_target(rewind_target)

        git_completed = await GitManager.get_completed_agents(deliverables)
        session_completed = self._session_success(workspace)
        file_exists = self._file_exists_map(deliverables)

        candidates = _AGENT_ORDER if mode == "auto" else self._before_rewind(rewind_target)

        completed: list[str] = []
        warnings: list[str] = []
        for agent in candidates:
            r = reconcile(
                git_completed=git_completed,
                session_completed=session_completed,
                file_exists=file_exists,
                agent=agent,
            )
            if r.aborted:
                return r  # 中止：G ∧ ¬F
            completed += r.completed_agents
            warnings += r.warnings

        state = WhiteboxResumeState(
            mode=mode,
            completed_agents=completed,
            warnings=warnings,
            interrupted_agent=self._locate_interrupted(completed, mode, rewind_target),
        )
        return state

    def _session_success(self, workspace: Path) -> set[str]:
        session_file = workspace / "session.json"
        if not session_file.exists():
            return set()
        try:
            data = json.loads(session_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # atomic_write 下 session.json 不会写一半；损坏仅外部干扰。
            # 与 FileNotFoundError 同等处理：返回空集，让 git_completed 主导对账。
            _logger.warning("session.json 损坏（JSONDecodeError），忽略 session success 信号: %s", session_file)
            return set()
        agents = (data.get("metrics") or {}).get("agents") or {}
        return {name for name, m in agents.items() if m.get("success") is True}

    @staticmethod
    def _file_exists_map(deliverables: Path) -> dict[str, bool]:
        out: dict[str, bool] = {}
        for name, defn in AGENTS.items():
            if defn.deliverable_filename:
                out[name.value] = (deliverables / defn.deliverable_filename).exists()
        return out

    @staticmethod
    def _before_rewind(target: str | None) -> list[str]:
        """rewind 模式：只保留编排顺序里严格在 target 之前的 agent。"""
        if target is None:
            return []
        idx = _AGENT_ORDER.index(target) if target in _AGENT_ORDER else len(_AGENT_ORDER)
        return _AGENT_ORDER[:idx]

    @staticmethod
    def _locate_interrupted(
        completed: list[str], mode: str, rewind_target: str | None
    ) -> str | None:
        """auto: 编排顺序里第一个未完成的 agent；rewind: rewind_target 本身。"""
        if mode == "rewind":
            return rewind_target
        for agent in _AGENT_ORDER:
            if agent not in completed:
                return agent
        return None

    @staticmethod
    async def cleanup(
        *,
        mode: Literal["auto", "rewind", "fresh"],
        deliverables: Path,
        completed_agents: list[str],
        rewind_target: str | None = None,
        run_ts: str | None = None,
    ) -> Path | None:
        """清理半成品/旧产出物，让重跑从干净状态开始。不用 git reset。

        auto:  删除不在 completed_agents 的产出物文件（半成品/旧残留）。
        rewind: 把 target 及之后 agent 的产出物归档到 .whitebox-archive/<run_ts>/。
        fresh: 不处理（全新扫会自建 deliverables）。
        返回归档目录（rewind）或 None。
        """
        if mode == "fresh":
            return None

        # 规范化 rewind 别名（vuln -> injection-vuln），保证 index 查找不 ValueError
        rewind_target = _normalize_rewind_target(rewind_target)

        if mode == "rewind":
            assert rewind_target and run_ts
            start = _AGENT_ORDER.index(rewind_target)
            to_archive = _AGENT_ORDER[start:]
            archive = deliverables / ".whitebox-archive" / run_ts
            archive.mkdir(parents=True, exist_ok=True)
            for agent_name in to_archive:
                defn = AGENTS.get(AgentName(agent_name))
                if defn and defn.deliverable_filename:
                    src = deliverables / defn.deliverable_filename
                    if src.exists():
                        shutil.move(str(src), str(archive / defn.deliverable_filename))
            return archive

        # auto: 删不在 completed_agents 的半成品
        for agent_name, defn in AGENTS.items():
            if not defn.deliverable_filename:
                continue
            if agent_name.value in completed_agents:
                continue  # 已完成，保留
            src = deliverables / defn.deliverable_filename
            if src.exists():
                src.unlink()
        return None
