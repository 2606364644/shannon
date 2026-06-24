# 移除 minimal AST-only mode 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 去掉 `_build_code_index_fallback` 降级路径,GitNexus CLI 不可用 / 索引失败 / MCP 查询失败时 `run_code_index` 直接硬失败(raise),不再产出 minimal AST-only 索引。

**Architecture:** 改两个层面——core 层 `build_code_index_with_gitnexus` 的 `auto_index` 分支从 fallback 改 raise;activity 层 `run_code_index` 删 minimal/stub fallback 改显式 raise。删除孤儿函数 `_build_code_index_fallback` 与 `_StubMCPClient`。

**Tech Stack:** Python 3.12, pytest + pytest-asyncio (mode=AUTO), temporalio, unittest.mock

**Spec:** `docs/superpowers/specs/2026-06-24-remove-minimal-fallback-hard-fail-design.md`

## Global Constraints

- GitNexus CLI 不可用 / `ensure_indexed()` 失败 / MCP 查询失败 → 一律 `raise PentestError(category="code_index", error_code=ErrorCode.CODE_INDEX_FAILED)`(activity 层被现有 try/except 包成 `ApplicationFailure`),**绝不降级**。
- 所有硬失败错误消息须含 "GitNexus" 字样,指引安装/排查。
- 不做 LLM 独立兜底重构(Out of Scope,见 spec)。
- 每个任务结束 commit;TDD red→green;清理类任务先 grep 全仓确认无引用。
- 测试命令统一用 `uv run pytest ...`;跑全套用 `packages/core/tests/code_index/`(约 415 个,须全绿)。

---

### Task 1: build_code_index GitNexus CLI 不可用 → 硬失败

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/__init__.py:88-95`(auto_index 分支的 is_available 检查)
- Test: `packages/core/tests/code_index/test_gitnexus_call_graph.py:243-279`(`TestPipelineAutoIndexing.test_auto_index_before_mcp`)

**Interfaces:**
- Consumes: `GitNexusEngine.is_available()`(`shannon_core.code_index.gitnexus_engine`);`PentestError`/`ErrorCode`(函数内 line 79 已 import)
- Produces: `build_code_index_with_gitnexus(auto_index=True)` 在 GitNexus CLI 不可用时 raise `PentestError`(消息含 "GitNexus")

- [ ] **Step 1: 改测试表达新行为(不可用 → raise,而非 fallback)**

把 `test_gitnexus_call_graph.py` 的 `TestPipelineAutoIndexing` 类(line 243-279)整段替换为:

```python
class TestPipelineAutoIndexing:
    @pytest.mark.asyncio
    async def test_unavailable_gitnexus_raises(self, tmp_path):
        """GitNexus CLI 不可用时,build_code_index_with_gitnexus 必须硬失败,
        不再降级到 minimal AST-only mode。"""
        from shannon_core.code_index import build_code_index_with_gitnexus
        from shannon_core.models.errors import PentestError

        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "app.py").write_text("def handler(): pass\n")

        with patch("shannon_core.code_index.gitnexus_engine.GitNexusEngine.is_available", return_value=False):
            mcp = FakeImpactMCPClient(responses={})
            with pytest.raises(PentestError, match="GitNexus"):
                await build_code_index_with_gitnexus(
                    str(tmp_path),
                    mcp_client=mcp,
                    llm_client=AsyncMock(return_value="{}"),
                    auto_index=True,
                )
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest packages/core/tests/code_index/test_gitnexus_call_graph.py::TestPipelineAutoIndexing -x`
Expected: FAIL —— `pytest.raises` 报 "DID NOT RAISE"(当前实现 fallback 返回 MINIMAL 索引,不 raise)

- [ ] **Step 3: 改实现:is_available=False → raise**

`code_index/__init__.py` 把 auto_index 分支(line 88-95):

```python
        if not engine.is_available():
            logger.warning(
                "GitNexus CLI not installed. Falling back to minimal AST-only mode. "
                "Install with: npm install -g gitnexus"
            )
            return await _build_code_index_fallback(
                str(repo), mcp_client=mcp_client, llm_client=llm_client,
            )
