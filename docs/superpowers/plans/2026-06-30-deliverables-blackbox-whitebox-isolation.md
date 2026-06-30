# 黑白盒产物目录隔离 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把白盒与黑盒扫描产物从同一 `deliverables/` 根物理隔离到 `deliverables/whitebox/` 与 `deliverables/blackbox/`，消除最终报告互相覆盖与目录混乱，读路径自动 fallback 兼容老 workspace。

**Architecture:** core path 层新增 `whitebox_dir` / `blackbox_dir` / `resolve_track_deliverable` 三个 helper（写侧直拼子目录、读侧集中 fallback）。白盒改统一入口 `_get_paths` 一处，19 个写点自动落 `whitebox/`；黑盒 `_get_deliverables_path` 保持返回根（因同一根既被用于读白盒 queue、又被用于写黑盒 evidence），各子模块内部按轨分流；`ReportAssembler` 不动（白盒传 `whitebox/`、黑盒传 `blackbox/` 自洽）。

**Tech Stack:** Python 3.x、pytest、Temporalio activities/workflows、pathlib。

## Global Constraints

- **不破坏双轨独立性铁律**（CLAUDE.md §1）：本改动只动产物落盘路径，不碰 LLM/确定性双轨、不喂确定性产物给 LLM 轨。
- **写侧永远写新结构**（`whitebox/` 或 `blackbox/` 子目录），**读侧走 `resolve_track_deliverable` fallback**（先子目录、无则回退根），老 workspace resume 不断。
- **helper 收 `deliverables_dir`**（即 `workspaces/<session>/deliverables` 根目录），**不是** `workspace_path`——所有调用点持有的都是 deliverables 根（spec §4.1 写的 `workspace_path` 已在 plan 细化为 `deliverables_dir`，spec 同步修正）。
- **不要走"改 `deliverables_dir_for_workspace` / `resolve_deliverables_path` 返回值加 `/whitebox`"的捷径**——那会把黑盒、multi correlation 也错误带进 `whitebox/`，破坏对称隔离。必须用独立的新 helper，显式按轨。
- **测试只跑改动相关文件**，避开预存挂起（`test_worker_progress` / cli follow / `test_audit_injection` / integration 挂起）；广跑需 `--ignore`。
- 每个 task 结束 `git commit`；分支 `feat/fork-py`。

---

## File Structure

| 文件 | 责任 | 改动 |
|---|---|---|
| `packages/core/src/shannon_core/utils/paths.py` | 路径解析（心脏） | 新增常量 + 3 helper |
| `packages/core/tests/test_paths.py` | path helper 测试 | 新增 3 helper 测试 |
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | 白盒所有 activity | `_get_paths` 返回 `whitebox/`（一处，全局跟进） |
| `packages/whitebox/src/shannon_whitebox/cli/main.py` | 白盒 CLI 展示 | 2 处读 queue 走 fallback |
| `packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py` | exploit 执行 | 读 queue fallback + 写 evidence/verdicts 落 `blackbox/` |
| `packages/blackbox/src/shannon_blackbox/services/coverage_renderer.py` | 覆盖率闭环 | 读 queue fallback + 读写 evidence 落 `blackbox/` |
| `packages/blackbox/src/shannon_blackbox/services/exploitation_checker.py` | queue 校验 | 读 queue fallback |
| `packages/core/src/shannon_core/services/findings_renderer.py` | findings 渲染（core 共享） | 读 queue fallback + 写 findings 落 `blackbox/` |
| `packages/blackbox/src/shannon_blackbox/pipeline/activities.py` | 黑盒 activity | report 写 `blackbox/` + ReportAssembler 传 `blackbox/` + detect_whitebox_results 读 queue fallback |
| `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` | 黑盒 workflow | `has_correlation_results` 读 queue fallback |
| `packages/blackbox/src/shannon_blackbox/cli/main.py` | 黑盒 CLI info | 读 queue fallback |
| `packages/blackbox/src/shannon_blackbox/pipeline/blackbox_rerun.py` | rerun 归档 | detect/archive 在 `blackbox/` 内 |
| `packages/multi/src/shannon_multi/orchestrator.py` | 关联编排 | 读子仓 queue 走 `whitebox/` fallback |

---

## Task 1: core path helper（心脏）

**Files:**
- Modify: `packages/core/src/shannon_core/utils/paths.py`（在 `deliverables_dir_for_workspace` 之后追加）
- Test: `packages/core/tests/test_paths.py`

**Interfaces:**
- Consumes: `deliverables_dir_for_workspace(workspace_path)`（已存在）、`Path`
- Produces:
  - `WHITEBOX_SUBDIR: str = "whitebox"`、`BLACKBOX_SUBDIR: str = "blackbox"`
  - `whitebox_dir(deliverables_dir: Path) -> Path` → `deliverables_dir / "whitebox"`
  - `blackbox_dir(deliverables_dir: Path) -> Path` → `deliverables_dir / "blackbox"`
  - `resolve_track_deliverable(deliverables_dir: Path, track: str, filename: str) -> Path`：先 `deliverables_dir/{track}/filename`，存在则返回；否则看 `deliverables_dir/filename`，存在则返回；都不存在返回新结构路径（`deliverables_dir/{track}/filename`）让调用方按 not-found 处理。

- [ ] **Step 1: 写失败测试**（追加到 `packages/core/tests/test_paths.py` 末尾）

