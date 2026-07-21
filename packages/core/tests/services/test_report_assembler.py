import pytest

from supernova_core.services.report_assembler import ReportAssembler


@pytest.mark.asyncio
async def test_assemble_produces_report_even_when_some_classes_missing(tmp_path):
    """部分 per-class deliverable 缺 → assemble 仍产 comprehensive report(底稿兜底)。

    ReportAssembler.assemble 是 host 拼接(不依赖 agent):对每个 class 做三级回退,
    缺失的 class 直接跳过,最后一定写盘——所以即使大部分 class 缺,底稿文件仍产生。
    这是 report 不需要 collector 治本的根本原因(agent 覆写失败时底稿仍在)。
    """
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    # 只给一个 class 的 analysis deliverable,其余缺
    (deliverables / "injection_analysis_deliverable.md").write_text("# INJ findings\n...")
    report_path = deliverables / "comprehensive_security_assessment_report.md"

    await ReportAssembler.assemble(deliverables, ["injection", "xss", "auth"], report_path)

    assert report_path.exists()  # 底稿一定产生
    content = report_path.read_text()
    assert "INJ findings" in content  # 给了的 class 进报告


@pytest.mark.asyncio
async def test_assemble_falls_back_through_three_levels(tmp_path):
    """三级回退:evidence → findings → analysis_deliverable(report_assembler.py 三级 if)。"""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "xss_findings.md").write_text("# XSS findings\n")  # 只有 findings 级
    report_path = deliverables / "comprehensive_security_assessment_report.md"

    await ReportAssembler.assemble(deliverables, ["xss"], report_path)
    assert report_path.exists() and "XSS findings" in report_path.read_text()


@pytest.mark.asyncio
async def test_assemble_writes_report_even_when_all_classes_missing(tmp_path):
    """所有 per-class deliverable 都缺 → sections 为空,assemble 仍写盘(空文件)。

    极端容错:底稿文件一定存在(validate_deliverable 查存在性即过),哪怕内容为空。
    这是 report 不会触发 Missing deliverable 的最后一道保证。
    """
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    report_path = deliverables / "comprehensive_security_assessment_report.md"

    await ReportAssembler.assemble(deliverables, ["injection", "xss", "auth"], report_path)

    assert report_path.exists()  # 即便全空,底稿仍写盘
    assert report_path.read_text() == ""
