# 黑盒默认引擎切 agent-browser + 引擎无关化修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把黑盒扫描默认浏览器引擎从 playwright 切到 agent-browser，并修复 CLI 前置闸口硬编码 playwright-cli 的 bug（含 bootstrap 安装链路）与显示层只识别 playwright-cli 的差距。

**Architecture:** 给 `BrowserEngine` Protocol 加 `cli_binary` 属性让引擎自描述 PATH binary 名；给 `BrowserEngineFactory` 加 `resolve_name(config_path)` 统一解析引擎名（env > config > 默认）；CLI 闸口经新 helper `ensure_browser_engine()` 按解析出的引擎检查对应 binary；三处默认值改 agent-browser；bootstrap.sh 加 agent-browser 安装；formatters 加 agent-browser 命令分支。保留双引擎，playwright 作 fallback。

**Tech Stack:** Python 3.13 / pytest / click / temporalio；bash（bootstrap.sh）

## Global Constraints

- 双引擎保留：`BrowserEngineType = Literal["playwright", "agent-browser"]` 不变，`PlaywrightEngine` 及其专属测试保留。
- 只跑改动相关测试子集（CLAUDE.md）：`packages/core/tests/test_browser_engine.py`、`packages/core/tests/test_prompt_manager.py`、`packages/core/tests/display/test_formatters.py`、`packages/blackbox/tests/test_workflows.py`、新增的 prerequisites 测试。**勿跑全套**（会 hang）。
- agent-browser npm 包名 = `agent-browser`，安装 `npm install -g agent-browser` + `agent-browser install`（下 Chrome）。
- 不碰 pre-existing 的 `session_flag`/`session_flags` 命名不一致（[playwright_engine.py:117](../../../packages/core/src/shannon_core/services/engines/playwright_engine.py)），与本任务无关。
- 工作目录：`/Users/mango/project/shannon-refactor/shannon-py`，分支 `feat/fork-py`。pytest 经 `uv run pytest` 或仓库既定方式运行（见各 Task 命令）。

## File Structure

| 文件 | 责任 | 改动 |
|---|---|---|
| `packages/core/src/shannon_core/services/browser_engine.py` | Protocol + Factory | 加 `cli_binary` Protocol 属性；加 `resolve_name` classmethod |
| `packages/core/src/shannon_core/services/engines/playwright_engine.py` | playwright 引擎 | 加 `cli_binary` property |
| `packages/core/src/shannon_core/services/engines/agent_browser_engine.py` | agent-browser 引擎 | 加 `cli_binary` property |
| `packages/core/src/shannon_core/runtime/prerequisites.py` | 前置检查 | 加 `ensure_browser_engine(config_path)` helper |
| `packages/blackbox/src/shannon_blackbox/cli/main.py` | 黑盒 CLI 入口 | 闸口改调 `ensure_browser_engine` |
| `packages/core/src/shannon_core/models/config.py` | Config 模型 | 默认 `browser_engine` → agent-browser |
| `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` | 黑盒 workflow | 无 config fallback → agent-browser |
| `packages/core/src/shannon_core/prompts/manager.py` | prompt 渲染 | 默认 `browser_engine` → agent-browser |
| `scripts/bootstrap.sh` | 依赖安装 | 加 `install_agent_browser`；blackbox/all profile 调用 |
| `packages/core/src/shannon_core/display/formatters.py` | 进度格式化 | `maybe_browser_action` 加 agent-browser 分支 |
| `packages/core/tests/test_browser_engine.py` | 引擎单测 | 加 `cli_binary`、`resolve_name` 用例；`_StubEngine` 补 `cli_binary` |
| `packages/core/tests/test_prompt_manager.py` | prompt 单测 | P2 连带：3 处默认断言调整 |
| `packages/blackbox/tests/test_workflows.py` | workflow 单测 | P2 连带：默认引擎断言调整 |
| `packages/core/tests/display/test_formatters.py` | 格式化单测 | 加 agent-browser 用例 |
| `packages/core/tests/test_prerequisites.py`（新建） | 前置检查单测 | `ensure_browser_engine` 用例 |

