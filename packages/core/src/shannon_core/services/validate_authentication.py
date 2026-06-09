"""Authentication validation — verifies user-supplied credentials via browser login."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from shannon_core.models.agents import AgentName
from shannon_core.utils.file_io import async_path_exists, async_read_file

if TYPE_CHECKING:
    from shannon_core.agents.executor import AgentExecutor
    from shannon_core.logging.activity_logger import ActivityLogger
    from shannon_core.prompts.manager import PromptManager


# Schema for structured output from the validate-authentication agent
AUTH_VALIDATION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "login_success": {"type": "boolean"},
        "failure_point": {
            "type": "string",
            "enum": ["username_or_password", "totp_secret", "out_of_band"],
        },
        "failure_detail": {"type": "string", "maxLength": 250},
    },
    "required": ["login_success"],
}


@dataclass
class AuthValidationResult:
    success: bool
    failure_point: str | None = None  # "username_or_password" | "totp_secret" | "out_of_band"
    failure_detail: str | None = None


def auth_state_path(workspace_path: str | Path) -> Path:
    return Path(workspace_path) / "auth-state.json"


async def cleanup_auth_state(workspace_path: str | Path) -> None:
    state_file = auth_state_path(workspace_path)
    if await async_path_exists(state_file):
        import aiofiles.os
        await aiofiles.os.remove(state_file)


def cleanup_auth_state_sync(workspace_path: str | Path) -> None:
    """Synchronous version of cleanup_auth_state for use in workflow finally blocks."""
    state_file = auth_state_path(workspace_path)
    if state_file.exists():
        state_file.unlink()


async def verify_auth_state(state_file: Path) -> AuthValidationResult:
    """Verify the auth-state.json file was saved correctly."""
    if not await async_path_exists(state_file):
        return AuthValidationResult(
            success=False,
            failure_point="out_of_band",
            failure_detail=f"Agent did not save auth state to {state_file}",
        )

    contents = await async_read_file(state_file)
    try:
        parsed = json.loads(contents)
    except json.JSONDecodeError as e:
        return AuthValidationResult(
            success=False,
            failure_point="out_of_band",
            failure_detail=f"Auth state file is not valid JSON: {e}",
        )

    cookie_count = len(parsed.get("cookies", []))
    origin_count = len(parsed.get("origins", []))
    if cookie_count == 0 and origin_count == 0:
        return AuthValidationResult(
            success=False,
            failure_point="out_of_band",
            failure_detail="Auth state contains no cookies or origins — browser was not actually logged in",
        )

    return AuthValidationResult(success=True)


async def validate_authentication(
    *,
    web_url: str,
    config_path: str | None,
    workspace_path: str,
    prompt_manager: PromptManager,
    executor: AgentExecutor,
    repo_path: str = "",
    api_key: str | None = None,
    audit_logger: "ActivityLogger | None" = None,
) -> AuthValidationResult:
    """Validate user-supplied credentials by running the validate-authentication agent.

    Returns ``AuthValidationResult(success=True)`` when no auth config is present
    (nothing to validate) or when the agent confirms successful login.
    """
    # 1. Parse config and check for authentication
    if not config_path:
        return AuthValidationResult(success=True)

    try:
        from shannon_core.config.parser import parse_config, distribute_config
        config = parse_config(config_path)
        dist_config = distribute_config(config)
    except Exception:
        return AuthValidationResult(success=True)

    if not dist_config.authentication:
        return AuthValidationResult(success=True)

    # 2. Delete stale auth-state file from prior run
    state_file = auth_state_path(workspace_path)
    await cleanup_auth_state(workspace_path)

    # 3. Execute validate-authentication agent with structured output schema
    metrics = await executor.execute(
        agent_name=AgentName.VALIDATE_AUTH,
        repo_path=repo_path or "/tmp/shannon-auth-check",
        web_url=web_url,
        config_path=config_path,
        api_key=api_key,
        prompt_override="validate-authentication",
        prompt_variables={"AUTH_STATE_FILE": str(state_file)},
        structured_output_schema=AUTH_VALIDATION_SCHEMA,
        audit_logger=audit_logger,
    )

    # 4. Classify structured output
    if metrics.structured_output is not None:
        verdict = metrics.structured_output
        if verdict.get("login_success"):
            return await verify_auth_state(state_file)
        else:
            failure_point = verdict.get("failure_point", "out_of_band")
            failure_detail = verdict.get("failure_detail", "Login failed without diagnostic")
            return AuthValidationResult(
                success=False,
                failure_point=failure_point,
                failure_detail=failure_detail,
            )

    # 5. Fallback: if no structured output, rely on auth-state verification
    return await verify_auth_state(state_file)
