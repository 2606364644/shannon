"""Tests for utils/paths.py workspace resolution."""
import os
from unittest.mock import patch
from pathlib import Path

from shannon_core.utils.paths import resolve_workspaces_dir


class TestResolveWorkspacesDir:
    def test_ignores_repo_parent(self, tmp_path):
        """repo_path 不再决定 workspace 根(不再落 repo 父目录)。"""
        repo = tmp_path / "NodeGoat"
        with patch.dict(os.environ, {}, clear=True):
            ws = resolve_workspaces_dir(repo_path=str(repo))
        # 不应是 <repo.parent>/workspaces
        assert ws != repo.parent / "workspaces"
        # 应是 find_project_root()/workspaces
        assert ws.name == "workspaces"

    def test_worker_root_env_overrides(self, tmp_path):
        with patch.dict(os.environ, {"SHANNON_WORKER_ROOT": str(tmp_path)}, clear=True):
            ws = resolve_workspaces_dir(repo_path="/some/repo")
        assert ws == tmp_path / "workspaces"

    def test_no_repo_no_env_uses_project_root(self):
        with patch.dict(os.environ, {}, clear=True):
            ws = resolve_workspaces_dir()
        assert ws.name == "workspaces"