---

### Task 1: P1a — `BrowserEngine.cli_binary` 属性

让引擎自描述它在 PATH 上要 `which` 检查的 binary 名（playwright 引擎的 `name="playwright"` 与 binary `playwright-cli` 不同，这是 P1 的根因）。

**Files:**
- Modify: `packages/core/src/shannon_core/services/browser_engine.py`（Protocol，加在 `name` 之后）
- Modify: `packages/core/src/shannon_core/services/engines/playwright_engine.py`（加在 `name` property 之后）
- Modify: `packages/core/src/shannon_core/services/engines/agent_browser_engine.py`（加在 `name` property 之后）
- Modify: `packages/core/tests/test_browser_engine.py`（`_StubEngine` 补 `cli_binary`；加断言用例）
- Test: `packages/core/tests/test_browser_engine.py`

**Interfaces:**
- Produces: `BrowserEngine.cli_binary` (property, `-> str`)；两引擎实现返回 `"playwright-cli"` / `"agent-browser"`

- [ ] **Step 1: Write the failing test**

在 `packages/core/tests/test_browser_engine.py` 的 `TestRegisteredEngines` 类内（`test_agent_browser_satisfies_protocol` 之后）追加：

```python
    def test_playwright_cli_binary(self):
        from shannon_core.services.engines.playwright_engine import PlaywrightEngine
        assert PlaywrightEngine().cli_binary == "playwright-cli"

    def test_agent_browser_cli_binary(self):
        from shannon_core.services.engines.agent_browser_engine import AgentBrowserEngine
        assert AgentBrowserEngine().cli_binary == "agent-browser"
```

并在文件顶部 `_StubEngine` 类内（`name` property 之后）补一个 stub 属性，保持它满足 Protocol：

```python
    @property
    def cli_binary(self) -> str:  # pragma: no cover – simple property
        return "stub-cli"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/test_browser_engine.py::TestRegisteredEngines::test_playwright_cli_binary packages/core/tests/test_browser_engine.py::TestRegisteredEngines::test_agent_browser_cli_binary -v`
Expected: FAIL with `AttributeError: 'PlaywrightEngine' object has no attribute 'cli_binary'`

- [ ] **Step 3: Add `cli_binary` to Protocol**

在 `packages/core/src/shannon_core/services/browser_engine.py` 的 `BrowserEngine` Protocol 内，紧接 `name` property 之后插入：

```python
    @property
    def cli_binary(self) -> str:
        """Name of the CLI binary to look up on PATH, e.g. ``'playwright-cli'``.

        Distinct from ``name`` (the registry identifier): playwright registers as
        ``'playwright'`` but its binary is ``'playwright-cli'``.
        """
        ...
```

- [ ] **Step 4: Add `cli_binary` to PlaywrightEngine**

在 `packages/core/src/shannon_core/services/engines/playwright_engine.py` 的 `PlaywrightEngine.name` property 之后插入：

```python
    @property
    def cli_binary(self) -> str:
        """PATH binary name for availability checks."""
        return "playwright-cli"
```

- [ ] **Step 5: Add `cli_binary` to AgentBrowserEngine**

在 `packages/core/src/shannon_core/services/engines/agent_browser_engine.py` 的 `AgentBrowserEngine.name` property 之后插入：

```python
    @property
    def cli_binary(self) -> str:
        """PATH binary name for availability checks."""
        return "agent-browser"
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/test_browser_engine.py -v`
Expected: PASS（含新增两个用例 + 既有 Protocol/Factory 用例；pre-existing 的 `session_flag` 相关失败若存在不算本 Task 回归）

- [ ] **Step 7: Commit**

```bash
git add packages/core/src/shannon_core/services/browser_engine.py \
        packages/core/src/shannon_core/services/engines/playwright_engine.py \
        packages/core/src/shannon_core/services/engines/agent_browser_engine.py \
        packages/core/tests/test_browser_engine.py
git commit -m "feat(core): BrowserEngine.cli_binary 属性，引擎自描述 PATH binary 名"
```

