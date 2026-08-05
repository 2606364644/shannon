"""Authentication validation — verifies user-supplied credentials via browser login."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from supernova_core.models.agents import AgentName
from supernova_core.utils.file_io import async_path_exists, async_read_file

if TYPE_CHECKING:
    from supernova_core.agents.executor import AgentExecutor
    from supernova_core.agents.tool_audit_logger import ToolAuditLogger
    from supernova_core.logging.activity_logger import ActivityLogger
    from supernova_core.prompts.manager import PromptManager


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
    """Verify the auth-state.json file was saved correctly.

    Not used for login-success arbitration since D3 (2026-08-05): the structured
    ``login_success`` verdict is trusted directly. Retained as a structural /
    diagnostic check over the saved storageState (cookies + localStorage) that the
    validate-authentication agent publishes for downstream exploit-agent reuse.
    """
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
    deliverables_path: str | None = None,
    api_key: str | None = None,
    audit_logger: "ActivityLogger | None" = None,
    tool_audit_logger: "ToolAuditLogger | None" = None,
) -> AuthValidationResult:
    """Validate user-supplied credentials by running the validate-authentication agent.

    Returns ``AuthValidationResult(success=True)`` when no auth config is present
    (nothing to validate) or when the agent confirms successful login.
    """
    # 1. Parse config and check for authentication
    if not config_path:
        return AuthValidationResult(success=True)

    try:
        from supernova_core.config.parser import parse_config, distribute_config
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
        deliverables_path=deliverables_path,
        config_path=config_path,
        api_key=api_key,
        prompt_override="validate-authentication",
        prompt_variables={"AUTH_STATE_FILE": str(state_file)},
        structured_output_schema=AUTH_VALIDATION_SCHEMA,
        audit_logger=audit_logger,
        tool_audit_logger=tool_audit_logger,
    )

    # 4. Trust the structured login_success verdict (D3, 2026-08-05).
    # The model's structured output is authoritative. We deliberately do NOT cross-check
    # auth-state.json cookies: cookie presence is a weak signal (CSRF / anonymous
    # session / rate-limit / bot cookies are set on the login page regardless of login
    # outcome), so a cookie "override" manufactured false positives (scanning while
    # unauthenticated → silent miss of every login-gated vuln). Trusting the field turns
    # the residual risk into visible false negatives (scan halts, retryable) instead.
    if metrics.structured_output is not None:
        verdict = metrics.structured_output
        if verdict.get("login_success"):
            return AuthValidationResult(success=True)
        return AuthValidationResult(
            success=False,
            failure_point=verdict.get("failure_point", "out_of_band"),
            failure_detail=verdict.get("failure_detail", "Login failed without diagnostic"),
        )

    # 5. No structured output → provider anomaly. Fail-fast rather than guessing via
    # cookies (rare; 0 occurrences across 72 probe runs on both engines).
    return AuthValidationResult(
        success=False,
        failure_point="out_of_band",
        failure_detail="Auth agent returned no structured login_success verdict",
    )
