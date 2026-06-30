from contextlib import asynccontextmanager

import shannon_whitebox.pipeline.activities as act
from shannon_whitebox.pipeline.shared import ActivityInput
from shannon_whitebox.audit.session_registry import (
    set_audit_session, clear_audit_session,
)


class _RecordingSession:
    def __init__(self) -> None:
        self.steps: list[tuple[str, str, str]] = []  # (name, phase, event)

    async def log_step(self, name: str, phase: str, event: str, **kw) -> None:
        self.steps.append((name, phase, event))

    @asynccontextmanager
    async def track_step(self, phase: str, name: str, intent: str | None = None):
        await self.log_step(name, phase, "start")
        try:
            yield
        except Exception:
            await self.log_step(name, phase, "complete", error="x")
            raise
        await self.log_step(name, phase, "complete")


async def test_assemble_report_writes_comprehensive_report(tmp_path, monkeypatch):
    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    (deliverables / "auth_analysis_deliverable.md").write_text(
        "# 认证分析报告\nAUTH-VULN-01", encoding="utf-8")
    monkeypatch.setattr(act, "_get_paths", lambda inp: (tmp_path, deliverables, tmp_path))

    rec = _RecordingSession()
    set_audit_session(rec)
    try:
        await act.assemble_report(ActivityInput(repo_path=str(tmp_path)))
    finally:
        clear_audit_session()

    report = deliverables / "comprehensive_security_assessment_report.md"
    assert report.exists()
    assert "认证分析报告" in report.read_text(encoding="utf-8")
    events = [(n, e) for (n, _ph, e) in rec.steps]
    assert ("assemble-report", "start") in events
    assert ("assemble-report", "complete") in events
