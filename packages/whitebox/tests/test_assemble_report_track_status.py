"""Task 6 (fix): inject_gitnexus_track_status activity — report-executive 之后注入。

**Spec gap fix (coordinator-adjudicated):** 原 Task 6 把 banner 注入在
assemble_report 里,但 workflow 顺序 assemble_report → run_agent("report")
(report-executive 重写整个 comprehensive_security_assessment_report.md)
→ inject_attack_chains,导致 banner 被 report-executive 覆盖丢失。对齐
inject_attack_chains 模式,新 activity 在 report-executive 之后注入。

铁律(CLAUDE.md §1):track_status 是 workflow/merger/report 编排产物,report 层读合法。
本测试只验 report 层渲染注记,不动合并逻辑、不喂 LLM 轨 prompt。
"""
import json

from shannon_whitebox.pipeline import activities
from shannon_whitebox.pipeline.shared import ActivityInput


def _write_report(deliverables, content: str) -> None:
    """模拟 report-executive 之后的状态:综合报告已存在(无 banner)。"""
    (deliverables / "comprehensive_security_assessment_report.md").write_text(
        content, encoding="utf-8")


def _write_track_status(deliverables, statuses: dict) -> None:
    (deliverables / "gitnexus_track_status.json").write_text(
        json.dumps(statuses), encoding="utf-8")


async def test_inject_gitnexus_track_status_failed_note(tmp_path, monkeypatch):
    """failed 类(xss)+ 报告已存在 → 调 activity 后 banner 注入到报告顶部。"""
    deliverables = tmp_path / "whitebox"
    deliverables.mkdir(parents=True)
    _write_track_status(deliverables, {
        "xss": {"status": "failed", "reason": "builder raised: KeyError"}})
    # 模拟 report-executive 之后:报告已被 agent 重写,banner 不在其中
    _write_report(deliverables, "# 安全评估报告\n\n## 执行摘要\n\n正文...\n")
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, deliverables, tmp_path))

    await activities.inject_gitnexus_track_status(
        ActivityInput(repo_path=str(tmp_path)))

    report = (deliverables / "comprehensive_security_assessment_report.md").read_text(
        encoding="utf-8")
    assert "GitNexus 轨判定失败" in report
    assert "xss" in report
    assert "builder raised: KeyError" in report
    assert "## GitNexus 轨判定状态" in report
    # 原报告内容保留
    assert "## 执行摘要" in report
    # banner 位于报告顶部(在原 H1 之后,作为独立 H2 章节)
    assert report.index("## GitNexus 轨判定状态") < report.index("## 执行摘要")


async def test_inject_gitnexus_track_status_multiple_failed(tmp_path, monkeypatch):
    """多个 failed 类(injection + xss)都列出,每条一行;ok 类(ssrf)不入注记。"""
    deliverables = tmp_path / "whitebox"
    deliverables.mkdir(parents=True)
    _write_track_status(deliverables, {
        "injection": {"status": "failed", "reason": "builder raised: ValueError"},
        "xss": {"status": "failed", "reason": "parameter_graph invalid"},
        "ssrf": {"status": "ok", "findings": 0},
    })
    _write_report(deliverables, "# 安全评估报告\n")
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, deliverables, tmp_path))

    await activities.inject_gitnexus_track_status(
        ActivityInput(repo_path=str(tmp_path)))

    report = (deliverables / "comprehensive_security_assessment_report.md").read_text(
        encoding="utf-8")
    assert "- injection: GitNexus 轨判定失败(builder raised: ValueError)" in report
    assert "- xss: GitNexus 轨判定失败(parameter_graph invalid)" in report
    # ssrf 是 ok,不应出现在注记里
    assert "ssrf" not in report


async def test_inject_gitnexus_track_status_no_failed_noop(tmp_path, monkeypatch):
    """无 failed 类(全 ok)→ 不改报告。"""
    deliverables = tmp_path / "whitebox"
    deliverables.mkdir(parents=True)
    _write_track_status(deliverables, {"xss": {"status": "ok", "findings": 2}})
    original = "# 安全评估报告\n\n正文\n"
    _write_report(deliverables, original)
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, deliverables, tmp_path))

    await activities.inject_gitnexus_track_status(
        ActivityInput(repo_path=str(tmp_path)))

    report = (deliverables / "comprehensive_security_assessment_report.md").read_text(
        encoding="utf-8")
    assert report == original


async def test_inject_gitnexus_track_status_idempotent(tmp_path, monkeypatch):
    """幂等:报告已含 banner 标题 → 再跑不重复注入。"""
    deliverables = tmp_path / "whitebox"
    deliverables.mkdir(parents=True)
    _write_track_status(deliverables, {
        "xss": {"status": "failed", "reason": "builder raised"}})
    # 报告已含 banner(模拟 resume / 二次跑)
    existing = (
        "## GitNexus 轨判定状态\n\n"
        "- xss: GitNexus 轨判定失败(builder raised),结果由 LLM 轨提供\n\n"
        "---\n\n# 安全评估报告\n"
    )
    _write_report(deliverables, existing)
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, deliverables, tmp_path))

    await activities.inject_gitnexus_track_status(
        ActivityInput(repo_path=str(tmp_path)))
    await activities.inject_gitnexus_track_status(
        ActivityInput(repo_path=str(tmp_path)))  # 二次跑

    report = (deliverables / "comprehensive_security_assessment_report.md").read_text(
        encoding="utf-8")
    assert report.count("## GitNexus 轨判定状态") == 1
    assert report.count(
        "- xss: GitNexus 轨判定失败(builder raised)") == 1


async def test_inject_gitnexus_track_status_missing_report(tmp_path, monkeypatch):
    """报告文件不存在 → 不抛(activity 直接 return)。"""
    deliverables = tmp_path / "whitebox"
    deliverables.mkdir(parents=True)
    _write_track_status(deliverables, {
        "xss": {"status": "failed", "reason": "x"}})
    # 不写 comprehensive_security_assessment_report.md
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, deliverables, tmp_path))

    # 不抛
    await activities.inject_gitnexus_track_status(
        ActivityInput(repo_path=str(tmp_path)))

    # 也未创建报告文件
    assert not (deliverables / "comprehensive_security_assessment_report.md").exists()


async def test_inject_gitnexus_track_status_missing_status_file(tmp_path, monkeypatch):
    """gitnexus_track_status.json 缺失(read_track_status 返 {})-> 不注入,不抛。"""
    deliverables = tmp_path / "whitebox"
    deliverables.mkdir(parents=True)
    original = "# 安全评估报告\n"
    _write_report(deliverables, original)
    # 不写 gitnexus_track_status.json
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, deliverables, tmp_path))

    await activities.inject_gitnexus_track_status(
        ActivityInput(repo_path=str(tmp_path)))

    report = (deliverables / "comprehensive_security_assessment_report.md").read_text(
        encoding="utf-8")
    assert report == original
