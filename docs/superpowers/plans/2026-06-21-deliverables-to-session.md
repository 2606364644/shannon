# deliverables 迁移至 session 目录 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 deliverables 产出物从 `<repo>/.shannon/deliverables` 迁到 `workspaces/<session>/deliverables`（不再污染被扫仓库），并删除已被 `--fresh`/`--rerun` 取代的 `clean` 功能。

**Architecture:** 单点重写 `resolve_deliverables_path` 为 workspace_name 维度（repo_path 退化为过渡兼容），所有白盒/黑盒/rerun/resume 调用点自动跟随；白盒无 `-w` 时自动生成 session name；黑盒默认 `--latest` 接最近白盒 session；删除 `clean_workspace` + `clean` CLI（与 rerun 的 `.blackbox-archive/` 冲突）。

**Tech Stack:** Python 3, Click CLI, dataclasses, pytest（`asyncio_mode=auto`, `--import-mode=importlib`）。

**Spec:** `docs/superpowers/specs/2026-06-19-deliverables-to-session-design.md`

## Global Constraints

- deliverables 子目录固定 `deliverables`（不再有 `.shannon` 前缀）；`DEFAULT_DELIVERABLES_SUBDIR = "deliverables"`。
- 按 session 组织，不按 repo 累积；不自动迁移旧 `<repo>/.shannon/deliverables`。
- 白盒黑盒继续**共享**同一 deliverables 目录（黑盒产物落白盒 session）。
- **跑测试只跑子集**：`pytest packages/<pkg>/tests/<file>.py -v`。**绝不跑全量**（`pytest` 或全包会卡 Temporal 慢测试 hang）。
- 每个 task 结束前相关子集测试必须全绿，再 commit。

---

### Task 1: 迁移 deliverables 路径根到 session（核心 + 路径测试）

**Files:**
- Modify: `packages/core/src/shannon_core/constants.py`
- Modify: `packages/core/src/shannon_core/utils/paths.py:41-86`
- Modify: `packages/core/tests/test_paths.py`
- Modify: `packages/core/tests/test_constants.py`
- Modify: `packages/blackbox/tests/test_workflows.py:28-88`

**Interfaces:**
- Produces: `resolve_deliverables_path(repo_path, deliverables_subdir, workspace_name=None, workspaces_root=None) -> Path`：**workspace_name 优先** → `workspaces_root/<workspace_name>/<deliverables_subdir>`；workspace_name 缺失时过渡回退 `repo_path/<subdir>`；都无 raise ValueError。
- Produces: `deliverables_dir_for_workspace(workspace_path) -> Path`：直接返回 `workspace_path / <subdir>`（不再从 session.json 恢复 repo_path）。
- Produces: `DEFAULT_DELIVERABLES_SUBDIR = "deliverables"`。

- [ ] **Step 1: 改 constants.py**

`packages/core/src/shannon_core/constants.py` 改为：
```python
DEFAULT_DELIVERABLES_SUBDIR: str = "deliverables"
```

- [ ] **Step 2: 重写 resolve_deliverables_path**

`packages/core/src/shannon_core/utils/paths.py:41-70` 整个函数替换为：
```python
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
```

- [ ] **Step 3: 简化 deliverables_dir_for_workspace**

`packages/core/src/shannon_core/utils/paths.py:73-86` 整个函数替换为：
```python
def deliverables_dir_for_workspace(workspace_path: Path) -> Path:
    """workspace 下的 deliverables 目录。

    deliverables 落在 session 下：workspaces/<session>/<subdir>。
    workspace_path 已是 workspaces/<session>，直接拼子目录。
    """
    return workspace_path / get_default_deliverables_subdir()
```

- [ ] **Step 4: 更新 test_paths.py — TestResolveDeliverablesPath**

