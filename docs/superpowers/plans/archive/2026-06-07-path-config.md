# Path Config (环境变量路径配置) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `SHANNON_DELIVERABLES_SUBDIR` 和 `SHANNON_WORKER_ROOT` 环境变量，将路径配置暴露到 `.env` 层面。

**Architecture:** 在 `paths.py` 中新增两个环境变量感知函数（`get_default_deliverables_subdir`、增强版 `resolve_workspaces_dir`），然后让 `BasePipelineInput` 的 `deliverables_subdir` 字段通过 `field(default_factory=...)` 在实例化时读取环境变量。最后在 `.env.example` 中记录新增的环境变量。

**Tech Stack:** Python 3.12+, dataclasses, pytest, os.getenv

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `packages/core/src/shannon_core/utils/paths.py` | Modify | 新增 `get_default_deliverables_subdir()`；增强 `resolve_workspaces_dir()` 感知 `SHANNON_WORKER_ROOT` |
| `packages/core/src/shannon_core/models/base.py` | Modify | `deliverables_subdir` 改用 `field(default_factory=get_default_deliverables_subdir)` |
| `packages/core/tests/test_paths.py` | Modify | 新增环境变量相关测试 |
| `packages/core/tests/test_base_model.py` | Modify | 新增 default_factory 环境变量测试 |
| `.env.example` | Modify | 新增路径配置部分 |

---

### Task 1: Add `get_default_deliverables_subdir()` to paths.py

**Files:**
- Modify: `packages/core/src/shannon_core/utils/paths.py`
- Modify: `packages/core/tests/test_paths.py`

- [x] **Step 1: Write the failing test**

Add the following imports and test class to `packages/core/tests/test_paths.py`.

At the top of the file, add `get_default_deliverables_subdir` to the existing import:

```python
from shannon_core.utils.paths import resolve_workspaces_dir, resolve_deliverables_path, has_valid_whitebox_results, get_default_deliverables_subdir
```

Add the following test class at the end of the file (after `TestHasValidWhiteboxResults`):

```python
class TestGetDefaultDeliverablesSubdir:
    def test_returns_constant_when_no_env(self, monkeypatch):
        """When SHANNON_DELIVERABLES_SUBDIR is not set, returns the default constant."""
        from shannon_core.constants import DEFAULT_DELIVERABLES_SUBDIR
        monkeypatch.delenv("SHANNON_DELIVERABLES_SUBDIR", raising=False)
        assert get_default_deliverables_subdir() == DEFAULT_DELIVERABLES_SUBDIR

    def test_returns_env_value_when_set(self, monkeypatch):
        """When SHANNON_DELIVERABLES_SUBDIR is set, returns its value."""
        monkeypatch.setenv("SHANNON_DELIVERABLES_SUBDIR", "custom/output")
        assert get_default_deliverables_subdir() == "custom/output"

    def test_returns_empty_string_when_env_empty(self, monkeypatch):
        """When SHANNON_DELIVERABLES_SUBDIR is set to empty string, returns empty string."""
        monkeypatch.setenv("SHANNON_DELIVERABLES_SUBDIR", "")
        assert get_default_deliverables_subdir() == ""
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest packages/core/tests/test_paths.py::TestGetDefaultDeliverablesSubdir -v`
Expected: FAIL — `ImportError: cannot import name 'get_default_deliverables_subdir'`

- [x] **Step 3: Write minimal implementation**

Add `import os` at the top of `packages/core/src/shannon_core/utils/paths.py` (line 1, before existing imports) and add the following function after the existing imports (after `from pathlib import Path`):

```python
import os

from shannon_core.constants import DEFAULT_DELIVERABLES_SUBDIR


def get_default_deliverables_subdir() -> str:
    """从环境变量获取默认产出物子目录。

    优先读取 SHANNON_DELIVERABLES_SUBDIR，未设置时返回 DEFAULT_DELIVERABLES_SUBDIR。
    """
    return os.getenv("SHANNON_DELIVERABLES_SUBDIR", DEFAULT_DELIVERABLES_SUBDIR)
```

