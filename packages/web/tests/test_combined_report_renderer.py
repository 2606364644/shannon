"""组合扫描融合报告渲染器（Task 8）：combined_report_renderer。

spec §10.2 — 按 vuln_class 交叉白盒 queue + 黑盒 verdicts，产
deliverables/combined/combined_report.md（顶部摘要表 + 按漏洞类详述）。

双轨读不同产物（与 ``workspace.get_workspace_vuln_counts`` 同口径）：
- 白盒：``{vt}_exploitation_queue.json``，count = ``len(vulnerabilities)``。
- 黑盒：``{vt}_exploit_verdicts.json``，count = ``verdicts`` 中 ``status=="exploited"``
  的条目数（成功 exploit 数）。

核心契约：
- 顶部「## 组合摘要」表：每漏洞类一行（白盒发现数 / 黑盒验证数 / 双轨交叉）。
- 「## 按漏洞类详述」：每类一节（### {vt}），#### 白盒视角 + #### 黑盒视角并列。
- 黑盒计数只数 exploited verdict（非 exploited 的 blocked/potential 不计入"验证"列）。
- 韧性：缺失 / 空 / 不可读产物不崩溃，emit 0 计数 + "无发现" 文案。
- 产物落 deliverables/combined/combined_report.md（新桶）。
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


def _make_scan_dir(tmp_path: Path) -> Path:
    scan_dir = tmp_path / "ws-1"
    (scan_dir / "deliverables").mkdir(parents=True)
    return scan_dir


def _write_wb_queue(scan_dir: Path, vt: str, vulns: list[dict]) -> None:
    """白盒：写 deliverables/whitebox/{vt}_exploitation_queue.json。"""
    d = scan_dir / "deliverables" / "whitebox"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{vt}_exploitation_queue.json").write_text(_queue(vulns), encoding="utf-8")


def _write_bb_verdicts(scan_dir: Path, vt: str, verdicts: list[dict]) -> None:
    """黑盒：写 deliverables/blackbox/{vt}_exploit_verdicts.json（与 executor.py:221
    产物的 stem + schema 一致；非 {vt}_exploitation_queue.json）。"""
    d = scan_dir / "deliverables" / "blackbox"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{vt}_exploit_verdicts.json").write_text(_verdicts(verdicts), encoding="utf-8")


# ── 主路径：白盒+黑盒都有产物 → 完整交叉报告 ────────────────────────────────
def test_render_combined_report_full_cross_reference(tmp_path: Path):
    """给定白盒 queue + 黑盒 verdicts 样例 → 报告含摘要表 + 按类详述 + 落 combined/。

    黑盒 verdicts 含混合 status（exploited + blocked），验证"黑盒验证"列只数 exploited。
    """
    scan_dir = _make_scan_dir(tmp_path)
    # 白盒：injection(2) + xss(1)
    _write_wb_queue(scan_dir, "injection", [
        {"ID": "INJ-1", "title": "SQLi in login", "source": "/login", "path": "x → y"},
        {"ID": "INJ-2", "title": "SQLi in search", "source": "/search"},
    ])
    _write_wb_queue(scan_dir, "xss", [
        {"ID": "XSS-1", "title": "Reflected XSS", "source": "/q"},
    ])
    # 黑盒 injection：2 verdicts（1 exploited + 1 blocked）→ "验证"列应只数 1
    _write_bb_verdicts(scan_dir, "injection", [
        {"vulnerability_id": "INJ-1", "status": "exploited",
         "severity": "high", "impact": "DB exfiltration"},
        {"vulnerability_id": "INJ-2", "status": "blocked_by_security",
         "severity": "medium"},
    ])
    # 黑盒 ssrf：1 exploited verdict
    _write_bb_verdicts(scan_dir, "ssrf", [
        {"vulnerability_id": "SSRF-1", "status": "exploited",
         "severity": "critical", "impact": "Internal port scan"},
    ])

    out = render_combined_report(scan_dir)

    # 1. 产物落 deliverables/combined/combined_report.md
    expected = scan_dir / "deliverables" / "combined" / "combined_report.md"
    assert out == expected, f"期望产物路径 {expected}，实际 {out}"
    assert expected.is_file(), "combined_report.md 未落盘"

    text = expected.read_text("utf-8")

    # 2. 顶部标题 + 摘要表
    assert "# 组合扫描融合报告" in text or "# Combined" in text, "缺顶部标题"
    assert "## 组合摘要" in text or "## Summary" in text, "缺摘要小节"
    assert "| 漏洞类" in text or "| Vulnerability" in text, "缺摘要表头"

    # 3. 摘要表 injection 行：白盒=2，黑盒=1（只数 exploited，blocked 不计）
    #    抽 injection 那一行校验（避免 "2"/"1" 整文误匹配）
    injection_row = [ln for ln in text.splitlines() if ln.startswith("| injection")][0]
    cells = [c.strip() for c in injection_row.split("|") if c.strip()]
    assert cells[0] == "injection"
    assert cells[1] == "2", f"白盒 injection 计数应为 2，行: {injection_row}"
    assert cells[2] == "1", (
        f"黑盒 injection 验证数应为 1（只数 exploited，blocked 不计），行: {injection_row}")
    assert cells[3] == "是", f"injection 双轨交叉应为 是，行: {injection_row}"

    # ssrf 行：白盒=0，黑盒=1
    ssrf_row = [ln for ln in text.splitlines() if ln.startswith("| ssrf")][0]
    ssrf_cells = [c.strip() for c in ssrf_row.split("|") if c.strip()]
    assert ssrf_cells[1] == "0", f"白盒 ssrf 应为 0，行: {ssrf_row}"
    assert ssrf_cells[2] == "1", f"黑盒 ssrf 验证应为 1，行: {ssrf_row}"

    # 4. 按漏洞类详述小节 + 标题层级（### {vt} → #### 白盒/黑盒视角）
    assert "## 按漏洞类详述" in text or "## Per" in text, "缺按类详述小节"
    assert "### injection" in text, "缺 ### injection 类标题"
    assert "#### 白盒视角" in text or "#### Whitebox" in text, "缺 #### 白盒视角"
    assert "#### 黑盒视角" in text or "#### Blackbox" in text, "缺 #### 黑盒视角"

    # 5. 白盒 queue 条目 ID 出现在详述
    assert "INJ-1" in text, "白盒 INJ-1 未出现在详述"
    # 黑盒 verdict 条目 ID 出现在详述（含 exploited + blocked 两条）
    assert "INJ-2" in text, "黑盒 INJ-2 (blocked) 未出现在详述"
    assert "[exploited]" in text, "exploited status 标签未出现"
    assert "[blocked_by_security]" in text, "blocked status 标签未出现"


# ── 韧性：缺失黑盒 verdicts ─────────────────────────────────────────────────
def test_render_combined_report_resilient_missing_blackbox(tmp_path: Path):
    """白盒有 queue、黑盒 deliverables/blackbox/ 不存在 → 不崩溃，黑盒列 0。"""
    scan_dir = _make_scan_dir(tmp_path)
    _write_wb_queue(scan_dir, "injection", [{"ID": "INJ-1", "title": "SQLi"}])

    out = render_combined_report(scan_dir)  # 不应 raise
    text = out.read_text("utf-8")
    assert "## 组合摘要" in text
    # 黑盒缺失 → 黑盒列 0（不崩溃）
    injection_row = [ln for ln in text.splitlines() if ln.startswith("| injection")][0]
    cells = [c.strip() for c in injection_row.split("|") if c.strip()]
    assert cells[2] == "0", f"黑盒缺失时验证列应为 0，行: {injection_row}"


# ── 韧性：完全无产物 ─────────────────────────────────────────────────────────
def test_render_combined_report_resilient_no_deliverables(tmp_path: Path):
    """白盒+黑盒 deliverables 均不存在 → 仍产报告（全 0 计数 + "无发现"）。"""
    scan_dir = _make_scan_dir(tmp_path)

    out = render_combined_report(scan_dir)  # 不应 raise
    text = out.read_text("utf-8")
    assert "## 组合摘要" in text
    # 四类都应出现在摘要表（即使计数 0）
    for vt in _VULN_CLASSES:
        assert f"| {vt} |" in text, f"摘要表缺 {vt} 行"


# ── 韧性：空 verdicts（verdicts: []）──────────────────────────────────────────
def test_render_combined_report_resilient_empty_verdicts(tmp_path: Path):
    """黑盒 verdicts 文件存在但 verdicts 为空 → 黑盒列 0，不崩溃。"""
    scan_dir = _make_scan_dir(tmp_path)
    _write_wb_queue(scan_dir, "xss", [])  # 空 queue
    _write_bb_verdicts(scan_dir, "xss", [])  # 空 verdicts

    out = render_combined_report(scan_dir)
    text = out.read_text("utf-8")
    assert "## 组合摘要" in text
    xss_row = [ln for ln in text.splitlines() if ln.startswith("| xss")][0]
    cells = [c.strip() for c in xss_row.split("|") if c.strip()]
    assert cells[1] == "0" and cells[2] == "0", f"xss 空 queue+verdicts 应双 0，行: {xss_row}"


# ── 韧性：损坏 JSON（黑盒 verdicts）──────────────────────────────────────────
def test_render_combined_report_resilient_corrupt_json(tmp_path: Path):
    """黑盒 verdicts 文件是损坏 JSON → 计数 0，不崩溃。"""
    scan_dir = _make_scan_dir(tmp_path)
    bb = scan_dir / "deliverables" / "blackbox"
    bb.mkdir(parents=True)
    # 损坏的是 verdicts 文件（渲染器实际读的黑盒产物）
    (bb / "injection_exploit_verdicts.json").write_text("{not valid json", "utf-8")

    out = render_combined_report(scan_dir)  # 不应 raise
    text = out.read_text("utf-8")
    assert "## 组合摘要" in text
    injection_row = [ln for ln in text.splitlines() if ln.startswith("| injection")][0]
    cells = [c.strip() for c in injection_row.split("|") if c.strip()]
    assert cells[2] == "0", f"损坏 verdicts 时黑盒列应为 0，行: {injection_row}"


# ── 黑盒计数只数 exploited（blocked/potential/false_positive 不计）──────────────
def test_blackbox_count_only_exploited_verdicts(tmp_path: Path):
    """黑盒 verdicts 含多种 status → 摘要表"黑盒验证"列只数 status==exploited。

    守护 Critical fix：黑盒产物是 {vt}_exploit_verdicts.json（非 exploitation_queue.json），
    计数口径 = exploited verdict 数（与 workspace.get_workspace_vuln_counts 一致）。
    """
    scan_dir = _make_scan_dir(tmp_path)
    _write_bb_verdicts(scan_dir, "authz", [
        {"vulnerability_id": "AU-1", "status": "exploited"},
        {"vulnerability_id": "AU-2", "status": "exploited"},
        {"vulnerability_id": "AU-3", "status": "blocked_by_security"},
        {"vulnerability_id": "AU-4", "status": "potential"},
        {"vulnerability_id": "AU-5", "status": "false_positive"},
    ])

    out = render_combined_report(scan_dir)
    text = out.read_text("utf-8")
    authz_row = [ln for ln in text.splitlines() if ln.startswith("| authz")][0]
    cells = [c.strip() for c in authz_row.split("|") if c.strip()]
    # 5 verdicts 但只有 2 exploited → "黑盒验证"列 = 2
    assert cells[2] == "2", (
        f"authz 黑盒验证应为 2（5 verdicts 中 2 exploited），行: {authz_row}")


# ── _VULN_CLASSES 契约：injection/xss/ssrf/authz（spec §10.2）────────────────
def test_vuln_classes_matches_spec():
    """_VULN_CLASSES 必须是 injection/xss/ssrf/authz（spec §10.2，不含 auth）。"""
    assert set(_VULN_CLASSES) == {"injection", "xss", "ssrf", "authz"}, (
        f"_VULN_CLASSES 应为 injection/xss/ssrf/authz，实际: {_VULN_CLASSES}")