`packages/core/tests/test_paths.py` 的 `class TestResolveDeliverablesPath`（78-143）整体替换为：
```python
class TestResolveDeliverablesPath:
    def test_workspace_name_takes_priority(self, tmp_path):
        """workspace_name 优先 → workspaces/<name>/deliverables，忽略 repo_path。"""
        result = resolve_deliverables_path(
            repo_path="/data/repos/myrepo",
            deliverables_subdir="deliverables",
            workspace_name="scan-1",
            workspaces_root=tmp_path / "workspaces",
        )
        assert result == tmp_path / "workspaces" / "scan-1" / "deliverables"

    def test_workspace_name_without_workspaces_root(self, tmp_path, monkeypatch):
        """workspace_name 但未传 workspaces_root → 用 resolve_workspaces_dir()。"""
        monkeypatch.setenv("SHANNON_WORKER_ROOT", str(tmp_path / "worker"))
        result = resolve_deliverables_path(
            repo_path=None,
            deliverables_subdir="deliverables",
            workspace_name="scan-1",
        )
        assert result == tmp_path / "worker" / "workspaces" / "scan-1" / "deliverables"

    def test_repo_path_fallback_when_no_workspace(self):
        """无 workspace_name 时过渡回退 repo_path/<subdir>。"""
        result = resolve_deliverables_path(
            repo_path="/data/repos/myrepo",
            deliverables_subdir="deliverables",
        )
        assert result == Path("/data/repos/myrepo/deliverables")

    def test_raises_when_no_repo_or_workspace(self):
        with pytest.raises(ValueError, match="必须提供"):
            resolve_deliverables_path(
                repo_path=None,
                deliverables_subdir="deliverables",
            )
```

- [ ] **Step 5: 更新 test_paths.py — TestDeliverablesDirForWorkspace**

`packages/core/tests/test_paths.py:233-257`（`class TestDeliverablesDirForWorkspace`）整体替换为：
```python
class TestDeliverablesDirForWorkspace:
    """deliverables_dir_for_workspace 直接返回 workspace 下的 deliverables 目录。"""

    def test_returns_workspace_deliverables(self, tmp_path):
        from shannon_core.session import SessionManager

        repo = tmp_path / "repo"
        repo.mkdir()
        mgr = SessionManager(tmp_path / "workspaces")
        ws = mgr.create_workspace("https://x.com", str(repo), name="wb-1")

        assert deliverables_dir_for_workspace(ws) == ws / "deliverables"

    def test_works_without_session_json(self, tmp_path):
        ws = tmp_path / "workspaces" / "orphan"
        ws.mkdir(parents=True)
        assert deliverables_dir_for_workspace(ws) == ws / "deliverables"
```

（`TestGetDefaultDeliverablesSubdir` 215-230 无需改——它读常量/环境变量，不硬编码值。）

- [ ] **Step 6: 更新 test_constants.py**

`packages/core/tests/test_constants.py:9-11` 的 `test_default_deliverables_subdir_starts_with_dot` 替换为：
```python
def test_default_deliverables_subdir_is_session_relative():
    """deliverables 落在 session 下，子目录名固定 'deliverables'（无 .shannon 前缀）。"""
    assert DEFAULT_DELIVERABLES_SUBDIR == "deliverables"
```

- [ ] **Step 7: 更新 test_workflows.py — 改用真实 resolve_deliverables_path**

`packages/blackbox/tests/test_workflows.py:28-88`（`_resolve_deliverables` helper + 3 个 test_path_resolution_*）整体替换为：
```python
from shannon_core.utils.paths import resolve_deliverables_path


def test_path_resolution_workspace_name_priority(tmp_path):
    """workspace_name 优先 → workspaces/<name>/deliverables。"""
    repo = tmp_path / "my-repo"
    repo.mkdir()
    input = BlackboxPipelineInput(
        web_url="https://example.com",
        repo_path=str(repo),
        workspace_name="my-scan",
    )
    result = resolve_deliverables_path(
        repo_path=input.repo_path,
        deliverables_subdir=input.deliverables_subdir,
        workspace_name=input.workspace_name,
        workspaces_root=tmp_path / "workspaces",
    )
    assert result == tmp_path / "workspaces" / "my-scan" / "deliverables"


def test_path_resolution_pure_fallback(tmp_path):
    """无 workspace_name 时回退 repo_path/deliverables。"""
    repo = tmp_path / "my-repo"
    repo.mkdir()
    result = resolve_deliverables_path(
        repo_path=str(repo),
        deliverables_subdir="deliverables",
    )
    assert result == repo / "deliverables"
```

删除原 `_resolve_deliverables` helper（不再复制 resolve 逻辑）。

