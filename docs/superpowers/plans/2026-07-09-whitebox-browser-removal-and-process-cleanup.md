# 白盒去 browser + browser 进程生命周期清理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** (A) 给 browser 进程加生命周期清理,覆盖正常结束 / ctrl+c 协作取消 / ctrl+c 强退三条退出路径,根除「扫描关掉后残留 agent-browser + Chrome 孤儿进程」;(B) 白盒去除全部 browser 依赖回归纯静态(在线目标运行时验证归黑盒)。全程 TDD,两 phase 独立可提交。

**Architecture:** Phase 1 在 `BrowserEngine` Protocol 新增 `cleanup_processes()` 方法(两引擎各实现),挂三条退出路径(workflow finally / `_do_cancel` 超时 / `ShutdownController._force_exit` 的 `os._exit` 前同步清理--后者是覆盖 ctrl+c 强退残留的关键,因 `os._exit` 跳过 Python finally)。Phase 2 移除白盒 7 个 browser 接触点,仅动白盒专用模板,黑盒专用模板(`*-exploit.txt` / `recon-blackbox.txt` / `validate-authentication.txt`)绝不动。

**Tech Stack:** Python 3.12, temporalio, pytest, pytest-asyncio;双引擎(claude-agent-sdk / openai-agents)经 `BrowserEngine` 协议统一。测试参照既有 `test_agent_browser_engine.py` / `test_browser_engine.py` / `test_scan_runner.py` / `test_workflows.py` 风格。

## Global Constraints

- **TDD 铁律**:每 task 先写失败测试、运行验证 FAIL、再实现到 PASS、再 commit。绝不跳过「验证 FAIL」步。
- **Phase 2 边界铁律**:`*-exploit.txt` / `recon-blackbox.txt` / `validate-authentication.txt` / `shared/_shared-session.txt` 文件本身**绝不动**(黑盒专用 / 黑白共用)。Phase 2 只改白盒专用模板:`recon.txt` + `vuln-{auth,authz,injection,ssrf,xss}.txt`(5 个)。见 spec §4.2.0 归属表。
- **cleanup_processes 永不抛**:全程 best-effort,每步 try/except,失败填 `errors` 返回,**绝不 raise**(清理不能反过来崩扫描/阻塞退出)。
- **`_force_exit` 路径同步约束**:该路径在 `os._exit` 前,不能 await--`cleanup_processes` 内部用同步 `subprocess.run`(短 timeout),不用 asyncio。
- **测试不跑全套**:本 repo 全套 pytest 有预存挂起/失败(见 `feat-fork-py-test-gotchas` 记忆)。**只跑本 task 改动相关的测试文件**,命令里给精确路径。
- **提交粒度**:每 task 一次 commit,commit message 用 `feat:` / `refactor:` / `test:` 前缀。
- **分支**:`feat/fork-py`(本地多项未 push;spec 已 commit `bbc85ba9`)。
- **规格出处**:spec `docs/superpowers/specs/2026-07-09-whitebox-browser-removal-and-process-cleanup-design.md`。

---

## File Structure

### Phase 1 创建/修改

- **Modify** `packages/core/src/shannon_core/services/browser_engine.py` -- `BrowserEngine` Protocol 新增 `cleanup_processes()` 方法声明。
- **Modify** `packages/core/src/shannon_core/services/engines/agent_browser_engine.py` -- 实现 `cleanup_processes()`(优雅 `agent-browser close` -> pkill 兜底)。
- **Modify** `packages/core/src/shannon_core/services/engines/playwright_engine.py` -- 对称实现 `cleanup_processes()`。
- **Modify** `packages/core/src/shannon_core/runtime/scan_runner.py` -- `_force_exit` 前同步清理 + `_do_cancel` 超时分支补清理;`ShutdownController` 持有 cleanup 回调。
- **Modify** `packages/blackbox/src/shannon_blackbox/pipeline/activities.py` -- `cleanup_engine_configs` activity 扩展调 `cleanup_processes`(进程清理 + config 清理合并)。
- **Modify** `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` -- 新增 `cleanup_browser_processes` activity(白盒 Phase 2 前过渡用;Phase 2 后成 no-op 兜底)。
- **Modify** `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` -- finally 接入 cleanup activity。
- **Modify** `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` -- finally 已调 `cleanup_engine_configs`,无需改(Task 5 验证)。
- **Modify** `packages/whitebox/src/shannon_whitebox/worker.py` + `packages/blackbox/src/shannon_blackbox/worker.py` -- `ctrl.install` 传 cleanup 回调。
- **Test** `packages/core/tests/test_browser_engine.py` / `test_agent_browser_engine.py` / 新增 `test_playwright_engine.py` / `runtime/test_scan_runner.py`。

### Phase 2 修改

- **Modify** `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` -- 删 setup engine 块(`:108-132`)+ finally cleanup(`:588-590`)+ auth validation 编排(`:96`)。
- **Modify** `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` -- 删 `run_auth_validation` activity(`:738`)。
- **Modify** `prompts/vuln-{auth,authz,injection,ssrf,xss}.txt` + `prompts/recon.txt` -- 移除 Browser Automation 行 + `@include(shared/_shared-session.txt)` 行。
- **Test** `packages/whitebox/tests/test_workflows.py` + 新增 `tests/test_whitebox_browser_decoupling.py`(两条铁律测试)。

---

## Phase 1:browser 进程生命周期清理

### Task 1:`BrowserEngine` Protocol 新增 `cleanup_processes` 方法

**Files:**
- Modify: `packages/core/src/shannon_core/services/browser_engine.py:71-84`(在 `cleanup_config` 后、`check_available` 前插入)
- Test: `packages/core/tests/test_browser_engine.py`

**Interfaces:**
- Produces: `BrowserEngine.cleanup_processes(self, source_dir: str | None = None, session_ids: list[str] | None = None) -> dict` -- 协议方法,所有引擎须实现。`_StubEngine` 也需补(否则 Protocol isinstance 检查失败)。

- [ ] **Step 1: 写失败测试**

追加到 `packages/core/tests/test_browser_engine.py` 末尾(在文件最后):

```python
# ---------------------------------------------------------------------------
# cleanup_processes protocol
# ---------------------------------------------------------------------------


class TestCleanupProcessesProtocol:
    def test_protocol_declares_cleanup_processes(self):
        """BrowserEngine Protocol 必须声明 cleanup_processes 方法。"""
        assert hasattr(BrowserEngine, "cleanup_processes")

    def test_stub_engine_without_cleanup_processes_fails_protocol(self):
        """_StubEngine 未实现 cleanup_processes 时不应满足 Protocol。"""
        # _StubEngine 当前没有 cleanup_processes -> isinstance 应为 False
        # (runtime_checkable Protocol 只检查方法存在性)
        from shannon_core.tests._stub_for_protocol import _StubNoCleanup  # noqa
        assert not isinstance(_StubNoCleanup(), BrowserEngine)
```

注意:`_StubNoCleanup` 需一个不实现 cleanup_processes 的 stub。为避免新建独立测试模块,改为内联在测试文件里。修正测试为:

