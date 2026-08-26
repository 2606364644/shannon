"""T8（spec 2026-08-26-report-generation-agent §6.2）：融合报告迁入 web 编排（fusion 版）。

_generate_combined_report 升级：白盒 rd.json + 黑盒 rd.json → fuse_report_data
（cross_verification 三态 + verification_gaps）→ combined/run-K/report_data.json
+ combined_report.md（导出）。融合失败回退旧 renderer（md 链路不断）。
"""
import json
from pathlib import Path

import pytest


def _setup(scan_dir: Path, run_id: str = "run-1"):
    from supernova_core.utils.paths import (
        blackbox_run_dir, combined_run_dir, whitebox_dir, blackbox_dir,
    )
    wb = whitebox_dir(scan_dir / "deliverables")
    (wb / "intermediate").mkdir(parents=True, exist_ok=True)
    (wb / "intermediate" / "xss_exploitation_queue.json").write_text(json.dumps({
        "vulnerabilities": [{
            "ID": "XSS-VULN-01", "vulnerability_type": "Stored",
            "externally_exploitable": True, "confidence": "high",
            "merge_source": "llm-only", "title": "存储型 XSS",
            "severity": "high",
            "endpoints": ["POST /memos"],
            "report_endpoints": [{"method": "POST", "path": "/memos"}],
        }]}, ensure_ascii=False), encoding="utf-8")
    bb = blackbox_dir(blackbox_run_dir(scan_dir, run_id) / "deliverables")
    (bb / "intermediate").mkdir(parents=True, exist_ok=True)
    (bb / "intermediate" / "xss_exploit_verdicts.json").write_text(json.dumps({
        "vuln_class": "xss", "accepted_ids": ["XSS-VULN-01"],
        "verdicts": [{
            "vulnerability_id": "XSS-VULN-01", "status": "exploited",
            "severity": "high", "impact": "i",
            "exploitation_steps": ["POST /memos curl -X POST http://t/memos"],
            "proof_of_impact": "uid=1000 回显",
        }], "rejected": [],
    }, ensure_ascii=False), encoding="utf-8")
    return combined_run_dir(scan_dir, run_id)


@pytest.mark.asyncio
async def test_generate_combined_report_fuses_report_data(tmp_path, monkeypatch):
    """黑盒完成后：产 combined/run-K/report_data.json（三态+gaps）+ md 导出。"""
    from supernova_web.components.scan_manager import ScanManager

    scan_dir = tmp_path / "NodeGoat-1"
    out_dir = _setup(scan_dir)
    mgr = ScanManager.__new__(ScanManager)  # 只测该方法,不跑 __init__
    await mgr._generate_combined_report(scan_dir, "run-1")

    rd_path = out_dir / "report_data.json"
    assert rd_path.exists()
    data = json.loads(rd_path.read_text(encoding="utf-8"))
    assert data["scan"]["track"] == "combined"
    by_id = {v["id"]: v for v in data["vulnerabilities"]}
    assert by_id["XSS-VULN-01"]["cross_verification"] == "verified"
    assert by_id["XSS-VULN-01"]["evidence"]["dynamic_evidence"] == "uid=1000 回显"
    # md 导出兼容（/report?track=combined 仍读 combined_report.md）
    assert (out_dir / "combined_report.md").exists()


@pytest.mark.asyncio
async def test_generate_combined_report_falls_back_to_renderer(tmp_path, monkeypatch):
    """fusion 失败（如白盒 rd 组装炸）→ 回退旧 renderer 产 md（链路不断）。"""
    from supernova_web.components import scan_manager as sm

    scan_dir = tmp_path / "NodeGoat-2"
    out_dir = _setup(scan_dir)
    mgr = sm.ScanManager.__new__(sm.ScanManager)

    called = {}

    async def _boom(*a, **kw):
        raise RuntimeError("fusion down")

    import supernova_core.services.report_data_builder as builder
    monkeypatch.setattr(builder, "build_report_data", _boom)

    def _fake_render(whitebox_root, blackbox_root, out_dir):
        called["yes"] = True
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "combined_report.md").write_text("# 降级融合报告",
                                                    encoding="utf-8")
    import supernova_web.components.combined_report_renderer as cr
    monkeypatch.setattr(cr, "render_combined_report", _fake_render)
    monkeypatch.setattr(sm, "combined_run_dir",
                        lambda scan_dir, run_id: out_dir)

    await mgr._generate_combined_report(scan_dir, "run-1")
    assert called.get("yes") is True
    assert (out_dir / "combined_report.md").read_text(encoding="utf-8") == \
        "# 降级融合报告"
