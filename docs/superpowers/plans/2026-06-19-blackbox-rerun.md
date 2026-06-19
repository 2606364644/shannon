# 黑盒 Rerun 实现计划（Phase 2）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让黑盒扫描在跑完后能基于已有白盒结果整体重跑（`--rerun`），重跑时归档旧 evidence、用新 workflow id 规避 `AlreadyStarted`；默认跑黑盒前幂等检测——已跑过则告知用户、不重复跑。

**Architecture:** CLI 层做幂等检测（已跑过→告知+不启动 worker，早失败省 Temporal 连接）；worker 层在 `--rerun` 时归档旧 evidence 到 `deliverables/.blackbox-archive/<run_ts>/` + workflow id 加时间戳。归档文件清单复用 `session.py:210` 的 `bb_deliverable_patterns`。幂等信号用 evidence 文件存在性（黑盒 session.json 是 MetricsTracker 写的 nested `session.status`，无 top-level status，evidence 文件最直接可靠）。

**Tech Stack:** Python 3、Temporal.io、click CLI、pytest（全 mock Temporal）、uv workspace。

## Global Constraints

- 包结构：monorepo，`packages/blackbox` 依赖 core（`shannon-core` workspace 依赖）。
- 测试：`uv run pytest packages/blackbox/tests/`（**只跑黑盒子集**——全量会 hang）。`asyncio_mode = "auto"`，无 conftest.py。
- 测试陷阱：dispatch 用 `isinstance`，**禁止用 `MagicMock` 当 event**，用真实 dataclass。
- commit message：中文。
- 黑盒 worker **没有** `resolve_workflow_id`（id 内联 `worker.py:86`）、**没有** `SessionManager.create_workspace` 调用（session.json 由 MetricsTracker 写）。
- 已有可复用：`bb_deliverable_patterns`（`session.py:210`）、`resolve_deliverables_path` / `deliverables_dir_for_workspace`（`paths.py`）、`atomic_write_json`、白盒 `.whitebox-archive` 归档模式（`whitebox_resume.py:224`）。

---

## Implementation Notes（相对 spec §4 的调整，基于代码核实）

spec：`docs/superpowers/specs/2026-06-19-resume-and-rerun-design.md` §4。

1. **幂等检测信号用 evidence 文件存在性**（spec §4.5 原说 session status + evidence）。原因：黑盒 worker 不调 `SessionManager.create_workspace`，session.json 由 MetricsTracker 写，**只有 nested `session.status`**（`session.py:get_status` 能读，但 evidence 文件更直接）。`detect_blackbox_completed` 用 glob `*_exploitation_evidence.md` 存在判定。
2. **幂等检测放 CLI 层，归档+id 放 worker 层**（spec §4.2/4.3/4.4 未明确分层）。CLI 早失败（已跑过→告知、不启动 Temporal worker，省连接）；worker 在 `--rerun` 时归档+id。
3. **归档文件清单复用 `bb_deliverable_patterns`**（`session.py:210`），不用硬编码 11 个文件名。
4. **`BlackboxPipelineInput` 直接加 `rerun: bool` 字段**（不用 setattr，比白盒 `_fresh` 干净）。
5. **workflow id**：黑盒无 `resolve_workflow_id`，`--rerun` 时直接构造 `<base>-rerun-<ts>`（base = workspace_name 或 `blackbox-<ts>`）规避 `AlreadyStarted`。

---

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `packages/blackbox/src/shannon_blackbox/pipeline/blackbox_rerun.py` | `detect_blackbox_completed` + `archive_blackbox_deliverables`（纯函数） | 新建 |
| `packages/blackbox/src/shannon_blackbox/pipeline/shared.py` | `BlackboxPipelineInput` 加 `rerun: bool` 字段 | 修改 |
| `packages/blackbox/src/shannon_blackbox/cli/main.py` | 加 `--rerun` flag + 幂等检测 | 修改 |
| `packages/blackbox/src/shannon_blackbox/worker.py` | `--rerun` 时归档 + workflow id 加时间戳 | 修改 |
| `packages/blackbox/tests/test_blackbox_rerun.py` | detect + archive 测试 | 新建 |
| `packages/blackbox/tests/test_cli.py` | `--rerun` + 幂等检测测试 | 修改 |
| `packages/blackbox/tests/test_worker.py` | rerun 归档 + id 测试 | 修改 |

---

## Task 1: `blackbox_rerun.py` — detect + archive（纯函数）

