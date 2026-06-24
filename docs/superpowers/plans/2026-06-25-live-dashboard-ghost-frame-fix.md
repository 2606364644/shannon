# Live Dashboard Ghost-Frame Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the sticky status footer's duplicate "ghost" frames (repeated `pre-recon · step 0/8` + full-width `─` rules) and its failure to re-flow on terminal resize.

**Architecture:** Switch the Rich `Live` region in `display_lifecycle.py` from transient relative-erase mode to alternate-screen full-redraw mode (`screen=True`, `transient=False`, `redirect_stdout=True`). Relative erase — Rich's "remember last frame's line count, move cursor up, erase" — desyncs because `RichConsoleRenderer` prints log lines to the same `Console`; full redraw has no line-count to desync, so ghosting is eliminated by construction and resize re-flows (Rich re-measures `Console.size` every refresh). One source file plus one regression test.

**Tech Stack:** Python 3.13, Rich 15.0.0, pytest, pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-06-25-live-dashboard-ghost-frame-fix-design.md`

---

## File Structure

- **Modify** `packages/core/src/shannon_core/audit/display_lifecycle.py` — change the `Live(...)` keyword arguments that select erase vs redraw mode. This is the only behavioral change.
- **Create** `packages/core/tests/display/test_live_ghost_frames.py` — a PTY-level integration test that drives the real `Live` configuration (same flags `run_with_display` now uses) through several footer height transitions with concurrent `console.print` log lines, captures the byte stream, and asserts no duplicate footer frames survive.

No other files change. The renderer (`live_dashboard.py`, `rich_renderer.py`), `DashboardState`, the dispatcher, and the plain (non-rich) path are untouched.

---

### Task 1: Switch the Live region to alternate-screen full redraw

**Files:**
- Modify: `packages/core/src/shannon_core/audit/display_lifecycle.py:36-38`

- [ ] **Step 1: Read the current Live construction to confirm line numbers**

Run: `sed -n '36,38p' packages/core/src/shannon_core/audit/display_lifecycle.py`
Expected output:
```
        live = Live(dashboard, console=console, transient=True,
                    refresh_per_second=default_refresh_hz(),
                    redirect_stderr=False)
```

- [ ] **Step 2: Apply the flag change**

Replace the `Live(...)` call with:

```python
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
```

- [ ] **Step 3: Verify the redirect_stderr comment block remains intact**

The existing comment block at lines 30-35 explains `redirect_stderr=False`. It is still accurate and must remain. Verify it sits immediately above the new `live = Live(...)` call.

Run: `sed -n '29,42p' packages/core/src/shannon_core/audit/display_lifecycle.py`
Expected: the `# redirect_stderr=False: ...` comment block is present immediately above the new `live = Live(...)` call.

- [ ] **Step 4: Verify the change compiles and imports**

Run: `uv run python -c "from shannon_core.audit.display_lifecycle import run_with_display; print('ok')"`
Expected: `ok`

- [ ] **Step 5: Run the existing lifecycle test**

Run: `uv run pytest packages/core/tests/display/test_display_lifecycle.py -v`
Expected: PASS (2 tests: `test_default_refresh_hz_is_3`, `test_refresh_hz_env_override`)

- [ ] **Step 6: Run the full display test suite to catch regressions**

Run: `uv run pytest packages/core/tests/display/ -v`
Expected: all PASS. The `test_separator_spans_full_console_width` test exercises the renderer's rule width, which is unchanged.

- [ ] **Step 7: Commit**

```bash
git add packages/core/src/shannon_core/audit/display_lifecycle.py
git commit -m "fix(display): use alt-screen full redraw to eliminate live dashboard ghost frames"
```

---

### Task 2: Lock the no-ghost-frames invariant with a PTY integration test

**Files:**
- Create: `packages/core/tests/display/test_live_ghost_frames.py`

- [ ] **Step 1: Write the test**

Create `packages/core/tests/display/test_live_ghost_frames.py` with the content shown below. It builds a footer that toggles 2<->3 lines (idle vs running agent) while PHASE/STEP/AGENT log lines print to the same Console — the exact interaction that caused ghosting under transient relative-erase mode — then asserts at most one footer frame survives.

