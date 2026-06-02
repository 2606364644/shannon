# 白盒-黑盒扫描衔接 Bug 修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复白盒扫描完成后黑盒扫描无法正确发现和复用白盒结果的 4 个致命/严重 bug。

**Architecture:** 在 `shannon_core` 中新增共享的路径解析和队列检测函数，白盒和黑盒各自删除内联的重复逻辑、改为调用共享函数。同时统一 `workspace_path` 语义和 `deliverables_subdir` 常量定义。

**Tech Stack:** Python 3.12, dataclasses, pathlib, pytest

---

## 文件结构

| 操作 | 文件路径 | 职责 |
|------|---------|------|
| 新增 | `packages/core/src/shannon_core/constants.py` | 统一 `DEFAULT_DELIVERABLES_SUBDIR` 常量 |
| 新增 | `packages/core/tests/test_constants.py` | 常量测试 |
| 新增 | `packages/core/src/shannon_core/utils/paths.py` | 共享路径解析 + 队列检测函数 |
| 新增 | `packages/core/tests/test_paths.py` | 路径解析 + 队列检测测试 |
| 修改 | `packages/whitebox/src/shannon_whitebox/pipeline/shared.py` | 导入共享常量替换硬编码 |
| 修改 | `packages/blackbox/src/shannon_blackbox/pipeline/shared.py` | 导入共享常量替换硬编码 |
| 修改 | `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | 用共享函数替换 `_get_paths()` |
| 修改 | `packages/whitebox/src/shannon_whitebox/worker.py` | 用共享函数替换内联路径逻辑 |
| 修改 | `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` | 用共享函数替换内联路径逻辑 + 修复 workspace_path + 统一检测标准 |
| 修改 | `packages/blackbox/src/shannon_blackbox/pipeline/activities.py` | 用共享函数替换 `_get_deliverables_path()` |
| 修改 | `packages/blackbox/src/shannon_blackbox/services/exploitation_checker.py` | 用共享函数替换内联检测逻辑 |

---

### Task 1: 统一 deliverables_subdir 常量

**Files:**
- Create: `packages/core/src/shannon_core/constants.py`
- Create: `packages/core/tests/test_constants.py`
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/shared.py:17`
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/shared.py:16,37`

- [ ] **Step 1: 写失败测试**

创建 `packages/core/tests/test_constants.py`:

```python
from shannon_core.constants import DEFAULT_DELIVERABLES_SUBDIR


def test_default_deliverables_subdir_is_string():
    assert isinstance(DEFAULT_DELIVERABLES_SUBDIR, str)
    assert len(DEFAULT_DELIVERABLES_SUBDIR) > 0


def test_default_deliverables_subdir_starts_with_dot():
    assert DEFAULT_DELIVERABLES_SUBDIR.startswith(".")
    assert "/" in DEFAULT_DELIVERABLES_SUBDIR
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/test_constants.py -v`
Expected: FAIL — `cannot import name 'DEFAULT_DELIVERABLES_SUBDIR' from 'shannon_core.constants'`

- [ ] **Step 3: 写实现**

创建 `packages/core/src/shannon_core/constants.py`:

```python
DEFAULT_DELIVERABLES_SUBDIR: str = ".shannon/deliverables"
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/test_constants.py -v`
Expected: PASS

- [ ] **Step 5: 修改白盒 shared.py 导入共享常量**

修改 `packages/whitebox/src/shannon_whitebox/pipeline/shared.py`，将第 1-2 行改为：

```python
from dataclasses import dataclass, field

from shannon_core.constants import DEFAULT_DELIVERABLES_SUBDIR
from shannon_core.models.agents import VulnType
from shannon_core.models.metrics import AgentMetrics
```

将第 17 行 `deliverables_subdir: str = ".shannon/deliverables"` 改为：

```python
    deliverables_subdir: str = DEFAULT_DELIVERABLES_SUBDIR
