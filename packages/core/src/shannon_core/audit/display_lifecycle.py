"""Display lifecycle: construct AuditSession + shared Console/Live and yield
the session inside an active Live context (rich mode) or plain (non-rich)."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from shannon_core.models.metrics import SessionMetadata

from .session import AuditSession


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
        live = Live(dashboard, console=console, transient=True, refresh_per_second=10,
                    redirect_stderr=False)
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
