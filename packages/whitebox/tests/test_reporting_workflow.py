"""reporting phase 接入断言。

reporting 真实执行依赖 temporalio worker + LLM,无法在 CI 单元测试;
此处用静态分析断言 workflow 串起了 render_findings → assemble_report →
run_agent(REPORT),行为正确性靠人工冒烟(Task 7 / spec §6.4)。
"""
from pathlib import Path


def _workflow_src() -> str:
    p = Path(__file__).resolve().parents[1] / "src/supernova_whitebox/pipeline/workflows.py"
    return p.read_text(encoding="utf-8")


def test_reporting_phase_calls_assemble_report():
    src = _workflow_src()
    assert "activities.assemble_report" in src, "reporting phase 须调 assemble_report"


def test_reporting_phase_runs_report_agent():
    src = _workflow_src()
    # run_agent 以 agent_name="report" 调用,跑 REPORT agent 生成执行摘要。
    # workflows.py 既有的 agent_name 覆写约定是 dict-spread:
    #   ActivityInput(**{**act_input.__dict__, "agent_name": "<name>"})
    # (见 pre-recon/recon 等阶段),因此这里匹配该字面量形式。
    assert '"agent_name": "report"' in src, (
        "reporting phase 须以 agent_name=report 调 run_agent 跑 REPORT agent"
    )


def test_reporting_phase_order_assemble_before_report():
    src = _workflow_src()
    i_assemble = src.find("activities.assemble_report")
    # REPORT agent 调用(agent_name="report" 字面量)必须在 assemble_report 之后;
    # 锚定可执行代码字面量,避免匹配到注释里的 "report" 字样。
    assert i_assemble != -1
    assert src.find('"agent_name": "report"', i_assemble) != -1, (
        "run_agent(agent_name=report) 必须在 assemble_report 之后"
    )


def test_reporting_phase_has_inject_attack_chains_after_run_report_agent() -> None:
    """攻击链注入必须在 run-report-agent 之后（顺序硬约束，防覆盖回归）。"""
    from supernova_whitebox.pipeline.step_intents import step_names
    steps = step_names("reporting")
    assert "inject-attack-chains" in steps
    assert steps.index("inject-attack-chains") > steps.index("run-report-agent")


def test_reporting_phase_inject_attack_chains_after_report_agent_in_workflow() -> None:
    """源码级硬约束：workflows.py 里 inject_attack_chains 必须在 run-report-agent 之后调用（防覆盖回归）。

    step_intents 注册表只保证 *意图* 顺序；若有人删掉 workflows.py 里
    `await workflow.execute_activity(activities.inject_attack_chains, …)` 调用但保留
    step_intents 条目，上面的注册表测试仍会通过，覆盖回归会再次发生。本测试直接
    扫源码，镜像 sibling `test_reporting_phase_order_assemble_before_report` 的
    `_workflow_src()` + `.find()` 链式模式，锚定可执行代码字面量。
    """
    src = _workflow_src()
    # 锚定 run-report-agent 对应的 execute_activity 调用（agent_name="report" 字面量，
    # 与 sibling 测试同款锚点；不匹配注释里的 "report" 字样）。
    i_report = src.find('"agent_name": "report"')
    assert i_report != -1, "找不到 run-report-agent 调用（agent_name=report 字面量）"
    assert src.find("activities.inject_attack_chains", i_report) != -1, (
        "inject_attack_chains 必须在 run-report-agent 之后执行（防 report-executive 覆盖攻击链章节）"
    )