---

### Task 2: P1b-prep — `BrowserEngineFactory.resolve_name`

统一"引擎名解析"逻辑：env > config > 默认。供 CLI 闸口用。

**Files:**
- Modify: `packages/core/src/shannon_core/services/browser_engine.py`（Factory 加 classmethod）
- Test: `packages/core/tests/test_browser_engine.py`

**Interfaces:**
- Produces: `BrowserEngineFactory.resolve_name(config_path: str | None = None) -> str`
- 优先级：`SHANNON_BROWSER_ENGINE` env > `parse_config(config_path).browser_engine`（config_path 提供时）> `"agent-browser"`

- [ ] **Step 1: Write the failing test**

在 `packages/core/tests/test_browser_engine.py` 的 `TestBrowserEngineFactory` 类内追加（`test_error_message_lists_available` 之后）：

```python
    def test_resolve_name_defaults_to_agent_browser(self, monkeypatch):
        monkeypatch.delenv("SHANNON_BROWSER_ENGINE", raising=False)
        assert BrowserEngineFactory.resolve_name(None) == "agent-browser"

    def test_resolve_name_env_overrides_default(self, monkeypatch):
        monkeypatch.setenv("SHANNON_BROWSER_ENGINE", "playwright")
        assert BrowserEngineFactory.resolve_name(None) == "playwright"

    def test_resolve_name_reads_config(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SHANNON_BROWSER_ENGINE", raising=False)
        cfg = tmp_path / "config.yaml"
        cfg.write_text("browser_engine: playwright\n")
        assert BrowserEngineFactory.resolve_name(str(cfg)) == "playwright"

    def test_resolve_name_env_beats_config(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SHANNON_BROWSER_ENGINE", "agent-browser")
        cfg = tmp_path / "config.yaml"
        cfg.write_text("browser_engine: playwright\n")
        assert BrowserEngineFactory.resolve_name(str(cfg)) == "agent-browser"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/test_browser_engine.py::TestBrowserEngineFactory::test_resolve_name_defaults_to_agent_browser -v`
Expected: FAIL with `AttributeError: type object 'BrowserEngineFactory' has no attribute 'resolve_name'`

- [ ] **Step 3: Implement `resolve_name`**

在 `packages/core/src/shannon_core/services/browser_engine.py` 文件顶部 import 区，把 `from __future__ import annotations` 之下补充标准库 import（若尚无）：

```python
import os
```

在 `BrowserEngineFactory.get_engine` classmethod 之后追加：

```python
    @classmethod
    def resolve_name(cls, config_path: str | None = None) -> str:
        """Resolve the effective browser engine name.

        Priority (matches ``config/parser.py`` env-override semantics):
        1. ``SHANNON_BROWSER_ENGINE`` env var (highest)
        2. ``browser_engine`` field parsed from *config_path* (when provided)
        3. Default ``"agent-browser"``
        """
        env_engine = os.environ.get("SHANNON_BROWSER_ENGINE")
        if env_engine:
            return env_engine.strip()
        if config_path:
            try:
                from shannon_core.config.parser import parse_config

                cfg = parse_config(config_path)
                if cfg.browser_engine:
                    return cfg.browser_engine
            except Exception:
                # Config unreadable → fall through to default rather than crash
                # the preflight gate. The workflow's hard check_available()
                # will surface real config errors later.
                pass
        return "agent-browser"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/test_browser_engine.py::TestBrowserEngineFactory -v`
Expected: PASS（4 个新用例 + 既有 Factory 用例）

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/services/browser_engine.py \
        packages/core/tests/test_browser_engine.py
