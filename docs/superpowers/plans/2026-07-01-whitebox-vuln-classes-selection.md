# 白盒 vuln 类选择控制 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 `shannon-whitebox start` 加 `--vuln-classes` CLI 选项（逗号分隔）+ `SHANNON_VULN_CLASSES` env，优先级链 CLI > env > YAML > 默认全跑，并顺手修通白盒 YAML `vuln_classes` 当前不生效的断链。

**Architecture:** 两个纯函数集中优先级逻辑——`resolve_vuln_classes` 合并「字符串来源」（CLI/env，CLI 优先）、`select_vuln_classes` 合并「list 来源」（override > YAML > 默认）。CLI 层读 env + 调 resolve，把 override 以 list 形式塞进 `PipelineInput.vuln_classes`；workflow 层调 `select_vuln_classes(input.vuln_classes, cfg.vuln_classes)` 修通 YAML 这一环。

**Tech Stack:** Python 3.12+、click（CLI）、pydantic（YAML Config）、temporalio（workflow）、pytest（TDD）

## Global Constraints

- **双轨独立性**（CLAUDE.md §1）：只动 vuln 类「调度选择」，不改 LLM 轨 / GitNexus 轨内部逻辑、不喂确定性产物给 LLM 轨 prompt。
- **workflow sandbox 不变量**：env 解析只在 CLI 层，`workflow.run()` 内禁读 env（override 以 list 进 `PipelineInput.vuln_classes`）。
- **黑盒不受影响**：黑盒 `--vuln-classes`（multiple）行为不变。
- **多仓库不受影响**：`shannon-multi` / `MultiRepoConfig` / `RepoSpec.scan_config` 一字不改。
- **测试纪律**（memory `pytest-whitebox-hang`）：只跑改动相关测试文件（命令已给出 `-k` / 单文件），**勿跑全套**（会卡 Temporal/网络慢测试）。
- **vuln 类合法值**：`injection, xss, auth, authz, ssrf`（严格小写匹配，不归一）。
- **`ALL_VULN_CLASSES` 来源**：`vuln_selection.py` 统一用 `from shannon_core.models.config import ALL_VULN_CLASSES`（值 `["injection","xss","auth","authz","ssrf"]`）；勿用 `models/agents.py` 那份（顺序不同）。

## File Structure

| 文件 | 职责 | 动作 |
|------|------|------|
| `packages/core/src/shannon_core/config/vuln_selection.py` | 优先级链纯函数（resolve/select/解析校验/异常） | **Create** |
| `packages/core/tests/test_vuln_selection.py` | 纯函数单测 | **Create** |
| `packages/whitebox/src/shannon_whitebox/cli/main.py` | `--vuln-classes` option + env 读取 + resolve + 传 PipelineInput | **Modify** |
| `packages/whitebox/tests/test_cli.py` | CLI 优先级集成测试 | **Modify**（追加） |
| `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | cfg 解析提前 + `select_vuln_classes` 调用（修通断链） | **Modify** |
| `packages/whitebox/tests/test_workflows.py` | 修通防回退 AST 锚点 | **Modify**（追加） |
| `packages/whitebox/src/shannon_whitebox/pipeline/shared.py` | `ActivityInput` 加 `vuln_classes` 字段（Task 4 可选） | **Modify** |
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | `assemble_report` 用 `input.vuln_classes`（Task 4 可选） | **Modify** |

## Task 依赖

Task 1（纯函数）→ Task 2（CLI）、Task 3（workflow）可并行 → Task 4（可选，依赖 Task 3 的 `selected_classes`）。

---

### Task 1: vuln 类选择纯函数模块

**Files:**
- Create: `packages/core/src/shannon_core/config/vuln_selection.py`
- Test: `packages/core/tests/test_vuln_selection.py`

**Interfaces:**
- Produces（后续 task 依赖的签名，必须逐字一致）:
  - `class InvalidVulnClass(ValueError)` — 非法 vuln 类异常
  - `def resolve_vuln_classes(cli_str: str | None, env_str: str | None, *, allowed: Sequence[str] = ALL_VULN_CLASSES) -> list[str] | None`
  - `def select_vuln_classes(override: list[str] | None, yaml_vuln: list[str] | None, *, default: Sequence[str] = ALL_VULN_CLASSES) -> list[str]`

- [ ] **Step 1: 写失败测试**

Create `packages/core/tests/test_vuln_selection.py`:

```python
"""vuln 类选择纯函数：优先级链 CLI > env > YAML > 默认，集中可测、黑白盒可复用。"""
import pytest

