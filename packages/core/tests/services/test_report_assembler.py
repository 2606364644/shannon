import pytest

from supernova_core.services.report_assembler import ReportAssembler, count_vuln_headings


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


# ── report-executive 后校验（report 页 0 漏洞回归防复发）─────────────────────

def test_count_vuln_headings_matches_frontend_pattern():
    """数节正则对齐前端 vuln-block.ts VULN_HEADING_RE:兼容 -VULN-/-GN- 双轨 ID,
    排除小写 chain 节(### llm-chain-N)与非漏洞标题。"""
    text = (
        "# 安全评估报告\n"
        "## 执行摘要\n"
        "### INJECTION-VULN-01: SQL 注入\n"
        "### AUTHZ-GN-03: 越权访问\n"
        "### llm-chain-1: 多步链\n"
        "### 其他标题\n"
        "正文行内的 INJECTION-VULN-02 引用不算节。\n"
    )
    assert count_vuln_headings(text) == 2


@pytest.mark.asyncio
async def test_verify_vuln_block_coverage_detects_compressed_report(tmp_path):
    """report-executive 把正文压成「模式汇总+行内 ID 引用」→ 覆盖校验暴露缺口。

    回归(2026-08-19 另一环境):report agent 自写 cleanup 脚本丢掉全部 ### ID 结构节,
    前端 splitByVulnBlocks 解析 0 节 → 报告页统计全 0。actual < expected 即事故形态。"""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "auth_findings.md").write_text(
        "### AUTH-VULN-01: 弱密码策略\n证据A\n### AUTH-VULN-02: 无锁定机制\n证据B\n"
    )
    report_path = deliverables / "comprehensive_security_assessment_report.md"
    await ReportAssembler.assemble(deliverables, ["auth"], report_path)
    # 模拟 agent 压缩:只剩摘要 + 行内 ID 引用(ID 没删,但节没了——正是误导点)
    report_path.write_text(
        "## 执行摘要\n\n认证整体薄弱(AUTH-VULN-01、AUTH-VULN-02 呈同一模式)。\n"
    )

    actual, expected = await ReportAssembler.verify_vuln_block_coverage(
        deliverables, ["auth"], report_path)

    assert expected == 2   # 底稿口径:2 个结构节
    assert actual == 0     # agent 版:0 个 → 缺口暴露


@pytest.mark.asyncio
async def test_verify_vuln_block_coverage_passes_when_intact(tmp_path):
    """agent 正常加工(加了摘要、节全保留)→ actual == expected,不误报。"""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "auth_findings.md").write_text(
        "### AUTH-VULN-01: 弱密码策略\n证据A\n### AUTH-VULN-02: 无锁定机制\n证据B\n"
    )
    report_path = deliverables / "comprehensive_security_assessment_report.md"
    await ReportAssembler.assemble(deliverables, ["auth"], report_path)
    # agent 合法加工:顶部加摘要,节原样保留
    content = report_path.read_text()
    report_path.write_text("## 执行摘要\n\n共 2 个认证类漏洞。\n\n---\n\n" + content)

    actual, expected = await ReportAssembler.verify_vuln_block_coverage(
        deliverables, ["auth"], report_path)

    assert actual == expected == 2


@pytest.mark.asyncio
async def test_verify_vuln_block_coverage_expected_zero_when_no_deliverables(tmp_path):
    """无 per-class deliverables(无漏洞扫描)→ expected=0,不误报。"""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    report_path = deliverables / "comprehensive_security_assessment_report.md"
    report_path.write_text("## 执行摘要\n\n未发现漏洞。\n")

    actual, expected = await ReportAssembler.verify_vuln_block_coverage(
        deliverables, ["auth", "ssrf"], report_path)

    assert actual == 0
    assert expected == 0
