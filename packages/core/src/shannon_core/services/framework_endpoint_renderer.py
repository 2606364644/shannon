"""Render framework-inferred endpoints for recon prompt injection."""

from shannon_core.services.framework_analyzer import InferredEndpoint


def render_framework_endpoints(endpoints: list[InferredEndpoint]) -> str:
    """Render framework-inferred endpoints as markdown for recon section 4.2."""
    if not endpoints:
        return "(no deterministically detected framework auto-generated endpoints.)"

    lines = [
        "## Framework Endpoints (deterministic finale-rest/epilogue detection)",
        "",
        "| Method | Path | Framework Origin | Model | Middleware | Vulnerability Indicators |",
        "|---|---|---|---|---|---|",
    ]
    for ep in endpoints:
        middleware = ", ".join(ep.middleware) if ep.middleware else "-"
        indicators = ", ".join(ep.vulnerability_indicators) if ep.vulnerability_indicators else "-"
        model = ep.model or "-"
        lines.append(f"- {ep.method} {ep.path}")
        lines.append(
            f"| `{ep.method}` | `{ep.path}` | {ep.source} | {model} | {middleware} | {indicators} |"
        )

    lines.extend(
        [
            "",
            "These framework-analyzer findings are deterministic facts for Framework Origin in section 4.2. "
            "The recon agent must still independently inspect other endpoints; 下限非上限.",
        ]
    )
    return "\n".join(lines)