- [ ] **Step 8: 跑测试验证**

Run: `pytest packages/core/tests/test_paths.py packages/core/tests/test_constants.py packages/blackbox/tests/test_workflows.py -v`
Expected: 全部 PASS。

- [ ] **Step 9: Commit**

```bash
git add packages/core/src/shannon_core/constants.py packages/core/src/shannon_core/utils/paths.py packages/core/tests/test_paths.py packages/core/tests/test_constants.py packages/blackbox/tests/test_workflows.py
git commit -m "refactor(paths): deliverables 迁至 session 维度（workspace_name 优先）"
```

---

### Task 2: 更新下游路径断言测试

Task 1 改了路径语义，下游硬编码 `repo / ".shannon" / "deliverables"` 的测试需同步。本 task 只改测试，不改生产代码。

**Files:**
- Modify: `packages/core/tests/test_workspace.py`（`_create_workspace_with_queues` helper ~204-223）
- Modify: `tests/integration/test_whitebox_blackbox_handoff.py`（`_repo_deliverables` helper ~21-25）
- Modify: `packages/whitebox/tests/test_cli.py`（`test_start_shows_deliverables_path` ~115、`test_start_shows_results_summary` ~210 中的路径构造）
- Modify: `packages/blackbox/tests/test_cli.py`（`test_start_informs_when_blackbox_already_ran` ~613、`test_start_rerun_bypasses_idempotency` ~640、`test_latest_resolves_to_workspace` ~188、`test_auto_detect_single_match` ~283）
- Modify: `packages/blackbox/tests/test_worker.py`（`test_run_scan_rerun_archives_old_evidence_and_uses_new_id` ~156）
- Modify: `packages/blackbox/tests/test_audit_injection.py`（~31-32 mock 路径值）

**Interfaces:**
- Consumes: Task 1 的 `resolve_deliverables_path` / `deliverables_dir_for_workspace` 新语义。

- [ ] **Step 1: 找出所有硬编码路径断言**

Run: `grep -rn '".shannon" / "deliverables"\|\.shannon/deliverables\|/.shannon/deliverables' packages tests --include="*.py"`
记录所有命中行。迁移后 deliverables 在 `workspaces/<session>/deliverables`，`.shannon` 段消失。

- [ ] **Step 2: 更新 test_workspace.py helper**

`packages/core/tests/test_workspace.py` 的 `_create_workspace_with_queues`（~204-223）：把构造 deliverables 目录的 `repo / ".shannon" / "deliverables"` 改为 `workspaces / <session_name> / "deliverables"`（与该 helper 创建 workspace 的方式一致；若 helper 已创建 session，用 session 目录下的 `deliverables`）。

具体：定位 helper 内 `deliverables = ... / ".shannon" / "deliverables"` 行，改为 `deliverables = ws / "deliverables"`（`ws` 为该 helper 创建的 workspace 目录）。

- [ ] **Step 3: 更新 integration handoff helper**

`tests/integration/test_whitebox_blackbox_handoff.py` 的 `_repo_deliverables`（~21-25）：原返回 `repo / ".shannon" / "deliverables"`。改为返回 `workspaces_dir / <workspace_name> / "deliverables"`（用测试中实际创建的 workspace name）。若该 helper 被多处调用且各用不同 workspace name，改为接收 workspace 参数。

- [ ] **Step 4: 更新 whitebox/blackbox test_cli.py 路径构造**

对每个命中测试，把 deliverables 目录构造从 `repo / ".shannon" / "deliverables"` 改为 `workspaces / <ws_name> / "deliverables"`，保证与 `resolve_deliverables_path(workspace_name=<ws_name>)` 返回值一致。

- [ ] **Step 5: 更新 test_worker.py rerun 归档测试**

`packages/blackbox/tests/test_worker.py::test_run_scan_rerun_archives_old_evidence_and_uses_new_id`（~156）：`deliverables = repo / ".shannon" / "deliverables"` 改为 `workspaces / <ws_name> / "deliverables"`，并据此调整 input 的 workspace_name。

- [ ] **Step 6: 更新 test_audit_injection.py mock 值**

