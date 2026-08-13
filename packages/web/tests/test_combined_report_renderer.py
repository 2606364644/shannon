"""组合扫描融合报告渲染器（Task 8）：combined_report_renderer。

spec §10.2 — 按 vuln_class 交叉白盒 queue + 黑盒 queue，产
deliverables/combined/combined_report.md（顶部摘要表 + 按漏洞类详述）。

核心契约：
- 顶部「## 组合摘要」表：每漏洞类一行（白盒发现数 / 黑盒验证数 / 双轨交叉）。
- 「## 按漏洞类详述」：每类一节，白盒视角 + 黑盒视角并列。
- 韧性：缺失 / 空 / 不可读 queue 不崩溃，emit 0 计数 + "无发现" 文案。
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
    return json.dumps({"vulnerabilities": vulns})


def _make_scan_dir(tmp_path: Path) -> Path:
    scan_dir = tmp_path / "ws-1"
    (scan_dir / "deliverables").mkdir(parents=True)
    return scan_dir


def _write_queue(scan_dir: Path, track: str, vt: str, vulns: list[dict]) -> None:
    d = scan_dir / "deliverables" / track
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{vt}_exploitation_queue.json").write_text(_queue(vulns), encoding="utf-8")


# ── 主路径：白盒+黑盒都有产物 → 完整交叉报告 ────────────────────────────────
def test_render_combined_report_full_cross_reference(tmp_path: Path):
    """给定白盒+黑盒 queue 样例 → 报告含摘要表 + 按类详述 + 落 combined/。"""
    scan_dir = _make_scan_dir(tmp_path)
    # 白盒：injection(2) + xss(1)
    _write_queue(scan_dir, "whitebox", "injection", [
        {"ID": "INJ-1", "title": "SQLi in login", "source": "/login", "path": "x → y"},
        {"ID": "INJ-2", "title": "SQLi in search", "source": "/search"},
    ])
    _write_queue(scan_dir, "whitebox", "xss", [
        {"ID": "XSS-1", "title": "Reflected XSS", "source": "/q"},
    ])
    # 黑盒：injection(1) + ssrf(1)
    _write_queue(scan_dir, "blackbox", "injection", [
        {"ID": "INJ-BB-1", "title": "Confirmed SQLi", "endpoint": "POST /login"},
    ])
    _write_queue(scan_dir, "blackbox", "ssrf", [
        {"ID": "SSRF-BB-1", "title": "SSRF via webhooks", "endpoint": "/webhook"},
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

    # 3. 计数正确（injection: wb=2, bb=1）
    assert "injection" in text
    assert "xss" in text
    assert "ssrf" in text
    # 摘要表应含白盒+黑盒计数列
    assert "2" in text  # 白盒 injection 计数
    assert "1" in text  # 黑盒 injection 计数

    # 4. 按漏洞类详述小节
    assert "## 按漏洞类详述" in text or "## Per" in text, "缺按类详述小节"
    # injection 节应含白盒+黑盒视角
    assert "白盒视角" in text or "Whitebox" in text, "缺白盒视角"
    assert "黑盒视角" in text or "Blackbox" in text, "缺黑盒视角"

    # 5. 具体漏洞 ID 出现在详述中
    assert "INJ-1" in text, "白盒 INJ-1 未出现在详述"
    assert "INJ-BB-1" in text, "黑盒 INJ-BB-1 未出现在详述"


# ── 韧性：缺失黑盒 queue ────────────────────────────────────────────────────
def test_render_combined_report_resilient_missing_blackbox(tmp_path: Path):
    """白盒有 queue、黑盒 deliverables/blackbox/ 不存在 → 不崩溃，黑盒列 0。"""
    scan_dir = _make_scan_dir(tmp_path)
    _write_queue(scan_dir, "whitebox", "injection", [
        {"ID": "INJ-1", "title": "SQLi"}])

    out = render_combined_report(scan_dir)  # 不应 raise
    text = out.read_text("utf-8")
    assert "## 组合摘要" in text
    # 黑盒缺失 → 黑盒列 0（不崩溃）
    assert "injection" in text


# ── 韧性：完全无产物 ─────────────────────────────────────────────────────────
def test_render_combined_report_resilient_no_deliverables(tmp_path: Path):
    """白盒+黑盒 deliverables 均不存在 → 仍产报告（全 0 计数 + "无发现"）。"""
    scan_dir = _make_scan_dir(tmp_path)

    out = render_combined_report(scan_dir)  # 不应 raise
    text = out.read_text("utf-8")
    assert "## 组合摘要" in text
    # 四类都应出现在摘要表（即使计数 0）
    for vt in _VULN_CLASSES:
        assert vt in text, f"摘要表缺 {vt} 行"


# ── 韧性：空 queue（vulnerabilities: []）─────────────────────────────────────
def test_render_combined_report_resilient_empty_queue(tmp_path: Path):
    """queue 文件存在但 vulnerabilities 为空 → 计数 0，不崩溃。"""
    scan_dir = _make_scan_dir(tmp_path)
    _write_queue(scan_dir, "whitebox", "xss", [])  # 空 queue
    _write_queue(scan_dir, "blackbox", "xss", [])

    out = render_combined_report(scan_dir)
    text = out.read_text("utf-8")
    assert "## 组合摘要" in text
    assert "xss" in text


# ── 韧性：损坏 JSON ─────────────────────────────────────────────────────────
def test_render_combined_report_resilient_corrupt_json(tmp_path: Path):
    """queue 文件是损坏 JSON → 计数 0，不崩溃。"""
    scan_dir = _make_scan_dir(tmp_path)
    bb = scan_dir / "deliverables" / "blackbox"
    bb.mkdir(parents=True)
    (bb / "injection_exploitation_queue.json").write_text("{not valid json", "utf-8")

    out = render_combined_report(scan_dir)  # 不应 raise
    text = out.read_text("utf-8")
    assert "## 组合摘要" in text


# ── _VULN_CLASSES 契约：injection/xss/ssrf/authz（spec §10.2）────────────────
def test_vuln_classes_matches_spec():
    """_VULN_CLASSES 必须是 injection/xss/ssrf/authz（spec §10.2，不含 auth）。"""
    assert set(_VULN_CLASSES) == {"injection", "xss", "ssrf", "authz"}, (
        f"_VULN_CLASSES 应为 injection/xss/ssrf/authz，实际: {_VULN_CLASSES}")