The full top of `paths.py` should now look like:

```python
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
    ...
```

- [x] **Step 4: Run test to verify it passes**

Run: `python -m pytest packages/core/tests/test_paths.py::TestGetDefaultDeliverablesSubdir -v`
Expected: PASS (3 tests)

- [x] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/utils/paths.py packages/core/tests/test_paths.py
git commit -m "feat(paths): add get_default_deliverables_subdir with env var support"
```

---

### Task 2: Enhance `resolve_workspaces_dir()` with `SHANNON_WORKER_ROOT`

**Files:**
- Modify: `packages/core/src/shannon_core/utils/paths.py`
- Modify: `packages/core/tests/test_paths.py`

- [x] **Step 1: Write the failing test**

Add the following tests to `TestResolveWorkspacesDir` class in `packages/core/tests/test_paths.py`:

```python
    def test_with_worker_root_env(self, tmp_path, monkeypatch):
        """When SHANNON_WORKER_ROOT is set, returns worker_root / workspaces."""
        worker_root = tmp_path / "shannon-worker"
        worker_root.mkdir()
        monkeypatch.setenv("SHANNON_WORKER_ROOT", str(worker_root))
        result = resolve_workspaces_dir()
        assert result == worker_root / "workspaces"

    def test_worker_root_env_ignored_when_repo_path_given(self, monkeypatch):
        """When repo_path is provided, SHANNON_WORKER_ROOT is ignored."""
        monkeypatch.setenv("SHANNON_WORKER_ROOT", "/custom/worker/root")
        result = resolve_workspaces_dir("/data/repos/myrepo")
        assert result == Path("/data/repos/workspaces")

    def test_worker_root_fallback_without_repo_path(self, tmp_path, monkeypatch):
        """When no repo_path and no SHANNON_WORKER_ROOT, uses project_root."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        (project_root / ".git").mkdir()
        monkeypatch.chdir(project_root)
        monkeypatch.delenv("SHANNON_WORKER_ROOT", raising=False)
        result = resolve_workspaces_dir()
        assert result == project_root / "workspaces"
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest packages/core/tests/test_paths.py::TestResolveWorkspacesDir::test_with_worker_root_env -v`
Expected: FAIL — the test expects `/tmp/.../shannon-worker/workspaces` but gets `project_root/workspaces`

- [x] **Step 3: Write minimal implementation**

Replace `resolve_workspaces_dir()` in `packages/core/src/shannon_core/utils/paths.py` with:

```python
def resolve_workspaces_dir(repo_path: str | None = None) -> Path:
    """解析 workspaces 根目录。

    优先级：
    1. repo_path 存在 → repo_path.parent / "workspaces"
    2. SHANNON_WORKER_ROOT 环境变量 → worker_root / "workspaces"
    3. find_project_root() / "workspaces"
    """
    if repo_path:
        return Path(repo_path).parent / "workspaces"
    worker_root = os.getenv("SHANNON_WORKER_ROOT")
    if worker_root:
        return Path(worker_root) / "workspaces"
    return find_project_root() / "workspaces"
```

- [x] **Step 4: Run test to verify it passes**

Run: `python -m pytest packages/core/tests/test_paths.py::TestResolveWorkspacesDir -v`
Expected: PASS (all 7 tests — 4 existing + 3 new)

- [x] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/utils/paths.py packages/core/tests/test_paths.py
git commit -m "feat(paths): enhance resolve_workspaces_dir with SHANNON_WORKER_ROOT env var"
```

---

### Task 3: Update `BasePipelineInput` to use env var default factory

**Files:**
- Modify: `packages/core/src/shannon_core/models/base.py`
- Modify: `packages/core/tests/test_base_model.py`

- [x] **Step 1: Write the failing test**

Add the following tests to `packages/core/tests/test_base_model.py`:

```python
def test_deliverables_subdir_uses_default_without_env():
    """Without SHANNON_DELIVERABLES_SUBDIR env var, uses DEFAULT_DELIVERABLES_SUBDIR."""
    from shannon_core.constants import DEFAULT_DELIVERABLES_SUBDIR
    import os
    old = os.environ.pop("SHANNON_DELIVERABLES_SUBDIR", None)
    try:
        inp = BasePipelineInput()
        assert inp.deliverables_subdir == DEFAULT_DELIVERABLES_SUBDIR
    finally:
        if old is not None:
            os.environ["SHANNON_DELIVERABLES_SUBDIR"] = old


def test_deliverables_subdir_uses_env_when_set(monkeypatch):
    """With SHANNON_DELIVERABLES_SUBDIR env var set, uses its value."""
    monkeypatch.setenv("SHANNON_DELIVERABLES_SUBDIR", "custom/path")
    inp = BasePipelineInput()
    assert inp.deliverables_subdir == "custom/path"


def test_deliverables_subdir_can_be_overridden():
    """Explicit value takes precedence over env var."""
    inp = BasePipelineInput(deliverables_subdir="explicit/path")
    assert inp.deliverables_subdir == "explicit/path"
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest packages/core/tests/test_base_model.py::test_deliverables_subdir_uses_env_when_set -v`
Expected: FAIL — `assert '.shannon/deliverables' == 'custom/path'` (current default ignores env var)

- [x] **Step 3: Write minimal implementation**

Replace the contents of `packages/core/src/shannon_core/models/base.py` with:

```python
"""Shared base types for pipeline inputs."""

import os
from dataclasses import dataclass, field

from shannon_core.constants import DEFAULT_DELIVERABLES_SUBDIR


def _get_default_deliverables_subdir() -> str:
    """从环境变量获取默认产出物子目录。"""
    return os.getenv("SHANNON_DELIVERABLES_SUBDIR", DEFAULT_DELIVERABLES_SUBDIR)


@dataclass
class BasePipelineInput:
    """Shared fields for whitebox and blackbox pipeline inputs."""
    config_path: str | None = None
    output_path: str | None = None
    workspace_name: str | None = None
    resume_from_workspace: str | None = None
    vuln_classes: list[str] | None = None      # Unified to str
    pipeline_testing_mode: bool = False
    api_key: str | None = None
    deliverables_subdir: str = field(default_factory=_get_default_deliverables_subdir)
```

- [x] **Step 4: Run test to verify it passes**

Run: `python -m pytest packages/core/tests/test_base_model.py -v`
Expected: PASS (all 6 tests — 3 existing + 3 new)

- [x] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/models/base.py packages/core/tests/test_base_model.py
git commit -m "feat(models): BasePipelineInput.deliverables_subdir reads SHANNON_DELIVERABLES_SUBDIR env var"
```

---

### Task 4: Update `.env.example` with path configuration section

**Files:**
- Modify: `.env.example`

- [x] **Step 1: Add path configuration section**

Add the following block at the end of `.env.example`, after the browser engine section (after line 82):

```ini

# =============================================================================
# 路径配置（可选）
# =============================================================================

# 产出物存储子目录（相对于目标仓库根目录，默认 .shannon/deliverables）
# SHANNON_DELIVERABLES_SUBDIR=.shannon/deliverables

# Worker 基准目录（用于解析相对路径，默认当前工作目录）
# SHANNON_WORKER_ROOT=/path/to/worker/root
```

- [x] **Step 2: Verify existing tests still pass**

Run: `python -m pytest packages/core/tests/ -v`
Expected: PASS (all tests)

- [x] **Step 3: Commit**

```bash
git add .env.example
git commit -m "docs: add path configuration env vars to .env.example"
```
