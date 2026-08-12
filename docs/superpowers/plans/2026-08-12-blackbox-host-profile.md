# 黑盒扫描 HOST 档案 + per-scan 本地代理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让多用户黑盒扫描能 per-scan 切换 HOST（同域名映射不同 IP）：新增 per-workspace 的 HOST 档案库（存 `域名→IP` 映射，支持手填或从 GET 链接导入 `/etc/hosts`），每个黑盒扫描起一个 per-scan 本地代理（proxy.py + 自定义 DNS 插件），让黑盒所有 HTTP 出口（bash+curl / agent-browser / playwright / web_fetch / preflight）统一走该代理，端口级隔离并发互不冲突，不动单 worker 架构。

**Architecture:** 三段式穿线——① core 起 per-scan proxy.py 子进程（`HostResolverPlugin.resolve_dns` 按 `HOST_MAP_JSON` env 查映射，HTTP+HTTPS CONNECT 均生效，实测确认）；② proxy_url 经 `BlackboxActivityInput` → `executor.execute` → `variables["proxy_url"]` + `run_claude_prompt`，分叉到 OpenAI `ToolContext`（bash/web_fetch 读 ctx）/ Anthropic `_build_sdk_env`（CLI env）/ 浏览器 `session_flag`·`write_config`（经 PromptManager variables）；③ web HOST 档案库（镜像 auth profile，**不加密**）+ 前端镜像 auth profile 链路。preflight 不走代理，IP 来源从 DNS 改查映射表直连。

**Tech Stack:** Python 3.13 / pytest / pydantic / temporalio / proxy.py（新依赖，core 包）。前端：React + TypeScript + vitest + msw + react-i18next。

## Global Constraints

- **`proxy.py` 新依赖落 `packages/core/pyproject.toml`**（`proxy.py>=2.4.10`）——`core/services/host_proxy.py` 在 core 包，worker 经 `supernova-core` 传递依赖获得。改 core/blackbox src **须 rebuild supernova-worker 才生效**（真机验证前必须 rebuild）。
- **只跑改动相关测试文件，勿跑全套 pytest**（全套有预存 hang，见 CLAUDE.md §3）。
- **§1 双轨铁律**：HOST 仅黑盒消费，**不触及白盒 / 双轨 LLM prompt / 确定性层**；不喂任何确定性产物给 LLM 轨。
- **HOST 档案不加密**：IP/域名非敏感凭据，明文落盘 `host-profiles.yaml`（区别 auth profile 的 Fernet）；`HostProfileStore` **不依赖 `CredentialVault`**。
- **per-scan proxy_url 必须经 manager `variables` 按扫描维度传入，不绑 session_id**——`get_session_id(agent_name)` 按 agent 名映射，并发 scan 同 agent 共享 session_id/profile；挂 session_id 会让并发扫描互相覆盖。
- **proxy.py 启动必带** `--num-workers 1 --num-acceptors 1 --local-executor 1`（否则按 CPU fork N×2 进程，per-scan 爆炸）。
- **映射表经 env `HOST_MAP_JSON`**（JSON dict）传给 per-proxy 子进程；插件 stateless + per-request 实例，**不能用 Python 全局变量**（proxy.py 多 worker 进程不共享）。
- **前端测试用** `./node_modules/.bin/vitest`（**勿用 `pnpm test`**，撞 verifyDeps，见 memory `web-frontend-vitest-pnpm-gotcha`）；前端 i18n 测试 `beforeEach(() => i18n.changeLanguage("zh"))`。
- **`PentestError(message, category, retryable=False, error_code=None, context=None)`**（`core/models/errors.py:27`）；`category` 必填 str。
- 向后兼容：HOST 选择**可选**，不启用 = 不起代理、走原 DNS，现有扫描零影响。

---

## File Structure

| 文件 | 责任 | 改动 |
|---|---|---|
| `packages/core/src/supernova_core/services/host_proxy.py` | per-scan proxy.py 子进程管理 + `HostResolverPlugin` + 端口分配 + 探活 | **新增** |
| `packages/core/src/supernova_core/agents/tools_openai/__init__.py` | `ToolContext` dataclass | 加 `proxy_url: str \| None = None` |
| `packages/core/src/supernova_core/agents/tools_openai/exec.py` | bash 工具（curl 出口） | `create_subprocess_shell` 传 env（HTTPS_PROXY/HTTP_PROXY/NO_PROXY） |
| `packages/core/src/supernova_core/agents/tools_openai/web.py` | web_fetch 工具（httpx 出口） | `AsyncClient(proxies=...)` |
| `packages/core/src/supernova_core/utils/security.py` | SSRF/可达性 + IP 解析 | `resolve_host`/`resolve_and_pin_host`/`validate_target_url` 加可选映射表 |
| `packages/core/src/supernova_core/models/errors.py` | `ErrorCode` enum | 加 `PROXY_UNREACHABLE` |
| `packages/core/src/supernova_core/agents/executor.py` | agent 执行 + prompt variables | `execute(..., proxy_url=None)`；variables 注入；下传 run_claude_prompt |
| `packages/core/src/supernova_core/agents/runner.py` | `run_claude_prompt` | 加 `proxy_url`，下传 provider.call |
| `packages/core/src/supernova_core/agents/providers_openai.py` | OpenAI 引擎 | `call(proxy_url)` → `ToolContext(proxy_url=...)` |
| `packages/core/src/supernova_core/agents/providers_anthropic.py` | Anthropic 引擎 | `_build_sdk_env(proxy_url)` 注入 HTTPS_PROXY |
| `packages/core/src/supernova_core/services/browser_engine.py` | `BrowserEngine` Protocol | `session_flag` 加 `proxy_url` 形参 |
| `packages/core/src/supernova_core/services/engines/agent_browser_engine.py` | agent-browser 引擎 | `session_flag` 拼 `--proxy` |
| `packages/core/src/supernova_core/services/engines/playwright_engine.py` | playwright 引擎 | `_build_stealth_config`/`write_config` 加 `launchOptions.proxy` |
| `packages/core/src/supernova_core/prompts/manager.py` | prompt 渲染 | `session_flag(session_id, proxy_url=variables.get("proxy_url"))` |
| `packages/blackbox/src/supernova_blackbox/pipeline/shared.py` | pipeline/activity input | 加 `host_mappings` + `proxy_url` |
| `packages/blackbox/src/supernova_blackbox/pipeline/activities.py` | activity 实现 | 新 `run_host_proxy_setup`/`stop_host_proxy`；preflight 用映射；exploit 传 proxy_url |
| `packages/blackbox/src/supernova_blackbox/pipeline/workflows.py` | workflow 编排 | preflight 前插 setup、finally 插 cleanup、act_input 透传 |
| `packages/blackbox/src/supernova_blackbox/worker.py` | CLI worker 装配 | 注册新 activity |
| `packages/worker/src/supernova_worker/runner.py` | web 常驻 worker 装配 | 注册新 activity（bb_ 别名） |
| `packages/web/src/supernova_web/components/host_profile_store.py` | HOST 档案库（镜像 auth，不加密） | **新增** |
| `packages/web/src/supernova_web/api/host_profiles.py` | HOST 档案 router | **新增** |
| `packages/web/src/supernova_web/app.py` | 装配 | 实例化 store + include router + 注入 scan_manager |
| `packages/web/src/supernova_web/models.py` | `ScanRequest` | 加 `host_profile_id`/`host_url` |
| `packages/web/src/supernova_web/components/scan_manager.py` | 黑盒输入解析 | `_resolve_blackbox_inputs` 解析 host 档案/链接 → mappings |
| `packages/web/frontend/src/api/hostProfiles.ts` | HOST api client | **新增** |
| `packages/web/frontend/src/api/types.ts` | 前端类型 | 加 `HostProfile`/`HostMapping` + ScanRequest 字段 |
| `packages/web/frontend/src/pages/HostProfilesPage.tsx` | 档案管理页 | **新增** |
| `packages/web/frontend/src/components/HostProfileDialog.tsx` | 档案表单 | **新增** |
| `packages/web/frontend/src/components/host/MappingRows.tsx` | 映射行编辑器 | **新增** |
| `packages/web/frontend/src/components/ScanFormFields.tsx` | 扫描表单 | 加 HOST 选择区 |
| `packages/web/frontend/src/pages/ScanNewPage.tsx` | 提交扫描 | `buildBody` 加 host 字段 |
| `packages/web/frontend/src/router.tsx` + `routes/WorkspaceDetail/index.tsx` | 路由 + 命令栏 | 加 host-profiles |
| `packages/web/frontend/src/locales/{zh,en}.json` | i18n | 加 `hostProfiles.*` / `scan.host.*` |

---

## Phase 1 — core：代理基础设施 + 出口注入

### Task 1: per-scan proxy.py 子进程管理（`host_proxy.py`）

**Files:**
- Create: `packages/core/src/supernova_core/services/host_proxy.py`
- Test: `packages/core/tests/services/test_host_proxy.py`
- 资产参考: `scripts/validate_host_proxy_probe/host_resolver.py`（实测过的插件，直接搬）

**Interfaces:**
- Produces:
  - `class HostResolverPlugin(HttpProxyBasePlugin)` —— `resolve_dns(self, host, port) -> tuple[str|None, HostPort|None]`，读 `os.environ["HOST_MAP_JSON"]`
  - `@dataclass class ProxyHandle`：`proxy_url: str` / `process: asyncio.subprocess.Process` / `port: int` / `port_file: str`
  - `async def start_host_proxy(mappings: dict[str, str]) -> ProxyHandle` —— 起 proxy.py 子进程（`--port 0 --port-file` + 必需 worker flag），从 port-file 读端口，探活失败 raise `PentestError(category="preflight", error_code=ErrorCode.PROXY_UNREACHABLE)`
  - `async def stop_host_proxy(handle: ProxyHandle) -> None` —— SIGTERM→wait→SIGKILL 升级，best-effort（绝不 raise），删 port-file
  - `async def _probe(handle: ProxyHandle) -> bool` —— 经代理 HEAD 一个回环探测点（`http://127.0.0.1:<port>` 自身或一个映射的 test host），断言代理监听

- [ ] **Step 1: 写失败测试**

```python
# packages/core/tests/services/test_host_proxy.py
import asyncio, json, os, socket
from pathlib import Path
import pytest
from supernova_core.services.host_proxy import start_host_proxy, stop_host_proxy, HostResolverPlugin

def test_plugin_resolve_dns_hit(monkeypatch):
    """插件命中映射返回 IP；未命中返回 (None,None) 走默认 DNS。"""
    monkeypatch.setenv("HOST_MAP_JSON", json.dumps({"target.test": "127.0.0.1"}))
    p = HostResolverPlugin.__new__(HostResolverPlugin)  # 不走 __init__（需 socket）
    assert p.resolve_dns("target.test", 80) == ("127.0.0.1", None)
    assert p.resolve_dns("unmapped.test", 80) == (None, None)

@pytest.mark.asyncio
async def test_start_stop_proxy_lifecycle(monkeypatch, tmp_path):
    """起代理→拿 proxy_url→停（进程退出、port-file 删）。"""
    mappings = {"target.test": "10.0.0.1"}
    handle = await start_host_proxy(mappings)
    assert handle.proxy_url.startswith("http://127.0.0.1:")
    assert handle.port > 0
    # 子进程存活
    assert handle.process.returncode is None
    await stop_host_proxy(handle)
    # 进程已终止
    assert handle.process.returncode is not None
    assert not Path(handle.port_file).exists()

@pytest.mark.asyncio
async def test_start_proxy_env_isolation(monkeypatch):
    """两个 proxy 各持独立映射 env（per-scan 隔离基石）。"""
    hA = await start_host_proxy({"target.test": "10.0.0.1"})
    hB = await start_host_proxy({"target.test": "10.0.0.2"})
    assert hA.port != hB.port
    await stop_host_proxy(hA)
    await stop_host_proxy(hB)
```