```python
from shannon_core.utils.paths import (
    WHITEBOX_SUBDIR, BLACKBOX_SUBDIR,
    whitebox_dir, blackbox_dir, resolve_track_deliverable,
)


class TestTrackSubdirHelpers:
    def test_whitebox_dir_appends_subdir(self, tmp_path):
        dlv = tmp_path / "deliverables"
        assert whitebox_dir(dlv) == dlv / "whitebox"

    def test_blackbox_dir_appends_subdir(self, tmp_path):
        dlv = tmp_path / "deliverables"
        assert blackbox_dir(dlv) == dlv / "blackbox"

    def test_subdir_constants(self):
        assert WHITEBOX_SUBDIR == "whitebox"
        assert BLACKBOX_SUBDIR == "blackbox"


class TestResolveTrackDeliverable:
    def test_prefers_new_track_subdir(self, tmp_path):
        dlv = tmp_path / "deliverables"
        (dlv / "whitebox").mkdir(parents=True)
        (dlv / "whitebox" / "injection_exploitation_queue.json").write_text("{}")
        # 老结构也存在，但新结构优先
        (dlv / "injection_exploitation_queue.json").write_text("{}")
        result = resolve_track_deliverable(dlv, "whitebox", "injection_exploitation_queue.json")
        assert result == dlv / "whitebox" / "injection_exploitation_queue.json"

    def test_falls_back_to_legacy_root(self, tmp_path):
        dlv = tmp_path / "deliverables"
        dlv.mkdir()
        # 仅老结构存在（老 workspace）
        (dlv / "injection_exploitation_queue.json").write_text("{}")
        result = resolve_track_deliverable(dlv, "whitebox", "injection_exploitation_queue.json")
        assert result == dlv / "injection_exploitation_queue.json"

    def test_returns_new_path_when_neither_exists(self, tmp_path):
        dlv = tmp_path / "deliverables"
        dlv.mkdir()
        result = resolve_track_deliverable(dlv, "blackbox", "x_evidence.md")
        # 都不存在 → 返回新结构路径（让调用方自然 not-found）
        assert result == dlv / "blackbox" / "x_evidence.md"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_paths.py::TestTrackSubdirHelpers packages/core/tests/test_paths.py::TestResolveTrackDeliverable -v`
Expected: FAIL — `ImportError: cannot import name 'whitebox_dir'`

- [ ] **Step 3: 实现 helper**（追加到 `paths.py` 的 `has_valid_whitebox_results` 函数之后）

```python
WHITEBOX_SUBDIR: str = "whitebox"
BLACKBOX_SUBDIR: str = "blackbox"


def whitebox_dir(deliverables_dir: Path) -> Path:
    """白盒产物子目录（写侧用）：deliverables_dir/whitebox/。

    deliverables_dir 是 workspaces/<session>/deliverables 根（非 workspace_path）。
    """
    return deliverables_dir / WHITEBOX_SUBDIR


def blackbox_dir(deliverables_dir: Path) -> Path:
    """黑盒产物子目录（写侧用）：deliverables_dir/blackbox/。

    deliverables_dir 是 workspaces/<session>/deliverables 根（非 workspace_path）。
    """
    return deliverables_dir / BLACKBOX_SUBDIR


def resolve_track_deliverable(deliverables_dir: Path, track: str, filename: str) -> Path:
    """读侧 fallback：先 deliverables_dir/{track}/filename（新结构），无则回退
    deliverables_dir/filename（老 workspace）。两者都不存在时返回新结构路径，
    让调用方按既定 not-found 语义处理（不在这里抛错）。

    track 取 WHITEBOX_SUBDIR 或 BLACKBOX_SUBDIR。
    """
    new = deliverables_dir / track / filename
    if new.exists():
        return new
    legacy = deliverables_dir / filename
    return legacy if legacy.exists() else new
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_paths.py::TestTrackSubdirHelpers packages/core/tests/test_paths.py::TestResolveTrackDeliverable -v`
Expected: PASS（5 passed）

- [ ] **Step 5: 回归现有 path 测试**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_paths.py -v`
Expected: PASS（原有测试不挂）

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/utils/paths.py packages/core/tests/test_paths.py
git commit -m "feat(core): 加 whitebox_dir/blackbox_dir/resolve_track_deliverable 路径 helper"
```

---

## Task 2: 白盒写侧 → `whitebox/`（含 CLI 读 queue）

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:32-40`（`_get_paths`）
- Modify: `packages/whitebox/src/shannon_whitebox/cli/main.py:104-116`、`:282-294`
- Test: `packages/whitebox/tests/test_run_merge_dual_track.py`、`packages/whitebox/tests/test_assemble_report.py`（改断言路径）

**Interfaces:**
- Consumes: Task 1 的 `WHITEBOX_SUBDIR`
- Produces: 白盒所有产物（含 `comprehensive_security_assessment_report.md`）落 `deliverables/whitebox/`

**说明：** 白盒 19 个写点全部经 `_get_paths(input)` 拿 deliverables，改这一处即全局跟进。`ReportAssembler` 不用改——白盒 `assemble_report` 传 `whitebox/` 给它，analysis 文件就在 `whitebox/` 下自洽。

- [ ] **Step 1: 改 `_get_paths`**（`activities.py:32-40`）

before:
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

after:
```python
def _get_paths(input: ActivityInput) -> tuple[Path, Path, Path]:
    from shannon_core.utils.paths import WHITEBOX_SUBDIR

    deliverables = resolve_deliverables_path(
        repo_path=input.repo_path,
        deliverables_subdir=input.deliverables_subdir,
        workspace_name=input.workspace_name,
    )
    # 白盒产物隔离到 deliverables/whitebox/（与黑盒 blackbox/ 对称）。
    # 写侧永远落新结构；黑盒读白盒 queue 走 resolve_track_deliverable fallback。
    deliverables = deliverables / WHITEBOX_SUBDIR
    repo = Path(input.repo_path)
    workspaces = repo.parent / "workspaces"
    return repo, deliverables, workspaces