```

同样将第 35 行 `deliverables_subdir: str = ".shannon/deliverables"` 改为：

```python
    deliverables_subdir: str = DEFAULT_DELIVERABLES_SUBDIR
```

- [ ] **Step 6: 修改黑盒 shared.py 导入共享常量**

修改 `packages/blackbox/src/shannon_blackbox/pipeline/shared.py`，将第 1 行改为：

```python
from dataclasses import dataclass, field

from shannon_core.constants import DEFAULT_DELIVERABLES_SUBDIR
```

将第 16 行 `deliverables_subdir: str = ".shannon/deliverables"` 改为：

```python
    deliverables_subdir: str = DEFAULT_DELIVERABLES_SUBDIR
```

将第 37 行 `deliverables_subdir: str = ".shannon/deliverables"` 改为：

```python
    deliverables_subdir: str = DEFAULT_DELIVERABLES_SUBDIR
```

- [ ] **Step 7: 运行全量测试确认无回归**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/test_constants.py packages/whitebox/tests/test_pipeline_shared.py packages/blackbox/tests/test_pipeline_shared.py -v`
Expected: 全部 PASS

- [ ] **Step 8: 提交**

```bash
git add packages/core/src/shannon_core/constants.py packages/core/tests/test_constants.py packages/whitebox/src/shannon_whitebox/pipeline/shared.py packages/blackbox/src/shannon_blackbox/pipeline/shared.py
git commit -m "fix(core): unify DEFAULT_DELIVERABLES_SUBDIR constant across whitebox and blackbox"
```

---

### Task 2: 新增共享路径解析函数

**Files:**
- Create: `packages/core/src/shannon_core/utils/paths.py`
- Create: `packages/core/tests/test_paths.py`

- [ ] **Step 1: 写失败测试 — resolve_workspaces_dir**

创建 `packages/core/tests/test_paths.py`:

```python
import json
import pytest
from pathlib import Path

from shannon_core.utils.paths import resolve_workspaces_dir, resolve_deliverables_path


class TestResolveWorkspacesDir:
    def test_with_repo_path(self):
        result = resolve_workspaces_dir("/data/repos/myrepo")
        assert result == Path("/data/repos/workspaces")

    def test_with_repo_path_nested(self):
        result = resolve_workspaces_dir("/a/b/c")
        assert result == Path("/a/b/workspaces")

    def test_without_repo_path(self):
        result = resolve_workspaces_dir()
        assert result == Path("workspaces")
```

- [ ] **Step 2: 写失败测试 — resolve_deliverables_path**

追加到 `packages/core/tests/test_paths.py`:

