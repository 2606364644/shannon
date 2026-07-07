from __future__ import annotations

import os
import shutil
from functools import lru_cache
from pathlib import Path


class WebConfig:
    def __init__(self) -> None:
        self.port = int(os.environ.get("SHANNON_WEB_PORT", "7878"))
        self.max_concurrent = max(1, int(os.environ.get("SHANNON_WEB_MAX_CONCURRENT", "1")))
        self.scan_timeout = float(os.environ.get("SHANNON_WEB_SCAN_TIMEOUT", "0"))
        self.gitlab_user = os.environ.get("GITLAB_USER")
        self.gitlab_token = os.environ.get("GITLAB_TOKEN")
        self.repos_dir = Path(os.environ.get("SHANNON_REPOS_DIR", "repos"))
        self.repos_max_concurrent_clones = max(
            1, int(os.environ.get("SHANNON_REPOS_MAX_CONCURRENT_CLONES", "3"))
        )
        self.configs_dir = Path(os.environ.get("SHANNON_CONFIGS_DIR", "configs"))
        self.frontend_dir = os.environ.get("SHANNON_WEB_FRONTEND_DIR")
        self.fs_roots: list[Path] = [
            Path(p).resolve() for p in os.environ.get("SHANNON_FS_ROOTS", "").split(",") if p.strip()
        ]

    @property
    def workspaces_dir(self) -> Path:
        from shannon_core.utils.paths import resolve_workspaces_dir
        return Path(resolve_workspaces_dir())

    @property
    def git_binary_available(self) -> bool:
        return shutil.which("git") is not None


@lru_cache
def get_config() -> WebConfig:
    return WebConfig()