```

- [ ] **Step 2: 改白盒 CLI 读 queue 走 fallback**（`cli/main.py`）

`cli/main.py:110`（summary 命令）before:
```python
queue_file = summary_path / f"{vc}_exploitation_queue.json"
```
after:
```python
from shannon_core.utils.paths import resolve_track_deliverable, WHITEBOX_SUBDIR
queue_file = resolve_track_deliverable(summary_path, WHITEBOX_SUBDIR, f"{vc}_exploitation_queue.json")
```
（`summary_path` 即 deliverables 根；import 放文件顶部 import 区）

`cli/main.py:284`（workspace show 命令）before:
```python
deliverables_dir = deliverables_dir_for_workspace(ws)
...
filepath = deliverables_dir / filename
```
after:
```python
from shannon_core.utils.paths import resolve_track_deliverable, WHITEBOX_SUBDIR
deliverables_dir = deliverables_dir_for_workspace(ws)
...
filepath = resolve_track_deliverable(deliverables_dir, WHITEBOX_SUBDIR, filename)
```

- [ ] **Step 3: 改白盒测试断言路径**（产物现在在 `whitebox/` 子目录下）

`packages/whitebox/tests/test_run_merge_dual_track.py:61` before:
```python
out = json.loads((deliverables / "injection_exploitation_queue.json").read_text())
```
after:
```python
out = json.loads((deliverables / "whitebox" / "injection_exploitation_queue.json").read_text())
```
同文件 :65 `(deliverables / "injection_llm_queue.json")` → `(deliverables / "whitebox" / "injection_llm_queue.json")`

`packages/whitebox/tests/test_assemble_report.py:42` before:
```python
report = deliverables / "comprehensive_security_assessment_report.md"
```
after:
```python
report = deliverables / "whitebox" / "comprehensive_security_assessment_report.md"
```

`packages/whitebox/tests/test_run_authz_gitnexus_judge.py:76`、`test_run_gitnexus_chain_verdict.py:110`、`test_run_auth_config_scan.py:52-55`：同样在断言路径中加 `/ "whitebox"`（这些测试构造的 deliverables 根 + activity 写入，现在写到 `whitebox/` 子目录）。

- [ ] **Step 4: 运行白盒相关测试验证通过**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_run_merge_dual_track.py packages/whitebox/tests/test_assemble_report.py packages/whitebox/tests/test_run_authz_gitnexus_judge.py packages/whitebox/tests/test_run_gitnexus_chain_verdict.py packages/whitebox/tests/test_run_auth_config_scan.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/src/shannon_whitebox/cli/main.py packages/whitebox/tests/
git commit -m "feat(whitebox): 产物落 deliverables/whitebox/ 子目录（_get_paths 一处全局跟进）"
```

---

## Task 3: 黑盒 exploit_executor（读 queue fallback + 写 evidence/verdicts 落 `blackbox/`）

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py:39`、`:95-98`
- Test: `packages/blackbox/tests/test_executors.py`

**Interfaces:**
- Consumes: Task 1 的 `resolve_track_deliverable`、`WHITEBOX_SUBDIR`、`blackbox_dir`
- Produces: exploit evidence 与 verdicts.json 落 `blackbox/`；queue 从 `whitebox/` fallback 读

**说明：** `deliverables_path` 参数（根）不改。:39 读 queue 走 `whitebox` fallback；:96 写 evidence、:98 `write_verdicts_json` 写 verdicts 都落 `blackbox/`。注意 :61/:63 传给 executor 的 `deliverables_path`（agent 沙箱工作目录，agent 在里面建 `.shannon/deliverables/`）**保持根不变**——那是 agent 内部工作目录，不是我们的产物落点。

- [ ] **Step 1: 写失败测试**（追加到 `packages/blackbox/tests/test_executors.py`）

```python
async def test_exploit_executor_reads_queue_from_whitebox_subdir(fake_executor, tmp_path):
    """新结构：白盒 queue 在 deliverables/whitebox/，exploit_executor 走 fallback 读到。"""
    from shannon_blackbox.agents.exploit_executor import ExploitExecutor
    dlv = tmp_path / "deliverables"
    (dlv / "whitebox").mkdir(parents=True)
    (dlv / "whitebox" / "injection_exploitation_queue.json").write_text(
        '{"vulnerabilities": [{"ID": "INJ-1", "vulnerability_type": "SQLi"}]}'
    )
    executor = ExploitExecutor(fake_executor)
    await executor.execute(
        agent_name=AgentName.INJECTION_EXPLOIT, vuln_type="injection",
        workspace_path=tmp_path, deliverables_path=dlv, web_url="https://x.com",
    )
    # evidence 落 blackbox/
    assert (dlv / "blackbox" / "injection_exploitation_evidence.md").exists()


async def test_exploit_executor_falls_back_to_legacy_queue(fake_executor, tmp_path):
    """老 workspace：queue 在 deliverables 根，fallback 读到。"""
    from shannon_blackbox.agents.exploit_executor import ExploitExecutor
    dlv = tmp_path / "deliverables"
    dlv.mkdir()
    (dlv / "injection_exploitation_queue.json").write_text(
        '{"vulnerabilities": [{"ID": "INJ-1", "vulnerability_type": "SQLi"}]}'
    )
    executor = ExploitExecutor(fake_executor)
    await executor.execute(
        agent_name=AgentName.INJECTION_EXPLOIT, vuln_type="injection",
        workspace_path=tmp_path, deliverables_path=dlv, web_url="https://x.com",
    )
    assert (dlv / "blackbox" / "injection_exploitation_evidence.md").exists()