```python
class TestResolveDeliverablesPath:
    def test_with_repo_path(self):
        result = resolve_deliverables_path(
            repo_path="/data/repos/myrepo",
            deliverables_subdir=".shannon/deliverables",
        )
        assert result == Path("/data/repos/myrepo/.shannon/deliverables")

    def test_with_workspace_name_reads_session(self, tmp_path):
        # Create a session.json with repo_path
        ws_dir = tmp_path / "workspaces" / "scan-1"
        ws_dir.mkdir(parents=True)
        session_data = {"repo_path": "/data/repos/myrepo", "web_url": "https://example.com"}
        (ws_dir / "session.json").write_text(json.dumps(session_data))

        result = resolve_deliverables_path(
            repo_path=None,
            deliverables_subdir=".shannon/deliverables",
            workspace_name="scan-1",
            workspaces_root=tmp_path / "workspaces",
        )
        assert result == Path("/data/repos/myrepo/.shannon/deliverables")

    def test_with_workspace_name_fallback_when_no_session(self, tmp_path):
        ws_dir = tmp_path / "workspaces"
        # No session.json created
        result = resolve_deliverables_path(
            repo_path=None,
            deliverables_subdir=".shannon/deliverables",
            workspace_name="scan-1",
            workspaces_root=ws_dir,
        )
        assert result == ws_dir / "scan-1" / ".shannon/deliverables"

    def test_with_workspace_name_fallback_when_no_repo_in_session(self, tmp_path):
        ws_dir = tmp_path / "workspaces" / "scan-1"
        ws_dir.mkdir(parents=True)
        session_data = {"web_url": "https://example.com"}  # no repo_path
        (ws_dir / "session.json").write_text(json.dumps(session_data))

        result = resolve_deliverables_path(
            repo_path=None,
            deliverables_subdir=".shannon/deliverables",
            workspace_name="scan-1",
            workspaces_root=tmp_path / "workspaces",
        )
        assert result == tmp_path / "workspaces" / "scan-1" / ".shannon/deliverables"

    def test_raises_when_no_repo_or_workspace(self):
        with pytest.raises(ValueError, match="必须提供"):
            resolve_deliverables_path(
                repo_path=None,
                deliverables_subdir=".shannon/deliverables",
            )

    def test_repo_path_takes_priority_over_workspace(self, tmp_path):
        ws_dir = tmp_path / "workspaces" / "scan-1"
        ws_dir.mkdir(parents=True)
        session_data = {"repo_path": "/other/repo"}
        (ws_dir / "session.json").write_text(json.dumps(session_data))

        result = resolve_deliverables_path(
            repo_path="/data/repos/myrepo",
            deliverables_subdir=".shannon/deliverables",
            workspace_name="scan-1",
            workspaces_root=tmp_path / "workspaces",
        )
        assert result == Path("/data/repos/myrepo/.shannon/deliverables")
```

- [ ] **Step 3: 写失败测试 — has_valid_whitebox_results**

追加到 `packages/core/tests/test_paths.py`:

```python
from shannon_core.utils.paths import has_valid_whitebox_results


class TestHasValidWhiteboxResults:
    def test_file_not_found(self, tmp_path):
        assert has_valid_whitebox_results(tmp_path / "nonexistent.json") is False

    def test_valid_vulnerabilities(self, tmp_path):
        queue_file = tmp_path / "injection_exploitation_queue.json"
        queue_file.write_text(json.dumps({"vulnerabilities": [{"ID": "V-001"}]}))
        assert has_valid_whitebox_results(queue_file) is True

    def test_empty_vulnerabilities(self, tmp_path):
        queue_file = tmp_path / "injection_exploitation_queue.json"
        queue_file.write_text(json.dumps({"vulnerabilities": []}))
        assert has_valid_whitebox_results(queue_file) is False

    def test_missing_vulnerabilities_key(self, tmp_path):
        queue_file = tmp_path / "injection_exploitation_queue.json"
        queue_file.write_text(json.dumps({"data": "something"}))
        assert has_valid_whitebox_results(queue_file) is False

    def test_invalid_json(self, tmp_path):
        queue_file = tmp_path / "injection_exploitation_queue.json"
        queue_file.write_text("not json")
        assert has_valid_whitebox_results(queue_file) is False

    def test_vulnerabilities_not_a_list(self, tmp_path):
        queue_file = tmp_path / "injection_exploitation_queue.json"
        queue_file.write_text(json.dumps({"vulnerabilities": "not a list"}))
        assert has_valid_whitebox_results(queue_file) is False
```

- [ ] **Step 4: 运行测试确认失败**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/test_paths.py -v`
Expected: FAIL — `cannot import name 'resolve_workspaces_dir' from 'shannon_core.utils.paths'`

- [ ] **Step 5: 写实现**

创建 `packages/core/src/shannon_core/utils/paths.py`:

```python
import json
from pathlib import Path


def resolve_workspaces_dir(repo_path: str | None = None) -> Path:
    """解析 workspaces 根目录。

    如果提供 repo_path，使用 repo_path.parent / "workspaces"；
    否则使用相对路径 "workspaces"（依赖调用方 CWD）。
    """
    if repo_path:
        return Path(repo_path).parent / "workspaces"
    return Path("workspaces")


