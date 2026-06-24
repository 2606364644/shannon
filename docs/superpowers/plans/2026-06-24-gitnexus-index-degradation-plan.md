# GitNexus 索引降级实现计划（Plan 4）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `run_code_index`（GitNexus 索引）在超时/失败时**优雅降级为空 GitNexus 轨**（只有 LLM 轨），而非 fail-fast 拖死并发 pre-recon agent——完成 spec §6（前置依赖：GitNexus 索引可靠性）+ §10 风险 + §9 验收 5。

**Architecture:** `run_code_index` 的三处调用（`engine.ensure_indexed()`、`build_code_index_with_gitnexus` MCP 路径、fallback 路径）全部包进 `asyncio.wait_for` 超时 + `asyncio.to_thread`（解同步阻塞）。超时/异常时**不 raise**，改为走 `_StubMCPClient` minimal 路径（`parameter_graph=None` → `parameter_graph.json` 不落盘 → 下游 `if exists` 守卫安全跳过）。索引层产 `DegradationLevel.MINIMAL` 的 `code_index.json`，下游已全部 `if exists` 守卫（`run_sink_detection:374` / `run_entry_point_fusion:420` / `run_risk_scoring:545-554` / `run_render_dataflow_hints:426-428`）。GitNexus 轨产物为空 = pipeline 继续跑 LLM 轨。

**Tech Stack:** Python 3.12, asyncio（`wait_for` + `to_thread`）, temporalio, pytest, pytest-asyncio

## Global Constraints

- **降级不 raise**：超时/异常时 `run_code_index` 必须返回一个 MINIMAL `code_index.json` + 不写 `parameter_graph.json`，**绝不向 Temporal 抛 `ApplicationFailure`**（否则取消并发 pre-recon agent，复现 memory 记录的 11min fail-fast）
- **不改下游读取逻辑**：下游已全部 `if exists` 守卫（`activities.py:374/420/426-428/545-554`）；GitNexus 轨降级=空 = 下游自然跳过（`code_index.json` 仍存在但 MINIMAL；`parameter_graph.json` 缺失）
- **保持 `write_index_files` 返回 2 元组**（`activities.py:294` 解包 `json_path, summary_path`）；`parameter_graph.json` 作为副作用写入
- **同步阻塞也解**：`engine.ensure_indexed()` 用同步 `subprocess.run`（`gitnexus_engine.py:137`）阻塞 event loop；用 `asyncio.to_thread` 包住，否则 `asyncio.wait_for` 在阻塞的同步调用上**无法取消**（memory 记录的上游阻塞根因）
- **TDD**：每个改动先写失败测试；frequent commits（`fix(whitebox):` / `fix(code_index):`）
- **真实 >10min 慢索引需真实仓库 + GitNexus CLI 环境**，单元测试用 mock 压缩超时窗口覆盖超时/降级语义；真实 GitNexus 慢索引流转由手动冒烟验证（spec 已注明端到端冒烟待人工）

---

### Task 1: `ensure_indexed` 同步阻塞改 `asyncio.to_thread` + `asyncio.wait_for` 超时

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:256-292`（`run_code_index` 的 GitNexus 索引段）
- Test: `packages/whitebox/tests/test_run_code_index_degradation.py`（Create）

**Interfaces:**
- Consumes: `GitNexusEngine.ensure_indexed()`（`gitnexus_engine.py:53`，同步，内部 `subprocess.run`，`timeout=300`）
- Produces: `run_code_index` 的索引段在可配置超时（`GITNEXUS_INDEX_TIMEOUT` 秒，默认 120）内完成；超时/异常时 `indexed=False`（不 raise）；同步阻塞用 `asyncio.to_thread` 包住使 `wait_for` 可取消

**背景**：当前 `engine.ensure_indexed()`（L261）直接同步调用，event loop 被阻塞，`asyncio.wait_for` 即使到期也无法取消该同步 `subprocess.run`（memory 记录的"上游同步阻塞"根因之一）。`to_thread` 把它丢进线程池，event loop 可继续调度并发 pre-recon agent，`wait_for` 到期可让 task 标记取消（同步 subprocess 仍在其线程内跑到 `self.timeout=300` 自然超时，但不阻塞 loop）。

- [ ] **Step 1: Write the failing test**

```python
# packages/whitebox/tests/test_run_code_index_degradation.py
"""Spec §6/§9.5/§10: run_code_index 在 GitNexus 索引超时/失败时优雅降级,
不向 Temporal 抛 ApplicationFailure(否则取消并发 pre-recon agent)。"""
import asyncio

