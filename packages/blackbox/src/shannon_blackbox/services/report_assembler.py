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