git commit -m "feat(core): BrowserEngineFactory.resolve_name 统一引擎名解析（env>config>默认）"
```

---

### Task 3: P1b — `ensure_browser_engine` helper + CLI 闸口引擎无关化

把"解析引擎 + 查 binary + 前置检查"封装成可单测的 helper，CLI 闸口改调它。

**Files:**
- Modify: `packages/core/src/shannon_core/runtime/prerequisites.py`（加 helper）
- Modify: `packages/blackbox/src/shannon_blackbox/cli/main.py:130-131`（闸口改调 helper）
- Create: `packages/core/tests/test_prerequisites.py`
- Test: `packages/core/tests/test_prerequisites.py`

**Interfaces:**
- Consumes: `BrowserEngineFactory.resolve_name`（Task 2）、`BrowserEngine.cli_binary`（Task 1）、`ensure_prerequisite`
- Produces: `ensure_browser_engine(config_path: str | None = None, *, profile: str = "blackbox") -> None`

- [ ] **Step 1: Write the failing test**

新建 `packages/core/tests/test_prerequisites.py`：

```python
"""Tests for browser-engine-aware prerequisite checks."""

from __future__ import annotations

import pytest

from shannon_core.runtime import prerequisites


def test_ensure_browser_engine_checks_agent_browser_by_default(monkeypatch):
    """No config, no env → default agent-browser → check 'agent-browser' binary."""
    import shannon_core.services.engines  # noqa: F401  (register engines)
    monkeypatch.delenv("SHANNON_BROWSER_ENGINE", raising=False)
    monkeypatch.delenv("SHANNON_SKIP_PREREQUISITES", raising=False)

    captured = {}
    monkeypatch.setattr(
        prerequisites,
        "ensure_prerequisite",
        lambda name, *, profile: captured.update(name=name, profile=profile),
    )

    prerequisites.ensure_browser_engine(None)

    assert captured["name"] == "agent-browser"
    assert captured["profile"] == "blackbox"


def test_ensure_browser_engine_env_selects_playwright(monkeypatch):
    """SHANNON_BROWSER_ENGINE=playwright → check 'playwright-cli' binary."""
    import shannon_core.services.engines  # noqa: F401
    monkeypatch.setenv("SHANNON_BROWSER_ENGINE", "playwright")

    captured = {}
    monkeypatch.setattr(
        prerequisites,
        "ensure_prerequisite",
        lambda name, *, profile: captured.update(name=name, profile=profile),
    )

    prerequisites.ensure_browser_engine(None)

    assert captured["name"] == "playwright-cli"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/test_prerequisites.py -v`
Expected: FAIL with `AttributeError: module 'shannon_core.runtime.prerequisites' has no attribute 'ensure_browser_engine'`

- [ ] **Step 3: Implement `ensure_browser_engine`**

在 `packages/core/src/shannon_core/runtime/prerequisites.py` 文件末尾追加：

```python
def ensure_browser_engine(
    config_path: str | None = None,
    *,
    profile: str = "blackbox",
) -> None:
    """Resolve the active browser engine and check its CLI binary.

    Engine name resolution (env > config > default) mirrors the workflow's own
    resolution, so the preflight gate checks the *same* binary the run will
    actually use — not a hardcoded ``playwright-cli``.

    Args:
        config_path: Optional YAML config path to read ``browser_engine`` from.
        profile: bootstrap.sh profile passed through to :func:`ensure_prerequisite`.
    """
    import shannon_core.services.engines  # noqa: F401  (ensure engines registered)
    from shannon_core.services.browser_engine import BrowserEngineFactory

    engine_name = BrowserEngineFactory.resolve_name(config_path)
    engine = BrowserEngineFactory.get_engine(engine_name)
    ensure_prerequisite(engine.cli_binary, profile=profile)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/test_prerequisites.py -v`
Expected: PASS（2 个用例）

- [ ] **Step 5: Rewire CLI gate**

编辑 `packages/blackbox/src/shannon_blackbox/cli/main.py`，把：

```python
    from shannon_core.runtime.prerequisites import ensure_prerequisite
    ensure_prerequisite("playwright-cli", profile="blackbox")
```

改为：

```python
    from shannon_core.runtime.prerequisites import ensure_browser_engine
    ensure_browser_engine(input.config_path, profile="blackbox")
