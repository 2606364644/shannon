# agent-browser auth 路径对齐（state save/load）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 agent-browser 引擎的 auth 完整 work——auth-validation 登录后导出到 `auth-state.json`，所有 exploit agent 导入复用，`verify_auth_state` 校验通过。

**Architecture:** agent-browser 原生有 `state save/load <path>`（与 playwright `state-save/load` 一一对应）。让 `AgentBrowserEngine` 用它走 `auth-state.json` 文件；`PromptManager` 显式注入 `{{AUTH_SAVE_COMMAND}}`/`{{AUTH_LOAD_COMMAND}}`（不靠 agent 从参考里找）；`--profile` 继续管运行时 session 隔离（并发安全），`auth-state.json` 管跨 agent 登录态传递。

**Tech Stack:** Python 3.12 / pytest / Temporal / Playwright（agent-browser 基于 Playwright）/ pydantic。

## Global Constraints

- 不改 `--profile` 运行时隔离（并发安全，保留）。
- 不改 playwright 引擎路径（已 work，仅经新变量统一注入）。
- 保留 `_shared-session.txt` 复用语义（load → 校验 → 跳过登录 / 失败则自登）。
- TDD：每步先写/改测试 → 跑失败 → 实现 → 跑通过 → commit。
- 只跑改动相关测试（CLAUDE.md：blackbox/core 全套 pytest 有预存挂起，勿广跑）。
- spec：`docs/superpowers/specs/2026-06-29-agent-browser-auth-state-save-load-design.md`。

---

## File Structure

| 文件 | 责任 | 改动 |
|---|---|---|
| `packages/core/src/shannon_core/services/engines/agent_browser_engine.py` | agent-browser engine | 改 `auth_save/load_command` + `_COMMANDS_REFERENCE` AUTH STATE 段 |
| `packages/core/tests/test_agent_browser_engine.py` | engine 单测 | 改 `TestAgentBrowserEngineAuth` + 加 commands_reference 断言 |
| `packages/core/src/shannon_core/prompts/manager.py` | prompt 渲染 | L105 后注入 `{{AUTH_SAVE_COMMAND}}`/`{{AUTH_LOAD_COMMAND}}` |
| `packages/core/tests/test_prompt_manager.py` | manager 单测 | 加变量注入测试 |
| `prompts/validate-authentication.txt` | auth-validation prompt | `<publish_session>` 用 `{{AUTH_SAVE_COMMAND}}` |
| `prompts/shared/_shared-session.txt` | exploit 共享 session partial | 用 `{{AUTH_LOAD_COMMAND}}` |
| `packages/core/src/shannon_core/services/validate_authentication.py` | auth 校验 | 按格式确认结果（大概率不改） |

---

### Task 1: AgentBrowserEngine 补 `state save/load` 命令

**Files:**
- Modify: `packages/core/src/shannon_core/services/engines/agent_browser_engine.py`（`auth_save_command` L124-126、`auth_load_command` L128-130、`_COMMANDS_REFERENCE` 的 AUTH STATE 段 L74-77）
- Test: `packages/core/tests/test_agent_browser_engine.py`（`TestAgentBrowserEngineAuth` L81-90、`TestAgentBrowserEngineCommandsReference` L55-73）

**Interfaces:**
- Produces: `AgentBrowserEngine.auth_save_command(session_id, path) -> "state save {path}"`；`auth_load_command(session_id, path) -> "state load {path}"`；`commands_reference()` 含 `state save` / `state load`。

- [ ] **Step 1: 改测试为新行为（先失败）**

把 `test_agent_browser_engine.py` 的 `TestAgentBrowserEngineAuth`（L81-90）整段替换为：

```python
class TestAgentBrowserEngineAuth:
    def test_auth_save_command_uses_state_save(self):
        """auth_save_command must emit `state save <path>` (agent-browser native)."""
        engine = AgentBrowserEngine()
        result = engine.auth_save_command("sess-1", "/tmp/auth.json")
        assert result == "state save /tmp/auth.json"

    def test_auth_load_command_uses_state_load(self):
        """auth_load_command must emit `state load <path>`."""
        engine = AgentBrowserEngine()
        result = engine.auth_load_command("sess-1", "/tmp/auth.json")
        assert result == "state load /tmp/auth.json"
```

在 `TestAgentBrowserEngineCommandsReference` 类内追加：

