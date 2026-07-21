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
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(ws))
    return ws


@pytest.fixture(autouse=True)
def _reset_config():
    """清 get_config lru_cache，使每个测试读到当前 env（tmp_workspaces 等）。"""
    from supernova_web import config as cfg_mod
    cfg_mod.get_config.cache_clear()
    yield
    cfg_mod.get_config.cache_clear()


@pytest.fixture
def app_with_ws(tmp_workspaces, monkeypatch):
    """create_app() 持单例，且让 get_config().workspaces_dir == tmp_workspaces。

    tmp_workspaces fixture 把 SUPERNOVA_WORKER_ROOT 设成 tmp_path/"workspaces"，
    而 resolve_workspaces_dir() 会再追加 /"workspaces" → 嵌套一层。此处把
    SUPERNOVA_WORKER_ROOT 改成父目录，使解析结果恰好等于 tmp_workspaces，
    这样测试在 tmp_workspaces 下直接建 ws 目录即可被 indexer 命中。
    """
    from supernova_core.utils.paths import resolve_workspaces_dir
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_workspaces.parent))
    assert resolve_workspaces_dir() == tmp_workspaces
    from supernova_web.app import create_app
    return create_app()
