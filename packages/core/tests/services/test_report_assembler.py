import pytest

from supernova_core.models.queue_schemas import (
    InjectionVulnerability,
    VulnerabilityQueue,
    XssVulnerability,
)
from supernova_core.services.report_assembler import (
    ReportAssembler,
    count_vuln_headings,
    render_summary_table,
)


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


# ── 漏洞速查表（spec 2026-08-25 §7：正文第一章，渲染层确定性注入）─────────────


def _sv(id_, severity, params, endpoint="POST /contributions", **kw):
    fields = dict(
        ID=id_, vulnerability_type="injection", externally_exploitable=True,
        confidence="high", title="命令注入", severity=severity,
        endpoint=endpoint, affected_parameters=params)
    fields.update(kw)  # confidence/verification 等可覆盖默认
    return InjectionVulnerability(**fields)


def test_summary_table_sorted_with_endpoint_params(monkeypatch):
    """plan Task 6 主用例：severity 降序、endpoint/参数列、严重度中文、验证缺省。"""
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "zh")
    table = render_summary_table({"injection": [
        _sv("INJ-VULN-02", "medium", ["threshold"], endpoint="GET /allocations/:userId"),
        _sv("INJ-VULN-01", "critical", ["preTax", "afterTax", "roth"])]})
    assert "## 漏洞速查表" in table
    assert "| ID | 漏洞 | 接口 | 参数 | 严重度 | 验证 | 置信度 |" in table
    rows = [l for l in table.splitlines() if l.startswith("| INJ-")]
    assert rows[0].startswith("| INJ-VULN-01")     # critical 在前
    assert "POST /contributions" in rows[0] and "preTax" in rows[0]
    assert "严重" in rows[0] and "静态分析" in rows[0]
    assert rows[1].startswith("| INJ-VULN-02")


def test_summary_table_endpoint_falls_back_to_path_extract(monkeypatch):
    """endpoint 缺省回退 extract_endpoint(path)（含 ' → file:line' 尾巴归一化）；
    两者都没有显示 '-'。"""
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "zh")
    table = render_summary_table({"injection": [
        _sv("INJ-VULN-10", "high", ["q"], endpoint=None,
            path="POST /search?q=1 → app.js:12"),
        _sv("INJ-VULN-11", "low", ["id"], endpoint=None, path="no route here"),
    ]})
    rows = [l for l in table.splitlines() if l.startswith("| INJ-")]
    assert "POST /search" in rows[0]          # query + 尾巴被剥掉
    assert "| - |" in rows[1]                 # 无 endpoint 且 path 无可提取路由


def test_summary_table_params_over_three_collapsed(monkeypatch):
    """参数 >3 个：取前 3 join + '等 N 个'；≤3 全量 join。"""
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "zh")
    table = render_summary_table({"injection": [
        _sv("INJ-VULN-20", "high", ["a", "b", "c", "d", "e"]),
        _sv("INJ-VULN-21", "medium", ["x", "y"]),
    ]})
    rows = [l for l in table.splitlines() if l.startswith("| INJ-")]
    assert "a、b、c 等 5 个" in rows[0]
    assert "| x、y |" in rows[1]


def test_summary_table_class_sections_title_fallback_empty_class(monkeypatch):
    """类别标题复用 findings_renderer CLASS_CONFIG heading（渲染层生成）；
    title 缺省回退 vulnerability_type 类中文名；空类输出一行 none_* 文案。"""
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "zh")
    table = render_summary_table({
        "xss": [XssVulnerability(ID="XSS-VULN-01", vulnerability_type="xss",
                                 externally_exploitable=False, confidence="medium")],
        "injection": [InjectionVulnerability(
            ID="INJ-VULN-30", vulnerability_type="injection",
            externally_exploitable=True, confidence="high")],  # 无 title
        "ssrf": [],  # 空类
    })
    assert "### 注入漏洞" in table and "### 跨站脚本 (XSS)" in table
    # 类段落按 CLASS_CONFIG 配置序（injection 先于 xss），不受调用方 dict 序影响
    assert table.index("### 注入漏洞") < table.index("### 跨站脚本 (XSS)")
    # title 缺省 → vulnerability_type 中文名（同类标题，勿自造映射）
    inj_row = [l for l in table.splitlines() if l.startswith("| INJ-")][0]
    assert "| 注入漏洞 |" in inj_row
    # 空类输出一行"本类无发现"（复用 none_* message）
    assert "未发现 SSRF 漏洞。" in table