```python
    def test_commands_reference_lists_state_save_load(self):
        """reference must document state save/load for cross-session auth reuse."""
        engine = AgentBrowserEngine()
        ref = engine.commands_reference()
        assert "state save" in ref
        assert "state load" in ref
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/core/tests/test_agent_browser_engine.py::TestAgentBrowserEngineAuth packages/core/tests/test_agent_browser_engine.py::TestAgentBrowserEngineCommandsReference::test_commands_reference_lists_state_save_load -v`
Expected: FAIL（`auth_save_command` 仍返回 `""`，commands_reference 不含 `state save`）。

- [ ] **Step 3: 实现 engine 改动**

`agent_browser_engine.py` 的 `auth_save_command` / `auth_load_command`（L122-130）替换为：

```python
    # -- Auth helpers --------------------------------------------------------

    def auth_save_command(self, session_id: str, path: str) -> str:
        """Return the CLI command that saves auth state (cookies/localStorage) to *path*.

        agent-browser's native `state save <path>` writes a portable JSON file
        (cookies + storage + auth state), mirroring playwright's `state-save`.
        Used so auth-validation can hand login state to concurrent exploit agents
        via the shared auth-state.json (profile isolation alone can't cross sessions).
        """
        return f"state save {path}"

    def auth_load_command(self, session_id: str, path: str) -> str:
        """Return the CLI command that restores auth state from *path*."""
        return f"state load {path}"
```

`_COMMANDS_REFERENCE` 的 AUTH STATE 段（原 L74-77 "No explicit save/load commands needed..."）替换为：

```
AUTH STATE:
  agent-browser --session <session> state save <path>
    Save cookies, localStorage, and auth state to a portable JSON file.
  agent-browser --session <session> state load <path>
    Restore saved auth state from a JSON file into the current session.

  Auth state also auto-persists via the --profile flag, but use
  `state save/load` to share auth across sessions (save in one, load in
  another). When a prompt gives an explicit AUTH_SAVE/AUTH_LOAD command,
  run it verbatim against {{AUTH_STATE_FILE}}.
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest packages/core/tests/test_agent_browser_engine.py -v`
Expected: PASS（含改后的 auth 测试 + 新 commands_reference 测试；其余 engine 测试不回归）。

- [ ] **Step 5: commit**

```bash
git add packages/core/src/shannon_core/services/engines/agent_browser_engine.py packages/core/tests/test_agent_browser_engine.py
git commit -m "feat(engine): agent-browser auth_save/load_command 用 state save/load"
```

---

### Task 2: PromptManager 注入 `{{AUTH_SAVE_COMMAND}}`/`{{AUTH_LOAD_COMMAND}}`

**Files:**
- Modify: `packages/core/src/shannon_core/prompts/manager.py`（L105 `{{BROWSER_COMMANDS}}` 替换之后插入）
- Test: `packages/core/tests/test_prompt_manager.py`

**Interfaces:**
- Consumes: `engine.auth_save_command(session_id, path)` / `auth_load_command(...)`（Task 1）。
- Produces: 渲染时 `{{AUTH_SAVE_COMMAND}}` → engine 具体 save 命令（含 path）；`{{AUTH_LOAD_COMMAND}}` → load 命令。无 `AUTH_STATE_FILE` 时两者替换为空串。

- [ ] **Step 1: 写失败测试**

在 `test_prompt_manager.py` 末尾追加：

```python
def test_renders_auth_save_command_agent_browser(tmp_path):
    """{{AUTH_SAVE_COMMAND}} resolves to agent-browser `state save <path>`."""
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "probe.txt").write_text("save: {{AUTH_SAVE_COMMAND}}")
    manager = PromptManager(prompts)
    result = manager.load_sync("probe", {
        "browser_engine": "agent-browser",
        "browser_session_id": "sess-1",
        "AUTH_STATE_FILE": "/tmp/auth.json",
    })
    assert "state save /tmp/auth.json" in result


def test_renders_auth_load_command_playwright(tmp_path):
    """{{AUTH_LOAD_COMMAND}} resolves to playwright `state-load <path>`."""
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "probe.txt").write_text("load: {{AUTH_LOAD_COMMAND}}")
    manager = PromptManager(prompts)
    result = manager.load_sync("probe", {
        "browser_engine": "playwright",
        "browser_session_id": "sess-1",
        "AUTH_STATE_FILE": "/tmp/auth.json",
    })
    assert "state-load /tmp/auth.json" in result


def test_auth_save_load_command_empty_without_state_file(tmp_path):
    """No AUTH_STATE_FILE → both placeholders empty (no auth path in scope)."""
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "probe.txt").write_text("[{{AUTH_SAVE_COMMAND}}][{{AUTH_LOAD_COMMAND}}]")
    manager = PromptManager(prompts)
    result = manager.load_sync("probe", {"browser_engine": "agent-browser"})
    assert result == "[][]"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/core/tests/test_prompt_manager.py::test_renders_auth_save_command_agent_browser packages/core/tests/test_prompt_manager.py::test_renders_auth_load_command_playwright packages/core/tests/test_prompt_manager.py::test_auth_save_load_command_empty_without_state_file -v`
