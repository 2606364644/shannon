# 黑盒 scan_id 编码白盒血缘 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让黑盒 scan_id 从无意义的 `repo-<ts>` 改为 `<白盒scan_id>~<N>`,把"继承自哪次白盒"的血缘直接编码进任务名/目录名。

**Architecture:** 黑盒恒复用白盒结果(`reuse_whitebox_scan_id` 必填,无 standalone)。`ScanStore.create_scan` 新增可选 `lineage` 参数;`_gen_scan_id` 在 `scan_type=="blackbox"` 时改用 `<lineage>~<N>` 格式(lineage = 白盒 scan_id,N = 该白盒已有黑盒序号)。`ScanManager.start` 把"解析白盒血缘"提前到 `create_scan` 之前,并用 `asyncio.Lock` 串行化黑盒 create_scan 以保证序号原子分配。白盒 scan_id 格式 `<repo>-<ts>` 完全不变。

**Tech Stack:** Python 3.12 / asyncio / pytest / FastAPI(web 层)。core `SessionManager` 零改动。

## Global Constraints

- **只跑改动相关测试文件**——shannon-py 全套 pytest 有预存挂起/失败(CLAUDE.md §3),勿广跑全套。本计划仅跑 `test_scan_store.py` 与 `test_scan_manager_blackbox.py`(及 `test_scan_manager.py` 回归)。
- **分支 `feat/fork-py`**(本地多项改动未 push)。动手前先 `git log --oneline -5` 与 `git status` 了解在途工作(CLAUDE.md §3)。
- **`~` 必须保持 URL/文件系统/workflow_id 安全**:RFC 3986 unreserved(URL path 无需 encode)、POSIX 文件名合法、Temporal workflow_id 合法。不得引入需 encode 的字符。
- **白盒 scan_id 格式 `<repo>-YYYYMMDD-HHMMSS` 不变**——向后兼容,现有白盒测试与产物不受影响。
- **core `SessionManager` 零改动**(CLAUDE.md §1 铁律,scan_store 复用 core,不碰 `packages/core/src/supernova_core/session.py`)。
- 黑盒血缘**不依赖从 scan_id 反解**——`session.json` 已存 `reuse_whitebox_scan_id`(`scan_manager.py:149-150`),resume 靠它重定位白盒 scan_dir(`scan_manager.py:219-223`),不读 scan_id 字面。`~` 仅展示用。

---

## File Structure

| 文件 | 责任 | 改动 |
|------|------|------|
| `packages/web/src/supernova_web/components/scan_store.py` | scan_id 生成 + scan 目录存储 | `create_scan` 加 `lineage` 参;`_gen_scan_id` 加 blackbox 分支;新增 `_next_blackbox_seq` |
| `packages/web/src/supernova_web/components/scan_manager.py` | web 提交编排 | `start` 黑盒分支提前解析 lineage + `__init__` 加锁;黑盒 create_scan 在锁内 |
| `packages/web/tests/test_scan_store.py` | scan_id 生成单测 | +3 测试:blackbox 血缘前缀+序号、白盒格式回归、blackbox 缺 lineage 报错 |
| `packages/web/tests/test_scan_manager_blackbox.py` | 黑盒提交集成测试 | +1 测试:`start` 黑盒 → scan_id == `<wb_scan_id>~1` |

---

## Task 1: ScanStore 生成黑盒 scan_id(`<wb_scan_id>~<N>`)

**Files:**
- Modify: `packages/web/src/supernova_web/components/scan_store.py:208-233`(`create_scan` + `_gen_scan_id`)
- Test: `packages/web/tests/test_scan_store.py`(新增 3 个测试)

**Interfaces:**
- Consumes: 无新依赖(`re` 已在模块顶部 import,`_repo_label`/`_now_local` 已存在)
- Produces:
  - `ScanStore.create_scan(self, ws, web_url, repo_path, scan_type="whitebox", lineage: str | None = None) -> tuple[str, Path]`(`lineage` 可选,仅 blackbox 用)
  - `ScanStore._gen_scan_id(self, scans_dir, repo_path, scan_type="whitebox", lineage=None) -> str`
  - `ScanStore._next_blackbox_seq(self, scans_dir, lineage) -> int`