```
（`fake_executor` fixture 沿用 `test_executors.py` 现有的；若名字不同，沿用该文件现有测试的 fixture 命名。）

- [ ] **Step 2: 运行验证失败**

Run: `cd /root/shannon-py && python -m pytest packages/blackbox/tests/test_executors.py::test_exploit_executor_reads_queue_from_whitebox_subdir packages/blackbox/tests/test_executors.py::test_exploit_executor_falls_back_to_legacy_queue -v`
Expected: FAIL（evidence 落在根而非 `blackbox/`、或 queue 从 whitebox 读不到）

- [ ] **Step 3: 改 exploit_executor**

`exploit_executor.py` 顶部 import 区加：
```python
from shannon_core.utils.paths import resolve_track_deliverable, WHITEBOX_SUBDIR, blackbox_dir
```

:39 before:
```python
queue_path = deliverables_path / f"{vuln_type}_exploitation_queue.json"
```
after:
```python
queue_path = resolve_track_deliverable(
    deliverables_path, WHITEBOX_SUBDIR, f"{vuln_type}_exploitation_queue.json"
)
```

:95-98 before:
```python
evidence_md = ExploitEvidenceRenderer.render(validation, vuln_type)
await async_write_file(
    deliverables_path / f"{vuln_type}_exploitation_evidence.md", evidence_md)
ExploitEvidenceRenderer.write_verdicts_json(
    validation, vuln_type, deliverables_path)
```
after:
```python
evidence_md = ExploitEvidenceRenderer.render(validation, vuln_type)
bb = blackbox_dir(deliverables_path)
bb.mkdir(parents=True, exist_ok=True)
await async_write_file(bb / f"{vuln_type}_exploitation_evidence.md", evidence_md)
ExploitEvidenceRenderer.write_verdicts_json(validation, vuln_type, bb)
```

- [ ] **Step 4: 改现有 executor 测试断言**（`test_executors.py:139-175` 附近，evidence/verdicts 现在落 `blackbox/`）

把现有断言中 `(deliverables / "injection_exploitation_evidence.md")` → `(deliverables / "blackbox" / "injection_exploitation_evidence.md")`；verdicts.json 同理加 `/ "blackbox"`。若现有测试构造 queue 在 deliverables 根，保留（fallback 仍读到）。

- [ ] **Step 5: 运行验证通过**

Run: `cd /root/shannon-py && python -m pytest packages/blackbox/tests/test_executors.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py packages/blackbox/tests/test_executors.py
git commit -m "feat(blackbox): exploit_executor 读白盒 queue 走 fallback、evidence/verdicts 落 blackbox/"
```

---

## Task 4: 黑盒 coverage_renderer + exploitation_checker（读 queue fallback + 读写 evidence 落 `blackbox/`）

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/services/coverage_renderer.py:97-112`
- Modify: `packages/blackbox/src/shannon_blackbox/services/exploitation_checker.py:78`
- Test: `packages/blackbox/tests/test_coverage_renderer.py`、`packages/blackbox/tests/test_exploitation_checker.py`

**Interfaces:**
- Consumes: Task 1 的 `resolve_track_deliverable`、`WHITEBOX_SUBDIR`、`BLACKBOX_SUBDIR`
- Produces: coverage 闭环正确读白盒 queue（whitebox fallback）、读写黑盒 evidence（blackbox）

**说明：** `close_coverage_gaps(deliverables_path, ...)` 收 deliverables 根。:97 queue 走 whitebox fallback；:98 evidence 读走 blackbox fallback（新结构 blackbox/、老结构根）；:102/:112 读写 evidence 都基于 fallback 解析出的 evidence_path，写时确保落 blackbox/。

- [ ] **Step 1: 写失败测试**（追加到 `test_coverage_renderer.py`）

```python
async def test_close_coverage_gaps_reads_queue_from_whitebox_writes_evidence_to_blackbox(tmp_path):
    """新结构：queue 在 whitebox/、evidence 在 blackbox/。"""
    from shannon_blackbox.services.coverage_renderer import close_coverage_gaps
    dlv = tmp_path / "deliverables"
    (dlv / "whitebox").mkdir(parents=True)
    (dlv / "blackbox").mkdir(parents=True)
    # queue 有 2 条，evidence 只覆盖 1 条 → 1 条未覆盖
    (dlv / "whitebox" / "injection_exploitation_queue.json").write_text(
        '{"vulnerabilities": [{"ID": "INJ-1"}, {"ID": "INJ-2"}]}'
    )
    (dlv / "blackbox" / "injection_exploitation_evidence.md").write_text(
        "# Evidence\n## INJ-1\nverified")
    results = await close_coverage_gaps(dlv, ["injection"])
    assert len(results) == 1
    assert "INJ-2" in results[0].uncovered_ids
    # 未覆盖节写到 blackbox/ 的 evidence
    assert "Unverified" in (dlv / "blackbox" / "injection_exploitation_evidence.md").read_text()
```

- [ ] **Step 2: 运行验证失败**

Run: `cd /root/shannon-py && python -m pytest packages/blackbox/tests/test_coverage_renderer.py::test_close_coverage_gaps_reads_queue_from_whitebox_writes_evidence_to_blackbox -v`
Expected: FAIL（queue/evidence 路径仍指向根）

- [ ] **Step 3: 改 coverage_renderer.py**

顶部 import 区加：
```python
from shannon_core.utils.paths import resolve_track_deliverable, WHITEBOX_SUBDIR, BLACKBOX_SUBDIR
```