```

- [ ] **Step 6: Smoke-check the CLI import path**

Run: `uv run python -c "from shannon_blackbox.cli.main import start; print('ok')"`
Expected: 打印 `ok`（验证改接线无 import/语法错误）

- [ ] **Step 7: Commit**

```bash
git add packages/core/src/shannon_core/runtime/prerequisites.py \
        packages/blackbox/src/shannon_blackbox/cli/main.py \
        packages/core/tests/test_prerequisites.py
git commit -m "fix(blackbox): CLI 前置闸口引擎无关化（ensure_browser_engine 按配置查 binary）"
```

---

### Task 4: P2 — 默认引擎改 agent-browser + 连带测试

三处默认值改 agent-browser；同步调整依赖默认引擎的断言。

**Files:**
- Modify: `packages/core/src/shannon_core/models/config.py:73`
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py:118`
- Modify: `packages/core/src/shannon_core/prompts/manager.py:98`
- Modify: `packages/core/tests/test_prompt_manager.py`（:400-411、:414-424、:427-436）
- Modify: `packages/blackbox/tests/test_workflows.py:179-185`
- Test: 上述两个测试文件

**Interfaces:**
- 无新接口；仅默认值变更。

- [ ] **Step 1: Update the failing tests first (they encode the new default)**

编辑 `packages/core/tests/test_prompt_manager.py`：

(a) `test_browser_commands_injected`（约 :400）——默认引擎现是 agent-browser，把断言与注释改为：

```python
    assert "{{BROWSER_COMMANDS}}" not in result
    # Default engine is agent-browser, so reference should mention agent-browser
    assert "agent-browser" in result.lower()
```

(b) `test_browser_session_id_variable`（约 :414）——此测的是 session id 解析（与引擎无关），显式指定 playwright 保持 `-s=` 断言语义。在传入 variables 加 `"browser_engine": "playwright"`，并把注释改为：

```python
    result = manager.load_sync("sid-test", {
        "web_url": "https://example.com",
        "repo_path": "/r",
        "browser_engine": "playwright",
        "browser_session_id": "custom-sess",
    })
    # playwright engine uses -s=<id> format
    assert "-s=custom-sess" in result
```

(c) `test_playwright_session_backward_compat`（约 :427）——同理显式指定 playwright：

```python
    result = manager.load_sync("pw-compat-test", {
        "web_url": "https://example.com",
        "repo_path": "/r",
        "browser_engine": "playwright",
        "playwright_session": "legacy-sess",
    })
    assert "-s=legacy-sess" in result
```

编辑 `packages/blackbox/tests/test_workflows.py` 的 `test_default_engine_without_config`（约 :179），反映新默认：

```python
    def test_default_engine_without_config(self):
        """Without config, engine defaults to agent-browser."""
        import shannon_core.services.engines  # noqa: F401

        engine_name = "agent-browser"
        engine = BrowserEngineFactory.get_engine(engine_name)
        assert engine.name == "agent-browser"
```

- [ ] **Step 2: Run tests to verify they fail (still old defaults in source)**

Run: `uv run pytest packages/core/tests/test_prompt_manager.py::test_browser_commands_injected -v`
Expected: FAIL（源码默认仍 playwright，断言要 agent-browser 不符）

- [ ] **Step 3: Flip the three source defaults**

(a) `packages/core/src/shannon_core/models/config.py` 约 :73：

```python
    browser_engine: BrowserEngineType = "agent-browser"
```

(b) `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` 约 :118：

```python
        engine_name = cfg.browser_engine if cfg else "agent-browser"
```

(c) `packages/core/src/shannon_core/prompts/manager.py` 约 :98：

```python
        engine = BrowserEngineFactory.get_engine(variables.get("browser_engine", "agent-browser"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
uv run pytest packages/core/tests/test_prompt_manager.py -k "browser or playwright_session" -v
uv run pytest packages/blackbox/tests/test_workflows.py -k "engine or default_engine" -v
```
Expected: PASS（含调整后的默认断言用例；既有 `test_browser_engine_variable_selects_engine` 等保持绿）。聚焦 `-k` 避开全文件 pre-existing hang。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/models/config.py \
        packages/blackbox/src/shannon_blackbox/pipeline/workflows.py \
        packages/core/src/shannon_core/prompts/manager.py \
        packages/core/tests/test_prompt_manager.py \
        packages/blackbox/tests/test_workflows.py
