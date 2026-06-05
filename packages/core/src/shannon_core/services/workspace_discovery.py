"""Unified workspace discovery service for cross-scan UX."""

from dataclasses import dataclass, field
from pathlib import Path

from shannon_core.session import SessionManager
from shannon_core.workspace import (
    compute_deliverables_summary,
    find_latest_workspace,
    get_workspace_age_human,
    get_workspace_vuln_counts,
    urls_match,
)


@dataclass
class WorkspaceSummary:
    """Summary of a workspace for display in discovery UI."""
    name: str
    path: Path
    web_url: str | None = None
    age_human: str = ""
    vuln_counts: dict[str, int] = field(default_factory=dict)
    vuln_queues: list[str] = field(default_factory=list)


@dataclass
class DiscoveryResult:
    """Result of workspace discovery for blackbox."""
    workspace_path: Path | None = None
    workspace_name: str | None = None
    summary: WorkspaceSummary | None = None
    message: str = ""


@dataclass
class ValidationResult:
    """Result of workspace validation."""
    valid: bool = False
    errors: list[str] = field(default_factory=list)


class WorkspaceDiscovery:
    """Unified entry point for workspace discovery."""

    def __init__(self, workspaces_dir: Path | None = None):
        self.workspaces_dir = workspaces_dir or Path("workspaces")

    def find_for_blackbox(
        self,
        url: str,
        *,
        latest: bool = False,
        workspace_name: str | None = None,
    ) -> DiscoveryResult:
        """Find a workspace for blackbox consumption."""
        if workspace_name:
            ws = self.workspaces_dir / workspace_name
            if ws.exists():
                return DiscoveryResult(
                    workspace_path=ws,
                    workspace_name=workspace_name,
                    summary=self._build_summary(ws),
                )
            return DiscoveryResult(message=f"Workspace '{workspace_name}' not found.")

        if latest:
            ws = find_latest_workspace(self.workspaces_dir, scan_type="whitebox", url=url)
            if ws:
                # Ensure the returned workspace actually matches the requested URL
                mgr = SessionManager(self.workspaces_dir)
                ws_url = mgr.get_web_url(ws)
                if ws_url and not urls_match(ws_url, url):
                    return DiscoveryResult(message="No matching white-box workspace found.")
                return DiscoveryResult(
                    workspace_path=ws,
                    workspace_name=ws.name,
                    summary=self._build_summary(ws),
                )
            return DiscoveryResult(message="No matching white-box workspace found.")

        return DiscoveryResult(message="Specify --latest or -w to select a workspace.")

    def list_whitebox_workspaces(self, url: str | None = None) -> list[WorkspaceSummary]:
        """List all available whitebox workspaces, optionally filtered by URL."""
        mgr = SessionManager(self.workspaces_dir)
        workspaces = mgr.list_workspaces()

        results = []
        for ws in workspaces:
            if mgr.get_scan_type(ws) != "whitebox":
                continue
            if url:
                ws_url = mgr.get_web_url(ws)
                if not ws_url or not urls_match(ws_url, url):
                    continue
            results.append(self._build_summary(ws))

        return results

    def validate_for_consumption(self, workspace_path: Path) -> ValidationResult:
        """Validate that a workspace is consumable by blackbox."""
        errors = []

        if not workspace_path.exists():
            errors.append(f"Workspace path does not exist: {workspace_path}")
            return ValidationResult(valid=False, errors=errors)

        session_file = workspace_path / "session.json"
        if not session_file.exists():
            errors.append("Missing session.json")

        summary = compute_deliverables_summary(workspace_path)
        if not summary["vuln_queues"]:
            errors.append("No valid deliverables found")

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
        )

    def _build_summary(self, workspace_path: Path) -> WorkspaceSummary:
        """Build a WorkspaceSummary for a workspace."""
        mgr = SessionManager(self.workspaces_dir)
        summary = compute_deliverables_summary(workspace_path)

        return WorkspaceSummary(
            name=workspace_path.name,
            path=workspace_path,
            web_url=mgr.get_web_url(workspace_path),
            age_human=get_workspace_age_human(workspace_path),
            vuln_counts=get_workspace_vuln_counts(workspace_path),
            vuln_queues=summary["vuln_queues"],
        )
