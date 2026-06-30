# 黑盒 recon 登录态接线（executor 基层统一注入）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让黑盒 recon 阶段复用 auth 保存的登录态——在 `AgentExecutor.execute` 基层统一注入 `AUTH_STATE_FILE`，对齐 TS `agent-execution.ts:133`，修复 recon_executor 移植遗漏。

**Architecture:** `AgentExecutor.execute`（core 共享层）构建 variables 时注入 `AUTH_STATE_FILE = auth_state_path(deliverables.parent)`；所有 agent 自动有，仅"有 auth 配置 + prompt include `shared/_shared-session.txt` partial"的 agent 生效，其余经 `manager.py:167` strip block 双重 no-op。移除 `exploit_executor` 的显式注入以保持单一来源。

**Tech Stack:** Python 3.12 / temporalio / pytest / asyncio / monkeypatch

## Global Constraints

- **双轨独立性 + 白盒/黑盒可分开执行**：本改动只动 executor 注入逻辑，注入对白盒 no-op（`manager.py:167` strip + 白盒不 include partial），不得破坏白盒。
- **TS 对齐**：本设计正是补齐 TS `agent-execution.ts:133` 的"统一注入，不区分 agent"。
- **测试陷阱**（CLAUDE.md）：全套 pytest 有预存挂起/失败，**只跑本 plan 改动相关的测试文件**，勿广跑全套。
- **路径不变量**：`deliverables.parent ≡ input.workspace_path`（auth save/load 落同一 `auth-state.json`），由 Task 4 锁定。

**Spec:** `docs/superpowers/specs/2026-06-30-blackbox-recon-auth-state-injection-design.md`

---

### Task 1: executor 基层统一注入 AUTH_STATE_FILE

**Files:**
- Modify: `packages/core/src/shannon_core/agents/executor.py`（variables 构建，约 line 79-84；顶部 import）
- Test: `packages/core/tests/test_executor_auth_state_injection.py`（新建）

**Interfaces:**
- Consumes: `auth_state_path(workspace_path)` from `shannon_core.services.validate_authentication`（签名 `(workspace_path: str | Path) -> Path`，返回 `Path(workspace_path)/"auth-state.json"`）
- Produces: `AgentExecutor.execute` 的 variables 字典含 `AUTH_STATE_FILE` 键（所有调用 execute 的 agent 自动有）

- [ ] **Step 1: 写失败测试**

新建 `packages/core/tests/test_executor_auth_state_injection.py`：

```python
"""AgentExecutor.execute 基层统一注入 AUTH_STATE_FILE（对齐 TS agent-execution.ts:133）。

截获 prompt_manager.load_sync 收到的 variables，断言 AUTH_STATE_FILE
= <deliverables.parent>/auth-state.json（与 auth save 的 input.workspace_path 同文件）。
"""
import asyncio

from shannon_core.agents import executor as exec_mod
from shannon_core.models.agents import AgentName
from shannon_core.models.metrics import AgentMetrics


def _run(coro):
    return asyncio.run(coro)


def test_executor_injects_auth_state_file(tmp_path, monkeypatch):
    deliverables = tmp_path / "workspaces" / "session" / "deliverables"
    deliverables.mkdir(parents=True)

    async def fake_run(**kw):
        return AgentMetrics(duration_ms=1, cost_usd=0.0, num_turns=1)

    monkeypatch.setattr(exec_mod, "run_claude_prompt", fake_run)
    monkeypatch.setattr(
        exec_mod.GitManager, "ensure_repository",
        classmethod(lambda cls, p: asyncio.sleep(0)),
    )
    monkeypatch.setattr(
        exec_mod.GitManager, "create_checkpoint",
        lambda *a, **k: asyncio.sleep(0),
    )

    from shannon_core.prompts.manager import PromptManager
    pm = PromptManager.__new__(PromptManager)
    pm.prompts_dir = tmp_path
    captured = {}

    def fake_load(template, *, variables=None, **kw):
        captured["variables"] = variables
        return "PROMPT"

    monkeypatch.setattr(pm, "load_sync", fake_load)

    ex = exec_mod.AgentExecutor(pm)
    _run(ex.execute(
        agent_name=AgentName.RECON_BLACKBOX,
        repo_path=str(deliverables),
        web_url="https://example.com",
        deliverables_path=str(deliverables),
        skip_artifact_postprocess=True,
    ))

    assert "AUTH_STATE_FILE" in captured["variables"], \
        "AgentExecutor.execute 必须基层统一注入 AUTH_STATE_FILE（对齐 TS）"
    assert captured["variables"]["AUTH_STATE_FILE"] == \
        str(deliverables.parent / "auth-state.json")
```

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run pytest packages/core/tests/test_executor_auth_state_injection.py -v`
Expected: FAIL — `KeyError: 'AUTH_STATE_FILE'`（executor 还没注入）

- [ ] **Step 3: 改 executor.py 注入 AUTH_STATE_FILE**

在 `packages/core/src/shannon_core/agents/executor.py` 顶部 import 区（与其它 `from shannon_core...` 同区）加：

```python
from shannon_core.services.validate_authentication import auth_state_path
```

找到 variables 构建（约 line 79-84），改为：

```python
        variables = {
            "web_url": web_url,
            "repo_path": str(repo),
            "deliverables_path": str(deliverables),
            "scratchpad_path": str(deliverables.parent / "scratchpad"),
            # 统一注入 auth-state 路径（对齐 TS agent-execution.ts:133）。
            # workspace_path = deliverables.parent（≡ input.workspace_path，
            # 见 spec §3.3）。仅"有 auth 配置 + prompt include shared-session
            # partial"的 agent 生效；其余 manager strip block，no-op（spec §4）。
            "AUTH_STATE_FILE": str(auth_state_path(deliverables.parent)),
        }
