"""Regression guard: the live dashboard must keep the scrolling log region
visible during a scan, so it must NOT use the alternate screen.

A screen=True (alt-screen) Live was tried to eliminate ghost frames, but it
wipes the scrolling log region: during the scan only the footer is visible and
the PHASE/STEP/AGENT lines disappear (standard alt-screen semantics). That is a
worse regression than the ghosting it fixed. This test locks the dashboard to
the transient mode that keeps log lines scrolling above the animating footer.

Note: this test does NOT claim the transient mode is free of ghost frames; it
only enforces that the footer does not run in an alternate screen, preserving
visible log output. The ghost-frame problem is a separate, interaction-dependent
issue tracked separately.
"""
import asyncio
from unittest.mock import patch

import pytest

from rich.live import Live

from supernova_core.audit.display_lifecycle import run_with_display
from supernova_core.models.metrics import SessionMetadata


def _meta() -> SessionMetadata:
    return SessionMetadata(
        id="ghost-probe", web_url="", repo_path="/tmp/ghost-probe",
        output_path="/tmp/ghost-probe",
    )


def _spy(captured):
    orig_init = Live.__init__

    def wrapper(self, renderable=None, *args, **kwargs):
        captured.append(kwargs)
        return orig_init(self, renderable, *args, **kwargs)

    return wrapper


@pytest.mark.asyncio
async def test_rich_live_does_not_use_alternate_screen():
    """The live footer must run in transient (scrolling) mode, never in the
    alternate screen. screen=True would hide the scrolling log region during the
    scan; transient=True keeps PHASE/STEP/AGENT lines visible above the footer.
    redirect_stderr must stay False (Temporal workflow-sandbox circular-import
    guard)."""
    captured: list[dict] = []

    with patch.object(Live, "__init__", _spy(captured)):
        async with run_with_display(_meta(), use_rich=True):
            pass

    assert captured, "no Live was constructed; the rich display path was not exercised"
    live_kwargs = captured[-1]
    # screen must be unset/False: the alternate screen hides the scrolling logs.
    assert not live_kwargs.get("screen"), (
        "Live must NOT use screen=True (alt screen wipes the scrolling log region); "
        f"got screen={live_kwargs.get('screen')!r}"
    )
    assert live_kwargs.get("transient") is True, (
        "Live must use transient=True so log lines scroll above the footer; "
        f"got transient={live_kwargs.get('transient')!r}"
    )
    assert live_kwargs.get("redirect_stderr") is False, (
        "redirect_stderr must stay False (Temporal workflow-sandbox circular-import guard); "
        f"got redirect_stderr={live_kwargs.get('redirect_stderr')!r}"
    )


def test_non_rich_path_does_not_construct_screen_live():
    """The plain (non-rich) path renders line-by-line to a plain console, so it
    must not construct an alternate-screen Live at all."""
    captured: list[dict] = []

    async def run():
        with patch.object(Live, "__init__", _spy(captured)):
            async with run_with_display(_meta(), use_rich=False):
                pass

    asyncio.run(run())
    assert not captured, "non-rich path should not construct a Live"