:97-98 before:
```python
queue_path = deliverables_path / f"{vc}_exploitation_queue.json"
evidence_path = deliverables_path / f"{vc}_exploitation_evidence.md"
```
after:
```python
queue_path = resolve_track_deliverable(
    deliverables_path, WHITEBOX_SUBDIR, f"{vc}_exploitation_queue.json")
evidence_path = resolve_track_deliverable(
    deliverables_path, BLACKBOX_SUBDIR, f"{vc}_exploitation_evidence.md")
```

:112 写 evidence：`evidence_path` 已是 fallback 解析结果。但新结构下要确保写进 `blackbox/` 而非回退的根。把 :102/:112 的 evidence 读写统一到 `evidence_path`，并在新结构下 `evidence_path` 指向 `blackbox/`。为保险，写前确保父目录存在：在 :102 之前加：
```python
evidence_path.parent.mkdir(parents=True, exist_ok=True)
```
（`evidence_path` 在新结构是 `blackbox/{vc}_evidence.md`，parent 是 `blackbox/`；老结构是根，mkdir 无害。）

:107 读 queue 同样用 fallback 后的 `queue_path`（已改），无需再动。

- [ ] **Step 4: 改 exploitation_checker.py:78**

before:
```python
queue_path = deliverables_path / f"{vuln_type}_exploitation_queue.json"
```
after:
```python
from shannon_core.utils.paths import resolve_track_deliverable, WHITEBOX_SUBDIR
queue_path = resolve_track_deliverable(
    deliverables_path, WHITEBOX_SUBDIR, f"{vuln_type}_exploitation_queue.json")
```
（import 提到文件顶部）

- [ ] **Step 5: 改现有测试断言**（`test_coverage_renderer.py:101-113`、`:153-172`；`test_exploitation_checker.py` 中构造 queue/evidence 路径处）

现有测试若把 queue/evidence 放在 deliverables 根，**保留根**（验证 fallback 能读到）；新增的 blackbox 场景测试放 `blackbox/`。两套都跑通才算兼容。

- [ ] **Step 6: 运行验证通过**

Run: `cd /root/shannon-py && python -m pytest packages/blackbox/tests/test_coverage_renderer.py packages/blackbox/tests/test_exploitation_checker.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/services/coverage_renderer.py packages/blackbox/src/shannon_blackbox/services/exploitation_checker.py packages/blackbox/tests/
git commit -m "feat(blackbox): coverage_renderer/exploitation_checker 读白盒 queue 走 fallback、evidence 读写落 blackbox/"
```

---

## Task 5: 黑盒 report 链（report 落 `blackbox/` + FindingsRenderer + ReportAssembler 调用适配）

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/activities.py:272`、`:279`、`:284`、`:288`、`:392`、`:399`
- Modify: `packages/core/src/shannon_core/services/findings_renderer.py:203-212`
- Test: `packages/blackbox/tests/test_assemble_report.py`（若存在；否则用 `test_coverage_renderer.py` 的 report 断言）、`packages/core/tests/services/test_findings_renderer.py`（若存在）

**Interfaces:**
- Consumes: Task 1 的 `blackbox_dir`、`resolve_track_deliverable`、`WHITEBOX_SUBDIR`、`BLACKBOX_SUBDIR`
- Produces: 黑盒 `comprehensive_security_assessment_report.md` 落 `blackbox/`，不再覆盖白盒报告；findings 落 `blackbox/`

**说明：** 黑盒 `_get_deliverables_path` 返回根，**不改**。report 写点拼 `blackbox_dir(deliverables)`。`ReportAssembler.assemble` 收到的 deliverables_path 传 `blackbox_dir(deliverables)`（evidence/findings 在 blackbox/，自洽）。`FindingsRenderer.render_findings_from_queues` 收根，内部读 queue 走 whitebox fallback、写 findings 落 blackbox/。

- [ ] **Step 1: 改 `assemble_report` activity（activities.py:264-294）**

:272 before:
```python
report_path = deliverables / "comprehensive_security_assessment_report.md"
```
after:
```python
from shannon_core.utils.paths import blackbox_dir
bb = blackbox_dir(deliverables)
bb.mkdir(parents=True, exist_ok=True)
report_path = bb / "comprehensive_security_assessment_report.md"
```

:279 `FindingsRenderer.render_findings_from_queues(deliverables, report_config)` —— **不改传参**（仍传 deliverables 根；FindingsRenderer 内部 Step 3 改成读 whitebox queue + 写 blackbox findings）。

:284 `close_coverage_gaps(deliverables, vuln_classes)` —— **不改**（Task 4 已让它内部按轨分流）。

:288 before:
```python
await ReportAssembler.assemble(deliverables, vuln_classes, report_path)
```
after:
```python
await ReportAssembler.assemble(bb, vuln_classes, report_path)
```
（传 `bb`=blackbox/，ReportAssembler 在 blackbox/ 找 evidence/findings；analysis 找不到但黑盒有 evidence 优先，无妨。）

- [ ] **Step 2: 改 `finalize_report` activity（activities.py:386-399）**

:392 before:
```python
report_path = deliverables / "comprehensive_security_assessment_report.md"
```
after:
```python
from shannon_core.utils.paths import blackbox_dir
report_path = blackbox_dir(deliverables) / "comprehensive_security_assessment_report.md"
```
:399 `provider.generate(report_path, deliverables)` —— `NoOpReportOutputProvider`，传 deliverables 根不改。

- [ ] **Step 3: 改 `FindingsRenderer.render_findings_from_queues`（findings_renderer.py:203-212）**

读现状（:209-212）：
```python
findings_path = deliverables_path / class_cfg.findings_file
...
queue_path = deliverables_path / class_cfg.queue_file
```
改为：
```python
from shannon_core.utils.paths import resolve_track_deliverable, WHITEBOX_SUBDIR, blackbox_dir
bb = blackbox_dir(deliverables_path)
bb.mkdir(parents=True, exist_ok=True)
findings_path = bb / class_cfg.findings_file
...
queue_path = resolve_track_deliverable(
    deliverables_path, WHITEBOX_SUBDIR, class_cfg.queue_file)