```

替换为:

```python
        if not engine.is_available():
            raise PentestError(
                "GitNexus CLI not installed but is required for code indexing. "
                "Install with: npm install -g gitnexus",
                category="code_index", error_code=ErrorCode.CODE_INDEX_FAILED,
            )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest packages/core/tests/code_index/test_gitnexus_call_graph.py::TestPipelineAutoIndexing -x`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/__init__.py packages/core/tests/code_index/test_gitnexus_call_graph.py
git commit -m "feat(code_index): GitNexus CLI 不可用时硬失败(不再 minimal fallback)"
```

---

### Task 2: build_code_index 索引失败 → 硬失败

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/__init__.py:96-104`(`ensure_indexed` 失败检查)
- Test: `packages/core/tests/code_index/test_gitnexus_call_graph.py`(TestPipelineAutoIndexing 新增)

**Interfaces:**
- Produces: `build_code_index_with_gitnexus(auto_index=True)` 在 `ensure_indexed()` 返回 `success=False` 时 raise `PentestError`(消息含错误原因 + "GitNexus")

- [ ] **Step 1: 写失败测试(索引失败 → raise)**

在 test_gitnexus_call_graph.py 顶部 import 行(line 3)补 `MagicMock`:

```python
from unittest.mock import AsyncMock, MagicMock, patch
```

在 `TestPipelineAutoIndexing` 类(Task1 改的 `test_unavailable_gitnexus_raises` 之后)新增:

```python
    @pytest.mark.asyncio
    async def test_indexing_failure_raises(self, tmp_path):
        """ensure_indexed() 失败时,build_code_index_with_gitnexus 必须硬失败。"""
        from shannon_core.code_index import build_code_index_with_gitnexus
        from shannon_core.models.errors import PentestError

        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "app.py").write_text("def handler(): pass\n")

        with patch("shannon_core.code_index.gitnexus_engine.GitNexusEngine") as mock_engine_cls:
            mock_engine = MagicMock()
            mock_engine.is_available.return_value = True
            mock_engine.ensure_indexed.return_value = MagicMock(
                success=False, error_message="boom"
            )
            mock_engine_cls.return_value = mock_engine
            mcp = FakeImpactMCPClient(responses={})
            with pytest.raises(PentestError, match="GitNexus"):
                await build_code_index_with_gitnexus(
                    str(tmp_path),
                    mcp_client=mcp,
                    llm_client=AsyncMock(return_value="{}"),
                    auto_index=True,
                )
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest packages/core/tests/code_index/test_gitnexus_call_graph.py::TestPipelineAutoIndexing::test_indexing_failure_raises -x`
Expected: FAIL —— DID NOT RAISE(当前 fallback)

- [ ] **Step 3: 改实现:ensure_indexed 失败 → raise**

`code_index/__init__.py` 把(line 96-104):

```python
        index_result = engine.ensure_indexed()
        if not index_result.success:
            logger.warning(
                "GitNexus indexing failed: %s. Falling back to minimal mode.",
                index_result.error_message,
            )
            return await _build_code_index_fallback(
                str(repo), mcp_client=mcp_client, llm_client=llm_client,
            )
```

替换为:

```python
        index_result = engine.ensure_indexed()
        if not index_result.success:
            raise PentestError(
                f"GitNexus indexing failed: {index_result.error_message}. "
                "Code index requires a working GitNexus index.",
                category="code_index", error_code=ErrorCode.CODE_INDEX_FAILED,
            )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest packages/core/tests/code_index/test_gitnexus_call_graph.py::TestPipelineAutoIndexing -x`
Expected: PASS(两个测试均过)

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/__init__.py packages/core/tests/code_index/test_gitnexus_call_graph.py
git commit -m "feat(code_index): GitNexus 索引失败时硬失败"
```

---

