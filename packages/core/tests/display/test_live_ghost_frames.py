"""Regression guard: the live dashboard must render on the alternate screen
(full redraw), not in transient relative-erase mode.

Ghost frames (duplicate `pre-recon - step 0/8` + full-width rules that also
fail to re-flow on resize) were caused by transient relative-erase desync:
Rich remembers the previous frame's line count, moves the cursor up, and erases
that many lines; once the count drifts (this region shares the console with
other writes and frame-height changes), stale frames are left on screen.
`screen=True` (alternate screen) makes Rich repaint from an absolute cursor
origin each refresh, so there is no line-count to drift and ghosting is
impossible by construction.

This test locks the invariant by intercepting the real `Live(...)` construction
inside `run_with_display(use_rich=True)` and asserting the screen/redraw flags.
It fails if anyone reverts `screen=True` back to `transient=True` (the mode that
produced the bug). Checking the flag values is the reliable regression signal
here: the ghosting is an interaction-dependent runtime effect that a byte-level
PTY capture cannot deterministically reproduce (Rich's alt screen is wiped on
stop, so the footer never survives into a post-stop capture, and the transient
baseline erases itself on stop by design).
"""
import asyncio
from unittest.mock import patch

from rich.live import Live

from shannon_core.audit.display_lifecycle import run_with_display
from shannon_core.models.metrics import SessionMetadata


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


import pytest

@pytest.mark.asyncio
async def test_rich_live_uses_alternate_screen_full_redraw():
    """run_with_display must build its Live with screen=True (alt-screen full
    redraw) and transient=False, with stderr left un-redirected for the
    Temporal workflow-sandbox circular-import guard."""
    captured: list[dict] = []

    with patch.object(Live, "__init__", _spy(captured)):
        async with run_with_display(_meta(), use_rich=True):
            pass

    assert captured, "no Live was constructed; the rich display path was not exercised"
    live_kwargs = captured[-1]
    # screen=True is the core fix: alternate-screen full redraw has no
    # relative-erase line count to desync, so ghost frames cannot accumulate.
    assert live_kwargs.get("screen") is True, (
        "Live must use screen=True (alt-screen full redraw); got "
        f"screen={live_kwargs.get('screen')!r}, which reintroduces ghost frames"
    )
    assert live_kwargs.get("transient") is False, (
        f"Live must use transient=False with screen=True; got transient={live_kwargs.get('transient')!r}"
    )
    # redirect_stderr must stay False: redirecting stderr installs a FileProxy
    # that re-imports rich inside the Temporal workflow-sandbox thread,
    # triggering a circular ImportError that fails every workflow task.
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