```
（import 提到文件顶部）

- [ ] **Step 4: 改/加测试**

`packages/blackbox/tests/test_assemble_report.py`（或对应 report 测试）：断言黑盒 report 落 `deliverables/blackbox/comprehensive_security_assessment_report.md`，且**不与白盒 report 同名覆盖**（构造 whitebox/report.md + 跑黑盒 assemble，两者共存）。

`packages/core/tests/services/test_findings_renderer.py`（若存在）：findings 断言加 `/ "blackbox"`；queue 输入放 `whitebox/`（新结构）或根（fallback）两套都验证。

- [ ] **Step 5: 运行验证通过**

Run: `cd /root/shannon-py && python -m pytest packages/blackbox/tests/test_assemble_report.py packages/core/tests/services/test_findings_renderer.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/pipeline/activities.py packages/core/src/shannon_core/services/findings_renderer.py packages/blackbox/tests/ packages/core/tests/services/
git commit -m "feat(blackbox): report/findings 落 blackbox/、ReportAssembler 收 blackbox/ 不再覆盖白盒报告"
```

---

## Task 6: 黑盒 detect_whitebox_results + has_correlation_results + cli info（读 queue fallback）

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/activities.py:498-528`（detect_whitebox_results）
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py:17-38`（has_correlation_results）
- Modify: `packages/blackbox/src/shannon_blackbox/cli/main.py:315-327`（info 命令）
- Test: `packages/blackbox/tests/test_detect_whitebox_results.py`（若有）、`packages/blackbox/tests/test_workflows.py`

**Interfaces:**
- Consumes: Task 1 的 `resolve_track_deliverable`、`WHITEBOX_SUBDIR`、`has_valid_whitebox_results`
- Produces: 黑盒 preflight 从 `whitebox/{vt}_exploitation_queue.json` 读白盒 queue（新结构），fallback 到根（老结构 + correlation merged queue）

**说明：** `detect_whitebox_results` 收 `deliverables_path`（根）。:515 单仓 queue 走 whitebox fallback。:522 correlation 路径也走 whitebox fallback——merged queue 在 correlation workspace 的 deliverables 根，fallback 先找 `corr_dlv/whitebox/`（不存在）再回退 `corr_dlv/` 根（命中 merged queue），天然兼容。

- [ ] **Step 1: 改 detect_whitebox_results（activities.py:498-528）**

把 :510-523 的 `has_valid_whitebox_results(dlv / f"{vt}_exploitation_queue.json")` 改成走 fallback。before:
```python
from shannon_core.utils.paths import has_valid_whitebox_results

dlv = Path(deliverables_path)
found_classes = [
    vt for vt in vuln_classes
    if has_valid_whitebox_results(dlv / f"{vt}_exploitation_queue.json")
]
corr_classes: list[str] = []
if correlation_deliverables_path and not found_classes:
    corr_dlv = Path(correlation_deliverables_path)
    corr_classes = [
        vt for vt in vuln_classes
        if has_valid_whitebox_results(corr_dlv / f"{vt}_exploitation_queue.json")
    ]
```
after:
```python
from shannon_core.utils.paths import has_valid_whitebox_results, resolve_track_deliverable, WHITEBOX_SUBDIR

dlv = Path(deliverables_path)
found_classes = [
    vt for vt in vuln_classes
    if has_valid_whitebox_results(
        resolve_track_deliverable(dlv, WHITEBOX_SUBDIR, f"{vt}_exploitation_queue.json"))
]
corr_classes: list[str] = []
if correlation_deliverables_path and not found_classes:
    corr_dlv = Path(correlation_deliverables_path)
    corr_classes = [
        vt for vt in vuln_classes
        if has_valid_whitebox_results(
            resolve_track_deliverable(corr_dlv, WHITEBOX_SUBDIR, f"{vt}_exploitation_queue.json"))
    ]
```

- [ ] **Step 2: 改 has_correlation_results（workflows.py:17-38）**

before:
```python
queue_file = corr_ws_deliverables / f"{vt}_exploitation_queue.json"
```
after:
```python
from shannon_core.utils.paths import resolve_track_deliverable, WHITEBOX_SUBDIR
queue_file = resolve_track_deliverable(corr_ws_deliverables, WHITEBOX_SUBDIR, f"{vt}_exploitation_queue.json")
```
（import 提到文件顶部；`has_valid_whitebox_results(queue_file)` 调用不变）

- [ ] **Step 3: 改黑盒 cli info（cli/main.py:315-327）**

before:
```python
deliverables_dir = deliverables_dir_for_workspace(ws)
...
filename = f"{vc}_exploitation_queue.json"
filepath = deliverables_dir / filename
```
after:
```python
from shannon_core.utils.paths import resolve_track_deliverable, WHITEBOX_SUBDIR
deliverables_dir = deliverables_dir_for_workspace(ws)
...
filename = f"{vc}_exploitation_queue.json"
filepath = resolve_track_deliverable(deliverables_dir, WHITEBOX_SUBDIR, filename)
```

- [ ] **Step 4: 写/改测试**

新增测试：`detect_whitebox_results` 在新结构（queue 在 `whitebox/`）返回 `has_whitebox_results=True`；老结构（queue 在根）也返回 True（fallback）。`has_correlation_results` 从 correlation workspace 根读到 merged queue。

- [ ] **Step 5: 运行验证通过**

Run: `cd /root/shannon-py && python -m pytest packages/blackbox/tests/test_workflows.py packages/blackbox/tests/ -k "detect_whitebox or correlation" -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/pipeline/activities.py packages/blackbox/src/shannon_blackbox/pipeline/workflows.py packages/blackbox/src/shannon_blackbox/cli/main.py packages/blackbox/tests/
git commit -m "feat(blackbox): detect_whitebox_results/has_correlation_results/cli 读白盒 queue 走 fallback"
```

---

## Task 7: 黑盒 rerun 归档迁移到 `blackbox/` 内

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/blackbox_rerun.py:26-51`
- Test: `packages/blackbox/tests/test_blackbox_rerun.py`

