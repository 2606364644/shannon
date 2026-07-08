"""Display lifecycle: construct AuditSession + shared Console/Live and yield
the session inside an active Live context (rich mode) or plain (non-rich).

统一日志总线（2026-07-08）：session.initialize 后 LogBus.attach(session.dispatcher)，
把散落 logging 汇入 dispatcher（与 PHASE/STEP 同 asyncio.Lock 序列化，根除 Rich Live
footer 鬼影）；退出时 drain_and_detach final flush + cancel drain（在 session.close 前）。
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from shannon_core.models.metrics import SessionMetadata
from shannon_core.logging.log_bus import LogBus

from .session import AuditSession


def default_refresh_hz() -> float:
    """Live dashboard refresh rate. Default 3Hz (calm); override via env."""
    return float(os.environ.get("SHANNON_LIVE_REFRESH_HZ", "3"))


@asynccontextmanager
async def run_with_display(meta: SessionMetadata, use_rich: bool = False) -> AsyncIterator[AuditSession]:
    if use_rich:
        from rich.console import Console
        from rich.live import Live
        from shannon_core.display.live_dashboard import LiveDashboardRenderer

        console = Console()
        dashboard = LiveDashboardRenderer(console)
        session = AuditSession(meta, use_rich=True, console=console, dashboard=dashboard)
        await session.initialize(workflow_id=meta.id)
        # 统一日志总线：attach 把散落 logging 汇入 dispatcher（起 drain task）。
        await LogBus.attach(session.dispatcher)
        # redirect_stderr=False: this process also hosts the Temporal worker, whose
        # workflow sandbox logs activation errors via logging lastResort -> sys.stderr.
        # Rich's default redirect turns sys.stderr into a FileProxy whose console.print
        # -> rich_cast re-imports rich *inside the sandbox thread*, hitting the sandbox
        # importer and throwing a circular ImportError that fails every workflow task.
        # Keep stderr real so worker logging never re-enters rich / the sandbox.
        # screen=True (alt screen) was tried to kill ghost frames but it wipes
        # the scrolling log region during the scan (only the footer is visible),
        # which is worse than the original ghosting. Reverted to transient: log
        # lines (PHASE/STEP/AGENT) scroll above the footer and stay visible while
        # the footer animates in place.
        live = Live(dashboard, console=console, transient=True,
                    refresh_per_second=default_refresh_hz(),
                    redirect_stderr=False)
        try:
            with live:
                yield session
        finally:
            await LogBus.drain_and_detach()
            await session.close()
    else:
        from rich.console import Console
        console = Console()  # auto-detects non-TTY in pipes -> plain text per event
        session = AuditSession(meta, use_rich=False, console=console)
        await session.initialize(workflow_id=meta.id)
        # 统一日志总线：non-rich 分支同样 attach（dispatcher 已有 FileLogRenderer +
        # RichConsoleRenderer；LogBus.attach 自动补 DiagnosticLogRenderer）。
        await LogBus.attach(session.dispatcher)
        try:
            yield session
        finally:
            await LogBus.drain_and_detach()
            await session.close()
