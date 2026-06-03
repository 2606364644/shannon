"""Shared workspace discovery and query utilities for cross-scan UX."""

import json
from pathlib import Path
from urllib.parse import urlparse

from shannon_core.session import SessionManager


def normalize_url(url: str) -> str:
    """Normalize a URL for comparison: strip trailing slash, default ports, lowercase host.

    Note: Query strings and fragments are intentionally stripped since they are
    not relevant for workspace URL matching.
    """
    parsed = urlparse(url)

    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower() if parsed.hostname else ""
    port = parsed.port
    path = parsed.path.rstrip("/")

    # Strip default ports
    if (scheme == "https" and port == 443) or (scheme == "http" and port == 80):
        port = None

    # Reconstruct
    netloc = host
    if port:
        netloc = f"{host}:{port}"

    normalized = f"{scheme}://{netloc}{path}"
    return normalized


def urls_match(url_a: str, url_b: str) -> bool:
    """Check if two URLs refer to the same target (scheme-tolerant, path-prefix aware)."""
    a = urlparse(url_a)
    b = urlparse(url_b)

    # Hostname must match exactly
    host_a = (a.hostname or "").lower()
    host_b = (b.hostname or "").lower()
    if host_a != host_b:
        return False

    # Port comparison (scheme-tolerant: only compare when ports are explicit)
    port_a = a.port
    port_b = b.port
    # Normalize default ports for comparison
    if port_a and (a.scheme == "https" and port_a == 443 or a.scheme == "http" and port_a == 80):
        port_a = None
    if port_b and (b.scheme == "https" and port_b == 443 or b.scheme == "http" and port_b == 80):
        port_b = None
    if port_a is not None and port_b is not None and port_a != port_b:
        return False

    # Path prefix match
    path_a = (a.path or "/").rstrip("/") or "/"
    path_b = (b.path or "/").rstrip("/") or "/"

    # One path must be a prefix of the other
    return path_a.startswith(path_b) or path_b.startswith(path_a)


def _is_valid_queue_file(filepath: Path) -> bool:
    """Check that a file exists, is non-empty, and parses as valid JSON with vulnerabilities."""
    if not filepath.exists() or filepath.stat().st_size == 0:
        return False
    try:
        data = json.loads(filepath.read_text(encoding="utf-8"))
        vulns = data.get("vulnerabilities", [])
        return isinstance(vulns, list) and len(vulns) > 0
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False


def compute_deliverables_summary(workspace_path: Path) -> dict:
    """Scan the deliverables directory and return a summary of vuln queues and reports."""
    deliverables_dir = workspace_path / "deliverables"
    vuln_queues: list[str] = []
    reports: list[str] = []

    if not deliverables_dir.exists():
        return {"vuln_queues": vuln_queues, "reports": reports}

    # Check per-class exploitation queue files: {class}_exploitation_queue.json
    for f in sorted(deliverables_dir.iterdir()):
        if f.is_file() and f.name.endswith("_exploitation_queue.json"):
            vuln_class = f.name.replace("_exploitation_queue.json", "")
            if _is_valid_queue_file(f):
                vuln_queues.append(vuln_class)

    # Also check the generic exploitation_queue.json
    generic_queue = deliverables_dir / "exploitation_queue.json"
    if generic_queue.exists() and _is_valid_queue_file(generic_queue):
        if "" not in vuln_queues:
            vuln_queues.insert(0, "general")

    # Collect report files (*.md)
    for f in sorted(deliverables_dir.iterdir()):
        if f.is_file() and f.name.endswith(".md"):
            reports.append(f.name)

    return {"vuln_queues": vuln_queues, "reports": reports}


def find_latest_workspace(
    workspaces_dir: Path,
    scan_type: str = "whitebox",
    url: str | None = None,
) -> Path | None:
    """Find the most recent workspace matching scan_type with valid deliverables."""
    mgr = SessionManager(workspaces_dir)
    workspaces = mgr.list_workspaces()

    # If URL provided, prioritize URL matches first
    if url:
        url_matches = [ws for ws in workspaces if urls_match(mgr.get_web_url(ws) or "", url)]
        non_url = [ws for ws in workspaces if ws not in url_matches]
        workspaces = url_matches + non_url

    for ws in workspaces:
        if mgr.get_scan_type(ws) != scan_type:
            continue
        if mgr.get_status(ws) not in ("completed", "unknown"):
            continue
        summary = compute_deliverables_summary(ws)
        if summary["vuln_queues"]:
            return ws

    return None


def find_workspaces_by_url(
    workspaces_dir: Path,
    url: str,
    scan_type: str = "whitebox",
) -> list[tuple[Path, dict]]:
    """Find all workspaces matching a target URL with valid deliverables."""
    mgr = SessionManager(workspaces_dir)
    workspaces = mgr.list_workspaces()
    results = []

    for ws in workspaces:
        ws_url = mgr.get_web_url(ws)
        if not ws_url or not urls_match(ws_url, url):
            continue
        if mgr.get_scan_type(ws) != scan_type:
            continue
        summary = compute_deliverables_summary(ws)
        if summary["vuln_queues"]:
            results.append((ws, summary))

    return results


def get_workspace_info(workspace_path: Path) -> dict:
    """Compute full workspace info for display."""
    workspaces_dir = workspace_path.parent
    mgr = SessionManager(workspaces_dir)
    data = mgr.get_session_data(workspace_path)

    return {
        "name": workspace_path.name,
        "scan_type": mgr.get_scan_type(workspace_path),
        "status": mgr.get_status(workspace_path),
        "web_url": mgr.get_web_url(workspace_path),
        "repo_path": data.get("repo_path") or data.get("session", {}).get("repoPath"),
        "created_at": mgr.get_created_at(workspace_path),
        "completed_at": mgr.get_completed_at(workspace_path),
        "links": mgr.get_links(workspace_path),
        "deliverables_summary": compute_deliverables_summary(workspace_path),
    }
