# Live Dashboard Ghost-Frame Fix

## Problem

During a white-box scan, the sticky status footer freezes into duplicate
"ghost" frames: repeated `pre-recon · step 0/8` lines plus their full-width `─`
rules, accumulating in the terminal. The frozen frames also appear not to
re-flow when the terminal is resized.

## Root cause

The scan uses a **transient** Rich `Live` region for the status footer
(`display_lifecycle.py`). Transient mode updates itself via **relative erase**:
each refresh records the previous frame's line count, moves the cursor up that
many lines, and erases them. The footer is correct only as long as that
recorded line count matches the real screen.

In this codebase the count desyncs, because `RichConsoleRenderer` prints
PHASE/STEP/AGENT log lines directly to the **same** `Console` that hosts the
`Live` region. Those prints are not mediated by Rich's live context, so Rich's
erase counter is blind to them; once the count drifts, old frames are left on
screen instead of erased, and the drift compounds into multiple frozen frames.
The frozen frames retain their original width, which is why a resize looks like
it "doesn't follow" — the live frame re-measures each refresh, but the stuck
frames do not.

(Note: three specific trigger hypotheses — a full-width rule, frame-height
oscillation 2<->3 lines, and raw `stderr` injection — were each tested in
isolation against Rich 15.0.0 and **none** reproduced ghosting on its own. The
desync is an interaction effect, not a single-line bug. This makes
root-causing-and-patching fragile; the robust fix is to stop using relative
erase entirely.)

## Fix

Switch the `Live` region from transient relative-erase mode to **alternate
screen, full redraw** mode. Rich then redraws the whole screen on every refresh
— there is no line-count to desync, so ghosting is eliminated by construction
and resize re-flows correctly (Rich re-measures `Console.size` every refresh).

One file, `packages/core/src/shannon_core/audit/display_lifecycle.py`:

```python
# before
live = Live(dashboard, console=console, transient=True,
            refresh_per_second=default_refresh_hz(),
            redirect_stderr=False)
```

```python
# after
live = Live(dashboard, console=console, screen=True, transient=False,
            refresh_per_second=default_refresh_hz(),
            redirect_stdout=True, redirect_stderr=False)
```

### Why each flag

- `screen=True` — alternate screen; Rich does a full repaint each refresh
  instead of relative erase. No erase counter, no desync. Verified across
  height changes, animated spinner, and concurrent `console.print` log lines
  with zero ghost frames.
- `redirect_stdout=True` — keeps `RichConsoleRenderer`'s `console.print` log
  lines inside the managed alternate screen (PHASE/STEP/AGENT stay visible)
  rather than leaking as bare bytes into the live region. Verified the log
  lines and the final summary `Panel` render correctly on the alt screen.
- `transient=False` (was `True`) — so the last footer frame plus the summary
  panel persist onto the main screen when `live.stop()` restores it. Verified:
  exit leaves a clean main screen with final AGENT line + summary panel.
- `redirect_stderr=False` — **unchanged**. This is the documented guard against
  the Temporal workflow-sandbox circular-import failure (Rich's `FileProxy`
  re-imports `rich` inside the sandbox thread -> ImportError -> every workflow
  task fails). Keeping stderr real is required.

## Trade-off

During the scan, the alternate screen has **no scrollback**: you cannot scroll
up to re-read past PHASE/STEP/AGENT lines in the same window. This is the
inherent cost of eliminating ghost frames.

The mitigation already exists in the codebase: the full per-line log streams to
`workflow.log` via `FileLogRenderer`, and is tail-followable in a second
window with `shannon-whitebox logs <workspace> --follow`. The header `Panel`
already advertises this command. After the scan, the summary panel prints to
the main screen normally.

The non-rich (plain) path is untouched.

## Testing

Add a PTY-level integration test that drives the real `Live` configuration
through several height transitions with concurrent log-line prints, captures
the byte stream, and asserts no duplicate `step`/rule frames survive — locking
in the "no ghost frames" invariant. Existing display tests must continue to
pass.

## Out of scope

- Re-deriving the precise in-repo desync trigger (interaction effect; not
  needed once relative erase is removed).
- Any change to `redirect_stderr` (must stay `False`).
- The plain (non-rich) code path.
