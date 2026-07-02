import os
import sys
from pathlib import Path

import pytest

# 确保 src 在 path（开发期非 wheel 安装）
_ROOT = Path(__file__).resolve().parents[3]
for member in ("src",):
    p = _ROOT / "packages" / "web" / member
    if p.is_dir():
        sys.path.insert(0, str(p))


@pytest.fixture
def tmp_workspaces(tmp_path, monkeypatch):
    ws = tmp_path / "workspaces"
    ws.mkdir()
    monkeypatch.setenv("SHANNON_WORKER_ROOT", str(ws))
    return ws
