"""reporting phase 接入断言（spec 2026-08-26-report-single-source-rendering §3）。

reporting 真实执行依赖 temporalio worker + LLM,无法在 CI 单元测试;
此处用静态分析断言 workflow 串起新时序:

    write_structured_poc → assemble_report（rd 初版+分项单点渲染,不产 md）
    → run_report_polish（摘要+QA 七节覆盖率+回炉）
    → export_report_markdown_files（rd → comprehensive md + poc_collection）

md 链路旧步骤（render_findings / run_agent(report) / verify_report_vuln_blocks /
inject_attack_chains / inject_gitnexus_track_status / generate_poc_report）
退役——md 改由 export activity 从 report_data 确定性导出（单源）。
"""
from pathlib import Path


def _workflow_src() -> str:
    p = Path(__file__).resolve().parents[1] / "src/supernova_whitebox/pipeline/workflows.py"
    return p.read_text(encoding="utf-8")


# 退役步骤的源码锚点（可执行调用字面量，非注释）
_RETIRED_MARKERS = (
    "activities.render_findings",
    '"agent_name": "report"',
    "activities.verify_report_vuln_blocks",
    "activities.inject_attack_chains",
    "activities.inject_gitnexus_track_status",
    "activities.generate_poc_report",
)


def test_reporting_phase_calls_assemble_report():
    src = _workflow_src()
    assert "activities.assemble_report" in src, "reporting phase 须调 assemble_report"


def test_reporting_phase_calls_polish_and_export():
    src = _workflow_src()
    assert "activities.run_report_polish" in src
    assert "activities.export_report_markdown_files" in src, (
        "reporting phase 须调 export_report_markdown_files（md 单源导出）"
    )


def test_reporting_phase_export_after_polish():
    """export 吃 polish 后的终版 rd——顺序硬约束。"""
    src = _workflow_src()
    i_polish = src.find("activities.run_report_polish")
    assert i_polish != -1
    assert src.find("activities.export_report_markdown_files", i_polish) != -1, (
        "export_report_markdown_files 必须在 run_report_polish 之后（吃终版 report_data）"
    )


def test_reporting_phase_write_structured_poc_before_assemble():
    src = _workflow_src()
    i_poc = src.find("activities.write_structured_poc")
    i_assemble = src.find("activities.assemble_report")
    assert i_poc != -1 and i_assemble != -1
    assert i_poc < i_assemble, "write_structured_poc 必须先于 assemble_report"


def test_reporting_phase_retired_md_activities_not_scheduled():
    src = _workflow_src()
    for marker in _RETIRED_MARKERS:
        assert marker not in src, f"退役 md 链路步骤仍被调度: {marker}"


def test_reporting_phase_step_registry_matches_new_timing():
    """step_intents 注册表与新时序一致（退役步移除、export 步新增）。"""
    from supernova_whitebox.pipeline.step_intents import step_names
    assert step_names("reporting") == (
        "write-structured-poc",
        "assemble-report",
        "report-polish",
        "export-report-markdown",
    )


def test_export_activity_registered_on_workers():
    """b51eb9a4 教训：新 activity 两处 worker 注册表（CLI worker.py + web
    runner.py）都必须 import + 列入 activities，漏注册会 fail-fast/静默不跑。"""
    root = Path(__file__).resolve().parents[3]
    for rel in ("packages/whitebox/src/supernova_whitebox/worker.py",
                "packages/worker/src/supernova_worker/runner.py"):
        src = (root / rel).read_text(encoding="utf-8")
        count = src.count("export_report_markdown_files")
        assert count >= 2, (
            f"export_report_markdown_files 在 {rel} 仅出现 {count} 次，"
            f"预期 >= 2（import + activities 列表）"
        )
