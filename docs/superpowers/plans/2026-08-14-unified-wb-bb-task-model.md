# 统一白盒/黑盒任务数据模型（Unified WB-BB Task Model）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把扫描任务重塑为「一次白盒运行（根）+ N 次版本化黑盒 run（共存）+ per-run 融合报告」的唯一范式——黑盒 run 嵌套进白盒任务目录的 `blackbox-runs/run-K/`，组合扫描降级为「白盒完自动接力 run-1」的便捷入口，三种发起方式收敛到同一终态。

**Architecture:** 在已落地的 2026-08-12 组合扫描（单 `bb_phase`/单目录三桶/`_combined_orchestrator`）之上加一层**版本化多 run**：黑盒 run 不再是平级 `~N` 目录或单 `bb_phase`，而是任务根下的 `blackbox-runs/run-K/` 子目录（per-run 独立 session/events/deliverables），由任务级 session 的 `bb_runs[]` 索引（非扫盘）。`bb_phase`/`bb_reason` 下沉到 run 级 session；任务级用 `bb_runs[]` + `latest_bb_run` 表达。白盒/黑盒 workflow **零代码改动**（只靠 `event_file.parent` 推 workspace_path 的不变量）。编排核心（`_run_blackbox_phase`/`_combined_orchestrator`）复用，仅把 event_file 落点迁到 `blackbox-runs/run-K/events.ndjson`、融合报告落点迁到 `combined/run-K/`。

**Tech Stack:** Python 3.11+ / FastAPI / Temporalio / pytest（后端）；React 18 + TypeScript + Vite + TanStack + vitest + MSW（前端 `packages/web/frontend`）。

**Spec:** `docs/superpowers/specs/2026-08-14-unified-wb-bb-task-model-design.md`（数据模型/目录布局/组件改动清单）。编排/接力/预验证/进度机制承接 `docs/superpowers/specs/2026-08-12-combined-wb-bb-scan-design.md`（已落地，本 plan 仅迁移其落点到 per-run 子目录）。

## Global Constraints

逐条从 spec §3 抄录，每个 task 的需求隐式包含本节：

- **白盒 workflow 零改动**：`event_file = <wb_scan_dir>/events.ndjson` → workspace_path = scan_dir → 产物落 `deliverables/whitebox/`。任何 task 不得改 `packages/whitebox/`。
- **黑盒 workflow 零改动**：黑盒从 `event_file.parent` 推 workspace_path；run 的 event_file 指 `blackbox-runs/run-K/events.ndjson` → 产物落 `run-K/deliverables/blackbox/`；`repo_path` 指 wb 任务根 → 读 `deliverables/whitebox/` 的 queue。任何 task 不得改 `packages/blackbox/`。
- **run 由 session 索引、非扫盘发现**：黑盒 run 不进顶层 scan 列表，不靠 `iterdir()` 发现，写进任务 session 的 `bb_runs[]`。
- **scan_end 语义**：每 run 的 events 各自唯一 `scan_end`（per-run 隔离天然消除「三方共写一个 events」的协调负担）。
- **纯白盒零回归**：开关关时行为/产物布局与今天完全一致（仅 `deliverables/whitebox/`）。
- **不引入新 scan_type**：任务根的 `scan_type` 仍是 `whitebox`。
- **不做生产数据迁移**：现有 `~N` / 已跑组合扫描为本地 dev 数据（spec §10）。`~N` 作只读遗留，列表隐藏。
- **run_id 格式**：`run-K`，K = per-task 单调序号（从 1 起）。run_id 校验正则 `^run-\\d+$`。
- **workflow_id 命名（run）**：run-K 黑盒 = `{ws}-{scan_id}-bb-{K}`（base 经现有 `_resolve_workflow_id`，append `-bb-{K}`）。**取代**旧的 `-bb` / `-bb-rerun-N`。
- **认证明文不进 session.json**：run 条目 `auth_ref` 仅存 `profile_id`；明文快照 = `<wb_scan_dir>/scan-config.yaml`，各 run 共用。

### 预存陷阱（每个 task 隐式遵守）

- **前端命令须 `cd packages/web/frontend`**（cwd 不持久）：用 `./node_modules/.bin/vitest|tsc|vite`，**别用 pnpm**（repo 用 npm）。
- **改 web/worker src 须 rebuild `supernova-worker` 镜像**：`uv sync --all-packages`。
- **pytest 只跑改动相关子集**（全套会 hang）：每个后端 task 只跑该 task 点名的测试文件。
- **预存失败测试勿误修**：`test_submit_whitebox_combined_flag_is_forwarded`、`test_run_blackbox_phase_without_auth_passes_no_config_path` 是 2026-08-12 scope 的预存失败（非本 plan 引入），除非本 plan task 明确点名，否则不动。
- 路径基准：`/root/shannon-py`（Linux 容器）；本地 dev 为 `/Users/mango/project/shannon-refactor/shannon-py`。

---

## File Structure

新增 / 改动文件与职责（spec §13 钉死位置）：

| 文件 | 职责 | 动作 |
|---|---|---|
| `packages/core/src/supernova_core/utils/paths.py` | 路径常量 + helper | **加** `BLACKBOX_RUNS_SUBDIR` + `blackbox_runs_dir/blackbox_run_dir/combined_run_dir` |
| `packages/web/src/supernova_web/components/scan_store.py` | scan 创建/id/lineage/列举/定位/删除 | **加** `create_blackbox_run/get_blackbox_run_dir/list_blackbox_runs/update_blackbox_run/delete_blackbox_run`；改 `_summarize`（latest-run progress）/`list_scans`（隐藏 `~N`）/`ScanSummary`（+bb_runs/latest_bb_run） |
| `packages/web/src/supernova_web/components/scan_manager.py` | 编排/接力/resume/cancel/reconcile/workflow_id | 改 `_run_blackbox_phase`/`_combined_orchestrator`/`rerun_blackbox`/`_resolve_workflow_id`/`_reconcile_combined_scan`/`resume`/`cancel`/`start` 黑盒分支；加 `_mark_run`/`_add_blackbox_run` |
| `packages/web/src/supernova_web/components/combined_report_renderer.py` | 融合报告渲染 | **改签名** `render_combined_report(*, whitebox_root, blackbox_root, out_dir)` 双路径 |
| `packages/web/src/supernova_web/api/scans.py` | 列表/详情/产物端点 | 加 run 级路由；`_scan_detail` 透传 `bb_runs[]` |
| `packages/web/src/supernova_web/models.py` | ScanRequest + validators | 复用现有（组合 validator 已落地）；加「给已有白盒加黑盒」入口校验（复用现有字段） |
| `packages/web/frontend/src/api/types.ts` | 前端类型 | 加 `BlackboxRunSummary`；`ScanSummary`/`SessionData` 加 `bb_runs/latest_bb_run` |
| `packages/web/frontend/src/api/client.ts` | API 客户端 | 加 `listBlackboxRuns/addBlackboxToWhitebox/getBlackboxRun` + run-scoped 路径 helper |
| `packages/web/frontend/src/routes/WorkspaceDetail/ScanList.tsx` | 列表卡片 | 加内嵌 run 列表（嵌套） |
| `packages/web/frontend/src/routes/WorkspaceDetail/ScanDetail.tsx` | 详情 | 加 run 选择器 |
| `packages/web/frontend/src/routes/WorkspaceDetail/ReportTab.tsx` / `DeliverablesTab.tsx` | 报告/产物 | per-run 视图 |
| `packages/web/frontend/src/locales/{zh,en}.json` | i18n | 加 run 相关 key |

**零改动可复用（spec §7.2，核查实证，禁止改）**：白盒 workflow 全文 / 黑盒 workflow 全文 / `deliverables_reader._infer_track`（传对目录即不变）/ `scan_store.resolve_workflow_id` + `_read_workflow_id_from_ndjson`（读 ndjson 首行）/ `_submit_whitebox`/`_submit_blackbox`（调用方传对参数）/ `_run_precheck`/`_compute_progress_pct`（纯函数）。

---

## 任务依赖图

```
T1(paths) → T2(create_run) → T3(list/update/delete/summarize) → T5(_run_blackbox_phase) ─┐
T4(renderer 双签名) → T5                                                                  ├→ T7(orchestrator) → T8(rerun) → T9/T10/T11(lifecycle)
T5 → T6(_add_blackbox_run + start 分支) ──────────────────────────────────────────────────┘
T2 → T12(API detail/list runs) → T13(API run 端点 + POST add-run)
T13 → T14(types/client) → T15(ScanList) → T16(ScanDetail+report/deliverables) → T17(add 入口+i18n)
全 → T18(e2e 冒烟)
```

---

## Phase A — Foundation & 数据模型（scan_store + paths）

### Task 1: paths.py run 级 helper

**Files:**
- Modify: `packages/core/src/supernova_core/utils/paths.py`（在 `combined_dir` 之后加常量 + 3 helper）
- Test: `packages/core/tests/test_paths.py`（`TestTrackSubdirHelpers` 类内加用例）

**Interfaces:**
- Produces: `BLACKBOX_RUNS_SUBDIR: str`、`blackbox_runs_dir(scan_dir: Path) -> Path`、`blackbox_run_dir(scan_dir: Path, run_id: str) -> Path`、`combined_run_dir(scan_dir: Path, run_id: str) -> Path`。注意：这三个 helper 接收 **scan_dir（任务根）**，不是 deliverables_dir——因为 `blackbox-runs/` 和 `combined/` 是任务根下 `deliverables/` 的平级（spec §4）。

- [ ] **Step 1: Write the failing test**

在 `test_paths.py` 的 `TestTrackSubdirHelpers` 类内追加：

```python
def test_blackbox_runs_helpers(self, tmp_path):
    from supernova_core.utils.paths import (
        BLACKBOX_RUNS_SUBDIR, blackbox_runs_dir, blackbox_run_dir, combined_run_dir)
    scan_dir = tmp_path / "repo-ts"
    assert BLACKBOX_RUNS_SUBDIR == "blackbox-runs"
    assert blackbox_runs_dir(scan_dir) == scan_dir / "blackbox-runs"
    assert blackbox_run_dir(scan_dir, "run-1") == scan_dir / "blackbox-runs" / "run-1"
    assert combined_run_dir(scan_dir, "run-1") == scan_dir / "combined" / "run-1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_paths.py::TestTrackSubdirHelpers::test_blackbox_runs_helpers -q`
Expected: FAIL `ImportError: cannot import name 'BLACKBOX_RUNS_SUBDIR'`

- [ ] **Step 3: Write minimal implementation**

在 `paths.py` 的 `combined_dir` 定义之后追加：

```python
BLACKBOX_RUNS_SUBDIR: str = "blackbox-runs"


def blackbox_runs_dir(scan_dir: Path) -> Path:
    """任务根下的 blackbox-runs/ 父目录（spec §4）。"""
    return Path(scan_dir) / BLACKBOX_RUNS_SUBDIR


def blackbox_run_dir(scan_dir: Path, run_id: str) -> Path:
    """单个黑盒 run 子目录：scan_dir/blackbox-runs/{run_id}/。"""
    return blackbox_runs_dir(scan_dir) / run_id


def combined_run_dir(scan_dir: Path, run_id: str) -> Path:
    """per-run 融合报告目录：scan_dir/combined/{run_id}/（spec §4/§9）。"""
    return Path(scan_dir) / COMBINED_SUBDIR / run_id
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_paths.py::TestTrackSubdirHelpers -q`
Expected: PASS（含既有 whitebox/blackbox/combined 用例）

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/supernova_core/utils/paths.py packages/core/tests/test_paths.py
git commit -m "feat(paths): blackbox run / combined run dir helpers"
```

---

### Task 2: scan_store create_blackbox_run + 序号 + get_blackbox_run_dir

**Files:**
- Modify: `packages/web/src/supernova_web/components/scan_store.py`（`create_scan` 之后加 3 方法；import `blackbox_runs_dir/blackbox_run_dir/combined_run_dir`）
- Test: `packages/web/tests/test_scan_store.py`

**Interfaces:**
- Consumes: T1 的 `blackbox_runs_dir/blackbox_run_dir`；现有 `SessionManager(workspace_path).update_session/get_session_data`、`get_scan_dir`、`_now_iso`（若无则用 `datetime.now().isoformat()`）。
- Produces: `create_blackbox_run(ws, wb_scan_id, *, auth_ref=None, reason=None) -> tuple[str, Path]`（返 `(run_id, run_dir)`）、`get_blackbox_run_dir(ws, wb_scan_id, run_id) -> Path | None`。后续 task（T3/T5/T6）依赖这两个。

- [ ] **Step 1: Write the failing test**

在 `test_scan_store.py` 追加：

```python
def test_create_blackbox_run_allocates_run1_under_task(tmp_path):
    store = ScanStore(tmp_path)
    wb_id, wb_dir = store.create_scan("WS", "http://e", "/code/x")  # 白盒任务根
    run_id, run_dir = store.create_blackbox_run("WS", wb_id)
    assert run_id == "run-1"
    assert run_dir == wb_dir / "blackbox-runs" / "run-1"
    assert (run_dir / "session.json").exists()
    run_sess = json.loads((run_dir / "session.json").read_text())
    assert run_sess["status"] == "pending"
    assert run_sess["bb_phase"] == "pending"
    # 任务级 session 索引 bb_runs[] + latest_bb_run + combined
    task_sess = json.loads((wb_dir / "session.json").read_text())
    assert task_sess["combined"] is True
    assert task_sess["latest_bb_run"] == "run-1"
    assert task_sess["bb_runs"] == [{"run_id": "run-1", "status": "pending"}]


