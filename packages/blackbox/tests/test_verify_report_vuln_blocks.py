"""黑盒 report-executive 后校验 + 自愈（镜像 whitebox 同名防线）。

黑盒报告链与白盒同构暴露：assemble_report（FindingsRenderer + ReportAssembler
拼 blackbox/ 子目录）→ run_report_agent（同一份 report-executive prompt 重写
报告）→ finalize_report。agent 自写脚本压缩正文丢 ### ID 结构节同样会让前端
报告页统计全 0（2026-08-19 回归在 combined scan 白盒侧爆发，黑盒无防线）。
"""
import pytest

from supernova_blackbox.pipeline import activities
from supernova_blackbox.pipeline.shared import BlackboxActivityInput


def _setup(tmp_path, findings_text):
    """铺 blackbox/ 子目录底稿(findings.md),返回 report path + input。"""
    scan_dir = tmp_path / "scans" / "repo-1"
    scan_dir.mkdir(parents=True)
    bb = scan_dir / "deliverables" / "blackbox"
    bb.mkdir(parents=True)
    (bb / "auth_findings.md").write_text(findings_text, encoding="utf-8")
    report = bb / "comprehensive_security_assessment_report.md"
    inp = BlackboxActivityInput(
        web_url="https://example.com",
        workspace_path=str(scan_dir),
        deliverables_subdir="deliverables",
    )
    return report, inp


@pytest.mark.asyncio
async def test_verify_passes_when_blocks_intact(tmp_path):
    """agent 正常加工(节全保留)→ 校验通过,文件不动。"""
    findings = "### AUTH-VULN-01: 弱密码\n证据A\n### AUTH-VULN-02: 无锁定\n证据B\n"
    report, inp = _setup(tmp_path, findings)
    report.write_text("## 执行摘要\n\n共2漏洞。\n\n---\n\n" + findings, encoding="utf-8")
    before = report.read_text(encoding="utf-8")

    await activities.verify_report_vuln_blocks(inp)

    assert report.read_text(encoding="utf-8") == before  # 原样


@pytest.mark.asyncio
async def test_verify_rebuilds_when_blocks_lost(tmp_path, caplog):
    """agent 压缩正文(0 节)→ 自愈:从 blackbox/ 底稿重建(节回来)。"""
    findings = "### AUTH-VULN-01: 弱密码\n证据A\n### AUTH-VULN-02: 无锁定\n证据B\n"
    report, inp = _setup(tmp_path, findings)
    report.write_text(
        "## 执行摘要\n\n认证整体薄弱(AUTH-VULN-01、AUTH-VULN-02 同一模式)。\n",
        encoding="utf-8")

    import logging
    with caplog.at_level(logging.WARNING,
                         logger="supernova_blackbox.pipeline.activities"):
        await activities.verify_report_vuln_blocks(inp)

    content = report.read_text(encoding="utf-8")
    assert "### AUTH-VULN-01" in content      # 节恢复
    assert "### AUTH-VULN-02" in content
    assert "证据A" in content
    assert "覆盖不足" in caplog.text


@pytest.mark.asyncio
async def test_verify_noop_when_report_missing(tmp_path):
    """主报告不存在(agent 失败)→ 直接 return 不崩。"""
    report, inp = _setup(tmp_path, "### AUTH-VULN-01: 弱密码\n证据A\n")
    assert not report.exists()

    await activities.verify_report_vuln_blocks(inp)  # 不抛


@pytest.mark.asyncio
async def test_verify_swallows_errors(tmp_path):
    """校验链路任何异常 → 吞掉(不阻塞主报告,只 warning)。"""
    scan_dir = tmp_path / "scans" / "repo-1"
    scan_dir.mkdir(parents=True)
    inp = BlackboxActivityInput(
        web_url="https://example.com",
        workspace_path=str(scan_dir),
        deliverables_subdir="deliverables",
    )
    # workspace_path 无 deliverables(异常路径)——校验须吞掉不抛
    await activities.verify_report_vuln_blocks(inp)


# ── workflow 顺序硬约束（镜像 whitebox test_reporting_workflow 源码锚定模式）──

def _workflow_src() -> str:
    from pathlib import Path
    p = (Path(__file__).resolve().parents[1]
         / "src/supernova_blackbox/pipeline/workflows.py")
    return p.read_text(encoding="utf-8")


def test_workflow_verifies_after_report_agent_before_finalize():
    """verify_report_vuln_blocks 必须夹在 run_report_agent 与 finalize_report 之间。

    agent 覆盖报告之后校验才有意义;finalize(model info 注入)之前重建才不丢注入。"""
    src = _workflow_src()
    i_agent = src.find("activities.run_report_agent")
    assert i_agent != -1, "找不到 run_report_agent 调用"
    i_verify = src.find("activities.verify_report_vuln_blocks", i_agent)
    assert i_verify != -1, (
        "verify_report_vuln_blocks 必须在 run_report_agent 之后（防丢漏洞节）"
    )
    assert src.find("activities.finalize_report", i_verify) != -1, (
        "verify_report_vuln_blocks 必须在 finalize_report 之前（重建不丢 model info 注入）"
    )
