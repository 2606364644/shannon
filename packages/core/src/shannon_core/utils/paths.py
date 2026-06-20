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
    """统一的 deliverables 路径解析（session 维度）。

    优先级：
    1. workspace_name → workspaces_root / workspace_name / deliverables_subdir
    2. repo_path（过渡兼容）→ repo_path / deliverables_subdir
    3. 都无 → raise ValueError

    deliverables 自 2026-06 起落在 session 下（workspaces/<session>/deliverables），
    不再写被扫仓库。repo_path 分支仅供迁移期调用方尚未提供 workspace_name 时兜底。
    """
    if workspace_name:
        ws_root = workspaces_root or resolve_workspaces_dir()
        return ws_root / workspace_name / deliverables_subdir

    if repo_path:
        return Path(repo_path) / deliverables_subdir

    raise ValueError("必须提供 workspace_name 或 repo_path 之一")


def deliverables_dir_for_workspace(workspace_path: Path) -> Path:
    """workspace 下的 deliverables 目录。

    deliverables 落在 session 下：workspaces/<session>/<subdir>。
    workspace_path 已是 workspaces/<session>，直接拼子目录。
    """
    return workspace_path / get_default_deliverables_subdir()


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
