"""Task 6: assemble_report 读 gitnexus_track_status.json 标红 failed 类。

验证 `assemble_report` 在 ReportAssembler 写出综合报告后,读 Task 1 的
`gitnexus_track_status.json`,若有 failed 类,在报告顶部注入注记章节
`## GitNexus 轨判定状态`,逐条列出 failed 类 + reason。

铁律(CLAUDE.md §1):track_status 是 workflow/merger/report 编排产物,report 屨读合法。
本测试只验 report 层渲染注记,不动合并逻辑、不喂 LLM 轨 prompt。
"""
import json
from contextlib import asynccontextmanager

import shannon_whitebox.pipeline.activities as act
from shannon_whitebox.pipeline.shared import ActivityInput
from shannon_whitebox.audit.session_registry import (
    set_audit_session, clear_audit_session,
)


class _RecordingSession:
    """最小 audit session stub — 仅满足 assemble_report 的 track_step 调用。"""

    @asynccontextmanager
    async def track_step(self, phase: str, name: str, intent: str | None = None):
        yield


def _setup(tmp_path, monkeypatch, deliverables):
    deliverables.mkdir(parents=True)
    monkeypatch.setattr(act, "_get_paths", lambda inp: (tmp_path, deliverables, tmp_path))
    set_audit_session(_RecordingSession())


async def test_report_includes_gitnexus_failed_note(tmp_path, monkeypatch):
    """GitNexus 轨 failed 的类(xss),在报告顶部加注记 + reason,结果由 LLM 轨提供。"""
    deliverables = tmp_path / "deliverables" / "whitebox"
    _setup(tmp_path, monkeypatch, deliverables)
    # Task 1 产物:xss GitNexus 轨 failed
    (deliverables / "gitnexus_track_status.json").write_text(
        json.dumps({"xss": {"status": "failed", "reason": "builder raised: KeyError"}}),
        encoding="utf-8",
    )
    # xss 分析产物(让 ReportAssembler 有内容可拼)
    (deliverables / "xss_analysis_deliverable.md").write_text(
        "# XSS 分析报告\n\nXSS-VULN-01\n", encoding="utf-8")

    try:
        await act.assemble_report(ActivityInput(repo_path=str(tmp_path)))
    finally:
        clear_audit_session()

    report = (deliverables / "comprehensive_security_assessment_report.md").read_text(
        encoding="utf-8")
    assert "GitNexus 轨判定失败" in report
    assert "xss" in report
    assert "builder raised: KeyError" in report
    # 注记应位于报告顶部(failed 类汇总摘要,先于 vuln 章节)
    assert report.index("GitNexus 轨判定失败") < report.index("XSS 分析报告")


async def test_report_includes_multiple_failed_classes(tmp_path, monkeypatch):
    """多个 failed 类(injection + xss)都列出,每条一行。"""
    deliverables = tmp_path / "deliverables" / "whitebox"
    _setup(tmp_path, monkeypatch, deliverables)
    (deliverables / "gitnexus_track_status.json").write_text(
        json.dumps({
            "injection": {"status": "failed", "reason": "builder raised: ValueError"},
            "xss": {"status": "failed", "reason": "parameter_graph invalid"},
            "ssrf": {"status": "ok", "findings": 0},
        }),
        encoding="utf-8",
    )
    (deliverables / "xss_analysis_deliverable.md").write_text(
        "# XSS 分析报告\n", encoding="utf-8")

    try:
        await act.assemble_report(ActivityInput(repo_path=str(tmp_path)))
    finally:
        clear_audit_session()

    report = (deliverables / "comprehensive_security_assessment_report.md").read_text(
        encoding="utf-8")
    assert "injection" in report and "xss" in report
    assert "builder raised: ValueError" in report
    assert "parameter_graph invalid" in report
    # ssrf 是 ok,不应出现注记行
    assert "ssrf" not in report.lower() or "ssrf" not in [
        line.split(":")[0].strip("- ").strip() for line in report.splitlines()
        if "GitNexus 轨判定失败" in line]


async def test_report_no_note_when_all_ok(tmp_path, monkeypatch):
    """GitNexus 轨全部 ok 时,报告不应有 failed 注记章节。"""
    deliverables = tmp_path / "deliverables" / "whitebox"
    _setup(tmp_path, monkeypatch, deliverables)
    (deliverables / "gitnexus_track_status.json").write_text(
        json.dumps({"xss": {"status": "ok", "findings": 2}}),
        encoding="utf-8",
    )
    (deliverables / "xss_analysis_deliverable.md").write_text(
        "# XSS 分析报告\n", encoding="utf-8")

    try:
        await act.assemble_report(ActivityInput(repo_path=str(tmp_path)))
    finally:
        clear_audit_session()

    report = (deliverables / "comprehensive_security_assessment_report.md").read_text(
        encoding="utf-8")
    assert "GitNexus 轨判定失败" not in report
    assert "GitNexus 轨判定状态" not in report


async def test_report_no_note_when_track_status_missing(tmp_path, monkeypatch):
    """gitnexus_track_status.json 缺失(read_track_status 返 {})-> 不注入注记,不抛。"""
    deliverables = tmp_path / "deliverables" / "whitebox"
    _setup(tmp_path, monkeypatch, deliverables)
    # 不写 gitnexus_track_status.json
    (deliverables / "xss_analysis_deliverable.md").write_text(
        "# XSS 分析报告\n", encoding="utf-8")

    try:
        await act.assemble_report(ActivityInput(repo_path=str(tmp_path)))
    finally:
        clear_audit_session()

    report = (deliverables / "comprehensive_security_assessment_report.md").read_text(
        encoding="utf-8")
    assert "GitNexus 轨判定失败" not in report