- [ ] **Step 1: 写失败测试(黑盒血缘前缀 + 序号)**

追加到 `packages/web/tests/test_scan_store.py` 末尾:

```python
def test_create_scan_blackbox_uses_lineage_prefix_with_seq(tmp_path):
    """黑盒 scan_id = <wb_scan_id>~<N>:整段白盒 scan_id 作血缘前缀,序号从 1 起。"""
    store = ScanStore(tmp_path)
    wb = "NodeGoat-20260803-1200"
    sid1, _ = store.create_scan("WS", "u", "", scan_type="blackbox", lineage=wb)
    assert sid1 == "NodeGoat-20260803-1200~1"
    sid2, _ = store.create_scan("WS", "u", "", scan_type="blackbox", lineage=wb)
    assert sid2 == "NodeGoat-20260803-1200~2"
    # 不同白盒独立序号空间
    sid_other, _ = store.create_scan("WS", "u", "", scan_type="blackbox",
                                     lineage="NodeGoat-20260804-0900")
    assert sid_other == "NodeGoat-20260804-0900~1"


def test_create_scan_whitebox_format_unchanged(tmp_path):
    """回归保护:白盒 scan_id 格式不变(<repo>-YYYYMMDD-HHMMSS),不含 ~。"""
    store = ScanStore(tmp_path)
    sid, _ = store.create_scan("WS", "u", "/code/NodeGoat")  # 默认 whitebox,不传 lineage
    assert sid.startswith("NodeGoat-")
    assert "~" not in sid


def test_create_scan_blackbox_requires_lineage(tmp_path):
    """黑盒无 lineage → ValueError(黑盒恒复用白盒,lineage 必填,防御性校验)。"""
    store = ScanStore(tmp_path)
    with pytest.raises(ValueError):
        store.create_scan("WS", "u", "", scan_type="blackbox", lineage=None)
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/web/tests/test_scan_store.py::test_create_scan_blackbox_uses_lineage_prefix_with_seq packages/web/tests/test_scan_store.py::test_create_scan_blackbox_requires_lineage -v`
Expected: FAIL(`create_scan` 不接受 `lineage` kwarg → `TypeError`,或黑盒仍走白盒分支生成 `repo-<ts>`)

- [ ] **Step 3: 改 `create_scan` 签名 + `_gen_scan_id` 黑盒分支 + `_next_blackbox_seq`**

修改 `packages/web/src/supernova_web/components/scan_store.py`。

把 `create_scan`(当前 `:208-222`)改成:

```python
    def create_scan(self, ws: str, web_url: str, repo_path: str,
                    scan_type: str = "whitebox",
                    lineage: str | None = None) -> tuple[str, Path]:
        """在 ws 内建新 scan_id 目录 + session.json（不复位 resume；重扫=新 scan）。

        返回 (scan_id, scan_dir)。
        - whitebox/correlation: scan_id = <repo>-YYYYMMDD-HHMMSS,同秒碰撞 -2/-3。
        - blackbox: scan_id = <wb_scan_id>~<N>（lineage=白盒 scan_id,N=该白盒已有黑盒序号,
          per-ws 单调）。lineage 仅 blackbox 用,白盒忽略。
        """
        ws_dir = self._dir / ws
        scans_dir = ws_dir / "scans"
        scan_id = self._gen_scan_id(scans_dir, repo_path, scan_type, lineage)
        # SessionManager(scans_dir) 复用 create_workspace：建 scans/<scan_id>/session.json。
        # core 零改动；幂等（session.json 已存在则不覆盖），但 scan_id 经碰撞规避保证新。
        mgr = SessionManager(scans_dir)
        scan_dir = mgr.create_workspace(
            web_url=web_url, repo_path=repo_path, name=scan_id, scan_type=scan_type)
        return scan_id, scan_dir
```

把 `_gen_scan_id`(当前 `:224-233`)改成(在白盒逻辑前加 blackbox 分支):