def test_summary_table_severity_fallback_rules_and_stable_order(monkeypatch):
    """severity 缺省走 effective_severity 兜底（RCE sink→critical、injection→high）；
    同档保持队列稳定序。"""
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "zh")
    table = render_summary_table({"injection": [
        _sv("INJ-VULN-41", None, ["x"]),                              # 兜底: injection → high
        _sv("INJ-VULN-42", "high", ["y"]),
        _sv("INJ-VULN-43", None, ["z"], sink_call="eval(userInput)"),  # 兜底: RCE sink → critical
    ]})
    rows = [l for l in table.splitlines() if l.startswith("| INJ-")]
    assert rows[0].startswith("| INJ-VULN-43") and "严重" in rows[0]
    assert rows[1].startswith("| INJ-VULN-41") and rows[2].startswith("| INJ-VULN-42")
    assert "高危" in rows[1] and "高危" in rows[2]


def test_summary_table_verification_and_confidence_mapping(monkeypatch):
    """verification 枚举映射中文（dynamically_verified→已动态验证）；
    confidence high/medium/low → 高/中/低。"""
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "zh")
    table = render_summary_table({"injection": [
        _sv("INJ-VULN-50", "high", ["a"], confidence="medium",
            verification="dynamically_verified"),
        _sv("INJ-VULN-51", "medium", ["b"], confidence="low",
            verification="static_analysis"),
    ]})
    rows = [l for l in table.splitlines() if l.startswith("| INJ-")]
    assert "已动态验证" in rows[0] and rows[0].endswith("| 中 |")
    assert "静态分析" in rows[1] and rows[1].endswith("| 低 |")


def test_summary_table_confidence_internal_labels_not_leaked(monkeypatch):
    """F2（终审）：merger 单轨分支写 confidence="needs_review"（及其它任意
    未知非空值）→ 置信度列显示「待复核」，内部标签不进中文正文。"""
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "zh")
    table = render_summary_table({"injection": [
        _sv("INJ-VULN-60", "high", ["a"], confidence="needs_review"),
        _sv("INJ-VULN-61", "medium", ["b"], confidence="some-internal-tag"),
    ]})
    assert "needs_review" not in table
    assert "some-internal-tag" not in table
    rows = [l for l in table.splitlines() if l.startswith("| INJ-")]
    assert rows[0].endswith("| 待复核 |")
    assert rows[1].endswith("| 待复核 |")


def test_summary_table_confidence_pending_review_en(monkeypatch):
    """F2 en 版：needs_review → Pending Review（走 _M 双语）。"""
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "en")
    table = render_summary_table({"injection": [
        _sv("INJ-VULN-62", "high", ["a"], confidence="needs_review")]})
    assert "needs_review" not in table
    row = [l for l in table.splitlines() if l.startswith("| INJ-")][0]
    assert row.endswith("| Pending Review |")


def test_summary_table_en_lang(monkeypatch):
    """en 报告：标题/表头/类标题/验证/置信度全走 Messages 双语。"""
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "en")
    table = render_summary_table({"injection": [_sv("INJ-VULN-01", "high", ["preTax"])]})
    assert "## Vulnerability Summary Table" in table
    assert ("| ID | Vulnerability | Endpoint | Parameters | Severity | "
            "Verification | Confidence |") in table
    assert "### Injection Vulnerabilities" in table
    row = [l for l in table.splitlines() if l.startswith("| INJ-")][0]
    assert "Static Analysis" in row and row.endswith("| High |")


