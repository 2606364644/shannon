"""组合扫描融合报告渲染器：黑盒报告为主体 + 白盒字段融入。

设计（2026-08-18 重做，取代旧 vuln_class 机械交叉表主体）：
- **主体 = 黑盒正式报告** ``comprehensive_security_assessment_report.md`` 原文
  （H1 换成融合报告标题，其余正文一字不动）。
- **卡片级融入**：黑盒报告 ``### {ID}:`` 漏洞卡标题后插白盒 queue 的结构化字段 ——
  ``- **脆弱代码位置（白盒）:**``（vulnerable_code_location / sink_function）+
  ``- **缺失防护（白盒）:**``（missing_defense / guard_evidence /
  sanitization_observed / encoding_observed），按 ID 匹配（黑盒接力读白盒 queue，
  两边 ID 同源）。
- **auth 纳入**：交叉范围 5 类（injection/xss/ssrf/authz/auth）。
- **头部**前置组合摘要表（5 行）。
- **尾部附录**「白盒独有发现（黑盒未实证）」：白盒 queue 有、黑盒报告无卡的条目，
  逐条标注黑盒 verdict 状态（无 verdict → 未尝试）。
- **占位过滤**：ID 含 PLACEHOLDER 的脏条目不渲染、不计数。
- **韧性降级**：黑盒报告缺失 / 不可读 → 回退旧机械交叉表（按类详述 bullet），
  摘要表仍产出。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from supernova_web.components.combined_report_renderer import (
    _VULN_CLASSES,
    render_combined_report,
)


# ── helpers ────────────────────────────────────────────────────────────────
def _queue(vulns: list[dict]) -> str:
    """白盒 {vt}_exploitation_queue.json payload。"""
    return json.dumps({"vulnerabilities": vulns})


def _verdicts(verdicts: list[dict]) -> str:
    """黑盒 {vt}_exploit_verdicts.json payload（schema: {vuln_class, accepted_ids,
    verdicts, rejected}；本测试只填 verdicts —— 计数器读此字段）。"""
    return json.dumps({"verdicts": verdicts})


_REPORT_FILENAME = "comprehensive_security_assessment_report.md"

# 样例黑盒报告（结构与真实产物一致：H1 + 执行摘要 + ### {ID}: 卡片）。
_SAMPLE_BB_REPORT = """# 安全评估报告

## 执行摘要

- **目标:** http://tgt:4000/（样例目标）
- **评估日期:** 2026-08-18

## 认证漏洞

### AUTH-VULN-02: 凭据泄露接管 admin
- **严重程度:** critical
- **利用步骤:**
  1. curl http://tgt:4000/tutorial/a7
- **影响证据:** evidence.html

### AUTH-VULN-07: 错误消息区分用户名/口令（黑盒独有，白盒 queue 无此条）
- **严重程度:** low

## 授权漏洞

### AUTHZ-VULN-01: IDOR 读取任意用户资产
- **严重程度:** high

## 攻击链（多步利用路径）

