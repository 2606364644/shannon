from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from shannon_core.models.multi_repo_config import MultiRepoConfig


@dataclass
class RepoScanPlan:
    service: str
    repo_path: str | None
    workspace: str | None
    reuse: bool
    scan_config: str | None


def plan_repo_scans(config: MultiRepoConfig) -> list[RepoScanPlan]:
    """纯函数:决定每个 repo 复用已有 workspace 还是现扫。
    复用条件:声明了 workspace(交付物完整性由编排器后续检查)。
    否则需要 path → 现扫。
    """
    plans: list[RepoScanPlan] = []
    for service, spec in config.repos.items():
        if spec.workspace:
            plans.append(RepoScanPlan(service=service, repo_path=spec.path,
                                      workspace=spec.workspace, reuse=True,
                                      scan_config=spec.scan_config))
        else:
            plans.append(RepoScanPlan(service=service, repo_path=spec.path,
                                      workspace=None, reuse=False,
                                      scan_config=spec.scan_config))
    return plans


async def run_cross_repo(config_path: Path, temporal_address: str, *, pipeline_testing: bool = False) -> dict:
    raise NotImplementedError  # Task A6