from shannon_core.config.vuln_selection import (
    InvalidVulnClass,
    resolve_vuln_classes,
    select_vuln_classes,
)
from shannon_core.models.config import ALL_VULN_CLASSES


class TestResolveVulnClasses:
    """合并字符串来源（CLI/env）：CLI 优先，都空返回 None。"""

    def test_cli_takes_precedence_over_env(self):
        assert resolve_vuln_classes("injection", "xss") == ["injection"]

    def test_env_used_when_cli_absent(self):
        assert resolve_vuln_classes(None, "xss,auth") == ["xss", "auth"]

    def test_cli_empty_string_falls_through_to_env(self):
        assert resolve_vuln_classes("", "xss") == ["xss"]

    def test_both_none_returns_none(self):
        assert resolve_vuln_classes(None, None) is None

    def test_both_empty_returns_none(self):
        assert resolve_vuln_classes("", "") is None

    def test_comma_split_and_trim(self):
        assert resolve_vuln_classes(" injection , xss ", None) == ["injection", "xss"]

    def test_empty_tokens_dropped(self):
        assert resolve_vuln_classes("injection,,xss,", None) == ["injection", "xss"]

    def test_duplicates_deduped_order_preserved(self):
        assert resolve_vuln_classes("xss,injection,xss", None) == ["xss", "injection"]

    def test_invalid_class_raises_with_legal_list(self):
        with pytest.raises(InvalidVulnClass) as exc:
            resolve_vuln_classes("injection,foo", None)
        msg = str(exc.value)
        assert "foo" in msg
        for legal in ALL_VULN_CLASSES:
            assert legal in msg

    def test_invalid_in_env_raises(self):
        with pytest.raises(InvalidVulnClass):
            resolve_vuln_classes(None, "nope")


class TestSelectVulnClasses:
    """合并 list 来源：override（CLI/env 已解析）> YAML > 默认全跑。"""

    def test_override_takes_precedence(self):
        assert select_vuln_classes(["injection"], ["xss"]) == ["injection"]

    def test_empty_override_falls_through_to_yaml(self):
        # 空列表 falsy，视同未指定
        assert select_vuln_classes([], ["xss"]) == ["xss"]

    def test_none_override_uses_yaml(self):
        assert select_vuln_classes(None, ["xss", "auth"]) == ["xss", "auth"]

    def test_both_none_uses_default(self):
        assert select_vuln_classes(None, None) == list(ALL_VULN_CLASSES)

    def test_default_covers_all_five_classes(self):
        result = select_vuln_classes(None, None)
        assert set(result) == {"injection", "xss", "auth", "authz", "ssrf"}

    def test_default_not_mutated(self):
        before = list(ALL_VULN_CLASSES)
        select_vuln_classes(None, None)
        select_vuln_classes(["xss"], None)
        assert ALL_VULN_CLASSES == before

    def test_returns_copy_not_alias(self):
        yaml_vuln = ["xss"]
        result = select_vuln_classes(None, yaml_vuln)
        result.append("injection")
        assert yaml_vuln == ["xss"]
```

- [ ] **Step 2: 跑测试确认 fail**

Run: `pytest packages/core/tests/test_vuln_selection.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shannon_core.config.vuln_selection'`

- [ ] **Step 3: 写实现**

Create `packages/core/src/shannon_core/config/vuln_selection.py`:

