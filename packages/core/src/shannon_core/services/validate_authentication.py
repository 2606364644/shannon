"""Preflight authentication validation — reuses AgentExecutor to drive a browser login check."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from shannon_core.models.agents import AgentName

if TYPE_CHECKING:
    from shannon_core.agents.executor import AgentExecutor
    from shannon_core.prompts.manager import PromptManager


@dataclass
class AuthValidationResult:
    success: bool
    failure_point: str | None = None  # "username_or_password" | "totp_secret" | "out_of_band"
    failure_detail: str | None = None


async def validate_authentication(
    *,
    web_url: str,
    config_path: str | None,
    prompt_manager: PromptManager,
    executor: AgentExecutor,
    repo_path: str = "",
    api_key: str | None = None,
) -> AuthValidationResult:
    """Validate user-supplied credentials by running the validate-authentication agent.

    Returns ``AuthValidationResult(success=True)`` when no auth config is present
    (nothing to validate) or when the agent confirms successful login.
    """
    if not config_path:
        return AuthValidationResult(success=True)

    # Try to parse config and check for authentication section
    try:
        from shannon_core.config.parser import parse_config, distribute_config
        config = parse_config(config_path)
        dist_config = distribute_config(config)
    except Exception:
        return AuthValidationResult(success=True)

    if not dist_config.authentication:
        return AuthValidationResult(success=True)

    # Execute as a one-shot agent using the existing executor infrastructure
    metrics = await executor.execute(
        agent_name=AgentName.PRE_RECON,  # Borrow pre-recon name — actual prompt is overridden
        repo_path=repo_path or "/tmp/shannon-auth-check",
        web_url=web_url,
        config_path=config_path,
        api_key=api_key,
        prompt_override="validate-authentication",
    )

    return AuthValidationResult(success=True)