`packages/blackbox/tests/test_audit_injection.py`（~31-32）：`patch(...resolve_deliverables_path..., return_value=deliverables_root / "deliverables")` 的 mock 返回值保持 `... / "deliverables"`（无 `.shannon` 段），确认 `deliverables_root` 指向 session 目录。

- [ ] **Step 7: 跑全部受影响测试验证**

Run: `pytest packages/core/tests/test_workspace.py tests/integration/test_whitebox_blackbox_handoff.py packages/whitebox/tests/test_cli.py packages/blackbox/tests/test_cli.py packages/blackbox/tests/test_worker.py packages/blackbox/tests/test_audit_injection.py -v`
Expected: 全部 PASS。若有测试因 mock 时序/其它无关原因失败，确认是迁移相关才修。

- [ ] **Step 8: Commit**

```bash
git add packages tests
git commit -m "test: 同步下游 deliverables 路径断言到 session 维度"
```

---

### Task 3: 白盒无 `-w` 自动生成 session name

**Files:**
- Modify: `packages/core/src/shannon_core/session.py:13-17`（`create_workspace` 命名兜底）
- Modify: `packages/whitebox/src/shannon_whitebox/worker.py:69-76`（去守卫 + 回填 name）
- Test: `packages/core/tests/test_session.py`（create_workspace 命名测试）
- Test: `packages/whitebox/tests/test_worker.py` 或新增

**Interfaces:**
- Produces: `create_workspace` 在 `web_url` 为空时用 repo basename 命名。
- Produces: 白盒 `run_scan` 无 `-w` 时自动生成 name 并回填 `input.workspace_name`，使后续 deliverables/session_id 解析一致。

- [ ] **Step 1: 写失败测试 — create_workspace web_url 缺失用 repo basename**

在 `packages/core/tests/test_session.py` 新增：
```python
def test_create_workspace_names_after_repo_basename_when_no_url(tmp_path):
    """web_url 为空时用 repo basename 命名，不以空 hostname 开头。"""
    mgr = SessionManager(tmp_path / "workspaces")
    repo = tmp_path / "myapp"
    repo.mkdir()
    ws = mgr.create_workspace(web_url="", repo_path=str(repo), name=None)
    assert ws.name.startswith("myapp_")
    assert "shannon-" in ws.name
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest packages/core/tests/test_session.py::test_create_workspace_names_after_repo_basename_when_no_url -v`
Expected: FAIL（当前 web_url="" 生成 `_shannon-<ts>`，不以 myapp 开头）。

- [ ] **Step 3: 实现 create_workspace 命名兜底**

`packages/core/src/shannon_core/session.py:13-17` 的 `if not name:` 块替换为：
```python
        if not name:
            if web_url:
                hostname = (
                    web_url.replace("https://", "").replace("http://", "")
                    .split("/")[0].replace(".", "-")
                ) or "repo"
            else:
                hostname = Path(repo_path).name.replace(".", "-") or "repo"
            session_id = f"shannon-{int(time.time() * 1000)}"
            name = f"{hostname}_{session_id}"
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest packages/core/tests/test_session.py -v`
Expected: PASS。

- [ ] **Step 5: 写失败测试 — worker 自动生成回填 workspace_name**

在 `packages/whitebox/tests/test_worker.py` 新增（mock 掉 Temporal/worker，只验证 workspace_name 回填逻辑）。若现有 test_worker 难以隔离 run_scan，改为直接验证：调用 `SessionManager.create_workspace(name=None)` 返回的 name 非空，并断言白盒 run_scan 在 workspace_name=None 时会先生成。

> 实现者注意：run_scan 涉及 Temporal 连接，单元测试应 mock `Client.connect` 与 `Worker`，或提取"确保 workspace_name"为纯函数单独测。若隔离成本高，本步可降级为：断言 `create_workspace(name=None)` 返回合法 name（Step 3 已覆盖），并在 Step 6 实现后用 Task 5 人工冒烟验证端到端。

- [ ] **Step 6: 改 worker.py — 去守卫 + 回填 name + deliverables_path 回填**

