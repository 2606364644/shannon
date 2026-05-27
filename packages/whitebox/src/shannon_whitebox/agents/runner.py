from dataclasses import dataclass, field
from typing import Any

@dataclass
class ClaudeRunResult:
    text: str = ""
    success: bool = False
    duration: int = 0
    turns: int = 0
    cost: float = 0.0
    model: str | None = None
    structured_output: Any | None = None
    error: str | None = None
    retryable: bool = True

async def run_claude_prompt(
    prompt: str,
    repo_path: str,
    model_tier: str = "medium",
    output_format: dict | None = None,
    api_key: str | None = None,
    deliverables_subdir: str | None = None,
    provider_config: dict | None = None,
) -> ClaudeRunResult:
    raise NotImplementedError(
        "Claude Agent SDK Python integration pending. "
        "Install claude-agent-sdk and implement this function."
    )