git commit -m "feat(blackbox): 默认浏览器引擎切 agent-browser（playwright 保留作 fallback）"
```

---

### Task 5: P1c — bootstrap.sh 加 agent-browser 安装

blackbox/all profile 现在也装 agent-browser，让 fallback 真正可用、CLI 闸口引导有效。

**Files:**
- Modify: `scripts/bootstrap.sh`（加 `install_agent_browser`；blackbox 与 all profile 调用）
- Test: `bash -n` 语法检查 + grep 锚点

**Interfaces:**
- 无 Python 接口；shell 函数 `install_agent_browser`。

- [ ] **Step 1: Add `install_agent_browser` function**

在 `scripts/bootstrap.sh` 的 `install_chromium` 函数之后（`check_docker` 之前，约 :108 后）插入：

```bash
install_agent_browser() {
    if has agent-browser; then
        ok "agent-browser (already installed)"
        return 0
    fi
    if ! confirm "Install agent-browser (default blackbox browser engine)?"; then
        warn "agent-browser skipped"
        return 0
    fi
    echo "Installing agent-browser..."
    if ! npm install -g agent-browser@latest; then
        fail "agent-browser installation failed."
        echo "  Manual: npm install -g agent-browser"
        return 1
    fi
    echo "Downloading Chrome for agent-browser..."
    if ! agent-browser install; then
        fail "agent-browser install (Chrome download) failed."
        echo "  Manual: agent-browser install"
        return 1
    fi
    if has agent-browser; then
        ok "agent-browser installed"
    else
        fail "agent-browser not found after install."
        echo "  Manual: npm install -g agent-browser && agent-browser install"
        return 1
    fi
}
```

- [ ] **Step 2: Wire into blackbox + all profiles**

把 `case "$PROFILE"` 块里的：

```bash
    blackbox)
        install_playwright_cli || FAILED=1
        install_chromium || FAILED=1
        ;;
    all)
        install_gitnexus || FAILED=1
        install_playwright_cli || FAILED=1
        install_chromium || FAILED=1
        check_docker
        ;;
```

改为：

```bash
    blackbox)
        install_agent_browser || FAILED=1
        install_playwright_cli || FAILED=1
        install_chromium || FAILED=1
        ;;
    all)
        install_gitnexus || FAILED=1
        install_agent_browser || FAILED=1
        install_playwright_cli || FAILED=1
        install_chromium || FAILED=1
        check_docker
        ;;
```

- [ ] **Step 3: Verify shell syntax + wiring anchors**

Run:
```bash
bash -n scripts/bootstrap.sh && echo "syntax-ok"
grep -c "install_agent_browser" scripts/bootstrap.sh
grep -A3 '^    blackbox)' scripts/bootstrap.sh
```
Expected: 打印 `syntax-ok`；`grep -c` 输出 `>= 3`（定义 + blackbox + all 三处引用）；blackbox 分支含 `install_agent_browser`。

- [ ] **Step 4: Commit**

```bash
git add scripts/bootstrap.sh
git commit -m "feat(blackbox): bootstrap.sh 加 agent-browser 安装（blackbox/all profile）"
```

---

### Task 6: P3 — formatters 加 agent-browser 命令分支

`maybe_browser_action` 识别 agent-browser 命令并渲染 emoji 短语。agent-browser 命令形如 `agent-browser --session s1 open <url>`（flag 在前、子命令在后，与 playwright-cli 的 `-s=x` 不同）。

**Files:**
- Modify: `packages/core/src/shannon_core/display/formatters.py`（`maybe_browser_action`）
- Test: `packages/core/tests/display/test_formatters.py`

**Interfaces:**
- 无新接口；`maybe_browser_action(params: dict) -> str | None` 行为扩展。

- [ ] **Step 1: Write the failing test**

在 `packages/core/tests/display/test_formatters.py` 的 `test_maybe_browser_action_non_browser_returns_none` 之后追加：

```python
def test_maybe_browser_action_agent_browser_navigate():
    assert maybe_browser_action(
        {"command": "agent-browser --session s1 open https://a.com"}
    ) == "🌐 Navigating to a.com"


