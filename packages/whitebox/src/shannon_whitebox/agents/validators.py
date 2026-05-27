from pathlib import Path

from shannon_core.models.agents import AgentName, AGENTS
from shannon_core.models.errors import ErrorCode, PentestError

async def validate_deliverable(deliverables_path: Path, agent_name: AgentName) -> bool:
    defn = AGENTS[agent_name]
    deliverable_file = deliverables_path / defn.deliverable_filename
    if not deliverable_file.exists():
        raise PentestError(
            f"Missing deliverable: {defn.deliverable_filename}",
            "validation",
            error_code=ErrorCode.OUTPUT_VALIDATION_FAILED,
            context={"agent_name": agent_name.value, "expected_file": defn.deliverable_filename},
        )
    return True

def get_vuln_type(agent_name: AgentName) -> str | None:
    value = agent_name.value
    if value.endswith("-vuln"):
        return value.replace("-vuln", "")
    return None

def get_queue_filename(agent_name: AgentName) -> str | None:
    vuln_type = get_vuln_type(agent_name)
    if vuln_type:
        return f"{vuln_type}_exploitation_queue.json"
    return None