```

- [ ] **Step 4: 跑测试验证通过**

Run: `uv run pytest packages/core/tests/test_executor_auth_state_injection.py -v`
Expected: PASS

- [ ] **Step 5: commit**

```bash
git add packages/core/src/shannon_core/agents/executor.py packages/core/tests/test_executor_auth_state_injection.py
git commit -m "feat(core): AgentExecutor 基层统一注入 AUTH_STATE_FILE（对齐 TS agent-execution）"
```

---

### Task 2: 移除 exploit_executor 显式注入（单一来源）

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py`（删 line 65 注入 + line 15 import）
- Test: `packages/blackbox/tests/test_executors.py:178-196`（更新现有测试）

**Interfaces:**
- Consumes: Task 1 的 executor 基层注入（exploit 不再需要自己传 AUTH_STATE_FILE）
- Produces: exploit_executor 不再向 prompt_variables 加 AUTH_STATE_FILE（基层接管）

- [ ] **Step 1: 更新现有测试断言（先改测试，让它反映新契约）**

在 `packages/blackbox/tests/test_executors.py` 把 `test_exploit_executor_passes_auth_state_file`（line 178-196）整体替换为：

```python
@pytest.mark.asyncio
async def test_exploit_executor_no_longer_injects_auth_state_file(mock_repo):
    """AUTH_STATE_FILE 由 AgentExecutor.execute 基层统一注入（方案 B），
    exploit_executor 不再显式传——单一来源，避免双注入。"""
    repo, deliverables = mock_repo
    mock_executor = AsyncMock()
    mock_executor.execute.return_value = AgentMetrics(duration_ms=1, cost_usd=0.0, num_turns=1)
    exploit = ExploitExecutor(mock_executor)
    await exploit.execute(
        agent_name=AgentName.INJECTION_EXPLOIT,
        vuln_type="injection",
        workspace_path=repo,
        deliverables_path=deliverables,
        web_url="https://example.com",
    )
    pv = mock_executor.execute.call_args.kwargs.get("prompt_variables") or {}
    assert "AUTH_STATE_FILE" not in pv, \
        "AUTH_STATE_FILE 应由 AgentExecutor 基层注入，exploit_executor 不再显式传"
```

- [ ] **Step 2: 跑测试验证失败（当前 exploit_executor 还显式传）**

Run: `uv run pytest packages/blackbox/tests/test_executors.py::test_exploit_executor_no_longer_injects_auth_state_file -v`
Expected: FAIL — `assert "AUTH_STATE_FILE" not in pv`（line 65 还在显式传）

- [ ] **Step 3: 移除 exploit_executor 的显式注入**

在 `packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py`：

删 line 15：
```python
from shannon_core.services.validate_authentication import auth_state_path
```

删 line 60-65 整段注释 + 注入：
```python
        # Auth-state reuse: let this exploit agent load the preflight's authenticated
        # session (cookies/localStorage) from the shared auth-state.json written by
        # validate-authentication. The <shared_authenticated_session> partial consumes
        # {{AUTH_STATE_FILE}} / {{AUTH_LOAD_COMMAND}}; the manager strips that block
        # when no auth is configured, so passing this unconditionally is a no-op then.
        prompt_variables["AUTH_STATE_FILE"] = str(auth_state_path(workspace_path))
```

保留 `workspace_path` 参数（签名稳定，不改调用方 `activities.py:225`）；移除注入后该参数在 execute 内不再被使用，可接受。

- [ ] **Step 4: 跑测试验证通过**

Run: `uv run pytest packages/blackbox/tests/test_executors.py::test_exploit_executor_no_longer_injects_auth_state_file -v`
Expected: PASS

- [ ] **Step 5: 顺带跑 exploit_executor 全部测试确认无回归**

Run: `uv run pytest packages/blackbox/tests/test_executors.py -v`
Expected: 全 PASS（其它 exploit 测试不受影响——它们不依赖 AUTH_STATE_FILE）

- [ ] **Step 6: commit**

```bash
git add packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py packages/blackbox/tests/test_executors.py
git commit -m "refactor(blackbox): exploit_executor 移除显式 AUTH_STATE_FILE 注入（基层统一管）"
```

---

### Task 3: no-op 回归守卫（无 auth 时 strip shared-session block）

**Files:**
- Test: `packages/core/tests/test_prompt_manager.py`（追加一个测试）