```python
class TestCleanupProcessesProtocol:
    def test_protocol_declares_cleanup_processes(self):
        """BrowserEngine Protocol 必须声明 cleanup_processes 方法。"""
        assert hasattr(BrowserEngine, "cleanup_processes")

    def test_stub_engine_without_cleanup_processes_fails_protocol(self):
        """未实现 cleanup_processes 的对象不满足 BrowserEngine Protocol。"""

        class _StubNoCleanup:
            @property
            def name(self) -> str:
                return "stub"

            @property
            def cli_binary(self) -> str:
                return "stub-cli"

            def session_flag(self, session_id: str) -> str:
                return f"--stub {session_id}"

            def commands_reference(self) -> str:
                return ""

            def auth_save_command(self, session_id: str, path: str) -> str:
                return ""

            def auth_load_command(self, session_id: str, path: str) -> str:
                return ""

            def write_config(self, source_dir, session_id=None) -> dict:
                return {}

            def cleanup_config(self, source_dir, session_id=None) -> None:
                pass

            def check_available(self) -> bool:
                return True
            # 故意不实现 cleanup_processes

        assert not isinstance(_StubNoCleanup(), BrowserEngine)
```

- [ ] **Step 2: 运行验证 FAIL**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_browser_engine.py::TestCleanupProcessesProtocol -v`
Expected: FAIL -- `test_protocol_declares_cleanup_processes` 断言 `hasattr(BrowserEngine, "cleanup_processes")` 为 False。

- [ ] **Step 3: 实现 Protocol 方法**

在 `packages/core/src/shannon_core/services/browser_engine.py` 的 `BrowserEngine` Protocol 中,在 `cleanup_config` 方法后、`check_available` 前插入:

```python
    def cleanup_processes(
        self,
        source_dir: str | None = None,
        session_ids: list[str] | None = None,
    ) -> dict:
        """Best-effort 回收 engine 拉起的浏览器进程。

        优先优雅关闭(engine CLI 的 close 命令),失败/残留再 pkill 兜底。
        清理失败一律 log + 吞(不反过来崩扫描)。

        - session_ids 非空:只清理这些 session(精准隔离,不误杀并发扫描)。
        - session_ids 为 None:清理 source_dir profile 下全部 session
          (_force_exit 强退路径用,粗粒度兜底)。

        返回 ``{"closed": [...], "killed": [...], "errors": [...]}`` 摘要。
        """
        ...
```

- [ ] **Step 4: 运行验证 PASS**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_browser_engine.py::TestCleanupProcessesProtocol -v`
Expected: PASS。

但注意:`runtime_checkable` Protocol 的 `isinstance` 只检查方法**存在性**不检查签名。现有 `_StubEngine`(test 文件里那个)没实现 `cleanup_processes`,会导致依赖 `_StubEngine` 满足 Protocol 的既有测试失败。所以同时给 `test_browser_engine.py` 里的 `_StubEngine` 补 `cleanup_processes`:

```python
    def cleanup_processes(self, source_dir=None, session_ids=None) -> dict:
        return {"closed": [], "killed": [], "errors": []}
```

加在 `_StubEngine.check_available` 方法后。

- [ ] **Step 5: 运行整个 test_browser_engine.py 确认无回归**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_browser_engine.py -v`
Expected: 全 PASS。

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/services/browser_engine.py packages/core/tests/test_browser_engine.py
git commit -m "feat(browser): BrowserEngine Protocol 新增 cleanup_processes 方法声明"
```

---

### Task 2:`AgentBrowserEngine.cleanup_processes()` 实现

**Files:**
- Modify: `packages/core/src/shannon_core/services/engines/agent_browser_engine.py`(在 `check_available` 后追加)
- Test: `packages/core/tests/test_agent_browser_engine.py`

**Interfaces:**
- Consumes: Task 1 的 Protocol 声明。
- Produces: `AgentBrowserEngine.cleanup_processes(source_dir=None, session_ids=None) -> dict`,内部用 `subprocess.run`(同步,短 timeout)。优雅 `agent-browser close --session <sid>` / `close --all` -> `pkill -f` 兜底。

- [ ] **Step 1: 写失败测试**

追加到 `packages/core/tests/test_agent_browser_engine.py` 末尾:

```python
# ---------------------------------------------------------------------------
# cleanup_processes
# ---------------------------------------------------------------------------


class TestAgentBrowserEngineCleanupProcesses:
    """cleanup_processes: 优雅 close 先于 pkill 兜底,失败吞掉,session 精准隔离。"""

    def test_returns_summary_dict_shape(self, monkeypatch):
        """返回 dict 含 closed/killed/errors 三键。"""
        engine = AgentBrowserEngine()
        calls = _record_subprocess(monkeypatch, returncodes=[0])
        result = engine.cleanup_processes(session_ids=["s1"])
        assert set(result.keys()) >= {"closed", "killed", "errors"}

    def test_graceful_close_called_before_pkill(self, monkeypatch):
        """对每个 session 先跑 agent-browser close,pkill 仅在 close 失败/残留时兜底。"""
        engine = AgentBrowserEngine()
        cmds = _record_subprocess(monkeypatch, returncodes=[0])
        engine.cleanup_processes(session_ids=["agent1"])
        joined = " ".join(cmds)
        assert "agent-browser close" in joined
        # close 成功(returncode 0)时不应触发 pkill
        assert "pkill" not in joined

    def test_pkill_fallback_when_close_fails(self, monkeypatch):
        """close 返回非零 -> 触发 pkill 兜底,匹配 profile 路径。"""
        engine = AgentBrowserEngine()
        cmds = _record_subprocess(monkeypatch, returncodes=[1])
        engine.cleanup_processes(session_ids=["agent1"])
        joined = " ".join(cmds)
        assert "pkill" in joined
        # 必须带 profile 路径以精准隔离(不误杀并发扫描)
        assert "profiles/agent1" in joined

    def test_none_session_ids_uses_close_all(self, monkeypatch):
        """session_ids=None(强退路径)走 close --all + 粗粒度 pkill。"""
        engine = AgentBrowserEngine()
        cmds = _record_subprocess(monkeypatch, returncodes=[0])
        engine.cleanup_processes(session_ids=None)
        joined = " ".join(cmds)
        assert "close --all" in joined

    def test_errors_swallowed_never_raises(self, monkeypatch):
        """subprocess 抛异常时 cleanup_processes 必须吞掉、填 errors、不 raise。"""
        engine = AgentBrowserEngine()

        def boom(*a, **kw):
            raise FileNotFoundError("agent-browser vanished")

        monkeypatch.setattr("shannon_core.services.engines.agent_browser_engine.subprocess.run", boom)
        result = engine.cleanup_processes(session_ids=["agent1"])
        assert result["errors"]  # 非空错误列表
        # 不抛异常即通过

    def test_only_targeted_sessions_not_others(self, monkeypatch):
        """session_ids=['agent1'] 时 pkill 匹配串不含 agent2。"""
        engine = AgentBrowserEngine()
        cmds = _record_subprocess(monkeypatch, returncodes=[1])
        engine.cleanup_processes(session_ids=["agent1"])
        joined = " ".join(cmds)
        assert "profiles/agent1" in joined
        assert "profiles/agent2" not in joined


# helper: 记录 subprocess.run 收到的命令,可控 returncode
def _record_subprocess(monkeypatch, returncodes):
    cmds = []
    from shannon_core.services.engines import agent_browser_engine as mod

    class _R:
        def __init__(self, rc):
            self.returncode = rc

    it = iter(returncodes)

    def fake_run(cmd, *a, **kw):
        cmds.append(" ".join(str(c) for c in cmd))
        try:
            rc = next(it)
        except StopIteration:
            rc = 0
        return _R(rc)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    return cmds
```