def resolve_deliverables_path(
    repo_path: str | None,
    deliverables_subdir: str,
    workspace_name: str | None = None,
    workspaces_root: Path | None = None,
) -> Path:
    """统一的 deliverables 路径解析。

    优先级：
    1. repo_path 存在 → repo_path / deliverables_subdir
    2. workspace_name 存在 → 从 session.json 恢复 repo_path → repo_path / deliverables_subdir
    3. fallback → workspaces_root / workspace_name / deliverables_subdir
    """
    if repo_path:
        return Path(repo_path) / deliverables_subdir

    if workspace_name:
        ws_root = workspaces_root or resolve_workspaces_dir()
        session_file = ws_root / workspace_name / "session.json"
        if session_file.exists():
            try:
                session_data = json.loads(session_file.read_text(encoding="utf-8"))
                saved_repo = session_data.get("repo_path")
                if saved_repo:
                    return Path(saved_repo) / deliverables_subdir
            except (json.JSONDecodeError, OSError):
                pass
        return ws_root / workspace_name / deliverables_subdir

    raise ValueError("必须提供 repo_path 或 workspace_name 之一")


def has_valid_whitebox_results(queue_file: Path) -> bool:
    """检查 exploitation queue 文件是否包含有效漏洞条目。"""
    if not queue_file.exists():
        return False
    try:
        data = json.loads(queue_file.read_text(encoding="utf-8"))
        return isinstance(data.get("vulnerabilities"), list) and len(data["vulnerabilities"]) > 0
    except (json.JSONDecodeError, KeyError, OSError):
        return False
```

- [ ] **Step 6: 运行测试确认通过**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/test_paths.py -v`
Expected: 全部 PASS

- [ ] **Step 7: 提交**

```bash
git add packages/core/src/shannon_core/utils/paths.py packages/core/tests/test_paths.py
git commit -m "feat(core): add shared path resolution and whitebox result validation utilities"
```

---

### Task 3: 白盒改用共享函数

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:18-22`
- Modify: `packages/whitebox/src/shannon_whitebox/worker.py:20`

- [ ] **Step 1: 修改白盒 activities.py**

在 `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` 顶部增加导入：

```python
from shannon_core.utils.paths import resolve_deliverables_path
```

删除第 18-22 行的 `_get_paths` 函数，替换为：

```python
def _get_paths(input: ActivityInput) -> tuple[Path, Path, Path]:
    deliverables = resolve_deliverables_path(
        repo_path=input.repo_path,
        deliverables_subdir=input.deliverables_subdir,
        workspace_name=input.workspace_name,
    )
    repo = Path(input.repo_path)
    workspaces = repo.parent / "workspaces"
    return repo, deliverables, workspaces
```

- [ ] **Step 2: 修改白盒 worker.py**

在 `packages/whitebox/src/shannon_whitebox/worker.py` 顶部增加导入：

```python
from shannon_core.utils.paths import resolve_workspaces_dir
```

将第 20 行：
```python
        workspaces_dir = Path(input.repo_path).parent / "workspaces"
```
替换为：
```python
        workspaces_dir = resolve_workspaces_dir(input.repo_path)
```

- [ ] **Step 3: 运行白盒测试确认无回归**

Run: `cd /root/shannon-py && uv run pytest packages/whitebox/tests/ -v`
Expected: 全部 PASS

- [ ] **Step 4: 提交**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/src/shannon_whitebox/worker.py
git commit -m "refactor(whitebox): use shared path resolution utilities"
```

---

### Task 4: 黑盒改用共享函数 + 修复 workspace_path + 统一检测标准

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py:34-43,76-97`
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/activities.py:15-19`
- Modify: `packages/blackbox/src/shannon_blackbox/services/exploitation_checker.py:20-42`

- [ ] **Step 1: 修改黑盒 activities.py**

在 `packages/blackbox/src/shannon_blackbox/pipeline/activities.py` 顶部增加导入：

```python
from shannon_core.utils.paths import resolve_deliverables_path
```

删除第 15-19 行的 `_get_deliverables_path` 函数，替换为：