def test_summary_table_severity_localized_en(monkeypatch):
    """F7a：en 报告速查表严重度列首字母大写（Critical/High/Medium/Low），
    不再无条件 SEVERITY_ZH 夹中文（严重/高危/中危/低危）。"""
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "en")
    table = render_summary_table({"injection": [
        _sv("INJ-VULN-71", "critical", ["a"]),
        _sv("INJ-VULN-72", "high", ["b"]),
        _sv("INJ-VULN-73", "medium", ["c"]),
        _sv("INJ-VULN-74", "low", ["d"]),
    ]})
    rows = [l for l in table.splitlines() if l.startswith("| INJ-")]
    assert "| Critical |" in rows[0] and "| High |" in rows[1]
    assert "| Medium |" in rows[2] and "| Low |" in rows[3]
    for zh_word in ("严重", "高危", "中危", "低危"):
        assert zh_word not in table


@pytest.mark.asyncio
async def test_assemble_injects_summary_table_as_first_section(tmp_path):
    """queue 可读 → 速查表注入 sections[0]（正文第一章），后接 per-class 产物。"""
    deliverables = tmp_path / "deliverables"
    (deliverables / "intermediate").mkdir(parents=True)
    queue = VulnerabilityQueue(vulnerabilities=[
        InjectionVulnerability(
            ID="INJ-VULN-01", vulnerability_type="injection",
            externally_exploitable=True, confidence="high",
            title="命令注入", severity="critical",
            endpoint="POST /contributions", affected_parameters=["preTax"])])
    (deliverables / "intermediate" / "injection_exploitation_queue.json").write_text(
        queue.model_dump_json(), encoding="utf-8")
    (deliverables / "injection_analysis_deliverable.md").write_text(
        "### INJ-VULN-01: 命令注入\n详情", encoding="utf-8")
    report_path = deliverables / "comprehensive_security_assessment_report.md"

    await ReportAssembler.assemble(deliverables, ["injection"], report_path)

    content = report_path.read_text(encoding="utf-8")
    assert "## 漏洞速查表" in content
    # 速查表在最前（report-executive 后续在其上加执行摘要 → 速查表成为正文第一章）
    assert content.index("## 漏洞速查表") < content.index("### INJ-VULN-01: 命令注入")
    assert "| INJ-VULN-01 |" in content
    assert "\n\n---\n\n" in content  # 与正文按既有分隔符拼接


@pytest.mark.asyncio
async def test_assemble_without_queues_keeps_report_unchanged(tmp_path):
    """queue 全缺（analysis-only 底稿兜底 / 黑盒 blackbox/ 目录）→ 不注入速查表。"""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "auth_analysis_deliverable.md").write_text("AUTH", encoding="utf-8")
    report_path = deliverables / "comprehensive_security_assessment_report.md"

    await ReportAssembler.assemble(deliverables, ["auth"], report_path)

    assert report_path.read_text(encoding="utf-8") == "AUTH"


@pytest.mark.asyncio
async def test_verify_vuln_block_coverage_unaffected_by_summary_table(tmp_path):
    """速查表行(| ID |)不是 ### ID 节 → 注入不改变覆盖校验口径。"""
    deliverables = tmp_path / "deliverables"
    (deliverables / "intermediate").mkdir(parents=True)
    queue = VulnerabilityQueue(vulnerabilities=[
        InjectionVulnerability(ID="INJ-VULN-01", vulnerability_type="injection",
                               externally_exploitable=True, confidence="high")])
    (deliverables / "intermediate" / "injection_exploitation_queue.json").write_text(
        queue.model_dump_json(), encoding="utf-8")
    (deliverables / "injection_findings.md").write_text(
        "### INJ-VULN-01: 命令注入\n证据\n", encoding="utf-8")
    report_path = deliverables / "comprehensive_security_assessment_report.md"

    await ReportAssembler.assemble(deliverables, ["injection"], report_path)
    actual, expected = await ReportAssembler.verify_vuln_block_coverage(
        deliverables, ["injection"], report_path)

    assert actual == expected == 1