Expected: FAIL（`{{AUTH_SAVE_COMMAND}}` 未被替换，原样留在结果里）。

- [ ] **Step 3: 实现 manager 注入**

`manager.py` 在 L105（`result = result.replace("{{BROWSER_COMMANDS}}", engine.commands_reference())`）之后插入：

```python
        # Auth state save/load commands (engine-specific). Only emitted when an
        # auth-state file is in scope (auth-validation + exploit reuse path).
        auth_state_file = variables.get("AUTH_STATE_FILE", "")
        if auth_state_file:
            result = result.replace(
                "{{AUTH_SAVE_COMMAND}}",
                engine.auth_save_command(session_id, auth_state_file),
            )
            result = result.replace(
                "{{AUTH_LOAD_COMMAND}}",
                engine.auth_load_command(session_id, auth_state_file),
            )
        else:
            result = result.replace("{{AUTH_SAVE_COMMAND}}", "")
            result = result.replace("{{AUTH_LOAD_COMMAND}}", "")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest packages/core/tests/test_prompt_manager.py -v`
Expected: PASS（新 3 测试 + 现有 manager 测试不回归）。

- [ ] **Step 5: commit**

```bash
git add packages/core/src/shannon_core/prompts/manager.py packages/core/tests/test_prompt_manager.py
git commit -m "feat(prompt): manager 注入 AUTH_SAVE/LOAD_COMMAND 变量"
```

---

### Task 3: prompt 文件用显式 `{{AUTH_SAVE_COMMAND}}`/`{{AUTH_LOAD_COMMAND}}`

> 直接修复当前 bug 病灶：prompt 原用泛指 "browser's session state save/load command"，agent-browser 无该命令 → agent 自己 write_file 写总结 → 格式不匹配 verify。改成显式变量，agent 拿到确切命令。

**Files:**
- Modify: `prompts/validate-authentication.txt`（`<publish_session>` 段 L22-28）
- Modify: `prompts/shared/_shared-session.txt`（`<shared_authenticated_session>` 段）
- Test: `packages/core/tests/test_prompt_manager.py`

**Interfaces:**
- Consumes: `{{AUTH_SAVE_COMMAND}}`/`{{AUTH_LOAD_COMMAND}}`（Task 2）+ `{{AUTH_STATE_FILE}}`（validate_authentication 传入）。

- [ ] **Step 1: 写失败测试（渲染后含具体命令）**

在 `test_prompt_manager.py` 追加（用真实 prompts 目录，验证端到端渲染）：

```python
def test_validate_auth_prompt_emits_save_command():
    """validate-authentication prompt renders a concrete save command."""
    from shannon_core.utils.paths import find_project_root  # noqa: F401
    from pathlib import Path
    prompts_dir = Path(__file__).resolve().parents[2] / "prompts"
    manager = PromptManager(prompts_dir)
    result = manager.load_sync("validate-authentication", {
        "browser_engine": "agent-browser",
        "browser_session_id": "sess-1",
        "AUTH_STATE_FILE": "/tmp/auth.json",
        "AUTH_CONTEXT": "(auth context)",
        "LOGIN_INSTRUCTIONS": "(login steps)",
        "BROWSER_COMMANDS": "(browser ref)",
        "BROWSER_SESSION_FLAG": "--session sess-1",
    })
    assert "state save /tmp/auth.json" in result
    # 泛指文字应已不在（被变量替换语义取代）
    assert "browser's session state save command" not in result
```