**Interfaces:**
- Consumes: Task 1 的 `BLACKBOX_SUBDIR`
- Produces: `detect_blackbox_completed` 在 `blackbox/` 下找 evidence；`archive_blackbox_deliverables` 在 `blackbox/.blackbox-archive/<run_ts>/` 归档

- [ ] **Step 1: 写失败测试**（改 `test_blackbox_rerun.py`）

```python
def test_detect_returns_true_when_evidence_in_blackbox_subdir(tmp_path):
    """新结构：evidence 在 deliverables/blackbox/。"""
    from shannon_blackbox.pipeline.blackbox_rerun import detect_blackbox_completed
    dlv = tmp_path / "deliverables"
    (dlv / "blackbox").mkdir(parents=True)
    (dlv / "blackbox" / "injection_exploitation_evidence.md").write_text("x")
    assert detect_blackbox_completed(dlv) is True


def test_archive_moves_to_blackbox_subdir_archive(tmp_path):
    """归档源与目标都在 blackbox/ 内。"""
    from shannon_blackbox.pipeline.blackbox_rerun import archive_blackbox_deliverables
    dlv = tmp_path / "deliverables"
    (dlv / "blackbox").mkdir(parents=True)
    (dlv / "blackbox" / "injection_exploitation_evidence.md").write_text("x")
    archive = archive_blackbox_deliverables(dlv, "20260630-120000")
    assert archive == dlv / "blackbox" / ".blackbox-archive" / "20260630-120000"
    assert (archive / "injection_exploitation_evidence.md").exists()
    assert not (dlv / "blackbox" / "injection_exploitation_evidence.md").exists()
```
（保留现有老结构测试：把 evidence 放 `dlv/blackbox/` 模拟新结构；若现有测试放根，改成放 `blackbox/` 或新增 blackbox 场景。）

- [ ] **Step 2: 运行验证失败**

Run: `cd /root/shannon-py && python -m pytest packages/blackbox/tests/test_blackbox_rerun.py -v`
Expected: FAIL（detect 在根找、archive 归档到根）

- [ ] **Step 3: 改 blackbox_rerun.py**

顶部加：
```python
from shannon_core.utils.paths import BLACKBOX_SUBDIR
```

:26-28 before:
```python
def detect_blackbox_completed(deliverables: Path) -> bool:
    """Return True if any `*_exploitation_evidence.md` exists in deliverables."""
    return bool(list(deliverables.glob("*_exploitation_evidence.md")))
```
after:
```python
def detect_blackbox_completed(deliverables: Path) -> bool:
    """Return True if any `*_exploitation_evidence.md` exists in blackbox/ subdir."""
    return bool(list((deliverables / BLACKBOX_SUBDIR).glob("*_exploitation_evidence.md")))
```

:31-51 `archive_blackbox_deliverables`：把归档源从 `deliverables` 改成 `deliverables / BLACKBOX_SUBDIR`，归档目录改成 `deliverables / BLACKBOX_SUBDIR / ".blackbox-archive" / run_ts`。before:
```python
def archive_blackbox_deliverables(deliverables: Path, run_ts: str) -> Path:
    archive = deliverables / ".blackbox-archive" / run_ts
    archive.mkdir(parents=True, exist_ok=True)
    for pattern in BB_DELIVERABLE_PATTERNS:
        for src in deliverables.glob(pattern):
            ...
```
after:
```python
def archive_blackbox_deliverables(deliverables: Path, run_ts: str) -> Path:
    bb = deliverables / BLACKBOX_SUBDIR
    archive = bb / ".blackbox-archive" / run_ts
    archive.mkdir(parents=True, exist_ok=True)
    for pattern in BB_DELIVERABLE_PATTERNS:
        for src in bb.glob(pattern):
            dest = archive / src.name
            if dest.exists():
                stem, suffix = src.stem, src.suffix
                i = 1
                while dest.exists():
                    dest = archive / f"{stem}_{i}{suffix}"
                    i += 1
            shutil.move(str(src), str(dest))
    return archive
```

- [ ] **Step 4: 运行验证通过**

Run: `cd /root/shannon-py && python -m pytest packages/blackbox/tests/test_blackbox_rerun.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/pipeline/blackbox_rerun.py packages/blackbox/tests/test_blackbox_rerun.py
git commit -m "feat(blackbox): rerun 归档迁入 deliverables/blackbox/ 内"
```

---

## Task 8: multi orchestrator 读子仓 queue 走 `whitebox/` fallback

**Files:**
- Modify: `packages/multi/src/shannon_multi/orchestrator.py:121-134`
- Test: `packages/core/tests/correlation/test_queue_merge.py`、`packages/multi/tests/`（若有）

**Interfaces:**
- Consumes: Task 1 的 `WHITEBOX_SUBDIR`
- Produces: multi 关联能从子仓 `deliverables/whitebox/` 读到白盒 queue（新结构），fallback 到子仓根（老结构）