```python
    def _gen_scan_id(self, scans_dir: Path, repo_path: str,
                     scan_type: str = "whitebox",
                     lineage: str | None = None) -> str:
        """生成 scan_id。

        blackbox: <wb_scan_id>~<N>（整段白盒 scan_id 作血缘前缀 + per-ws 单调序号;
          lineage=wb_scan_id 必填）。序号并发由 ScanManager 的 create_scan lock 串行化,
          此处 while-exists 兜底防同序号目录竞态。
        whitebox/correlation(默认): <repo>-YYYYMMDD-HHMMSS（仓库名前缀 + 本地时区紧凑秒级）;
          同秒碰撞 -2/-3。仓库名前缀让扫描目录一眼可辨（对齐 legacy NodeGoat_<ts> 可读性）。
        """
        if scan_type == "blackbox":
            if not lineage:
                raise ValueError("blackbox scan_id 需要 lineage（=白盒 scan_id）")
            n = self._next_blackbox_seq(scans_dir, lineage)
            scan_id = f"{lineage}~{n}"
            while (scans_dir / scan_id / "session.json").exists():
                n += 1
                scan_id = f"{lineage}~{n}"
            return scan_id
        base = f"{_repo_label(repo_path)}-{_now_local().strftime('%Y%m%d-%H%M%S')}"
        scan_id = base
        i = 2
        while (scans_dir / scan_id / "session.json").exists():
            scan_id = f"{base}-{i}"
            i += 1
        return scan_id

    def _next_blackbox_seq(self, scans_dir: Path, lineage: str) -> int:
        """数 scans_dir 下 {lineage}~<N> 已有黑盒序号,返回 max+1（从 1 起）。

        linegae 含 '-',故用 re.escape;~ 不需转义但 re.escape 整串处理。匹配锚定末尾
        （防 repo 名含 lineage 前缀的误匹配）。
        """
        pat = re.compile(re.escape(lineage) + r"~(\d+)$")
        existing: list[int] = []
        if scans_dir.is_dir():
            for entry in scans_dir.iterdir():
                m = pat.match(entry.name)
                if m:
                    existing.append(int(m.group(1)))
        return (max(existing) + 1) if existing else 1
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/web/tests/test_scan_store.py -v`
Expected: PASS(含原有 `test_create_scan_id_format` / `test_create_scan_same_second_collision` / `test_create_scan_repo_name_prefix` 白盒测试不破,3 个新测试过)

- [ ] **Step 5: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add packages/web/src/supernova_web/components/scan_store.py packages/web/tests/test_scan_store.py
git commit -m "feat(web): blackbox scan_id encodes whitebox lineage (<wb>~<N>)