> 探活 `_probe` 用真实 HTTP 难单测（需起目标 server）；插件 `resolve_dns` + 起停生命周期是核心可单测面。`start_host_proxy` 实测依赖 proxy.py 装好——若 CI 无 proxy.py，用 `pytest.importorskip("proxy")` 跳过 lifecycle 测试。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd packages/core && python -m pytest tests/services/test_host_proxy.py -v`
Expected: FAIL（模块不存在 / ImportError）

- [ ] **Step 3: 实现 `host_proxy.py`**

```python
# packages/core/src/supernova_core/services/host_proxy.py
"""Per-scan 本地代理：proxy.py 子进程 + 自定义 DNS 插件。

每个黑盒扫描起一个独立 proxy.py 子进程（bind 127.0.0.1:<OS 端口>），
持该扫描的域名→IP 映射（经 env HOST_MAP_JSON 注入），让黑盒所有 HTTP
出口统一走该代理，per-scan 端口级隔离。实测（2026-08-12）resolve_dns 对
HTTP 请求与 HTTPS CONNECT 隧道均生效。"""
from __future__ import annotations
import asyncio, json, os, tempfile, time
from dataclasses import dataclass
from pathlib import Path

from proxy.http.proxy import HttpProxyBasePlugin
from proxy.common.types import HostPort

from supernova_core.models.errors import PentestError, ErrorCode

# 插件模块所在目录 = 本文件目录；proxy.py --plugins 经 PYTHONPATH 加载
_PLUGIN_DIR = str(Path(__file__).resolve().parent)
# 必需 flag：否则 proxy.py 按 CPU fork N×2 进程，per-scan 爆炸
_REQUIRED_FLAGS = ["--num-workers", "1", "--num-acceptors", "1", "--local-executor", "1"]


class HostResolverPlugin(HttpProxyBasePlugin):
    """按域名查映射表返回指定 IP；未命中走 proxy.py 默认 DNS。
    映射表经 env HOST_MAP_JSON 注入（每个 proxy 子进程独立 env → per-scan 隔离）。
    插件 stateless + per-request 实例，不能用全局变量。"""

    def resolve_dns(self, host: str, port: int) -> tuple[str | None, HostPort | None]:
        try:
            mapping = json.loads(os.environ.get("HOST_MAP_JSON", "{}"))
        except (json.JSONDecodeError, TypeError):
            mapping = {}
        return (mapping.get(host), None)  # 命中→映射 IP；未命中→(None,None) 默认 DNS


@dataclass
class ProxyHandle:
    proxy_url: str
    process: asyncio.subprocess.Process
    port: int
    port_file: str