**Files:**
- Create: `packages/blackbox/src/shannon_blackbox/pipeline/blackbox_rerun.py`
- Test: `packages/blackbox/tests/test_blackbox_rerun.py`（新建）

**Interfaces:**
- Produces:
  - `detect_blackbox_completed(deliverables: Path) -> bool`：glob `*_exploitation_evidence.md` 有文件 → True
  - `archive_blackbox_deliverables(deliverables: Path, run_ts: str) -> Path`：把 `bb_deliverable_patterns` 匹配的文件移到 `deliverables/.blackbox-archive/<run_ts>/`，返回归档目录

- [ ] **Step 1: 写失败测试**

```python
# packages/blackbox/tests/test_blackbox_rerun.py
from pathlib import Path

from shannon_blackbox.pipeline.blackbox_rerun import (
    detect_blackbox_completed,
    archive_blackbox_deliverables,
)


def test_detect_returns_false_when_no_evidence(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    # 只有白盒 queue 文件，无 evidence
    (deliverables / "injection_exploitation_queue.json").write_text("{}")

    assert detect_blackbox_completed(deliverables) is False


def test_detect_returns_true_when_evidence_exists(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "injection_exploitation_evidence.md").write_text("# evidence")

    assert detect_blackbox_completed(deliverables) is True


def test_archive_moves_blackbox_deliverables_to_dated_dir(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    # evidence + findings + report（覆盖 bb_deliverable_patterns 三类）
    (deliverables / "injection_exploitation_evidence.md").write_text("e")
    (deliverables / "ssrf_findings.md").write_text("f")
    (deliverables / "comprehensive_security_assessment_report.md").write_text("r")
    # 白盒文件不归档
    (deliverables / "injection_analysis_deliverable.md").write_text("keep")

    archive = archive_blackbox_deliverables(deliverables, "20260619-1530")

    assert archive == deliverables / ".blackbox-archive" / "20260619-1530"
    assert (archive / "injection_exploitation_evidence.md").exists()
    assert (archive / "ssrf_findings.md").exists()
    assert (archive / "comprehensive_security_assessment_report.md").exists()
    # 源文件已移走
    assert not (deliverables / "injection_exploitation_evidence.md").exists()
    # 白盒文件保留
    assert (deliverables / "injection_analysis_deliverable.md").exists()


def test_archive_handles_empty_deliverables(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()

    archive = archive_blackbox_deliverables(deliverables, "20260619-1530")

    assert archive.exists()  # 目录创建，即使无文件可移
    assert list(archive.iterdir()) == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/blackbox/tests/test_blackbox_rerun.py -v`
Expected: FAIL — `ImportError: cannot import name 'detect_blackbox_completed'`

- [ ] **Step 3: 实现**

```python
# packages/blackbox/src/shannon_blackbox/pipeline/blackbox_rerun.py
"""Blackbox scan rerun: idempotent detection + evidence archiving.

rerun 场景：白盒+黑盒跑完后，基于已有白盒结果整体重跑黑盒。
- detect_blackbox_completed: 判断是否已跑过黑盒（evidence 文件存在）
- archive_blackbox_deliverables: --rerun 时把旧黑盒产出物归档到 .blackbox-archive/<run_ts>/

幂等信号用 evidence 文件存在性（黑盒 session.json 是 MetricsTracker 写的 nested
session.status，无 top-level status；evidence 文件最直接可靠）。
归档文件清单复用 session.py 的 bb_deliverable_patterns。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from shannon_core.session import BB_DELIVERABLE_PATTERNS  # 见 Step 3b


def detect_blackbox_completed(deliverables: Path) -> bool:
    """Return True if any `*_exploitation_evidence.md` exists in deliverables."""
    return bool(list(deliverables.glob("*_exploitation_evidence.md")))


def archive_blackbox_deliverables(deliverables: Path, run_ts: str) -> Path:
    """Move blackbox deliverables (evidence/findings/report) to a dated archive dir.

    归档清单复用 bb_deliverable_patterns。白盒产出物（analysis_deliverable 等）不归档。
    返回归档目录 deliverables/.blackbox-archive/<run_ts>/。
    """
    archive = deliverables / ".blackbox-archive" / run_ts
    archive.mkdir(parents=True, exist_ok=True)
    for pattern in BB_DELIVERABLE_PATTERNS:
        for src in deliverables.glob(pattern):
            shutil.move(str(src), str(archive / src.name))
    return archive
```

- [ ] **Step 3b: 把 session.py 的 patterns 提为模块常量**