def test_create_blackbox_run_monotonic_per_task(tmp_path):
    store = ScanStore(tmp_path)
    wb_id, wb_dir = store.create_scan("WS", "http://e", "/code/x")
    r1, _ = store.create_blackbox_run("WS", wb_id)
    r2, _ = store.create_blackbox_run("WS", wb_id)
    assert (r1, r2) == ("run-1", "run-2")
    task_sess = json.loads((wb_dir / "session.json").read_text())
    assert [r["run_id"] for r in task_sess["bb_runs"]] == ["run-1", "run-2"]
    assert task_sess["latest_bb_run"] == "run-2"


def test_get_blackbox_run_dir_validates_run_id(tmp_path):
    store = ScanStore(tmp_path)
    wb_id, _ = store.create_scan("WS", "http://e", "/code/x")
    store.create_blackbox_run("WS", wb_id)
    assert store.get_blackbox_run_dir("WS", wb_id, "run-1").name == "run-1"
    assert store.get_blackbox_run_dir("WS", wb_id, "run-9") is None  # 不存在
    assert store.get_blackbox_run_dir("WS", wb_id, "../etc") is None  # 越界
    assert store.get_blackbox_run_dir("WS", wb_id, "run-x") is None  # 非法格式
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/web/tests/test_scan_store.py::test_create_blackbox_run_allocates_run1_under_task packages/web/tests/test_scan_store.py::test_create_blackbox_run_monotonic_per_task packages/web/tests/test_scan_store.py::test_get_blackbox_run_dir_validates_run_id -q`
Expected: FAIL `AttributeError: 'ScanStore' object has no attribute 'create_blackbox_run'`

- [ ] **Step 3: Write minimal implementation**

在 `scan_store.py` 顶部 import 区追加（合并进现有 `from supernova_core.utils.paths import ...`）：

```python
from supernova_core.utils.paths import (
    blackbox_runs_dir, blackbox_run_dir, combined_run_dir)
```

在 `create_scan` 方法之后追加：

```python
import re as _re
_RUN_ID_RE = _re.compile(r"^run-(\d+)$")

def _next_blackbox_run_seq(self, wb_dir: Path) -> int:
    """per-task run 序号：扫 blackbox-runs/run-<N> 取 max+1（从 1 起）。"""
    runs_dir = blackbox_runs_dir(wb_dir)
    if not runs_dir.is_dir():
        return 1
    seqs = []
    for d in runs_dir.iterdir():
        m = _RUN_ID_RE.match(d.name)
        if m and d.is_dir():
            seqs.append(int(m.group(1)))
    return (max(seqs) + 1) if seqs else 1