ScanStore.create_scan 新增 lineage 参;_gen_scan_id blackbox 分支用
<wb_scan_id>~<N> 替代空 repo 兜底的 repo-<ts>。白盒格式不变。"
```

---

## Task 2: ScanManager.start 提前解析白盒血缘 + 原子锁

**Files:**
- Modify: `packages/web/src/supernova_web/components/scan_manager.py:108-160`(`start`) + `__init__`(加锁)
- Test: `packages/web/tests/test_scan_manager_blackbox.py`(新增 1 个 start 集成测试)

**Interfaces:**
- Consumes: Task 1 的 `ScanStore.create_scan(..., lineage=)`(blackbox 必传)
- Produces: `ScanManager.start` 黑盒分支产出的 scan_id 形如 `<wb_scan_id>~1`;`self._create_scan_lock: asyncio.Lock`

- [ ] **Step 1: 写失败测试(start 黑盒 → scan_id 含血缘)**

追加到 `packages/web/tests/test_scan_manager_blackbox.py`。复用该文件已有的 mock 风格(`ScanManager(workspaces_dir, repos_dir, config_store)`),并参考 `test_scan_manager.py` 的 `_patch_temporal_ok` / `_patch_client`(本测试内联等价 mock):

```python
@pytest.mark.asyncio
async def test_start_blackbox_scan_id_encodes_whitebox_lineage(tmp_path, monkeypatch):
    """start 黑盒:scan_id = <wb_scan_id>~1,血缘前缀来自 reuse_whitebox_scan_id。"""
    from unittest.mock import AsyncMock, MagicMock
    from supernova_web.components.scan_manager import ScanManager
    from supernova_web.models import ScanRequest

    mgr = ScanManager(workspaces_dir=tmp_path, repos_dir=tmp_path, config_store=object())
    monkeypatch.setattr(mgr, "_check_temporal", AsyncMock(return_value=None))
    mock_handle = MagicMock()
    mock_client = AsyncMock()
    mock_client.start_workflow = AsyncMock(return_value=mock_handle)
    monkeypatch.setattr("supernova_web.components.scan_manager.Client.connect",
                        AsyncMock(return_value=mock_client))

    # 先建白盒 scan 作 reuse 源(真实 wb_scan_id = NodeGoat-<ts>)
    wb_scan_id, _ = mgr._store.create_scan(
        "WS1", "http://e", "/code/NodeGoat", "whitebox")

    ws, scan_id = await mgr.start(ScanRequest(
        type="blackbox", url="http://e", workspace="WS1",
        reuse_whitebox_scan_id=wb_scan_id))

    assert scan_id == f"{wb_scan_id}~1"
    # session 仍持久化 reuse_whitebox_scan_id(resume 靠它)
    import json
    sess = json.loads((tmp_path / "WS1" / "scans" / scan_id / "session.json").read_text())
    assert sess.get("reuse_whitebox_scan_id") == wb_scan_id
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/web/tests/test_scan_manager_blackbox.py::test_start_blackbox_scan_id_encodes_whitebox_lineage -v`
Expected: FAIL(现状黑盒 scan_id 是 `repo-<ts>`,断言 `== {wb}~1` 不成立)

- [ ] **Step 3: `__init__` 加 create_scan 锁**

在 `ScanManager.__init__`(类定义起始,约 `:56` 之后)末尾加一行:

```python
        self._create_scan_lock = asyncio.Lock()  # 串行化黑盒 create_scan,保证 ~<N> 序号原子
```

(`asyncio` 已在 `scan_manager.py:3` import。)

- [ ] **Step 4: `start` 黑盒分支提前解析 lineage + create_scan 入锁**

修改 `start` 方法中 `create_scan` 调用处(当前 `:127-131`)。把:

```python
        ws_dir = self._workspaces_dir / ws
        ws_dir.mkdir(parents=True, exist_ok=True)
        # T3: ScanStore 建 scan_id 目录 + session.json（ws 根不再写 session.json）。
        scan_id, scan_dir = self._store.create_scan(
            ws, req.url or "", target or "", req.type)
```

改为:

```python
        ws_dir = self._workspaces_dir / ws
        ws_dir.mkdir(parents=True, exist_ok=True)

        # 黑盒:提前解析白盒血缘作 scan_id 前缀(<wb_scan_id>~<N>)。
        # _resolve_blackbox_inputs 仍负责 config_path + repo_path(需 scan_dir,在 create_scan 后),
        # 此处只提前拿 lineage 喂 _gen_scan_id;reuse 校验与 _resolve_blackbox_inputs 幂等重复,可接受。
        lineage: str | None = None
        if req.type == "blackbox":
            if not req.reuse_whitebox_scan_id:
                raise ValueError("blackbox 扫描必须复用白盒结果（reuse_whitebox_scan_id）")
            wb_scan_dir = self._store.get_scan_dir(ws, req.reuse_whitebox_scan_id)
            if wb_scan_dir is None:
                raise ValueError(f"要复用的白盒扫描不存在: {req.reuse_whitebox_scan_id}")
            lineage = req.reuse_whitebox_scan_id

        # 黑盒 create_scan 在锁内,保证 ~<N> 序号分配原子(防并发同白盒争同序号);
        # 白盒无序号竞态,不加锁。
        if req.type == "blackbox":
            async with self._create_scan_lock:
                scan_id, scan_dir = self._store.create_scan(
                    ws, req.url or "", target or "", req.type, lineage=lineage)
        else:
            # T3: ScanStore 建 scan_id 目录 + session.json（ws 根不再写 session.json）。
            scan_id, scan_dir = self._store.create_scan(
                ws, req.url or "", target or "", req.type)