`session.py:210-216` 当前 `bb_deliverable_patterns` 是 `clean_workspace` 内的局部变量。提为模块级常量 `BB_DELIVERABLE_PATTERNS` 供复用（`clean_workspace` 改用模块常量）：

修改 `packages/core/src/shannon_core/session.py`，在 `clean_workspace` 函数之前（约 `:205` 前）加：

```python
BB_DELIVERABLE_PATTERNS: list[str] = [
    "*_exploitation_evidence.md",
    "*_findings.md",
    "comprehensive_security_assessment_report.md",
]
```

并把 `clean_workspace` 内的局部 `bb_deliverable_patterns = [...]` 删除，改用模块常量（`for pattern in BB_DELIVERABLE_PATTERNS:`）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest packages/blackbox/tests/test_blackbox_rerun.py -v`
Expected: PASS（4 个测试）

- [ ] **Step 5: 提交**

```bash
git add packages/blackbox/src/shannon_blackbox/pipeline/blackbox_rerun.py packages/blackbox/tests/test_blackbox_rerun.py packages/core/src/shannon_core/session.py
git commit -m "feat(rerun): blackbox_rerun detect+archive 纯函数（复用 bb_deliverable_patterns）"
```

---

## Task 2: `BlackboxPipelineInput` 加 `rerun` 字段

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/shared.py:7-14`
- Modify: `packages/blackbox/tests/test_pipeline_shared.py`（若存在，加字段断言；否则新建小测试）

**Interfaces:**
- Produces: `BlackboxPipelineInput.rerun: bool = False`

- [ ] **Step 1: 写失败测试**

追加到 `packages/blackbox/tests/test_pipeline_shared.py`（若文件不存在则新建）：

```python
from shannon_blackbox.pipeline.shared import BlackboxPipelineInput


def test_blackbox_input_has_rerun_field_default_false():
    inp = BlackboxPipelineInput(web_url="https://x.com")
    assert inp.rerun is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/blackbox/tests/test_pipeline_shared.py::test_blackbox_input_has_rerun_field_default_false -v`
Expected: FAIL — `AttributeError: 'BlackboxPipelineInput' has no attribute 'rerun'`

- [ ] **Step 3: 实现字段**

修改 `packages/blackbox/src/shannon_blackbox/pipeline/shared.py:7-14`：

```python
@dataclass
class BlackboxPipelineInput(BasePipelineInput):
    """Blackbox-specific fields."""
    web_url: str = ""
    repo_path: str | None = None
    exploit: bool = True
    max_concurrent: int = 3
    retry_profile: str | None = None
    rerun: bool = False  # 强制重跑黑盒（归档旧 evidence + 新 workflow id）
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest packages/blackbox/tests/test_pipeline_shared.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add packages/blackbox/src/shannon_blackbox/pipeline/shared.py packages/blackbox/tests/test_pipeline_shared.py
git commit -m "feat(rerun): BlackboxPipelineInput 加 rerun 字段（默认 False）"
```

---

## Task 3: CLI `--rerun` + 幂等检测

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/cli/main.py:31-44`（加 option + 函数签名）、`:135` 前加幂等检测
- Modify: `packages/blackbox/tests/test_cli.py`（加测试）

**Interfaces:**
- Consumes: `detect_blackbox_completed`（Task 1）、`resolve_deliverables_path`

- [ ] **Step 1: 写失败测试**

追加到 `packages/blackbox/tests/test_cli.py`：

```python
def test_start_informs_when_blackbox_already_ran(tmp_path, monkeypatch):
    """默认（非 --rerun）检测到已跑过黑盒 → 告知、不调 run_scan。"""
    from click.testing import CliRunner
    from unittest.mock import patch, AsyncMock
    from shannon_blackbox.cli.main import cli

    # 构造一个已有黑盒 evidence 的 deliverables（指向 repo）
    repo = tmp_path / "repo"
    deliverables = repo / ".shannon" / "deliverables"
    deliverables.mkdir(parents=True)
    (deliverables / "injection_exploitation_evidence.md").write_text("# done")

    run_scan_called = []
    async def fake_run_scan(input, temporal_address, use_rich=False):
        run_scan_called.append(True)
        return {"status": "completed"}

    with patch("shannon_blackbox.cli.main.ensure_infra", AsyncMock()), \
         patch("shannon_blackbox.worker.run_scan", side_effect=fake_run_scan):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "https://x.com", "-r", str(repo), "-w", "ws1"])

    assert result.exit_code == 0
    assert "已跑过" in result.output or "already" in result.output.lower()
    assert run_scan_called == []  # 没调 run_scan


