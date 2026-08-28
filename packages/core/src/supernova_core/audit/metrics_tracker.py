import json
import logging
import os
import time
from pathlib import Path

from supernova_core.models.metrics import SessionMetadata
from supernova_core.models.audit import AgentEndResult
from .utils import (
    format_timestamp,
    generate_session_json_path,
)

_log = logging.getLogger(__name__)


# 终态集合:与 WorkflowSummary.status Literal["completed","failed","cancelled"] 对齐。
# update_session_status 收到这些值时同时落顶层 completed_at;非终态(如 in-progress/paused)不落。
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


class MetricsTracker:
    """Manages session.json with atomic read/write."""

    def __init__(self, session_metadata: SessionMetadata):
        self._meta = session_metadata
        self._path = generate_session_json_path(session_metadata)
        self._data: dict = {}

    async def initialize(self, workflow_id: str | None = None) -> None:
        """Create the initial session.json structure.

        If a session.json already exists (e.g. written by SessionManager.create_workspace
        with blackbox-discovery fields like ``repo_path``), merge the metrics-tracker
        payload into it instead of overwriting. The metrics tracker owns the ``session``
        and ``metrics`` sub-trees; all other top-level keys (``repo_path``, ``web_url``,
        ``created_at``, ``scan_type``, ``links``, ...) are preserved as-is.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        ts = format_timestamp()
        new_payload = {
            "session": {
                "id": self._meta.id,
                "webUrl": self._meta.web_url,
                "status": "in-progress",
                "createdAt": ts,
                "originalWorkflowId": workflow_id,
                "resumeAttempts": [],
            },
            "metrics": {
                "total_duration_ms": 0,
                "total_cost_usd": 0,
                "cost_currency": "USD",
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_cache_read_tokens": 0,
                "total_cache_creation_tokens": 0,
                "phases": {},
                "agents": {},
            },
        }
        existing: dict = {}
        if self._path.exists():
            try:
                existing = json.loads(self._path.read_text(encoding="utf-8"))
                if not isinstance(existing, dict):
                    existing = {}
            except (json.JSONDecodeError, OSError):
                existing = {}
        # Preserve pre-existing top-level fields (e.g. repo_path) written by other
        # writers; MetricsTracker owns only the `session` and `metrics` sub-trees.
        merged = dict(existing)
        merged["session"] = new_payload["session"]
        merged["metrics"] = new_payload["metrics"]
        self._data = merged
        await self._atomic_write()

    def start_agent(self, agent_name: str, attempt_number: int) -> None:
        """Record that an agent has started (in-memory only)."""
        if agent_name not in self._data["metrics"]["agents"]:
            self._data["metrics"]["agents"][agent_name] = {
                "duration_ms": 0,
                "cost_usd": 0,
                "cost_currency": "USD",
                "attempts": attempt_number,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
            }

    async def end_agent(self, agent_name: str, result: AgentEndResult) -> None:
        """Persist agent results, update running totals, and aggregate phase metrics."""
        agents = self._data["metrics"]["agents"]
        if agent_name not in agents:
            agents[agent_name] = {}
        agents[agent_name].update({
            "duration_ms": result.duration_ms,
            "cost_usd": result.cost_usd,
            "cost_currency": result.cost_currency,
            "success": result.success,
            "attempt_number": result.attempt_number,
            "model": result.model,
            "input_tokens": result.input_tokens or 0,
            "output_tokens": result.output_tokens or 0,
            "cache_read_tokens": result.cache_read_tokens or 0,
            "cache_creation_tokens": result.cache_creation_tokens or 0,
            # 始终覆盖 error（spec 2026-08-08 bug2）：success 时 result.error=None 清除
            # attempt-1 残留的 error，避免 session.json success:true + 旧 error 共存（live 红标）。
            "error": result.error,
        })

        self._data["metrics"]["total_duration_ms"] += result.duration_ms
        # 混合币种 session（per-model currency 2026-08-28 引入的可能）：跨币种直加读数
        # 失真，warning 可观测（聚合行为不变——last-wins + 直加，分币种聚合另列后续）。
        # 0 成本 agent（未知模型守「不假估算」）不参与判定。
        prev_currency = self._data["metrics"].get("cost_currency")
        prev_cost = self._data["metrics"].get("total_cost_usd") or 0
        if (
            prev_currency is not None
            and prev_cost > 0
            and result.cost_usd > 0
            and prev_currency != result.cost_currency
        ):
            _log.warning(
                "session 混合币种计费：agent %s 为 %s，此前累计为 %s；"
                "total_cost_usd 为跨币种直加，读数需注意",
                agent_name, result.cost_currency, prev_currency,
            )
        self._data["metrics"]["total_cost_usd"] += result.cost_usd
        # session 内币种一致：取 agent 的 cost_currency（spec 2026-07-09；混合时 last-wins）
        self._data["metrics"]["cost_currency"] = result.cost_currency
        self._data["metrics"]["total_input_tokens"] += result.input_tokens or 0
        self._data["metrics"]["total_output_tokens"] += result.output_tokens or 0
        self._data["metrics"]["total_cache_read_tokens"] += result.cache_read_tokens or 0
        self._data["metrics"]["total_cache_creation_tokens"] += result.cache_creation_tokens or 0

        # Phase aggregation — only for successful agents
        if result.success:
            self._aggregate_phase(agent_name, result)

        await self._atomic_write()

    def _aggregate_phase(self, agent_name: str, result: AgentEndResult) -> None:
        """Accumulate metrics for the agent's phase and recalculate percentages."""
        from supernova_core.models.agents import AGENT_PHASE_MAP

        phase_name = AGENT_PHASE_MAP.get(agent_name)
        if phase_name is None:
            return

        phases = self._data["metrics"]["phases"]
        if phase_name not in phases:
            phases[phase_name] = {
                "duration_ms": 0,
                "duration_percentage": 0.0,
                "cost_usd": 0.0,
                "cost_currency": "USD",
                "agent_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
            }

        phases[phase_name]["duration_ms"] += result.duration_ms
        phases[phase_name]["cost_usd"] += result.cost_usd
        phases[phase_name]["cost_currency"] = result.cost_currency
        phases[phase_name]["agent_count"] += 1
        phases[phase_name]["input_tokens"] += result.input_tokens or 0
        phases[phase_name]["output_tokens"] += result.output_tokens or 0
        phases[phase_name]["cache_read_tokens"] += result.cache_read_tokens or 0
        phases[phase_name]["cache_creation_tokens"] += result.cache_creation_tokens or 0

        self._recalculate_phase_percentages()

    def _recalculate_phase_percentages(self) -> None:
        """Recalculate duration_percentage for all phases based on total duration."""
        total = self._data["metrics"]["total_duration_ms"]
        phases = self._data["metrics"]["phases"]
        if total == 0:
            for phase_data in phases.values():
                phase_data["duration_percentage"] = 0.0
            return
        for phase_data in phases.values():
            phase_data["duration_percentage"] = round(
                phase_data["duration_ms"] / total * 100, 2
            )

    async def update_session_status(self, status: str) -> None:
        """Update the session status — both top-level and nested.

        顶层 status 是 web 显示/孤儿对账的权威来源(SessionManager.get_status 顶层优先,
        WorkspacesIndexer._status_of 据此判终态)。历史只写内层 session.status → 顶层永留
        create_workspace 的 "running" → 扫描完成后 _status_of 兜底成 interrupted → web 显示
        "已中断"(回归 hr_20260713-104726)。终态同时落 completed_at,与
        SessionManager.mark_completed / scan_manager._mark_cancelled 同源。
        """
        self._data["status"] = status
        self._data["session"]["status"] = status
        if status in _TERMINAL_STATUSES:
            self._data["completed_at"] = time.time()
        await self._atomic_write()

    async def add_resume_attempt(self, workflow_id: str, terminated: list[str], checkpoint: str | None = None) -> None:
        """Append a resume attempt to the session record."""
        attempt = {
            "workflowId": workflow_id,
            "terminatedAgents": terminated,
            "checkpoint": checkpoint,
        }
        self._data["session"]["resumeAttempts"].append(attempt)
        await self._atomic_write()

    async def reload(self) -> None:
        """Reload session.json from disk (pick up external changes)."""
        if self._path.exists():
            content = self._path.read_text(encoding="utf-8")
            self._data = json.loads(content)

    def get_metrics(self) -> dict:
        """Return the current metrics dict."""
        return self._data.get("metrics", {})

    async def _atomic_write(self) -> None:
        """Write to a temp file then atomically replace session.json."""
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, default=str), encoding="utf-8")
        os.replace(str(tmp), str(self._path))