`packages/whitebox/src/shannon_whitebox/worker.py:69-76` 替换为：
```python
    # 持久化 session（无 -w 时自动生成 name 并回填，使 deliverables/session_id 解析一致）
    workspaces_dir = resolve_workspaces_dir(input.repo_path)
    mgr = SessionManager(workspaces_dir)
    ws_path = mgr.create_workspace(
        web_url=input.web_url or "",
        repo_path=input.repo_path,
        name=input.workspace_name,
    )
    input.workspace_name = ws_path.name
```

同文件 `worker.py:260-263`（run_scan 末尾回填 `deliverables_path`）替换为（原代码硬编码 `Path(repo_path)/subdir`，不走 resolve，必须单独改）：
```python
                # deliverables 落 session 下（workspaces/<session>/deliverables）
                from shannon_core.utils.paths import resolve_deliverables_path
                result_dict["deliverables_path"] = str(
                    resolve_deliverables_path(
                        repo_path=input.repo_path,
                        deliverables_subdir=input.deliverables_subdir,
                        workspace_name=input.workspace_name,
                    )
                )
```
（`workspace_name` 已在上方回填，此处解析到 session 维度。）

- [ ] **Step 7: 跑 worker 相关测试**

Run: `pytest packages/whitebox/tests/test_worker.py -v`
Expected: PASS（或仅与本 task 无关的 Temporal 测试跳过/已知 hang，记录之）。

- [ ] **Step 8: Commit**

```bash
git add packages/core/src/shannon_core/session.py packages/whitebox/src/shannon_whitebox/worker.py packages/core/tests/test_session.py packages/whitebox/tests/test_worker.py
git commit -m "feat(whitebox): 无 -w 时自动生成 session name 并回填"
```

---

### Task 4: 删除 clean 功能

**Files:**
- Modify: `packages/core/src/shannon_core/session.py`（删 `BB_DELIVERABLE_PATTERNS` 8-13 + `clean_workspace` 174-245）
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/blackbox_rerun.py:16-17`（BB_PATTERNS 改本地定义）
- Modify: `packages/whitebox/src/shannon_whitebox/cli/main.py:347-368`（删 `clean` 命令）
- Modify: `packages/blackbox/src/shannon_blackbox/cli/main.py:406-428`（删 `clean` 命令）
- Modify: `packages/core/tests/test_session.py`（删 `test_clean_workspace_*` 252-334）
- Modify: `packages/whitebox/tests/test_cli.py`（删 `test_workspace_clean` 系列 ~351,375,386,403）
- Modify: `packages/blackbox/tests/test_cli.py`（删 `test_workspace_clean` 系列 ~520,549,560,577）

**Interfaces:**
- Produces: `BB_DELIVERABLE_PATTERNS` 移到 `blackbox_rerun.py`（删 clean 后只剩 rerun 用）。
- 移除：`SessionManager.clean_workspace`、`shannon-whitebox clean`、`shannon-blackbox clean`。

- [ ] **Step 1: 把 BB_DELIVERABLE_PATTERNS 迁到 blackbox_rerun.py**

`packages/blackbox/src/shannon_blackbox/pipeline/blackbox_rerun.py:11-17` 替换 import + 加本地常量：
```python
from __future__ import annotations

import shutil
from pathlib import Path

