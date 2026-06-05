import json
from pathlib import Path


def find_project_root() -> Path:
    """Walk up from CWD to find project root (directory with .git or pyproject.toml)."""
    current = Path.cwd()
    for parent in [current, *current.parents]:
        if (parent / ".git").exists() or (parent / "pyproject.toml").exists():
            return parent
    return current


def resolve_workspaces_dir(repo_path: str | None = None) -> Path:
    """解析 workspaces 根目录。

    如果提供 repo_path，使用 repo_path.parent / "workspaces"；
    否则使用 find_project_root() / "workspaces"。
    """
    if repo_path:
        return Path(repo_path).parent / "workspaces"
    return find_project_root() / "workspaces"


def resolve_deliverables_path(
    repo_path: str | None,
    deliverables_subdir: str,
    workspace_name: str | None = None,
    workspaces_root: Path | None = None,
) -> Path:
    """统一的 deliverables 路径解析。

    优先级：
    1. repo_path 存在 → repo_path / deliverables_subdir
    2. workspace_name 存在 → 从 session.json 恢复 repo_path → repo_path / deliverables_subdir
    3. fallback → workspaces_root / workspace_name / deliverables_subdir
    """
    if repo_path:
        return Path(repo_path) / deliverables_subdir

    if workspace_name:
        ws_root = workspaces_root or resolve_workspaces_dir()
        session_file = ws_root / workspace_name / "session.json"
        if session_file.exists():
            try:
                session_data = json.loads(session_file.read_text(encoding="utf-8"))
                saved_repo = session_data.get("repo_path")
                if saved_repo:
                    return Path(saved_repo) / deliverables_subdir
            except (json.JSONDecodeError, OSError):
                pass
        return ws_root / workspace_name / deliverables_subdir

    raise ValueError("必须提供 repo_path 或 workspace_name 之一")


def has_valid_whitebox_results(queue_file: Path) -> bool:
    """检查 exploitation queue 文件是否包含有效漏洞条目。"""
    if not queue_file.exists():
        return False
    try:
        data = json.loads(queue_file.read_text(encoding="utf-8"))
        return isinstance(data.get("vulnerabilities"), list) and len(data["vulnerabilities"]) > 0
    except (json.JSONDecodeError, KeyError, OSError):
        return False
