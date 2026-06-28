# 黑盒 finalize_report 漏注册修复 + activity 注册完整性护栏 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 blackbox `finalize_report` activity 漏注册到 worker 导致 reporting 卡死的 bug，并用 core 共享的集合相等护栏（黑白盒共用）根治此类「activity 漏注册」复发。

**Architecture:** 修 bug = `worker.py` 补 import + 注册 `finalize_report`（2 处同步）。护栏 = core 新增 `assert_all_activities_registered(worker_module, activities_modules)`，用 AST 解析源码比对 `set(注册) == set(@activity.defn 定义)`，既抓 missing 也抓 extra；blackbox/whitebox 各加一个测试调用它。

**Tech Stack:** Python 3.13、temporalio（`@activity.defn` / `Worker`）、pytest、`ast` 标准库。monorepo `packages/{core,blackbox,whitebox}`，测试经 `uv run pytest`。

## Global Constraints

- **测试只跑改动相关子集，禁跑全套**：全套 pytest 会 hang（memory `pytest-whitebox-hang`，卡 Temporal/网络慢测试）。每个 task 的「Run」步骤都只跑该 task 点名的测试文件/用例。
- **TDD**：先写测试看它 fail，再写实现转 green，禁止「先实现后补测试」。
- **改 worker 注册须两处同步**：`from .pipeline.activities import (...)` 段 + `Worker(..., activities=[...])` 列表，漏任一处即 bug。
- **不改非目标**：不动生产 retry policy（`PRODUCTION_RETRY` 50 次）、不改 reporting 3 步结构、不碰引擎流程、不引入「worker 自动收集」结构性方案（见 spec §5 非目标）。
- **frequent commits**：每个 task 结束 commit；commit 只 add 该 task 的文件，不 `git add -A`（当前分支有其他未提交改动，勿误带）。

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `packages/core/src/shannon_core/testing/__init__.py` | 标记 `testing` 子包（空） | Create |
| `packages/core/src/shannon_core/testing/activity_registration.py` | 纯函数 `_activity_def_names` / `_registered_activity_names` + 公共 `assert_all_activities_registered` | Create |
| `packages/core/tests/test_activity_registration.py` | helper 单测（纯函数提取 + missing/extra/相等） | Create |
| `packages/blackbox/src/shannon_blackbox/worker.py` | import 段 + activities 列表补 `finalize_report` | Modify |
| `packages/blackbox/tests/test_worker.py` | 加 `test_all_activities_registered` | Modify |
| `packages/whitebox/tests/test_worker.py` | 加 `test_all_activities_registered` | Modify |

---

## Task 1: core 集合相等护栏 helper + 单测

**Files:**
- Create: `packages/core/src/shannon_core/testing/__init__.py`
- Create: `packages/core/src/shannon_core/testing/activity_registration.py`
- Test: `packages/core/tests/test_activity_registration.py`

**Interfaces:**
- Produces: `assert_all_activities_registered(worker_module: ModuleType, activities_modules: Sequence[ModuleType]) -> None` —— Task 2/3 的测试调用它。签名固定，后续 task 按此 import。

- [ ] **Step 1: 写 helper 单测（先红）**

Create `packages/core/tests/test_activity_registration.py`：

