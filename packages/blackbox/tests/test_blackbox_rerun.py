from pathlib import Path

from supernova_blackbox.pipeline.blackbox_rerun import (
    detect_blackbox_completed,
    archive_blackbox_deliverables,
)


def _bb(deliverables: Path) -> Path:
    """Helper：返回 deliverables/blackbox（新结构黑盒产出物目录）。"""
    bb = deliverables / "blackbox"
    bb.mkdir(parents=True, exist_ok=True)
    return bb


def test_detect_returns_false_when_no_evidence(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    # 只有白盒 queue 文件（根），无 evidence 在 blackbox/
    (deliverables / "injection_exploitation_queue.json").write_text("{}")

    assert detect_blackbox_completed(deliverables) is False


def test_detect_returns_true_when_evidence_exists(tmp_path):
    deliverables = tmp_path / "deliverables"
    bb = _bb(deliverables)
    (bb / "injection_exploitation_evidence.md").write_text("# evidence")

    assert detect_blackbox_completed(deliverables) is True


def test_detect_returns_true_when_evidence_in_blackbox_subdir(tmp_path):
    """新结构：evidence 在 deliverables/blackbox/。"""
    from supernova_blackbox.pipeline.blackbox_rerun import detect_blackbox_completed
    dlv = tmp_path / "deliverables"
    (dlv / "blackbox").mkdir(parents=True)
    (dlv / "blackbox" / "injection_exploitation_evidence.md").write_text("x")
    assert detect_blackbox_completed(dlv) is True


def test_archive_moves_blackbox_deliverables_to_dated_dir(tmp_path):
    deliverables = tmp_path / "deliverables"
    bb = _bb(deliverables)
    # evidence + findings + report（覆盖 bb_deliverable_patterns 三类）
    (bb / "injection_exploitation_evidence.md").write_text("e")
    (bb / "ssrf_findings.md").write_text("f")
    (bb / "comprehensive_security_assessment_report.md").write_text("r")
    # 白盒文件不归档（放根 deliverables/，属白盒轨）
    (deliverables / "injection_analysis_deliverable.md").write_text("keep")

    archive = archive_blackbox_deliverables(deliverables, "20260619-1530")

    assert archive == deliverables / "blackbox" / ".blackbox-archive" / "20260619-1530"
    assert (archive / "injection_exploitation_evidence.md").exists()
    assert (archive / "ssrf_findings.md").exists()
    assert (archive / "comprehensive_security_assessment_report.md").exists()
    # 源文件已移走
    assert not (bb / "injection_exploitation_evidence.md").exists()
    # 白盒文件保留
    assert (deliverables / "injection_analysis_deliverable.md").exists()


def test_archive_moves_to_blackbox_subdir_archive(tmp_path):
    """归档源与目标都在 blackbox/ 内。"""
    from supernova_blackbox.pipeline.blackbox_rerun import archive_blackbox_deliverables
    dlv = tmp_path / "deliverables"
    (dlv / "blackbox").mkdir(parents=True)
    (dlv / "blackbox" / "injection_exploitation_evidence.md").write_text("x")
    archive = archive_blackbox_deliverables(dlv, "20260630-120000")
    assert archive == dlv / "blackbox" / ".blackbox-archive" / "20260630-120000"
    assert (archive / "injection_exploitation_evidence.md").exists()
    assert not (dlv / "blackbox" / "injection_exploitation_evidence.md").exists()


def test_archive_handles_empty_deliverables(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    # blackbox/ 子目录存在但为空
    _bb(deliverables)

    archive = archive_blackbox_deliverables(deliverables, "20260619-1530")

    assert archive.exists()  # 目录创建，即使无文件可移
    assert list(archive.iterdir()) == []


def test_detect_true_with_multiple_evidence(tmp_path):
    deliverables = tmp_path / "deliverables"
    bb = _bb(deliverables)
    for vt in ("injection", "xss", "auth"):
        (bb / f"{vt}_exploitation_evidence.md").write_text("e")
    assert detect_blackbox_completed(deliverables) is True


def test_archive_all_five_evidence_and_findings(tmp_path):
    deliverables = tmp_path / "deliverables"
    bb = _bb(deliverables)
    for vt in ("injection", "xss", "auth", "ssrf", "authz"):
        (bb / f"{vt}_exploitation_evidence.md").write_text("e")
        (bb / f"{vt}_findings.md").write_text("f")
    (bb / "comprehensive_security_assessment_report.md").write_text("r")

    archive = archive_blackbox_deliverables(deliverables, "20260619-1600")

    assert len(list(archive.glob("*_exploitation_evidence.md"))) == 5
    assert len(list(archive.glob("*_findings.md"))) == 5
    assert (archive / "comprehensive_security_assessment_report.md").exists()
    # blackbox/ 顶层（归档目录之外）清空了黑盒产出物
    assert list(bb.glob("*_exploitation_evidence.md")) == []


def test_archive_avoids_overwrite_on_duplicate_run_ts(tmp_path):
    """同 run_ts 二次归档：重名文件加序号后缀，不覆盖第一次的归档。"""
    deliverables = tmp_path / "deliverables"
    bb = _bb(deliverables)
    run_ts = "20260619-1600"

    # 第一次：归档一个 evidence
    (bb / "injection_exploitation_evidence.md").write_text("first")
    archive_blackbox_deliverables(deliverables, run_ts)
    # 第二次：构造同名新 evidence 文件，用相同 run_ts 再归档
    (bb / "injection_exploitation_evidence.md").write_text("second")
    archive = archive_blackbox_deliverables(deliverables, run_ts)

    # 归档目录两个文件并存：原名 + _1 后缀，第二次不覆盖第一次
    assert (archive / "injection_exploitation_evidence.md").exists()
    assert (archive / "injection_exploitation_evidence_1.md").exists()
    archived = sorted(p.name for p in archive.iterdir())
    assert len(archived) == 2
    # 原文件内容保留（未被覆盖）
    assert (archive / "injection_exploitation_evidence.md").read_text() == "first"


def test_clean_command_removed():
    """Regression guard: clean 子命令已删除（被 --fresh/--rerun 取代）。

    调用应非零退出（click 报 no such command "clean"）。护栏防 clean 复活。
    """
    from click.testing import CliRunner
    from supernova_blackbox.cli.main import cli as bb_cli
    from supernova_whitebox.cli.main import cli as wb_cli

    runner = CliRunner()

    bb_res = runner.invoke(bb_cli, ["workspace", "clean", "x"])
    assert bb_res.exit_code != 0
    assert "No such command" in bb_res.output or "clean" in bb_res.output.lower()

    wb_res = runner.invoke(wb_cli, ["workspace", "clean", "x"])
    assert wb_res.exit_code != 0
    assert "No such command" in wb_res.output or "clean" in wb_res.output.lower()
