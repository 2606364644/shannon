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


@dataclass
class IdentityRecord:
    account_id: str            # "primary" 或 account.id
    role: str | None
    tier: str                  # "high" | "low"
    auth_state_file: str       # "auth-state.json" | "auth-state-{id}.json"
    available: bool
    failure_detail: str | None = None


@dataclass
class IdentityManifest:
    identities: list[IdentityRecord]

    def write(self, workspace_path: str | Path) -> Path:
        p = Path(workspace_path) / "identity-manifest.json"
        p.write_text(json.dumps({
            "identities": [r.__dict__ for r in self.identities]
        }, ensure_ascii=False), encoding="utf-8")
        return p


def load_identity_manifest(workspace_path: str | Path) -> IdentityManifest | None:
    p = Path(workspace_path) / "identity-manifest.json"
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    return IdentityManifest(identities=[IdentityRecord(**d) for d in data.get("identities", [])])


def auth_state_path(workspace_path: str | Path, account_id: str | None = None) -> Path:
    name = "auth-state.json" if account_id is None else f"auth-state-{account_id}.json"
    return Path(workspace_path) / name


async def cleanup_auth_state(workspace_path: str | Path) -> None:
    import glob as _glob
    import aiofiles.os
    for f in _glob.glob(str(Path(workspace_path) / "auth-state*.json")):
        await aiofiles.os.remove(f)


def cleanup_auth_state_sync(workspace_path: str | Path) -> None:
    """Synchronous version of cleanup_auth_state for use in workflow finally blocks."""
    import glob as _glob
    for f in _glob.glob(str(Path(workspace_path) / "auth-state*.json")):
        Path(f).unlink(missing_ok=True)


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


def _build_validate_auth_executor_kwargs(
    *,
    web_url: str,
    config_path: str | None,
    deliverables_path: str | None,
    api_key: str | None,
    repo_path: str,
    state_file: Path,
    audit_logger: "ActivityLogger | None",
    tool_audit_logger: "ToolAuditLogger | None",
) -> dict:
    """Build the kwargs dict for one validate-authentication executor.execute call.

    Shared between the byte-identical single-identity path (no accounts) and the
    multi-identity loop so the per-call contract stays identical.
    """
    return dict(
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

    子项目2 T4: 当 ``dist_config.accounts`` 非空时，循环 primary + 每个 account 各登录
    一次，落 ``identity-manifest.json``；primary 失败 fail-fast，非 primary 失败仅记
    ``available=False`` 不拖垮整体。无 accounts 时走原单次登录路径，**byte-identical**、
    不落 manifest。
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

    accounts = dist_config.accounts or []

    # 2. Delete stale auth-state file(s) from prior run
    await cleanup_auth_state(workspace_path)

    # ── Branch A: 无 accounts → 原 byte-identical 单次登录路径，不落 manifest ──
    if not accounts:
        state_file = auth_state_path(workspace_path)

        # 3. Execute validate-authentication agent with structured output schema
        metrics = await executor.execute(
            **_build_validate_auth_executor_kwargs(
                web_url=web_url, config_path=config_path,
                deliverables_path=deliverables_path, api_key=api_key,
                repo_path=repo_path, state_file=state_file,
                audit_logger=audit_logger, tool_audit_logger=tool_audit_logger,
            )
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

    # ── Branch B: 多身份 preflight 登录循环 + identity-manifest.json ──
    # 身份列表：(account_id, role, tier)。primary 继承 authentication（无 role），
    # tier 占位 "low"（实际 tier 由 build_comparison_matrix 时按 role 推导）。
    id_list: list[tuple[str, str | None, str | None]] = [("primary", None, None)]
    for acct in accounts:
        id_list.append((acct.id, acct.role, acct.tier))

    identities: list[IdentityRecord] = []
    primary_failed = False
    primary_failure_point: str = "out_of_band"
    primary_failure_detail: str = "primary attacker login failed"

    for acct_id, role, tier in id_list:
        state_file = auth_state_path(
            workspace_path, None if acct_id == "primary" else acct_id
        )
        try:
            metrics = await executor.execute(
                **_build_validate_auth_executor_kwargs(
                    web_url=web_url, config_path=config_path,
                    deliverables_path=deliverables_path, api_key=api_key,
                    repo_path=repo_path, state_file=state_file,
                    audit_logger=audit_logger, tool_audit_logger=tool_audit_logger,
                )
            )
            so = (metrics.structured_output or {}) if metrics and metrics.structured_output is not None else {}
            ok = bool(so.get("login_success"))
        except Exception:
            ok = False
            so = {}

        rec = IdentityRecord(
            account_id=acct_id, role=role, tier=tier or "low",
            auth_state_file=state_file.name, available=ok,
            failure_detail=None if ok else (so.get("failure_detail") or "login failed"),
        )
        identities.append(rec)

        if not ok and acct_id == "primary":
            primary_failed = True
            if so:
                primary_failure_point = so.get("failure_point", "out_of_band")
                primary_failure_detail = so.get("failure_detail", "primary attacker login failed")
            break  # attacker 必须，fail-fast

    IdentityManifest(identities=identities).write(workspace_path)

    if primary_failed:
        return AuthValidationResult(
            success=False,
            failure_point=primary_failure_point,
            failure_detail=primary_failure_detail,
        )
    return AuthValidationResult(success=True)
