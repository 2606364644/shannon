"""Build deterministic GitNexus context for the pre-recon prompt."""

from pathlib import Path

from shannon_core.code_index.models import CodeIndex, EntryPoint
from shannon_core.code_index.parameter_models import SinkCallSite
from shannon_core.code_index.template_escape_detector import (
    TemplateEscapeFinding,
    detect_template_escapes,
)


_ENTRY_CAP = 80
_SINK_CAP = 150
_TEMPLATE_CAP = 150


def render_pre_recon_gitnexus_track(
    entry_points: list[EntryPoint],
    sinks: list[SinkCallSite],
    escape_findings: list[TemplateEscapeFinding],
) -> str:
    """Render deterministic entry, sink, and template escape findings as markdown."""
    lines = [
        "## Pre-Recon GitNexus Track (deterministic entry points / sinks / template escaping)",
        "",
        f"### Entry Points ({len(entry_points)})",
    ]

    for entry_point in entry_points[:_ENTRY_CAP]:
        route = entry_point.route or entry_point.func_block_id
        method = entry_point.http_method or "?"
        auth = entry_point.authentication or "?"
        lines.append(f"- `{method} {route}` - auth={auth}")
    if len(entry_points) > _ENTRY_CAP:
        lines.append(f"- ... (+{len(entry_points) - _ENTRY_CAP} more; see code_index.json)")

    lines.extend(["", f"### Sinks ({len(sinks)})"])
    for sink in sinks[:_SINK_CAP]:
        category = sink.category.value if sink.category else "?"
        lines.append(
            f"- `{sink.id}` ({sink.file_path}:{sink.line}) "
            f"{category}/{sink.sink_subtype} @ `{sink.callee_name}`"
        )
    if len(sinks) > _SINK_CAP:
        lines.append(f"- ... (+{len(sinks) - _SINK_CAP} more; see code_index.json)")

    unescaped = [finding for finding in escape_findings if finding.escaping == "unescaped"]
    lines.extend(["", f"### 模板转义 (unescaped = high risk, {len(unescaped)})"])
    for finding in unescaped[:_TEMPLATE_CAP]:
        lines.append(
            f"- `{finding.file_path}:{finding.line}` {finding.engine}: "
            f"`{finding.directive}` - unescaped"
        )
    if len(unescaped) > _TEMPLATE_CAP:
        lines.append(f"- ... (+{len(unescaped) - _TEMPLATE_CAP} more; see template files)")

    lines.extend(
        [
            "",
            "**下限非上限**: These deterministic entry/sink/template findings are a lower bound "
            "for pre-recon coverage. The LLM must cover them and still 独立 explore sinks, "
            "entry points, and templates not listed here.",
        ]
    )
    return "\n".join(lines)


def build_pre_recon_gitnexus_track(repo_root: Path, deliverables: Path) -> str:
    """Read code_index.json and template files, then return deterministic track markdown."""
    code_index_path = deliverables / "code_index.json"
    if not code_index_path.exists():
        return "无 code_index.json; pre-recon GitNexus track 降级为空. LLM should continue autonomous exploration."

    index = CodeIndex.model_validate_json(code_index_path.read_text(encoding="utf-8"))

    template_files: list[Path] = []
    if index.file_manifest:
        for entry in index.file_manifest.filter_by_type("template"):
            template_path = repo_root / entry.file_path
            if template_path.exists():
                template_files.append(template_path)

    escape_findings = detect_template_escapes(template_files)
    return render_pre_recon_gitnexus_track(
        index.entry_points,
        index.sink_call_sites,
        escape_findings,
    )