### llm-chain-1: 某链
"""


def _make_scan_dir(tmp_path: Path) -> Path:
    scan_dir = tmp_path / "ws-1"
    (scan_dir / "deliverables").mkdir(parents=True)
    return scan_dir


def _write_wb_queue(scan_dir: Path, vt: str, vulns: list[dict]) -> None:
    d = scan_dir / "deliverables" / "whitebox"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{vt}_exploitation_queue.json").write_text(_queue(vulns), encoding="utf-8")


def _write_bb_verdicts(scan_dir: Path, vt: str, verdicts: list[dict]) -> None:
    d = scan_dir / "deliverables" / "blackbox"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{vt}_exploit_verdicts.json").write_text(_verdicts(verdicts), encoding="utf-8")


def _write_bb_report(scan_dir: Path, text: str = _SAMPLE_BB_REPORT) -> None:
    d = scan_dir / "deliverables" / "blackbox"
    d.mkdir(parents=True, exist_ok=True)
    (d / _REPORT_FILENAME).write_text(text, encoding="utf-8")


def _render(scan_dir: Path) -> Path:
    return render_combined_report(
        whitebox_root=scan_dir / "deliverables" / "whitebox",
        blackbox_root=scan_dir / "deliverables" / "blackbox",
        out_dir=scan_dir / "deliverables" / "combined")


def _lines_after(text: str, heading_prefix: str, n: int = 4) -> list[str]:
    """取 ``heading_prefix`` 行之后紧邻的 n 行（卡片注入位置断言用）。"""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith(heading_prefix):
            return [l for l in lines[i + 1:i + 1 + n] if l.strip()]
    return []


# ── 主路径：黑盒报告为主体 + 卡片级白盒融入 ─────────────────────────────────
def test_fusion_body_is_blackbox_report(tmp_path: Path):
    """黑盒报告存在 → 主体 = 报告原文（执行摘要/卡片内容一字不动），H1 换标题。"""
    scan_dir = _make_scan_dir(tmp_path)
    _write_bb_report(scan_dir)
    _write_wb_queue(scan_dir, "authz", [
        {"ID": "AUTHZ-VULN-01", "title": "IDOR", "endpoint": "GET /allocations/:userId",
         "vulnerable_code_location": "app/routes/allocations.js:16-18",
         "guard_evidence": "仅挂 isLoggedInMiddleware，无归属校验"}])
    _write_bb_verdicts(scan_dir, "authz", [
        {"vulnerability_id": "AUTHZ-VULN-01", "status": "exploited"}])

    text = _render(scan_dir).read_text("utf-8")

    # 黑盒正文原样保留
    assert "## 执行摘要" in text
    assert "- **目标:** http://tgt:4000/（样例目标）" in text
    assert "### AUTH-VULN-02: 凭据泄露接管 admin" in text
    assert "### llm-chain-1: 某链" in text
    # H1 换成融合报告标题，原 H1 不再出现
    assert text.splitlines()[0] == "# 组合扫描融合报告"
    assert "# 安全评估报告" not in text


def test_fusion_card_injects_whitebox_fields(tmp_path: Path):
    """黑盒卡片标题后插白盒脆弱代码位置 + 缺失防护（authz 用 guard_evidence）。"""
    scan_dir = _make_scan_dir(tmp_path)
    _write_bb_report(scan_dir)
    _write_wb_queue(scan_dir, "authz", [
        {"ID": "AUTHZ-VULN-01", "endpoint": "GET /allocations/:userId",
         "vulnerable_code_location": "app/routes/allocations.js:16-18",
         "guard_evidence": "仅挂 isLoggedInMiddleware，无 userId 归属校验"}])
    _write_wb_queue(scan_dir, "auth", [
        {"ID": "AUTH-VULN-02", "source_endpoint": "GET /tutorial/a7",
         "vulnerable_code_location": "app/routes/tutorial.js:31",
         "missing_defense": "路由无鉴权，页面明文凭据"}])
    _write_bb_verdicts(scan_dir, "authz", [
        {"vulnerability_id": "AUTHZ-VULN-01", "status": "exploited"}])
    _write_bb_verdicts(scan_dir, "auth", [
        {"vulnerability_id": "AUTH-VULN-02", "status": "exploited"}])

    text = _render(scan_dir).read_text("utf-8")

    # authz 卡：guard_evidence 作缺失防护
    after_az = _lines_after(text, "### AUTHZ-VULN-01:")
    assert any("脆弱代码位置（白盒）" in ln and "allocations.js:16-18" in ln
               for ln in after_az), f"AUTHZ 卡后缺脆弱代码位置行: {after_az}"
    assert any("缺失防护（白盒）" in ln and "isLoggedInMiddleware" in ln
               for ln in after_az), f"AUTHZ 卡后缺失护行: {after_az}"

    # auth 卡：missing_defense 作缺失防护
    after_au = _lines_after(text, "### AUTH-VULN-02:")
    assert any("脆弱代码位置（白盒）" in ln and "tutorial.js:31" in ln
               for ln in after_au), f"AUTH 卡后缺脆弱代码位置行: {after_au}"
    assert any("缺失防护（白盒）" in ln and "路由无鉴权" in ln
               for ln in after_au), f"AUTH 卡后缺失护行: {after_au}"

    # 黑盒独有卡（白盒无此条）不注入
    after_bb_only = _lines_after(text, "### AUTH-VULN-07:")
    assert not any("（白盒）" in ln for ln in after_bb_only), (
        f"黑盒独有卡不应注入白盒字段: {after_bb_only}")


def test_fusion_injection_class_uses_sink_fields(tmp_path: Path):
    """inj/xss 无 vulnerable_code_location → 脆弱代码位置回落 sink_function，
    缺失防护回落 sanitization_observed。"""
    scan_dir = _make_scan_dir(tmp_path)
    report = _SAMPLE_BB_REPORT.replace(
        "### llm-chain-1: 某链",
        "### INJ-VULN-01: SQLi\n- **严重程度:** high\n\n### llm-chain-1: 某链")
    _write_bb_report(scan_dir, report)
    _write_wb_queue(scan_dir, "injection", [
        {"ID": "INJ-VULN-01", "source": "GET /allocations?threshold=",
         "path": "req.query.threshold → allocationsDAO",
         "sink_function": "allocations-dao.js:77 $where 拼接",
         "sanitization_observed": "无输入校验，直拼 $where 字符串"}])
    _write_bb_verdicts(scan_dir, "injection", [
        {"vulnerability_id": "INJ-VULN-01", "status": "exploited"}])

    text = _render(scan_dir).read_text("utf-8")
    after = _lines_after(text, "### INJ-VULN-01:")
    assert any("脆弱代码位置（白盒）" in ln and "$where" in ln for ln in after), (
        f"inj 卡脆弱代码位置应回落 sink_function: {after}")
    assert any("缺失防护（白盒）" in ln and "直拼" in ln for ln in after), (
        f"inj 卡缺失防护应回落 sanitization_observed: {after}")


def test_fusion_summary_table_includes_auth(tmp_path: Path):
    """摘要表 5 类含 auth，计数口径不变（黑盒只数 exploited）。"""
    scan_dir = _make_scan_dir(tmp_path)
    _write_bb_report(scan_dir)
    _write_wb_queue(scan_dir, "auth", [
        {"ID": "AUTH-VULN-02", "vulnerable_code_location": "a.js:1",
         "missing_defense": "无鉴权"},
        {"ID": "AUTH-VULN-09", "vulnerable_code_location": "b.js:2",
         "missing_defense": "无超时"}])
    _write_bb_verdicts(scan_dir, "auth", [
        {"vulnerability_id": "AUTH-VULN-02", "status": "exploited"},
        {"vulnerability_id": "AUTH-VULN-09", "status": "blocked_by_security"}])

    text = _render(scan_dir).read_text("utf-8")
    assert "## 组合摘要" in text
    # 摘要表在主体正文之前（头部前置）
    assert text.index("## 组合摘要") < text.index("## 执行摘要")
    auth_row = [ln for ln in text.splitlines() if ln.startswith("| auth |")][0]
    cells = [c.strip() for c in auth_row.split("|") if c.strip()]
    assert cells == ["auth", "2", "1", "是"], f"auth 行应为 2/1/是: {auth_row}"
    for vt in _VULN_CLASSES:
        assert f"| {vt} |" in text, f"摘要表缺 {vt} 行"


def test_fusion_appendix_lists_whitebox_only_findings(tmp_path: Path):
    """白盒有、黑盒报告无卡的条目 → 尾部附录，标注 verdict 状态（无 → 未尝试）。"""
    scan_dir = _make_scan_dir(tmp_path)
    _write_bb_report(scan_dir)
    # AUTH-VULN-09：白盒有、黑盒报告无卡、verdict=blocked → 附录 + [blocked_by_security]
    _write_wb_queue(scan_dir, "auth", [
        {"ID": "AUTH-VULN-09", "title": "会话无超时",
         "vulnerable_code_location": "server.js:92", "missing_defense": "无超时"}])
    _write_bb_verdicts(scan_dir, "auth", [
        {"vulnerability_id": "AUTH-VULN-09", "status": "blocked_by_security"}])
    # SSRF-1：白盒有、黑盒无 verdict → 附录 + 未尝试
    _write_wb_queue(scan_dir, "ssrf", [
        {"ID": "SSRF-1", "title": "research SSRF",
         "vulnerable_code_location": "research.js:10", "missing_defense": "无 URL 白名单"}])

    text = _render(scan_dir).read_text("utf-8")
    assert "## 白盒独有发现（黑盒未实证）" in text
    assert "AUTH-VULN-09" in text and "[blocked_by_security]" in text
    assert "SSRF-1" in text and "未尝试" in text
    # 附录在主体之后（尾部）
    assert text.index("## 白盒独有发现（黑盒未实证）") > text.index("## 执行摘要")


def test_fusion_filters_placeholder_entries(tmp_path: Path):
    """ID 含 PLACEHOLDER 的脏条目 → 不渲染、不计数。"""
    scan_dir = _make_scan_dir(tmp_path)
    _write_bb_report(scan_dir)
    _write_wb_queue(scan_dir, "authz", [
        {"ID": "AUTHZ-VULN-01", "vulnerable_code_location": "a.js:1",
         "guard_evidence": "无归属校验"},
        {"ID": "AUTHULN_PLACEHOLDER", "title": "占位：请忽略本条"}])
    _write_bb_verdicts(scan_dir, "authz", [
        {"vulnerability_id": "AUTHZ-VULN-01", "status": "exploited"}])

    text = _render(scan_dir).read_text("utf-8")
    assert "PLACEHOLDER" not in text
    authz_row = [ln for ln in text.splitlines() if ln.startswith("| authz")][0]
    cells = [c.strip() for c in authz_row.split("|") if c.strip()]
    assert cells[1] == "1", f"占位条目不应计数，authz 白盒应 1: {authz_row}"


# ── 降级：黑盒报告缺失 → 旧机械交叉表 ───────────────────────────────────────
def test_fallback_when_blackbox_report_missing(tmp_path: Path):
    """黑盒报告不存在 → 回退按类详述 bullet 报告，摘要表仍产出（含 auth）。"""
    scan_dir = _make_scan_dir(tmp_path)
    _write_wb_queue(scan_dir, "injection", [
        {"ID": "INJ-1", "title": "SQLi in login", "source": "/login", "path": "x → y"}])
    _write_bb_verdicts(scan_dir, "injection", [
        {"vulnerability_id": "INJ-1", "status": "exploited", "severity": "high"},
        {"vulnerability_id": "INJ-2", "status": "blocked_by_security"}])

    out = _render(scan_dir)
    text = out.read_text("utf-8")
    assert out == scan_dir / "deliverables" / "combined" / "combined_report.md"
    assert "# 组合扫描融合报告" in text
    assert "## 组合摘要" in text
    assert "## 按漏洞类详述" in text, "降级路径应保留按类详述"
    assert "#### 白盒视角" in text and "#### 黑盒视角" in text
    assert "INJ-1" in text and "[blocked_by_security]" in text
    injection_row = [ln for ln in text.splitlines() if ln.startswith("| injection")][0]
    cells = [c.strip() for c in injection_row.split("|") if c.strip()]
    assert cells == ["injection", "1", "1", "是"], f"injection 行应 1/1/是: {injection_row}"
    # 占位条目在降级路径同样过滤
    assert f"| {_VULN_CLASSES[-1]} |" in text


def test_fallback_when_blackbox_report_corrupt(tmp_path: Path):
    """黑盒报告是损坏内容（空文件）→ 降级机械交叉表，不崩溃。"""
    scan_dir = _make_scan_dir(tmp_path)
    _write_bb_report(scan_dir, "")
    _write_wb_queue(scan_dir, "xss", [{"ID": "XSS-1", "title": "反射 XSS"}])

    text = _render(scan_dir).read_text("utf-8")
    assert "## 组合摘要" in text
    assert "## 按漏洞类详述" in text


# ── 韧性：产物缺失 / 空 / 损坏 ──────────────────────────────────────────────
def test_render_resilient_missing_blackbox(tmp_path: Path):
    """白盒有 queue、黑盒 deliverables/blackbox/ 不存在 → 不崩溃，黑盒列 0。"""
    scan_dir = _make_scan_dir(tmp_path)
    _write_wb_queue(scan_dir, "injection", [{"ID": "INJ-1", "title": "SQLi"}])

    text = _render(scan_dir).read_text("utf-8")
    assert "## 组合摘要" in text
    injection_row = [ln for ln in text.splitlines() if ln.startswith("| injection")][0]
    cells = [c.strip() for c in injection_row.split("|") if c.strip()]
    assert cells[2] == "0", f"黑盒缺失时验证列应为 0，行: {injection_row}"
    # 白盒发现进附录（黑盒未实证）
    assert "INJ-1" in text


def test_render_resilient_no_deliverables(tmp_path: Path):
    """白盒+黑盒 deliverables 均不存在 → 仍产报告（全 0 计数）。"""
    scan_dir = _make_scan_dir(tmp_path)

    text = _render(scan_dir).read_text("utf-8")
    assert "## 组合摘要" in text
    for vt in _VULN_CLASSES:
        assert f"| {vt} |" in text, f"摘要表缺 {vt} 行"


def test_render_resilient_empty_verdicts(tmp_path: Path):
    """黑盒 verdicts 文件存在但 verdicts 为空 → 黑盒列 0，不崩溃。"""
    scan_dir = _make_scan_dir(tmp_path)
    _write_wb_queue(scan_dir, "xss", [])
    _write_bb_verdicts(scan_dir, "xss", [])

    text = _render(scan_dir).read_text("utf-8")
    xss_row = [ln for ln in text.splitlines() if ln.startswith("| xss")][0]
    cells = [c.strip() for c in xss_row.split("|") if c.strip()]
    assert cells[1] == "0" and cells[2] == "0", f"xss 空 queue+verdicts 应双 0，行: {xss_row}"


def test_render_resilient_corrupt_json(tmp_path: Path):
    """黑盒 verdicts 文件是损坏 JSON → 计数 0，不崩溃。"""
    scan_dir = _make_scan_dir(tmp_path)
    bb = scan_dir / "deliverables" / "blackbox"
    bb.mkdir(parents=True)
    (bb / "injection_exploit_verdicts.json").write_text("{not valid json", "utf-8")

    text = _render(scan_dir).read_text("utf-8")
    assert "## 组合摘要" in text
    injection_row = [ln for ln in text.splitlines() if ln.startswith("| injection")][0]
    cells = [c.strip() for c in injection_row.split("|") if c.strip()]
    assert cells[2] == "0", f"损坏 verdicts 时黑盒列应为 0，行: {injection_row}"


# ── 黑盒计数只数 exploited ────────────────────────────────────────────────
def test_blackbox_count_only_exploited_verdicts(tmp_path: Path):
    """黑盒 verdicts 含多种 status → 摘要表"黑盒验证"列只数 status==exploited。"""
    scan_dir = _make_scan_dir(tmp_path)
    _write_bb_verdicts(scan_dir, "authz", [
        {"vulnerability_id": "AU-1", "status": "exploited"},
        {"vulnerability_id": "AU-2", "status": "exploited"},
        {"vulnerability_id": "AU-3", "status": "blocked_by_security"},
        {"vulnerability_id": "AU-4", "status": "potential"},
        {"vulnerability_id": "AU-5", "status": "false_positive"},
    ])

    text = _render(scan_dir).read_text("utf-8")
    authz_row = [ln for ln in text.splitlines() if ln.startswith("| authz")][0]
    cells = [c.strip() for c in authz_row.split("|") if c.strip()]
    assert cells[2] == "2", (
        f"authz 黑盒验证应为 2（5 verdicts 中 2 exploited），行: {authz_row}")


# ── _VULN_CLASSES 契约：5 类含 auth ────────────────────────────────────────
def test_vuln_classes_matches_spec():
    """_VULN_CLASSES 必须是 injection/xss/ssrf/authz/auth（auth 纳入交叉）。"""
    assert set(_VULN_CLASSES) == {"injection", "xss", "ssrf", "authz", "auth"}, (
        f"_VULN_CLASSES 应为 5 类含 auth，实际: {_VULN_CLASSES}")


# ── per-run 双路径签名（spec §4/§9）────────────────────────────────────────
def test_render_writes_to_per_run_out_dir(tmp_path: Path):
    """双路径签名：白盒根 + 黑盒根（run-K 嵌套）+ 输出根 combined/run-K。"""
    wb_root = tmp_path / "deliverables" / "whitebox"
    bb_root = tmp_path / "blackbox-runs" / "run-1" / "deliverables" / "blackbox"
    out_dir = tmp_path / "combined" / "run-1"
    wb_root.mkdir(parents=True)
    bb_root.mkdir(parents=True)
    (wb_root / "injection_exploitation_queue.json").write_text(_queue([
        {"ID": "INJ-1", "title": "SQLi", "source": "/login"}]))
    (bb_root / "injection_exploit_verdicts.json").write_text(_verdicts([
        {"vulnerability_id": "INJ-1", "status": "exploited", "severity": "high"}]))
    out = render_combined_report(whitebox_root=wb_root, blackbox_root=bb_root,
                                 out_dir=out_dir)
    assert out == out_dir / "combined_report.md"
    text = out.read_text("utf-8")
    assert "| injection | 1 | 1 |" in text
    assert "### injection" in text  # 降级路径（无黑盒报告）按类详述仍在