```python
"""PTY-level guard: the live footer must not accumulate duplicate "ghost" frames.

Drives the real Live configuration (the same flags run_with_display now uses)
through several footer height transitions with concurrent console.print log
lines, captures the rendered byte stream from a real PTY, and asserts that at
most one footer frame (the separator rule + status line) survives. This locks
in the no-ghost-frames invariant: relative-erase desync produced multiple;
alternate-screen full redraw produces exactly one.
"""
import os
import select
import subprocess
import sys
import time

WIDTH = 70


def _run_scan_in_pty(script: str, timeout: float = 8.0) -> str:
    master, slave = os.openpty()
    proc = subprocess.Popen(
        [sys.executable, "-u", "-c", script],
        stdin=slave, stdout=slave, stderr=slave, close_fds=True,
    )
    os.close(slave)
    buf = b""
    deadline = time.time() + timeout
    while time.time() < deadline:
        ready, _, _ = select.select([master], [], [], 0.2)
        if not ready:
            if proc.poll() is not None:
                break
            continue
        try:
            data = os.read(master, 65536)
        except OSError:
            break
        if not data:
            break
        buf += data
        if b"===DONE===" in buf:
            break
    try:
        proc.wait(timeout=3)
    except Exception:
        proc.kill()
    os.close(master)
    return buf.decode("utf-8", "replace")


# Child script run inside the PTY.
_CHILD = r'''
import sys, time
from rich.console import Console, Group
from rich.live import Live
from rich.text import Text
from rich.table import Table
from rich.spinner import Spinner
from shannon_core.audit.display_lifecycle import default_refresh_hz

WIDTH = 70

def status_line(elapsed):
    row = Table.grid()
    row.add_row(
        Text("pre-recon", style="bold cyan"),
        Text(" \u00b7 step 0/8 \u00b7 " + str(elapsed) + ".0s", style="green"),
    )
    return row

def frame_idle(e):
    return Group(Text("\u2500" * WIDTH, style="dim"), status_line(e))

def frame_running(e):
    g = Table.grid()
    g.add_row(Spinner("dots"), Text(" scanning...", style="blue"))
    return Group(Text("\u2500" * WIDTH, style="dim"), status_line(e), g)

console = Console(legacy_windows=False)
live = Live(frame_idle(1), console=console, screen=True, transient=False,
            refresh_per_second=default_refresh_hz(),
            redirect_stdout=True, redirect_stderr=False)
live.start()
time.sleep(0.3)
console.print("[2026-06-25 01:25:37] PHASE  Starting pre-recon", highlight=False)
live.update(frame_running(1))            # idle -> running (2 -> 3 lines)
time.sleep(0.3)
console.print("[2026-06-25 01:25:37] STEP   \u25cb build call graph", highlight=False)
live.update(frame_idle(3))               # running -> idle (3 -> 2 lines)
time.sleep(0.3)
console.print("[2026-06-25 01:25:40] STEP   \u2713 build call graph  3.3s", highlight=False)
live.update(frame_running(4))            # idle -> running again (2 -> 3 lines)
time.sleep(0.5)
live.update(frame_running(38))
time.sleep(0.3)
live.stop()
sys.__stdout__.write("===DONE===\n")
sys.__stdout__.flush()
'''


def test_no_duplicate_footer_frames_after_height_transitions():
    out = _run_scan_in_pty(_CHILD)
    # Under the buggy transient relative-erase mode the status text "step 0/8"
    # and the separator rule accumulated into multiple frozen copies. With the
    # alternate-screen full redraw there is exactly one footer frame at stop.
    assert out.count("step 0/8") <= 1, (
        "ghost frames detected: 'step 0/8' appears "
        + str(out.count("step 0/8")) + " times"
    )
    assert out.count("\u2500") <= WIDTH, (
        "duplicate separator rules detected: "
        + str(out.count("\u2500")) + " rule chars"
    )


def test_harness_runs_buggy_baseline_to_completion():
    """The harness must actually drive a footer (not be trivially green): swap
    in the OLD transient=relative-erase config and assert the run completes and
    produces footer output. We do not assert the buggy config ghosts every time
    (that is interaction-dependent); we assert the PTY captured a DONE marker
    and at least one rule char, proving the harness exercised a real Live."""
    buggy = _CHILD.replace(
        "screen=True, transient=False",
        "screen=False, transient=True",
    ).replace("redirect_stdout=True,", "")
    out = _run_scan_in_pty(buggy)
    assert out.count("===DONE===") == 1
    assert "\u2500" in out
```

- [ ] **Step 2: Run the invariant test to verify it passes under the fixed config**

Run: `uv run pytest packages/core/tests/display/test_live_ghost_frames.py::test_no_duplicate_footer_frames_after_height_transitions -v`
Expected: PASS

- [ ] **Step 3: Run the harness-baseline test**

Run: `uv run pytest packages/core/tests/display/test_live_ghost_frames.py::test_harness_runs_buggy_baseline_to_completion -v`
Expected: PASS (asserts the harness drives the buggy path to completion with footer output)

- [ ] **Step 4: Confirm the invariant test is not trivially green**

Temporarily change `<= 1` to `<= 0` in `test_no_duplicate_footer_frames_after_height_transitions`, run it, confirm it FAILS (proving the footer IS rendered once), then revert the change back to `<= 1`.

Run: `uv run pytest packages/core/tests/display/test_live_ghost_frames.py::test_no_duplicate_footer_frames_after_height_transitions -v`
Expected (with `<= 0`): FAIL
Then revert `<= 0` back to `<= 1`.

- [ ] **Step 5: Commit**

```bash
git add packages/core/tests/display/test_live_ghost_frames.py
git commit -m "test(display): add PTY guard for no-ghost-frames live footer invariant"
```

---

### Task 3: Verify end-to-end and close out

**Files:** none (verification only)

- [ ] **Step 1: Run the entire display test suite**

Run: `uv run pytest packages/core/tests/display/ -v`
Expected: all PASS, including the new `test_live_ghost_frames.py`.

- [ ] **Step 2: Confirm no other call sites need the flag change**

Run: `rg -n "Live\(" packages/ --type py -g '!**/tests/**'`
Expected: only `packages/core/src/shannon_core/audit/display_lifecycle.py` constructs the scan's `Live`.

Run: `rg -n "run_with_display" packages/ --type py -g '!**/tests/**'`
Expected: `packages/whitebox/src/shannon_whitebox/worker.py` and `packages/blackbox/src/shannon_blackbox/worker.py` both call `run_with_display(..., use_rich=...)`, so both inherit the fix with no per-worker change.

- [ ] **Step 3: Optional real-scan smoke check (manual)**

If a small repo is available, confirm the footer no longer ghosts and resize re-flows:
Run: `uv run shannon-whitebox start -r <path/to/small-repo>`
Expected: during pre-recon, only one footer frame is present; resizing the terminal re-flows the footer to the new width. Scrollback during the scan is unavailable on the alternate screen (expected); `shannon-whitebox logs <workspace> --follow` in a second window shows the per-line log.