```python
"""vuln 类选择纯函数：集中优先级链 CLI > env > YAML > 默认。

两个函数分层负责：
- resolve_vuln_classes: 合并「字符串来源」（CLI/env），返回 list 或 None。
- select_vuln_classes: 合并「list 来源」（override > YAML > 默认）。

env 必须在 CLI 层读取（workflow sandbox 不变量：workflow.run() 内禁 env 解析），
resolve 出的 override 以 list 形式进 PipelineInput.vuln_classes。
"""
from typing import Sequence

from shannon_core.models.config import ALL_VULN_CLASSES


class InvalidVulnClass(ValueError):
    """CLI/env 指定了不存在的 vuln 类。"""


def _parse_and_validate(raw: str, allowed: Sequence[str]) -> list[str]:
    """逗号分隔 → trim → 去空串 → 保序去重 → 校验每个 ∈ allowed。"""
    items: list[str] = []
    seen: set[str] = set()
    for token in raw.split(","):
        v = token.strip()
        if not v:
            continue
        if v not in allowed:
            raise InvalidVulnClass(
                f"未知的 vuln 类 {v!r}；合法值：{', '.join(allowed)}"
            )
        if v not in seen:
            seen.add(v)
            items.append(v)
    return items


def resolve_vuln_classes(
    cli_str: str | None,
    env_str: str | None,
    *,
    allowed: Sequence[str] = ALL_VULN_CLASSES,
) -> list[str] | None:
    """合并字符串来源：CLI > env。两者都空 → None（由调用方兜底 YAML/默认）。"""
    for raw in (cli_str, env_str):
        if raw and raw.strip():
            return _parse_and_validate(raw, allowed)
    return None


def select_vuln_classes(
    override: list[str] | None,
    yaml_vuln: list[str] | None,
    *,
    default: Sequence[str] = ALL_VULN_CLASSES,
) -> list[str]:
    """合并 list 来源：override（CLI/env 已解析）> YAML > 默认全跑。"""
    if override:
        return list(override)
    if yaml_vuln:
        return list(yaml_vuln)
    return list(default)
```

- [ ] **Step 4: 跑测试确认 pass**

Run: `pytest packages/core/tests/test_vuln_selection.py -v`
Expected: PASS（17 个测试全绿）

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/config/vuln_selection.py packages/core/tests/test_vuln_selection.py
git commit -m "feat(core): vuln 类选择纯函数 resolve/select（优先级链 CLI>env>YAML>默认）"
```

---

### Task 2: 白盒 CLI `--vuln-classes` + env

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/cli/main.py`（顶部 import + `start` 签名 + PipelineInput 构造）
- Test: `packages/whitebox/tests/test_cli.py`（追加 4 个测试）

**Interfaces:**
- Consumes: `resolve_vuln_classes`, `InvalidVulnClass`（Task 1）
- Produces: `start` 命令接受 `--vuln-classes`（逗号分隔）；`PipelineInput.vuln_classes` 被 CLI/env override 填充（None 时落回 YAML/默认，由 Task 3 处理）

- [ ] **Step 1: 写失败测试**

在 `packages/whitebox/tests/test_cli.py` 末尾追加：

```python
def _patch_start_env(monkeypatch, tmp_path):
    """patch 掉 start 命令里的 infra/prereq/run_scan，返回捕获 PipelineInput 的 dict。"""
    from unittest.mock import AsyncMock

    captured: dict = {}

    def fake_run_scan(input, *args, **kwargs):
        captured["vuln_classes"] = input.vuln_classes
        return {
            "status": "completed",
            "workspace_name": "ws",
            "deliverables_path": str(tmp_path),
            "web_url": "",
        }

    # cli/main.py:49 是函数内 `from shannon_whitebox.worker import run_scan`，
    # patch worker 模块源头即可被函数内 import 取到。
    monkeypatch.setattr("shannon_whitebox.worker.run_scan", fake_run_scan)
    # ensure_infra 是顶部 import，绑定在 cli.main 命名空间。
    monkeypatch.setattr("shannon_whitebox.cli.main.ensure_infra", AsyncMock(return_value=None))
    # ensure_prerequisite 是函数内 import（line 67），patch 源头模块。
    monkeypatch.setattr(
        "shannon_core.runtime.prerequisites.ensure_prerequisite",
        lambda *a, **k: None,
    )
    return captured


def test_start_vuln_classes_option_sets_pipeline_input(monkeypatch, tmp_path):
    """--vuln-classes 逗号分隔 → PipelineInput.vuln_classes。"""
    captured = _patch_start_env(monkeypatch, tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["start", "-r", str(repo), "--vuln-classes", "injection,xss", "--plain"],
    )
    assert result.exit_code == 0, result.output
    assert captured["vuln_classes"] == ["injection", "xss"]


def test_start_vuln_classes_env_sets_pipeline_input(monkeypatch, tmp_path):
    """SHANNON_VULN_CLASSES env → PipelineInput.vuln_classes。"""
    monkeypatch.setenv("SHANNON_VULN_CLASSES", "injection,ssrf")
    captured = _patch_start_env(monkeypatch, tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()

    runner = CliRunner()
    result = runner.invoke(cli, ["start", "-r", str(repo), "--plain"])
    assert result.exit_code == 0, result.output
    assert captured["vuln_classes"] == ["injection", "ssrf"]


def test_start_vuln_classes_cli_overrides_env(monkeypatch, tmp_path):
    """CLI > env 优先。"""
    monkeypatch.setenv("SHANNON_VULN_CLASSES", "ssrf")
    captured = _patch_start_env(monkeypatch, tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["start", "-r", str(repo), "--vuln-classes", "xss", "--plain"],
    )
    assert result.exit_code == 0, result.output
    assert captured["vuln_classes"] == ["xss"]


def test_start_vuln_classes_invalid_raises_usage_error(monkeypatch, tmp_path):
    """非法 vuln 类 → click.UsageError（exit_code != 0，提示含合法值）。"""
    captured = _patch_start_env(monkeypatch, tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["start", "-r", str(repo), "--vuln-classes", "injection,foo", "--plain"],
    )
    assert result.exit_code != 0
    assert "foo" in result.output
    # run_scan 不应被调用（解析在构造 PipelineInput 前就失败）
    assert "vuln_classes" not in captured
```

