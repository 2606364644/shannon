from contextlib import asynccontextmanager

from shannon_whitebox.pipeline.activities import (
    log_phase_start_activity, log_phase_complete_activity,
)
from shannon_whitebox.pipeline.shared import ActivityInput
from shannon_whitebox.audit.session_registry import (
    set_audit_session, clear_audit_session,
)


class _RecordingSession:
    def __init__(self) -> None:
        self.phases_started: list[tuple[str, tuple[str, ...]]] = []
        self.phases_completed: list[str] = []
        self.steps: list[tuple[str, str, str]] = []   # (name, phase, event)

    async def log_phase_start(self, phase: str, steps: tuple[str, ...] = (),
                              step_intents: tuple[str | None, ...] = ()) -> None:
        self.phases_started.append((phase, tuple(steps)))

    async def log_phase_complete(self, phase: str) -> None:
        self.phases_completed.append(phase)

    async def log_step(self, name: str, phase: str, event: str, **kw) -> None:
        self.steps.append((name, phase, event))

    # requires `from contextlib import asynccontextmanager` at the test file top
    @asynccontextmanager
    async def track_step(self, phase: str, name: str, intent: str | None = None):
        await self.log_step(name, phase, "start")
        try:
            yield
        except Exception:
            await self.log_step(name, phase, "complete", error="x")
            raise
        await self.log_step(name, phase, "complete")


async def test_phase_marker_activities_call_session():
    rec = _RecordingSession()
    set_audit_session(rec)
    try:
        await log_phase_start_activity(ActivityInput(repo_path=".", workspace_name="recon"))
        await log_phase_complete_activity(ActivityInput(repo_path=".", workspace_name="recon"))
    finally:
        clear_audit_session()
    assert rec.phases_started == [("recon", ())]
    assert rec.phases_completed == ["recon"]


async def test_phase_marker_falls_back_to_unknown_when_no_workspace():
    rec = _RecordingSession()
    set_audit_session(rec)
    try:
        await log_phase_start_activity(ActivityInput(repo_path="."))
    finally:
        clear_audit_session()
    assert rec.phases_started == [("unknown", ())]


async def test_phase_marker_activity_passes_steps():
    rec = _RecordingSession()
    set_audit_session(rec)
    try:
        await log_phase_start_activity(
            ActivityInput(repo_path=".", workspace_name="pre-recon"),
            steps=["code-index", "pre-recon", "merge-sinks"])
    finally:
        clear_audit_session()
    assert rec.phases_started == [("pre-recon", ("code-index", "pre-recon", "merge-sinks"))]


async def test_phase_marker_backward_compat_no_steps():
    rec = _RecordingSession()
    set_audit_session(rec)
    try:
        await log_phase_start_activity(ActivityInput(repo_path=".", workspace_name="recon"))
    finally:
        clear_audit_session()
    assert rec.phases_started == [("recon", ())]


async def test_save_adjudication_emits_step_events(monkeypatch):
    """Representative deterministic activity wrapped in track_step."""
    import shannon_whitebox.pipeline.activities as act
    import shannon_core.code_index as ci
    monkeypatch.setattr(ci, "save_adjudication", lambda d: None)   # stub function-level import
    monkeypatch.setattr(act, "_get_paths", lambda inp: ("repo", "deliverables", "ws"))
    rec = _RecordingSession()
    set_audit_session(rec)
    try:
        await act.run_save_adjudication(ActivityInput(repo_path="repo"))
    finally:
        clear_audit_session()
    events = [(n, e) for (n, _ph, e) in rec.steps]
    assert ("adjudication", "start") in events
    assert ("adjudication", "complete") in events
