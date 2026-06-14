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
        live = Live(dashboard, console=console, transient=False, refresh_per_second=10)
        try:
            with live:
                yield session
        finally:
            await session.close()
    else:
        session = AuditSession(meta, use_rich=False)
        await session.initialize(workflow_id=meta.id)
        try:
            yield session
        finally:
            await session.close()