- [ ] **Step 2: 跑测试确认 fail**

Run: `pytest packages/whitebox/tests/test_cli.py -v -k vuln_classes`
Expected: FAIL — `Error: No such option: --vuln-classes`

- [ ] **Step 3: 改 cli/main.py**

3a. 顶部 import 段（第 1-6 行附近）加 `import os` 与 vuln_selection：

```python
import asyncio
import json
import os
import time

import click
from pathlib import Path

from shannon_core.config.env_loader import load_env
from shannon_core.config.profile_validator import validate_active_profile
from shannon_core.config.concurrency import get_max_concurrent, is_llm_track_enabled
from shannon_core.config.vuln_selection import resolve_vuln_classes, InvalidVulnClass
```

3b. `start` 命令加 option（在现有 `--debug` option 之后、`def start(` 之前加一行）：

```python
@click.option("--debug", is_flag=True, help="扫描失败时在终端打印完整堆栈（调试用）")
@click.option(
    "--vuln-classes", "vuln_classes_cli", default=None,
    help="逗号分隔的 vuln 类（如 injection,xss）；优先于 SHANNON_VULN_CLASSES env 与 YAML vuln_classes。"
)
def start(repo, output, workspace, config_path, pipeline_testing, temporal_address, plain, url, fresh, rewind, debug, vuln_classes_cli):
```

3c. 在 `def start(...)` 函数体内、`fresh/rewind` 互斥检查之后、`from shannon_whitebox.worker import run_scan` 之前，插入 resolve 逻辑；并把 `vuln_classes=override` 加入 `PipelineInput(...)` 构造。

把这一段：

```python
    if fresh and rewind:
        raise click.UsageError("--fresh 与 --rewind 互斥，不能同时使用。")
    from shannon_whitebox.worker import run_scan

    input = PipelineInput(
        repo_path=str(Path(repo).resolve()),
        web_url=url or "",
        output_path=str(Path(output).resolve()) if output else None,
        workspace_name=workspace,
        config_path=config_path,
        pipeline_testing_mode=pipeline_testing,
        max_concurrent=get_max_concurrent(),
        enable_llm_track=is_llm_track_enabled(),
    )
```

改为：

```python
    if fresh and rewind:
        raise click.UsageError("--fresh 与 --rewind 互斥，不能同时使用。")

    # vuln 类优先级链: CLI > env（YAML/默认在 workflow 层 select_vuln_classes 兜底）。
    # env 在 CLI 层读（workflow sandbox 不变量：workflow.run() 内禁 env 解析）。
    try:
        override = resolve_vuln_classes(
            vuln_classes_cli,
            os.environ.get("SHANNON_VULN_CLASSES"),
        )
    except InvalidVulnClass as e:
        raise click.UsageError(str(e)) from e

    from shannon_whitebox.worker import run_scan

    input = PipelineInput(
        repo_path=str(Path(repo).resolve()),
        web_url=url or "",
        output_path=str(Path(output).resolve()) if output else None,
        workspace_name=workspace,
        config_path=config_path,
        pipeline_testing_mode=pipeline_testing,
        max_concurrent=get_max_concurrent(),
        enable_llm_track=is_llm_track_enabled(),
        vuln_classes=override,
    )
```

- [ ] **Step 4: 跑测试确认 pass**