async def start_host_proxy(mappings: dict[str, str]) -> ProxyHandle:
    """起 proxy.py 子进程，bind 127.0.0.1:<OS 分配端口>，加载映射。
    探活失败 raise PentestError → 扫描 fail-fast。"""
    port_file = tempfile.NamedTemporaryFile(
        suffix=".port", delete=False, prefix="host_proxy_"
    ).name
    os.unlink(port_file)  # proxy.py 要求文件不存在
    env = {
        **os.environ,
        "HOST_MAP_JSON": json.dumps(mappings),
        "PYTHONPATH": _PLUGIN_DIR + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    cmd = (
        ["proxy", "--plugins", "host_resolver.HostResolverPlugin",
         "--hostname", "127.0.0.1", "--port", "0", "--port-file", port_file,
         "--log-level", "WARNING"]
        + _REQUIRED_FLAGS
    )
    proc = await asyncio.create_subprocess_exec(
        *cmd, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    # 等 port-file 写出
    for _ in range(40):
        if Path(port_file).exists() and Path(port_file).stat().st_size > 0:
            break
        if proc.returncode is not None:  # 进程已死
            stderr = await proc.stderr.read() if proc.stderr else b""
            raise PentestError(
                f"host proxy exited prematurely: {stderr.decode(errors='replace')[:300]}",
                category="preflight", error_code=ErrorCode.PROXY_UNREACHABLE,
            )
        await asyncio.sleep(0.5)
    else:
        proc.kill()
        raise PentestError("host proxy port-file timeout", category="preflight",
                           error_code=ErrorCode.PROXY_UNREACHABLE)
    port = int(Path(port_file).read_text().strip())
    handle = ProxyHandle(f"http://127.0.0.1:{port}", proc, port, port_file)
    if not await _probe(handle):
        await stop_host_proxy(handle)
        raise PentestError(f"host proxy probe failed on {handle.proxy_url}",
                           category="preflight", error_code=ErrorCode.PROXY_UNREACHABLE)
    return handle


async def _probe(handle: ProxyHandle) -> bool:
    """探活：代理端口可连即视为存活（代理自身 accept）。"""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", handle.port), timeout=3)
        writer.close()
        await writer.wait_closed()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


async def stop_host_proxy(handle: ProxyHandle) -> None:
    """SIGTERM→wait→SIGKILL 升级，best-effort（绝不 raise）。"""
    try:
        if handle.process.returncode is None:
            handle.process.terminate()
            try:
                await asyncio.wait_for(handle.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                handle.process.kill()
                await handle.process.wait()
    except Exception:
        pass
    try:
        Path(handle.port_file).unlink(missing_ok=True)
    except Exception:
        pass
```

> 注：`--plugins host_resolver.HostResolverPlugin` 要求 proxy.py 能 import 到 `host_resolver` 模块名。实现时把 `HostResolverPlugin` 同时写到一个名为 `host_resolver.py` 的文件，或用 `--plugins` 指向 `supernova_core.services.host_proxy.HostResolverPlugin`（proxy.py 支持完整模块路径，PYTHONPATH 已含 core src）。**Task 1 实现时二选一并在测试里锁定**——推荐后者（`supernova_core.services.host_proxy.HostResolverPlugin`），无需额外文件。把 cmd 里 `"host_resolver.HostResolverPlugin"` 改为 `"supernova_core.services.host_proxy.HostResolverPlugin"`。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd packages/core && python -m pytest tests/services/test_host_proxy.py -v`
Expected: PASS（plugin 单测必过；lifecycle 测试需 proxy.py 装好，CI 无则 `importorskip`）

- [ ] **Step 5: 加依赖 + commit**

```bash
# packages/core/pyproject.toml dependencies 段加：
#   "proxy.py>=2.4.10",
git add packages/core/src/supernova_core/services/host_proxy.py \
        packages/core/tests/services/test_host_proxy.py \
        packages/core/src/supernova_core/models/errors.py packages/core/pyproject.toml
git commit -m "feat(core): per-scan host proxy (proxy.py + HostResolverPlugin)"
```

> `ErrorCode` 加 `PROXY_UNREACHABLE` 成员（`models/errors.py` enum，L4-23 段尾追加 `PROXY_UNREACHABLE`）。先改 errors.py 再跑测试。

---

### Task 2: 工具出口注入（ToolContext + bash env + web_fetch proxies）

**Files:**
- Modify: `packages/core/src/supernova_core/agents/tools_openai/__init__.py:12-18`（ToolContext）
- Modify: `packages/core/src/supernova_core/agents/tools_openai/exec.py:24-43`（_bash_impl）
- Modify: `packages/core/src/supernova_core/agents/tools_openai/web.py:25-37`（_web_fetch_impl）
- Test: `packages/core/tests/agents/tools_openai/test_proxy_injection.py`

**Interfaces:**
- Consumes: `ToolContext`（Task 2 自身扩展）
- Produces: `ToolContext.proxy_url: str | None`；`_bash_impl` 与 `_web_fetch_impl` 读 `ctx.context.proxy_url` 注入代理

- [ ] **Step 1: 写失败测试**

```python
# packages/core/tests/agents/tools_openai/test_proxy_injection.py
import asyncio, os
from unittest.mock import patch, AsyncMock
import pytest
from agents import RunContextWrapper
from supernova_core.agents.tools_openai import ToolContext
from supernova_core.agents.tools_openai.exec import _bash_impl
from supernova_core.agents.tools_openai.web import _web_fetch_impl

def test_tool_context_has_proxy_url_field():
    ctx = ToolContext(cwd="/tmp", proxy_url="http://127.0.0.1:8080")
    assert ctx.proxy_url == "http://127.0.0.1:8080"
    assert ToolContext(cwd="/tmp").proxy_url is None  # 默认 None，向后兼容

@pytest.mark.asyncio
async def test_bash_impl_injects_proxy_env(monkeypatch):
    """有 proxy_url 时，子进程 env 含 HTTPS_PROXY/HTTP_PROXY/NO_PROXY。"""
    captured = {}
    async def fake_shell(cmd, **kw):
        captured["env"] = kw.get("env")
        captured["cmd"] = cmd
        class P:
            stdout = b"ok\n"
            returncode = 0
            async def communicate(self): return (b"ok\n", b"")
            async def wait(self): return 0
        return P()
    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_shell)
    ctx = RunContextWrapper(ToolContext(cwd="/tmp", proxy_url="http://127.0.0.1:9090"))
    await _bash_impl(ctx, "curl http://x.test")
    assert captured["env"]["HTTPS_PROXY"] == "http://127.0.0.1:9090"
    assert captured["env"]["HTTP_PROXY"] == "http://127.0.0.1:9090"
    assert "NO_PROXY" in captured["env"]

@pytest.mark.asyncio
async def test_bash_impl_no_proxy_when_ctx_none(monkeypatch):
    """无 proxy_url 时不传 env（继承 worker env，向后兼容）。"""
    captured = {}
    async def fake_shell(cmd, **kw):
        captured["env"] = kw.get("env")
        class P:
            stdout = b""
            returncode = 0
            async def communicate(self): return (b"", b"")
            async def wait(self): return 0
        return P()
    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_shell)
    ctx = RunContextWrapper(ToolContext(cwd="/tmp"))  # proxy_url=None
    await _bash_impl(ctx, "echo hi")
    assert captured["env"] is None  # 不注入

@pytest.mark.asyncio
async def test_web_fetch_impl_passes_proxies(monkeypatch):
    """有 proxy_url 时 httpx.AsyncClient 收到 proxies=。"""
    captured = {}
    class FakeClient:
        def __init__(self, **kw): captured["kwargs"] = kw
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, **kw):
            class R:
                status_code = 200; text = "body"
                def raise_for_status(self): pass
            return R()
    monkeypatch.setattr("supernova_core.agents.tools_openai.web.httpx.AsyncClient", FakeClient)
    ctx = RunContextWrapper(ToolContext(cwd="/tmp", proxy_url="http://127.0.0.1:9090"))
    await _web_fetch_impl(ctx, "http://x.test")
    assert captured["kwargs"].get("proxies") == "http://127.0.0.1:9090"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd packages/core && python -m pytest tests/agents/tools_openai/test_proxy_injection.py -v`
Expected: FAIL（无 proxy_url 字段 / env 为 None / proxies 缺失）

- [ ] **Step 3: 实现**

`tools_openai/__init__.py`（ToolContext 加字段）:
```python
@dataclass
class ToolContext:
    cwd: str
    subagent_run: Callable[[str], Awaitable[str]] | None = None
    proxy_url: str | None = None   # Task 2: per-scan 代理（黑盒 HOST 切换）
```

`exec.py`（_bash_impl 注入 env）—— 顶部 `import os`，改 `create_subprocess_shell` 调用:
```python
# _bash_impl 内，构造 proc 前：
env = None
proxy_url = ctx.context.proxy_url
if proxy_url:
    env = {
        **os.environ,
        "HTTPS_PROXY": proxy_url,
        "HTTP_PROXY": proxy_url,
        "NO_PROXY": "127.0.0.1,localhost",  # LLM/temporal host 由 Task 4 补全
    }
proc = await asyncio.create_subprocess_shell(
    command, cwd=cwd, env=env,
    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
)
```

`web.py`（_web_fetch_impl 加 proxies）:
```python
proxy_url = ctx.context.proxy_url
client_kwargs = {"timeout": 30, "follow_redirects": True}
if proxy_url:
    client_kwargs["proxies"] = proxy_url
client = httpx.AsyncClient(**client_kwargs)
```

> `web_search`（web.py:60）同样从 ctx 取 proxy_url 加 proxies（同一文件同模式，一并改）。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd packages/core && python -m pytest tests/agents/tools_openai/test_proxy_injection.py -v`
Expected: PASS

- [ ] **Step 5: commit**

```bash
git add packages/core/src/supernova_core/agents/tools_openai/__init__.py \
        packages/core/src/supernova_core/agents/tools_openai/exec.py \
        packages/core/src/supernova_core/agents/tools_openai/web.py \
        packages/core/tests/agents/tools_openai/test_proxy_injection.py
git commit -m "feat(core): inject per-scan proxy into bash/web_fetch tools"
```

---

### Task 3: security.py —— IP 来源从 DNS 改查映射表

**Files:**
- Modify: `packages/core/src/supernova_core/utils/security.py:14-29,44-85,124-132`
- Test: `packages/core/tests/utils/test_security_host_mapping.py`

**Interfaces:**
- Consumes: 调用方传入可选 `host_mappings: dict[str,str] | None`
- Produces: `resolve_host(url, host_mappings=None)` / `resolve_and_pin_host(url, host_mappings=None)` / `validate_target_url(url, host_mappings=None)` —— 命中映射用映射 IP，未命中走原 DNS；SSRF/loopback 拦截不变

- [ ] **Step 1: 写失败测试**

```python
# packages/core/tests/utils/test_security_host_mapping.py
import pytest
from supernova_core.utils.security import resolve_host, validate_target_url, check_loopback

def test_resolve_host_uses_mapping_when_hit():
    """映射命中 hostname → 直接返回映射 IP，不走 DNS。"""
    ip = resolve_host("http://target.test", host_mappings={"target.test": "10.0.0.1"})
    assert ip == "10.0.0.1"

def test_resolve_host_falls_back_to_dns_when_unmapped(monkeypatch):
    """未命中映射 → 走原 DNS（mock 成一个公网 IP）。"""
    import supernova_core.utils.security as sec
    monkeypatch.setattr(sec.socket, "getaddrinfo",
                        lambda *a, **k: [(0,0,0,0,("93.184.216.34",0))])
    ip = resolve_host("http://example.com", host_mappings={"other.test": "10.0.0.1"})
    assert ip == "93.184.216.34"

def test_resolve_host_no_mapping_behaves_as_before(monkeypatch):
    """host_mappings=None → 完全等同旧行为（向后兼容）。"""
    import supernova_core.utils.security as sec
    monkeypatch.setattr(sec.socket, "getaddrinfo",
                        lambda *a, **k: [(0,0,0,0,("1.2.3.4",0))])
    assert resolve_host("http://example.com") == "1.2.3.4"

def test_validate_target_url_mapping_bypasses_dns(monkeypatch):
    """映射 IP 直接用，不经 DNS（避免内网域名 DNS 解析失败）。"""
    import supernova_core.utils.security as sec
    called = []
    monkeypatch.setattr(sec.socket, "getaddrinfo",
                        lambda *a, **k: called.append(1) or [(0,0,0,0,("9.9.9.9",0))])
    ip = validate_target_url("http://target.test", host_mappings={"target.test": "10.0.0.1"})
    assert ip == "10.0.0.1"
    assert called == []  # 没调 DNS

def test_mapping_loopback_still_blocked():
    """映射里填 127.x → preflight 照拦（SSRF/loopback 不退化）。"""
    with pytest.raises(Exception):  # PentestError
        validate_target_url("http://target.test", host_mappings={"target.test": "127.0.0.1"})
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd packages/core && python -m pytest tests/utils/test_security_host_mapping.py -v`
Expected: FAIL（`resolve_host` 不接受 host_mappings）

- [ ] **Step 3: 实现**

`security.py:14-29` `resolve_host` 加可选映射:
```python
def resolve_host(url: str, host_mappings: dict[str, str] | None = None) -> str | None:
    hostname = urlparse(url).hostname
    if not hostname:
        return None
    # 命中映射 → 直接返回映射 IP，跳过 DNS
    if host_mappings and hostname in host_mappings:
        return host_mappings[hostname]
    addrinfos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    for _f, _t, _p, _c, sockaddr in addrinfos:
        return sockaddr[0]
    return None
```

`resolve_and_pin_host`（L44-85）加 `host_mappings: dict[str,str] | None = None` 形参，透传给内部 `resolve_host(url, host_mappings)` 调用（SSRF/loopback 检查**不动**——映射 IP 仍过 `check_ssrf`/`check_loopback`，故 127.x/169.254.x 照拦）。

`validate_target_url`（L124-132）加 `host_mappings` 形参透传:
```python
def validate_target_url(url: str, host_mappings: dict[str, str] | None = None) -> str:
    pinned_ip, _host = resolve_and_pin_host(url, host_mappings=host_mappings)
    return pinned_ip
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd packages/core && python -m pytest tests/utils/test_security_host_mapping.py -v`
Expected: PASS。另跑既有 security 测试确认无回归：`python -m pytest tests/utils/ -k security -v`（只相关文件）。

- [ ] **Step 5: commit**

```bash
git add packages/core/src/supernova_core/utils/security.py \
        packages/core/tests/utils/test_security_host_mapping.py
git commit -m "feat(core): security.py IP source from host mapping (fallback DNS)"
```

---

### Task 4: executor → runner → provider 穿线 proxy_url

**Files:**
- Modify: `packages/core/src/supernova_core/agents/executor.py:71-130`（execute 签名 + variables + 下传）
- Modify: `packages/core/src/supernova_core/agents/runner.py:117-160`（run_claude_prompt 签名 + 下传 provider.call）
- Modify: `packages/core/src/supernova_core/agents/providers_openai.py:239,283-312`（call + ToolContext）
- Modify: `packages/core/src/supernova_core/agents/providers_anthropic.py:78-89,193-265,315`（call + _build_sdk_env）
- Test: `packages/core/tests/agents/test_proxy_threading.py`

**Interfaces:**
- Consumes: activity 传入 `proxy_url: str | None`
- Produces: `execute(proxy_url=)` → `run_claude_prompt(proxy_url=)` → `provider.call(proxy_url=)`；OpenAI 构造 `ToolContext(proxy_url=)`；Anthropic `_build_sdk_env(proxy_url=)` 注入 env；`variables["proxy_url"]` 供浏览器（Task 5）

- [ ] **Step 1: 写失败测试**

```python
# packages/core/tests/agents/test_proxy_threading.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from supernova_core.agents.executor import AgentExecutor

@pytest.mark.asyncio
async def test_execute_threads_proxy_url_into_variables(monkeypatch):
    """execute(proxy_url=) 把 proxy_url 注入 prompt variables（供浏览器 session_flag）。"""
    captured = {}
    async def fake_load(self, template, variables, **kw):
        captured["variables"] = variables
        return "prompt"
    monkeypatch.setattr("supernova_core.agents.executor.PromptManager.load_sync", fake_load)
    # mock run_claude_prompt 不实际跑
    monkeypatch.setattr("supernova_core.agents.executor.run_claude_prompt",
                        AsyncMock(return_value=MagicMock()))
    pm = MagicMock()
    exe = AgentExecutor(pm)
    # 最小可用调用（deliverables_path 必填）
    await exe.execute("exploitation", repo_path="/tmp/repo",
                      deliverables_path="/tmp/repo/deliverables",
                      proxy_url="http://127.0.0.1:9090")
    assert captured["variables"].get("proxy_url") == "http://127.0.0.1:9090"

@pytest.mark.asyncio
async def test_execute_no_proxy_means_no_proxy_in_vars(monkeypatch):
    """proxy_url=None → variables 无 proxy_url 键（向后兼容）。"""
    captured = {}
    async def fake_load(self, template, variables, **kw):
        captured["variables"] = variables
        return "prompt"
    monkeypatch.setattr("supernova_core.agents.executor.PromptManager.load_sync", fake_load)
    monkeypatch.setattr("supernova_core.agents.executor.run_claude_prompt",
                        AsyncMock(return_value=MagicMock()))
    exe = AgentExecutor(MagicMock())
    await exe.execute("exploitation", repo_path="/tmp/repo",
                      deliverables_path="/tmp/repo/deliverables")
    assert "proxy_url" not in captured["variables"] or captured["variables"].get("proxy_url") in (None, "")
```

> provider 侧穿线（run_claude_prompt → call → ToolContext/_build_sdk_env）用各 provider 现有测试结构补断言：OpenAI 断言 `ToolContext.proxy_url` 透传，Anthropic 断言 `_build_sdk_env` 返回 dict 含 `HTTPS_PROXY`。两 provider 测试文件已存在，按其 fixture 风格加 case。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd packages/core && python -m pytest tests/agents/test_proxy_threading.py -v`
Expected: FAIL（execute 不接受 proxy_url）

- [ ] **Step 3: 实现**

`executor.py:71-89` execute 加形参 + variables 注入 + 下传:
```python
    async def execute(
        self, ...,
        provider_config: dict | None = None,
        queue_root: str | None = None,
        proxy_url: str | None = None,   # Task 4: per-scan 代理穿线
    ) -> AgentMetrics:
        ...
        # L105 variables 构造后、prompt_variables update 前：
        if proxy_url:
            variables["proxy_url"] = proxy_url   # 供 manager L146 session_flag
        ...
        # 下传 run_claude_prompt（L141-154 调用处）加 proxy_url=proxy_url
```

`runner.py:117` run_claude_prompt 加 `proxy_url: str | None = None` 形参，下传 `provider.call(..., proxy_url=proxy_url)`。

`providers_openai.py`:
- `call()`（L283-294）加 `proxy_url: str | None = None` 形参
- L312 主 agent：`context=ToolContext(cwd=cwd, subagent_run=..., proxy_url=proxy_url)`
- L239 子代理：`context=ToolContext(cwd=cwd, proxy_url=proxy_url)`（子代理继承同一 per-scan proxy）

`providers_anthropic.py`:
- `call()`（L78-89）加 `proxy_url` 形参，存到 `self`（如 `self._proxy_url`）或透传 `_build_sdk_env`
- `_build_sdk_env`（L193）加 `proxy_url: str | None = None`，末尾返回前:
```python
        if proxy_url:
            sdk_env["HTTPS_PROXY"] = proxy_url
            sdk_env["HTTP_PROXY"] = proxy_url
            sdk_env["NO_PROXY"] = "127.0.0.1,localhost"
        return sdk_env
```
- L315 `options.env = self._build_sdk_env(proxy_url=...)`（或 call 时传入）

- [ ] **Step 4: 运行测试确认通过**

Run: `cd packages/core && python -m pytest tests/agents/test_proxy_threading.py tests/agents/ -k "proxy or provider" -v`（只相关）
Expected: PASS

- [ ] **Step 5: commit**

```bash
git add packages/core/src/supernova_core/agents/executor.py \
        packages/core/src/supernova_core/agents/runner.py \
        packages/core/src/supernova_core/agents/providers_openai.py \
        packages/core/src/supernova_core/agents/providers_anthropic.py \
        packages/core/tests/agents/test_proxy_threading.py
git commit -m "feat(core): thread proxy_url executor->provider (ToolContext + SDK env)"
```

---

### Task 5: 浏览器引擎 session_flag / write_config 加 proxy_url

**Files:**
- Modify: `packages/core/src/supernova_core/services/browser_engine.py:36-42`（Protocol）
- Modify: `packages/core/src/supernova_core/services/engines/agent_browser_engine.py:117-123`（session_flag）
- Modify: `packages/core/src/supernova_core/services/engines/playwright_engine.py:70-100,143-176`（_build_stealth_config + write_config）
- Modify: `packages/core/src/supernova_core/prompts/manager.py:146`（传 proxy_url 给 session_flag）
- Test: `packages/core/tests/services/engines/test_browser_proxy.py`

**Interfaces:**
- Consumes: `variables["proxy_url"]`（manager.py:146）
- Produces: `BrowserEngine.session_flag(session_id, proxy_url=None) -> str`（两引擎实现）；playwright `write_config(source_dir, session_id, proxy_url=None)` 写 `launchOptions.proxy`

- [ ] **Step 1: 写失败测试**

```python
# packages/core/tests/services/engines/test_browser_proxy.py
from supernova_core.services.engines.agent_browser_engine import AgentBrowserEngine
from supernova_core.services.engines.playwright_engine import _build_stealth_config

def test_agent_browser_session_flag_appends_proxy():
    e = AgentBrowserEngine()
    flag = e.session_flag("scanA", proxy_url="http://127.0.0.1:9090")
    assert "--session scanA" in flag
    assert "--profile .agent-browser/profiles/scanA" in flag
    assert "--proxy http://127.0.0.1:9090" in flag

def test_agent_browser_session_flag_no_proxy_backward_compat():
    e = AgentBrowserEngine()
    flag = e.session_flag("scanA")  # proxy_url=None
    assert "--proxy" not in flag
    assert "--session scanA" in flag

def test_playwright_stealth_config_launchoptions_proxy():
    cfg = _build_stealth_config("/tmp/init.js", session_id="s1",
                                proxy_url="http://127.0.0.1:9090")
    assert cfg["browser"]["launchOptions"]["proxy"]["server"] == "http://127.0.0.1:9090"

def test_playwright_stealth_config_no_proxy_omits_key():
    cfg = _build_stealth_config("/tmp/init.js", session_id="s1")  # proxy_url=None
    assert "proxy" not in cfg["browser"]["launchOptions"]
```

> `manager.py` 单测：mock 一个 engine，断言 `session_flag` 收到 `variables["proxy_url"]`（manager.load_sync 路径）。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd packages/core && python -m pytest tests/services/engines/test_browser_proxy.py -v`
Expected: FAIL（session_flag 不接受 proxy_url）

- [ ] **Step 3: 实现**

`browser_engine.py` Protocol（L36-42）:
```python
class BrowserEngine(Protocol):
    def session_flag(self, session_id: str, proxy_url: str | None = None) -> str: ...
```

`agent_browser_engine.py:117`:
```python
    def session_flag(self, session_id: str, proxy_url: str | None = None) -> str:
        flag = f"--session {session_id} --profile .agent-browser/profiles/{session_id}"
        if proxy_url:
            flag += f" --proxy {proxy_url}"
        return flag
```

`playwright_engine.py:70` `_build_stealth_config`:
```python
def _build_stealth_config(init_script_path: str, session_id: str | None = None,
                          proxy_url: str | None = None) -> dict:
    config: dict = {"browser": {"browserName": "chromium",
        "launchOptions": {"headless": True,
            "args": ["--disable-blink-features=AutomationControlled"],
            "ignoreDefaultArgs": ["--enable-automation"]},
        ...}}
    if proxy_url:
        config["browser"]["launchOptions"]["proxy"] = {"server": proxy_url}
    return config
```

`playwright_engine.py:143` `write_config(self, source_dir, session_id=None, proxy_url=None)` 透传 `proxy_url` 给 `_build_stealth_config`（L173 调用处）。

`manager.py:146`:
```python
        proxy_url = variables.get("proxy_url")
        result = result.replace("{{BROWSER_SESSION_FLAG}}",
                                engine.session_flag(session_id, proxy_url=proxy_url))
```

> playwright `write_config` 触发点（`activities.py:686` write_engine_config_for_session / `playwright_config_writer.py:50`）在 Task 7 / 传参：write_engine_config_for_session 从 input 取 proxy_url 传给 write_config。Task 5 先让 write_config 接受 proxy_url（默认 None 向后兼容），Task 7 接上 input。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd packages/core && python -m pytest tests/services/engines/test_browser_proxy.py tests/prompts/ -k "proxy or browser or session" -v`
Expected: PASS

- [ ] **Step 5: commit**

```bash
git add packages/core/src/supernova_core/services/browser_engine.py \
        packages/core/src/supernova_core/services/engines/*.py \
        packages/core/src/supernova_core/prompts/manager.py \
        packages/core/tests/services/engines/test_browser_proxy.py
git commit -m "feat(core): browser engines accept per-scan proxy (session_flag + launchOptions)"
```

---

## Phase 2 — blackbox pipeline 编排

### Task 6: shared.py —— input 加 host_mappings / proxy_url 字段

**Files:**
- Modify: `packages/blackbox/src/supernova_blackbox/pipeline/shared.py:7-22,82-102`
- Test: `packages/blackbox/tests/pipeline/test_shared_fields.py`

**Interfaces:**
- Consumes: web 层 scan_manager 传入 `host_mappings`
- Produces: `BlackboxPipelineInput.host_mappings: dict[str,str]`；`BlackboxActivityInput.host_mappings: dict[str,str]` + `proxy_url: str | None`

- [ ] **Step 1: 写失败测试**

```python
# packages/blackbox/tests/pipeline/test_shared_fields.py
from supernova_blackbox.pipeline.shared import BlackboxPipelineInput, BlackboxActivityInput

def test_pipeline_input_host_mappings_default_empty():
    inp = BlackboxPipelineInput(web_url="http://x.test")
    assert inp.host_mappings == {}

def test_pipeline_input_host_mappings_set():
    inp = BlackboxPipelineInput(web_url="http://x.test",
                                host_mappings={"x.test": "10.0.0.1"})
    assert inp.host_mappings == {"x.test": "10.0.0.1"}

def test_activity_input_defaults():
    a = BlackboxActivityInput(web_url="http://x.test")
    assert a.host_mappings == {}
    assert a.proxy_url is None

def test_activity_input_proxy_url():
    a = BlackboxActivityInput(web_url="http://x.test", proxy_url="http://127.0.0.1:9090")
    assert a.proxy_url == "http://127.0.0.1:9090"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd packages/blackbox && python -m pytest tests/pipeline/test_shared_fields.py -v`
Expected: FAIL（无 host_mappings 字段）

- [ ] **Step 3: 实现**

`shared.py` 顶部 `from dataclasses import dataclass, field`（若未 import field）。

`BlackboxPipelineInput`（L7-22）末尾加:
```python
    host_mappings: dict[str, str] = field(default_factory=dict)
```

`BlackboxActivityInput`（L82-102）末尾加（都有默认值，不破坏 dataclass 非 default 后跟 default 顺序）:
```python
    host_mappings: dict[str, str] = field(default_factory=dict)
    proxy_url: str | None = None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd packages/blackbox && python -m pytest tests/pipeline/test_shared_fields.py -v`
Expected: PASS

- [ ] **Step 5: commit**

```bash
git add packages/blackbox/src/supernova_blackbox/pipeline/shared.py \
        packages/blackbox/tests/pipeline/test_shared_fields.py
git commit -m "feat(blackbox): add host_mappings/proxy_url to pipeline inputs"
```

---

### Task 7: activities —— host_proxy setup/cleanup + preflight 用映射 + exploit 传 proxy

**Files:**
- Modify: `packages/blackbox/src/supernova_blackbox/pipeline/activities.py`（+ import、+ 2 activity、改 preflight L107-123、改 exploit L247 / endpoint_verify L326 传 proxy）
- Modify: `packages/blackbox/src/supernova_blackbox/pipeline/activities.py:686`（write_engine_config_for_session 传 proxy_url 给 playwright write_config）
- Test: `packages/blackbox/tests/pipeline/test_host_proxy_activities.py`

**Interfaces:**
- Consumes: `BlackboxActivityInput.host_mappings`（setup）、`input.proxy_url`（exploit）
- Produces:
  - `@activity.defn async def run_host_proxy_setup(input: BlackboxActivityInput) -> str` —— 无 mappings 返回 `""`；有 mappings 起 `start_host_proxy`，返回 `proxy_url`；失败 raise `ApplicationFailure`（包装 PentestError）
  - `@activity.defn async def stop_host_proxy(proxy_url: str) -> None` —— best-effort 停

- [ ] **Step 1: 写失败测试**

```python
# packages/blackbox/tests/pipeline/test_host_proxy_activities.py
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from temporalio.exceptions import ApplicationError
from supernova_blackbox.pipeline import activities

@pytest.mark.asyncio
async def test_setup_no_mappings_returns_empty():
    """无 host_mappings → 不起代理，返回 ''（向后兼容）。"""
    inp = MagicMock(); inp.host_mappings = {}
    with patch("supernova_blackbox.pipeline.activities.start_host_proxy") as m:
        result = await activities.run_host_proxy_setup(inp)
        assert result == ""
        m.assert_not_called()

@pytest.mark.asyncio
async def test_setup_starts_proxy_returns_url():
    """有 mappings → 起代理，返回 proxy_url。"""
    inp = MagicMock(); inp.host_mappings = {"x.test": "10.0.0.1"}
    fake_handle = MagicMock(proxy_url="http://127.0.0.1:9090", port=9090)
    with patch("supernova_blackbox.pipeline.activities.start_host_proxy",
               AsyncMock(return_value=fake_handle)), \
         patch("supernova_blackbox.pipeline.activities._PROXY_HANDLES", {}):
        result = await activities.run_host_proxy_setup(inp)
        assert result == "http://127.0.0.1:9090"

@pytest.mark.asyncio
async def test_setup_fail_fast_on_error():
    """代理起不来 → ApplicationFailure（扫描 fail-fast）。"""
    from supernova_core.models.errors import PentestError
    inp = MagicMock(); inp.host_mappings = {"x.test": "10.0.0.1"}
    with patch("supernova_blackbox.pipeline.activities.start_host_proxy",
               AsyncMock(side_effect=PentestError("nope", category="preflight"))):
        with pytest.raises(ApplicationError):
            await activities.run_host_proxy_setup(inp)

@pytest.mark.asyncio
async def test_stop_proxy_best_effort_no_raise():
    """stop 永不 raise（cleanup best-effort）。"""
    with patch("supernova_blackbox.pipeline.activities.stop_host_proxy_func",
               AsyncMock(side_effect=Exception("boom"))):
        await activities.stop_host_proxy("http://127.0.0.1:9090")  # 不 raise
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd packages/blackbox && python -m pytest tests/pipeline/test_host_proxy_activities.py -v`
Expected: FAIL（无 run_host_proxy_setup）

- [ ] **Step 3: 实现**

`activities.py` 顶部 import:
```python
from supernova_core.services.host_proxy import start_host_proxy, stop_host_proxy as stop_host_proxy_func, ProxyHandle
```

模块级注册表（按 proxy_url 记 handle，供 cleanup）:
```python
_PROXY_HANDLES: dict[str, ProxyHandle] = {}
```

新增两 activity（放在 run_blackbox_preflight 附近）:
```python
@activity.defn
async def run_host_proxy_setup(input: BlackboxActivityInput) -> str:
    """起 per-scan 本地代理（若 host_mappings 非空）。返回 proxy_url（空 mappings 返回 ''）。"""
    if not input.host_mappings:
        return ""
    try:
        handle = await start_host_proxy(input.host_mappings)
        _PROXY_HANDLES[handle.proxy_url] = handle
        return handle.proxy_url
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def stop_host_proxy(proxy_url: str) -> None:
    """best-effort 停代理（绝不 raise）。"""
    if not proxy_url:
        return
    handle = _PROXY_HANDLES.pop(proxy_url, None)
    if handle:
        try:
            await stop_host_proxy_func(handle)
        except Exception:
            pass  # cleanup best-effort
```

改 `run_blackbox_preflight`（L111-112）用映射:
```python
        pinned_ip = validate_target_url(input.web_url, host_mappings=input.host_mappings)
```

改 exploit activity 调 `executor.execute`（L247）传 proxy_url:
```python
        metrics = await exploit.execute(
            ...,
            proxy_url=input.proxy_url,   # 新增
        )
```
同样改 endpoint_verify（L326 `verifier.execute(..., proxy_url=input.proxy_url)`）、report activity（L457 `executor.execute(..., proxy_url=input.proxy_url)`）。

改 `write_engine_config_for_session`（L672-688）：加 `proxy_url: str | None = None` 形参，传给 `engine.write_config(repo_path, session_id=session_id, proxy_url=proxy_url)`。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd packages/blackbox && python -m pytest tests/pipeline/test_host_proxy_activities.py -v`
Expected: PASS

- [ ] **Step 5: commit**

```bash
git add packages/blackbox/src/supernova_blackbox/pipeline/activities.py \
        packages/blackbox/tests/pipeline/test_host_proxy_activities.py
git commit -m "feat(blackbox): host_proxy setup/cleanup activities + preflight mapping + exploit proxy"
```

---

### Task 8: workflows 编排 + worker 注册

**Files:**
- Modify: `packages/blackbox/src/supernova_blackbox/pipeline/workflows.py:103-145,516-542`（act_input 透传、preflight 前插 setup、finally 插 cleanup）
- Modify: `packages/blackbox/src/supernova_blackbox/worker.py:8-29,135-154`（import + 注册）
- Modify: `packages/worker/src/supernova_worker/runner.py:37-46,82-101`（import + 注册 bb_ 别名）
- Modify: `packages/blackbox/src/supernova_blackbox/pipeline/workflows.py`（write_engine_config_for_session 调用点传 proxy_url）
- Test: `packages/blackbox/tests/pipeline/test_workflow_proxy_orchestration.py`

**Interfaces:**
- Consumes: Task 6/7 的 input 字段 + activity
- Produces: workflow 在 preflight 前起代理拿 proxy_url 注入 act_input；finally best-effort 停代理

- [ ] **Step 1: 写失败测试**

```python
# packages/blackbox/tests/pipeline/test_workflow_proxy_orchestration.py
"""用 pipeline_testing_mode + mocked activities 断言编排顺序：
setup_activity(preflight 前) -> proxy_url 注入后续 act_input -> cleanup(finally)。"""
import pytest
from unittest.mock import AsyncMock, patch
from datetime import timedelta
from temporalio import workflow, activity
from temporalio.testing import WorkflowEnvironment
from supernova_blackbox.pipeline.shared import BlackboxPipelineInput

@pytest.mark.asyncio
async def test_workflow_runs_setup_before_preflight_and_cleanup_in_finally():
    """断言 activity 调用顺序：host_proxy_setup 在 preflight 前；stop 在 finally。"""
    # 用 WorkflowEnvironment + activity mock（对齐 blackbox 现有 workflow 测试范式）
    # 关键断言：call_order[0] == "run_host_proxy_setup"，末尾含 "stop_host_proxy"
    ...  # 按现有 blackbox workflow 测试（test_workflows*.py）的 mocking 范式补全

@pytest.mark.asyncio
async def test_workflow_no_mappings_skips_setup():
    """host_mappings={} → setup 返回 ''，preflight 走原 DNS，无 cleanup。"""
    ...
```

> blackbox workflow 测试用 `WorkflowEnvironment` + `with_worker(worker...)` 范式。参照 `packages/blackbox/tests/pipeline/` 现有 `test_workflows*.py` 的 mock activity 模式补全（实现者先读一个现有 workflow 测试作模板）。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd packages/blackbox && python -m pytest tests/pipeline/test_workflow_proxy_orchestration.py -v`
Expected: FAIL（workflow 未插 setup）

- [ ] **Step 3: 实现**

`workflows.py` L103-114 act_input 构造加 host_mappings:
```python
        act_input = BlackboxActivityInput(
            ...,  # 现有字段
            host_mappings=input.host_mappings or {},
        )
```

preflight 前（L131 log_phase preflight 之后、L141 preflight 之前）插 setup:
```python
        # per-scan host proxy（preflight 前起，供 preflight 映射 + exploit 出口）
        proxy_url = await workflow.execute_activity(
            activities.run_host_proxy_setup,
            args=[act_input],
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
        if proxy_url:
            act_input.proxy_url = proxy_url   # 注入，后续所有派生子 input 经 **act_input.__dict__ 继承
```

finally 块（L516-542）末尾追加 best-effort stop（与现有 cleanup 同模式）:
```python
    if act_input.proxy_url:
        try:
            await workflow.execute_activity(
                activities.stop_host_proxy,
                args=[act_input.proxy_url],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
        except Exception:
            pass  # best-effort
```

`write_engine_config_for_session` 调用点（workflows.py 内若有 args=[..., session_id, engine_name]，加 proxy_url=act_input.proxy_url；若经 activity input 则已在 act_input）—— 实现者 grep `write_engine_config_for_session` 在 workflows.py 的调用点补参。

worker 注册：
- `worker.py:8-29` import 加 `run_host_proxy_setup, stop_host_proxy`；L135-154 activities 列表加这两个
- `runner.py:37-46` import 加 `run_host_proxy_setup as bb_run_host_proxy_setup, stop_host_proxy as bb_stop_host_proxy`（bb_ 别名，对齐同文件现有风格）；L82-101 bb_worker activities 列表加这两个

- [ ] **Step 4: 运行测试确认通过**

Run: `cd packages/blackbox && python -m pytest tests/pipeline/test_workflow_proxy_orchestration.py tests/pipeline/test_workflows -v`（相关文件）
Expected: PASS

- [ ] **Step 5: commit**

```bash
git add packages/blackbox/src/supernova_blackbox/pipeline/workflows.py \
        packages/blackbox/src/supernova_blackbox/worker.py \
        packages/worker/src/supernova_worker/runner.py \
        packages/blackbox/tests/pipeline/test_workflow_proxy_orchestration.py
git commit -m "feat(blackbox): orchestrate host proxy setup/cleanup in workflow + register"
```

---

## Phase 3 — web 后端：HOST 档案库 + API + scan 解析

### Task 9: host_profile_store.py + /etc/hosts 解析

**Files:**
- Create: `packages/web/src/supernova_web/components/host_profile_store.py`
- Test: `packages/web/tests/components/test_host_profile_store.py`
- 参考模板: `packages/web/src/supernova_web/components/auth_profile_store.py`（镜像结构，**剥离所有 vault/加密**）

**Interfaces:**
- Produces:
  - `class HostMapping(BaseModel)`：`ip: str` / `host: str`
  - `class HostProfile(BaseModel)`：`id` / `name` / `source_url: str|None` / `mappings: list[HostMapping]` / `scope: Literal["workspace","system"]="workspace"` / `created_at` / `updated_at`
  - `HOST_PROFILES_FILENAME = "host-profiles.yaml"`
  - `class HostProfileStore`：`__init__(self, workspaces_dir: Path)`（**无 vault 参数**）；`read` / `get` / `write` / `upsert_profile` / `delete_profile` / `fork_from_system` / `import_from_url(ws, url, name?)` / `refresh(ws, pid)`（同 auth 模式，去加密）
  - `def fetch_and_parse_hosts(url: str, timeout: int = 15) -> tuple[list[HostMapping], list[str]]` —— GET + 解析 `/etc/hosts`，返回 `(mappings, warnings)`，纯拉取解析不落盘（`/parse`、`refresh`、scan_manager 共用）

- [ ] **Step 1: 写失败测试**

```python
# packages/web/tests/components/test_host_profile_store.py
import pytest
from pathlib import Path
from supernova_web.components.host_profile_store import (
    HostProfileStore, HostProfile, HostMapping, fetch_and_parse_hosts, AlreadyForked)

ETC_HOSTS_SAMPLE = """# comment line
10.0.0.1 api.example.com alias.example.com

# blank above
not-a-valid-line
192.168.1.5 svc.test
"""

def test_parse_etc_hosts_basic():
    """跳过注释/空行/非法行；别名同指 IP 各生成一条。"""
    mappings, warnings = fetch_and_parse_hosts.__wrapped__(ETC_HOSTS_SAMPLE) \
        if hasattr(fetch_and_parse_hosts, "__wrapped__") else _parse(ETC_HOSTS_SAMPLE)
    ips = {m.host: m.ip for m in mappings}
    assert ips["api.example.com"] == "10.0.0.1"
    assert ips["alias.example.com"] == "10.0.0.1"
    assert ips["svc.test"] == "192.168.1.5"
    assert len(warnings) >= 1  # "not-a-valid-line"

def test_store_crud(tmp_path):
    store = HostProfileStore(tmp_path)
    p = HostProfile(id="", name="华南", source_url=None,
                    mappings=[HostMapping(ip="10.0.0.1", host="x.test")],
                    created_at="", updated_at="")
    saved = store.upsert_profile("ws1", p)
    assert saved.id.startswith("host_")
    assert len(store.read("ws1")) == 1
    assert store.get("ws1", saved.id).name == "华南"
    assert store.delete_profile("ws1", saved.id) is True
    assert store.read("ws1") == []

def test_store_system_merge_dedup(tmp_path):
    """.system 段 + ws 段按 id 去重（ws 优先）。"""
    store = HostProfileStore(tmp_path)
    sys_p = HostProfile(id="host_sys", name="sys", source_url=None, mappings=[],
                        created_at="", updated_at="", scope="system")
    store.upsert_profile(".system", sys_p)
    # ws 自己的 host_sys 应覆盖 system 的
    ws_p = HostProfile(id="host_sys", name="ws-override", source_url=None,
                       mappings=[], created_at="", updated_at="")
    store.upsert_profile("ws1", ws_p)
    profiles = store.read("ws1")
    assert len([p for p in profiles if p.id == "host_sys"]) == 1
    assert next(p for p in profiles if p.id == "host_sys").name == "ws-override"

def test_fork_from_system(tmp_path):
    store = HostProfileStore(tmp_path)
    sys_p = HostProfile(id="host_s1", name="sys", source_url=None, mappings=[],
                        created_at="", updated_at="", scope="system")
    store.upsert_profile(".system", sys_p)
    forked = store.fork_from_system("ws1", "host_s1")
    assert forked.scope == "workspace"
    with pytest.raises(AlreadyForked):
        store.fork_from_system("ws1", "host_s1")

def test_import_from_url_fetches_and_saves(tmp_path, monkeypatch):
    """import_from_url: GET + 解析 + 落盘 + 存 source_url。"""
    async def fake_get(url, timeout=15):
        return ETC_HOSTS_SAMPLE
    monkeypatch.setattr("supernova_web.components.host_profile_store._http_get_hosts", fake_get)
    store = HostProfileStore(tmp_path)
    p = store.import_from_url("ws1", "https://hosts.test/get?id=1", name="导入")
    assert p.source_url == "https://hosts.test/get?id=1"
    assert any(m.host == "api.example.com" for m in p.mappings)

def test_refresh_fallback_on_failure(tmp_path, monkeypatch):
    """refresh 失败 → 保留落盘快照，不 raise。"""
    async def boom(url, timeout=15): raise OSError("net")
    monkeypatch.setattr("supernova_web.components.host_profile_store._http_get_hosts", boom)
    store = HostProfileStore(tmp_path)
    p = store.upsert_profile("ws1", HostProfile(id="host_x", name="x",
        source_url="https://hosts.test/get?id=1",
        mappings=[HostMapping(ip="10.0.0.1", host="x.test")],
        created_at="", updated_at=""))
    refreshed = store.refresh("ws1", "host_x")   # 不 raise
    assert refreshed.mappings == p.mappings  # 保留快照
```

> 解析函数提取为纯函数 `parse_etc_hosts(text: str) -> tuple[list[HostMapping], list[str]]`（不碰网络），`fetch_and_parse_hosts` = `_http_get_hosts` + `parse_etc_hosts`。这样解析逻辑可纯单测（test_parse_etc_hosts_basic 调 `parse_etc_hosts`）。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd packages/web && python -m pytest tests/components/test_host_profile_store.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `host_profile_store.py`**

镜像 `auth_profile_store.py` 结构（L8-342），关键差异：
- **删**：`CredentialVault` import、`vault` 参数、`_CRED_SECRET_FIELDS`、`_encrypt_credential`/`_decrypt_credential`/`_mask_credential`/`MASKED`、`read_masked`、`credential_to_authentication`、verify_status 相关
- **保留**：`_validate_ws_segment`、`_path`（路径穿越防护 `is_relative_to`）、`_read_segment`（去解密，直接 YAML→HostProfile，标 scope）、`read`（.system 合并去重）、`get`/`write`/`upsert_profile`（id 前缀 `host_`）/`delete_profile`/`fork_from_system`/`seed_from_config`（若有 configs hosts seed 需求；否则省）
- **新增** `parse_etc_hosts(text)` / `fetch_and_parse_hosts(url)` / `_http_get_hosts(url)`（httpx GET）/ `import_from_url` / `refresh`

```python
# 核心新增函数
def parse_etc_hosts(text: str) -> tuple[list[HostMapping], list[str]]:
    warnings: list[str] = []
    mappings: list[HostMapping] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()  # 去行内注释
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            warnings.append(f"L{lineno}: {raw!r} 字段不足")
            continue
        ip = parts[0]
        try:
            ipaddress.ip_address(ip)  # 校验合法 IP
        except ValueError:
            warnings.append(f"L{lineno}: {raw!r} 非合法 IP")
            continue
        for host in parts[1:]:
            mappings.append(HostMapping(ip=ip, host=host))
    return mappings, warnings

async def _http_get_hosts(url: str, timeout: int = 15) -> str:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.text

async def fetch_and_parse_hosts(url: str, timeout: int = 15) -> tuple[list[HostMapping], list[str]]:
    text = await _http_get_hosts(url, timeout)
    return parse_etc_hosts(text)
```

`refresh(ws, pid)`：读 profile → 若 `source_url` 为空直接返回；否则 `await fetch_and_parse_hosts(source_url)` → 成功更新 mappings+updated_at+write；失败（任何 Exception）保留原 mappings、日志 warning、返回原 profile（**不 raise、不阻断**）。

`import_from_url(ws, url, name)`：`await fetch_and_parse_hosts(url)` → 构造 HostProfile(source_url=url) → upsert。

> store 方法若是 async（fetch 用 await），改 store 相关方法为 async，或在 store 内 `asyncio.run` / 由 API 层 await。推荐 store 方法 `import_from_url`/`refresh` 为 async，API 层 await。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd packages/web && python -m pytest tests/components/test_host_profile_store.py -v`
Expected: PASS

- [ ] **Step 5: commit**

```bash
git add packages/web/src/supernova_web/components/host_profile_store.py \
        packages/web/tests/components/test_host_profile_store.py
git commit -m "feat(web): HostProfileStore + /etc/hosts parse (mirror auth, no encryption)"
```

---

### Task 10: host_profiles router + app 装配

**Files:**
- Create: `packages/web/src/supernova_web/api/host_profiles.py`
- Modify: `packages/web/src/supernova_web/app.py:445-482`（import + 实例化 + include router + 注入 scan_manager）
- Test: `packages/web/tests/api/test_host_profiles.py`
- 模板: `packages/web/src/supernova_web/api/auth_profiles.py`

**Interfaces:**
- Consumes: `HostProfileStore`（app.state.host_profile_store）
- Produces: router `prefix="/api/workspaces"`，端点：
  - `GET /{ws}/host-profiles`（member）
  - `POST /{ws}/host-profiles`（manager，upsert）
  - `GET /{ws}/host-profiles/{pid}`（member）
  - `PUT /{ws}/host-profiles/{pid}`（manager）
  - `DELETE /{ws}/host-profiles/{pid}`（manager）
  - `POST /{ws}/host-profiles/{pid}/fork`（manager）
  - `POST /{ws}/host-profiles/parse?url=`（manager，GET+解析不落盘，预览）
  - `POST /{ws}/host-profiles/{pid}/refresh`（manager，按 source_url 刷新）

- [ ] **Step 1: 写失败测试**

```python
# packages/web/tests/api/test_host_profiles.py
import pytest
from httpx import AsyncClient, ASGITransport
from supernova_web.app import create_app

@pytest.mark.asyncio
async def test_list_create_get_delete(monkeypatch):
    app = create_app(overrides={"auth": _no_auth()})  # 测试绕过鉴权（对齐 auth_profiles 测试范式）
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        # 列空
        r = await ac.get("/api/workspaces/ws1/host-profiles")
        assert r.status_code == 200 and r.json() == []
        # 建
        r = await ac.post("/api/workspaces/ws1/host-profiles",
                          json={"name": "华南", "mappings": [{"ip": "10.0.0.1", "host": "x.test"}]})
        assert r.status_code == 200
        pid = r.json()["id"]
        # 取
        r = await ac.get(f"/api/workspaces/ws1/host-profiles/{pid}")
        assert r.json()["name"] == "华南"
        # 删
        r = await ac.delete(f"/api/workspaces/ws1/host-profiles/{pid}")
        assert r.status_code == 200

@pytest.mark.asyncio
async def test_parse_endpoint_no_persist(monkeypatch):
    """parse: GET+解析返回 mappings，不落盘。"""
    async def fake_fetch(url, timeout=15):
        return ([__import__("supernova_web.components.host_profile_store", fromlist=["HostMapping"])
                 .HostMapping(ip="10.0.0.1", host="x.test")], [])
    monkeypatch.setattr("supernova_web.api.host_profiles.fetch_and_parse_hosts", fake_fetch)
    app = create_app(overrides={"auth": _no_auth()})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/workspaces/ws1/host-profiles/parse",
                          params={"url": "https://h.test/get?id=1"})
        assert r.status_code == 200
        assert any(m["host"] == "x.test" for m in r.json()["mappings"])
        # 不落盘
        r2 = await ac.get("/api/workspaces/ws1/host-profiles")
        assert r2.json() == []
```

> `_no_auth()` / overrides 范式对齐 `tests/api/test_auth_profiles*.py`（实现者先读该文件取 fixture）。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd packages/web && python -m pytest tests/api/test_host_profiles.py -v`
Expected: FAIL（无 router）

- [ ] **Step 3: 实现**

`api/host_profiles.py` 镜像 `auth_profiles.py`（L1-50），替换 store 名 + 端点：
```python
from fastapi import APIRouter, Request, HTTPException, Depends
from supernova_web.auth.dependencies import workspace_member, workspace_manager
from ..components.host_profile_store import HostProfile, HostProfileStore, fetch_and_parse_hosts, AlreadyForked

router = APIRouter(prefix="/api/workspaces", tags=["host-profiles"])

def _store(request: Request) -> HostProfileStore:
    return request.app.state.host_profile_store

@router.get("/{ws}/host-profiles")
async def list_profiles(ws: str, request: Request, user=Depends(workspace_member)):
    return [p.model_dump() for p in _store(request).read(ws)]

@router.post("/{ws}/host-profiles")
async def create_profile(ws: str, payload: dict, request: Request, user=Depends(workspace_manager)):
    p = HostProfile(**{**payload, "id": payload.get("id", ""), "created_at": "", "updated_at": ""})
    return _store(request).upsert_profile(ws, p).model_dump()

@router.post("/{ws}/host-profiles/parse")
async def parse_profile(ws: str, url: str, request: Request, user=Depends(workspace_manager)):
    """GET + 解析 /etc/hosts，不落盘（预览）。"""
    try:
        mappings, warnings = await fetch_and_parse_hosts(url)
    except Exception as e:
        raise HTTPException(422, f"拉取/解析失败: {e}")
    return {"mappings": [m.model_dump() for m in mappings], "warnings": warnings}

@router.post("/{ws}/host-profiles/{pid}/refresh")
async def refresh_profile(ws: str, pid: str, request: Request, user=Depends(workspace_manager)):
    p = _store(request).get(ws, pid)
    if not p:
        raise HTTPException(404)
    refreshed = await _store(request).refresh(ws, pid)
    return refreshed.model_dump()

# GET/{pid}, PUT/{pid}, DELETE/{pid}, POST/{pid}/fork 同 auth_profiles 范式
# system 档案 403 守卫：if existing.scope == "system": raise HTTPException(403, "系统档案只读")
```

`app.py`（L445-482）：
- import 加 `HostProfileStore`（L445 旁）+ `host_profiles`（L446 api import 列表加）
- 实例化（L452 旁）：`app.state.host_profile_store = HostProfileStore(cfg.workspaces_dir)`（**无 vault**）
- 注入 scan_manager（L461-465）：加 `host_profile_store=app.state.host_profile_store`
- include router（L482 旁）：`app.include_router(host_profiles.router, dependencies=_require_auth)`

- [ ] **Step 4: 运行测试确认通过**

Run: `cd packages/web && python -m pytest tests/api/test_host_profiles.py -v`
Expected: PASS

- [ ] **Step 5: commit**

```bash
git add packages/web/src/supernova_web/api/host_profiles.py \
        packages/web/src/supernova_web/app.py \
        packages/web/tests/api/test_host_profiles.py
git commit -m "feat(web): host-profiles router + app wiring"
```

---

### Task 11: ScanRequest 字段 + scan_manager 解析

**Files:**
- Modify: `packages/web/src/supernova_web/models.py:23-95`（ScanRequest 加字段 + 互斥校验）
- Modify: `packages/web/src/supernova_web/components/scan_manager.py:328-477`（_resolve_blackbox_inputs 解析 host → mappings）
- Modify: `packages/web/src/supernova_web/components/scan_manager.py`（起扫描处把 host_mappings 灌进 BlackboxPipelineInput）
- Test: `packages/web/tests/components/test_scan_manager_host.py`

**Interfaces:**
- Consumes: `ScanRequest.host_profile_id` / `host_url`
- Produces: `BlackboxPipelineInput.host_mappings`（scan_manager 解析后灌入）

- [ ] **Step 1: 写失败测试**

```python
# packages/web/tests/components/test_scan_manager_host.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from supernova_web.models import ScanRequest

def test_scan_request_accepts_host_fields():
    r = ScanRequest(type="blackbox", url="http://x.test", workspace="ws1",
                    reuse_whitebox_scan_id="wb1", host_profile_id="host_x")
    assert r.host_profile_id == "host_x"
    r2 = ScanRequest(type="blackbox", url="http://x.test", workspace="ws1",
                     reuse_whitebox_scan_id="wb1", host_url="https://h.test/get?id=1")
    assert r2.host_url == "https://h.test/get?id=1"

@pytest.mark.asyncio
async def test_resolve_host_profile_to_mappings(tmp_path, monkeypatch):
    """选 host_profile_id → 从 store 读 mappings 灌入 pipeline input。"""
    # mock store.get 返回带 mappings 的 profile
    ...
    # 断言起扫描时 BlackboxPipelineInput.host_mappings == {"x.test":"10.0.0.1"}

@pytest.mark.asyncio
async def test_resolve_host_url_fetches_at_scan_start(tmp_path, monkeypatch):
    """填 host_url → 扫描启动时 GET 解析得 mappings。"""
    async def fake_fetch(url, timeout=15):
        from supernova_web.components.host_profile_store import HostMapping
        return ([HostMapping(ip="10.0.0.2", host="x.test")], [])
    monkeypatch.setattr("supernova_web.components.scan_manager.fetch_and_parse_hosts", fake_fetch)
    ...
    # 断言 mappings 灌入
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd packages/web && python -m pytest tests/components/test_scan_manager_host.py -v`
Expected: FAIL

- [ ] **Step 3: 实现**

`models.py` ScanRequest（L49 旁，`save_as` 后）加:
```python
    host_profile_id: str | None = None   # 选 HOST 档案
    host_url: str | None = None          # 或填 GET 链接（扫描时拉取）
```
加校验器（`_host_profile_xor_url`）：`host_profile_id` 与 `host_url` 互斥（不能同填），与 auth 字段独立（不互斥）。

`scan_manager._resolve_blackbox_inputs`（L328）：返回值或副作用增加 `host_mappings`。两种来源：
- `host_profile_id`：`profile = host_store.get(ws, req.host_profile_id)` → 若 `profile.source_url` 尝试 `await host_store.refresh(ws, pid)`（失败 fallback 原快照，store 内已处理）→ `{m.host: m.ip for m in profile.mappings}`
- `host_url`：`mappings, _ = await fetch_and_parse_hosts(req.host_url)` → dict；扫描结束后按 `host_url` 去重 upsert（`name=env-{short}`）—— 在扫描完成回调或 finalize 处补（plan 默认「成功/失败都入」，spec §4.2）

scan_manager 起黑盒扫描处（构造 BlackboxPipelineInput 的点）：把解析出的 `host_mappings` 灌入 `BlackboxPipelineInput(host_mappings=...)`。ScanManager `__init__` 加 `host_profile_store: Any = None` 关键字参数（对齐 auth_profile_store L82/L97 模式），app.py 已在 Task 10 注入。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd packages/web && python -m pytest tests/components/test_scan_manager_host.py -v`
Expected: PASS

- [ ] **Step 5: commit**

```bash
git add packages/web/src/supernova_web/models.py \
        packages/web/src/supernova_web/components/scan_manager.py \
        packages/web/tests/components/test_scan_manager_host.py
git commit -m "feat(web): ScanRequest host fields + scan_manager resolves host mappings"
```

---

## Phase 4 — 前端

### Task 12: api/types + HostProfilesPage + HostProfileDialog + MappingRows + 路由

**Files:**
- Create: `packages/web/frontend/src/api/hostProfiles.ts`
- Modify: `packages/web/frontend/src/api/types.ts`（加 HostProfile/HostMapping + ScanRequest 字段）
- Create: `packages/web/frontend/src/pages/HostProfilesPage.tsx`
- Create: `packages/web/frontend/src/pages/HostProfilesPage.test.tsx`
- Create: `packages/web/frontend/src/components/HostProfileDialog.tsx`
- Create: `packages/web/frontend/src/components/host/MappingRows.tsx`
- Modify: `packages/web/frontend/src/router.tsx:88`（加路由）
- Modify: `packages/web/frontend/src/routes/WorkspaceDetail/index.tsx:110-116`（加命令栏按钮）
- 模板: `api/authProfiles.ts` / `pages/AuthProfilesPage.tsx` / `components/AuthProfileDialog.tsx` / `components/auth/CredentialRows.tsx`

**Interfaces:**
- Consumes: 后端 `/api/workspaces/{ws}/host-profiles` 端点（Task 10）
- Produces: `listHostProfiles`/`createHostProfile`/`updateHostProfile`/`deleteHostProfile`/`forkHostProfile`/`parseHostProfile`/`refreshHostProfile` api；`HostProfile`/`HostMapping` 类型；管理页 + 表单 + 行编辑器

- [ ] **Step 1: 写失败测试**

```tsx
// packages/web/frontend/src/pages/HostProfilesPage.test.tsx
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { Toaster } from "@/components/ui/sonner";
import { HostProfilesPage } from "./HostProfilesPage";
import "@/i18n";

const server = setupServer();
beforeEach(() => { server.resetHandlers(); });
beforeAll(() => server.listen());
afterAll(() => server.close());

function renderPage() {
  return render(<MemoryRouter initialEntries={["/p/ws1/host-profiles"]}>
    <Routes><Route path="/p/:workspace/host-profiles" element={<><HostProfilesPage/><Toaster/></>}/></Routes>
  </MemoryRouter>);
}

describe("HostProfilesPage", () => {
  beforeEach(async () => { await i18n.changeLanguage("zh"); });

  it("列表 + 新建档案", async () => {
    let store: any[] = [];
    server.use(
      http.get("/api/workspaces/ws1/host-profiles", () => HttpResponse.json(store)),
      http.post("/api/workspaces/ws1/host-profiles", async ({request}) => {
        const body = await request.json() as any;
        const p = { ...body, id: "host_1", created_at: "", updated_at: "" };
        store = [...store, p];
        return HttpResponse.json(p);
      }),
    );
    renderPage();
    await waitFor(() => expect(screen.getByText("暂无 HOST 档案")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "新建档案" }));
    await userEvent.type(screen.getByLabelText("档案名"), "华南生产");
    // mappings 行编辑器
    await userEvent.type(screen.getByLabelText("IP"), "10.0.0.1");
    await userEvent.type(screen.getByLabelText("域名"), "api.test");
    await userEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(screen.getByText("华南生产")).toBeInTheDocument());
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd packages/web/frontend && ./node_modules/.bin/vitest run src/pages/HostProfilesPage.test.tsx`
Expected: FAIL（组件不存在）

- [ ] **Step 3: 实现**

`api/hostProfiles.ts` 镜像 `authProfiles.ts`（Pattern B，arrow const + apiGet/Post/Put/Delete）:
```typescript
import { apiGet, apiPost, apiPut, apiDelete } from "./client";
import type { HostProfile } from "./types";
const enc = encodeURIComponent;
export const listHostProfiles = (ws: string) => apiGet<HostProfile[]>(`/workspaces/${enc(ws)}/host-profiles`);
export const createHostProfile = (ws: string, body: Partial<HostProfile>) => apiPost<HostProfile>(`/workspaces/${enc(ws)}/host-profiles`, body);
export const updateHostProfile = (ws: string, pid: string, body: Partial<HostProfile>) => apiPut<{ok:true}>(`/workspaces/${enc(ws)}/host-profiles/${enc(pid)}`, body);
export const deleteHostProfile = (ws: string, pid: string) => apiDelete<{ok:true}>(`/workspaces/${enc(ws)}/host-profiles/${enc(pid)}`);
export const forkHostProfile = (ws: string, pid: string) => apiPost<HostProfile>(`/workspaces/${enc(ws)}/host-profiles/${enc(pid)}/fork`, {});
export const parseHostProfile = (ws: string, url: string) => apiPost<{mappings:{ip:string;host:string}[];warnings:string[]}>(`/workspaces/${enc(ws)}/host-profiles/parse`, {}, { params: { url } });
export const refreshHostProfile = (ws: string, pid: string) => apiPost<HostProfile>(`/workspaces/${enc(ws)}/host-profiles/${enc(pid)}/refresh`, {});
```
> `parseHostProfile` 的 `?url=` query：确认 `client.ts` 的 apiPost 是否支持 params（若不支持，改用 `apiPost(path + "?url=" + enc(url), {})` 或加 client 支持）。

`types.ts` 加（L297 旁）:
```typescript
export interface HostMapping { ip: string; host: string; }
export interface HostProfile {
  id: string; name: string; source_url?: string;
  mappings: HostMapping[]; created_at?: string; updated_at?: string;
  scope?: "workspace" | "system";
}
```
ScanRequest 加（L316 旁）：`host_profile_id?: string; host_url?: string;`

`components/host/MappingRows.tsx` 镜像 `CredentialRows.tsx`（受控行编辑器，grid-cols-2：IP / 域名，增删行）。

`HostProfilesPage.tsx` 镜像 `AuthProfilesPage.tsx`（手写 useState/useEffect/refresh，4 列表格：名称/system 徽章/来源（手填=「手动」/GET 链接截断+Tooltip+复制）/映射条数/更新时间/操作（编辑·刷新·删除；system 仅 fork））。

`HostProfileDialog.tsx` 镜像 `AuthProfileDialog.tsx`（字段 name + MappingRows + 可选 source_url + 「从 GET 链接拉取」按钮调 parseHostProfile 填 mappings）。

`router.tsx:88` 旁加：`{ path: "host-profiles", element: <HostProfilesPage /> }`；import 加 HostProfilesPage。

`WorkspaceDetail/index.tsx:110-116` 旁加命令栏按钮（`Globe` 图标，`to="host-profiles"`，文案 `t("hostProfiles.openLabel")`）。

- [ ] **Step 4: 运行测试确认通过 + tsc**

Run: `cd packages/web/frontend && ./node_modules/.bin/vitest run src/pages/HostProfilesPage.test.tsx && ./node_modules/.bin/tsc --noEmit`
Expected: PASS + tsc 0 error

- [ ] **Step 5: commit**

```bash
git add packages/web/frontend/src/api/hostProfiles.ts packages/web/frontend/src/api/types.ts \
        packages/web/frontend/src/pages/HostProfilesPage.tsx packages/web/frontend/src/pages/HostProfilesPage.test.tsx \
        packages/web/frontend/src/components/HostProfileDialog.tsx packages/web/frontend/src/components/host/MappingRows.tsx \
        packages/web/frontend/src/router.tsx packages/web/frontend/src/routes/WorkspaceDetail/index.tsx
git commit -m "feat(web): HOST profiles management page (mirror auth profiles)"
```

---

### Task 13: ScanFormFields HOST 选择区 + ScanNewPage buildBody + i18n

**Files:**
- Modify: `packages/web/frontend/src/components/ScanFormFields.tsx:824-849`（黑盒表单加 HOST 区）
- Modify: `packages/web/frontend/src/pages/ScanNewPage.tsx:27-41,162-193`（FormState 加 host + buildBody 加字段）
- Modify: `packages/web/frontend/src/locales/zh.json` + `en.json`（加 `hostProfiles.*` / `scan.host.*`）
- Test: `packages/web/frontend/src/components/ScanFormFields.test.tsx`（既有文件加 case）
- 模板: `RightAuthCore`（segmented toggle，`ScanFormFields.tsx:126-173`）/ `BottomProfileBlock`（卡片点选）

**Interfaces:**
- Consumes: `listHostProfiles`（Task 12）
- Produces: `FormState.host`（`{enabled, mode: "profile"|"url", profileId, hostUrl}`）；`buildBody` 发 `host_profile_id` / `host_url`

- [ ] **Step 1: 写失败测试**

```tsx
// ScanFormFields.test.tsx 加 case（用既有 render 范式）
it("黑盒 HOST 选择区：选档案 / 填链接 toggle", async () => {
  // render ScanFormFields type="blackbox"
  // 点 HOST 区 toggle 切「填 GET 链接」→ 出现 URL 输入
  // 切「选档案」→ 出现档案下拉/卡片
  // buildBody 断言 host_profile_id / host_url 正确
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd packages/web/frontend && ./node_modules/.bin/vitest run src/components/ScanFormFields.test.tsx`
Expected: FAIL（无 HOST 区）

- [ ] **Step 3: 实现**

`ScanNewPage.tsx`：
- `HostFormState`（L27 旁）：`{ enabled: boolean; mode: "profile" | "url"; profileId: string; hostUrl: string }` + `DEFAULT_HOST`
- `FormState`（L139）加 `host: HostFormState`
- `buildBody`（L162-193）blackbox 段加:
```typescript
  if (f.host.enabled) {
    if (f.host.mode === "profile") body.host_profile_id = f.host.profileId || undefined;
    else body.host_url = f.host.hostUrl || undefined;
  }
```
- 重跑预填（`RerunPreset`/`presetToHostState`）镜像 auth 的 `presetToAuthState`，从 SessionData 回填（若有 host 字段）

`ScanFormFields.tsx`：黑盒区（L824 认证行旁）加「HOST 解析」section——镜像 `RightAuthCore` 的 segmented toggle（`["profile","url"]`）：
- profile 模式：下拉/卡片选当前 ws 的 host profile（`listHostProfiles`）
- url 模式：URL 输入框
- 可选（不启用 = 不起代理，向后兼容）；与认证区并列、互不影响

`locales/zh.json` + `en.json` 加 key（zh 用 key 作值，en 留空人工补）：
- `hostProfiles.*`（openLabel="HOST 档案"/title/create/name/source/mappings/ip/host/importFromUrl/refresh/systemBadge/forkLabel/empty...）
- `scan.host.*`（sectionLabel/sourceProfile/sourceUrl/selectProfile/urlPlaceholder...）

> 用 `npm run i18n:scan`（i18next-parser）自动提取 zh key（defaultValue 规则），再人工补 en。

- [ ] **Step 4: 运行测试确认通过 + tsc**

Run: `cd packages/web/frontend && ./node_modules/.bin/vitest run src/components/ScanFormFields.test.tsx src/pages/ && ./node_modules/.bin/tsc --noEmit`
Expected: PASS + tsc 0

- [ ] **Step 5: commit**

```bash
git add packages/web/frontend/src/components/ScanFormFields.tsx packages/web/frontend/src/pages/ScanNewPage.tsx \
        packages/web/frontend/src/locales/zh.json packages/web/frontend/src/locales/en.json \
        packages/web/frontend/src/components/ScanFormFields.test.tsx
git commit -m "feat(web): scan form HOST selector + i18n"
```

---

## Phase 5 — 收尾

### Task 14: 端到端集成 + 并发隔离验证

**Files:**
- Test: `packages/blackbox/tests/pipeline/test_host_proxy_e2e.py`（集成：两扫描并发同域名不同 IP 互不串）
- 验证脚本: `scripts/validate_host_proxy_probe/`（已存在，复跑确认）

**Interfaces:**
- Consumes: 全链路（Task 1-13）

- [ ] **Step 1: 写集成测试（两扫描并发隔离）**

```python
# packages/blackbox/tests/pipeline/test_host_proxy_e2e.py
"""端到端：两扫描各持不同映射、同域名 target.test 不同 IP，互不影响。
对齐 scripts/validate_host_proxy_probe/ 的实测断言（serverA/B hits 互不串）。"""
import pytest
from unittest.mock import AsyncMock, patch
from supernova_core.services.host_proxy import start_host_proxy, stop_host_proxy

@pytest.mark.asyncio
async def test_two_proxies_same_host_different_ip_isolated():
    """两个独立 proxy：同 host 不同 IP，端口不同，映射互不覆盖。"""
    hA = await start_host_proxy({"target.test": "10.0.0.1"})
    hB = await start_host_proxy({"target.test": "10.0.0.2"})
    assert hA.port != hB.port
    assert hA.proxy_url != hB.proxy_url
    # 各自 env 独立（per-scan 隔离基石）
    assert hA.process != hB.process
    await stop_host_proxy(hA)
    await stop_host_proxy(hB)
```

> 真正的"两扫描并发同域名落不同 IP"端到端需要起目标 server + 跑完整 workflow——这超出单测范围，由 `scripts/validate_host_proxy_probe/`（双 session probe_server/tls_server + agent-browser/playwright/curl）覆盖。Task 14 在此复跑脚本确认，并补一个集成单测锁"两 proxy 端口独立"。

- [ ] **Step 2: 运行测试 + 复跑实测脚本**

Run:
```bash
cd packages/blackbox && python -m pytest tests/pipeline/test_host_proxy_e2e.py -v
# 复跑实测（需 proxy.py + chrome + playwright-cli 装好）
bash scripts/validate_host_proxy_probe/probe_agent_browser.sh
bash scripts/validate_host_proxy_probe/probe_playwright.sh
bash scripts/validate_host_proxy_probe/probe_https_connect.sh
```
Expected: 单测 PASS；三个脚本各自 `PASS scanA->serverA` / `PASS scanB->serverB` / `PASS HTTPS CONNECT`

- [ ] **Step 3: rebuild worker 真机冒烟**

```bash
# 改了 core/blackbox src，rebuild worker 才生效（CLAUDE.md Global Constraints）
docker compose build supernova-worker   # 或项目实际 build 命令
# 起一个带 HOST 档案的黑盒扫描，确认：preflight 用映射 IP、exploit 经代理、扫描结束 cleanup
```

- [ ] **Step 4: 最终 commit + 更新 memory**

```bash
git add packages/blackbox/tests/pipeline/test_host_proxy_e2e.py
git commit -m "test(blackbox): e2e host proxy isolation + probe scripts verified"
```

记一条 memory（更新 `blackbox-host-proxy-feasibility-proven.md` → 实现已落地，或新建 `blackbox-host-profile-impl-status.md` 记录实现状态、未 push、待 rebuild、真机冒烟结果）。

---

## Self-Review 结论

**Spec 覆盖（逐节核对）：**
- §1.3 出口清单 6 项：bash+curl（Task 2）、agent-browser（Task 5）、playwright（Task 5）、web_fetch（Task 2）、preflight（Task 3）、claude Bash（Task 4 Anthropic env）——✅ 全覆盖
- §4.1 档案模型/存储：Task 9 ✅
- §4.2 GET 解析/刷新：Task 9（`fetch_and_parse_hosts`/`import_from_url`/`refresh` + fallback）✅
- §4.3 per-scan 代理：Task 1 ✅
- §4.4 出口注入：Task 2/4/5 ✅；per-scan 不绑 session_id（Global Constraints + Task 5 variables 注入）✅
- §4.5 preflight/SSRF：Task 3（映射 IP + loopback 照拦）✅
- §4.6 前端 A/B/C/D/E：Task 12/13 ✅
- §5 数据流：Task 6/7/8/11 穿线 ✅
- §6 错误处理：fail-fast（Task 1/7）、刷新 fallback（Task 9）、loopback 拦截（Task 3）、cleanup best-effort（Task 7/8）✅；运行中探活策略 = 启动探活一次（Task 1 `_probe`），周期探活待 plan 确认项标注（§8，本期不做）
- §8 待 plan 确认项：proxy.py 版本锁定（Global Constraints `>=2.4.10`）、入库时机（Task 11 默认成功/失败都入）、chrome ConnectionReset（exploit 主路径 curl 不受影响，本期不特殊处理）✅

**待 plan 确认项落定：**
- 运行中探活：**仅启动探活一次**（Task 1），不做周期/按 activity 探活（开销权衡；代理挂掉属异常，cleanup best-effort 兜底）
- 入库时机：**扫描结束按 source_url 去重入库（成功/失败都入）**（Task 11）
- proxy.py 版本：`>=2.4.10`（Global Constraints + Task 1 依赖）

**类型一致性：** `proxy_url`（全链路统一）、`host_mappings: dict[str,str]`（pipeline input）、`HostProfile`/`HostMapping`（web + 前端统一字段名 `source_url`/`mappings`/`scope`）—— 已跨 task 对齐。

**风险/注意：**
- Task 8 workflow 测试范式（`WorkflowEnvironment`）需实现者先读 `packages/blackbox/tests/pipeline/test_workflows*.py` 一个现成测试作模板。
- Task 10/11 web 测试的鉴权绕过 fixture 需读 `tests/api/test_auth_profiles*.py` 取范式。
- proxy.py `--plugins` 模块路径（Task 1 注）实现时锁定为 `supernova_core.services.host_proxy.HostResolverPlugin`。
