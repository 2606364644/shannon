"""Display lifecycle: construct AuditSession + shared Console/Live and yield
the session inside an active Live context (rich mode) or plain (non-rich)."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from shannon_core.models.metrics import SessionMetadata

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
        # redirect_stderr=False: this process also hosts the Temporal worker, whose
        # workflow sandbox logs activation errors via logging lastResort -> sys.stderr.
        # Rich's default redirect turns sys.stderr into a FileProxy whose console.print
        # -> rich_cast re-imports rich *inside the sandbox thread*, hitting the sandbox
        # importer and throwing a circular ImportError that fails every workflow task.
        # Keep stderr real so worker logging never re-enters rich / the sandbox.
        # Alternate screen + full redraw: Rich repaints the whole screen each
        # refresh instead of relative erase (cursor-up + line erase). Relative
        # erase desyncs here because RichConsoleRenderer prints PHASE/STEP/AGENT
        # log lines to this same Console; full redraw has no line-count to drift,
        # so duplicate "ghost" footer frames cannot accumulate and resize re-flows
        # (Rich re-measures Console.size every refresh). redirect_stdout=True keeps
        # those console.print log lines inside the managed alt screen so they stay
        # visible. redirect_stderr stays False: that is the documented guard against
        # the Temporal workflow-sandbox circular-import failure.
        live = Live(dashboard, console=console, screen=True, transient=False,
                    refresh_per_second=default_refresh_hz(),
                    redirect_stdout=True, redirect_stderr=False)
        try:
            with live:
                yield session
        finally:
            await session.close()
    else:
        from rich.console import Console
        console = Console()  # auto-detects non-TTY in pipes -> plain text per event
        session = AuditSession(meta, use_rich=False, console=console)
        await session.initialize(workflow_id=meta.id)
        try:
            yield session
        finally:
            await session.close()
