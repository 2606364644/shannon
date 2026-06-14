from shannon_whitebox.pipeline.activities import (
    log_phase_start_activity, log_phase_complete_activity,
)
from shannon_whitebox.pipeline.shared import ActivityInput
from shannon_whitebox.audit.session_registry import (
    set_audit_session, clear_audit_session,
)


class _RecordingSession:
    def __init__(self) -> None:
        self.phases_started: list[str] = []
        self.phases_completed: list[str] = []

    async def log_phase_start(self, phase: str) -> None:
        self.phases_started.append(phase)

    async def log_phase_complete(self, phase: str) -> None:
        self.phases_completed.append(phase)


async def test_phase_marker_activities_call_session():
    rec = _RecordingSession()
    set_audit_session(rec)
    try:
        await log_phase_start_activity(ActivityInput(repo_path=".", workspace_name="recon"))
        await log_phase_complete_activity(ActivityInput(repo_path=".", workspace_name="recon"))
    finally:
        clear_audit_session()
    assert rec.phases_started == ["recon"]
    assert rec.phases_completed == ["recon"]


async def test_phase_marker_falls_back_to_unknown_when_no_workspace():
    rec = _RecordingSession()
    set_audit_session(rec)
    try:
        await log_phase_start_activity(ActivityInput(repo_path="."))
    finally:
        clear_audit_session()
    assert rec.phases_started == ["unknown"]