Run: `pytest packages/whitebox/tests/test_cli.py -v -k vuln_classes`
Expected: PASS（4 个新测试绿）

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/cli/main.py packages/whitebox/tests/test_cli.py
git commit -m "feat(whitebox): CLI --vuln-classes + SHANNON_VULN_CLASSES env（CLI>env 优先）"
```

---

### Task 3: workflow 修通 YAML vuln_classes 断链 ★

> 这是本计划的核心价值点：修通 pre-existing 断链，让 `cfg.vuln_classes` 真正参与 `selected_classes` 决定（当前 `workflows.py:40` 只看 `input.vuln_classes`，第 99 行 parse_config 后从不引用 `cfg.vuln_classes`）。`selected_classes` 在 317 行驱动 vuln agent 调度，修通会真正改变跑哪些 agent。

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`（顶部 import + 第 40 行 + 第 96-103 行）
- Test: `packages/whitebox/tests/test_workflows.py`（追加防回退锚点）

**Interfaces:**
- Consumes: `select_vuln_classes`（Task 1）
- Produces: `WhiteboxScanWorkflow.run` 用 `select_vuln_classes(input.vuln_classes, cfg.vuln_classes)` 解析 `selected_classes`

- [ ] **Step 1: 写失败测试**

在 `packages/whitebox/tests/test_workflows.py` 末尾追加：

```python
def test_workflow_run_resolves_vuln_classes_via_select_function():
    """防回退: selected_classes 必须经 select_vuln_classes(input.vuln_classes, cfg.vuln_classes) 解析。

    旧断链形式 `input.vuln_classes or list(ALL_VULN_CLASSES)` 会让 cfg 解析后的
    cfg.vuln_classes 被丢弃（YAML vuln_classes 不生效）。本锚点守住修通成果。
    spec docs/superpowers/specs/2026-07-01-whitebox-vuln-classes-selection-design.md §2.3/§4.3。
    """
    import inspect

    from shannon_whitebox.pipeline.workflows import WhiteboxScanWorkflow

    src = inspect.getsource(WhiteboxScanWorkflow.run)
    assert "select_vuln_classes" in src, "run() 必须调用 select_vuln_classes"
    assert "cfg.vuln_classes" in src, "run() 必须把 cfg.vuln_classes 传给 select_vuln_classes"
    assert (
        "input.vuln_classes or list(ALL_VULN_CLASSES)" not in src
    ), "不得回退到旧断链形式（丢失 YAML vuln_classes）"
```

- [ ] **Step 2: 跑测试确认 fail**

Run: `pytest packages/whitebox/tests/test_workflows.py::test_workflow_run_resolves_vuln_classes_via_select_function -v`
Expected: FAIL — `AssertionError: run() 必须调用 select_vuln_classes`

- [ ] **Step 3: 改 workflows.py**

3a. 顶部 import 段（第 8 行 `from shannon_core.models.agents import ...` 之后）加：

```python
from shannon_core.config.vuln_selection import select_vuln_classes
```

3b. 把 `cfg` 解析提前到 `selected_classes` 之前，并用 `select_vuln_classes`。把第 38-40 行：

```python
        self._state.start_time = workflow.time_ns() / 1e9

        selected_classes: list[VulnType] = input.vuln_classes or list(ALL_VULN_CLASSES)
```

改为：

```python
        self._state.start_time = workflow.time_ns() / 1e9

        # Resolve config (YAML) early so vuln-class selection can consult cfg.vuln_classes.
        cfg = None
        if input.config_path:
            from shannon_core.config.parser import parse_config
            cfg = parse_config(input.config_path)

        # vuln 类优先级链: CLI/env override(经 input.vuln_classes) > YAML(cfg.vuln_classes) > 默认全跑。
        # 修通 pre-existing 断链（旧: input.vuln_classes or ALL_VULN_CLASSES，丢弃 cfg.vuln_classes）。
        selected_classes: list[VulnType] = select_vuln_classes(
            input.vuln_classes,
            cfg.vuln_classes if cfg else None,
        )
```

3c. 把原第 96-103 行重复的 cfg 解析块改为只保留 engine 初始化（cfg 已在 3b 解析）：

```python
        # Resolve browser engine (cfg 已在 run() 开头解析)
        engine = None
        engine_name = cfg.browser_engine if cfg else "playwright"
```

（即删除原 `cfg = None` / `if input.config_path: cfg = parse_config(...)` 三行，因为已提前；保留 `engine = None` 与 `engine_name = ...`。）

- [ ] **Step 4: 跑测试确认 pass**