import pytest

from shannon_whitebox.pipeline import activities


def _make_input(tmp_path):
    from shannon_whitebox.pipeline.shared import ActivityInput
    return ActivityInput(
        agent_name="code-index",
        repo_path=str(tmp_path),
        deliverables_subdir="deliverables",
    )


@pytest.mark.asyncio
async def test_ensure_indexed_uses_to_thread_and_wait_for(monkeypatch, tmp_path):
    """ensure_indexed 跑在 to_thread(wait_for 包),超时 → indexed=False(不 raise)。"""
    # 模拟 ensure_indexed 阻塞 5s(远超我们设置的 wait_for 窗口)
    class SlowEngine:
        def __init__(self, *a, **kw):
            pass

        def is_available(self):
            return True

        def ensure_indexed(self, force=False):
            import time
            time.sleep(5)
            return type("R", (), {"success": True})()

    monkeypatch.setattr(activities, "GitNexusEngine", SlowEngine)

    # 把超时窗口压到 0.1s,使 wait_for 必然超时
    monkeypatch.setattr(activities, "GITNEXUS_INDEX_TIMEOUT", 0.1)

    captured_index = {}
    async def fake_build(*args, **kwargs):
        captured_index["called"] = True
        from shannon_core.code_index.models import CodeIndex, DegradationLevel
        return CodeIndex(
            repository=str(tmp_path), language="python",
            total_blocks=0, total_entry_points=0, total_chains=0,
            blocks=[], edges=[], entry_points=[], chains=[],
            degradation_level=DegradationLevel.MINIMAL,
        )

    async def fake_write(index, out_dir):
        from pathlib import Path
        p = Path(out_dir) / "code_index.json"
        p.write_text("{}")
        return p, p

    monkeypatch.setattr(activities, "build_code_index_with_gitnexus", fake_build)
    monkeypatch.setattr(activities, "write_index_files", fake_write)

    class FakeGit:
        @staticmethod
        async def commit_index(d):
            return None
    monkeypatch.setattr(activities, "GitManager", FakeGit)

    # 跳过 audit session(真实 track_step 用桩)
    class FakeSession:
        def track_step(self, *a, **kw):
            class CM:
                async def __aenter__(self):
                    return self
                async def __aexit__(self, *a):
                    return False
            return CM()
    monkeypatch.setattr(
        "shannon_whitebox.audit.session_registry.get_audit_session",
        lambda: FakeSession(),
    )

    from temporalio import activity
    # 跳过 activity.info().attempt
    monkeypatch.setattr(activity, "info", lambda: type("I", (), {"attempt": 1})())

    # 超时应被捕获 → indexed=False → 走 _StubMCPClient minimal 分支
    result = await activities.run_code_index(_make_input(tmp_path))
    # 降级不 raise;返回 MINIMAL index 的统计
    assert result["total_blocks"] == 0
    # build 用 StubMCPClient(minimal) 路径被调用
    assert captured_index.get("called") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_run_code_index_degradation.py::test_ensure_indexed_uses_to_thread_and_wait_for -v`
Expected: FAIL — `ensure_indexed` 当前直接同步调用（无 `to_thread`/`wait_for`），5s 阻塞不会被 0.1s 超时截断；且超时会冒泡为 `asyncio.TimeoutError` → `ApplicationFailure`（fail-fast）。测试可能 hang 到默认超时或抛 TimeoutError。

- [ ] **Step 3: Wrap ensure_indexed in to_thread + wait_for**

Edit `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`。在 `run_code_index` 内（L255-292），先在文件顶部加 `asyncio` import（L1 区，`import json` 之后）：

```python
import asyncio
import json
import time
```

然后在 `run_code_index` 的 `from shannon_core.code_index.gitnexus_engine import GitNexusEngine`（L256）**之后**加超时常量（模块级，放 `_StubMCPClient` 之前约 L222）：

```python
# Spec §6/§10: GitNexus 索引超时上限。超过此值降级为 minimal(GitNexus 轨空),
# 而非 fail-fast 拖死并发 pre-recon agent(memory pre-recon-gitnexus-blockage)。
# 注意:activity 级 10min 超时(workflows.py)是最后兜底;此处先于它降级。
GITNEXUS_INDEX_TIMEOUT: int = 120  # seconds
```

替换索引段（L258-292）为：

```python
            engine = GitNexusEngine(Path(repo))
            indexed = False
            if engine.is_available():
                try:
                    # ensure_indexed 用同步 subprocess.run(gitnexus_engine.py:137),
                    # 直接调用会阻塞 event loop(memory 根因①)。to_thread 丢线程池,
                    # wait_for 到期可让 task 标记取消(不阻塞并发 pre-recon agent)。
                    result = await asyncio.wait_for(
                        asyncio.to_thread(engine.ensure_indexed),
                        timeout=GITNEXUS_INDEX_TIMEOUT,
                    )
                    indexed = result.success
                    if not indexed:
                        logger.warning("GitNexus indexing failed: %s", result.error_message)
                except asyncio.TimeoutError:
                    logger.warning(
                        "GitNexus indexing timed out after %ss, degrading to minimal",
                        GITNEXUS_INDEX_TIMEOUT,
                    )
                    indexed = False
                except Exception as exc:
                    logger.warning(
                        "GitNexus indexing errored (%s), degrading to minimal", exc,
                    )
                    indexed = False

            if indexed:
                try:
                    async with GitNexusMCPClient(Path(repo)) as mcp:
                        index = await build_code_index_with_gitnexus(
                            str(repo),
                            mcp_client=mcp,
                            llm_client=_llm_taint_client,
                            auto_index=False,  # already indexed above
                        )
                except Exception as exc:
                    logger.warning(
                        "GitNexus MCP failed (%s), falling back to minimal index", exc,
                    )
                    index = await build_code_index_with_gitnexus(
                        str(repo),
                        mcp_client=_StubMCPClient(),
                        llm_client=_llm_taint_client,
                        auto_index=True,  # will detect unavailable → minimal mode
                    )
            else:
                # GitNexus CLI missing / indexing failed / timed out — minimal AST-only
                index = await build_code_index_with_gitnexus(
                    str(repo),
                    mcp_client=_StubMCPClient(),
                    llm_client=_llm_taint_client,
                    auto_index=True,
                )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_run_code_index_degradation.py::test_ensure_indexed_uses_to_thread_and_wait_for -v`
Expected: PASS — 0.1s 超时触发，`indexed=False`，走 minimal `_StubMCPClient` 分支，返回 MINIMAL index，不 raise。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/tests/test_run_code_index_degradation.py
git commit -m "fix(whitebox): wrap GitNexus ensure_indexed in to_thread+wait_for (degrade on timeout)"
```