def test_maybe_browser_action_agent_browser_click():
    assert maybe_browser_action(
        {"command": "agent-browser --session s1 click @e5"}
    ) == "🖱️ Clicking @e5"


def test_maybe_browser_action_agent_browser_snapshot():
    assert maybe_browser_action(
        {"command": "agent-browser --session s1 snapshot"}
    ) == "📸 Taking page snapshot"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/display/test_formatters.py::test_maybe_browser_action_agent_browser_navigate -v`
Expected: FAIL（现正则只匹配 `playwright-cli`，agent-browser 命令返回 `None`）

- [ ] **Step 3: Add agent-browser branch to `maybe_browser_action`**

把 `packages/core/src/shannon_core/display/formatters.py` 的 `maybe_browser_action` 函数开头（docstring + 取 command + match 那几行）改为：

```python
def maybe_browser_action(params: dict) -> str | None:
    """Parse a browser-CLI Bash command into an emoji phrase. None if not browser.

    Recognises both ``playwright-cli -s=<id> <sub>`` and
    ``agent-browser --session <id> <sub>`` command shapes.
    """
    command = params.get("command", "") if isinstance(params, dict) else ""

    # agent-browser: `agent-browser --session <id> <subcommand> [args]`
    ab_match = re.match(
        r"agent-browser\s+(?:--session\s+\S+\s+)?(\S+)(?:\s+(.*))?", command
    )
    # playwright-cli: `playwright-cli -s=<id> <subcommand> [args]`
    pw_match = re.match(r"playwright-cli\s+(?:-s=\S+\s+)?(\S+)(?:\s+(.*))?", command)

    match = ab_match or pw_match
    if not match:
        return None
    subcommand, args = match.group(1), (match.group(2) or "").strip()
```

（函数体后半的 `if subcommand in (...)` 分派逻辑保持不变。）

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/display/test_formatters.py -v`
Expected: PASS（3 个新 agent-browser 用例 + 既有 playwright-cli 用例 + non-browser 返回 None）

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/display/formatters.py \
        packages/core/tests/display/test_formatters.py
git commit -m "feat(display): maybe_browser_action 识别 agent-browser 命令分支"
```

---

## 收尾验证

- [ ] **全量回归（仅改动相关子集）**

Run:
```bash
uv run pytest packages/core/tests/test_browser_engine.py \
             packages/core/tests/test_prerequisites.py \
             packages/core/tests/test_prompt_manager.py \
             packages/core/tests/display/test_formatters.py \
             packages/blackbox/tests/test_workflows.py -v
```
Expected: 本次新增/调整用例全 PASS；显式测 playwright 引擎本身的既有用例（`test_playwright_*`、`test_factory_returns_playwright_engine` 等）保持绿，证明双引擎都在。

- [ ] **真机冒烟（人工，记录到 memory，不阻塞 merge）**

按 CLAUDE.md §2，agent-browser 现是默认引擎。冒烟：用一个本地靶场（参考 memory `blackbox-scan-local-docker-target`：用宿主局域网 IP，勿用 localhost）跑 `shannon-blackbox start`，确认：
1. 默认（不传 `browser_engine`）走 agent-browser（闸口查 `agent-browser` binary、prompt 注入 agent-browser 命令参考）。
2. `SHANNON_BROWSER_ENGINE=playwright` 能切回 playwright（闸口查 `playwright-cli`）。
3. 缺 binary 时 bootstrap 引导对应引擎安装。

## 非目标（不做）

- 不重构 workflow / manager 既有的 engine name 解析为统一调 `resolve_name`（已正确工作，范围克制）。
- 不删除 playwright 引擎及其测试。
- 不触碰白盒双轨不变量（CLAUDE.md §1）。
- 不修 pre-existing 的 `session_flag`/`session_flags` 命名不一致。