```python
"""assert_all_activities_registered 护栏的单测。

用合成源码验证三种情形：注册==定义通过、missing 报错、extra 报错，
以及两个纯提取函数只抓该抓的节点。
"""
import textwrap
from types import ModuleType

import pytest

from shannon_core.testing.activity_registration import (
    _activity_def_names,
    _registered_activity_names,
    assert_all_activities_registered,
)


def test_activity_def_names_picks_up_decorated_only():
    source = textwrap.dedent("""
        import activity
        @activity.defn
        async def alpha(input): ...
        @activity.defn(name="custom")
        async def beta(input): ...
        async def not_an_activity(input): ...
    """)
    assert _activity_def_names(source) == {"alpha", "beta"}


def test_registered_activity_names_collects_worker_list():
    source = "worker = Worker(client, activities=[alpha, beta, gamma])\n"
    assert _registered_activity_names(source) == {"alpha", "beta", "gamma"}


def _make_module(name: str, file_path, source: str) -> ModuleType:
    file_path.write_text(source, encoding="utf-8")
    mod = ModuleType(name)
    mod.__file__ = str(file_path)
    return mod


def test_assert_passes_when_registered_equals_defined(tmp_path):
    activities_src = textwrap.dedent("""
        import activity
        @activity.defn
        async def alpha(input): ...
        @activity.defn
        async def beta(input): ...
    """)
    worker_src = "worker = Worker(client, activities=[alpha, beta])\n"
    worker = _make_module("fake_worker", tmp_path / "worker.py", worker_src)
    activities = _make_module("fake_activities", tmp_path / "activities.py", activities_src)
    assert_all_activities_registered(worker, [activities])  # 不抛即通过


def test_assert_reports_missing(tmp_path):
    activities_src = textwrap.dedent("""
        import activity
        @activity.defn
        async def alpha(input): ...
        @activity.defn
        async def forgotten(input): ...
    """)
    worker_src = "worker = Worker(client, activities=[alpha])\n"
    worker = _make_module("fake_worker", tmp_path / "worker.py", worker_src)
    activities = _make_module("fake_activities", tmp_path / "activities.py", activities_src)
    with pytest.raises(AssertionError, match="missing.*forgotten"):
        assert_all_activities_registered(worker, [activities])


def test_assert_reports_extra(tmp_path):
    activities_src = textwrap.dedent("""
        import activity
        @activity.defn
        async def alpha(input): ...
    """)
    worker_src = "worker = Worker(client, activities=[alpha, ghost])\n"
    worker = _make_module("fake_worker", tmp_path / "worker.py", worker_src)
    activities = _make_module("fake_activities", tmp_path / "activities.py", activities_src)
    with pytest.raises(AssertionError, match="extra.*ghost"):
        assert_all_activities_registered(worker, [activities])
```

- [ ] **Step 2: 跑测试确认 fail**

Run: `uv run pytest packages/core/tests/test_activity_registration.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'shannon_core.testing.activity_registration'`（helper 尚未创建）。

- [ ] **Step 3: 实现 helper（转绿）**

Create `packages/core/src/shannon_core/testing/__init__.py`（空文件，标记子包）：

```python
```

Create `packages/core/src/shannon_core/testing/activity_registration.py`：

```python
"""Activity 注册完整性护栏：断言 worker 注册集合 == @activity.defn 定义集合。

用 AST 解析源码（不依赖运行期 import、不连 temporal），既抓「漏注册」(missing)
也抓「幽灵注册」(extra)。供 blackbox/whitebox test_worker 复用，防 temporalio
activity 漏注册导致 workflow 卡死（见
docs/superpowers/specs/2026-06-29-blackbox-finalize-report-worker-registration-design.md）。
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import ModuleType
from typing import Sequence


def _activity_def_names(source: str) -> set[str]:
    """从源码提取所有 @activity.defn / @activity.defn(...) 装饰的函数名。"""
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            if isinstance(target, ast.Attribute) and target.attr == "defn":
                names.add(node.name)
    return names


def _registered_activity_names(source: str) -> set[str]:
    """从 worker 源码提取所有 Worker(..., activities=[...]) 里注册的 activity 名。

    合并所有 Worker(...) 调用的 activities= 关键字（防多实例化），仅取 Name 节点。
    """
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        callee = (
            func.id if isinstance(func, ast.Name)
            else (func.attr if isinstance(func, ast.Attribute) else None)
        )
        if callee != "Worker":
            continue
        for kw in node.keywords:
            if kw.arg != "activities" or not isinstance(kw.value, ast.List):
                continue
            for el in kw.value.elts:
                if isinstance(el, ast.Name):
                    names.add(el.id)
    return names


def assert_all_activities_registered(
    worker_module: ModuleType,
    activities_modules: Sequence[ModuleType],
) -> None:
    """断言 worker 注册的 activity 集合 == activities 模块定义的 @activity.defn 集合。

    worker_module: 含 Worker(..., activities=[...]) 调用的 worker 模块。
    activities_modules: 定义 @activity.defn 的模块列表（支持多模块）。
    不等则 AssertionError 报 missing / extra diff（pytest 友好）。
    """
    expected: set[str] = set()
    for mod in activities_modules:
        expected |= _activity_def_names(Path(mod.__file__).read_text(encoding="utf-8"))
    registered = _registered_activity_names(
        Path(worker_module.__file__).read_text(encoding="utf-8")
    )
    missing = expected - registered
    extra = registered - expected
    assert not missing and not extra, (
        f"activity registration mismatch in {worker_module.__name__}: "
        f"missing (defined but not registered)={sorted(missing)}, "
        f"extra (registered but not defined)={sorted(extra)}"
    )
```