```python
def _get_deliverables_path(input: BlackboxActivityInput) -> Path:
    return resolve_deliverables_path(
        repo_path=input.repo_path,
        deliverables_subdir=input.deliverables_subdir,
        workspace_name=input.workspace_name,
    )
```

- [ ] **Step 2: 修改黑盒 workflows.py — 修复 workspace_path + 导入**

在 `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` 顶部增加导入（在第 9 行后）：

```python
from shannon_core.utils.paths import resolve_workspaces_dir, resolve_deliverables_path, has_valid_whitebox_results
```

将第 34-43 行（`act_input = BlackboxActivityInput(...)` 整块）替换为：

```python
        # Compute workspace_path consistent with whitebox (workspaces/<name>/)
        if input.workspace_name:
            workspace_path = str(resolve_workspaces_dir(input.repo_path) / input.workspace_name)
        else:
            workspace_path = input.repo_path

        act_input = BlackboxActivityInput(
            web_url=input.web_url,
            repo_path=input.repo_path,
            config_path=input.config_path,
            workspace_name=input.workspace_name,
            deliverables_subdir=input.deliverables_subdir,
            pipeline_testing_mode=input.pipeline_testing_mode,
            api_key=input.api_key,
            workspace_path=workspace_path,
        )
```

- [ ] **Step 3: 修改黑盒 workflows.py — 替换内联路径解析为共享函数**

将第 76-89 行（`# Resolve deliverables path...` 整块，直到 `deliverables = Path("workspaces")...`）替换为：

```python
            # Resolve deliverables path using shared utility
            deliverables = resolve_deliverables_path(
                repo_path=input.repo_path,
                deliverables_subdir=input.deliverables_subdir,
                workspace_name=input.workspace_name,
                workspaces_root=resolve_workspaces_dir(input.repo_path),
            )
```

- [ ] **Step 4: 修改黑盒 workflows.py — 统一白盒结果检测标准**

将第 93-97 行（`for vt in selected_classes:` 循环中的检测逻辑）：

```python
            for vt in selected_classes:
                queue_file = deliverables / f"{vt}_exploitation_queue.json"
                if queue_file.exists():
                    has_whitebox_results = True
                    found_classes.append(vt)
```

替换为：

```python
            for vt in selected_classes:
                queue_file = deliverables / f"{vt}_exploitation_queue.json"
                if has_valid_whitebox_results(queue_file):
                    has_whitebox_results = True
                    found_classes.append(vt)
```

- [ ] **Step 5: 修改黑盒 exploitation_checker.py — 使用共享函数**

在 `packages/blackbox/src/shannon_blackbox/services/exploitation_checker.py` 顶部增加导入：

```python
from shannon_core.utils.paths import has_valid_whitebox_results
```

将 `should_exploit` 方法体（第 17-42 行）替换为：

```python
        @staticmethod
        async def should_exploit(
            deliverables_path: Path,
            vuln_type: str,
            exploit_enabled: bool = True,
        ) -> bool:
            if not exploit_enabled:
                return False

            queue_path = deliverables_path / f"{vuln_type}_exploitation_queue.json"
            if not await async_path_exists(queue_path):
                return False

            return has_valid_whitebox_results(queue_path)
```

- [ ] **Step 6: 运行黑盒测试确认无回归**

Run: `cd /root/shannon-py && uv run pytest packages/blackbox/tests/ -v`
Expected: 全部 PASS

- [ ] **Step 7: 运行全量测试**

Run: `cd /root/shannon-py && uv run pytest -v`
Expected: 全部 PASS

- [ ] **Step 8: 提交**

```bash
git add packages/blackbox/src/shannon_blackbox/pipeline/workflows.py packages/blackbox/src/shannon_blackbox/pipeline/activities.py packages/blackbox/src/shannon_blackbox/services/exploitation_checker.py
git commit -m "fix(blackbox): use shared path resolution, fix workspace_path semantics, unify whitebox result detection"
```
