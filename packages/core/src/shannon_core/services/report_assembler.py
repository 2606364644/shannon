import json
from pathlib import Path
from typing import Any

from shannon_core.utils.file_io import async_path_exists, async_read_file, async_write_file


class ReportAssembler:
    @staticmethod
    async def assemble(
        deliverables_path: Path,
        vuln_classes: list[str],
        report_path: Path,
        report_config: dict[str, Any] | None = None,
    ) -> None:
        sections: list[str] = []
        for vuln_class in vuln_classes:
            evidence = deliverables_path / f"{vuln_class}_exploitation_evidence.md"
            findings = deliverables_path / f"{vuln_class}_findings.md"
            analysis = deliverables_path / f"{vuln_class}_analysis_deliverable.md"
            if await async_path_exists(evidence):
                content = await async_read_file(evidence)
                sections.append(content)
            elif await async_path_exists(findings):
                content = await async_read_file(findings)
                sections.append(content)
            elif await async_path_exists(analysis):
                content = await async_read_file(analysis)
                sections.append(content)
        report_content = "\n\n---\n\n".join(sections)
        await async_write_file(report_path, report_content)

    @staticmethod
    async def render_attack_chains(deliverables_path: Path) -> str:
        """Render multi-step attack chains from attack_chains.json as a markdown section.

        Reads the merged dual-track attack_chains.json (produced by
        run_attack_chain_assembly_v2) and renders each chain as a markdown
        sub-section with ordered steps.  Returns empty string when the file is
        missing or contains no chains — callers just append the result.
        """
        chains_path = deliverables_path / "attack_chains.json"
        if not await async_path_exists(chains_path):
            return ""

        try:
            raw = await async_read_file(chains_path)
            data = json.loads(raw)
        except (json.JSONDecodeError, OSError, ValueError):
            return ""

        chains: list[dict] = data.get("chains", []) or []
        if not chains:
            return ""

        lines: list[str] = [
            "",
            "---",
            "",
            "## 攻击链（多步利用路径）",
            "",
        ]
        for i, chain in enumerate(chains, start=1):
            cid = chain.get("id", f"chain-{i}")
            name = chain.get("name", cid)
            desc = chain.get("description", "")
            vuln_type = chain.get("vuln_type", "")
            severity = chain.get("severity", "")
            confidence = chain.get("confidence", "")

            lines.append(f"### {cid}: {name}")
            lines.append("")
            if desc:
                lines.append(f"{desc}")
                lines.append("")
            lines.append(f"- **类型:** {vuln_type}")
            lines.append(f"- **严重程度:** {severity}")
            lines.append(f"- **置信度:** {confidence}")
            lines.append(f"- **步骤:**")
            for step in chain.get("steps", []):
                order = step.get("order", "")
                endpoint = step.get("endpoint", "")
                method = step.get("method", "-")
                sd = step.get("description", "")
                lines.append(f"  {order}. {endpoint} ({method}) — {sd}")
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    async def inject_model_info(report_path: Path, session_path: Path) -> None:
        if not session_path.exists():
            return

        try:
            session_data = json.loads(session_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return

        metrics = session_data.get("metrics", {})
        agents = metrics.get("agents", {})
        models: set[str] = set()
        for agent_data in agents.values():
            if isinstance(agent_data, dict):
                model = agent_data.get("model")
                if model:
                    models.add(str(model))

        if not models:
            return

        if not await async_path_exists(report_path):
            return

        model_line = f"- **Model:** {', '.join(sorted(models))}"
        content = await async_read_file(report_path)
        lines = content.split("\n")
        new_lines: list[str] = []
        inserted = False

        for line in lines:
            new_lines.append(line)
            if not inserted and "- Assessment Date:" in line:
                new_lines.append(model_line)
                inserted = True

        if not inserted:
            for i, line in enumerate(new_lines):
                if line.strip() == "## Executive Summary":
                    new_lines.insert(i + 1, model_line)
                    inserted = True
                    break

        if inserted:
            await async_write_file(report_path, "\n".join(new_lines))
