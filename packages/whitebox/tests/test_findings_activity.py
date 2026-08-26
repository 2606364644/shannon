import json
import pytest
from pathlib import Path

from supernova_core.models.queue_schemas import InjectionVulnerability, VulnerabilityQueue


@pytest.fixture(autouse=True)
def _en_lang_default(monkeypatch):
    """断言基于英文渲染（i18n 前行为）；默认 en。"""
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "en")


@pytest.mark.asyncio
async def test_render_findings_activity_generates_findings(tmp_path):
    """Integration test: render_findings activity should produce findings MD from queue JSON."""
    from supernova_core.services.findings_renderer import FindingsRenderer

    repo = tmp_path / "my-repo"
    deliverables = tmp_path / "workspaces" / "wb-session" / "deliverables"
    deliverables.mkdir(parents=True)

    queue = VulnerabilityQueue(vulnerabilities=[
        InjectionVulnerability(
            ID="INJECTION-001", vulnerability_type="SQLi",
            externally_exploitable=True, confidence="high",
            source="query param", path="/search", sink_call="db.execute",
        ),
    ])
    (deliverables / "injection_exploitation_queue.json").write_text(
        queue.model_dump_json(indent=2)
    )

    await FindingsRenderer.render_findings_from_queues(deliverables)

    findings = (deliverables / "injection_findings.md")
    assert findings.exists()
    content = findings.read_text()
    assert "### INJECTION-001" in content
    # Sink 行已并入问题点说明句（vuln-card-consolidation §4.1 + 七节卡 §4.3）
    assert "db.execute" in content.split("- **Issue:**", 1)[1].splitlines()[0]


# ---------- §4.2（spec 2026-08-26-vuln-card-seven-sections）POC 写回时序前移 ----------
# md 卡要原生渲染 POC 节（curl + Burp 双格式），结构化 POC 写回必须在
# render_findings 之前完成——镜像 test_reporting_workflow.py 的源码锚定模式
# （reporting 真实执行依赖 temporal worker + LLM，静态断言防时序回归）。


def _workflow_src() -> str:
    return (Path(__file__).resolve().parents[1]
            / "src/supernova_whitebox/pipeline/workflows.py"
            ).read_text(encoding="utf-8")


def test_write_structured_poc_runs_before_render_findings_in_workflow():
    """源码级硬约束：workflows.py 里 write_structured_poc 在 render_findings 之前。"""
    src = _workflow_src()
    i_write = src.find("activities.write_structured_poc")
    assert i_write != -1, "找不到 write_structured_poc 的 execute_activity 调用"
    i_render = src.find("activities.render_findings")
    assert i_render != -1, "找不到 render_findings 的 execute_activity 调用"
    assert i_write < i_render, (
        "write_structured_poc 必须在 render_findings 之前执行"
        "（md 卡原生 POC 节依赖写回后的 report_poc）"
    )


def test_write_structured_poc_step_registered_before_render_findings():
    """step_intents 注册表顺序：write-structured-poc 在 render-findings 之前（dashboard 一致）。"""
    from supernova_whitebox.pipeline.step_intents import step_names
    steps = step_names("reporting")
    assert "write-structured-poc" in steps
    assert steps.index("write-structured-poc") < steps.index("render-findings")