def test_start_rerun_bypasses_idempotency(tmp_path, monkeypatch):
    """--rerun 跳过幂等检测，正常调 run_scan。"""
    from click.testing import CliRunner
    from unittest.mock import patch, AsyncMock
    from shannon_blackbox.cli.main import cli

    repo = tmp_path / "repo"
    deliverables = repo / ".shannon" / "deliverables"
    deliverables.mkdir(parents=True)
    (deliverables / "injection_exploitation_evidence.md").write_text("# old")

    run_scan_called = []
    captured = {}
    async def fake_run_scan(input, temporal_address, use_rich=False):
        run_scan_called.append(True)
        captured["rerun"] = input.rerun
        return {"status": "completed"}

    with patch("shannon_blackbox.cli.main.ensure_infra", AsyncMock()), \
         patch("shannon_blackbox.worker.run_scan", side_effect=fake_run_scan):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "https://x.com", "-r", str(repo), "-w", "ws1", "--rerun"])

    assert result.exit_code == 0
    assert run_scan_called == [True]
    assert captured["rerun"] is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/blackbox/tests/test_cli.py::test_start_informs_when_blackbox_already_ran packages/blackbox/tests/test_cli.py::test_start_rerun_bypasses_idempotency -v`
Expected: FAIL — `Error: No such option: --rerun`

- [ ] **Step 3: 加 option + 函数签名 + 幂等检测**

先读 `cli/main.py` 确认 `def start(...)` 签名（`:44`）+ workspace 解析（`:52-70`）+ input 构造（`:117-120`）+ run_scan 调用（`:135`）的精确位置。

(a) 加 option（在 `--plain` `:43` 之后）：

```python
@click.option("--rerun", is_flag=True, help="强制重跑黑盒（归档旧 evidence，基于已有白盒结果重新跑）")
```

(b) `def start(...)` 签名末尾加 `rerun` 参数。

(c) 在 `run_scan` 调用（`:135`）之前、input 构造之后，加幂等检测：

```python
    # 幂等检测：默认（非 --rerun）若已跑过黑盒 → 告知、不启动 worker
    if not rerun:
        from pathlib import Path as _Path
        from shannon_core.utils.paths import resolve_deliverables_path
        from shannon_blackbox.pipeline.blackbox_rerun import detect_blackbox_completed
        deliverables = resolve_deliverables_path(
            repo_path=str(_Path(repo).resolve()) if repo else None,
            deliverables_subdir=input.deliverables_subdir,
            workspace_name=resolved_workspace,
        )
        if detect_blackbox_completed(deliverables):
            click.echo(
                f"该 workspace 已跑过黑盒，结果在 {deliverables}。"
                f"如需重跑请加 --rerun（旧 evidence 会归档到 .blackbox-archive/）。"
            )
            return
    input.rerun = rerun
```

（`resolved_workspace` 是 CLI 里 workspace 解析后的变量名，确认实际变量名；若用 `-w workspace`，则 resolved_workspace = workspace。）

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest packages/blackbox/tests/test_cli.py -v`
Expected: PASS（含 2 新测试）

- [ ] **Step 5: 提交**

```bash
git add packages/blackbox/src/shannon_blackbox/cli/main.py packages/blackbox/tests/test_cli.py
git commit -m "feat(rerun): CLI --rerun flag + 默认幂等检测（已跑过告知不跑）"
```

---