Run: `pytest packages/whitebox/tests/test_workflows.py::test_workflow_run_resolves_vuln_classes_via_select_function -v`
Expected: PASS

补充回归（确保没碰坏 workflow 其他逻辑）：`pytest packages/whitebox/tests/test_workflows.py -v`
Expected: 既有测试仍绿（本 task 只改 selected_classes 计算顺序 + cfg 提前，不改后续逻辑）。

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/workflows.py packages/whitebox/tests/test_workflows.py
git commit -m "fix(whitebox): 修通 YAML vuln_classes 断链（selected_classes 经 select_vuln_classes 解析）"
```

---

### Task 4: [Optional / Minor] report assemble 用 selected_classes

> **可选**。`ReportAssembler.assemble` 对缺失文件 tolerant（`if await async_path_exists(...)`），故 `activities.py:830` 硬编码 `ALL_VULN_CLASSES` 当前**无害**（没跑的类无产物，自动跳过）。本 task 仅求语义一致（report 视角 = 实际 selected），不阻塞主特性。执行者可跳过，留作 follow-up。

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/shared.py`（`ActivityInput` 加字段）
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py:474`（assemble_report 调用传参）
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:830`
- Test: `packages/whitebox/tests/test_workflows.py`（追加锚点）

**Interfaces:**
- Consumes: Task 3 的 `selected_classes`（workflow 作用域变量）
- Produces: `assemble_report` activity 读 `input.vuln_classes` 而非硬编码

- [ ] **Step 1: 写失败测试**

在 `packages/whitebox/tests/test_workflows.py` 末尾追加：

```python
def test_assemble_report_reads_vuln_classes_from_input():
    """assemble_report 应从 input.vuln_classes 读（默认 ALL），不再硬编码。"""
    import inspect

    from shannon_whitebox.pipeline.activities import assemble_report

    src = inspect.getsource(assemble_report)
    assert "input.vuln_classes" in src, "assemble_report 必须读 input.vuln_classes"


def test_activity_input_has_vuln_classes_field():
    """ActivityInput 必须有 vuln_classes 字段（默认 None），供 assemble_report 接收 selected。"""
    from shannon_whitebox.pipeline.shared import ActivityInput

    ai = ActivityInput(repo_path="/tmp/x")
    assert hasattr(ai, "vuln_classes")
    assert ai.vuln_classes is None
```

- [ ] **Step 2: 跑测试确认 fail**

Run: `pytest packages/whitebox/tests/test_workflows.py::test_assemble_report_reads_vuln_classes_from_input packages/whitebox/tests/test_workflows.py::test_activity_input_has_vuln_classes_field -v`
Expected: FAIL（`input.vuln_classes` 不在 src / `ActivityInput` 无 vuln_classes 属性）

- [ ] **Step 3a: shared.py — ActivityInput 加字段**

`packages/whitebox/src/shannon_whitebox/pipeline/shared.py` 的 `ActivityInput` dataclass（约第 38-52 行）末尾加一行字段：

```python
    info_message: str | None = None   # log_info_activity 用户提示（替代 workflow.logger.info→stderr 抢行）
    info_level: str = "info"          # "info" | "warning"
    vuln_classes: list[str] | None = None   # assemble_report 用（默认 ALL，由 workflow 传 selected）
```

- [ ] **Step 3b: workflows.py:474 — assemble_report 调用传 selected_classes**

把第 474 行附近：

```python
                    await workflow.execute_activity(
                        activities.assemble_report, act_input,
                        ...
                    )
```

改为（用展开的 ActivityInput 附加 vuln_classes）：

```python
                    await workflow.execute_activity(
                        activities.assemble_report,
                        ActivityInput(**{**act_input.__dict__,
                                         "vuln_classes": [str(vt) for vt in selected_classes]}),
                        ...
                    )
```

（保持该 `execute_activity` 调用原有的 `start_to_close_timeout` / `retry_policy` 等参数不变，只改第二个位置参数。）

- [ ] **Step 3c: activities.py:830 — 用 input.vuln_classes**

把 `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:830`：

```python
        vuln_classes = list(ALL_VULN_CLASSES)
```

改为：

```python
        vuln_classes = input.vuln_classes or list(ALL_VULN_CLASSES)
```

- [ ] **Step 4: 跑测试确认 pass**

