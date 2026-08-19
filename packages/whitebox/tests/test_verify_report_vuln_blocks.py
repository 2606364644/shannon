"""report-executive 后校验 + 自愈 activity(report 页 0 漏洞回归防复发)。

回归(2026-08-19 另一环境):report agent 自写 cleanup 脚本把报告正文压成
「模式汇总+行内 ID 引用」,丢掉全部 ### ID 结构节——前端 splitByVulnBlocks
解析 0 节 → 报告页统计全 0、PoC 无卡片可并。prompt 三处锁定节格式仍被绕过,
故加确定性防线:节数不足 → 重新 assemble 覆盖 agent 版(丢执行摘要、保漏洞数据)。
"""
import supernova_whitebox.pipeline.activities as act
from supernova_whitebox.pipeline.shared import ActivityInput


def _setup(tmp_path, monkeypatch, findings_text):
    """铺 deliverables(findings 底稿)+ monkeypatch _get_paths,返回 report path。"""
    deliverables = tmp_path / "whitebox"
    deliverables.mkdir()
    (deliverables / "auth_findings.md").write_text(findings_text, encoding="utf-8")
    report = deliverables / "comprehensive_security_assessment_report.md"
    monkeypatch.setattr(act, "_get_paths", lambda inp: (tmp_path, deliverables, tmp_path))
    return report


async def test_verify_passes_when_blocks_intact(tmp_path, monkeypatch):
    """agent 正常加工(节全保留)→ 校验通过,文件不动。"""
    findings = "### AUTH-VULN-01: 弱密码\n证据A\n### AUTH-VULN-02: 无锁定\n证据B\n"
    report = _setup(tmp_path, monkeypatch, findings)
    report.write_text("## 执行摘要\n\n共2漏洞。\n\n---\n\n" + findings, encoding="utf-8")
    before = report.read_text(encoding="utf-8")

    await act.verify_report_vuln_blocks(
        ActivityInput(repo_path=str(tmp_path), vuln_classes=["auth"]))

    assert report.read_text(encoding="utf-8") == before  # 原样


async def test_verify_rebuilds_when_blocks_lost(tmp_path, monkeypatch, caplog):
    """agent 压缩正文(模式汇总+行内引用,0 节)→ 自愈:重建底稿版(节回来)。"""
    findings = "### AUTH-VULN-01: 弱密码\n证据A\n### AUTH-VULN-02: 无锁定\n证据B\n"
    report = _setup(tmp_path, monkeypatch, findings)
    report.write_text(
        "## 执行摘要\n\n认证整体薄弱(AUTH-VULN-01、AUTH-VULN-02 同一模式)。\n",
        encoding="utf-8")

    import logging
    with caplog.at_level(logging.WARNING, logger="supernova_whitebox.pipeline.activities"):
        await act.verify_report_vuln_blocks(
            ActivityInput(repo_path=str(tmp_path), vuln_classes=["auth"]))

    content = report.read_text(encoding="utf-8")
    assert "### AUTH-VULN-01" in content      # 节恢复
    assert "### AUTH-VULN-02" in content
    assert "证据A" in content                  # 证据随节恢复
    assert "覆盖不足" in caplog.text           # 告警可观测


async def test_verify_noop_when_no_deliverables(tmp_path, monkeypatch):
    """无底稿(无漏洞扫描,expected=0)→ agent 摘要版不动。"""
    deliverables = tmp_path / "whitebox"
    deliverables.mkdir()
    report = deliverables / "comprehensive_security_assessment_report.md"
    report.write_text("## 执行摘要\n\n未发现漏洞。\n", encoding="utf-8")
    monkeypatch.setattr(act, "_get_paths", lambda inp: (tmp_path, deliverables, tmp_path))

    await act.verify_report_vuln_blocks(
        ActivityInput(repo_path=str(tmp_path), vuln_classes=["auth"]))

    assert report.read_text(encoding="utf-8") == "## 执行摘要\n\n未发现漏洞。\n"


async def test_verify_noop_when_report_missing(tmp_path, monkeypatch):
    """主报告不存在(agent 失败)→ 直接 return 不崩。"""
    findings = "### AUTH-VULN-01: 弱密码\n证据A\n"
    report = _setup(tmp_path, monkeypatch, findings)  # _setup 不创建 report 文件
    assert not report.exists()

    await act.verify_report_vuln_blocks(
        ActivityInput(repo_path=str(tmp_path), vuln_classes=["auth"]))  # 不抛


async def test_verify_swallows_errors(tmp_path, monkeypatch):
    """校验链路任何异常 → 吞掉(不阻塞主报告,只 warning)。"""
    def _boom(inp):
        raise RuntimeError("boom")
    monkeypatch.setattr(act, "_get_paths", _boom)

    await act.verify_report_vuln_blocks(
        ActivityInput(repo_path=str(tmp_path), vuln_classes=["auth"]))  # 不抛
