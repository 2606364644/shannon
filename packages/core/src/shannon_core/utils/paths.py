import json
import os
from pathlib import Path

from shannon_core.constants import DEFAULT_DELIVERABLES_SUBDIR


def get_default_deliverables_subdir() -> str:
    """从环境变量获取默认产出物子目录。

    优先读取 SHANNON_DELIVERABLES_SUBDIR，未设置时返回 DEFAULT_DELIVERABLES_SUBDIR。
    """
    return os.getenv("SHANNON_DELIVERABLES_SUBDIR", DEFAULT_DELIVERABLES_SUBDIR)


def find_project_root() -> Path:
    """Walk up from CWD to find project root (directory with .git or pyproject.toml)."""
    current = Path.cwd()
    for parent in [current, *current.parents]:
        if (parent / ".git").exists() or (parent / "pyproject.toml").exists():
            return parent
    return current


def resolve_workspaces_dir(repo_path: str | None = None) -> Path:
    """解析 workspaces 根目录。

    优先级:
    1. SHANNON_WORKER_ROOT 环境变量 → worker_root / "workspaces"
    2. find_project_root() / "workspaces"  (shannon-py 项目根)

    注意: repo_path 不再用于定位 workspace 根(曾导致 workspace 落到 repo 父目录)。
    参数保留仅为调用方签名兼容;deliverables 仍落在 repo_path/.shannon/deliverables。
    """
    worker_root = os.getenv("SHANNON_WORKER_ROOT")
    if worker_root:
        return Path(worker_root) / "workspaces"
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


def deliverables_dir_for_workspace(workspace_path: Path) -> Path:
    """从 workspace 的 session.json 恢复 repo_path，解析真实 deliverables 目录。

    与写入侧（resolve_deliverables_path(repo_path=...)）保持一致：白盒/黑盒
    session 记录了 repo_path → 返回 ``<repo>/<deliverables_subdir>``；session 无
    repo_path 或无 session.json → fallback 到 ``workspaces/<name>/<subdir>``。
    供消费侧（compute_deliverables_summary 等）统一解析，避免再硬拼 workspace 目录。
    """
    return resolve_deliverables_path(
        repo_path=None,
        deliverables_subdir=get_default_deliverables_subdir(),
        workspace_name=workspace_path.name,
        workspaces_root=workspace_path.parent,
    )


REQUIRED_VULN_FIELDS = {"title", "description", "severity", "location"}


def has_valid_whitebox_results(queue_file: Path) -> bool:
    """检查 exploitation queue 文件是否包含有效漏洞条目。

    验证 vulnerabilities 列表中的每个条目都包含必需字段：
    title, description, severity, location。
    """
    if not queue_file.exists():
        return False
    try:
        data = json.loads(queue_file.read_text(encoding="utf-8"))
        vulns = data.get("vulnerabilities")
        if not isinstance(vulns, list) or len(vulns) == 0:
            return False
        for v in vulns:
            if not isinstance(v, dict):
                return False
            if not REQUIRED_VULN_FIELDS.issubset(v.keys()):
                return False
        return True
    except (json.JSONDecodeError, KeyError, OSError):
        return False