```

- [ ] **Step 5: 跑测试验证通过**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/web/tests/test_scan_manager_blackbox.py -v`
Expected: PASS(新测试过;原有 `_submit_blackbox` / `_resolve_blackbox_inputs` 直测不破——它们隔离了 create_scan,且 `_resolve_blackbox_inputs` 的 reuse 校验仍存在)

- [ ] **Step 6: 回归白盒 start 测试**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/web/tests/test_scan_manager.py -v`
Expected: PASS(白盒 start 走 else 分支,行为不变)

- [ ] **Step 7: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add packages/web/src/supernova_web/components/scan_manager.py packages/web/tests/test_scan_manager_blackbox.py
git commit -m "feat(web): wire blackbox lineage into scan_id in ScanManager.start

start 黑盒分支提前解析 reuse_whitebox_scan_id 作 lineage 传给 create_scan;
asyncio.Lock 串行化黑盒 create_scan 保证 ~<N> 序号原子。"
```

---

## Self-Review

**1. Spec coverage(诉求 → task):**
- "黑盒 scan_id 编码白盒血缘 `<wb>~<N>`" → Task 1 `_gen_scan_id` blackbox 分支 ✓
- "整段白盒 scan_id 都在" → Task 1 `f"{lineage}~{n}"`,lineage=wb_scan_id 整段 ✓
- "`~` 分隔符" → Task 1 ✓
- "序号从 1,per-ws 单调" → Task 1 `_next_blackbox_seq` ✓
- "调用顺序调整(血缘解析提前到 create_scan 前)" → Task 2 Step 4 ✓
- "并发序号原子" → Task 2 `asyncio.Lock` ✓
- "白盒不变" → Task 1 默认分支 + Task 2 else 分支 + 回归测试 ✓
- "测试断言更新" → Task 1/2 新增测试(原有 `startswith("repo-")` 是白盒空 repo 测试,白盒行为未变,保留)✓

**2. Placeholder scan:** 无 TBD/TODO/"适当处理";所有代码块含实际实现与测试代码;commit message 完整。

**3. Type一致性:**
- `create_scan(..., lineage: str | None = None)`(Task 1 定义) ↔ `create_scan(..., lineage=lineage)`(Task 2 调用)✓
- `_gen_scan_id(self, scans_dir, repo_path, scan_type="whitebox", lineage=None)` ↔ `create_scan` 内 `self._gen_scan_id(scans_dir, repo_path, scan_type, lineage)` ✓
- `_next_blackbox_seq(self, scans_dir, lineage) -> int` ↔ `_gen_scan_id` 内 `n = self._next_blackbox_seq(...)` ✓
- `self._create_scan_lock`(Task 2 Step 3 定义) ↔ `async with self._create_scan_lock`(Step 4 使用)✓

**遗留说明(非本计划范围,记录备查):**
- `_resolve_blackbox_inputs`(`scan_manager.py:264-301`)内部仍重复校验一次 `reuse_whitebox_scan_id` + `get_scan_dir`,与 Task 2 Step 4 提前校验幂等重复。本计划不去重(最小改动);如需收敛,可让 `_resolve_blackbox_inputs` 接收预解析的 `wb_scan_dir` 跳过二次查找,但属单独重构。
- 序号 N 在黑盒 scan 被删除后可能重用(`-bb1` 删后再建又 `~1`)——"第 N 次"语义在删除后不准,但 `reuse_whitebox_scan_id` 在 session 精确溯源不受影响。可接受;如需稳定序号,后续可加持久计数器。

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-04-blackbox-scan-id-whitebox-lineage.md`. Two execution options:

**1. Subagent-Driven (recommended)** - 每个 task 派 fresh subagent,task 间两阶段 review,迭代快
**2. Inline Execution** - 在本 session 按 executing-plans 批量执行,带 checkpoint review

Which approach?