# Blackbox deliverable filename patterns (glob). 归档清单（archive_blackbox_deliverables 用）。
BB_DELIVERABLE_PATTERNS: list[str] = [
    "*_exploitation_evidence.md",
    "*_findings.md",
    "comprehensive_security_assessment_report.md",
]
```
（删除 `from shannon_core.session import BB_DELIVERABLE_PATTERNS` 这行。）

- [ ] **Step 2: 删 session.py 的 BB_DELIVERABLE_PATTERNS 和 clean_workspace**

`packages/core/src/shannon_core/session.py`：
- 删除文件顶部 `BB_DELIVERABLE_PATTERNS` 列表（8-13，含其上方注释）。
- 删除 `clean_workspace` 方法整体（174-245）。
- 若 `import fnmatch` 仅 clean_workspace 用，一并删除。

- [ ] **Step 3: 删白盒 clean CLI 命令**

`packages/whitebox/src/shannon_whitebox/cli/main.py`：删除 `clean` 子命令（~347-368，含 `@cli.command("clean")` 或 `@workspace.command("clean")` 装饰器到函数体）。

- [ ] **Step 4: 删黑盒 clean CLI 命令**

`packages/blackbox/src/shannon_blackbox/cli/main.py`：删除 `clean` 子命令（~406-428）。

- [ ] **Step 5: 删 clean 相关测试**

- `packages/core/tests/test_session.py`：删 `test_clean_workspace_whitebox`（252）、`test_clean_workspace_blackbox`（294）。
- `packages/whitebox/tests/test_cli.py`：删 `test_workspace_clean` 系列 4 个（~351,375,386,403）。
- `packages/blackbox/tests/test_cli.py`：删 `test_workspace_clean` 系列 4 个（~520,549,560,577）。

- [ ] **Step 6: 写测试验证 clean 已删、rerun archive 仍工作**

在 `packages/blackbox/tests/test_blackbox_rerun.py` 确认 `archive_blackbox_deliverables` 仍 import 自本地常量并 PASS（现有测试应不受影响，仅 import 来源变了）。若 `test_blackbox_rerun.py` 有 import `BB_DELIVERABLE_PATTERNS` from session，改为 from `blackbox_rerun`。

新增一个回归测试确保 `clean` 命令不存在：
```python
def test_clean_command_removed():
    from click.testing import CliRunner
    from shannon_blackbox.cli.main import cli as bb_cli
    from shannon_whitebox.cli.main import cli as wb_cli
    runner = CliRunner()
    # clean 子命令已删除 → 调用应非零退出/报 no such command
    res = runner.invoke(bb_cli, ["clean", "x"])
    assert res.exit_code != 0
```

- [ ] **Step 7: 跑测试验证**

Run: `pytest packages/core/tests/test_session.py packages/blackbox/tests/test_blackbox_rerun.py packages/blackbox/tests/test_cli.py packages/whitebox/tests/test_cli.py -v`
Expected: PASS（clean 测试已删，其余绿）。

- [ ] **Step 8: Commit**

```bash
git add packages
git commit -m "refactor: 删除 clean 功能（被 --fresh/--rerun 取代，与 rerun archive 冲突）"
```

---

### Task 5: 黑盒默认 `--latest` + 纯黑盒自建 session

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/cli/main.py:57-116`（start 命令的 workspace 发现逻辑）
- Modify: `packages/blackbox/src/shannon_blackbox/worker.py`（纯黑盒无白盒 session 时自建）
- Test: `packages/blackbox/tests/test_cli.py`

**Interfaces:**
- Produces: 黑盒 `start` 不传 `-w`/`--latest` 时默认 `find_latest_workspace`（软默认：找不到则 standalone，不报错）。
- Produces: 纯黑盒（无白盒 session）时黑盒自建 session，deliverables 落自建 session。

- [ ] **Step 1: 写失败测试 — 默认 --latest**

在 `packages/blackbox/tests/test_cli.py` 新增（mock worker/infra）：
```python
def test_start_defaults_to_latest_when_no_flags(tmp_path, monkeypatch):
    """不传 -w/--latest 时默认复用最近白盒 workspace。"""
    # 构造一个最近的白盒 workspace + 有效 queue
    workspaces = tmp_path / "workspaces"
    wb = workspaces / "wb-recent"
    (wb / "deliverables").mkdir(parents=True)
    (wb / "deliverables" / "injection_exploitation_queue.json").write_text(
        '{"vulnerabilities":[{"title":"t","description":"d","severity":"high","location":"x"}]}'
    )
    (wb / "session.json").write_text('{"scan_type":"whitebox","web_url":"https://x"}')
    monkeypatch.chdir(tmp_path)
    # mock 掉实际 worker 启动，只验证 resolved_workspace 解析
    ...
    # 断言：黑盒接上 wb-recent（不再走 URL 交互匹配）
```