**Interfaces:**
- Consumes: `PromptManager.load_sync(template, variables, config=None)` —— `config` 无 authentication 时 `manager.py:167` strip `<shared_authenticated_session>` block
- Produces: 锁定 no-op 不变性（基层注入 AUTH_STATE_FILE 对白盒/无-c 场景无害的回归守卫）

- [ ] **Step 1: 写测试（验证现有 strip 行为，回归守卫）**

在 `packages/core/tests/test_prompt_manager.py` 末尾追加：

```python
def test_shared_session_block_stripped_when_no_auth(prompts_dir, tmp_path):
    """无 config.authentication 时 manager strip <shared_authenticated_session> block。

    即便 variables 含 AUTH_STATE_FILE（executor 基层统一注入），无 auth 场景 block
    被移除 → state load 不出现 → 白盒/无-c no-op（方案 B 安全性根基，spec §4）。
    """
    tmpl = prompts_dir / "with-shared-session.txt"
    tmpl.write_text(
        "Before\n"
        "<shared_authenticated_session>\n"
        "Restore: {{AUTH_LOAD_COMMAND}} file {{AUTH_STATE_FILE}}\n"
        "</shared_authenticated_session>\n"
        "After"
    )
    manager = PromptManager(prompts_dir)
    result = manager.load_sync(
        "with-shared-session",
        {"web_url": "https://x.com", "repo_path": "/r",
         "AUTH_STATE_FILE": "/ws/auth-state.json"},
        config=None,
    )
    assert "<shared_authenticated_session>" not in result
    assert "state load" not in result
    assert "Before" in result and "After" in result
```

- [ ] **Step 2: 跑测试验证通过（manager 已有 strip 逻辑）**

Run: `uv run pytest packages/core/tests/test_prompt_manager.py::test_shared_session_block_stripped_when_no_auth -v`
Expected: PASS（`manager.py:167` 现有 strip 逻辑生效）

- [ ] **Step 3: commit**

```bash
git add packages/core/tests/test_prompt_manager.py
git commit -m "test(core): 守卫无 auth 时 strip shared-session block（基层注入 no-op 保证）"
```

---

### Task 4: 路径一致性锁定 + 白盒/blackbox 回归验证

**Files:**
- Test: `packages/core/tests/test_paths.py`（追加路径一致性测试）

**Interfaces:**
- Consumes: `resolve_deliverables_path(repo_path, deliverables_subdir, workspace_name, workspaces_root)` from `shannon_core.utils.paths`
- Produces: 锁定 `deliverables.parent ≡ workspace_path`（executor 基层推导 AUTH_STATE_FILE 的隐含约定，防未来 deliverables 结构变更悄悄破坏 save/load 一致性）

- [ ] **Step 1: 写路径一致性测试**

在 `packages/core/tests/test_paths.py` 末尾追加：

```python
def test_resolve_deliverables_parent_is_workspace(tmp_path):
    """deliverables.parent ≡ workspace_path（auth save/load 路径一致性根基）。

    executor 基层用 deliverables.parent 推导 AUTH_STATE_FILE，须与 auth save 用的
    input.workspace_path 同目录（spec §3.3）。锁定此隐含约定，防 deliverables
    结构变更悄悄破坏 save/load 一致性。
    """
    from shannon_core.utils.paths import resolve_deliverables_path
    ws_root = tmp_path / "workspaces"
    ws_root.mkdir()
    deliverables = resolve_deliverables_path(
        repo_path=None,
        deliverables_subdir="deliverables",
        workspace_name="session",
        workspaces_root=ws_root,
    )
    workspace_path = ws_root / "session"
    assert deliverables.parent == workspace_path
    assert deliverables.name == "deliverables"
```

- [ ] **Step 2: 跑测试验证通过**

Run: `uv run pytest packages/core/tests/test_paths.py::test_resolve_deliverables_parent_is_workspace -v`
Expected: PASS

- [ ] **Step 3: 回归——跑白盒 prompt 渲染 + blackbox executor 相关测试（确认注入无回归）**

Run:
```bash
uv run pytest packages/core/tests/test_prompt_manager.py packages/core/tests/test_executor_template.py packages/blackbox/tests/test_executors.py -v
```
Expected: 全 PASS（白盒 prompt 渲染不受注入影响——变量不被未 include 的 prompt 消费；executor template/exploit 测试不破坏）

- [ ] **Step 4: commit**

```bash
git add packages/core/tests/test_paths.py
git commit -m "test(core): 锁定 deliverables.parent ≡ workspace_path（auth save/load 一致性）"
```

---

## Self-Review 已完成

- **Spec coverage**：§3.1 注入→Task 1；§3.2 清理→Task 2；§4 no-op→Task 3；§3.3 路径一致性→Task 4；§7.5 白盒无回归→Task 4 Step 3。recon 端到端"有 auth 渲染含 state load"（§7.3）由 Task 1 variables 注入 + 现有 manager load 逻辑共同保证，spec §6 标注真机冒烟端到端验证。
- **Placeholder scan**：无 TBD/TODO，每步含完整代码与命令。
- **Type consistency**：`auth_state_path`、`AUTH_STATE_FILE`、`deliverables.parent` 跨 task 一致。