- [ ] **Step 4: 跑测试确认 pass**

Run: `uv run pytest packages/core/tests/test_activity_registration.py -v`
Expected: PASS（5 个用例全绿）。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/testing/__init__.py \
        packages/core/src/shannon_core/testing/activity_registration.py \
        packages/core/tests/test_activity_registration.py
git commit -m "feat(core): activity 注册完整性护栏 assert_all_activities_registered"
```

---

## Task 2: blackbox 护栏测试 + 修 worker.py 补 finalize_report

**Files:**
- Modify: `packages/blackbox/tests/test_worker.py`（末尾追加测试）
- Modify: `packages/blackbox/src/shannon_blackbox/worker.py`（import 段 line 7-19 + activities 列表 line 84-90）

**Interfaces:**
- Consumes: Task 1 的 `assert_all_activities_registered(worker_module, activities_modules)`。

- [ ] **Step 1: 加 blackbox 护栏测试（先红）**

在 `packages/blackbox/tests/test_worker.py` 末尾追加：

```python
def test_all_activities_registered():
    """护栏：worker.py 必须注册 pipeline/activities.py 里所有 @activity.defn。

    防 finalize_report 式漏注册（temporal NotFoundError → production retry 卡死~24h）。
    见 docs/superpowers/specs/2026-06-29-blackbox-finalize-report-worker-registration-design.md。
    """
    from shannon_core.testing.activity_registration import assert_all_activities_registered
    from shannon_blackbox import worker
    from shannon_blackbox.pipeline import activities

    assert_all_activities_registered(worker, [activities])
```

- [ ] **Step 2: 跑测试确认 fail（证明护栏能抓到 bug）**

Run: `uv run pytest packages/blackbox/tests/test_worker.py::test_all_activities_registered -v`
Expected: FAIL —— `AssertionError: activity registration mismatch ... missing (defined but not registered)=['finalize_report'] ...`

- [ ] **Step 3: 修 worker.py（转绿）—— 两处同步**

(a) import 段加 `finalize_report`。把 `packages/blackbox/src/shannon_blackbox/worker.py` 的 import 块改为：

```python
from .pipeline.activities import (
    run_blackbox_preflight,
    run_blackbox_auth_validation,
    run_recon,
    run_exploit_agent,
    validate_exploitation_queue,
    assemble_report,
    run_report_agent,
    finalize_report,
    log_phase_start_activity,
    log_phase_complete_activity,
    log_info_activity,
    load_correlation_context,
)
```

(b) `Worker(...)` 的 activities 列表加 `finalize_report`：

```python
        activities=[
            run_blackbox_preflight, run_blackbox_auth_validation, run_recon,
            run_exploit_agent, validate_exploitation_queue, assemble_report, run_report_agent,
            finalize_report,
            log_phase_start_activity, log_phase_complete_activity,
            log_info_activity,
            load_correlation_context,
        ],
