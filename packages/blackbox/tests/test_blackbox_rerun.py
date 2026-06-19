from pathlib import Path

from shannon_blackbox.pipeline.blackbox_rerun import (
    detect_blackbox_completed,
    archive_blackbox_deliverables,
)


def test_detect_returns_false_when_no_evidence(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    # 只有白盒 queue 文件，无 evidence
    (deliverables / "injection_exploitation_queue.json").write_text("{}")

    assert detect_blackbox_completed(deliverables) is False


def test_detect_returns_true_when_evidence_exists(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "injection_exploitation_evidence.md").write_text("# evidence")

    assert detect_blackbox_completed(deliverables) is True


def test_archive_moves_blackbox_deliverables_to_dated_dir(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    # evidence + findings + report（覆盖 bb_deliverable_patterns 三类）
    (deliverables / "injection_exploitation_evidence.md").write_text("e")
    (deliverables / "ssrf_findings.md").write_text("f")
    (deliverables / "comprehensive_security_assessment_report.md").write_text("r")
    # 白盒文件不归档
    (deliverables / "injection_analysis_deliverable.md").write_text("keep")

    archive = archive_blackbox_deliverables(deliverables, "20260619-1530")

    assert archive == deliverables / ".blackbox-archive" / "20260619-1530"
    assert (archive / "injection_exploitation_evidence.md").exists()
    assert (archive / "ssrf_findings.md").exists()
    assert (archive / "comprehensive_security_assessment_report.md").exists()
    # 源文件已移走
    assert not (deliverables / "injection_exploitation_evidence.md").exists()
    # 白盒文件保留
    assert (deliverables / "injection_analysis_deliverable.md").exists()


def test_archive_handles_empty_deliverables(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()

    archive = archive_blackbox_deliverables(deliverables, "20260619-1530")

    assert archive.exists()  # 目录创建，即使无文件可移
    assert list(archive.iterdir()) == []


def test_detect_true_with_multiple_evidence(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    for vt in ("injection", "xss", "auth"):
        (deliverables / f"{vt}_exploitation_evidence.md").write_text("e")
    assert detect_blackbox_completed(deliverables) is True


def test_archive_all_five_evidence_and_findings(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    for vt in ("injection", "xss", "auth", "ssrf", "authz"):
        (deliverables / f"{vt}_exploitation_evidence.md").write_text("e")
        (deliverables / f"{vt}_findings.md").write_text("f")
    (deliverables / "comprehensive_security_assessment_report.md").write_text("r")

    archive = archive_blackbox_deliverables(deliverables, "20260619-1600")

    assert len(list(archive.glob("*_exploitation_evidence.md"))) == 5
    assert len(list(archive.glob("*_findings.md"))) == 5
    assert (archive / "comprehensive_security_assessment_report.md").exists()
    # deliverables 顶层清空了黑盒产出物
    assert list(deliverables.glob("*_exploitation_evidence.md")) == []
