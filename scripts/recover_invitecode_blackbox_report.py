"""一次性恢复脚本：invite_code_center 黑盒报告 verdict 双重丢失救济。

根因：exploit_executor.py:75 只从 metrics.structured_output 取 verdicts，但
GLM/claude-agent-sdk 引擎下 agent 用 Write 工具把 verdicts 写到
deliverables/.supernova/deliverables/{vuln}_exploitation_verdicts.json（final
message 是自然语言 → structured_output 为空），加 skip_artifact_postprocess=True
断了文件提升兜底 → verdicts.json accepted_ids=[] → coverage_renderer 把
evidence 全覆盖成 "Unverified" → 报告全 Unverified。

救济：用 .supernova/deliverables/ 里 agent 写的真实 verdicts 重新渲染 evidence +
verdicts.json，再重 assemble 报告。不耗 token、不重跑黑盒。跑完可删。
"""
import asyncio
import json
import shutil
from pathlib import Path

from supernova_blackbox.services.exploit_evidence_renderer import ExploitEvidenceRenderer
from supernova_blackbox.services.exploit_verdict_validator import validate_exploit_verdicts
from supernova_blackbox.services.coverage_renderer import close_coverage_gaps
from supernova_core.models.queue_schemas import VulnerabilityQueue
from supernova_core.services.report_assembler import ReportAssembler

WS = Path("workspaces/invite_code_center_20260629-134944")
DELIV = WS / "deliverables"
AGENT_OUT = DELIV / ".supernova/deliverables"
# 只重渲染有 agent verdict 落盘的 3 类；injection/auth 无 exploitation（走 analysis 回退）
VULNS = ["xss", "ssrf", "authz"]
ALL_CLASSES = ["injection", "xss", "auth", "authz", "ssrf"]

_SEVERITY_MAP = {"critical": "critical", "high": "high", "medium": "medium", "low": "low"}


def normalize_verdict(v: dict) -> dict:
    """L0 lenient: 把 agent 不严格的产出 normalize 到 ExploitVerdict schema。

    根因层②：agent 不严格遵守 structured_output_schema——severity 大写、部分
    字段富结构（steps/proof/evidence/what_we_tried）。L1 pydantic 严格校验会拒，
    这里先归一化（生产路径应在 validator 加同样层）。
    """
    v = dict(v)
    status = v.get("status")
    sev = v.get("severity")
    if isinstance(sev, str):
        v["severity"] = _SEVERITY_MAP.get(sev.lower(), "low")
    if status == "exploited":
        steps = v.get("exploitation_steps")
        if isinstance(steps, list) and steps and not isinstance(steps[0], str):
            v["exploitation_steps"] = [
                s.get("action") if isinstance(s, dict) else str(s) for s in steps
            ]
        if isinstance(v.get("proof_of_impact"), (dict, list)):
            v["proof_of_impact"] = json.dumps(v["proof_of_impact"], ensure_ascii=False)
    elif status in ("false_positive", "out_of_scope_internal"):
        if isinstance(v.get("evidence"), (dict, list)):
            v["evidence"] = json.dumps(v["evidence"], ensure_ascii=False)
    elif status == "blocked_by_security":
        wwt = v.get("what_we_tried")
        if isinstance(wwt, list):
            v["what_we_tried"] = "; ".join(str(x) for x in wwt)
    return v


async def main() -> None:
    bak = WS / "deliverables._corrupt_backup"
    if not bak.exists():
        bak.mkdir()
        for vuln in VULNS:
            for suf in ["_exploitation_evidence.md", "_exploit_verdicts.json"]:
                f = DELIV / f"{vuln}{suf}"
                if f.exists():
                    shutil.copy2(f, bak / f.name)
        rpt = DELIV / "comprehensive_security_assessment_report.md"
        if rpt.exists():
            shutil.copy2(rpt, bak / rpt.name)
        print(f"[backup] corrupted files -> {bak}")

    print("\n[re-render] evidence + verdicts.json from agent-written verdicts:")
    for vuln in VULNS:
        verdict_file = AGENT_OUT / f"{vuln}_exploitation_verdicts.json"
        raw = [normalize_verdict(v) for v in json.loads(
            verdict_file.read_text(encoding="utf-8"))["verdicts"]]
        queue_path = DELIV / f"{vuln}_exploitation_queue.json"
        parsed = VulnerabilityQueue.parse_lenient(queue_path.read_text(encoding="utf-8"))
        valid_ids = {v.ID for v in parsed.queue.vulnerabilities}
        validation = validate_exploit_verdicts(raw, valid_ids)
        (DELIV / f"{vuln}_exploitation_evidence.md").write_text(
            ExploitEvidenceRenderer.render(validation, vuln), encoding="utf-8")
        ExploitEvidenceRenderer.write_verdicts_json(validation, vuln, DELIV)
        accepted = [f"{v.vulnerability_id}:{v.status}" for v in validation.accepted]
        rejected = [(r[0].get("vulnerability_id"), r[1][:50]) for r in validation.rejected]
        print(f"  {vuln}: queue={sorted(valid_ids)} accepted={accepted} rejected={rejected}")

    print("\n[reassemble] close_coverage_gaps + ReportAssembler.assemble")
    await close_coverage_gaps(DELIV, ALL_CLASSES)
    await ReportAssembler.assemble(
        DELIV, ALL_CLASSES, DELIV / "comprehensive_security_assessment_report.md")
    print("  done -> comprehensive_security_assessment_report.md")


if __name__ == "__main__":
    asyncio.run(main())
