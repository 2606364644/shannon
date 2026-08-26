import json
import pytest

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
# render_findings 已退役（spec 2026-08-26-report-single-source-rendering §3：
# 逻辑并入 assemble_report，findings.md 从 report_data 单点渲染）——时序锚点
# 改为 write_structured_poc 先于 assemble_report（rd 组装吃写回后的 report_poc），
# 断言移至 test_reporting_workflow.py 统一维护。