```

- [ ] **Step 4: 跑测试确认 pass**

Run: `uv run pytest packages/blackbox/tests/test_worker.py::test_all_activities_registered -v`
Expected: PASS。

- [ ] **Step 5: 回归 blackbox test_worker.py 全文件（确认未破坏既有测试）**

Run: `uv run pytest packages/blackbox/tests/test_worker.py -v`
Expected: PASS（既有 5 个 + 新增 1 个，全绿；既有测试用 mock，不受 worker.py 改动影响）。

- [ ] **Step 6: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/worker.py \
        packages/blackbox/tests/test_worker.py
git commit -m "fix(blackbox): 注册 finalize_report activity，修 reporting 卡死"
```

---

## Task 3: whitebox 复用护栏测试

**Files:**
- Modify: `packages/whitebox/tests/test_worker.py`（末尾追加测试）

**Interfaces:**
- Consumes: Task 1 的 `assert_all_activities_registered(worker_module, activities_modules)`。

- [ ] **Step 1: 加 whitebox 护栏测试**

在 `packages/whitebox/tests/test_worker.py` 末尾追加：

```python
def test_all_activities_registered():
    """护栏：worker.py 必须注册 pipeline/activities.py 里所有 @activity.defn。

    whitebox 当前 23/23 全齐，本测试作为防未来漏注册的保护（无行为变更）。
    """
    from shannon_core.testing.activity_registration import assert_all_activities_registered
    from shannon_whitebox import worker
    from shannon_whitebox.pipeline import activities

    assert_all_activities_registered(worker, [activities])
```

- [ ] **Step 2: 跑测试确认 pass**

Run: `uv run pytest packages/whitebox/tests/test_worker.py::test_all_activities_registered -v`
Expected: PASS（whitebox 23/23 全齐）。
> 若 FAIL：说明 whitebox 也有漏注册（mismatch 报告会点名），按报告在 `packages/whitebox/src/shannon_whitebox/worker.py` 同步补注册——但当前 AST 比对显示 whitebox 干净，预期直接绿。

- [ ] **Step 3: Commit**

```bash
git add packages/whitebox/tests/test_worker.py
git commit -m "test(whitebox): 加 activity 注册完整性护栏（复用 core helper）"
```

---

## 善后（非 task，交付前提醒）

- 当前卡住的旧 workflow `NodeGoat_20260628-023125-rerun-20260629-003708` 仍在后台重试。worker 与 client 同进程，**Ctrl+C 杀进程**即停。重跑用新 `run_ts` 起新 workflow id，互不影响。
- 修复后真机冒烟（spec §6 验收标准）：重跑 `uv run shannon-blackbox start --url http://192.168.100.188:4000 --repo /Users/mango/project/vuln-range/NodeGoat -w NodeGoat_20260628-023125 --rerun`，观察 reporting 阶段 `finalize_report` 正常执行、workflow 正常 `return` 退出、`activity_failures.log` 不再出现 `finalize_report is not registered`。
- 报告主体 `comprehensive_security_assessment_report.md` 此前已生成（54KB），冒烟重点看是否正常收尾退出，不必重看报告内容。

## Self-Review

- [x] **Spec coverage**：spec §3.1（worker.py 补注册）→ Task 2；§3.2（core helper）→ Task 1；§3.3（blackbox 护栏）→ Task 2；§3.4（whitebox 护栏）→ Task 3；§3.5 TDD 顺序 → Task 2 先红(fail missing)后绿、Task 1/3 各自 TDD；§3.6 测试范围 → 每个 task 的 Run 仅跑点名文件；§4 善后 → 善后段；§5 非目标 → Global Constraints。全覆盖。
- [x] **Placeholder scan**：无 TBD/TODO；每步含完整代码或精确命令 + expected。
- [x] **Type consistency**：`assert_all_activities_registered(worker_module, activities_modules)` 签名在 Task 1 定义、Task 2/3 一致调用；`finalize_report` 拼写全程一致。