- [ ] **Step 2: 运行验证 FAIL**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_agent_browser_engine.py::TestAgentBrowserEngineCleanupProcesses -v`
Expected: FAIL -- `AttributeError: 'AgentBrowserEngine' object has no attribute 'cleanup_processes'`。

- [ ] **Step 3: 实现 cleanup_processes**

在 `packages/core/src/shannon_core/services/engines/agent_browser_engine.py`:
(a) 顶部 import 区补 `import subprocess`(在现有 `import shutil` 后加一行 `import subprocess`)。
(b) 在 `check_available` 方法后追加:

```python
    # -- Process lifecycle ---------------------------------------------------

    def cleanup_processes(
        self,
        source_dir: str | None = None,
        session_ids: list[str] | None = None,
    ) -> dict:
        """Best-effort 回收 agent-browser + Chrome 子进程。

        优先优雅 ``agent-browser close``(按 session / --all),失败/残留再
        ``pkill -f`` 兜底(匹配 profile 路径以精准隔离,不误杀并发扫描)。
        全程 try/except,绝不 raise(清理不能反过来崩扫描/阻塞退出)。
        """
        import logging

        log = logging.getLogger(__name__)
        closed: list[str] = []
        killed: list[str] = []
        errors: list[str] = []

        def _run(cmd: list[str], timeout: float = 5.0) -> int:
            """同步跑命令,返回 returncode;异常吞掉填 errors。"""
            try:
                proc = subprocess.run(
                    cmd,
                    timeout=timeout,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return proc.returncode
            except Exception as exc:  # noqa: BLE001 - best-effort,绝不抛
                errors.append(f"{' '.join(cmd)}: {exc}")
                return -1

        targets = session_ids if session_ids is not None else [None]

        for sid in targets:
            # 1. 优雅 close
            if sid is None:
                close_cmd = ["agent-browser", "close", "--all"]
                close_tag = "all"
            else:
                close_cmd = ["agent-browser", "--session", sid, "close"]
                close_tag = sid
            rc = _run(close_cmd, timeout=5.0)
            if rc == 0:
                closed.append(close_tag)
                continue  # close 成功 -> 不 pkill 该 session

            # 2. pkill 兜底(匹配 profile 路径以精准隔离)
            if sid is None:
                pk_cmd = ["pkill", "-f", "agent-browser"]
            else:
                profile = f".agent-browser/profiles/{sid}"
                pk_cmd = ["pkill", "-f", f"agent-browser.*{profile}"]
            if _run(pk_cmd, timeout=5.0) == 0:
                killed.append(close_tag)
            # Chrome 子进程(headless chrome 带 profile user-data-dir)
            chrome_profile = "agent-browser" if sid is None else f"profiles/{sid}"
            _run(["pkill", "-f", f"headless.*{chrome_profile}"], timeout=5.0)

        log.debug(
            "agent-browser cleanup: closed=%s killed=%s errors=%s",
            closed, killed, errors,
        )
        return {"closed": closed, "killed": killed, "errors": errors}
```

- [ ] **Step 4: 运行验证 PASS**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_agent_browser_engine.py::TestAgentBrowserEngineCleanupProcesses -v`
Expected: 全 PASS。

- [ ] **Step 5: 运行整个 test_agent_browser_engine.py 确认无回归**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_agent_browser_engine.py -v`
Expected: 全 PASS。

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/services/engines/agent_browser_engine.py packages/core/tests/test_agent_browser_engine.py
git commit -m "feat(browser): AgentBrowserEngine.cleanup_processes 优雅 close + pkill 兜底"
```

---

### Task 3:`PlaywrightEngine.cleanup_processes()` 对称实现

**Files:**
- Modify: `packages/core/src/shannon_core/services/engines/playwright_engine.py`(在 `check_available` 后追加)
- Test: `packages/core/tests/test_playwright_engine.py`(新建)

**Interfaces:**
- Consumes: Task 1 的 Protocol 声明。
- Produces: `PlaywrightEngine.cleanup_processes(...)`,对称于 Task 2,用 `playwright-cli` close 语义 + `pkill -f playwright-cli` 兜底。

- [ ] **Step 1: 写失败测试**

创建 `packages/core/tests/test_playwright_engine.py`:

```python
"""Tests for PlaywrightEngine.cleanup_processes."""

from __future__ import annotations

from shannon_core.services.browser_engine import BrowserEngine
from shannon_core.services.engines.playwright_engine import PlaywrightEngine


class TestPlaywrightEngineCleanupProcesses:
    def test_satisfies_protocol(self):
        assert isinstance(PlaywrightEngine(), BrowserEngine)

    def test_returns_summary_dict_shape(self, monkeypatch):
        engine = PlaywrightEngine()
        _record(monkeypatch, returncodes=[0])
        result = engine.cleanup_processes(session_ids=["agent1"])
        assert {"closed", "killed", "errors"} <= set(result.keys())

    def test_graceful_close_before_pkill(self, monkeypatch):
        engine = PlaywrightEngine()
        cmds = _record(monkeypatch, returncodes=[0])
        engine.cleanup_processes(session_ids=["agent1"])
        joined = " ".join(cmds)
        assert "playwright-cli" in joined
        assert "pkill" not in joined  # close 成功不 pkill

    def test_pkill_fallback_when_close_fails(self, monkeypatch):
        engine = PlaywrightEngine()
        cmds = _record(monkeypatch, returncodes=[1])
        engine.cleanup_processes(session_ids=["agent1"])
        joined = " ".join(cmds)
        assert "pkill" in joined

    def test_errors_swallowed(self, monkeypatch):
        engine = PlaywrightEngine()

        def boom(*a, **kw):
            raise FileNotFoundError("no playwright-cli")

        monkeypatch.setattr(
            "shannon_core.services.engines.playwright_engine.subprocess.run", boom
        )
        result = engine.cleanup_processes(session_ids=["agent1"])
        assert result["errors"]


def _record(monkeypatch, returncodes):
    from shannon_core.services.engines import playwright_engine as mod

    cmds = []

    class _R:
        def __init__(self, rc):
            self.returncode = rc

    it = iter(returncodes)

    def fake_run(cmd, *a, **kw):
        cmds.append(" ".join(str(c) for c in cmd))
        try:
            rc = next(it)
        except StopIteration:
            rc = 0
        return _R(rc)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    return cmds
```

- [ ] **Step 2: 运行验证 FAIL**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_playwright_engine.py -v`
Expected: FAIL -- `AttributeError: 'PlaywrightEngine' object has no attribute 'cleanup_processes'`。

- [ ] **Step 3: 实现 cleanup_processes**

在 `packages/core/src/shannon_core/services/engines/playwright_engine.py`:
(a) 顶部 import 区补 `import subprocess`(在 `import shutil` 后)。
(b) 在 `check_available` 方法后追加:

```python
    # -- Process lifecycle ---------------------------------------------------

    def cleanup_processes(
        self,
        source_dir: str | None = None,
        session_ids: list[str] | None = None,
    ) -> dict:
        """Best-effort 回收 playwright-cli + Chrome 子进程。

        对称于 AgentBrowserEngine.cleanup_processes:优雅 close 先于 pkill 兜底,
        全程 try/except 绝不 raise。playwright-cli 无 --all,session_ids=None 时
        直接走粗粒度 pkill(强退路径)。
        """
        import logging

        log = logging.getLogger(__name__)
        closed: list[str] = []
        killed: list[str] = []
        errors: list[str] = []

        def _run(cmd: list[str], timeout: float = 5.0) -> int:
            try:
                proc = subprocess.run(
                    cmd,
                    timeout=timeout,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return proc.returncode
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{' '.join(cmd)}: {exc}")
                return -1

        targets = session_ids if session_ids is not None else [None]

        for sid in targets:
            if sid is not None:
                close_cmd = ["playwright-cli", f"-s={sid}", "close"]
                tag = sid
                rc = _run(close_cmd, timeout=5.0)
                if rc == 0:
                    closed.append(tag)
                    continue
                _run(["pkill", "-f", f"playwright-cli.*{sid}"], timeout=5.0)
                killed.append(tag)
            else:
                # 粗粒度(强退路径)
                _run(["pkill", "-f", "playwright-cli"], timeout=5.0)
                _run(["pkill", "-f", "headless.*playwright"], timeout=5.0)
                killed.append("all")

        log.debug(
            "playwright cleanup: closed=%s killed=%s errors=%s",
            closed, killed, errors,
        )
        return {"closed": closed, "killed": killed, "errors": errors}
```

- [ ] **Step 4: 运行验证 PASS**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_playwright_engine.py -v`
Expected: 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/services/engines/playwright_engine.py packages/core/tests/test_playwright_engine.py
git commit -m "feat(browser): PlaywrightEngine.cleanup_processes 对称实现"
```

---

### Task 4:scan_runner 路径 ③ `_force_exit` 前同步清理

**Files:**
- Modify: `packages/core/src/shannon_core/runtime/scan_runner.py:24-75`(`ShutdownController`)+ `:197-215`(`_do_cancel`)
- Test: `packages/core/tests/runtime/test_scan_runner.py`

**Interfaces:**
- Consumes: Task 2/3 的 `engine.cleanup_processes()`。
- Produces: `ShutdownController.__init__` 新增 `cleanup_callback` 字段;`install(loop, cleanup_callback=None)` 接收;`_force_exit` 在 `os._exit(130)` 前同步调 `cleanup_callback(session_ids=None)`。`_do_cancel` 超时分支调 `cleanup_callback(session_ids=None)`(async 包装)。

- [ ] **Step 1: 写失败测试**

追加到 `packages/core/tests/runtime/test_scan_runner.py` 的 `TestShutdownController` 类后(或新建 `TestShutdownCleanup` 类):

```python
class TestShutdownCleanup:
    """路径③: _force_exit 前 os._exit 必有同步进程清理。"""

    def test_force_exit_calls_cleanup_before_os_exit(self):
        """第 2 次 SIGINT -> _force_exit -> 先调 cleanup_callback 再 os._exit(130)。"""
        ctrl = ShutdownController()
        called = []

        def cleanup(session_ids=None):
            called.append(session_ids)

        ctrl._loop = MagicMock()
        ctrl.install(MagicMock(), cleanup_callback=cleanup)
        ctrl._on_signal(signal.SIGINT)  # 第 1 次: graceful
        with patch("shannon_core.runtime.scan_runner.os._exit") as mock_exit:
            ctrl._on_signal(signal.SIGINT)  # 第 2 次: force
        mock_exit.assert_called_once_with(130)
        assert called == [[None]]  # cleanup 在 os._exit 前被调,session_ids=None

    def test_force_exit_without_callback_still_exits(self):
        """未提供 cleanup_callback 时 _force_exit 仍正常 os._exit(不崩)。"""
        ctrl = ShutdownController()
        ctrl._loop = MagicMock()
        ctrl.install(MagicMock())  # 无 cleanup_callback
        ctrl._on_signal(signal.SIGINT)
        with patch("shannon_core.runtime.scan_runner.os._exit") as mock_exit:
            ctrl._on_signal(signal.SIGINT)
        mock_exit.assert_called_once_with(130)

    def test_cleanup_exception_does_not_block_exit(self):
        """cleanup_callback 抛异常时仍必须 os._exit(清理绝不阻塞退出)。"""
        ctrl = ShutdownController()

        def boom(session_ids=None):
            raise RuntimeError("cleanup blew up")

        ctrl._loop = MagicMock()
        ctrl.install(MagicMock(), cleanup_callback=boom)
        ctrl._on_signal(signal.SIGINT)
        with patch("shannon_core.runtime.scan_runner.os._exit") as mock_exit:
            ctrl._on_signal(signal.SIGINT)
        mock_exit.assert_called_once_with(130)
```

- [ ] **Step 2: 运行验证 FAIL**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/runtime/test_scan_runner.py::TestShutdownCleanup -v`
Expected: FAIL -- `install()` 不接受 `cleanup_callback` 参数(`TypeError`)。

- [ ] **Step 3: 修改 ShutdownController**

在 `packages/core/src/shannon_core/runtime/scan_runner.py` 的 `ShutdownController`:

(a) `__init__` 末尾加字段(在 `self._loop = None` 后):

```python
        self._cleanup_callback = None
```

(b) `install` 改签名 + 存回调:

```python
    def install(
        self,
        loop: asyncio.AbstractEventLoop,
        cleanup_callback: "callable | None" = None,
    ) -> None:
        """在给定 event loop 上注册信号 handler（仅 Unix）。

        cleanup_callback(session_ids=None) 在 _force_exit 的 os._exit 前同步调用,
        用于回收 browser 子进程(强退路径,不能 await)。
        """
        self._loop = loop
        self._cleanup_callback = cleanup_callback
        loop.add_signal_handler(signal.SIGINT, self._on_signal, signal.SIGINT)
        loop.add_signal_handler(signal.SIGTERM, self._on_signal, signal.SIGTERM)
```

(c) `_force_exit` 改为清理在前:

```python
    def _force_exit(self) -> None:
        print()
        print_line("SCAN", "", "强制退出")
        if self._cleanup_callback is not None:
            try:
                self._cleanup_callback(session_ids=None)
            except Exception:  # noqa: BLE001 - 清理绝不阻塞退出
                pass
        os._exit(130)
```

- [ ] **Step 4: 运行验证 PASS**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/runtime/test_scan_runner.py::TestShutdownCleanup -v`
Expected: 全 PASS。

- [ ] **Step 5: 路径 ② `_do_cancel` 超时分支补清理 + 测试**

追加到 `TestShutdownCleanup`:

```python
    async def test_do_cancel_calls_cleanup_on_timeout(self):
        """路径②: _do_cancel grace 超时后调 cleanup_callback。"""
        from shannon_core.runtime.scan_runner import _do_cancel

        called = []

        def cleanup(session_ids=None):
            called.append(session_ids)

        fake_handle = MagicMock()
        fake_handle.cancel = AsyncMock()
        result_task = asyncio.ensure_future(asyncio.sleep(100))  # 永不完成 -> 超时
        try:
            await _do_cancel(
                fake_handle, result_task, cancel_grace_seconds=0.01,
                cleanup_callback=cleanup,
            )
        except Exception:
            pass
        assert called == [[None]]
        result_task.cancel()
```

- [ ] **Step 6: 运行验证 FAIL**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/runtime/test_scan_runner.py::TestShutdownCleanup::test_do_cancel_calls_cleanup_on_timeout -v`
Expected: FAIL -- `_do_cancel()` 不接受 `cleanup_callback` 参数(`TypeError`)。

- [ ] **Step 7: 修改 `_do_cancel`**

在 `packages/core/src/shannon_core/runtime/scan_runner.py` 的 `_do_cancel`:

```python
async def _do_cancel(
    handle,
    result_task,
    cancel_grace_seconds: float,
    cleanup_callback: "callable | None" = None,
) -> None:
    """发协作式 cancel,并在 grace 期内等待结果;超时放弃等待(不 escalate)。

    超时后调 cleanup_callback(session_ids=None) 回收 browser 子进程(路径②)。
    """
    print_line("CANCEL", "", "正在取消 Temporal workflow…")
    try:
        await handle.cancel()
    except Exception as exc:
        print_line("CANCEL", "", f"cancel 请求失败（忽略）: {exc}")
    try:
        await asyncio.wait_for(result_task, timeout=cancel_grace_seconds)
    except asyncio.TimeoutError:
        print_line(
            "CANCEL", "",
            f"{cancel_grace_seconds}s 内 workflow 未响应取消，放弃等待"
            f"（server 端 cancel 仍生效）",
        )
        if cleanup_callback is not None:
            try:
                cleanup_callback(session_ids=None)
            except Exception:  # noqa: BLE001
                pass
    except Exception:
        # result_task 因 cancel 抛出的异常属预期，吞掉
        pass
```

- [ ] **Step 8: 运行验证 PASS**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/runtime/test_scan_runner.py::TestShutdownCleanup -v`
Expected: 全 PASS。

- [ ] **Step 9: 运行整个 test_scan_runner.py 确认无回归**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/runtime/test_scan_runner.py -v`
Expected: 全 PASS(注意既有 `test_second_sigint_force_exits_130` 仍需绿--它用默认 install 无 callback)。

- [ ] **Step 10: Commit**

```bash
git add packages/core/src/shannon_core/runtime/scan_runner.py packages/core/tests/runtime/test_scan_runner.py
git commit -m "feat(scan-runner): _force_exit/_do_cancel 前同步 browser 进程清理(覆盖 ctrl+c 强退)"
```

---

### Task 5:黑盒 `cleanup_engine_configs` activity 扩展进程清理

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/activities.py:607-635`(`cleanup_engine_configs`)
- Test: `packages/blackbox/tests/test_workflows.py`(或新建 `test_blackbox_cleanup.py`)

**Interfaces:**
- Consumes: Task 2/3 的 `cleanup_processes`。
- Produces: `cleanup_engine_configs(repo_path, engine_name)` 现在既删 config 又杀进程(复用现有 activity,黑盒 workflow finally 已调它,无需改 workflow)。

- [ ] **Step 1: 写失败测试**

创建 `packages/blackbox/tests/test_blackbox_cleanup.py`:

```python
"""Blackbox cleanup_engine_configs 同时清理 config 文件 + browser 进程。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestCleanupEngineConfigsAlsoKillsProcesses:
    async def test_cleanup_calls_cleanup_processes(self, monkeypatch):
        """cleanup_engine_configs 应在删 config 后调 engine.cleanup_processes。"""
        from shannon_blackbox.pipeline import activities as act

        calls = {}

        class FakeEngine:
            def cleanup_config(self, source_dir, session_id=None):
                calls.setdefault("cleanup_config", []).append(session_id)

            def cleanup_processes(self, source_dir=None, session_ids=None):
                calls["cleanup_processes"] = (source_dir, session_ids)
                return {"closed": [], "killed": [], "errors": []}

        # patch BrowserEngineFactory.get_engine 返回 FakeEngine
        monkeypatch.setattr(
            "shannon_core.services.browser_engine.BrowserEngineFactory.get_engine",
            lambda name: FakeEngine(),
        )
        # AGENT_SESSION_MAPPING 提供非空 session 集合
        monkeypatch.setattr(
            "shannon_core.services.playwright_config_writer.AGENT_SESSION_MAPPING",
            {"a": "agent1", "b": "agent2"},
        )

        await act.cleanup_engine_configs("/tmp/repo", "agent-browser")

        assert "cleanup_processes" in calls
        src, sids = calls["cleanup_processes"]
        assert src == "/tmp/repo"
        assert set(sids) == {"agent1", "agent2"}
```

- [ ] **Step 2: 运行验证 FAIL**

Run: `cd /root/shannon-py && python -m pytest packages/blackbox/tests/test_blackbox_cleanup.py -v`
Expected: FAIL -- `cleanup_processes` 未被调(当前 activity 只调 `cleanup_config`)。

- [ ] **Step 3: 扩展 cleanup_engine_configs**

修改 `packages/blackbox/src/shannon_blackbox/pipeline/activities.py` 的 `cleanup_engine_configs`(`:607`),在末尾 `engine.cleanup_config(repo_path)` 前加进程清理:

```python
async def cleanup_engine_configs(repo_path: str, engine_name: str) -> None:
    """finally 收尾: 清理各 session 的浏览器 stealth config + 进程。

    与 write_engine_config_for_session 对称--engine_name 由 resolve_blackbox_engine 在
    preflight 解析后经 workflow 透传，engine 对象不可跨 workflow/activity 边界故按 engine_name
    重新 get_engine。best-effort cleanup（write_config 幂等，残留 config 下次覆盖），失败由
    workflow 侧 try/except 吞掉不阻断收尾。session_id 集合取自 AGENT_SESSION_MAPPING（同 worker
    进程，get_session_id 在 workflow 侧填充）。

    进程清理（Phase 1）: 同步 engine.cleanup_processes(repo_path, session_ids) 回收
    agent-browser + Chrome 子进程，best-effort 不抛。
    """
    from shannon_core.services.browser_engine import BrowserEngineFactory
    from shannon_core.services.playwright_config_writer import AGENT_SESSION_MAPPING
    import shannon_core.services.engines  # noqa: F401 – registers engines

    engine = BrowserEngineFactory.get_engine(engine_name)
    session_ids = list(set(AGENT_SESSION_MAPPING.values()))
    # 进程清理先于 config 清理（config 删了 profile 目录不影响杀进程匹配）
    try:
        engine.cleanup_processes(repo_path, session_ids=session_ids)
    except Exception:  # noqa: BLE001 - best-effort
        pass
    for session_id in session_ids:
        engine.cleanup_config(repo_path, session_id=session_id)
    engine.cleanup_config(repo_path)
```

- [ ] **Step 4: 运行验证 PASS**

Run: `cd /root/shannon-py && python -m pytest packages/blackbox/tests/test_blackbox_cleanup.py -v`
Expected: 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/pipeline/activities.py packages/blackbox/tests/test_blackbox_cleanup.py
git commit -m "feat(blackbox): cleanup_engine_configs 扩展回收 browser 进程"
```

---

### Task 6:白盒 workflow finally 接入进程清理 activity

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`(新增 `cleanup_browser_processes` activity)
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py:588-590`(finally 调用)
- Test: `packages/whitebox/tests/test_workflows.py`

**Interfaces:**
- Consumes: Task 2/3 的 `cleanup_processes`。
- Produces: `cleanup_browser_processes(repo_path, engine_name)` activity;白盒 workflow finally 调它。

> **Phase 1 过渡说明**:此 task 给白盒 workflow finally 接入进程清理,让白盒在 Phase 2 之前也受益。Phase 2 的 Task 10 会因白盒移除 engine 而删掉这个 finally 调用(此时 `engine`/`engine_name` 变量不再存在);`cleanup_browser_processes` activity **定义本身保留**,作 no-op 兜底(Task 11 验证:被调时 engine_name 缺失 -> KeyError 被 `except Exception: pass` 吞掉)。这是有意过渡,非重复劳动。

- [ ] **Step 1: 写失败测试**

追加到 `packages/whitebox/tests/test_workflows.py`:

```python
class TestWhiteboxBrowserProcessCleanup:
    """白盒 workflow finally 应调 cleanup_browser_processes activity。"""

    async def test_cleanup_browser_processes_activity_exists(self):
        from shannon_whitebox.pipeline import activities
        assert hasattr(activities, "cleanup_browser_processes")

    async def test_cleanup_browser_processes_calls_engine(self, monkeypatch):
        from shannon_whitebox.pipeline import activities as act

        called = {}

        class FakeEngine:
            def cleanup_processes(self, source_dir=None, session_ids=None):
                called["proc"] = (source_dir, session_ids)
                return {"closed": [], "killed": [], "errors": []}

            def cleanup_config(self, source_dir, session_id=None):
                called.setdefault("cfg", []).append(session_id)

        monkeypatch.setattr(
            "shannon_core.services.browser_engine.BrowserEngineFactory.get_engine",
            lambda name: FakeEngine(),
        )
        await act.cleanup_browser_processes("/tmp/repo", "agent-browser")
        assert "proc" in called
```

- [ ] **Step 2: 运行验证 FAIL**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_workflows.py::TestWhiteboxBrowserProcessCleanup -v`
Expected: FAIL -- `cleanup_browser_processes` 不存在。

- [ ] **Step 3: 新增 activity**

在 `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` 的 `run_auth_validation` activity 附近(或文件末尾 activity 区)追加:

```python
@activity.defn
async def cleanup_browser_processes(repo_path: str, engine_name: str) -> None:
    """finally 收尾: 回收白盒 browser 进程(Phase 1)。

    best-effort: engine 不可用或缺名时 no-op(Phase 2 白盒去 engine 后此 activity
    成 no-op 兜底,保留防御未来回退)。
    """
    try:
        from shannon_core.services.browser_engine import BrowserEngineFactory
        from shannon_core.services.playwright_config_writer import AGENT_SESSION_MAPPING
        import shannon_core.services.engines  # noqa: F401 – registers engines

        engine = BrowserEngineFactory.get_engine(engine_name)
        session_ids = list(set(AGENT_SESSION_MAPPING.values()))
        engine.cleanup_processes(repo_path, session_ids=session_ids)
    except Exception:  # noqa: BLE001 - best-effort
        pass
```

- [ ] **Step 4: 运行验证 PASS**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_workflows.py::TestWhiteboxBrowserProcessCleanup -v`
Expected: 全 PASS。

- [ ] **Step 5: workflow finally 接入**

修改 `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` 的 finally 块(`:588` 附近,`engine.cleanup_config` 旁)。当前:

```python
            engine.cleanup_config(input.repo_path)
        cleanup_auth_state_sync(workspace_path)
```

改为(在 `cleanup_config` 后加进程清理):

```python
            engine.cleanup_config(input.repo_path)
            await workflow.execute_activity(
                activities.cleanup_browser_processes,
                args=[input.repo_path, engine_name],
                start_to_close_timeout=timedelta(seconds=15),
                retry_policy=retry_for("log"),
            )
        cleanup_auth_state_sync(workspace_path)
```

> 注:此 finally 调用在 Phase 2 Task 10 会随白盒移除 engine 而删除(见上述 Phase 1 过渡说明)。

- [ ] **Step 6: 运行白盒 workflow 测试确认无回归**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_workflows.py -v`
Expected: 全 PASS。

- [ ] **Step 7: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/src/shannon_whitebox/pipeline/workflows.py packages/whitebox/tests/test_workflows.py
git commit -m "feat(whitebox): workflow finally 接入 browser 进程清理 activity"
```

---

### Task 7:worker 把 cleanup 回调注入 ShutdownController

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/worker.py:236-237`
- Modify: `packages/blackbox/src/shannon_blackbox/worker.py:133-134`
- Test: `packages/whitebox/tests/test_worker_cleanup_callback.py`(新建)+ `packages/blackbox/tests/test_worker_cleanup_callback.py`(新建)

**Interfaces:**
- Consumes: Task 4 的 `install(loop, cleanup_callback=)`。
- Produces: worker 构造一个同步 cleanup 函数(用当前 engine_name + repo_path 调 `engine.cleanup_processes(session_ids=None)`),传给 `ctrl.install`。

- [ ] **Step 1: 写失败测试**

创建 `packages/whitebox/tests/test_worker_cleanup_callback.py`:

```python
"""worker 把 browser 进程 cleanup 回调注入 ShutdownController。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_worker_install_passes_cleanup_callback():
    """run_scan 应把 cleanup_callback 传给 ctrl.install(经 monkeypatch 验证)。"""
    # 验证 build_cleanup_callback 工具函数存在并调 engine.cleanup_processes
    from shannon_whitebox import worker

    assert hasattr(worker, "build_browser_cleanup_callback")


def test_cleanup_callback_invokes_engine(monkeypatch):
    """build_browser_cleanup_callback(repo_path, engine_name) 返回的 fn 调 cleanup_processes。"""
    from shannon_whitebox import worker

    called = {}

    class FakeEngine:
        def cleanup_processes(self, source_dir=None, session_ids=None):
            called["args"] = (source_dir, session_ids)

    monkeypatch.setattr(
        "shannon_core.services.browser_engine.BrowserEngineFactory.get_engine",
        lambda name: FakeEngine(),
    )
    cb = worker.build_browser_cleanup_callback("/tmp/repo", "agent-browser")
    cb(session_ids=None)
    assert called["args"][1] is None


def test_cleanup_callback_swallows_errors(monkeypatch):
    """engine 不可用时回调不抛(强退路径绝不崩)。"""
    from shannon_whitebox import worker

    def boom(name):
        raise KeyError("no engine")

    monkeypatch.setattr(
        "shannon_core.services.browser_engine.BrowserEngineFactory.get_engine", boom
    )
    cb = worker.build_browser_cleanup_callback("/tmp/repo", "agent-browser")
    cb(session_ids=None)  # 不抛即通过
```

- [ ] **Step 2: 运行验证 FAIL**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_worker_cleanup_callback.py -v`
Expected: FAIL -- `build_browser_cleanup_callback` 不存在。

- [ ] **Step 3: 在 worker 实现 build_browser_cleanup_callback + 接线**

在 `packages/whitebox/src/shannon_whitebox/worker.py`,import 区底部加(若未有):

```python
import shannon_core.services.engines  # noqa: F401 – registers engines
```

在模块级(类/函数外,worker 主函数前)加:

```python
def build_browser_cleanup_callback(repo_path: str, engine_name: str | None):
    """构造一个同步 cleanup 函数,供 ShutdownController 在 os._exit 前调用。

    engine_name 为 None(无 config)时返回 no-op;否则 get_engine + cleanup_processes。
    全程不抛(强退路径绝不崩)。
    """
    if not engine_name:
        return lambda session_ids=None: None

    def _cleanup(session_ids=None) -> None:
        try:
            from shannon_core.services.browser_engine import BrowserEngineFactory

            engine = BrowserEngineFactory.get_engine(engine_name)
            engine.cleanup_processes(repo_path, session_ids=session_ids)
        except Exception:  # noqa: BLE001 - best-effort
            pass

    return _cleanup
```

然后改 `:236-237` 的 `ctrl.install`:

```python
    ctrl = ShutdownController()
    engine_name = _resolve_engine_name(input.config_path)
    ctrl.install(
        asyncio.get_running_loop(),
        cleanup_callback=build_browser_cleanup_callback(input.repo_path, engine_name),
    )
```

并新增 helper(模块级):

```python
def _resolve_engine_name(config_path: str | None) -> str | None:
    """从 config 解析 engine_name(无 config 返回 None)。"""
    if not config_path:
        return None
    try:
        from shannon_core.config.parser import parse_config

        cfg = parse_config(config_path)
        return cfg.browser_engine
    except Exception:  # noqa: BLE001
        return None
```

- [ ] **Step 4: 运行验证 PASS**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_worker_cleanup_callback.py -v`
Expected: 全 PASS。

- [ ] **Step 5: 黑盒对称接线**

对 `packages/blackbox/src/shannon_blackbox/worker.py` 做对称改动:加 `build_browser_cleanup_callback` + `_resolve_engine_name`(同上代码),改 `:133-134` 的 `ctrl.install` 传 `cleanup_callback`。

新建 `packages/blackbox/tests/test_worker_cleanup_callback.py`(内容与白盒对称,import 改 `shannon_blackbox.worker`)。

Run: `cd /root/shannon-py && python -m pytest packages/blackbox/tests/test_worker_cleanup_callback.py -v`
Expected: 全 PASS。

- [ ] **Step 6: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/worker.py packages/whitebox/tests/test_worker_cleanup_callback.py packages/blackbox/src/shannon_blackbox/worker.py packages/blackbox/tests/test_worker_cleanup_callback.py
git commit -m "feat(worker): 注入 browser cleanup 回调到 ShutdownController(强退兜底)"
```

**Phase 1 完成检查点**:运行 Phase 1 全部相关测试:

```bash
cd /root/shannon-py && python -m pytest \
  packages/core/tests/test_browser_engine.py \
  packages/core/tests/test_agent_browser_engine.py \
  packages/core/tests/test_playwright_engine.py \
  packages/core/tests/runtime/test_scan_runner.py \
  packages/blackbox/tests/test_blackbox_cleanup.py \
  packages/blackbox/tests/test_worker_cleanup_callback.py \
  packages/whitebox/tests/test_workflows.py \
  packages/whitebox/tests/test_worker_cleanup_callback.py \
  -v
```
Expected: 全 PASS。

---

## Phase 2:白盒去 browser

### Task 8:铁律测试 1 + 2(先红,定义不变量)

**Files:**
- Test: `packages/whitebox/tests/test_whitebox_browser_decoupling.py`(新建)
- 本 task 仅写测试,不实现(后续 task 改到绿)

**Interfaces:**
- Produces: 两条铁律测试,锁定白盒不注入 browser / 不 resolve engine。后续 task 9-11 使其转绿。

- [ ] **Step 1: 写铁律测试(此时应为 FAIL 或部分 PASS,作为目标)**

创建 `packages/whitebox/tests/test_whitebox_browser_decoupling.py`:

```python
"""白盒 browser 解耦铁律测试(类比 static-dataflow-hints 解耦铁律)。

锁定两条不变量:
1. 白盒专用 prompt 模板不得注入 browser 命令。
2. 白盒 workflow 不得 resolve/check browser engine。
反向断言:黑盒专用模板必须保留 browser 占位符(防回归误删)。
"""

from __future__ import annotations

from pathlib import Path

import pytest

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

# 白盒专用模板(Phase 2 须清干净 browser)
WHITEBOX_TEMPLATES = [
    "recon",
    "vuln-auth",
    "vuln-authz",
    "vuln-injection",
    "vuln-ssrf",
    "vuln-xss",
]

# 黑盒专用 / 黑白共用模板(必须保留 browser 占位符,绝不动)
BLACKBOX_TEMPLATES = [
    "auth-exploit",
    "authz-exploit",
    "injection-exploit",
    "ssrf-exploit",
    "xss-exploit",
    "recon-blackbox",
    "validate-authentication",
]

BROWSER_MARKERS = [
    "{{BROWSER_COMMANDS}}",
    "{{BROWSER_SESSION_FLAG}}",
    "@include(shared/_shared-session.txt)",
]


class TestWhiteboxNoBrowserInPrompts:
    """铁律 1: 白盒专用模板不含任何 browser 占位符/include。"""

    @pytest.mark.parametrize("name", WHITEBOX_TEMPLATES)
    def test_whitebox_template_has_no_browser(self, name):
        content = (PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")
        for marker in BROWSER_MARKERS:
            assert marker not in content, (
                f"白盒模板 {name}.txt 含 browser 标记 {marker}(应已移除)"
            )

    @pytest.mark.parametrize("name", BLACKBOX_TEMPLATES)
    def test_blackbox_template_keeps_browser(self, name):
        """反向断言: 黑盒专用模板仍含 browser 占位符(防回归误删)。"""
        content = (PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")
        # exploit/recon-blackbox 模板应至少含一个 browser 标记
        assert any(m in content for m in BROWSER_MARKERS), (
            f"黑盒模板 {name}.txt 丢失 browser 标记(误删? 黑盒需 browser)"
        )


class TestWhiteboxWorkflowNoBrowserEngine:
    """铁律 2: 白盒 workflow 不 resolve/check/write_config browser engine。"""

    def test_workflow_source_has_no_browser_engine_refs(self):
        wf = (
            Path(__file__).resolve().parents[1]
            / "src/shannon_whitebox/pipeline/workflows.py"
        )
        src = wf.read_text(encoding="utf-8")
        forbidden = [
            "BrowserEngineFactory.get_engine",
            "engine.check_available",
            "engine.write_config",
            "engine.cleanup_config",
        ]
        for token in forbidden:
            assert token not in src, (
                f"白盒 workflow 仍引用 browser engine: {token}"
            )
```

- [ ] **Step 2: 运行验证当前状态(应为 FAIL--Phase 2 尚未做)**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_whitebox_browser_decoupling.py -v`
Expected: FAIL(白盒模板仍含 browser 标记 + workflow 仍引用 engine)。这是预期--后续 task 使其转绿。

- [ ] **Step 3: Commit(测试先于实现,TDD)**

```bash
git add packages/whitebox/tests/test_whitebox_browser_decoupling.py
git commit -m "test(whitebox): browser 解耦铁律测试(先红,锁定白盒去 browser 不变量)"
```

---

### Task 9:白盒 prompt 模板移除 browser 注入 + @include

**Files:**
- Modify: `prompts/vuln-auth.txt:122`
- Modify: `prompts/vuln-authz.txt:23,143`
- Modify: `prompts/vuln-injection.txt:24,108`
- Modify: `prompts/vuln-ssrf.txt:23,95`
- Modify: `prompts/vuln-xss.txt:23,105`
- Modify: `prompts/recon.txt:36,78,136`
- Test: `packages/whitebox/tests/test_whitebox_browser_decoupling.py`(Task 8 的铁律测试 1)

**Interfaces:**
- Consumes: Task 8 铁律测试 1。
- Produces: 白盒专用模板清干净 browser。

- [ ] **Step 1: 移除每个白盒模板的 browser 行 + @include 行**

对每个文件,删除以下两类行:
- `@include(shared/_shared-session.txt)` 整行
- `- **Browser Automation:** {{BROWSER_COMMANDS}} ...` 整行

精确改动:

`prompts/vuln-auth.txt`:删 `:122` 的 Browser Automation 行。
`prompts/vuln-authz.txt`:删 `:23` 的 @include 行 + `:143` 的 Browser Automation 行。
`prompts/vuln-injection.txt`:删 `:24` 的 @include 行 + `:108` 的 Browser Automation 行。
`prompts/vuln-ssrf.txt`:删 `:23` 的 @include 行 + `:95` 的 Browser Automation 行。
`prompts/vuln-xss.txt`:删 `:23` 的 @include 行 + `:105` 的 Browser Automation 行。
`prompts/recon.txt`:删 `:36` 的 @include 行 + `:78` 的 Browser Automation 行 + `:136` 的 `- Use your browser automation tool with ...` 行。

> 注意:删行后行号会变,按**内容**定位删除(用 Edit 工具 old_string 精确匹配整行)。`recon.txt:136` 是带 `{{BROWSER_SESSION_FLAG}}` 的叙述行,也删。

- [ ] **Step 2: 运行铁律测试 1 验证白盒部分转绿**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_whitebox_browser_decoupling.py::TestWhiteboxNoBrowserInPrompts -v`
Expected: 白盒 parametrize 全 PASS;黑盒反向断言全 PASS。

- [ ] **Step 3: 验证黑盒模板未被动到(反向断言)**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_whitebox_browser_decoupling.py::TestWhiteboxNoBrowserInPrompts::test_blackbox_template_keeps_browser -v`
Expected: 全 PASS(确认没误删黑盒模板的 browser)。

- [ ] **Step 4: 运行 prompt_manager 残留占位符检测(确认白盒模板无残留)**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_prompt_manager.py -v`
Expected: 全 PASS(prompt_manager 占位符替换逻辑未改,白盒模板无残留 browser 占位符 -> 不触发 warning,既有测试绿)。

- [ ] **Step 5: Commit**

```bash
git add prompts/vuln-auth.txt prompts/vuln-authz.txt prompts/vuln-injection.txt prompts/vuln-ssrf.txt prompts/vuln-xss.txt prompts/recon.txt
git commit -m "refactor(prompts): 白盒专用模板移除 browser 注入 + _shared-session include"
```

---

### Task 10:白盒 workflow 移除 setup engine 块 + finally cleanup + auth validation 编排

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py:96`(删 auth validation 编排)+ `:108-132`(删 setup engine 块)+ `:588-590`(删 finally cleanup_config)
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:738`(删 `run_auth_validation` activity)
- Test: `packages/whitebox/tests/test_whitebox_browser_decoupling.py`(铁律测试 2)+ `packages/whitebox/tests/test_workflows.py`(更新既有断言)

**Interfaces:**
- Consumes: Task 8 铁律测试 2。
- Produces: 白盒 workflow 不碰 browser engine;`run_auth_validation` activity 删除。

- [ ] **Step 1: 删 auth validation 编排 + setup engine 块**

在 `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`:

(a) 删 auth validation 编排块(`:95-100`,含注释 `# Auth validation` 到 `retry_policy=retry_for("auth-validation"),`)整段。

(b) 删 setup engine 块(`:108-132`,从 `# Resolve browser engine` 注释到 `engine.write_config(input.repo_path)`)整段。注意保留其后的 `# Write code path deny rules (S6)` 块。

(c) finally 块(`:588` 附近):删 Task 6 加的 `cleanup_browser_processes` activity 调用 + 原有 `engine.cleanup_config` 行 + `if engine:` 守卫。保留 `cleanup_auth_state_sync(workspace_path)`(防御残留 auth-state)。改后 finally 仅剩 `cleanup_settings()` + `cleanup_auth_state_sync(...)`。

具体 finally 块改为:

```python
        finally:
            cleanup_settings()
            cleanup_auth_state_sync(workspace_path)
```

> 注意:删 setup engine 块后,`engine` / `engine_name` 变量不再存在,finally 里所有 `engine.*` 引用必须一并删,否则 NameError。Task 6 的 `cleanup_browser_processes` activity 调用也删(Phase 2 白盒无 engine,该 activity 在白盒侧不再被调;activity 定义本身保留作 no-op 兜底--见 Task 11 验证)。

- [ ] **Step 2: 删 run_auth_validation activity**

在 `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` 删 `:738` 的 `run_auth_validation` activity 整个函数(含 `@activity.defn` 装饰器)。

- [ ] **Step 3: 删不再需要的 import**

`workflows.py` 顶部 `with workflow.unsafe.imports_passed_through():` 块内,删 `from shannon_core.services.browser_engine import BrowserEngineFactory` 和 `import shannon_core.services.engines  # noqa: F401` 两行(若仅白盒用)。检查 `BrowserEngineFactory` / `engines` 在 workflow 内是否还有其他引用--若无则删;若 finally 残留引用则保留。同理 `PentestError` 的 `BROWSER_ENGINE_UNAVAILABLE` 用法随 setup 块删除而消失,`PentestError` 本身若其他地方还用则保留。

- [ ] **Step 4: 运行铁律测试 2 验证转绿**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_whitebox_browser_decoupling.py::TestWhiteboxWorkflowNoBrowserEngine -v`
Expected: PASS。

- [ ] **Step 5: 更新既有 test_workflows.py 失败断言**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_workflows.py -v`

预期会有 FAIL:`TestWhiteboxBrowserEngineIntegration` 类(`:96-135`)整块断言白盒 resolve engine--Phase 2 后这些断言不再成立。处理:

- 删除整个 `TestWhiteboxBrowserEngineIntegration` 类(`:93-135`),或改为断言白盒**不** resolve engine(与铁律测试 2 重复则删)。
- 检查其他依赖 `run_auth_validation` / `engine` mock 的测试,移除相关 mock。

逐个修复 FAIL 测试(每个改后重跑确认):

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_workflows.py -v`
Expected: 全 PASS。

- [ ] **Step 6: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/workflows.py packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/tests/test_workflows.py
git commit -m "refactor(whitebox): 移除 setup engine 块 / auth validation / finally browser cleanup"
```

---

### Task 11:Phase 2 回归 + Phase 1 兜底验证

**Files:**
- Test: 跑 Phase 1 + Phase 2 全部相关测试

**Interfaces:**
- 验证 Phase 2 完成后 Phase 1 清理对白盒成 no-op 但保留兜底。

- [ ] **Step 1: 验证 cleanup_browser_processes activity 成 no-op 兜底**

确认 `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` 的 `cleanup_browser_processes` activity 仍在(Phase 2 没删 workflow 对它的调用--因为 Task 10 Step 1(c) 已删调用)。activity 定义保留作 no-op:即使被调,`BrowserEngineFactory.get_engine(engine_name)` 在白盒无 engine_name 时抛 KeyError,被 `except Exception: pass` 吞掉,安全。

运行 Task 6 的测试确认 activity 仍可用(它 mock 了 engine,不依赖白盒 workflow 是否调):

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_workflows.py::TestWhiteboxBrowserProcessCleanup -v`
Expected: 全 PASS。

- [ ] **Step 2: 跑全部铁律测试**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_whitebox_browser_decoupling.py -v`
Expected: 全 PASS(铁律 1 + 2 + 反向断言)。

- [ ] **Step 3: 跑 Phase 1 相关测试确认无回归**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_browser_engine.py packages/core/tests/test_agent_browser_engine.py packages/core/tests/test_playwright_engine.py packages/core/tests/runtime/test_scan_runner.py -v`
Expected: 全 PASS。

- [ ] **Step 4: 跑 prompt_manager + blackbox 相关测试确认无回归**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_prompt_manager.py packages/blackbox/tests/test_blackbox_cleanup.py packages/blackbox/tests/test_worker_cleanup_callback.py -v`
Expected: 全 PASS。

- [ ] **Step 5: 最终 commit(若有 lint/格式调整)**

```bash
git add -A
git commit -m "test(whitebox): Phase 2 回归 + Phase 1 兜底验证全绿" --allow-empty
```

> 若无改动可省略此 commit。`--allow-empty` 仅在需留标记时用。

---

## 自审记录

- **Spec 覆盖**:
  - §4.1 Phase 1:Task 1-7(协议/两引擎/三路径/两 workflow finally/worker 回调)。
  - §4.2 Phase 2:Task 8-11(铁律测试/模板/workflow/回归)。
  - §4.2.0 归属表:Task 8 反向断言锁定黑盒模板不动。
  - §6 不变量 4 条:Task 1/2(永不抛)、Task 4(os._exit 前清理)、Task 8(铁律 1/2)。
- **Placeholder 扫描**:无 TBD/TODO,所有步骤含实际代码。
- **类型一致性**:`cleanup_processes(source_dir=None, session_ids=None) -> dict` 全 plan 一致;`install(loop, cleanup_callback=None)` 一致;`_do_cancel(..., cleanup_callback=None)` 一致。
- **风险**:`runtime_checkable` Protocol 的 isinstance 仅查方法存在性(Task 1 已在测试中体现);agent-browser 进程命令行带 profile 路径的假设需真机验证(plan §8 风险,Task 2 测试用 mock 验证逻辑,真机冒烟待实现后)。