> 注：`prompts_dir` 路径 `parents[2]` —— 测试文件在 `packages/core/tests/`，`parents[2]` = `packages/`，需确认 repo 根 prompts 在 `packages/` 还是 repo 根。执行时若路径不对，改用 `Path(__file__).resolve().parents[3] / "prompts"`（repo 根）。先按 `parents[2]` 跑，FAIL 则按报错调整。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/core/tests/test_prompt_manager.py::test_validate_auth_prompt_emits_save_command -v`
Expected: FAIL（`<publish_session>` 仍是泛指文字，渲染后不含 `state save /tmp/auth.json`）。

- [ ] **Step 3: 改 `validate-authentication.txt` 的 `<publish_session>`**

原 L22-28 `<publish_session>` 段替换为：

```
<publish_session>
After verification confirms login_success, save the authenticated browser session so the rest of the pipeline can reuse it instead of logging in again:

  Run this exact command (substituting the session flag you've been using):
    {{AUTH_SAVE_COMMAND}}

This writes the portable auth-state JSON to {{AUTH_STATE_FILE}}. Run it only when login_success is true. Skip it on failure. Do NOT hand-write a summary JSON — run the command verbatim so downstream agents can load the real cookies/localStorage.
</publish_session>
```

- [ ] **Step 4: 改 `prompts/shared/_shared-session.txt`**

把 `<shared_authenticated_session>` 内的 "Use your browser's session state load command with {{BROWSER_SESSION_FLAG}} to restore authentication state from {{AUTH_STATE_FILE}}" 替换为显式命令：

```
<shared_authenticated_session>
The preflight already logged in and saved the authenticated browser
session to:

  {{AUTH_STATE_FILE}}

Restore it before doing anything else — run this exact command:

  {{AUTH_LOAD_COMMAND}}

Then run verification (per the success_condition in your authentication
config) to confirm the restored session is still valid:

- If verification passes → SKIP the login flow below entirely and
  proceed with your primary task. You are authenticated.
- If verification fails → the saved session is stale. Fall through to
  the full login flow below and perform it on your own browser session.
  Do NOT overwrite {{AUTH_STATE_FILE}}.
</shared_authenticated_session>
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest packages/core/tests/test_prompt_manager.py -v`
Expected: PASS。

- [ ] **Step 6: commit**

```bash
git add prompts/validate-authentication.txt prompts/shared/_shared-session.txt packages/core/tests/test_prompt_manager.py
git commit -m "feat(prompt): validate-auth/shared-session 用显式 AUTH_SAVE/LOAD_COMMAND"
```

---

### Task 4: 确认 agent-browser `state save` 格式 + verify 决策

> agent-browser 基于 Playwright，`state save` 预期 = Playwright `storage_state()` = `{cookies:[...], origins:[...]}`，则 `verify_auth_state`（`validate_authentication.py:79-80`）**不用改**。本 task 先探针确认，按结果走 A/B。

**Files:**
- Probe: 临时 `/tmp/ab-state-probe.json`
- Maybe modify: `packages/core/src/shannon_core/services/validate_authentication.py`（仅路径 B）
- Test: `packages/core/tests/test_validate_authentication.py`（已有 `verify_auth_state` 测试 L26-48）

- [ ] **Step 1: 探针确认格式**

Run（在装好 agent-browser + 浏览器的环境）:
```bash
agent-browser --session probe open https://example.com
agent-browser --session probe state save /tmp/ab-state-probe.json
head -c 400 /tmp/ab-state-probe.json; echo
```
观察顶层 key：
- 若为 `{"cookies": [...], "origins": [...]}` → **走路径 A**（verify 不改）。
- 若顶层无 `cookies`（如 `{"cookies":...}` 在别处，或别的 schema）→ **走路径 B**。

- [ ] **Step 2（路径 A：格式=storageState）: 加确认测试，verify 不改**

在 `test_validate_authentication.py` 追加（文档化假设 + 锁定 storageState 校验行为）：

```python
async def test_verify_accepts_storagestate_with_cookies(tmp_path):
    """agent-browser `state save` ≈ Playwright storageState {cookies, origins}.
    verify_auth_state must accept it when cookies present."""
    state_file = tmp_path / "auth-state.json"
    state_file.write_text(json.dumps({
        "cookies": [{"name": "s", "value": "v", "domain": "example.com"}],
        "origins": [],
    }))
    result = await verify_auth_state(state_file)
    assert result.success is True
```

Run: `uv run pytest packages/core/tests/test_validate_authentication.py -v`
Expected: PASS（verify 现有逻辑已覆盖，新测试确认 storageState 格式被接受）。→ 跳到 Step 4。

- [ ] **Step 3（路径 B：格式≠storageState）: verify 按 engine 分支**

> 仅当 Step 1 探针显示顶层无 `cookies` 时执行。把实际探针到的 schema 填入下方 `_agent_browser_state_has_auth` 的判断。

`validate_authentication.py` 的 `verify_auth_state`（L60-88）改为按顶层结构兼容：

```python
async def verify_auth_state(state_file: Path) -> AuthValidationResult:
    if not await async_path_exists(state_file):
        return AuthValidationResult(success=False, failure_point="out_of_band",
                                    failure_detail=f"Agent did not save auth state to {state_file}")
    contents = await async_read_file(state_file)
    try:
        parsed = json.loads(contents)
    except json.JSONDecodeError as e:
        return AuthValidationResult(success=False, failure_point="out_of_band",
                                    failure_detail=f"Auth state file is not valid JSON: {e}")

    # Playwright storageState 格式 (playwright + agent-browser state save): {cookies, origins}
    if isinstance(parsed, dict) and ("cookies" in parsed or "origins" in parsed):
        cookie_count = len(parsed.get("cookies", []))
        origin_count = len(parsed.get("origins", []))
    else:
        # 探针确认的 agent-browser 备用 schema（按 Step 1 实际结构填）
        cookie_count = origin_count = 0
    if cookie_count == 0 and origin_count == 0:
        return AuthValidationResult(success=False, failure_point="out_of_band",
                                    failure_detail="Auth state contains no cookies or origins — browser was not actually logged in")
    return AuthValidationResult(success=True)
```

为路径 B 加测试（覆盖 dict 但无 cookies/origins 的边界）：

```python
async def test_verify_rejects_dict_without_cookies_or_origins(tmp_path):
    state_file = tmp_path / "auth-state.json"
    state_file.write_text(json.dumps({"login_success": True}))  # 语义化总结，非 storageState
    result = await verify_auth_state(state_file)
    assert result.success is False
```

Run: `uv run pytest packages/core/tests/test_validate_authentication.py -v`
Expected: PASS。

- [ ] **Step 4: commit**

路径 A:
```bash
git add packages/core/tests/test_validate_authentication.py
git commit -m "test(auth): 锁定 verify_auth_state 接受 storageState 格式（agent-browser state save）"
```
路径 B:
```bash
git add packages/core/src/shannon_core/services/validate_authentication.py packages/core/tests/test_validate_authentication.py
git commit -m "fix(auth): verify_auth_state 兼容 agent-browser state 文件格式"
```

---

### Task 5: 真机冒烟（手动）

> 需装好 agent-browser 浏览器 + 网络。自动化测试到此为止，最终由人确认。

- [ ] **Step 1: 准备 agent-browser config**

确保一个 config 用 `browser_engine: agent-browser`（可在 `configs/moomoo.yaml` 改回，或新建）。当前 `configs/moomoo.yaml` 是临时 `playwright`，改回：
```yaml
browser_engine: agent-browser
```

- [ ] **Step 2: 重跑黑盒**

```bash
uv run shannon-blackbox start -c configs/moomoo.yaml \
  --repo /root/code/frontend/invite_code_center/ \
  --url https://invite-code.moomoo.com/ \
  -w invite_code_center_20260629-134944 --rerun
```

- [ ] **Step 3: 确认**

- auth-validation 过（agent 跑 `state save` 产 `auth-state.json`，verify 通过——不再 `'str' has no attribute 'get'` / `no cookies or origins`）。
- exploit agent 日志显示 `state load` + 跳过登录（复用，不重复 OA→开户→moomoo 登录）。
- 检查 `auth-state.json` 顶层是 `{cookies, origins}`。

---

## Self-Review

**1. Spec coverage:**
- engine `auth_save/load_command` + commands_reference → Task 1 ✓
- manager 注入变量 → Task 2 ✓
- prompt 显式变量（`<publish_session>` + `_shared-session`）→ Task 3 ✓
- verify 按格式确认（大概率不改 + 退路）→ Task 4 ✓
- 测试（engine/prompt/verify）→ Task 1/2/3/4 ✓
- 真机冒烟 → Task 5 ✓
- 不改 --profile / playwright / _shared-session 语义 → 全局约束 + 各 task 范围 ✓

**2. Placeholder scan:** Task 4 路径 B 的 `_agent_browser_state_has_auth` 注释"按 Step 1 实际结构填"——这是条件分支（仅路径 B + 探针结果驱动），非占位；主路径 A 完整。Task 3 Step 1 的 `parents[2]` 路径给了 fallback 修正说明。无 TBD/TODO。

**3. Type consistency:** `auth_save_command(session_id, path)` / `auth_load_command(session_id, path)` 在 Task 1 定义、Task 2 消费，签名一致（engine 既有签名，两引擎统一）。`{{AUTH_SAVE_COMMAND}}`/`{{AUTH_LOAD_COMMAND}}` 在 Task 2 注入、Task 3 消费，变量名一致。`verify_auth_state(state_file)` 签名不变。
