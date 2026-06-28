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
    参数保留仅为调用方签名兼容;deliverables 落在 workspaces/<session>/deliverables。

    污染防御: find_project_root 是 cwd-based,若用户 cd 进被扫 repo 再跑,根会落到
    <repo>/workspaces 而污染被扫 repo。此时 fallback 到 repo_path.parent/workspaces
    (repo 同级)—— 宁可落 repo 父目录也不污染 repo 本身(项目核心目标)。
    """
    worker_root = os.getenv("SHANNON_WORKER_ROOT")
    if worker_root:
        return Path(worker_root) / "workspaces"
    root = find_project_root() / "workspaces"
    if repo_path:
        try:
            root.resolve().relative_to(Path(repo_path).resolve())
            # cwd-based 根落在被扫 repo 内 → 污染 repo,fallback 到 repo 同级
            return Path(repo_path).resolve().parent / "workspaces"
        except ValueError:
            pass
    return root


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


def has_valid_whitebox_results(queue_file: Path) -> bool:
    """检查 exploitation queue 是否含有效漏洞条目(对齐原始 TS validateQueueStructure)。

    原始 shannon 全链路只校验:文件存在 + ``vulnerabilities`` 是非空数组;**不校验条目
    内部字段**——``title``/``description``/``severity``/``location`` 是 exploit 阶段
    (exploit-collector)的字段,并非 vuln 分析阶段 queue 的字段。重构早期误把这套
    exploit 字段当成 vuln queue 的必填校验,导致即便 queue 正常落盘(字段实为
    ``ID``/``vulnerability_type``/``source``/``path``/``verdict``/...)也判 False,
    黑盒 preflight 永远报 "No whitebox results found"。此处对齐 TS:条目级容错
    交给 ``VulnerabilityQueue.parse_lenient``。
    """
    if not queue_file.exists():
        return False
    try:
        data = json.loads(queue_file.read_text(encoding="utf-8"))
        vulns = data.get("vulnerabilities")
        return isinstance(vulns, list) and len(vulns) > 0
    except (json.JSONDecodeError, KeyError, OSError):
        return False