### Task 3: 删除 `_build_code_index_fallback`(孤儿清理)

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/__init__.py:230-296`(删除整个函数)

**Interfaces:**
- 删除后:全仓无任何对 `_build_code_index_fallback` 的引用

- [ ] **Step 1: grep 确认无其它引用**

Run: `grep -rn "_build_code_index_fallback" packages --include=*.py`
Expected: 仅 `code_index/__init__.py:230` 的定义本身(Task1/2 已删除两处 `return` 调用)。若仍有 src/test 引用,先处理。

- [ ] **Step 2: 删除函数**

`code_index/__init__.py` 删除 `_build_code_index_fallback` 整个函数(line 230-296:从 `async def _build_code_index_fallback(` 到对应 `return CodeIndex(...)` 结束 + 紧随的一个空行)。

- [ ] **Step 3: 验证无残留 + 全套绿**

Run: `uv run python -c "from shannon_core.code_index import build_code_index_with_gitnexus; print('import OK')"`
Expected: `import OK`

Run: `uv run pytest packages/core/tests/code_index/ 2>&1 | tail -5`
Expected: 全 passed(约 415 个;无 NameError/ImportError)

- [ ] **Step 4: Commit**

```bash
git add packages/core/src/shannon_core/code_index/__init__.py
git commit -m "refactor(code_index): 删除孤儿 _build_code_index_fallback"
```

---

### Task 4: run_code_index 硬失败(删 minimal/stub fallback)

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:256-292`(run_code_index 的 GitNexus 分支)
- Test(Create): `packages/whitebox/tests/test_run_code_index.py`

**Interfaces:**
- Consumes: `GitNexusEngine`(函数内 line 256 import)、`GitNexusMCPClient`(line 237)、`get_audit_session`(line 233 函数内 import)、`PentestError`/`ErrorCode`(模块级 line 8 import)
- Produces: `run_code_index` 在 GitNexus CLI 不可用 / 索引失败 / MCP 查询失败时 raise `ApplicationFailure`(由函数末尾 try/except 把 `PentestError` 包装而成),消息含 "GitNexus"

- [ ] **Step 1: 写失败测试(GitNexus 不可用 → activity 失败)**

新建 `packages/whitebox/tests/test_run_code_index.py`:

```python
"""run_code_index activity: GitNexus 不可用必须硬失败(不降级 minimal)。"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from temporalio.exceptions import ApplicationError as ApplicationFailure

from shannon_whitebox.pipeline.activities import run_code_index
from shannon_whitebox.pipeline.shared import ActivityInput


@pytest.mark.asyncio
async def test_run_code_index_raises_when_gitnexus_unavailable(tmp_path):
    """GitNexus CLI 不可用 → run_code_index 抛 ApplicationFailure,不再降级。"""
    input = ActivityInput(repo_path=str(tmp_path), workspace_name="test")

    with patch("shannon_whitebox.audit.session_registry.get_audit_session") as mock_sess, \
         patch("shannon_core.code_index.gitnexus_engine.GitNexusEngine") as mock_engine_cls, \
         patch("shannon_whitebox.pipeline.activities._get_paths") as mock_paths:
        # track_step 是 async context manager
        cm = mock_sess.return_value.track_step.return_value
        cm.__aenter__ = AsyncMock()
        cm.__aexit__ = AsyncMock()
        mock_engine = MagicMock()
        mock_engine.is_available.return_value = False
        mock_engine_cls.return_value = mock_engine
        mock_paths.return_value = (tmp_path, tmp_path / "deliverables", tmp_path)

        with pytest.raises(ApplicationFailure, match="GitNexus"):
            await run_code_index(input)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest packages/whitebox/tests/test_run_code_index.py -x`
Expected: FAIL —— DID NOT RAISE(当前 run_code_index 在 `is_available=False` 时走 else minimal 分支,不 raise)

- [ ] **Step 3: 改实现:三处 fallback → 显式 raise**

`activities.py` 把 run_code_index 的 GitNexus 分支(line 256-292):

```python
            engine = GitNexusEngine(Path(repo))
            indexed = False
            if engine.is_available():
                result = engine.ensure_indexed()
                indexed = result.success
                if not indexed:
                    logger.warning("GitNexus indexing failed: %s", result.error_message)

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
                # GitNexus CLI missing or indexing failed — minimal AST-only mode
                index = await build_code_index_with_gitnexus(
                    str(repo),
                    mcp_client=_StubMCPClient(),
                    llm_client=_llm_taint_client,
                    auto_index=True,
                )
```

替换为:

```python
            engine = GitNexusEngine(Path(repo))
            if not engine.is_available():
                raise PentestError(
                    "GitNexus CLI not available, cannot build code index. "
                    "Install with: npm install -g gitnexus",
                    category="code_index", error_code=ErrorCode.CODE_INDEX_FAILED,
                )
            result = engine.ensure_indexed()
            if not result.success:
                raise PentestError(
                    f"GitNexus indexing failed: {result.error_message}. "
                    "Code index requires a working GitNexus index.",
                    category="code_index", error_code=ErrorCode.CODE_INDEX_FAILED,
                )

            try:
                async with GitNexusMCPClient(Path(repo)) as mcp:
                    index = await build_code_index_with_gitnexus(
                        str(repo),
                        mcp_client=mcp,
                        llm_client=_llm_taint_client,
                        auto_index=False,
                    )
            except PentestError:
                raise
            except Exception as exc:
                raise PentestError(
                    f"GitNexus MCP query failed: {exc}. "
                    "Code index requires a working GitNexus MCP connection.",
                    category="code_index", error_code=ErrorCode.CODE_INDEX_FAILED,
                ) from exc
```

(PentestError、ErrorCode 已在 activities.py line 8 模块级 import。)

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest packages/whitebox/tests/test_run_code_index.py -x`
Expected: PASS

- [ ] **Step 5: 跑现有 whitebox 测试确认无回归**

Run: `uv run pytest packages/whitebox/tests/test_phase_steps.py packages/whitebox/tests/test_run_code_index.py 2>&1 | tail -5`
Expected: 全 passed

- [ ] **Step 6: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/tests/test_run_code_index.py
git commit -m "feat(whitebox): run_code_index GitNexus 不可用/失败时硬失败"
```

---

### Task 5: 删除 `_StubMCPClient`(孤儿清理)

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:225-228`(删除 `_StubMCPClient` 类)

**Interfaces:**
- 删除后:全仓无对 `_StubMCPClient` 的引用

- [ ] **Step 1: grep 确认无引用**

Run: `grep -rn "_StubMCPClient" packages --include=*.py`
Expected: 仅 `activities.py:225` 的定义(Task4 已删除两处调用)。无其它引用。

- [ ] **Step 2: 删除类**

`activities.py` 删除 `_StubMCPClient` 类(line 225-228):

```python
class _StubMCPClient:
    """Fallback MCP client that returns None, triggering degradation."""
    async def call_tool(self, tool_name: str, arguments: dict):
        return None
```

(连同其上方/下方的分隔空行一并清理,保持文件格式。)

- [ ] **Step 3: 验证 import + 测试绿**

Run: `uv run python -c "from shannon_whitebox.pipeline import activities; print('import OK')"`
Expected: `import OK`

Run: `uv run pytest packages/core/tests/code_index/ packages/whitebox/tests/test_run_code_index.py packages/whitebox/tests/test_phase_steps.py 2>&1 | tail -5`
Expected: 全 passed

- [ ] **Step 4: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py
git commit -m "refactor(whitebox): 删除孤儿 _StubMCPClient"
```

---

## Self-Review

**Spec coverage:**
- spec 改动①(`build_code_index_with_gitnexus` auto_index 分支 raise)→ Task1 + Task2 ✓
- spec 改动②(删 `_build_code_index_fallback`)→ Task3 ✓
- spec 改动③(`run_code_index` 三处硬失败)→ Task4 ✓
- spec 改动④(删 `_StubMCPClient`)→ Task5 ✓
- spec 细节1(MCP 查询失败也硬失败)→ Task4 Step3 的 `except Exception → raise PentestError` ✓
- spec 细节2(两孤儿直接删除)→ Task3 + Task5 ✓
- 测试更新 → Task1(改 test_auto_index_before_mcp)、Task2(新增)、Task4(新建 test_run_code_index)✓

**Placeholder scan:** 无 TBD/TODO/"add error handling" 等;每个代码步骤含完整代码块。✓

**Type consistency:**
- `PentestError(category="code_index", error_code=ErrorCode.CODE_INDEX_FAILED)` 签名在 Task1/2/4 一致 ✓
- `ErrorCode.CODE_INDEX_FAILED` 存在(code_index/__init__.py:111 已用)✓
- `ApplicationFailure` = `temporalio.exceptions.ApplicationError`(activities.py line 5 已 alias)✓
- `GitNexusEngine` patch 路径用源模块 `shannon_core.code_index.gitnexus_engine.GitNexusEngine`(函数内 import)✓
- `get_audit_session` patch 路径用 `shannon_whitebox.audit.session_registry.get_audit_session`(line 233 函数内 import)✓

**风险提示(实现时留意):**
- Task4 测试 mock `track_step` 的 async cm;若 `get_audit_session` 的真实返回链路与 mock 不符,可能需调整 `cm = mock_sess.return_value.track_step.return_value`。实现时若 FAIL 原因是 mock 结构而非 DID NOT RAISE,先核对 `track_step` 调用链。
- Task1 改测试后,原 `test_auto_index_before_mcp` 名字改为 `test_unavailable_gitnexus_raises`;确认无其它地方引用旧测试名(一般无)。