def create_blackbox_run(self, ws: str, wb_scan_id: str, *,
                        auth_ref: dict | None = None,
                        reason: str | None = None) -> tuple[str, Path]:
    """在白盒任务根下分配 run-K 子目录（spec §4/§5.1）。

    - 建 blackbox-runs/run-K/ + run 级 session.json（status=pending, bb_phase=pending）。
    - 任务级 session 追加 bb_runs[] 条目 + 设 latest_bb_run + combined=True。
    序号并发由 ScanManager 的 _create_scan_lock 串行化（与 create_scan 同 lock 口径）。
    """
    wb_dir = self.get_scan_dir(ws, wb_scan_id)
    if wb_dir is None:
        raise ValueError(f"白盒任务不存在: {wb_scan_id}")
    k = self._next_blackbox_run_seq(wb_dir)
    run_id = f"run-{k}"
    run_dir = blackbox_run_dir(wb_dir, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    # run 级 session（spec §5.3：bb_phase/bb_reason 下沉到此）
    started = datetime.now().isoformat()
    SessionManager(blackbox_runs_dir(wb_dir)).update_session(run_dir, {
        "status": "pending", "bb_phase": "pending", "started_at": started,
        "expected_agents": {}, "completed_agents": [], "host_mappings": {},
    })
    # 任务级索引
    task_mgr = SessionManager(wb_dir.parent)
    task_data = task_mgr.get_session_data(wb_dir)
    runs = list(task_data.get("bb_runs") or [])
    runs.append({"run_id": run_id, "status": "pending",
                 "auth_ref": auth_ref or {}, "reason": reason})
    task_mgr.update_session(wb_dir, {
        "bb_runs": runs, "latest_bb_run": run_id, "combined": True})
    return run_id, run_dir

def get_blackbox_run_dir(self, ws: str, wb_scan_id: str, run_id: str) -> Path | None:
    """定位 run 子目录（spec §7.1 #1）。run_id 须 ^run-\\d+$；不存在/越界 → None。"""
    if not run_id or not _RUN_ID_RE.match(run_id):
        return None
    wb_dir = self.get_scan_dir(ws, wb_scan_id)
    if wb_dir is None:
        return None
    run_dir = blackbox_run_dir(wb_dir, run_id)
    return run_dir if (run_dir / "session.json").exists() else None
```

> 注：若 `scan_store.py` 顶部已 `import re`，复用之（去掉 `_re as` 别名）；若已 import `datetime` 则复用。实现时合并到现有 import，勿重复。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/web/tests/test_scan_store.py -k blackbox_run -q`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/supernova_web/components/scan_store.py packages/web/tests/test_scan_store.py
git commit -m "feat(scan_store): create_blackbox_run + per-task seq + get_blackbox_run_dir"
```

---

### Task 3: scan_store list/update/delete run + ScanSummary 透传 + 隐藏 legacy ~N

**Files:**
- Modify: `packages/web/src/supernova_web/components/scan_store.py`（加 `list_blackbox_runs/update_blackbox_run/delete_blackbox_run`；改 `ScanSummary` dataclass + `_summarize` + `_scan_entries`）
- Test: `packages/web/tests/test_scan_store.py`

**Interfaces:**
- Consumes: T2 的 `create_blackbox_run/get_blackbox_run_dir`、`_compute_progress_pct`、`combined_run_dir`。
- Produces: `list_blackbox_runs(ws, wb_scan_id) -> list[dict]`、`update_blackbox_run(ws, wb_scan_id, run_id, *, status=None, phase=None, reason=None, completed_at=None)`、`delete_blackbox_run(ws, wb_scan_id, run_id) -> bool`；`ScanSummary` 新增 `bb_runs`/`latest_bb_run` 字段并进 `as_dict()`；`_summarize` 在 combined 时合并 latest-run 的 completed_agents + bb_phase 算进度。

- [ ] **Step 1: Write the failing test**

```python
def test_list_update_delete_blackbox_run(tmp_path):
    store = ScanStore(tmp_path)
    wb_id, wb_dir = store.create_scan("WS", "http://e", "/code/x")
    r1, _ = store.create_blackbox_run("WS", wb_id)
    r2, _ = store.create_blackbox_run("WS", wb_id)
    # list 从 session bb_runs[] 取
    runs = store.list_blackbox_runs("WS", wb_id)
    assert [r["run_id"] for r in runs] == ["run-1", "run-2"]
    # update：写 run session + 任务索引条目 + latest_bb_run
    store.update_blackbox_run("WS", wb_id, r1, status="completed", phase="completed")
    runs = store.list_blackbox_runs("WS", wb_id)
    assert next(r for r in runs if r["run_id"] == "run-1")["status"] == "completed"
    run_sess = json.loads((wb_dir / "blackbox-runs" / "run-1" / "session.json").read_text())
    assert run_sess["bb_phase"] == "completed"
    # delete：rmtree run + combined/run-K + 移除 bb_runs[] 条目
    assert store.delete_blackbox_run("WS", wb_id, r2) is True
    assert not (wb_dir / "blackbox-runs" / "run-2").exists()
    assert store.list_blackbox_runs("WS", wb_id) == [
        {"run_id": "run-1", "status": "completed"}]


def test_summarize_combined_progress_uses_latest_run_phase(tmp_path):
    store = ScanStore(tmp_path)
    wb_id, wb_dir = store.create_scan("WS", "http://e", "/code/x")
    SessionManager(wb_dir.parent).update_session(wb_dir, {
        "expected_agents": {"whitebox": 4, "blackbox": 2},
        "completed_agents": ["a", "b", "c", "d"]})  # 白盒 4 完成
    r1, run_dir = store.create_blackbox_run("WS", wb_id)
    # run-1 跑到 running，黑盒完成 1/2
    SessionManager(run_dir.parent).update_session(run_dir, {
        "bb_phase": "running", "completed_agents": ["e"]})
    summ = store.list_scans("WS")[0]
    # combined + running 分段：55 + 45*(1/2) = 77.5
    assert summ.combined is True
    assert summ.bb_phase == "running"  # 取自 latest run
    assert summ.progress_pct == 77.5
    assert summ.latest_bb_run == "run-1"
    assert summ.bb_runs[-1]["run_id"] == "run-1"


def test_list_scans_hides_legacy_tilde_n(tmp_path):
    store = ScanStore(tmp_path)
    wb_id, _ = store.create_scan("WS", "http://e", "/code/x")
    # 手造一个 legacy 平级 ~N 目录
    legacy = tmp_path / "WS" / "scans" / f"{wb_id}~1"
    legacy.mkdir(parents=True)
    (legacy / "session.json").write_text('{"scan_type":"blackbox","created_at":"2026-01-01T00:00:00"}')
    ids = [s.scan_id for s in store.list_scans("WS")]
    assert wb_id in ids
    assert f"{wb_id}~1" not in ids  # legacy 隐藏
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/web/tests/test_scan_store.py -k "list_update_delete_blackbox_run or summarize_combined_progress_uses_latest_run_phase or list_scans_hides_legacy" -q`
Expected: FAIL（方法/字段不存在 + progress 不读 run）

- [ ] **Step 3: Write minimal implementation**

(a) `ScanSummary` dataclass（约 L211-264）加两字段（在 `progress_pct` 之后）：

```python
    bb_runs: list[dict] | None = None
    latest_bb_run: str | None = None
```

并在 `as_dict()`（同 dataclass 内）末尾追加：

```python
        "bb_runs": self.bb_runs,
        "latest_bb_run": self.latest_bb_run,
```

(b) `_scan_entries`（L338）源①循环内，跳过 legacy `~N`：把

```python
            for scan_dir in mgr.list_workspaces():
                created = _to_unix(mgr.get_created_at(scan_dir)) or 0.0
                entries.append((scan_dir.name, scan_dir, created))
```

改为：

```python
            for scan_dir in mgr.list_workspaces():
                if "~" in scan_dir.name and scan_dir.name.rsplit("~", 1)[-1].isdigit():
                    continue  # legacy 平级 <wb>~N 黑盒：隐藏（spec §10 只读遗留）
                created = _to_unix(mgr.get_created_at(scan_dir)) or 0.0
                entries.append((scan_dir.name, scan_dir, created))
```

(c) `_summarize`（L368）：combined 时合并 latest-run 的 completed_agents + bb_phase。把现有的：

```python
        combined = data.get("combined") if isinstance(data, dict) else None
        bb_phase = data.get("bb_phase") if isinstance(data, dict) else None
        bb_reason = data.get("bb_reason") if isinstance(data, dict) else None
        progress_pct = _compute_progress_pct(status, combined, bb_phase, data)
```

替换为：

```python
        combined = data.get("combined") if isinstance(data, dict) else None
        bb_phase = data.get("bb_phase") if isinstance(data, dict) else None
        bb_reason = data.get("bb_reason") if isinstance(data, dict) else None
        bb_runs = data.get("bb_runs") if isinstance(data, dict) else None
        latest_bb_run = data.get("latest_bb_run") if isinstance(data, dict) else None
        progress_data = data
        if combined and latest_bb_run:
            run_dir = blackbox_run_dir(scan_dir, latest_bb_run)
            if (run_dir / "session.json").exists():
                run_data = SessionManager(run_dir.parent).get_session_data(run_dir)
                bb_phase = run_data.get("bb_phase", bb_phase)
                bb_reason = run_data.get("bb_reason", bb_reason)
                merged = dict(data)
                merged["completed_agents"] = list(data.get("completed_agents") or []) + \
                    list(run_data.get("completed_agents") or [])
                progress_data = merged
        progress_pct = _compute_progress_pct(status, combined, bb_phase, progress_data)
```

并在 `return ScanSummary(...)` 里加 `bb_runs=bb_runs, latest_bb_run=latest_bb_run,`（保留现有 `combined/bb_phase/bb_reason/progress_pct`）。

(d) 新增三个方法（放在 `get_blackbox_run_dir` 之后）：

```python
def list_blackbox_runs(self, ws: str, wb_scan_id: str) -> list[dict]:
    """从任务 session bb_runs[] 读 run 列表（非扫盘，spec §3 铁律）。"""
    wb_dir = self.get_scan_dir(ws, wb_scan_id)
    if wb_dir is None:
        return []
    data = SessionManager(wb_dir.parent).get_session_data(wb_dir)
    return list(data.get("bb_runs") or [])

def update_blackbox_run(self, ws: str, wb_scan_id: str, run_id: str, *,
                        status: str | None = None, phase: str | None = None,
                        reason: str | None = None, completed_at: str | None = None) -> None:
    """更新 run 级 session（bb_phase/bb_reason/status）+ 任务 bb_runs[] 条目 + latest_bb_run。"""
    run_dir = self.get_blackbox_run_dir(ws, wb_scan_id, run_id)
    if run_dir is None:
        raise ValueError(f"run 不存在: {run_id}")
    patch: dict = {}
    if phase is not None:
        patch["bb_phase"] = phase
    if reason is not None:
        patch["bb_reason"] = reason
    if status is not None:
        patch["status"] = status
    if completed_at is not None:
        patch["completed_at"] = completed_at
    if patch:
        SessionManager(run_dir.parent).update_session(run_dir, patch)
    # 任务索引条目
    wb_dir = self.get_scan_dir(ws, wb_scan_id)
    task_mgr = SessionManager(wb_dir.parent)
    data = task_mgr.get_session_data(wb_dir)
    runs = list(data.get("bb_runs") or [])
    changed = False
    for r in runs:
        if r.get("run_id") == run_id:
            if status is not None:
                r["status"] = status
            if completed_at is not None:
                r["completed_at"] = completed_at
            if reason is not None:
                r["reason"] = reason
            changed = True
    upd = {"bb_runs": runs, "latest_bb_run": run_id}
    if not changed and runs == []:
        pass
    task_mgr.update_session(wb_dir, upd)

def delete_blackbox_run(self, ws: str, wb_scan_id: str, run_id: str) -> bool:
    """删单 run：rmtree run 子目录 + combined/run-K + 移除 bb_runs[] 条目（spec §7.1 #4）。"""
    run_dir = self.get_blackbox_run_dir(ws, wb_scan_id, run_id)
    if run_dir is None:
        return False
    wb_dir = self.get_scan_dir(ws, wb_scan_id)
    shutil.rmtree(run_dir, ignore_errors=True)
    shutil.rmtree(combined_run_dir(wb_dir, run_id), ignore_errors=True)
    task_mgr = SessionManager(wb_dir.parent)
    data = task_mgr.get_session_data(wb_dir)
    runs = [r for r in (data.get("bb_runs") or []) if r.get("run_id") != run_id]
    latest = runs[-1]["run_id"] if runs else None
    task_mgr.update_session(wb_dir, {"bb_runs": runs, "latest_bb_run": latest})
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/web/tests/test_scan_store.py -q`
Expected: PASS（含本 task 3 用例 + T2 用例 + 既有用例；若有既存失败非本 task 引入则忽略并记录）

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/supernova_web/components/scan_store.py packages/web/tests/test_scan_store.py
git commit -m "feat(scan_store): list/update/delete blackbox run + ScanSummary bb_runs + hide legacy ~N"
```

## Phase B — 编排（scan_manager + renderer）

### Task 4: render_combined_report 双路径签名

**Files:**
- Modify: `packages/web/src/supernova_web/components/combined_report_renderer.py`（`render_combined_report` 改签名）
- Test: `packages/web/tests/test_combined_report_renderer.py`（改既有用例适配新签名 + helper）

**Interfaces:**
- Consumes: 无（纯函数）。
- Produces: `render_combined_report(*, whitebox_root: Path, blackbox_root: Path, out_dir: Path) -> Path`。`whitebox_root` = `<wb>/deliverables/whitebox/`（读 `{vt}_exploitation_queue.json`）；`blackbox_root` = `<wb>/blackbox-runs/run-K/deliverables/blackbox/`（读 `{vt}_exploit_verdicts.json`）；`out_dir` = `<wb>/combined/run-K/`（写 `combined_report.md`）。T5 的 `_generate_combined_report(scan_dir, run_id)` 依赖此签名。

- [ ] **Step 1: Update the test helper + signature usage**

在 `test_combined_report_renderer.py` 把 `_make_scan_dir` / `_write_wb_queue` / `_write_bb_verdicts` 改为产出**两个独立根**（白盒根 + 黑盒根），调用改为 keyword。例如把现有 `out = render_combined_report(scan_dir)` 全部替换为：

```python
def _roots(scan_dir, run="run-1"):
    wb_root = scan_dir / "deliverables" / "whitebox"
    bb_root = scan_dir / "blackbox-runs" / run / "deliverables" / "blackbox"
    out_dir = scan_dir / "combined" / run
    wb_root.mkdir(parents=True, exist_ok=True)
    bb_root.mkdir(parents=True, exist_ok=True)
    return wb_root, bb_root, out_dir

def test_render_writes_to_out_dir(tmp_path):
    wb_root, bb_root, out_dir = _roots(tmp_path)
    (wb_root / "injection_exploitation_queue.json").write_text(
        '{"vulnerabilities":[{"ID":"INJ-1","title":"SQLi","source":"/login"}]}')
    (bb_root / "injection_exploit_verdicts.json").write_text(
        '{"verdicts":[{"vulnerability_id":"INJ-1","status":"exploited","severity":"high"}]}')
    out = render_combined_report(whitebox_root=wb_root, blackbox_root=bb_root, out_dir=out_dir)
    assert out == out_dir / "combined_report.md"
    text = out.read_text("utf-8")
    assert "| injection | 1 | 1 |" in text
    assert "### injection" in text

def test_render_tolerates_missing_blackbox(tmp_path):
    wb_root, bb_root, out_dir = _roots(tmp_path)
    (wb_root / "xss_exploitation_queue.json").write_text('{"vulnerabilities":[{"ID":"X1"}]}')
    # blackbox 根存在但无 verdicts 文件 → 计数 0、不崩溃
    out = render_combined_report(whitebox_root=wb_root, blackbox_root=bb_root, out_dir=out_dir)
    assert "| xss | 1 | 0 |" in out.read_text("utf-8")
```

> 既有用例（如 `test_render_combined_report_full_cross_reference`）改为同样的三根 keyword 调用；`_write_wb_queue` 写 `wb_root`、`_write_bb_verdicts` 写 `bb_root`，断言 `out_dir/combined_report.md`。

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/web/tests/test_combined_report_renderer.py -q`
Expected: FAIL（旧签名 `render_combined_report(scan_dir)` 不接 keyword）

- [ ] **Step 3: Write minimal implementation**

把 `combined_report_renderer.py` 的 `render_combined_report` 整体替换为（其余 `_read_queue/_read_verdicts/_format_*` 不动）：

```python
def render_combined_report(*, whitebox_root: Path, blackbox_root: Path,
                           out_dir: Path) -> Path:
    """生成 per-run 融合报告（spec §9/§10.2）。

    读 whitebox_root/{vt}_exploitation_queue.json + blackbox_root/{vt}_exploit_verdicts.json，
    按 _VULN_CLASSES 交叉，写 out_dir/combined_report.md。
    韧性：任一根缺失/空/损坏都不崩溃 —— emit 0 计数 + 无发现。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / _COMBINED_REPORT_FILENAME
    wb_base = Path(whitebox_root)
    bb_base = Path(blackbox_root)
    # ……（rows / detail_sections 组装逻辑原样保留，把 wb_base/bb_base 用上面的值即可）
```

并把文件顶部 import 里的 `WHITEBOX_SUBDIR/BLACKBOX_SUBDIR/combined_dir` 若不再被引用则删除（`COMBINED_SUBDIR` 同理——out_dir 现由调用方给）。函数体内 `deliverables = Path(scan_dir)/"deliverables"` / `out_dir = combined_dir(deliverables)` / `wb_base = deliverables/WHITEBOX_SUBDIR` / `bb_base = deliverables/BLACKBOX_SUBDIR` 这四行删除，用上面新变量。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/web/tests/test_combined_report_renderer.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/supernova_web/components/combined_report_renderer.py packages/web/tests/test_combined_report_renderer.py
git commit -m "refactor(combined_report_renderer): dual-path signature (whitebox/blackbox/out roots)"
```

---

### Task 5: scan_manager _mark_run + _generate_combined_report(scan_dir, run_id) + _run_blackbox_phase(run_id) 参数化

**Files:**
- Modify: `packages/web/src/supernova_web/components/scan_manager.py`（加 `_mark_run`；改 `_generate_combined_report`/`_run_blackbox_phase` 签名 + 落点）
- Test: `packages/web/tests/test_combined_orchestrator.py`

**Interfaces:**
- Consumes: T1 的 `blackbox_run_dir/combined_run_dir`；T3 的 `update_blackbox_run`；T4 的 `render_combined_report` 新签名；现有 `_submit_blackbox(event_file, repo_path, config_path, workflow_id_suffix)`、`_whitebox_deliverables_ready`、`_count_nonempty_queues`、`_session_host_mappings`、`SessionManager`、`blackbox_dir/whitebox_dir`。
- Produces: `_mark_run(scan_dir, run_id, phase, reason=None, status=None)`；`_generate_combined_report(scan_dir, run_id)`；`_run_blackbox_phase(scan_dir, ws, scan_id, auth_ref, run_id, workflow_id_suffix="-bb-1")`。T7/T8/T9 依赖。

- [ ] **Step 1: Write the failing test**

在 `test_combined_orchestrator.py` 加（沿用其 `mgr` fixture）：

```python
async def test_run_blackbox_phase_event_file_points_to_run_subdir(mgr, tmp_path):
    from supernova_web.components.scan_store import ScanStore
    store = ScanStore(tmp_path); mgr._store = store
    wb_id, scan_dir = store.create_scan("ws", "http://t", "/code/x")
    (scan_dir / "deliverables" / "whitebox").mkdir(parents=True)
    (scan_dir / "deliverables" / "whitebox" / "recon_deliverable.md").write_text("x")
    (scan_dir / "deliverables" / "whitebox" / "injection_exploitation_queue.json").write_text(
        '{"vulnerabilities":[{"id":1}]}')
    run_id, _ = store.create_blackbox_run("ws", wb_id)
    bb_handle = MagicMock(); bb_handle.result = AsyncMock(return_value=None)
    with patch.object(mgr, "_submit_blackbox", new=AsyncMock(return_value=bb_handle)) as sb, \
         patch.object(mgr, "_generate_combined_report", new=AsyncMock()) as gcr, \
         patch.object(mgr, "_mark_run", new=AsyncMock()) as mr:
        await mgr._run_blackbox_phase(scan_dir, "ws", wb_id, {"profile_id": None}, run_id)
        kwargs = sb.call_args.kwargs
        assert kwargs["event_file"] == scan_dir / "blackbox-runs" / "run-1" / "events.ndjson"
        assert kwargs["repo_path"] == str(scan_dir)  # 仍指白盒任务根
        assert kwargs["workflow_id_suffix"] == "-bb-1"
        # run 标 running → completed（_mark_run 而非 _mark_bb）
        phases = [c.args[2] for c in mr.call_args_list]  # (scan_dir, run_id, phase)
        assert "running" in phases and "completed" in phases
        gcr.assert_awaited_with(scan_dir, run_id)


async def test_generate_combined_report_writes_combined_run_dir(mgr, tmp_path):
    from supernova_web.components.scan_store import ScanStore
    store = ScanStore(tmp_path); mgr._store = store
    wb_id, scan_dir = store.create_scan("ws", "http://t", "/code/x")
    run_id, _ = store.create_blackbox_run("ws", wb_id)
    (scan_dir / "deliverables" / "whitebox" / "injection_exploitation_queue.json").write_text(
        '{"vulnerabilities":[{"ID":"INJ-1"}]}')
    (scan_dir / "blackbox-runs" / "run-1" / "deliverables" / "blackbox").mkdir(parents=True)
    (scan_dir / "blackbox-runs" / "run-1" / "deliverables" / "blackbox"
     / "injection_exploit_verdicts.json").write_text(
        '{"verdicts":[{"vulnerability_id":"INJ-1","status":"exploited"}]}')
    await mgr._generate_combined_report(scan_dir, run_id)
    out = scan_dir / "combined" / "run-1" / "combined_report.md"
    assert out.exists()
    assert "| injection | 1 | 1 |" in out.read_text("utf-8")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/web/tests/test_combined_orchestrator.py -k "run_blackbox_phase_event_file or generate_combined_report_writes" -q`
Expected: FAIL（`_run_blackbox_phase` 无 `run_id` 参 / `_generate_combined_report` 无 `run_id` 参）

- [ ] **Step 3: Write minimal implementation**

在 `scan_manager.py` import 区追加：

```python
from supernova_core.utils.paths import (
    blackbox_run_dir, combined_run_dir, blackbox_dir, whitebox_dir)
```

（`whitebox_dir`/`blackbox_dir` 若已 import 则跳过。）

(a) 新增 `_mark_run`（放在 `_mark_bb` 之后）：

```python
async def _mark_run(self, scan_dir: Path, run_id: str, phase: str,
                    reason: str | None = None,
                    status: str | None = None) -> None:
    """run 级 phase 写入（替代 _mark_bb 对 run 的调用，spec §5.2/§5.3）：
    写 run session（bb_phase/bb_reason/status）+ 任务 bb_runs[] 条目 + latest_bb_run。"""
    try:
        self._store.update_blackbox_run(
            self._ws_of(scan_dir), self._scan_id_of(scan_dir), run_id,
            status=status, phase=phase, reason=reason,
            completed_at=_now_iso() if status in ("completed", "failed", "skipped") else None)
    except Exception:  # noqa: BLE001 - best-effort，不阻塞接力
        pass
```

> `_ws_of` / `_scan_id_of`：从 scan_dir 派生 `scan_dir.parent.parent.name` / `scan_dir.name`（与 `_reconcile_combined_scan` 同口径，scan_dir 在 `scans/<id>/`）。若类内已有等价 helper 则复用；否则加：

```python
@staticmethod
def _ws_of(scan_dir: Path) -> str:
    return scan_dir.parent.parent.name  # scans/<ws>/<scan_id> → <ws>

@staticmethod
def _scan_id_of(scan_dir: Path) -> str:
    return scan_dir.name
```

(b) `_generate_combined_report`（L1775）改为接 `run_id`：

```python
async def _generate_combined_report(self, scan_dir: Path, run_id: str) -> None:
    """per-run 融合报告 → combined/run-K/combined_report.md（spec §9）。"""
    from supernova_web.components.combined_report_renderer import render_combined_report
    run_dir = blackbox_run_dir(scan_dir, run_id)
    render_combined_report(
        whitebox_root=whitebox_dir(scan_dir / "deliverables"),
        blackbox_root=blackbox_dir(run_dir / "deliverables"),
        out_dir=combined_run_dir(scan_dir, run_id))
```

(c) `_run_blackbox_phase`（L1622）签名 + 落点改。把签名改为：

```python
async def _run_blackbox_phase(self, scan_dir: Path, ws: str, scan_id: str,
                              auth_ref: dict, run_id: str,
                              workflow_id_suffix: str = "-bb-1") -> None:
```

函数体替换（预检后）：`event_file` 指 run 子目录；expected_agents 写任务级（进度分母，同今天）；phase 写 `_mark_run`：

```python
        if not self._whitebox_deliverables_ready(scan_dir):
            await self._mark_run(scan_dir, run_id, "skipped", reason="白盒无可利用产物",
                                 status="skipped")
            return
        session = SessionManager(scan_dir.parent).get_session_data(scan_dir)
        bb_url = session.get("bb_url") or session.get("web_url") or ""
        host_mappings = self._session_host_mappings(session)
        bb_expected = self._count_nonempty_queues(scan_dir)
        if bb_expected > 0:
            expected = dict(session.get("expected_agents") or {})
            expected["blackbox"] = bb_expected
            try:
                SessionManager(scan_dir.parent).update_session(
                    scan_dir, {"expected_agents": expected})
            except Exception:  # noqa: BLE001
                pass
        run_dir = blackbox_run_dir(scan_dir, run_id)
        bb_handle = await self._submit_blackbox(
            repo_path=str(scan_dir), ws=ws, scan_id=scan_id, scan_dir=scan_dir,
            event_file=run_dir / "events.ndjson", web_url=bb_url,
            config_path=str(scan_dir / "scan-config.yaml"),
            host_mappings=host_mappings, workflow_id_suffix=workflow_id_suffix)
        await self._mark_run(scan_dir, run_id, "running", status="running")
        await bb_handle.result()
        await self._generate_combined_report(scan_dir, run_id)
        await self._mark_run(scan_dir, run_id, "completed", status="completed")
```

> 旧 `_mark_bb(scan_dir, ...)` 调用全部被 `_mark_run(scan_dir, run_id, ...)` 取代。`_mark_bb` 方法保留（其他非 run 路径可能仍用；若 grep 确认无引用可在 T11 后删，但本 task 不删以降风险）。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/web/tests/test_combined_orchestrator.py -q`
Expected: 本 task 2 用例 PASS（既有用例若因签名变化红，在 T7/T8 修复——记录之，勿在本 task 扩散改）

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/supernova_web/components/scan_manager.py packages/web/tests/test_combined_orchestrator.py
git commit -m "feat(scan_manager): _run_blackbox_phase run parameterization + _mark_run + per-run combined report"
```

---

### Task 6: scan_manager _add_blackbox_run（手动加黑盒）+ start 黑盒分支收敛

**Files:**
- Modify: `packages/web/src/supernova_web/components/scan_manager.py`（加 `_add_blackbox_run`；改 `start` 黑盒 create/submit 分支）
- Test: `packages/web/tests/test_scan_manager_blackbox.py`（或新建 `test_add_blackbox_run.py`）

**Interfaces:**
- Consumes: T2 `create_blackbox_run`；T5 `_run_blackbox_phase(run_id)`、`_mark_run`；现有 `_run_precheck`、`_dump_auth_config`、`_snapshot_auth_ref`、`_resolve_blackbox_inputs`、`_host_config_mappings`、`_create_scan_lock`。
- Produces: `_add_blackbox_run(ws, wb_scan_id, req: ScanRequest | None = None) -> str`（返 run_id；做预验证 → create_blackbox_run → fire orchestrator）。T8 rerun + API POST add-run 复用。

- [ ] **Step 1: Write the failing test**

```python
async def test_add_blackbox_run_creates_run_and_fires_orchestrator(mgr, tmp_path):
    from supernova_web.components.scan_store import ScanStore
    store = ScanStore(tmp_path); mgr._store = store
    wb_id, scan_dir = store.create_scan("ws", "http://t", "/code/x")
    (scan_dir / "deliverables" / "whitebox").mkdir(parents=True)
    (scan_dir / "deliverables" / "whitebox" / "recon_deliverable.md").write_text("x")
    (scan_dir / "deliverables" / "whitebox" / "injection_exploitation_queue.json").write_text(
        '{"vulnerabilities":[{"id":1}]}')
    with patch.object(mgr, "_run_precheck", new=AsyncMock(return_value=True)), \
         patch.object(mgr, "_rerun_orchestrator", new=AsyncMock()) as orch:
        run_id = await mgr._add_blackbox_run("ws", wb_id)
    assert run_id == "run-1"
    assert (scan_dir / "blackbox-runs" / "run-1" / "session.json").exists()
    orch.assert_awaited()  # 编排被 fire


async def test_add_blackbox_run_precheck_fail_marks_run_failed(mgr, tmp_path):
    from supernova_web.components.scan_store import ScanStore
    store = ScanStore(tmp_path); mgr._store = store
    wb_id, scan_dir = store.create_scan("ws", "http://t", "/code/x")
    (scan_dir / "scan-config.yaml").write_text("url: http://t")  # 有认证 → 走 precheck
    with patch.object(mgr, "_run_precheck", new=AsyncMock(return_value=False)), \
         patch.object(mgr, "_mark_run", new=AsyncMock()) as mr:
        run_id = await mgr._add_blackbox_run("ws", wb_id)
    mr.assert_await_with(scan_dir, "run-1", "failed", reason="auth_failed", status="failed")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/web/tests/test_scan_manager_blackbox.py -k add_blackbox_run -q`
Expected: FAIL `AttributeError: _add_blackbox_run`

- [ ] **Step 3: Write minimal implementation**

加 `_rerun_orchestrator`（run 版通用编排，替代旧 `_rerun_blackbox_orchestrator` + `_combined_report_orchestrator` 的 run 化）：

```python
async def _rerun_orchestrator(self, scan_key: tuple[str, str], scan_dir: Path,
                              ws: str, scan_id: str, auth_ref: dict,
                              run_id: str, suffix: str) -> None:
    """run 版编排：_run_blackbox_phase(run_id, suffix) → try/except/finally 经 _ensure_scan_end。"""
    final_status = "completed"
    try:
        await self._run_blackbox_phase(
            scan_dir, ws, scan_id, auth_ref, run_id, workflow_id_suffix=suffix)
    except Exception as exc:
        final_status = "failed"
        await self._mark_run(scan_dir, run_id, "failed", reason=str(exc), status="failed")
    finally:
        await self._ensure_scan_end(scan_dir, status=final_status)
        self._orchestrator_tasks.pop(scan_key, None)
```

加 `_add_blackbox_run`：

```python
async def _add_blackbox_run(self, ws: str, wb_scan_id: str,
                            req: ScanRequest | None = None) -> str:
    """给已有白盒任务加一个黑盒 run（spec §6/§7.1 #8 手动入口 + 纯黑盒入口收敛）。

    流程：预验证（req 给了认证或 scan-config 存在）→ create_blackbox_run → fire
    _rerun_orchestrator(run_id, -bb-{K})。预验证 fail → run 标 failed。
    """
    scan_dir = self._store.get_scan_dir(ws, wb_scan_id)
    if scan_dir is None:
        raise ValueError("白盒任务不存在")
    if not self._whitebox_deliverables_ready(scan_dir):
        raise ValueError("白盒产物未就绪，不能加黑盒")
    # 认证：req 给了就 dump 覆盖 scan-config.yaml；否则沿用现盘
    cfg = scan_dir / "scan-config.yaml"
    if req is not None:
        await self._dump_auth_config(req, ws, scan_dir)
    config_path = str(cfg) if cfg.exists() else None
    data = SessionManager(scan_dir.parent).get_session_data(scan_dir)
    bb_url = data.get("bb_url") or data.get("web_url") or ""
    if req is not None and req.url:
        bb_url = req.url
        SessionManager(scan_dir.parent).update_session(scan_dir, {"bb_url": bb_url})
    # 序号分配须串行（与 create_scan 同 lock）
    async with self._create_scan_lock:
        run_id, run_dir = self._store.create_blackbox_run(
            ws, wb_scan_id, auth_ref=self._snapshot_auth_ref(req) if req else
            (data.get("bb_auth_ref") or {"profile_id": None}))
    k = int(run_id.split("-")[1])
    if config_path and not await self._run_precheck(
            scan_dir, ws, wb_scan_id, bb_url, config_path,
            host_mappings=self._session_host_mappings(data)):
        await self._mark_run(scan_dir, run_id, "failed", reason="auth_failed", status="failed")
        await self._ensure_scan_end(scan_dir, status="failed")
        return run_id
    self._store.update_blackbox_run(ws, wb_scan_id, run_id, phase="pending")
    scan_key = (ws, wb_scan_id)
    self._orchestrator_tasks[scan_key] = asyncio.create_task(
        self._rerun_orchestrator(scan_key, scan_dir, ws, wb_scan_id,
                                 data.get("bb_auth_ref") or {"profile_id": None},
                                 run_id, f"-bb-{k}"))
    return run_id
```

改 `start` 黑盒分支（L201-207 create + L248-256 submit）：纯黑盒（`type=="blackbox"`，带 `reuse_whitebox_scan_id`）不再建平级 `~N`、不再直接 `_submit_blackbox`，改为调 `_add_blackbox_run`。把黑盒 create 分支的 `if req.type == "blackbox":` 块改为走白盒 task 定位 + add-run：

```python
        if req.type == "blackbox":
            wb_scan_id = req.reuse_whitebox_scan_id
            # 不再 create_scan 平级目录；复用白盒任务根，加 run（spec §2 收敛）
            scan_dir = self._store.get_scan_dir(ws, wb_scan_id)
            if scan_dir is None:
                raise ValueError("复用的白盒任务不存在")
            scan_id = wb_scan_id
```

并在黑盒 submit 分支（L248）替换为：

```python
        elif req.type == "blackbox":
            await self._add_blackbox_run(ws, scan_id, req)
            return scan_id, str(scan_dir)
```

> 旧行 `_resolve_blackbox_inputs`/`_host_config_mappings`/直接 `_submit_blackbox` 的黑盒分支代码删除（其职责并入 `_add_blackbox_run`）。`_submit_blackbox` 方法保留（被 `_run_blackbox_phase` 复用）。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/web/tests/test_scan_manager_blackbox.py -k add_blackbox_run packages/web/tests/test_add_blackbox_run.py -q`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/supernova_web/components/scan_manager.py packages/web/tests/test_scan_manager_blackbox.py
git commit -m "feat(scan_manager): _add_blackbox_run manual entry + start blackbox branch folds to nested run"
```

---

### Task 7: scan_manager _combined_orchestrator → create_blackbox_run(run-1)

**Files:**
- Modify: `packages/web/src/supernova_web/components/scan_manager.py`（`_combined_orchestrator` L1549 改为建 run-1）
- Test: `packages/web/tests/test_combined_orchestrator.py`

**Interfaces:**
- Consumes: T2 `create_blackbox_run`；T5 `_run_blackbox_phase(run_id)`、`_mark_run`、`_ensure_scan_end`；现有 `_snapshot_auth_ref`。
- Produces: 组合开关打开时白盒完 → 自动接力 `run-1`（与手动 run-K 走同一 `_run_blackbox_phase`，by-construction 一致）。

- [ ] **Step 1: Write the failing test**

```python
async def test_combined_orchestrator_creates_run1_after_whitebox(mgr, tmp_path):
    from supernova_web.components.scan_store import ScanStore
    store = ScanStore(tmp_path); mgr._store = store
    wb_id, scan_dir = store.create_scan("ws", "http://t", "/code/x")
    (scan_dir / "deliverables" / "whitebox").mkdir(parents=True)
    (scan_dir / "deliverables" / "whitebox" / "recon_deliverable.md").write_text("x")
    (scan_dir / "deliverables" / "whitebox" / "injection_exploitation_queue.json").write_text(
        '{"vulnerabilities":[{"id":1}]}')
    wb_handle = AsyncMock(); wb_handle.result = AsyncMock(return_value=None)
    with patch.object(mgr, "_run_blackbox_phase", new=AsyncMock()) as rbp:
        await mgr._combined_orchestrator(("ws", wb_id), wb_handle, scan_dir,
                                         _combined_req())
        rbp.assert_awaited()
        # 第 5 位参 run_id == "run-1"，第 6 位 suffix == "-bb-1"
        assert rbp.call_args.args[4] == "run-1"
        assert rbp.call_args.kwargs.get("workflow_id_suffix") == "-bb-1"
    assert (scan_dir / "blackbox-runs" / "run-1" / "session.json").exists()
```

（`_combined_req()` 用 `ScanRequest(type="whitebox", url="http://t", source={...}, auth_profile_id=...)` 或既有 fixture。）

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/web/tests/test_combined_orchestrator.py::test_combined_orchestrator_creates_run1_after_whitebox -q`
Expected: FAIL（orchestrator 仍调旧 `_run_blackbox_phase(scan_dir, ws, scan_id, auth_ref)` 无 run_id）

- [ ] **Step 3: Write minimal implementation**

把 `_combined_orchestrator`（L1549）的 try 块替换为：

```python
        ws, scan_id = scan_key
        run_id: str | None = None
        final_status = "completed"
        try:
            await wb_handle.result()
            async with self._create_scan_lock:
                run_id, _ = self._store.create_blackbox_run(
                    ws, scan_id, auth_ref=self._snapshot_auth_ref(req))
            k = int(run_id.split("-")[1])
            await self._run_blackbox_phase(
                scan_dir, ws, scan_id, self._snapshot_auth_ref(req), run_id,
                workflow_id_suffix=f"-bb-{k}")
        except Exception as exc:
            final_status = "failed"
            if run_id is not None:
                await self._mark_run(scan_dir, run_id, "failed", reason=str(exc), status="failed")
        finally:
            await self._ensure_scan_end(scan_dir, status=final_status)
            self._orchestrator_tasks.pop(scan_key, None)
```

> 旧 `_combined_report_orchestrator`（resume 报告-only，L1578）改 run 版：签名加 `run_id`，调 `_generate_combined_report(scan_dir, run_id)` + `_mark_run(scan_dir, run_id, "completed")`。其 resume 调用点在 T9 改。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/web/tests/test_combined_orchestrator.py -q`
Expected: PASS（本 task + T5 用例；既有 `test_orchestrator_success_does_not_write_second_scan_end` 若因签名红，按新签名修其 mock——它断言 `_write_scan_end` 不被调，逻辑不变）

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/supernova_web/components/scan_manager.py packages/web/tests/test_combined_orchestrator.py
git commit -m "feat(scan_manager): combined orchestrator relays to nested run-1"
```

---

### Task 8: scan_manager rerun_blackbox → add-run（rerun 折叠进 runs）

**Files:**
- Modify: `packages/web/src/supernova_web/components/scan_manager.py`（`rerun_blackbox` L1670 改为建下一个 run）
- Test: `packages/web/tests/test_combined_rerun.py`

**Interfaces:**
- Consumes: T6 `_add_blackbox_run`。
- Produces: 「续跑/换认证」= 新建下一个 run（run-K+1），workflow_id `{ws}-{scan_id}-bb-{K+1}`。**取代**旧 `-bb-rerun-N` + `bb_rerun_attempts`。

- [ ] **Step 1: Write the failing test**

```python
async def test_rerun_blackbox_creates_next_run(mgr, tmp_path):
    from supernova_web.components.scan_store import ScanStore
    store = ScanStore(tmp_path); mgr._store = store
    wb_id, scan_dir = store.create_scan("ws", "http://t", "/code/x")
    (scan_dir / "deliverables" / "whitebox").mkdir(parents=True)
    (scan_dir / "deliverables" / "whitebox" / "recon_deliverable.md").write_text("x")
    (scan_dir / "deliverables" / "whitebox" / "injection_exploitation_queue.json").write_text(
        '{"vulnerabilities":[{"id":1}]}')
    # 已有 run-1 失败
    store.create_blackbox_run("ws", wb_id)
    store.update_blackbox_run("ws", wb_id, "run-1", status="failed", phase="failed")
    with patch.object(mgr, "_add_blackbox_run", new=AsyncMock(return_value="run-2")) as abr:
        run_id = await mgr.rerun_blackbox("ws", wb_id)
    assert run_id == "run-2"
    abr.assert_awaited()


async def test_rerun_blackbox_requires_failed_run(mgr, tmp_path):
    from supernova_web.components.scan_store import ScanStore
    store = ScanStore(tmp_path); mgr._store = store
    wb_id, scan_dir = store.create_scan("ws", "http://t", "/code/x")
    store.create_blackbox_run("ws", wb_id)
    store.update_blackbox_run("ws", wb_id, "run-1", status="completed", phase="completed")
    with pytest.raises(ValueError):
        await mgr.rerun_blackbox("ws", wb_id)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/web/tests/test_combined_rerun.py -q`
Expected: FAIL（旧 rerun_blackbox 走 `-bb-rerun-N`，不建 run）

- [ ] **Step 3: Write minimal implementation**

把 `rerun_blackbox`（L1670）整体替换为：

```python
async def rerun_blackbox(self, ws: str, scan_id: str,
                         new_auth: ScanRequest | None = None) -> str:
    """换认证续跑 = 新建下一个 run（spec §8/§11.3 折叠进 runs）。

    前置：latest run 状态为 failed（或无活跃 run）；白盒产物完好。
    返回新 run_id（run-K+1）。new_auth 非空 → 重 dump scan-config.yaml 覆盖。
    """
    scan_dir = self._store.get_scan_dir(ws, scan_id)
    if scan_dir is None:
        raise ValueError("scan 不存在")
    if not self._whitebox_deliverables_ready(scan_dir):
        raise ValueError("白盒产物需完好才能续跑黑盒")
    runs = self._store.list_blackbox_runs(ws, scan_id)
    latest = runs[-1] if runs else None
    if latest and latest.get("status") not in ("failed", "skipped", None):
        raise ValueError("仅 latest run 失败/跳过时可续跑（新建 run）")
    return await self._add_blackbox_run(ws, scan_id, new_auth)
```

> 旧 `_rerun_blackbox_orchestrator`（L1726）删除（被 `_rerun_orchestrator` 取代，T6 已加）。`_cancel_combined`/`resume` 里对 `bb_rerun_attempts` 的引用在 T9/T10 清理。API 层 `rerun_blackbox` 端点（`scans.py`）原返 `{workspace, scan_id}`，改为 `{workspace, scan_id, run_id}`（T13 同步）。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/web/tests/test_combined_rerun.py -q`
Expected: PASS（既有 rerun 用例按新语义改：断言建 run-K+1 而非 `-bb-rerun-N`）

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/supernova_web/components/scan_manager.py packages/web/tests/test_combined_rerun.py
git commit -m "refactor(scan_manager): rerun_blackbox folds into next nested run (drops -bb-rerun-N)"
```

## Phase C — 生命周期（workflow_id / resume / cancel / reconcile）

### Task 9: _resolve_workflow_id run 维度 + resume 按 latest run

**Files:**
- Modify: `packages/web/src/supernova_web/components/scan_manager.py`（`_resolve_workflow_id` L1165 加 run 入参；`resume` L271 改 latest-run 分支）
- Test: `packages/web/tests/test_combined_resume_cancel.py`

**Interfaces:**
- Consumes: T2 `get_blackbox_run_dir/list_blackbox_runs`；T5 `_run_blackbox_phase(run_id)`；现有 `_strip_trailing_scan_end`、`_submit_whitebox`、`_resolve_workflow_id`。
- Produces: run workflow_id = `_resolve_workflow_id(ws, scan_id) + f"-bb-{K}"`；resume 按 latest run 的 bb_phase 分支（running → re-attach `-bb-{K}` + 报告-only orchestrator；无活跃 run/白盒未完 → resume 白盒 + combined orchestrator）。

- [ ] **Step 1: Write the failing test**

```python
async def test_resume_latest_run_running_reattaches_bb_workflow(mgr, tmp_path):
    from supernova_web.components.scan_store import ScanStore
    store = ScanStore(tmp_path); mgr._store = store
    wb_id, scan_dir = store.create_scan("ws", "http://t", "/code/x")
    run_id, run_dir = store.create_blackbox_run("ws", wb_id)
    store.update_blackbox_run("ws", wb_id, run_id, phase="running", status="running")
    # 白盒已 done（写 scan_end 模拟）
    (scan_dir / "events.ndjson").write_text('{"type":"scan_end","status":"completed"}\n')
    handle = AsyncMock()
    client = AsyncMock(); client.get_workflow_handle = AsyncMock(return_value=handle)
    with patch("supernova_web.components.scan_manager.Client.connect",
               AsyncMock(return_value=client)), \
         patch.object(mgr, "_combined_report_orchestrator", new=AsyncMock()) as cro:
        await mgr.resume("ws", wb_id)
    # re-attach 的 workflow_id 应含 -bb-1
    wf_id = client.get_workflow_handle.call_args.args[0]
    assert wf_id.endswith("-bb-1")
    cro.assert_awaited()


async def test_resume_no_active_run_resubmits_whitebox(mgr, tmp_path):
    from supernova_web.components.scan_store import ScanStore
    store = ScanStore(tmp_path); mgr._store = store
    wb_id, scan_dir = store.create_scan("ws", "http://t", "/code/x")
    # combined 但无 run、白盒未完 → resume 白盒
    SessionManager(scan_dir.parent).update_session(scan_dir, {"combined": True})
    handle = AsyncMock()
    client = AsyncMock(); client.start_workflow = AsyncMock(return_value=handle)
    with patch("supernova_web.components.scan_manager.Client.connect",
               AsyncMock(return_value=client)), \
         patch.object(mgr, "_submit_whitebox", new=AsyncMock(return_value=handle)) as sw, \
         patch.object(mgr, "_combined_orchestrator", new=AsyncMock()):
        await mgr.resume("ws", wb_id)
    sw.assert_awaited()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/web/tests/test_combined_resume_cancel.py -k "resume_latest_run_running or resume_no_active_run" -q`
Expected: FAIL（resume 仍读 task-level bb_phase）

- [ ] **Step 3: Write minimal implementation**

(a) `_resolve_workflow_id`（L1165）保持现状（白盒 base）；新增 run 维度 helper（紧随其后）：

```python
def _resolve_run_workflow_id(self, ws: str, scan_id: str, run_id: str) -> str:
    """run 黑盒 workflow_id = base + '-bb-{K}'。优先读 run events 首行 WorkflowHeader。"""
    run_dir = blackbox_run_dir(
    self._workspaces_dir / ws / "scans" / scan_id, run_id)
    wf = _read_workflow_id_from_ndjson(run_dir)  # 现有模块函数
    if wf:
        return _strip_ws_prefix(ws, wf)
    k = int(run_id.split("-")[1])
    return self._resolve_workflow_id(ws, scan_id) + f"-bb-{k}"
```

> `_read_workflow_id_from_ndjson` / `_strip_ws_prefix` 在 `scan_store.py` 模块级；若 scan_manager 未 import 则加 `from supernova_web.components.scan_store import _read_workflow_id_from_ndjson`（`_strip_ws_prefix` 同理，或经 `resolve_workflow_id` 所在模块）。若为私有不便 import，则 run workflow_id 直接用 `self._resolve_workflow_id(ws, scan_id) + f"-bb-{k}"`（读 ndjson 仅用于 resume re-attach，可省）。

(b) `resume`（L271）combined 分支改：读 latest run 的 phase（run session），而非 task-level `bb_phase`。把 combined 分支内对 `bb_phase`/`bb_rerun_attempts` 的读取替换为：

```python
        if data.get("combined"):
            self._strip_trailing_scan_end(scan_dir / "events.ndjson")
            runs = self._store.list_blackbox_runs(ws, scan_id)
            latest = runs[-1] if runs else None
            latest_phase = None
            if latest:
                rd = self._store.get_blackbox_run_dir(ws, scan_id, latest["run_id"])
                if rd:
                    latest_phase = SessionManager(rd.parent).get_session_data(rd).get("bb_phase")
            if latest and latest_phase == "running":
                wf_id = self._resolve_run_workflow_id(ws, scan_id, latest["run_id"])
                client = await Client.connect(self._temporal_address())
                bb_handle = client.get_workflow_handle(wf_id)
                self._orchestrator_tasks[(ws, scan_id)] = asyncio.create_task(
                    self._combined_report_orchestrator(
                        (ws, scan_id), bb_handle, scan_dir, latest["run_id"]))
                return wf_id, str(scan_dir)
            # 无活跃 run / 白盒未完 → resume 白盒 + 重启接力（建 run-1 由 orchestrator 完成）
            ...  # 既有 resumeAttempts + _submit_whitebox + _combined_orchestrator 逻辑保留
```

> 把 `resume` 里 `bb_rerun_attempts` 相关计算删除（runs 版本化后不再用）。`_combined_report_orchestrator` 签名已在 T7 加 `run_id`。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/web/tests/test_combined_resume_cancel.py -q`
Expected: PASS（既有 cancel/resume 用例按新语义修）

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/supernova_web/components/scan_manager.py packages/web/tests/test_combined_resume_cancel.py
git commit -m "feat(scan_manager): run-dimension workflow_id + resume by latest run"
```

---

### Task 10: cancel 活跃 run

**Files:**
- Modify: `packages/web/src/supernova_web/components/scan_manager.py`（`_cancel_combined` L1510 按 latest run）
- Test: `packages/web/tests/test_combined_resume_cancel.py`

**Interfaces:**
- Consumes: T9 `_resolve_run_workflow_id`；T2 `list_blackbox_runs/get_blackbox_run_dir`、`update_blackbox_run`。
- Produces: cancel = 取消 latest 活跃 run 的 `-bb-{K}` workflow（或白盒 workflow 若白盒在跑）。

- [ ] **Step 1: Write the failing test**

```python
async def test_cancel_cancels_latest_run_bb_workflow(mgr, tmp_path):
    from supernova_web.components.scan_store import ScanStore
    store = ScanStore(tmp_path); mgr._store = store
    wb_id, scan_dir = store.create_scan("ws", "http://t", "/code/x")
    run_id, _ = store.create_blackbox_run("ws", wb_id)
    store.update_blackbox_run("ws", wb_id, run_id, phase="running", status="running")
    handle = AsyncMock()
    client = AsyncMock(); client.get_workflow_handle = AsyncMock(return_value=handle)
    with patch("supernova_web.components.scan_manager.Client.connect",
               AsyncMock(return_value=client)):
        res = await mgr.cancel("ws", wb_id)
    wf_id = client.get_workflow_handle.call_args.args[0]
    assert wf_id.endswith("-bb-1")
    handle.cancel.assert_awaited()
    runs = store.list_blackbox_runs("ws", wb_id)
    assert runs[-1]["status"] == "cancelled"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/web/tests/test_combined_resume_cancel.py::test_cancel_cancels_latest_run_bb_workflow -q`
Expected: FAIL（`_cancel_combined` 用旧 suffix 推导）

- [ ] **Step 3: Write minimal implementation**

`_cancel_combined`（L1510）改为按 latest run：取 `list_blackbox_runs` 末条，若其 phase==running → workflow_id = `_resolve_run_workflow_id(ws, scan_id, run_id)`；cancel orchestrator task + `handle.cancel()` + `update_blackbox_run(..., status="cancelled", phase="cancelled")`。若 latest 非活跃（白盒在跑）→ cancel 白盒 `{ws}-{scan_id}`。删除对 `bb_rerun_attempts` 的依赖。

骨架（替换 `_cancel_combined` body）：

```python
async def _cancel_combined(self, ws, scan_id, scan_dir, scan_key):
    runs = self._store.list_blackbox_runs(ws, scan_id)
    latest = runs[-1] if runs else None
    active_run_phase = None
    if latest:
        rd = self._store.get_blackbox_run_dir(ws, scan_id, latest["run_id"])
        if rd:
            active_run_phase = SessionManager(rd.parent).get_session_data(rd).get("bb_phase")
    client = await Client.connect(self._temporal_address())
    if latest and active_run_phase == "running":
        wf_id = self._resolve_run_workflow_id(ws, scan_id, latest["run_id"])
        try:
            await (await client.get_workflow_handle(wf_id)).cancel()
        except Exception:  # noqa: BLE001
            pass
        self._store.update_blackbox_run(
            ws, scan_id, latest["run_id"], status="cancelled", phase="cancelled")
    else:
        wb_wf = self._resolve_workflow_id(ws, scan_id)
        try:
            await (await client.get_workflow_handle(wb_wf)).cancel()
        except Exception:  # noqa: BLE001
            pass
    self._handles.pop(scan_key, None)
    await self._mark_cancelled(scan_dir)
    return {"cancelled": scan_id}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/web/tests/test_combined_resume_cancel.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/supernova_web/components/scan_manager.py packages/web/tests/test_combined_resume_cancel.py
git commit -m "feat(scan_manager): cancel active run workflow"
```

---

### Task 11: _reconcile_combined_scan 逐 run 兜底

**Files:**
- Modify: `packages/web/src/supernova_web/components/scan_manager.py`（`_reconcile_combined_scan` L1868 按 `bb_runs[]` 逐 run）
- Test: `packages/web/tests/test_combined_reconcile.py`

**Interfaces:**
- Consumes: T9 `_resolve_run_workflow_id`；T5 `_generate_combined_report(scan_dir, run_id)`/`_mark_run`；T2 `list_blackbox_runs`；现有 `_query_workflow_status`、`_ensure_scan_end`。
- Produces: 进程重启后对每个非终态 run 探测其 workflow：completed → 补融合报告 + 标 completed；running → 不干预；其余 → 补 scan_end。

- [ ] **Step 1: Write the failing test**

```python
async def test_reconcile_completed_run_generates_report(mgr, tmp_path):
    from supernova_web.components.scan_store import ScanStore
    store = ScanStore(tmp_path); mgr._store = store
    wb_id, scan_dir = store.create_scan("ws", "http://t", "/code/x")
    run_id, run_dir = store.create_blackbox_run("ws", wb_id)
    store.update_blackbox_run("ws", wb_id, run_id, phase="running", status="running")
    # workflow 已 completed
    with patch.object(mgr, "_query_workflow_status", new=AsyncMock(return_value="completed")), \
         patch.object(mgr, "_generate_combined_report", new=AsyncMock()) as gcr, \
         patch.object(mgr, "_ensure_scan_end", new=AsyncMock()):
        await mgr._reconcile_combined_scan(scan_dir)
    gcr.assert_awaited_with(scan_dir, run_id)
    runs = store.list_blackbox_runs("ws", wb_id)
    assert runs[-1]["status"] == "completed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/web/tests/test_combined_reconcile.py::test_reconcile_completed_run_generates_report -q`
Expected: FAIL（reconcile 读 task-level bb_phase，不逐 run）

- [ ] **Step 3: Write minimal implementation**

把 `_reconcile_combined_scan`（L1868）的分支体替换为逐 run 循环（保留 ws/scan_id 派生守卫 + combined 守卫 + finally `_ensure_scan_end`）：

```python
        bb_runs = data.get("bb_runs") or []
        wf_active = False
        try:
            # 白盒未完：补白盒接力（pending → run-1 由 orchestrator 建）
            wb_status = await self._query_workflow_status(self._resolve_workflow_id(ws, scan_id))
            if wb_status == "running":
                wf_active = True
            for r in bb_runs:
                run_id = r.get("run_id")
                if not run_id or r.get("status") in ("completed", "failed", "skipped", "cancelled"):
                    continue
                bb_status = await self._query_workflow_status(
                    self._resolve_run_workflow_id(ws, scan_id, run_id))
                if bb_status == "running":
                    wf_active = True
                elif bb_status == "completed":
                    await self._generate_combined_report(scan_dir, run_id)
                    await self._mark_run(scan_dir, run_id, "completed", status="completed")
                # 其余（None/不存在）→ fall-through 补 scan_end
        except Exception:  # noqa: BLE001
            _log.exception("_reconcile_combined_scan failed for %s", scan_dir)
        finally:
            if not wf_active:
                try:
                    await self._ensure_scan_end(scan_dir)
                except Exception:  # noqa: BLE001
                    _log.exception("_ensure_scan_end in reconcile for %s", scan_dir)
```

> `_kick_combined_reconcile`（L1957）不改（仍 fire `_reconcile_combined_scan`）。删除 reconcile 内对 `bb_phase`/`bb_rerun_attempts`/单 `-bb-rerun-N` suffix 的所有引用。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/web/tests/test_combined_reconcile.py -q`
Expected: PASS（既有 reconcile 用例按逐-run 语义修）

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/supernova_web/components/scan_manager.py packages/web/tests/test_combined_reconcile.py
git commit -m "feat(scan_manager): reconcile per-run (bb_runs[]) instead of single bb_phase"
```

---

## Phase D — API 透传 + run 级路由

### Task 12: scans.py _scan_detail 透传 bb_runs + 列 run + run 详情

**Files:**
- Modify: `packages/web/src/supernova_web/api/scans.py`（`_scan_detail` 透传 `bb_runs/latest_bb_run`；加 list/detail run 路由）
- Test: `packages/web/tests/test_scans_api.py`

**Interfaces:**
- Consumes: T3 `list_blackbox_runs/get_blackbox_run_dir`。
- Produces: `GET /{ws}/scans/{scan_id}` payload 含 `bb_runs`/`latest_bb_run`；`GET /{ws}/scans/{scan_id}/blackbox-runs`（列表）；`GET /{ws}/scans/{scan_id}/blackbox-runs/{run_id}`（run 详情，读 run session）。

- [ ] **Step 1: Write the failing test**

```python
def test_scan_detail_includes_bb_runs(authed_client, tmp_workspaces):
    scan_dir = _make_scan(tmp_workspaces, "WS", scan_id="s1")
    store = ScanStore(tmp_workspaces)
    store.create_blackbox_run("WS", "s1")
    detail = authed_client.get("/api/workspaces/WS/scans/s1").json()
    assert detail["combined"] is True
    assert detail["latest_bb_run"] == "run-1"
    assert detail["bb_runs"][0]["run_id"] == "run-1"


def test_list_blackbox_runs_route(authed_client, tmp_workspaces):
    scan_dir = _make_scan(tmp_workspaces, "WS", scan_id="s1")
    store = ScanStore(tmp_workspaces)
    store.create_blackbox_run("WS", "s1")
    store.create_blackbox_run("WS", "s1")
    runs = authed_client.get("/api/workspaces/WS/scans/s1/blackbox-runs").json()
    assert [r["run_id"] for r in runs] == ["run-1", "run-2"]


def test_blackbox_run_detail_route(authed_client, tmp_workspaces):
    scan_dir = _make_scan(tmp_workspaces, "WS", scan_id="s1")
    store = ScanStore(tmp_workspaces)
    store.create_blackbox_run("WS", "s1")
    store.update_blackbox_run("WS", "s1", "run-1", phase="running", status="running")
    rd = authed_client.get("/api/workspaces/WS/scans/s1/blackbox-runs/run-1").json()
    assert rd["run_id"] == "run-1"
    assert rd["bb_phase"] == "running"
```

（`_make` 见既有 `test_scans_api.py` helper；`ScanStore(tmp_workspaces)` 直接构造。）

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/web/tests/test_scans_api.py -k "bb_runs or list_blackbox_runs_route or blackbox_run_detail_route" -q`
Expected: FAIL（字段/路由不存在）

- [ ] **Step 3: Write minimal implementation**

(a) `_scan_detail`（L66）payload dict 末尾追加：

```python
        "bb_runs": data.get("bb_runs"),
        "latest_bb_run": data.get("latest_bb_run"),
```

(b) 加路由（在 `get_scan` 路由之后）：

```python
@router.get("/{ws}/scans/{scan_id}/blackbox-runs")
async def list_blackbox_runs(ws: str, scan_id: str, request: Request,
                             _: User = Depends(workspace_member)) -> list:
    return _store(request).list_blackbox_runs(ws, scan_id)


@router.get("/{ws}/scans/{scan_id}/blackbox-runs/{run_id}")
async def blackbox_run_detail(ws: str, scan_id: str, run_id: str, request: Request,
                              _: User = Depends(workspace_member)) -> dict:
    store = _store(request)
    run_dir = store.get_blackbox_run_dir(ws, scan_id, run_id)
    if run_dir is None:
        raise HTTPException(status_code=404, detail="run 不存在")
    from supernova_core.session import SessionManager
    data = SessionManager(run_dir.parent).get_session_data(run_dir)
    return {"run_id": run_id, **data}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/web/tests/test_scans_api.py -k "bb_runs or list_blackbox_runs_route or blackbox_run_detail_route" -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/supernova_web/api/scans.py packages/web/tests/test_scans_api.py
git commit -m "feat(api): scan detail bb_runs passthrough + list/detail blackbox-run routes"
```

---

### Task 13: scans.py run 级 deliverables/report/logs/events + POST add-run

**Files:**
- Modify: `packages/web/src/supernova_web/api/scans.py`（加 run 级子资源路由 + POST add-run；rerun-blackbox 返 run_id）
- Test: `packages/web/tests/test_scans_api.py`

**Interfaces:**
- Consumes: T3 `get_blackbox_run_dir`；T6 scan_manager `_add_blackbox_run` / T8 `rerun_blackbox`；现有 `report_for/deliverables_*` helper、`DeliverablesReader`。
- Produces: `GET .../blackbox-runs/{run_id}/{deliverables,report,logs,events}`（scan_dir 上下文指 run 子目录）；`POST .../blackbox-runs`（手动加 run，body 可选 ScanRequest）；`rerun-blackbox` 返 `{..., run_id}`。

- [ ] **Step 1: Write the failing test**

```python
def test_blackbox_run_report_route(authed_client, tmp_workspaces):
    scan_dir = _make(tmp_workspaces, "WS", scan_id="s1")
    store = ScanStore(tmp_workspaces); store.create_blackbox_run("WS", "s1")
    run_dir = scan_dir / "blackbox-runs" / "run-1"
    (run_dir / "deliverables" / "blackbox").mkdir(parents=True)
    (run_dir / "deliverables" / "blackbox" / "comprehensive_security_assessment_report.md").write_text("# 黑盒报告")
    txt = authed_client.get(
        "/api/workspaces/WS/scans/s1/blackbox-runs/run-1/report").text
    assert txt == "# 黑盒报告"


def test_post_add_blackbox_run(authed_client, tmp_workspaces, monkeypatch):
    scan_dir = _make(tmp_workspaces, "WS", scan_id="s1")
    (scan_dir / "deliverables" / "whitebox").mkdir(parents=True)
    (scan_dir / "deliverables" / "whitebox" / "recon_deliverable.md").write_text("x")
    (scan_dir / "deliverables" / "whitebox" / "injection_exploitation_queue.json").write_text(
        '{"vulnerabilities":[{"id":1}]}')
    # mock scan_manager._add_blackbox_run
    ...  # 注入 app.state scan_manager 的 _add_blackbox_run = AsyncMock(return_value="run-1")
    res = authed_client.post("/api/workspaces/WS/scans/s1/blackbox-runs", json={})
    assert res.status_code == 202
    assert res.json()["run_id"] == "run-1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/web/tests/test_scans_api.py -k "blackbox_run_report_route or post_add_blackbox_run" -q`
Expected: FAIL（路由不存在）

- [ ] **Step 3: Write minimal implementation**

加 run 级子资源路由（复用现有 helper，传 run 子目录作 scan_dir 上下文）：

```python
@router.get("/{ws}/scans/{scan_id}/blackbox-runs/{run_id}/deliverables")
async def run_deliverables_summary(ws, scan_id, run_id, request, _=Depends(workspace_member),
                                   path: str | None = Query(None)):
    run_dir = _store(request).get_blackbox_run_dir(ws, scan_id, run_id) or _raise404()
    return deliverables_summary_for(run_dir, path)

@router.get("/{ws}/scans/{scan_id}/blackbox-runs/{run_id}/deliverables/{filename}")
async def run_deliverables_file(ws, scan_id, run_id, filename, request,
                                _=Depends(workspace_member)):
    run_dir = _store(request).get_blackbox_run_dir(ws, scan_id, run_id) or _raise404()
    return deliverables_file_for(run_dir, filename, track="blackbox")

@router.get("/{ws}/scans/{scan_id}/blackbox-runs/{run_id}/report",
            response_class=PlainTextResponse)
async def run_report(ws, scan_id, run_id, request, _=Depends(workspace_member)) -> str:
    run_dir = _store(request).get_blackbox_run_dir(ws, scan_id, run_id) or _raise404()
    return report_for(run_dir, track="blackbox")

@router.get("/{ws}/scans/{scan_id}/blackbox-runs/{run_id}/logs")
async def run_logs(ws, scan_id, run_id, request, _=Depends(workspace_member),
                   file: str | None = Query(None)):
    run_dir = _store(request).get_blackbox_run_dir(ws, scan_id, run_id) or _raise404()
    return logs_for(run_dir, file)

@router.get("/{ws}/scans/{scan_id}/blackbox-runs/{run_id}/events")
async def run_events(ws, scan_id, run_id, request, _=Depends(workspace_member)):
    run_dir = _store(request).get_blackbox_run_dir(ws, scan_id, run_id) or _raise404()
    return _stream_events(run_dir / "events.ndjson")  # 复用现有 events 流式实现

@router.post("/{ws}/scans/{scan_id}/blackbox-runs", status_code=202)
async def add_blackbox_run(ws, scan_id, request, _=Depends(workspace_member)) -> dict:
    body = await request.json()
    req = ScanRequest(**body) if body else None
    mgr = request.app.state.scan_manager
    run_id = await mgr._add_blackbox_run(ws, scan_id, req)
    return {"workspace": ws, "scan_id": scan_id, "run_id": run_id}
```

`_raise404`：

```python
def _raise404():
    raise HTTPException(status_code=404, detail="run 不存在")
```

把既有 `rerun-blackbox` 端点返回改为含 `run_id`：

```python
@router.post("/{ws}/scans/{scan_id}/combined/rerun-blackbox", status_code=202)
async def rerun_blackbox(ws, scan_id, request, _=Depends(workspace_member)) -> dict:
    mgr = request.app.state.scan_manager
    run_id = await mgr.rerun_blackbox(ws, scan_id)
    return {"workspace": ws, "scan_id": scan_id, "run_id": run_id}
```

> `_stream_events` / `request.app.state.scan_manager` 名字以既有代码为准（grep 确认 events 端点 + scan_manager 注入名）。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/web/tests/test_scans_api.py -q`
Expected: PASS（本 task + T12 + 既有；预存失败非本 task 引入则忽略）

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/supernova_web/api/scans.py packages/web/tests/test_scans_api.py
git commit -m "feat(api): run-scoped deliverables/report/logs/events + POST add-blackbox-run"
```

## Phase E — 前端（types/client + 列表嵌套 + 详情 per-run + 加黑盒入口）

### Task 14: types.ts BlackboxRunSummary + client.ts API

**Files:**
- Modify: `packages/web/frontend/src/api/types.ts`、`packages/web/frontend/src/api/client.ts`
- Test: `packages/web/frontend/src/api/__tests__/client.test.ts`（若无则建）

**Interfaces:**
- Consumes: T12/T13 后端路由。
- Produces: `BlackboxRunSummary` 类型；`ScanSummary`/`SessionData` 加 `bb_runs?`/`latest_bb_run?`；`listBlackboxRuns(ws, scanId)`、`addBlackboxToWhitebox(ws, scanId, body?)`、`getBlackboxRun(ws, scanId, runId)`；run-scoped 路径 helper `blackboxRunReportPath/blackboxRunDeliverablesPath`。

- [ ] **Step 1: Write the failing test**

```ts
import { describe, it, expect } from "vitest";
import { listBlackboxRuns, blackboxRunReportPath } from "../client";

describe("blackbox run api", () => {
  it("listBlackboxRuns builds correct path", () => {
    expect(listBlackboxRuns("WS", "s1")).toBeInstanceOf(Promise);
  });
  it("blackboxRunReportPath encodes segments", () => {
    expect(blackboxRunReportPath("WS", "s1", "run-1"))
      .toBe(`/workspaces/WS/scans/s1/blackbox-runs/run-1/report`);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/web/frontend && ./node_modules/.bin/vitest run src/api/__tests__/client.test.ts`
Expected: FAIL（导出不存在）

- [ ] **Step 3: Write minimal implementation**

`types.ts` 加：

```ts
export interface BlackboxRunSummary {
  run_id: string;
  status?: string;
  started_at?: string;
  completed_at?: string;
  auth_ref?: { profile_id?: string | null };
  reason?: string | null;
  bb_phase?: string;
}
```

在 `ScanSummary` 与 `SessionData` 接口各加：

```ts
  bb_runs?: BlackboxRunSummary[];
  latest_bb_run?: string | null;
```

`client.ts` 加（紧随 `rerunBlackbox`）：

```ts
export const listBlackboxRuns = (ws: string, scanId: string) =>
  apiGet<BlackboxRunSummary[]>(`/workspaces/${encWs(ws)}/scans/${encWs(scanId)}/blackbox-runs`);

export const getBlackboxRun = (ws: string, scanId: string, runId: string) =>
  apiGet<BlackboxRunSummary & Record<string, unknown>>(
    `/workspaces/${encWs(ws)}/scans/${encWs(scanId)}/blackbox-runs/${encWs(runId)}`);

export const addBlackboxToWhitebox = (ws: string, scanId: string, body?: ScanRequest) =>
  apiPost<{ workspace: string; scan_id: string; run_id: string }>(
    `/workspaces/${encWs(ws)}/scans/${encWs(scanId)}/blackbox-runs`, body ?? {});

export const blackboxRunReportPath = (ws: string, scanId: string, runId: string) =>
  `/workspaces/${encWs(ws)}/scans/${encWs(scanId)}/blackbox-runs/${encWs(runId)}/report`;

export const blackboxRunDeliverablesPath = (ws: string, scanId: string, runId: string, file?: string) =>
  file
    ? `/workspaces/${encWs(ws)}/scans/${encWs(scanId)}/blackbox-runs/${encWs(runId)}/deliverables?path=${encodeURIComponent(file)}`
    : `/workspaces/${encWs(ws)}/scans/${encWs(scanId)}/blackbox-runs/${encWs(runId)}/deliverables`;

export const blackboxRunEventsUrl = (ws: string, scanId: string, runId: string) =>
  `/api/workspaces/${encWs(ws)}/scans/${encWs(scanId)}/blackbox-runs/${encWs(runId)}/events`;
```

> `BlackboxRunSummary`/`ScanRequest` 须在 client.ts 已 import 或从 types re-export。`encWs` 单段编码 helper 已存在。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/web/frontend && ./node_modules/.bin/vitest run src/api/__tests__/client.test.ts && ./node_modules/.bin/tsc --noEmit`
Expected: PASS + tsc 无错

- [ ] **Step 5: Commit**

```bash
git add packages/web/frontend/src/api/types.ts packages/web/frontend/src/api/client.ts packages/web/frontend/src/api/__tests__/client.test.ts
git commit -m "feat(web): BlackboxRunSummary type + run-scoped api client"
```

---

### Task 15: ScanList 内嵌 run 列表

**Files:**
- Modify: `packages/web/frontend/src/routes/WorkspaceDetail/ScanList.tsx`（`ScanCard` 内 `CombinedStepTimeline` 之后加 `<NestedBlackboxRuns>`）
- Test: `packages/web/frontend/src/routes/WorkspaceDetail/__tests__/ScanListRuns.test.tsx`

**Interfaces:**
- Consumes: T14 `listBlackboxRuns`、`scanReportPath`、`BlackboxRunSummary`；`ScanSummary.bb_runs`。
- Produces: 白盒任务卡内嵌 run 列表（每 run 一行：run_id + status + 跳到该 run 报告/详情链接）。

- [ ] **Step 1: Write the failing test**

```tsx
// 沿用 ScanListCombined.test.tsx 的 MSW + MemoryRouter + i18n(zh) 模式
it("renders nested blackbox runs inside a whitebox task card", async () => {
  server.use(
    http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([
      { scan_id: "s1", scan_type: "whitebox", status: "completed", combined: true,
        latest_bb_run: "run-2", bb_runs: [
          { run_id: "run-1", status: "completed" },
          { run_id: "run-2", status: "running" }] }])))
  renderList();
  expect(await screen.findByText("run-1")).toBeInTheDocument();
  expect(screen.getByText("run-2")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/web/frontend && ./node_modules/.bin/vitest run src/routes/WorkspaceDetail/__tests__/ScanListRuns.test.tsx`
Expected: FAIL（run 列表未渲染）

- [ ] **Step 3: Write minimal implementation**

在 `ScanCard` 内（`CombinedStepTimeline` 块之后）加：

```tsx
{scan.combined && scan.bb_runs && scan.bb_runs.length > 0 && (
  <NestedBlackboxRuns ws={ws} scanId={scan.scan_id} runs={scan.bb_runs} />
)}
```

新组件（同文件或抽 `NestedBlackboxRuns.tsx`）：

```tsx
function NestedBlackboxRuns({ ws, scanId, runs }: {
  ws: string; scanId: string; runs: BlackboxRunSummary[] }) {
  const { t } = useTranslation();
  return (
    <ul className="ml-4 mt-1 space-y-1 border-l pl-3" data-testid="nested-runs">
      {runs.map((r) => (
        <li key={r.run_id} className="flex items-center gap-2 text-sm">
          <span className="font-mono">{r.run_id}</span>
          <StatusBadge status={r.status ?? r.bb_phase ?? "unknown"} />
          <Link to={`/p/${ws}/scans/${scanId}?run=${r.run_id}`}
                className="text-primary hover:underline">{t("workspaceDetail.scans.runs.view")}</Link>
        </li>
      ))}
    </ul>
  );
}
```

> `StatusBadge`/`useTranslation`/`Link` 复用现有 import。i18n key 在 T17 加。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/web/frontend && ./node_modules/.bin/vitest run src/routes/WorkspaceDetail/__tests__/ScanListRuns.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/web/frontend/src/routes/WorkspaceDetail/ScanList.tsx packages/web/frontend/src/routes/WorkspaceDetail/__tests__/ScanListRuns.test.tsx
git commit -m "feat(web): ScanList card shows nested blackbox runs"
```

---

### Task 16: ScanDetail run 选择器 + per-run 报告/产物

**Files:**
- Modify: `packages/web/frontend/src/routes/WorkspaceDetail/ScanDetail.tsx`、`ReportTab.tsx`、`DeliverablesTab.tsx`
- Test: `packages/web/frontend/src/routes/WorkspaceDetail/__tests__/ScanDetailRuns.test.tsx`、`ReportTabRuns.test.tsx`

**Interfaces:**
- Consumes: T14 client APIs/path helper；`SessionData.bb_runs`。
- Produces: 详情页顶部 run 选择器（select latest 默认，可切历史 run）；ReportTab/DeliverablesTab 黑盒/融合视图按选中 run 取（run-scoped 路径）。

- [ ] **Step 1: Write the failing test**

```tsx
it("run selector switches blackbox report source", async () => {
  // detail 返 combined + bb_runs[run-1,run-2]
  server.use(
    http.get("/api/workspaces/:ws/scans/:id", () => HttpResponse.json({
      scan_type: "whitebox", combined: true, status: "completed",
      latest_bb_run: "run-2",
      bb_runs: [{ run_id: "run-1", status: "completed" }, { run_id: "run-2", status: "completed" }] })),
    http.get("/api/workspaces/:ws/scans/:id/blackbox-runs/run-1/report",
      () => new HttpResponse("# run-1 黑盒报告")),
    http.get("/api/workspaces/:ws/scans/:id/blackbox-runs/run-2/report",
      () => new HttpResponse("# run-2 黑盒报告")));
  renderDetail();  // 沿用 ReportTabCombined.test.tsx 的 renderDetail
  // 默认 run-2
  expect(await screen.findByText("# run-2 黑盒报告")).toBeTruthy(); // 经 MarkdownView
  // 切到 run-1
  fireEvent.change(screen.getByRole("combobox", { name: /run/i }), { target: { value: "run-1" } });
  await waitFor(() => expect(screen.getByText("# run-1 黑盒报告")).toBeTruthy());
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/web/frontend && ./node_modules/.bin/vitest run src/routes/WorkspaceDetail/__tests__/ScanDetailRuns.test.tsx src/routes/WorkspaceDetail/__tests__/ReportTabRuns.test.tsx`
Expected: FAIL（无 run 选择器）

- [ ] **Step 3: Write minimal implementation**

(a) `ScanDetail.tsx`：`isCombined` 时在 header 与 tabs 之间加 run 选择器，状态 `selectedRun`（默认 `latest_bb_run`），经 query string `?run=` 同步（`useSearchParams`）：

```tsx
const [searchParams, setSearchParams] = useSearchParams();
const selectedRun = searchParams.get("run") ?? meta?.latest_bb_run ?? null;
const runs = meta?.bb_runs ?? [];
{isCombined && runs.length > 0 && (
  <select aria-label={t("workspaceDetail.scans.runs.select")}
          value={selectedRun ?? ""}
          onChange={(e) => setSearchParams({ run: e.target.value })}>
    {runs.map((r) => <option key={r.run_id} value={r.run_id}>{r.run_id} · {r.status}</option>)}
  </select>
)}
```

并通过 `<Outlet context={{ ... }}> 或 context 把 `selectedRun` 下传给 ReportTab/DeliverablesTab（用 react-router `useOutletContext`）。

(b) `ReportTab.tsx`：黑盒/融合子 tab 在有 `selectedRun` 时改用 `blackboxRunReportPath(ws, scanId, selectedRun)`（黑盒报告）+ 融合报告读 `/scans/{id}/blackbox-runs/{run}/report?track=combined`（后端 T13 run report 默认 blackbox track；融合报告复用 `combined/run-K/combined_report.md`——加一个 run-scoped combined 端点或让 run report 支持 `?track=combined` 读 `combined/run-K/`）。简化：在 `report_for` run 版加 `track="combined"` 分支读 `combined_run_dir`。

> 后端补丁（小）：`scans.py` 的 `run_report` 支持可选 `track` query；track=="combined" 时读 `combined_run_dir(scan_dir, run_id)/"combined_report.md"`。在 T13 的 `run_report` 里加：

```python
async def run_report(ws, scan_id, run_id, request, _=Depends(workspace_member),
                     track: str | None = Query(None)) -> str:
    store = _store(request)
    wb_dir = store.get_scan_dir(ws, scan_id) or _raise404()
    run_dir = store.get_blackbox_run_dir(ws, scan_id, run_id) or _raise404()
    if track == "combined":
        from supernova_core.utils.paths import combined_run_dir
        p = combined_run_dir(wb_dir, run_id) / "combined_report.md"
        if not p.is_file():
            raise HTTPException(404, "融合报告未生成")
        return p.read_text("utf-8")
    return report_for(run_dir, track="blackbox")
```

（此补丁归入 T13 范畴——若 T13 已 commit，单独加一个小 commit「api: run report combined track」。）

(c) `DeliverablesTab.tsx`：黑盒/融合 bucket 在有 `selectedRun` 时改用 `blackboxRunDeliverablesPath(ws, scanId, selectedRun, file)`。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/web/frontend && ./node_modules/.bin/vitest run src/routes/WorkspaceDetail/__tests__/`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/web/frontend/src/routes/WorkspaceDetail/ packages/web/src/supernova_web/api/scans.py
git commit -m "feat(web): ScanDetail run selector + per-run report/deliverables"
```

---

### Task 17: 加黑盒入口 + i18n

**Files:**
- Modify: `packages/web/frontend/src/routes/WorkspaceDetail/ScanDetail.tsx`（终端态白盒任务加「给该任务加黑盒」按钮 + Dialog）；`ScanList.tsx`（卡片菜单同款入口）；`pages/ScanNewPage.tsx`（`buildBody` 对 blackbox type 已支持 reuse，确认 preset 带得对）；`locales/zh.json`/`locales/en.json`
- Test: `packages/web/frontend/src/routes/WorkspaceDetail/__tests__/AddBlackboxEntry.test.tsx`

**Interfaces:**
- Consumes: T14 `addBlackboxToWhitebox`；现有 `AuthFields`/`HostFields`/`rerunBlackbox` Dialog 模式。
- Produces: 在已完成白盒任务（`!combined` 或 combined 但可再加 run）上提供「加黑盒」入口 → Dialog（可选认证/HOST）→ POST `blackbox-runs` → 刷新。

- [ ] **Step 1: Write the failing test**

```tsx
it("add-blackbox entry POSTs and toasts success", async () => {
  server.use(
    http.get("/api/workspaces/:ws/scans/:id", () => HttpResponse.json(
      { scan_type: "whitebox", status: "completed", combined: false })),
    http.post("/api/workspaces/:ws/scans/:id/blackbox-runs",
      () => HttpResponse.json({ workspace: "WS", scan_id: "s1", run_id: "run-1" }, { status: 202 })));
  renderDetail();
  await screen.findByRole("button", { name: /加黑盒/ });
  fireEvent.click(screen.getByRole("button", { name: /加黑盒/ }));
  fireEvent.click(screen.getByRole("button", { name: /确定|提交|确认/ }));
  await waitFor(() => expect(toast.success).toHaveBeenCalled());
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/web/frontend && ./node_modules/.bin/vitest run src/routes/WorkspaceDetail/__tests__/AddBlackboxEntry.test.tsx`
Expected: FAIL（无入口）

- [ ] **Step 3: Write minimal implementation**

(a) `ScanDetail.tsx`：终端态白盒任务 header 加按钮（镜像现有「续扫黑盒」Dialog 结构，但调 `addBlackboxToWhitebox(ws, scanId, body)`）：

```tsx
{(meta?.status === "completed" && meta?.scan_type === "whitebox") && (
  <AddBlackboxDialog ws={ws} scanId={scanId} onDone={reload} />
)}
```

`AddBlackboxDialog`（复用 `AuthFields`/`HostFields` 受控 + `buildBody("blackbox", ...)` 构造 body；empty body = 无认证直连）。沿用 `rerunBlackbox` Dialog 的 open/submit/toast 模式。

(b) i18n：`zh.json` / `en.json` 在 `workspaceDetail.scans` 下加：

```json
"runs": {
  "view": "查看",
  "select": "选择黑盒 run",
  "addBlackbox": "加黑盒扫描",
  "addBlackboxDesc": "为该白盒结果新建一次黑盒 run",
  "addedSuccess": "黑盒 run 已创建",
  "addedFailed": "创建黑盒 run 失败"
}
```

en.json 对应英文。`scan.combined.*` 既有 key 不动。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/web/frontend && ./node_modules/.bin/vitest run src/routes/WorkspaceDetail/__tests__/ && ./node_modules/.bin/tsc --noEmit`
Expected: PASS + tsc 无错

- [ ] **Step 5: Commit**

```bash
git add packages/web/frontend/src/ packages/web/frontend/src/locales/
git commit -m "feat(web): add-blackbox-to-whitebox entry + run i18n"
```

---

## Phase F — 端到端冒烟（人工）

### Task 18: 端到端冒烟（真机/容器）

**Files:** 无代码改动；记录冒烟结果到 plan 末尾或 PR 描述。

**Interfaces:** 消费 T1-T17 全部产物。

- [ ] **Step 1: rebuild 镜像**

Run: `uv sync --all-packages`（后端 src 改了，worker 镜像须重建）；前端 `cd packages/web/frontend && npm run build`。

- [ ] **Step 2: 纯白盒零回归**

发起纯白盒（组合开关关）扫描 → 终态 `deliverables/whitebox/` 仅此一桶，列表无 run 内嵌，详情无 run 选择器。

- [ ] **Step 3: 纯白盒后手动加黑盒（需求 1+2）**

白盒完成后点「加黑盒」→ run-1 产 `blackbox-runs/run-1/deliverables/blackbox/` + `combined/run-1/combined_report.md`；详情三向 toggle（白盒 / run-1 黑盒 / run-1 融合）；再加一次 → run-2，列表卡内嵌两 run，可切换对比。

- [ ] **Step 4: 组合扫描 run-1（需求 3 by-construction）**

组合开关打开 → 白盒完自动接力 run-1；其终态目录/融合报告与「Step 3 手动 run-1」逐字节一致（同 `_run_blackbox_phase`、同输入语义）。

- [ ] **Step 5: 失败续跑（spec §8 隔离）**

让 run-1 黑盒失败（错误认证）→ run-1.status=failed，白盒与其他 run 不受影响；换认证「加黑盒」→ run-2 成功；对比 run-1(failed) vs run-2(completed)。

- [ ] **Step 6: resume/cancel 真机**

扫描中途停 worker（模拟 crash）→ `orphan_reconciler` 按 `bb_runs[]` 逐 run 补（completed 的补报告；running 的不干预）；cancel 取消 latest 活跃 run 的 `-bb-{K}` workflow。

- [ ] **Step 7: 记录冒烟结论**

把通过的步骤勾选；任何偏差回填到对应 task（补测试或修实现）。

---

## Self-Review（plan 作者自检，已执行）

**1. Spec 覆盖**（spec §7.1 十三处必须改 vs task）：
- #1 `get_scan_dir` 拒 `/` + 新 `get_blackbox_run_dir` → **T2** ✓（`get_scan_dir` 本身不改，新增 run 定位方法）
- #2 `_scan_entries`/`list_scans` 只枚举白盒 + run 从 `bb_runs[]` → **T3**（`_scan_entries` 跳过 `~N`；`list_blackbox_runs` 从 session）✓
- #3 `_gen_scan_id`/`_next_blackbox_seq` + 新 `create_blackbox_run` per-task → **T2**（`create_blackbox_run` + `_next_blackbox_run_seq`）✓
- #4 `delete_scan` 级联 + 删单 run → **T3**（`delete_scan` rmtree 整 task 已含 run 子目录；`delete_blackbox_run` 精确删 run）✓
- #5 `render_combined_report` 双路径签名 → **T4** ✓
- #6 `_generate_combined_report` 适配 → **T5** ✓
- #7 `_run_blackbox_phase` event_file/repo_path/config_path run 化 → **T5** ✓
- #8 `start` 黑盒分支 `create_blackbox_run` → **T6** ✓
- #9 `_scan_detail`/`get_scan` run 级 → **T12** ✓
- #10 deliverables/report/logs/events 端点按 run → **T13** ✓
- #11 `rerun_blackbox`/`_rerun_orchestrator` → **T8**（rerun=新 run）✓
- #12 `_resolve_workflow_id` run 维度 → **T9**（`_resolve_run_workflow_id`）✓
- #13 `_reconcile_combined_scan` 逐 run → **T11** ✓
- 零改动 12 处（§7.2）：plan 明确禁止改白盒/黑盒 workflow、`deliverables_reader._infer_track`、`_submit_*`、`_run_precheck`、`_compute_progress_pct` ✓
- 数据模型（§5）：任务级 `bb_runs[]`/`latest_bb_run`/`combined_status` → T2/T3；run 级 session bb_phase/bb_reason → T2/T5 ✓
- 报告三视图（§9）→ T16 ✓；前端列表/详情/入口（§9/§8）→ T15/T16/T17 ✓

**2. Placeholder 扫描**：无 TBD/TODO；每个代码 step 给了真实代码或真实测试。个别 `...` 仅标「沿用既有逻辑保留」（已指明既有代码位置），非占位。

**3. 类型一致性**：`create_blackbox_run(ws, wb_scan_id, *, auth_ref, reason) -> (run_id, run_dir)` 在 T2 定义、T5/T6/T7 调用一致；`_run_blackbox_phase(scan_dir, ws, scan_id, auth_ref, run_id, workflow_id_suffix="-bb-1")` T5 定义、T6/T7/T9 调用一致；`render_combined_report(*, whitebox_root, blackbox_root, out_dir)` T4 定义、T5 调用一致；run workflow_id `{ws}-{scan_id}-bb-{K}` 全 plan 一致；`BlackboxRunSummary` T14 定义、T15/T16 一致。

**已知简化/偏离（已记录，非缺陷）**：
- `bb_rerun_attempts`：runs 版本化后冗余，T8/T9/T10/T11 移除其引用（workflow_id 用 run-K）。spec §5.3 列其为 run 级字段，但语义被 run_id 覆盖——属 by-design 简化。
- 预存失败测试（`_submit_whitebox` 不转发 combined、`_run_blackbox_phase` config_path 缺 auth）是 2026-08-12 scope，本 plan 不修（Global Constraints 已声明）。
- `_mark_bb` 保留未删（降风险）；run 路径统一走 `_mark_run`。

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-08-14-unified-wb-bb-task-model.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — 每个 task 派一个 fresh subagent，task 间 review，快速迭代。适合本 plan：18 个 task 多数相互独立（Phase A→B→C→D→E 有清晰依赖链），TDD 每步可独立验证。

**2. Inline Execution** — 在本 session 按 executing-plans 批量执行 + checkpoint review。

**关键执行注意（所有 executor 必读）：**
- 依赖顺序严格遵守任务依赖图（T1→T2→T3→T5→T6/T7→T8→T9/T10/T11→T12→T13→T14→T15→T16→T17→T18）。T4 可与 T2/T3 并行。
- 后端每 task 只跑点名的 pytest 子集（全套 hang）。前端每 task `cd packages/web/frontend` 显式 cd + `./node_modules/.bin/vitest`（**别用 pnpm**）。
- 改 web/worker src 后真机冒烟前 `uv sync --all-packages` 重建 worker 镜像。
- 既有 combined-scan 测试（`test_combined_*`）会因签名/语义变化变红——每个 task 的 step 4 已点名「按新语义修既有用例」，executor 在该 task 内一并修，勿留跨 task 红灯。

**Which approach?**