---

### Task 2: `build_code_index_with_gitnexus` 调用也加 `wait_for` 超时（MCP 路径）

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:266-292`（`run_code_index` 的 build 段，Task 1 改完后）
- Test: `packages/whitebox/tests/test_run_code_index_degradation.py`（扩展）

**Interfaces:**
- Consumes: `build_code_index_with_gitnexus`（async，`code_index/__init__.py`）、`GITNEXUS_INDEX_TIMEOUT`（Task 1）、`_StubMCPClient`（`activities.py:225`，返回 None → MINIMAL）
- Produces: MCP build 路径（含 `GitNexusMCPClient` + tree-sitter `parser.parse_file` 同步阻塞，memory 根因②）超时 → 降级 minimal；三处 build 调用统一超时上限

**背景**：`build_code_index_with_gitnexus` 是 async，但内部 `parser.parse_file`（`code_index/__init__.py:137`）是同步 tree-sitter，对大仓库（memory 记录的 651 文件）阻塞 event loop。MCP 查询路径也是已知慢点。Task 1 只覆盖 `ensure_indexed`；本 task 覆盖三处 `build_code_index_with_gitnexus` 调用（MCP 路径 L269、fallback L279、minimal L287），统一加 `wait_for`。

- [ ] **Step 1: Write the failing test**

Append to `packages/whitebox/tests/test_run_code_index_degradation.py`:

```python
@pytest.mark.asyncio
async def test_build_mcp_path_timeout_degrades_to_minimal(monkeypatch, tmp_path):
    """MCP build 路径超时 → 降级 minimal(StubMCPClient),不 raise。"""
    class FastEngine:
        def __init__(self, *a, **kw):
            pass
        def is_available(self):
            return True
        def ensure_indexed(self, force=False):
            return type("R", (), {"success": True})()

    monkeypatch.setattr(activities, "GitNexusEngine", FastEngine)

    call_count = {"n": 0}

    async def slow_build(*args, **kwargs):
        call_count["n"] += 1
        # 第一次(MCP 路径)永远 hang;第二次(fallback minimal)正常返回
        if call_count["n"] == 1:
            await asyncio.sleep(5)
        from shannon_core.code_index.models import CodeIndex, DegradationLevel
        return CodeIndex(
            repository=str(tmp_path), language="python",
            total_blocks=0, total_entry_points=0, total_chains=0,
            blocks=[], edges=[], entry_points=[], chains=[],
            degradation_level=DegradationLevel.MINIMAL,
        )

    async def fake_write(index, out_dir):
        from pathlib import Path
        p = Path(out_dir) / "code_index.json"
        p.write_text("{}")
        return p, p

    monkeypatch.setattr(activities, "build_code_index_with_gitnexus", slow_build)
    monkeypatch.setattr(activities, "write_index_files", fake_write)
    # MCP 路径超时窗口压到 0.1s
    monkeypatch.setattr(activities, "GITNEXUS_BUILD_TIMEOUT", 0.1)

    class FakeGit:
        @staticmethod
        async def commit_index(d):
            return None
    monkeypatch.setattr(activities, "GitManager", FakeGit)

    class FakeMCP:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
    monkeypatch.setattr(activities, "GitNexusMCPClient", lambda repo: FakeMCP())

    class FakeSession:
        def track_step(self, *a, **kw):
            class CM:
                async def __aenter__(self):
                    return self
                async def __aexit__(self, *a):
                    return False
            return CM()
    monkeypatch.setattr(
        "shannon_whitebox.audit.session_registry.get_audit_session",
        lambda: FakeSession(),
    )

    from temporalio import activity
    monkeypatch.setattr(activity, "info", lambda: type("I", (), {"attempt": 1})())

    result = await activities.run_code_index(_make_input(tmp_path))
    # MCP 路径超时 → fallback minimal build 被调用两次(call_count==2)
    assert call_count["n"] == 2
    assert result["total_blocks"] == 0  # MINIMAL
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_run_code_index_degradation.py::test_build_mcp_path_timeout_degrades_to_minimal -v`
Expected: FAIL — 当前 MCP 路径的 `build_code_index_with_gitnexus`（L269）无 `wait_for`；slow_build 第一次 `await asyncio.sleep(5)` 不会被 0.1s 截断，测试 hang 到默认超时或抛 `asyncio.TimeoutError` → `ApplicationFailure`。

- [ ] **Step 3: Add `GITNEXUS_BUILD_TIMEOUT` and wrap the MCP build path**

Edit `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`。在 Task 1 加的 `GITNEXUS_INDEX_TIMEOUT` 之后追加：

```python
# Spec §6/§10: build_code_index_with_gitnexus(MCP 查询 + tree-sitter parse_file
# 同步阻塞,memory 根因②)的超时上限。超时降级 minimal。
GITNEXUS_BUILD_TIMEOUT: int = 180  # seconds
```

替换 `if indexed:` 块（Task 1 改后的 L266-284）为：

```python
            if indexed:
                try:
                    async with GitNexusMCPClient(Path(repo)) as mcp:
                        try:
                            index = await asyncio.wait_for(
                                build_code_index_with_gitnexus(
                                    str(repo),
                                    mcp_client=mcp,
                                    llm_client=_llm_taint_client,
                                    auto_index=False,  # already indexed above
                                ),
                                timeout=GITNEXUS_BUILD_TIMEOUT,
                            )
                        except asyncio.TimeoutError:
                            logger.warning(
                                "GitNexus MCP build timed out after %ss, "
                                "falling back to minimal index",
                                GITNEXUS_BUILD_TIMEOUT,
                            )
                            index = await build_code_index_with_gitnexus(
                                str(repo),
                                mcp_client=_StubMCPClient(),
                                llm_client=_llm_taint_client,
                                auto_index=True,
                            )
                except Exception as exc:
                    logger.warning(
                        "GitNexus MCP failed (%s), falling back to minimal index", exc,
                    )
                    index = await build_code_index_with_gitnexus(
                        str(repo),
                        mcp_client=_StubMCPClient(),
                        llm_client=_llm_taint_client,
                        auto_index=True,  # will detect unavailable → minimal mode
                    )
            else:
                # GitNexus CLI missing / indexing failed / timed out — minimal AST-only
                try:
                    index = await asyncio.wait_for(
                        build_code_index_with_gitnexus(
                            str(repo),
                            mcp_client=_StubMCPClient(),
                            llm_client=_llm_taint_client,
                            auto_index=True,
                        ),
                        timeout=GITNEXUS_BUILD_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "Minimal build timed out after %ss; emitting empty index",
                        GITNEXUS_BUILD_TIMEOUT,
                    )
                    from shannon_core.code_index.models import CodeIndex, DegradationLevel
                    index = CodeIndex(
                        repository=str(repo), language="unknown",
                        total_blocks=0, total_entry_points=0, total_chains=0,
                        blocks=[], edges=[], entry_points=[], chains=[],
                        degradation_level=DegradationLevel.MINIMAL,
                    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_run_code_index_degradation.py::test_build_mcp_path_timeout_degrades_to_minimal -v`
Expected: PASS — MCP 路径 0.1s 超时触发 fallback，`slow_build` 被调两次（第二次 minimal 正常返回），不 raise。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/tests/test_run_code_index_degradation.py
git commit -m "fix(whitebox): add wait_for timeout to build_code_index MCP+minimal paths"
```

---

### Task 3: 降级产物为空 `parameter_graph.json`（GitNexus 轨空）+ 下游安全跳过验证

**Files:**
- Test: `packages/whitebox/tests/test_run_code_index_degradation.py`（扩展）
- Verify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:426-428,545-554`（只读确认下游守卫已存在）

**Interfaces:**
- Consumes: Task 1/2（降级时 `build_code_index_with_gitnexus` 用 `_StubMCPClient` → MINIMAL → `index.parameter_graph=None`）、`write_index_files`（`code_index/__init__.py:299`，当 `parameter_graph is None` 时不写 `parameter_graph.json`）、下游 `if param_graph_path.exists()` 守卫
- Produces: 验证降级时 `parameter_graph.json` **不存在**；`code_index.json` 存在但 MINIMAL；下游 `if exists` 守卫跳过 → GitNexus 轨产物为空 = pipeline 继续

**背景**：降级语义闭环验证。GitNexus 轨产物是 `parameter_graph.json`（taint 候选链，Plan 1 落盘）+ `code_index.json` 的 sink/chain（MINIMAL 时为空）。降级 = GitNexus 轨产物为空。下游客户端（`run_risk_scoring:545-554`、`run_render_dataflow_hints:426-428`）已 `if exists` 守卫读 `parameter_graph.json`；`run_sink_detection:374` / `run_entry_point_fusion:420` `if exists` 守卫读 `code_index.json`（MINIMAL 时存在但内容空）。本 task 验证降级产物 + 下游守卫闭环。

- [ ] **Step 1: Write the failing test (degradation → no parameter_graph.json)**

Append to `packages/whitebox/tests/test_run_code_index_degradation.py`:

```python
@pytest.mark.asyncio
async def test_degradation_produces_no_parameter_graph(monkeypatch, tmp_path):
    """Spec §9.5: GitNexus 索引失败 → GitNexus 轨空(parameter_graph.json 缺失),
    code_index.json 存在但 MINIMAL;下游 if exists 守卫安全跳过。"""
    from pathlib import Path

    class FailEngine:
        def __init__(self, *a, **kw):
            pass
        def is_available(self):
            return True
        def ensure_indexed(self, force=False):
            return type("R", (), {"success": False, "error_message": "boom"})()

    monkeypatch.setattr(activities, "GitNexusEngine", FailEngine)

    written_files: dict = {}

    # 用真实的 write_index_files(验证 parameter_graph 缺失语义)
    from shannon_core.code_index import write_index_files as real_write
    async def fake_write(index, out_dir):
        return real_write(index, out_dir)
    monkeypatch.setattr(activities, "write_index_files", fake_write)

    class FakeGit:
        @staticmethod
        async def commit_index(d):
            return None
    monkeypatch.setattr(activities, "GitManager", FakeGit)

    class FakeSession:
        def track_step(self, *a, **kw):
            class CM:
                async def __aenter__(self):
                    return self
                async def __aexit__(self, *a):
                    return False
            return CM()
    monkeypatch.setattr(
        "shannon_whitebox.audit.session_registry.get_audit_session",
        lambda: FakeSession(),
    )

    from temporalio import activity
    monkeypatch.setattr(activity, "info", lambda: type("I", (), {"attempt": 1})())

    # ActivityInput 把 deliverables 指到 tmp_path/deliverables
    from shannon_whitebox.pipeline.shared import ActivityInput
    inp = ActivityInput(
        agent_name="code-index",
        repo_path=str(tmp_path),
        deliverables_subdir="deliverables",
    )

    await activities.run_code_index(inp)

    deliverables = tmp_path / "deliverables"
    # code_index.json 存在(MINIMAL);parameter_graph.json 缺失
    assert (deliverables / "code_index.json").exists()
    assert not (deliverables / "parameter_graph.json").exists(), (
        "降级时 GitNexus 轨(parameter_graph)必须为空;存在则下游误读非空 taint"
    )

    # code_index.json 标 MINIMAL
    import json
    idx = json.loads((deliverables / "code_index.json").read_text())
    assert idx["degradation_level"] == "minimal"
```

> 注：`ActivityInput` 字段以 `packages/whitebox/src/shannon_whitebox/pipeline/shared.py` 实际定义为准；若 `deliverables_subdir` 名不同，先 `grep "class ActivityInput" packages/whitebox/src/shannon_whitebox/pipeline/shared.py` 确认后调整。`resolve_deliverables_path`（`activities.py:25`）用 `repo_path + deliverables_subdir` 解析到 `tmp_path/deliverables`。

- [ ] **Step 2: Run test to verify it fails (or passes as baseline confirmation)**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_run_code_index_degradation.py::test_degradation_produces_no_parameter_graph -v`
Expected: PASS（MINIMAL 路径 `index.parameter_graph=None` → `write_index_files` 不写 `parameter_graph.json`，此为 Plan 1 Task 2 已实现语义）— 此测试锁定降级产物语义不回归。若 FAIL（如 `parameter_graph.json` 被错误写出），说明 `_StubMCPClient` minimal 路径产了非空 pgraph，需回查 `build_code_index_with_gitnexus` 的 fallback 分支。

- [ ] **Step 3: Verify downstream guards exist (read-only, no edit)**

Confirm 下游 `if exists` 守卫已存在（无需改）。Run:

```bash
cd /root/shannon-py && grep -n "parameter_graph.json\|code_index.json" packages/whitebox/src/shannon_whitebox/pipeline/activities.py
```

Expected output（关键行）：
- `:374` `if code_index_path.exists():`（`run_sink_detection` 读 `code_index.json`）
- `:420` `if not code_index_path.exists():`（`run_entry_point_fusion` 早返回）
- `:426-428` `param_graph_path = ... "parameter_graph.json"; if param_graph_path.exists():`（`run_render_dataflow_hints`）
- `:545-554` `param_graph_path = ... "parameter_graph.json"; ... if param_graph_path.exists()`（`run_risk_scoring`）

> 闭环：降级 → `parameter_graph.json` 缺失 → `:428/:554` 跳过；`code_index.json` MINIMAL 存在 → `:374/:420` 读到空 sinks/entry_points（不崩）。

- [ ] **Step 4: Run the full degradation test suite**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_run_code_index_degradation.py -v`
Expected: PASS（3 tests：Task 1 超时降级 + Task 2 MCP 超时降级 + Task 3 产物缺失）

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/whitebox/tests/test_run_code_index_degradation.py
git commit -m "test(whitebox): verify GitNexus degradation emits empty parameter_graph (§9.5)"
```

---

### Task 4: `run_code_index` 顶层异常不 fail-fast（最终降级兜底）

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:308-313`（`run_code_index` 的 `except` 块）
- Test: `packages/whitebox/tests/test_run_code_index_degradation.py`（扩展）

**Interfaces:**
- Consumes: Task 1/2/3（降级路径）、`classify_error_for_temporal`（`activities.py:10`）
- Produces: 任何逃逸异常（含 `write_index_files` 失败、`GitManager.commit_index` 失败）也不 fail-fast 拖死 pipeline；返回 MINIMAL 空产物 dict

**背景**：Task 1/2 覆盖了索引/build 阶段的超时。但 `write_index_files`（L294）或 `GitManager.commit_index`（L299）失败时，当前 `except Exception`（L311-313）仍 raise `ApplicationFailure` → 拖死并发 agent。spec §9.5 要求"GitNexus 索引失败时优雅降级,不拖死 pipeline"——即整个 `run_code_index` 不应有任何路径 raise（配置错误如 REPO_NOT_FOUND 仍 raise,那是 preflight 职责）。本 task 给最外层加一个"产空产物 + 不 raise"的兜底。

> **决策点**：是否所有异常都不 raise?`PentestError`（配置/仓库问题,如 REPO_NOT_FOUND）**应仍 raise**(那是真错误,该停);但 GitNexus/索引/落盘相关的非配置异常**不应 raise**(降级)。本 task 用 `PentestError` 仍 raise + 其余 `Exception` 降级的方式实现。如果你希望更保守(任何异常都不 raise),把 `except PentestError` 块也改为降级——但那样会掩盖配置错误,**默认不这么做**(见 Self-Review 决策点)。

- [ ] **Step 1: Write the failing test**

Append to `packages/whitebox/tests/test_run_code_index_degradation.py`:

```python
@pytest.mark.asyncio
async def test_write_index_failure_does_not_fail_pipeline(monkeypatch, tmp_path):
    """Spec §9.5: write_index_files/commit_index 失败也不 fail-fast,
    返回空产物 dict(下游 if exists 守卫安全跳过)。"""
    class FastEngine:
        def __init__(self, *a, **kw):
            pass
        def is_available(self):
            return False  # 直走 minimal

    monkeypatch.setattr(activities, "GitNexusEngine", FastEngine)

    async def fake_build(*args, **kwargs):
        from shannon_core.code_index.models import CodeIndex, DegradationLevel
        return CodeIndex(
            repository=str(tmp_path), language="python",
            total_blocks=0, total_entry_points=0, total_chains=0,
            blocks=[], edges=[], entry_points=[], chains=[],
            degradation_level=DegradationLevel.MINIMAL,
        )
    monkeypatch.setattr(activities, "build_code_index_with_gitnexus", fake_build)

    # write_index_files 抛非配置异常
    async def failing_write(index, out_dir):
        raise OSError("disk full")
    monkeypatch.setattr(activities, "write_index_files", failing_write)

    class FakeGit:
        @staticmethod
        async def commit_index(d):
            return None
    monkeypatch.setattr(activities, "GitManager", FakeGit)

    class FakeSession:
        def track_step(self, *a, **kw):
            class CM:
                async def __aenter__(self):
                    return self
                async def __aexit__(self, *a):
                    return False
            return CM()
    monkeypatch.setattr(
        "shannon_whitebox.audit.session_registry.get_audit_session",
        lambda: FakeSession(),
    )

    from temporalio import activity
    monkeypatch.setattr(activity, "info", lambda: type("I", (), {"attempt": 1})())

    # 不应 raise;返回空产物 dict
    result = await activities.run_code_index(_make_input(tmp_path))
    assert result["total_blocks"] == 0
    assert result["total_entry_points"] == 0
    assert result["degraded"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_run_code_index_degradation.py::test_write_index_failure_does_not_fail_pipeline -v`
Expected: FAIL — `failing_write` 抛 `OSError` → 当前 `except Exception`（L311-313）raise `ApplicationFailure`；测试收到 `ApplicationFailure` 而非降级 dict。且 `result["degraded"]` 键当前不存在。

- [ ] **Step 3: Change the outer except to degrade (non-PentestError)**

Edit `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:308-313`。替换最外层 `except` 块：

```python
    except PentestError as e:
        # 配置/仓库真错误(如 REPO_NOT_FOUND)仍 raise——那是 preflight 职责,该停。
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        # Spec §9.5/§10: GitNexus 索引/落盘/提交的任何非配置异常不 fail-fast。
        # 降级为空产物(GitNexus 轨空),pipeline 继续跑 LLM 轨。
        import logging
        logging.getLogger(__name__).warning(
            "run_code_index degraded (non-config error: %s); emitting empty index", e,
        )
        return {
            "total_blocks": 0,
            "total_entry_points": 0,
            "total_chains": 0,
            "json_path": "",
            "summary_path": "",
            "degraded": True,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_run_code_index_degradation.py::test_write_index_failure_does_not_fail_pipeline -v`
Expected: PASS — `OSError` 被最外层捕获，返回降级 dict（`degraded=True`），不 raise。

- [ ] **Step 5: Run the full degradation + related suite to verify no regression**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_run_code_index_degradation.py packages/whitebox/tests/test_workflows.py -v`
Expected: PASS（4 degradation tests + 现有 workflow 测试不回归）

- [ ] **Step 6: Commit**

```bash
cd /root/shannon-py
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/tests/test_run_code_index_degradation.py
git commit -m "fix(whitebox): run_code_index non-config errors degrade instead of fail-fast (§9.5)"
```

> **手动冒烟（本 plan 外）**：在真实慢仓库（如 `/root/code/backend/question`,memory 记录的 651 文件仓库）跑一次白盒扫描。期望：pre-recon agent 正常产生 LLM 成本（不再被饿死到零）；`run_code_index` 在 `GITNEXUS_INDEX_TIMEOUT=120s` 或 `GITNEXUS_BUILD_TIMEOUT=180s` 内降级；`parameter_graph.json` 缺失；pipeline 跑通（LLM 轨产物齐全）。这是唯一单元测试无法覆盖的真实慢索引路径。

---

## Self-Review

**1. Spec coverage**（对照 spec §6 索引可靠性 + §9 验收 5 + §10 风险）：
- §6「索引超时/失败时 GitNexus 轨优雅降级为空,不拖死 pipeline」→ Task 1（`ensure_indexed` 超时降级）+ Task 2（build 超时降级）+ Task 4（顶层兜底不 fail-fast）✓
- §6「stop() 超时兜底已修」→ 已修（`gitnexus_mcp.py:64-80`,`MCP_STOP_TIMEOUT=5`），本 plan 不重复 ✓
- §6「上游同步阻塞待修」→ Task 1 `asyncio.to_thread`（解 `ensure_indexed` 同步 `subprocess.run` 阻塞）+ Task 2（覆盖 `build` 内 `parse_file` 同步阻塞，虽未直接 to_thread 但 wait_for 截断）✓ —— memory 记录的「上游同步阻塞」具体指 `gitnexus_engine._run_cli` 的同步 `subprocess.run`（L137）+ `code_index/__init__.py:137 parser.parse_file` 同步 tree-sitter，两者阻塞 event loop 使并发 pre-recon agent 饿死
- §9 验收 5「GitNexus 索引失败时优雅降级,不拖死 pipeline」→ Task 1/2/3/4 ✓
- §10「GitNexus 索引超时拖死 pipeline → 优雅降级为空」→ Task 1/2/4 ✓
- §6「`.gitnexus` 目录存在即当成功无完整性校验」→ **不在本 plan**（治本另做，memory 标注③）；本 plan 只保证「索引慢/失败时降级」，不校验索引完整性
- §6「② GitNexus 索引/查询对该仓库慢」(治本)→ **不在本 plan**（索引速度优化另做）；本 plan 用超时兜住慢，不优化慢本身
- LLM 轨照常跑 → 隐含保证：降级只影响 GitNexus 轨产物（`parameter_graph.json` 缺失、`code_index.json` MINIMAL），LLM 轨（pre-recon/vuln agent）不受 `run_code_index` 降级影响 ✓

**2. Placeholder scan**：无 TBD/TODO。Task 3 注明 `ActivityInput` 字段「以实际定义为准并先 grep 确认」——诚实标注动态类型风险，非占位符。Task 4 决策点（`PentestError` 是否 raise）明确给出默认选择 + 备选,非占位。

**3. Type consistency**：
- `GITNEXUS_INDEX_TIMEOUT` / `GITNEXUS_BUILD_TIMEOUT`（模块级 `int`）在 Task 1/2 定义、测试用 `monkeypatch.setattr` 覆盖,一致
- `_StubMCPClient`（`activities.py:225`,`call_tool` 返回 None）在 Task 1/2 降级路径复用,一致
- 降级返回 dict 键（`total_blocks/total_entry_points/total_chains/json_path/summary_path/degraded`）在 Task 4 定义;Task 1/2/3 的成功路径返回原 5 键 dict（`activities.py:301-307`），`degraded` 仅 Task 4 兜底出现——下游读 `total_*` 不受影响（`degraded` 是新增键,向后兼容）
- `CodeIndex(degradation_level=DegradationLevel.MINIMAL, parameter_graph=None)` 在 Task 2 minimal timeout 兜底 + Task 3 验证一致;`write_index_files`（`parameter_graph is None` 时不写文件）在 Task 3 用真实实现验证

**决策点（需人确认,已在 Task 4 标注）**：
- `PentestError`（配置/仓库错误）是否仍 raise?**默认：是**（REPO_NOT_FOUND 等是真错误,该停;且 preflight 已先跑过同类校验）。备选：任何异常都不 raise（更保守,但掩盖配置错误）。当前 plan 选默认。

**已知缺口（诚实）**：
- Task 1/2 的 `to_thread`/`wait_for` 超时窗口（120s/180s）是经验值;真实仓库（如 memory 的 651 文件仓库）的最优阈值需手动冒烟调参。本 plan 给默认值,可经环境变量/配置化（本期不做,留硬编码）。
- Task 1 `to_thread` 让 `wait_for` 可标记取消,但底层同步 `subprocess.run`（`gitnexus_engine.py:137`,`self.timeout=300`）仍在其线程内跑到自然超时——线程不会被强杀（Python 线程无法强制终止）。这意味着降级后该线程仍占资源直到 `subprocess` 自然结束或进程退出。**这是已知限制**,但不再阻塞 event loop（主目的达成）。彻底解需改 `gitnexus_engine` 用 `asyncio.create_subprocess_exec`（治本另做,本 plan 范围外）。
- 真实 >10min 慢索引的端到端降级验证需真实仓库 + GitNexus CLI 环境,单元测试用 mock 压缩超时窗口覆盖语义;真实流转由 Task 末手动冒烟验证（spec 已注明端到端冒烟待人工）。
