"""Process-wide registry for AuditSession instances, keyed by workflow_id.

Activities reach the live display via ``get_audit_session()``, which resolves
the current workflow_id (``activity.info()`` in the worker path; explicit param
or ``'_cli'`` fallback in CLI/tests). The driver (``run_scan``) sets the
per-workflow entry before starting the worker and clears it after. P3c 阶段 3：
三个进程级单例之一 dict 化，解除 ``max_concurrent_workflow_tasks=1`` 硬钉。

NullAuditSession is the safe default when no scan is active (tests, standalone
tooling): every method is a no-op so callers never null-check.
"""
from __future__ import annotations

import contextvars
from contextlib import asynccontextmanager
from typing import Any, Literal

# P3c 阶段 3：按 workflow_id 索引（替代进程级 _current 单例）。
# web worker 路径 setup_display/finalize 在 activity 体内 → _resolve_wf_id 自动拿 workflow_id；
# CLI 路径 worker.py:342 在 workflow 启动前 → 显式传 workflow_id（Task 4）。
_SESSIONS: dict[str, Any] = {}
_current_wf_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "audit_wf_id", default=None
)


def _resolve_wf_id(explicit: str | None = None) -> str:
    """解析当前 workflow_id。

    优先级：显式参数 > contextvar > ``activity.info().workflow_id`` > ``'_cli'`` 兜底。
    try/except 惯例对齐 ``logging/activity_logger.py::create_activity_logger``。
    """
    if explicit:
        return explicit
    ctx = _current_wf_id.get()
    if ctx:
        return ctx
    try:
        from temporalio import activity

        wf_id = activity.info().workflow_id
        if wf_id:
            return wf_id
    except RuntimeError:
        pass
    return "_cli"


# 保留旧名 _current 供现有导入兼容（已无实际用途，恒 None）。
_current: Any = None


class NullAuditSession:
    """No-op AuditSession stand-in. Safe to call without initialize().

    每个方法签名必须镜像真实 ``AuditSession``（core/audit/session.py）的公开面，
    使 session 未绑定（worker 容器未跑 setup_display / 测试 / 独立工具）时调用者
    不崩、no-op。新增/改真实 AuditSession 方法时务必同步此处签名，否则 drift 会
    复发——回归守卫见 ``tests/audit/test_null_audit_session_surface.py``（曾因
    ``log_phase_start`` 漏 ``steps``/``step_intents`` 致 web 扫描首 activity 即
    TypeError 崩、live 页显示"已中断"）。
    """

    async def initialize(self, workflow_id: str | None = None, *,
                         event_file: str | None = None) -> None: pass
    async def start_agent(self, agent_name: str, prompt: str, attempt: int = 1) -> None: pass
    async def end_agent(self, agent_name: str, result: Any) -> None: pass
    async def log_llm_turn(self, agent_name: str, turn: int, content: str) -> None: pass
    async def log_tool_call(self, agent_name: str, tool_name: str, parameters: Any) -> None: pass
    async def log_event(self, event_type: str, event_data: Any) -> None: pass
    async def log_phase_start(self, phase: str,
                              steps: tuple[str, ...] = (),
                              step_intents: tuple[str | None, ...] = ()) -> None: pass
    async def log_phase_complete(self, phase: str) -> None: pass
    async def log_info(self, message: str, level: Literal["info", "warning"] = "info") -> None: pass
    async def log_step(self, name: str, phase: str, event: str,
                       duration_ms: int | None = None, error: str | None = None,
                       intent: str | None = None) -> None: pass

    @asynccontextmanager
    async def track_step(self, phase: str, name: str, intent: str | None = None):
        # no-op async context manager: activities 大量 `async with ... .track_step(...)`,
        # Null 命中时须可正常进入/退出而不崩。
        yield

    async def log_gitnexus_progress(self, phase: str, kind: str, done: int,
                                    total: int, hits: int,
                                    detail: str | None = None) -> None: pass
    async def log_workflow_complete(self, summary: Any) -> None: pass
    async def update_session_status(self, status: str) -> None: pass
    async def add_resume_attempt(self, workflow_id: str, terminated: list[str],
                                 checkpoint: str | None = None) -> None: pass
    async def log_error(self, error: Any, context: str | None = None, *,
                        attempt: int | None = None,
                        max_attempts: int | None = None) -> None: pass
    async def log_resume_header(self, resume_info: Any) -> None: pass
    async def close(self) -> None: pass
    async def get_metrics(self) -> dict: return {}

    @property
    def dispatcher(self):
        # 真实 session 暴露 LogBus dispatcher；Null 无下游，返 None。访问不得 AttributeError。
        return None


def set_audit_session(session: Any, workflow_id: str | None = None) -> None:
    wf_id = _resolve_wf_id(workflow_id)
    _SESSIONS[wf_id] = session


def get_audit_session() -> Any:
    wf_id = _resolve_wf_id()
    s = _SESSIONS.get(wf_id)
    return s if s is not None else NullAuditSession()


def get_audit_session_for(workflow_id: str) -> Any:
    """显式按 workflow_id 查询（测试 + CLI 兜底用）。"""
    s = _SESSIONS.get(workflow_id)
    return s if s is not None else NullAuditSession()


def clear_audit_session(workflow_id: str | None = None) -> None:
    wf_id = _resolve_wf_id(workflow_id)
    _SESSIONS.pop(wf_id, None)