> 实现者注意：参照现有 `test_latest_resolves_to_workspace`（~188）的 mock 模式构造，断言默认行为变成 latest。

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest packages/blackbox/tests/test_cli.py::test_start_defaults_to_latest_when_no_flags -v`
Expected: FAIL（当前默认走 URL 匹配 `find_workspaces_by_url`，非 latest）。

- [ ] **Step 3: 改 CLI 默认发现逻辑**

`packages/blackbox/src/shannon_blackbox/cli/main.py` 的 start 命令：把"不传 -w 且不传 --latest"分支（~73-116，当前 `find_workspaces_by_url` 交互）改为默认调用 `find_latest_workspace`：
```python
    if workspace:
        resolved_workspace = workspace
    else:
        # 默认 --latest（软默认）：复用最近白盒 workspace，找不到则 standalone
        latest = find_latest_workspace(Path("workspaces"), scan_type="whitebox", url=url)
        if latest is not None:
            resolved_workspace = latest.name
        else:
            resolved_workspace = None  # standalone 纯黑盒
```
保留 `--latest` 显式 flag 的既有处理；URL 交互匹配逻辑若无其它入口引用可删，否则保留为 `--match-url` 之类显式选项（YAGNI：若无人用，直接删）。

> 实现者注意：核对 `find_latest_workspace` 签名与返回（`packages/core/src/shannon_core/workspace.py`），返回 workspace 目录或 None。保留"找到的 workspace 需有有效 queue"的校验。

- [ ] **Step 4: 纯黑盒自建 session**

`packages/blackbox/src/shannon_blackbox/worker.py` 的 `run_scan`：当 `input.workspace_name` 为空（standalone）时，自建一个黑盒 session：
```python
    if not input.workspace_name:
        from shannon_core.session import SessionManager
        workspaces_dir = resolve_workspaces_dir(input.repo_path)
        mgr = SessionManager(workspaces_dir)
        ws_path = mgr.create_workspace(
            web_url=input.web_url or "",
            repo_path=input.repo_path or "",
            name=None,
            scan_type="blackbox",
        )
        input.workspace_name = ws_path.name
```
这样黑盒 deliverables（run_recon mkdir）落 `workspaces/<自建session>/deliverables`。

- [ ] **Step 5: 跑 CLI + worker 测试**

Run: `pytest packages/blackbox/tests/test_cli.py packages/blackbox/tests/test_worker.py -v`
Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add packages/blackbox
git commit -m "feat(blackbox): 默认 --latest 接最近白盒 + 纯黑盒自建 session"
```

---

### Task 6: 人工冒烟验证（不进 pytest）

> memory 已记录：白盒/黑盒/combined 真实路径无自动测试，需人工冒烟。本 task 不写自动化测试。

**Files:** 无代码改动，记录冒烟结果到 commit message 或 `docs/superpowers/specs/` 旁的 smoke note。

- [ ] **Step 1: 白盒无 `-w` 冒烟**

准备一个测试 repo（如 `tmp/repo`，含 `.git`）。
Run: `shannon-whitebox start -r tmp/repo --url https://example.com`（不带 -w）
验证：
- 输出的 workspace name 形如 `example-com_shannon-<ts>` 或 `repo_shannon-<ts>`（无空 hostname）。
- `workspaces/<session>/deliverables/` 下有白盒产物（如 `code_index.json`、`*_exploitation_queue.json`）。
- **被扫 repo 内不再出现 `.shannon/`**。

- [ ] **Step 2: 黑盒默认接白盒冒烟**

Run: `shannon-blackbox start --url https://example.com`（不带 -w/--latest）
验证：
- 自动接上 Step 1 的白盒 session（日志显示复用）。
- 黑盒 evidence 落 `workspaces/<白盒session>/deliverables/`（产物集中）。

- [ ] **Step 3: rerun 冒烟**

Run: `shannon-blackbox start --url https://example.com --rerun`
验证：
- 旧 evidence 归档到 `workspaces/<白盒session>/deliverables/.blackbox-archive/<ts>/`。
- 新 evidence 在顶层。
- 不报 `AlreadyStarted`。

- [ ] **Step 4: clean 已删验证**

Run: `shannon-whitebox clean x` 和 `shannon-blackbox clean x`
验证：报 "No such command"（clean 已删）。需要清理时用 `delete`。

- [ ] **Step 5: 记录冒烟结果**

冒烟通过后，在 commit message 注明，或写一行到 `docs/superpowers/specs/2026-06-19-deliverables-to-session-design.md` 末尾"验证状态"。

```bash
git add docs/superpowers/specs/2026-06-19-deliverables-to-session-design.md
git commit -m "docs: deliverables 迁移人工冒烟通过"
```