## Task 4: worker `--rerun` 归档 + workflow id 时间戳

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/worker.py:69-88`（meta + start_workflow 区域）
- Modify: `packages/blackbox/tests/test_worker.py`（加 rerun 测试）

**Interfaces:**
- Consumes: `archive_blackbox_deliverables`（Task 1）、`input.rerun`（Task 2）、`resolve_deliverables_path`

- [ ] **Step 1: 写失败测试**

追加到 `packages/blackbox/tests/test_worker.py`（参考现有 mock 模式 `:7-45`）：

```python
@pytest.mark.asyncio
async def test_run_scan_rerun_archives_old_evidence_and_uses_new_id(tmp_path, monkeypatch):
    """--rerun 时：归档旧 evidence + workflow id 带 -rerun- 后缀。"""
    from pathlib import Path
    from unittest.mock import AsyncMock, patch
    from shannon_blackbox.worker import run_scan
    from shannon_blackbox.pipeline.shared import BlackboxPipelineInput

    repo = tmp_path / "repo"
    deliverables = repo / ".shannon" / "deliverables"
    deliverables.mkdir(parents=True)
    (deliverables / "injection_exploitation_evidence.md").write_text("# old")

    monkeypatch.setenv("SHANNON_WORKSPACES_DIR", str(tmp_path / "workspaces"))

    captured_wf_id = {}
    mock_handle = AsyncMock()
    mock_handle.result = AsyncMock(return_value=BlackboxPipelineInput.__dict__ and type("R", (), {"status": "completed"})())
    mock_client = AsyncMock()
    async def capture_start(wf, inp, id, task_queue):
        captured_wf_id["id"] = id
        return mock_handle
    mock_client.start_workflow = capture_start
    mock_worker = AsyncMock()
    mock_worker.__aenter__ = AsyncMock(return_value=None)
    mock_worker.__aexit__ = AsyncMock(return_value=None)

    inp = BlackboxPipelineInput(web_url="https://x.com", repo_path=str(repo), workspace_name="ws1", rerun=True)

    with patch("shannon_blackbox.worker.Client.connect", AsyncMock(return_value=mock_client)), \
         patch("shannon_blackbox.worker.Worker", return_value=mock_worker), \
         patch("shannon_blackbox.worker.ShutdownController.install"), \
         patch("shannon_blackbox.worker.ShutdownController.uninstall"), \
         patch("shannon_blackbox.worker.run_with_display") as mock_rwd:
        mock_rwd.return_value.__aenter__ = AsyncMock(return_value=AsyncMock(log_workflow_complete=AsyncMock(), log_workflow_header=AsyncMock()))
        mock_rwd.return_value.__aexit__ = AsyncMock(return_value=False)
        await run_scan(inp, "localhost:7233")

    # 归档了旧 evidence
    archive_dirs = list(deliverables.glob(".blackbox-archive/*"))
    assert len(archive_dirs) == 1
    assert (archive_dirs[0] / "injection_exploitation_evidence.md").exists()
    assert not (deliverables / "injection_exploitation_evidence.md").exists()
    # workflow id 带 -rerun- 后缀
    assert "-rerun-" in captured_wf_id["id"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/blackbox/tests/test_worker.py::test_run_scan_rerun_archives_old_evidence_and_uses_new_id -v`
Expected: FAIL — 归档未发生 / id 无 -rerun- 后缀

- [ ] **Step 3: 实现**

在 `packages/blackbox/src/shannon_blackbox/worker.py` 的 `run_scan`，`meta = SessionMetadata(...)`（`:69-74`）之后、`start_workflow`（`:83`）之前，加 rerun 处理。先读 `run_scan` 确认 deliverables 在 worker 层怎么拿（若无，用 `resolve_deliverables_path` 算）：

```python
        # rerun：归档旧 evidence + workflow id 加时间戳规避 AlreadyStarted
        workflow_id_base = input.workspace_name or f"blackbox-{int(asyncio.get_event_loop().time())}"
        if input.rerun:
            from datetime import datetime
            from shannon_core.utils.paths import resolve_deliverables_path
            from shannon_blackbox.pipeline.blackbox_rerun import archive_blackbox_deliverables
            deliverables = resolve_deliverables_path(
                repo_path=input.repo_path,
                deliverables_subdir=input.deliverables_subdir,
                workspace_name=input.workspace_name,
                workspaces_root=resolve_workspaces_dir(input.repo_path),
            )
            run_ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            archive_blackbox_deliverables(deliverables, run_ts)
            workflow_id = f"{workflow_id_base}-rerun-{run_ts}"
        else:
            workflow_id = workflow_id_base
```

然后把 `start_workflow`（`:83-88`）的 `id=...` 改为 `id=workflow_id`（原内联表达式移到上面的 workflow_id_base）：

```python
        handle = await client.start_workflow(
            BlackboxScanWorkflow.run,
            input,
            id=workflow_id,
            task_queue=task_queue,
        )
```

（`resolve_workspaces_dir` 若未 import 则补 `from shannon_core.utils.paths import resolve_deliverables_path, resolve_workspaces_dir`。）

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest packages/blackbox/tests/test_worker.py -v`
Expected: PASS（含新 rerun 测试）

- [ ] **Step 5: 提交**

```bash
git add packages/blackbox/src/shannon_blackbox/worker.py packages/blackbox/tests/test_worker.py
git commit -m "feat(rerun): worker --rerun 归档旧 evidence + workflow id 时间戳规避 AlreadyStarted"
```

---

## Task 5: 回归 + 冒烟文档

**Files:**
- Modify: `packages/blackbox/tests/test_blackbox_rerun.py`（补 detect 多 evidence 场景）
- Create: `docs/superpowers/plans/2026-06-19-blackbox-rerun-smoke.md`

- [ ] **Step 1: 补测试**

追加到 `packages/blackbox/tests/test_blackbox_rerun.py`：

```python
def test_detect_true_with_multiple_evidence(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    for vt in ("injection", "xss", "auth"):
        (deliverables / f"{vt}_exploitation_evidence.md").write_text("e")
    assert detect_blackbox_completed(deliverables) is True


def test_archive_all_five_evidence_and_findings(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    for vt in ("injection", "xss", "auth", "ssrf", "authz"):
        (deliverables / f"{vt}_exploitation_evidence.md").write_text("e")
        (deliverables / f"{vt}_findings.md").write_text("f")
    (deliverables / "comprehensive_security_assessment_report.md").write_text("r")

    archive = archive_blackbox_deliverables(deliverables, "20260619-1600")

    assert len(list(archive.glob("*_exploitation_evidence.md"))) == 5
    assert len(list(archive.glob("*_findings.md"))) == 5
    assert (archive / "comprehensive_security_assessment_report.md").exists()
    # deliverables 顶层清空了黑盒产出物
    assert list(deliverables.glob("*_exploitation_evidence.md")) == []
```

- [ ] **Step 2: 跑黑盒回归**

Run: `uv run pytest packages/blackbox/tests/test_blackbox_rerun.py packages/blackbox/tests/test_pipeline_shared.py packages/blackbox/tests/test_cli.py packages/blackbox/tests/test_worker.py -v`
Expected: PASS（rerun 相关全绿）。若 test_worker.py 有预存失败（非 rerun），记录为预存。

- [ ] **Step 3: 写冒烟文档**

```markdown
# docs/superpowers/plans/2026-06-19-blackbox-rerun-smoke.md
# 黑盒 Rerun 人工冒烟

前提：已有跑完的白盒+黑盒 workspace（deliverables 有 evidence + report）。

## 场景 1：默认幂等（已跑过→告知不跑）
1. `shannon-blackbox start --url <url> -r <repo> -w <已有ws>`
2. 预期：输出"该 workspace 已跑过黑盒...如需重跑请加 --rerun"，不启动扫描

## 场景 2：--rerun 强制重跑
1. `shannon-blackbox start --url <url> -r <repo> -w <已有ws> --rerun`
2. 预期：旧 evidence 归档到 `<repo>/.shannon/deliverables/.blackbox-archive/<ts>/`，
   顶层重新生成新 evidence；workflow id 带 `-rerun-<ts>`（Temporal 不报 AlreadyStarted）

## 场景 3：首次黑盒（无 evidence→正常跑）
1. `shannon-blackbox start --url <url> -r <repo> -w <新ws>`（deliverables 无 evidence）
2. 预期：正常跑黑盒，不触发幂等告知
```

- [ ] **Step 4: 提交**

```bash
git add packages/blackbox/tests/test_blackbox_rerun.py docs/superpowers/plans/2026-06-19-blackbox-rerun-smoke.md
git commit -m "test(rerun): detect/archive 多场景 + 人工冒烟文档"
```

---

## Self-Review（已做）

**Spec coverage:** spec §4.1 默认幂等检测 → Task 3；§4.2 `--rerun` 强制重跑 → Task 3/4；§4.3 归档目录 `.blackbox-archive/<run_ts>/` → Task 1/4；§4.4 workflow id 时间戳 → Task 4；§4.5 幂等信号（调整为 evidence 文件，见 Implementation Notes）→ Task 1/3。覆盖完整。

**Placeholder scan:** Task 3/4 的"先读 main.py/worker.py 确认精确位置/变量名"是合理的实现前核实（不是空洞占位），有具体指导。Task 4 测试的 `run_with_display` mock 较复杂——implementer 若卡可简化（mock 整个 run_with_display 上下文），报告里说明。无 TBD/TODO。

**Type consistency:** `detect_blackbox_completed(deliverables) -> bool`、`archive_blackbox_deliverables(deliverables, run_ts) -> Path`、`BlackboxPipelineInput.rerun: bool`、`BB_DELIVERABLE_PATTERNS: list[str]` 在各 Task 间签名一致。
