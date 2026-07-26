from __future__ import annotations

import os
import shutil
from functools import lru_cache
from pathlib import Path


class WebConfig:
    def __init__(self) -> None:
        self.port = int(os.environ.get("SUPERNOVA_WEB_PORT", "7878"))
        # P3c 阶段 3：与 worker SUPERNOVA_WORKER_MAX_CONCURRENT_WF 建议同值（避免 pending 堆积）。
        self.max_concurrent = max(1, int(os.environ.get("SUPERNOVA_WEB_MAX_CONCURRENT", "4")))
        self.scan_timeout = float(os.environ.get("SUPERNOVA_WEB_SCAN_TIMEOUT", "0"))
        self.gitlab_user = os.environ.get("GITLAB_USER")
        self.gitlab_token = os.environ.get("GITLAB_TOKEN")
        self.repos_dir = Path(os.environ.get("SUPERNOVA_REPOS_DIR", "repos"))
        self.repos_max_concurrent_clones = max(
            1, int(os.environ.get("SUPERNOVA_REPOS_MAX_CONCURRENT_CLONES", "3"))
        )
        self.configs_dir = Path(os.environ.get("SUPERNOVA_CONFIGS_DIR", "configs"))
        self.frontend_dir = os.environ.get("SUPERNOVA_WEB_FRONTEND_DIR")
        # Web 控制台品牌名(左上角字标 + 浏览器标签页 title);默认 Supernova,部署者可经
        # SUPERNOVA_WEB_BRAND_NAME 覆盖(white-label / 改名场景,无需改代码)。
        self.brand_name = os.environ.get("SUPERNOVA_WEB_BRAND_NAME", "Supernova")
        self.fs_roots: list[Path] = [
            Path(p).resolve() for p in os.environ.get("SUPERNOVA_FS_ROOTS", "").split(",") if p.strip()
        ]
        # auth（P0）
        self.session_ttl_hours = int(os.environ.get("SUPERNOVA_WEB_SESSION_TTL_HOURS", "12"))
        self.cookie_secure = os.environ.get("SUPERNOVA_WEB_COOKIE_SECURE", "1") not in ("0", "false", "False")
        self.users_seed_file = os.environ.get("SUPERNOVA_WEB_USERS_SEED", "configs/users.yaml")

    @property
    def workspaces_dir(self) -> Path:
        from supernova_core.utils.paths import resolve_workspaces_dir
        return Path(resolve_workspaces_dir())

    @property
    def master_key_file(self) -> Path:
        """P3c 阶段 2：凭据 master key 落盘路径（env SUPERNOVA_MASTER_KEY 优先于该文件）。"""
        return self.workspaces_dir / ".master_key"

    @property
    def auth_db_path(self) -> Path:
        return self.workspaces_dir / "auth.db"

    @property
    def git_binary_available(self) -> bool:
        return shutil.which("git") is not None


@lru_cache
def get_config() -> WebConfig:
    return WebConfig()