**说明：** :121 `dlv = deliverables_dir_for_workspace(ws_path)` 是子仓 deliverables 根。:124 `dlv.glob("*_exploitation_queue.json")` 改成先 glob `whitebox/` 子目录、再 glob 根（合并去重）。

- [ ] **Step 1: 改 orchestrator.py:121-134**

before:
```python
dlv = deliverables_dir_for_workspace(ws_path)
per_repo_deliverables[p.service] = dlv
for q in dlv.glob("*_exploitation_queue.json"):
    vc = q.stem.replace("_exploitation_queue", "")
    ...
```
after:
```python
from shannon_core.utils.paths import WHITEBOX_SUBDIR

dlv = deliverables_dir_for_workspace(ws_path)
per_repo_deliverables[p.service] = dlv
# 白盒 queue 新结构在 whitebox/ 子目录，老结构在根；合并去重（whitebox/ 优先）。
queue_files: dict[str, Path] = {}
for q in (dlv / WHITEBOX_SUBDIR).glob("*_exploitation_queue.json"):
    queue_files[q.name] = q
for q in dlv.glob("*_exploitation_queue.json"):
    queue_files.setdefault(q.name, q)
for q in queue_files.values():
    vc = q.stem.replace("_exploitation_queue", "")
    ...
```
（`...` 部分 try/except 解析逻辑原样保留；import 提到文件顶部）

- [ ] **Step 2: 改/加测试**

`packages/core/tests/correlation/test_queue_merge.py`：构造子仓 queue 放 `whitebox/` 子目录，断言 merge 能读到；同时保留老结构（queue 在根）用例验证 fallback。

- [ ] **Step 3: 运行验证通过**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/correlation/test_queue_merge.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add packages/multi/src/shannon_multi/orchestrator.py packages/core/tests/correlation/test_queue_merge.py
git commit -m "feat(multi): 关联读子仓白盒 queue 走 whitebox/ fallback"
```

---

## Task 9: 集成测试 + 回归守卫

**Files:**
- Test: `tests/integration/test_whitebox_blackbox_handoff.py`（已存在，改/加断言）

**Interfaces:**
- Consumes: Task 1-8 全部
- Produces: 验收标准 1-6 全部满足

- [ ] **Step 1: 加集成测试断言**

在 `tests/integration/test_whitebox_blackbox_handoff.py` 中加（或改现有 handoff 测试）：
```python
def test_whitebox_and_blackbox_reports_coexist_without_overwrite(tmp_path, ...):
    """黑白盒同 workspace：两份 report 分居 whitebox/ 与 blackbox/，共存不覆盖。"""
    # 跑白盒 → whitebox/comprehensive_security_assessment_report.md 存在
    # 跑黑盒（复用同 workspace）→ blackbox/comprehensive_security_assessment_report.md 存在
    # whitebox/ 那份仍在（未被覆盖）
    assert (deliverables / "whitebox" / "comprehensive_security_assessment_report.md").exists()
    assert (deliverables / "blackbox" / "comprehensive_security_assessment_report.md").exists()
```

- [ ] **Step 2: 运行集成测试**

Run: `cd /root/shannon-py && python -m pytest tests/integration/test_whitebox_blackbox_handoff.py -v`
Expected: PASS（若该文件在预存挂起名单，用 `-k` 只跑新断言或标记跳过预存部分）

- [ ] **Step 3: 全包改动相关测试回归**

Run:
```bash
cd /root/shannon-py && python -m pytest packages/core/tests/test_paths.py packages/core/tests/correlation/test_queue_merge.py packages/core/tests/services/test_findings_renderer.py packages/whitebox/tests/test_run_merge_dual_track.py packages/whitebox/tests/test_assemble_report.py packages/whitebox/tests/test_run_authz_gitnexus_judge.py packages/whitebox/tests/test_run_gitnexus_chain_verdict.py packages/whitebox/tests/test_run_auth_config_scan.py packages/blackbox/tests/test_executors.py packages/blackbox/tests/test_coverage_renderer.py packages/blackbox/tests/test_exploitation_checker.py packages/blackbox/tests/test_assemble_report.py packages/blackbox/tests/test_blackbox_rerun.py packages/blackbox/tests/test_workflows.py -v
```
Expected: PASS（避开预存挂起文件）

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_whitebox_blackbox_handoff.py
git commit -m "test(integration): 守卫黑白盒 report 分居 whitebox/+blackbox/ 共存不覆盖"
```

---

## Self-Review 已执行

- **Spec 覆盖**：spec §2.1 目标 1（白盒→whitebox/）= Task 2；目标 1（黑盒→blackbox/）= Task 3-5；目标 2（报告不覆盖）= Task 5+9；目标 3（桥接/rerun）= Task 6+7；目标 4（fallback）= Task 1+各读侧 task。spec §4.2 ①白盒写=Task2 ②黑盒写=Task3-5 ③读桥接=Task4-6+8 ④rerun=Task7。spec §6 已知局限（老 report 救不回）在 Task 1 fallback 语义体现。
- **占位符**：无 TBD/TODO；每个改点有 before/after 代码。
- **类型一致**：`resolve_track_deliverable(deliverables_dir, track, filename)`、`whitebox_dir(deliverables_dir)`、`blackbox_dir(deliverables_dir)` 签名在所有 task 一致；`WHITEBOX_SUBDIR`/`BLACKBOX_SUBDIR` 常量名一致。
- **spec 签名回填**：spec §4.1 写 `resolve_track_deliverable(workspace_path, ...)`，plan 细化为 `deliverables_dir`——需同步修正 spec（plan 写完后执行）。

## Execution Handoff

见 plan 末尾的执行选择。
