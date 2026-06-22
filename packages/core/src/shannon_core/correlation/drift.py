from dataclasses import dataclass


@dataclass
class DriftReport:
    drifted: bool
    note: str


def detect_drift(workspace_created_at: float, repo_mtime: float) -> DriftReport:
    """A2: session.json 不存 git commit,用时间戳粗判。
    repo 最近改动 > workspace 创建 → 可能漂移。警告不阻断。
    """
    if repo_mtime > workspace_created_at:
        return DriftReport(drifted=True,
                           note="复用产物,源码版本可能漂移(repo 在扫描后改动),请人工确认")
    return DriftReport(drifted=False, note="")