Run: `pytest packages/whitebox/tests/test_workflows.py::test_assemble_report_reads_vuln_classes_from_input packages/whitebox/tests/test_workflows.py::test_activity_input_has_vuln_classes_field -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/shared.py packages/whitebox/src/shannon_whitebox/pipeline/workflows.py packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/tests/test_workflows.py
git commit -m "refactor(whitebox): report assemble 用 selected vuln_classes（语义一致，tolerant 故无行为变化）"
```

---

## Smoke Verification（人工冒烟，自动化测试不覆盖）

实现完成后，在真仓库跑一次确认链路通（memory 各 plan 惯例：单元绿 ≠ 真机通）：

1. **CLI 默认全跑**（不传）：`shannon-whitebox start -r <repo> -w sm-default --plain` → 终端 Results summary 应列出全部存在的 vuln queue。
2. **CLI 子集**：`shannon-whitebox start -r <repo> -w sm-cli --vuln-classes injection,xss --plain` → 只跑 injection/xss（观察日志 vuln 阶段只出现 `injection-vuln` / `xss-vuln`，无 auth/authz/ssrf）。
3. **env 子集**：`SHANNON_VULN_CLASSES=injection,ssrf shannon-whitebox start -r <repo> -w sm-env --plain` → 只跑 injection/ssrf。
4. **CLI > env**：`SHANNON_VULN_CLASSES=ssrf shannon-whitebox start -r <repo> -w sm-override --vuln-classes xss --plain` → 只跑 xss。
5. **YAML 生效**（修通断链验证）：写一个 `vuln_classes: [injection]` 的 YAML，`shannon-whitebox start -r <repo> -w sm-yaml -c that.yaml --plain` → 只跑 injection（**修通前会全跑，这是关键回归点**）。
6. **非法类**：`shannon-whitebox start -r <repo> --vuln-classes foo --plain` → 立即报错 `未知的 vuln 类 'foo'；合法值：...`，不启动扫描。
7. **多仓库关联回归**（确认没碰坏 multi）：`shannon-multi start -c <现有 multi-repo.yaml>` 仍能正常复用/关联。

---

## Self-Review

**1. Spec coverage**（逐条对照 spec）：
- §3 目标 1（CLI 逗号）→ Task 2 ✓
- §3 目标 2（env）→ Task 2 ✓
- §3 目标 3（优先级链 CLI>env>YAML>默认）→ Task 1（纯函数）+ Task 2（CLI>env）+ Task 3（YAML 接入）✓
- §3 目标 4（修通 YAML 断链）→ Task 3 ✓
- §3 目标 5（非法 fail fast）→ Task 1 `_parse_and_validate` + Task 2 UsageError ✓
- §4.1 两纯函数 → Task 1 ✓
- §4.2 CLI 改动 → Task 2 ✓
- §4.3 workflow 改动 → Task 3 ✓
- §4.4 report assemble（Minor）→ Task 4（可选）✓
- §5 边界情况 → Task 1 测试覆盖（逗号/trim/去空/去重/非法/大小写严格）✓
- §6 测试策略 → Task 1（纯函数）+ Task 2（cli 集成）+ Task 3（防回退锚点）✓
- §7 不变量（双轨/sandbox/黑盒/多仓库）→ Global Constraints + 各 task 不碰这些 ✓

**2. Placeholder scan**：无 TBD/TODO；每个 code step 都给了完整代码；测试都给了完整断言。✓

**3. Type consistency**：
- `resolve_vuln_classes(cli_str, env_str) -> list[str] | None`：Task 1 定义、Task 2 调用签名一致 ✓
- `select_vuln_classes(override, yaml_vuln) -> list[str]`：Task 1 定义、Task 3 调用 `select_vuln_classes(input.vuln_classes, cfg.vuln_classes if cfg else None)` 一致 ✓
- `InvalidVulnClass`：Task 1 定义、Task 2 `except InvalidVulnClass` 一致 ✓
- `PipelineInput.vuln_classes`：Task 2 写入 `vuln_classes=override`、Task 3 读取 `input.vuln_classes` 一致 ✓
- `ActivityInput.vuln_classes`：Task 4 加字段、activities.py 读取 `input.vuln_classes` 一致 ✓

**4. 顺序调整副作用**（spec §8 风险）：Task 3 把 `cfg` 解析提前。`parse_config` 是纯解析+校验（`parser.py:240-249`，无副作用、无 I/O 依赖时序），提前安全。原 `engine` 逻辑（103+）只是读已解析的 `cfg`，不受影响。✓

无遗留问题。
