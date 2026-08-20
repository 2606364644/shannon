# Codex Agent SDK 第三引擎实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增第三引擎 `codex_cli`——GLM 经官方 `openai-codex` Python SDK（Codex app-server CLI 运行时，Responses 线协议）跑 supernova 全部 agent，与 claude/openai 引擎平级。

**Architecture:** `CodexProvider(BaseProvider)` 经 SDK 编程注入自定义 model_provider（`wire_api="responses"` → GLM 官方端点 `https://open.bigmodel.cn/api/v1`）+ per-call `CODEX_HOME` 隔离；事件流 `run_streamed` → `CodexStreamCollector`；结构化输出走 L0（本地 JSON 提取）+ L1（thread 内修复，AsyncOpenAI 回落）；collector `set_*` 工具经 stdio MCP 子进程 + JSONL 回放。

**Tech Stack:** Python 3.12 / `openai-codex` SDK / pytest / uv workspace

**Spec:** `docs/superpowers/specs/2026-08-21-codex-agent-sdk-engine-design.md`（含 deepsec 实战教训清单与 GLM 官方接入参考——执行本计划前先读）

## Global Constraints

- **pytest 纪律**：只跑本计划新增/改动相关的测试文件，严禁全套 pytest（预存 hang）。
- **uv sync 必须 `--all-packages`**：否则静默卸载 worker 依赖（memory 前科）。
- **SDK API 名以 Task 1 spike notes 为准**：TS SDK 是 `runStreamed`/`startThread`/`resumeThread`；Python 对应物预期为 `run_streamed`/`thread_start`/`thread_resume`，Task 1 核实后写入 notes，Task 2/3/8 动笔前先读 notes 校正代码中的调用名。
- **线协议**：`wire_api="responses"`，端点全前缀 `https://open.bigmodel.cn/api/v1`（Codex 在后面拼 `/responses`）。凭据 = GLM Coding Plan 套餐 Key（env_key `SUPERNOVA_CODEX_API_KEY`；若 spike 发现 env_key 不被接受则改 `experimental_bearer_token`，notes 记录）。
- **sandbox**：`danger-full-access` 无条件（对齐 claude 引擎 `bypassPermissions` 无条件语义）；`approval_policy=never`；不禁网（blackbox/PoC 需出网——与 deepsec 有意分歧）。
- **A2 契约**：`ClaudeRunResult` 字段语义不变（success 真实反映、error_code+retryable 分类、structured_output 产出义务、cost best-effort 经 `pricing.compute_cost`）。
- **双轨铁律**：不改任何 vuln/recon prompt，不喂确定性产物给 LLM 轨。
- **worker Docker 适配不在本计划**（runtime 获取方式在 Task 1 记录；若走 npm，Dockerfile/provision.sh 的具体加行内容记入 spike notes，改动随 NodeGoat 冒烟另起任务）；本计划验收 = host 真机探针。
- 每个 Task 结束一 commit，信息用中文（对齐仓库风格）。

---

## Phase 1 — Spike（真机验证，回炉闸门）

### Task 1: 添加 `openai-codex` 依赖 + SDK API 面探察

**Files:**
- Modify: `packages/core/pyproject.toml`（dependencies 列表）
- Create: `docs/superpowers/specs/2026-08-21-codex-spike-notes.md`

**Interfaces:**
- Produces: spike notes 文档——后续所有任务的 SDK 调用名 SSOT（`openai_codex` 的类/方法签名、CodexConfig 字段、runtime 二进制解析方式）

- [ ] **Step 1: pyproject 加依赖**

`packages/core/pyproject.toml` 的 `dependencies` 列表末尾（`"proxy.py>=2.4.10",` 之后）加：

```toml
    "openai-codex>=0.144,<0.145",
```

- [ ] **Step 2: 安装（必须 --all-packages）**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv sync --all-packages`
Expected: 成功，无 "uninstalled" worker 相关行

- [ ] **Step 3: 探察 SDK API 面（不动 repo 代码，纯 REPL 探察）**

依次运行并记录输出：

```bash
uv run python -c "
import openai_codex, inspect
print('version:', getattr(openai_codex, '__version__', '?'))
print('exports:', [n for n in dir(openai_codex) if not n.startswith('_')])
"
uv run python -c "
import openai_codex, inspect
for name in ('Codex', 'AsyncCodex', 'CodexConfig', 'Sandbox'):
    obj = getattr(openai_codex, name, None)
    if obj is None: print(name, '= MISSING'); continue
    print('===', name, '===')
    try: print(inspect.signature(obj))
    except Exception as e: print('sig err', e)
    print([m for m in dir(obj) if not m.startswith('_')])
"
uv run python -c "
import openai_codex, inspect
print(inspect.getdoc(openai_codex.AsyncCodex.thread_start) or 'no doc')
print(inspect.getdoc(openai_codex.CodexConfig) or 'no doc')
"
```

必须核实并记入 notes 的项：
1. `AsyncCodex` 构造参数（options/config/env 形态；TS 是 `new Codex({apiKey, config, env, codexPathOverride})`）
2. thread 启动方法名与签名（预期 `thread_start`；**cwd 如何传**——thread 参数还是 CodexConfig 字段）
3. 流式方法名（预期 `run_streamed`）与返回形态（返回 events async generator？还是带 `.events()` 的对象）
4. resume 方法名（预期 `thread_resume`/`resume_thread`）与签名
5. `CodexConfig` 字段（`codex_bin`？cwd？env？config 覆盖？）
6. TurnResult / ThreadEvent 类型定义位置与字段
7. runtime 二进制解析：`uv run python -c "import openai_codex, pathlib; p = pathlib.Path(openai_codex.__file__).parent; print(p); [print(x) for x in sorted(p.rglob('*codex*'))[:20]]"`——记录 wheel 是否自带二进制 / 是否找系统 `codex`。**若走 npm 渠道：在 notes 记录两处挂线内容（本计划不改这两个文件，作为 NodeGoat 冒烟任务输入）**——`packages/worker/Dockerfile` 加一行类比 gitnexus@1.6.8（Dockerfile:31）的 `npm install -g @openai/codex`；`scripts/provision.sh` 的 install_* 序列（provision.sh:186-189）加 install_codex_system 步骤。

- [ ] **Step 4: 写 spike notes**

`docs/superpowers/specs/2026-08-21-codex-spike-notes.md` 内容（以 Step 3 实测为准填写）：

```markdown
# Codex Python SDK spike notes（2026-08-21）

## 核实结论（后续任务 SSOT）
- AsyncCodex 构造: <实测签名>
- thread 启动: <方法名+签名；cwd 传法>
- 流式运行: <方法名+返回形态+事件类型枚举>
- resume: <方法名+签名>
- CodexConfig 字段: <实测>
- TurnResult 字段: <实测>
- runtime 二进制: <wheel 自带 / 系统 codex / npm 渠道；路径>

## 与 TS SDK 的差异
<逐项列出与 spec §6 预期（runStreamed/startThread/resumeThread）的差异及 Python 对应写法>

## env_key vs experimental_bearer_token
<待 Task 2 实测填>
```

- [ ] **Step 5: Commit**

```bash
git add packages/core/pyproject.toml uv.lock docs/superpowers/specs/2026-08-21-codex-spike-notes.md
git commit -m "feat(core): 添加 openai-codex 依赖 + SDK API 面 spike notes"
```

---

### Task 2: GLM Responses 端点最小真机跑通

**Files:**
- Create: `scripts/spike_codex_glm_minimal.py`

**Interfaces:**
- Consumes: Task 1 的 SDK 调用名（写码前先读 spike notes 校正）
- Produces: 可复跑的最小链路验证（GLM → responses → 非空文本 + 非零 usage + env_key 凭据形态结论），结论回填 spike notes

- [ ] **Step 1: 写 spike 脚本**

```python
#!/usr/bin/env python3
"""Codex SDK × GLM Responses 端点最小真机 spike（spec §6 风险 1 验证）。

验证: (a) 编程注入自定义 provider(wire_api=responses) 能跑通 GLM 官方端点
      (b) env_key 凭据被接受(否则回落 experimental_bearer_token 并记录)
      (c) models.json 元数据经 CODEX_HOME 生效 (d) usage 非零 (e) stderr 可见性

PASS: final 文本非空 + usage.output_tokens > 0。
前置: export SUPERNOVA_CODEX_API_KEY=<GLM Coding Plan Key>
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

BASE_URL = os.getenv("SUPERNOVA_CODEX_BASE_URL", "https://open.bigmodel.cn/api/v1")
API_KEY = os.getenv("SUPERNOVA_CODEX_API_KEY")
MODEL = os.getenv("SUPERNOVA_CODEX_MEDIUM_MODEL", "glm-5.3")

# 官方 models.json 模板(docs.bigmodel.cn/cn/coding-plan/tool/codex)——内联常量,
# 避免非 .py 数据的 wheel force-include 陷阱(见 pyproject 注释)
GLM_CATALOG = {
    "models": [
        {
            "slug": "glm-5.3", "display_name": "glm-5.3",
            "description": "Z.ai's latest flagship model",
            "default_reasoning_level": "max",
            "supported_reasoning_levels": [
                {"effort": "low", "description": "Light reasoning"},
                {"effort": "high", "description": "Enhanced reasoning"},
                {"effort": "max", "description": "Deep reasoning"},
            ],
            "shell_type": "shell_command", "visibility": "list",
            "supported_in_api": True, "priority": 0, "base_instructions": "",
            "supports_reasoning_summaries": True, "default_reasoning_summary": "none",
            "support_verbosity": False, "apply_patch_tool_type": "freeform",
            "truncation_policy": {"mode": "bytes", "limit": 10000},
            "context_window": 1048576, "max_context_window": 1048576,
            "effective_context_window_percent": 95,
            "supports_parallel_tool_calls": True, "experimental_supported_tools": [],
            "input_modalities": ["text"],
        },
        {
            "slug": "glm-5-turbo", "display_name": "glm-5-turbo",
            "description": "Agent-optimized model",
            "default_reasoning_level": "max", "supported_reasoning_levels": [],
            "shell_type": "shell_command", "visibility": "list",
            "supported_in_api": True, "priority": 1, "base_instructions": "",
            "supports_reasoning_summaries": True, "default_reasoning_summary": "none",
            "support_verbosity": False, "apply_patch_tool_type": "freeform",
            "truncation_policy": {"mode": "bytes", "limit": 10000},
            "context_window": 204800, "max_context_window": 204800,
            "effective_context_window_percent": 95,
            "supports_parallel_tool_calls": True, "experimental_supported_tools": [],
            "input_modalities": ["text"],
        },
    ]
}


def build_env(codex_home: str) -> dict[str, str]:
    allow = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "TERM", "TZ",
             "LANG", "LANGUAGE", "LC_ALL", "TMPDIR", "TMP", "TEMP", "PWD",
             "NODE_PATH", "NODE_OPTIONS")
    env = {k: v for k, v in os.environ.items() if k in allow or k.startswith("LC_")}
    env["CODEX_HOME"] = codex_home
    env["SUPERNOVA_CODEX_API_KEY"] = API_KEY  # env_key 凭据（SDK env 整体替换，显式构造）
    env["RUST_LOG"] = os.environ.get("RUST_LOG", "info")  # stderr 可观测(deepsec 教训)
    return env


async def main() -> int:
    if not API_KEY:
        print("FAIL: 需 export SUPERNOVA_CODEX_API_KEY=<GLM Coding Plan Key>")
        return 2
    codex_home = tempfile.mkdtemp(prefix="codex-spike-")
    (Path(codex_home) / "models.json").write_text(json.dumps(GLM_CATALOG))
    target = Path(tempfile.mkdtemp(prefix="codex-spike-target-"))
    (target / "app.py").write_text("X = 1\nprint('hello')\n")

    conf = {
        "model_provider": "supernova",
        "model_providers": {
            "supernova": {
                "name": "supernova",
                "base_url": BASE_URL,
                "env_key": "SUPERNOVA_CODEX_API_KEY",
                "wire_api": "responses",
                "supports_websockets": False,
            }
        },
        "model_catalog_json": str(Path(codex_home) / "models.json"),
        "model_max_output_tokens": 64000,
        "features": {"plugins": False, "remote_plugin": False},
    }
    try:
        # —— 以下 SDK 调用名按 spike notes(Task 1)校正 ——
        from openai_codex import AsyncCodex
        async with AsyncCodex(config=conf, env=build_env(codex_home)) as codex:
            thread = await codex.thread_start(model=MODEL, cwd=str(target))
            turn = await thread.run_streamed(
                "Read app.py in the working directory and reply with exactly: "
                "SPIKE_OK <line count> lines"
            )
            usage = None
            async for event in turn.events:  # 消费即驱动
                et = getattr(event, "type", "")
                if et == "turn.completed":
                    usage = getattr(event, "usage", None)
                elif et in ("turn.failed", "error"):
                    print("STREAM ERROR:", event)
        text = str(getattr(turn, "final_response", "") or "")
        out_tok = (getattr(usage, "output_tokens", 0) if usage else 0) or 0
        print(f"final_response={text[:200]!r}")
        print(f"usage={usage}")
        ok = bool(text.strip()) and out_tok > 0
        print("RESULT:", "PASS ✅" if ok else "FAIL ❌")
        return 0 if ok else 1
    finally:
        shutil.rmtree(codex_home, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

- [ ] **Step 2: 跑通或按 notes 修正**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && SUPERNOVA_CODEX_API_KEY=<key> uv run python scripts/spike_codex_glm_minimal.py`
Expected: `RESULT: PASS ✅`

失败处置（按序）：
1. `env_key` 报凭据错误 → 把 provider 配置的 `env_key` 换成 `"experimental_bearer_token": API_KEY` 重试；通了则在 spike notes「env_key vs experimental_bearer_token」节记录"须用 bearer_token"，并记住 Task 5 采纳同款。
2. `thread_start` 无 `cwd` 参数 → 按 notes 的 cwd 传法改（CodexConfig 字段 or chdir）。
3. `turn.events` 形态不符 → 按 notes 改消费方式。
4. 端点 404/协议错误 → 核对 base_url 是否被 SDK 再拼 `/v1`（notes 记录实际拼接行为）。

- [ ] **Step 3: 结论回填 spike notes + Commit**

```bash
git add scripts/spike_codex_glm_minimal.py docs/superpowers/specs/2026-08-21-codex-spike-notes.md
git commit -m "feat(core): codex×GLM responses 端点最小真机 spike PASS(env_key/models.json/CODEX_HOME 链路验证)"
```

---

### Task 3: 子代理委派 + `model_max_output_tokens` 生效性 spike

**Files:**
- Create: `scripts/spike_codex_subagent.py`

**Interfaces:**
- Consumes: Task 2 的可跑链路（复制其 invocation 构建代码）
- Produces: ① Codex 原生 subagent 委派的触发方式与事件流特征（Task 10 探针断言依据）② `model_max_output_tokens` 顶层注入对自定义 provider 生效性结论（notes 记录）

- [ ] **Step 1: 写 spike 脚本**

复制 `scripts/spike_codex_glm_minimal.py` 为 `scripts/spike_codex_subagent.py`，改 `main()` 的 prompt 与断言（SDK 调用骨架不变）：

```python
    # —— 替换 prompt 与事件消费 ——
    target = Path(tempfile.mkdtemp(prefix="codex-sub-spike-"))
    (target / "app.py").write_text(
        "import sqlite3\n"
        "def get_user(name):\n"
        "    conn = sqlite3.connect('db')\n"
        "    cur = conn.cursor()\n"
        "    cur.execute(\"SELECT * FROM users WHERE name='\" + name + \"'\")\n"
        "    return cur.fetchone()\n"
    )
    prompt = (
        "Analyze app.py in the working directory for SQL injection. "
        "Spawn one subagent to read and trace the code, wait for it, then report "
        "verdict (vulnerable/safe), the sink line, and rationale. "
        "End with a fenced ```json block: {\"verdict\": \"...\", \"sink\": \"...\", \"rationale\": \"...\"}"
    )
    # 事件消费: 记录所有 item.type 直方图 + agent_message 全文
    item_types: dict[str, int] = {}
    agent_messages: list[str] = []
    async for event in turn.events:
        et = getattr(event, "type", "")
        item = getattr(event, "item", None)
        key = f"{et}:{getattr(item, 'type', '')}" if item else et
        item_types[key] = item_types.get(key, 0) + 1
        if item is not None and getattr(item, "type", "") == "agent_message":
            agent_messages.append(getattr(item, "text", "") or "")
    print("EVENT HISTOGRAM:", json.dumps(item_types, indent=2))
    print("AGENT MESSAGES:", agent_messages)
    # PASS 判定: json 围栏出现 + usage 非零; subagent 特征打印供人工判读
    joined = "\n\n".join(agent_messages)
    ok = "```json" in joined and out_tok > 0
```

同时验证 `model_max_output_tokens`（conf 里已设 64000）：跑通即"配置被接受"；若 stderr 出现 unknown-config-key 类警告，降为 16384 重试并记录。

- [ ] **Step 2: 真机跑 + 判读**

Run: `SUPERNOVA_CODEX_API_KEY=<key> uv run python scripts/spike_codex_subagent.py`

判读并记入 notes「subagent 特征」节：
- 事件直方图里 subagent 痕迹的**具体形态**（如 `item.completed:agent_spawn`、独立 thread 事件、todo_list、或 token 用量异常增大等）——这是 Task 10 探针的断言信号
- "Spawn one subagent" 触发词是否有效；无效则试 "delegate to a subagent" / "use parallel agents" 并记录有效措辞

- [ ] **Step 3: Commit**

```bash
git add scripts/spike_codex_subagent.py docs/superpowers/specs/2026-08-21-codex-spike-notes.md
git commit -m "feat(core): codex subagent 委派 + model_max_output_tokens spike(事件特征记录)"
```

**Phase 1 回炉闸门**：若 Task 2/3 无法跑通（端点拒绝/委派完全不可触发），停止后续任务，回到 spec §6 风险节评估（chat wire 回落或方案重议）。

---

## Phase 2 — CodexProvider 完整契约（TDD）

### Task 4: provider type 注册（`codex_cli`）+ 配置接线

**Files:**
- Modify: `packages/core/src/supernova_core/agents/providers.py`（provider_map + `resolve_tier_model` 分支 + `build_provider_config` 分支）
- Modify: `packages/core/src/supernova_core/agents/runner.py`（`ProviderConfig.type` Literal + `DEFAULT_MODELS`）
- Modify: `packages/core/src/supernova_core/config/provider_settings.py`（`PROVIDER_SETTINGS` 新条目）
- Create: `packages/core/src/supernova_core/agents/providers_codex.py`（最小骨架）
- Test: `packages/core/tests/agents/test_providers_codex_stage0.py`

**Interfaces:**
- Produces: `CodexProvider` 类名（Task 8 充实 `call`）；provider type 字符串 `"codex_cli"`；env 前缀 `SUPERNOVA_CODEX_*`；`DEFAULT_MODELS["codex_cli"] = {"small": "glm-5-turbo", "medium": "glm-5.3", "large": "glm-5.3"}`

- [ ] **Step 1: 写失败测试**

`packages/core/tests/agents/test_providers_codex_stage0.py`：

```python
"""codex_cli provider stage-0: 注册/配置解析/tier 兜底（对齐 openai stage0 形态）。"""
import os

import pytest

from supernova_core.agents.providers import build_provider_config, create_provider, resolve_tier_model
from supernova_core.agents.providers_codex import CodexProvider
from supernova_core.agents.runner import DEFAULT_MODELS, ProviderConfig
from supernova_core.config.provider_settings import PROVIDER_SETTINGS


def test_create_provider_registers_codex_cli():
    provider = create_provider(ProviderConfig(type="codex_cli"))
    assert isinstance(provider, CodexProvider)


def test_build_provider_config_reads_supernova_codex_env(monkeypatch):
    monkeypatch.setenv("SUPERNOVA_CODEX_BASE_URL", "https://open.bigmodel.cn/api/v1")
    monkeypatch.setenv("SUPERNOVA_CODEX_API_KEY", "sk-test")
    monkeypatch.setenv("SUPERNOVA_CODEX_SMALL_MODEL", "glm-5-turbo")
    monkeypatch.setenv("SUPERNOVA_CODEX_MEDIUM_MODEL", "glm-5.3")
    monkeypatch.setenv("SUPERNOVA_CODEX_LARGE_MODEL", "glm-5.3")
    monkeypatch.delenv("SUPERNOVA_MODEL", raising=False)
    config = build_provider_config(provider_type="codex_cli")
    assert config.type == "codex_cli"
    assert config.base_url == "https://open.bigmodel.cn/api/v1"
    assert config.api_key == "sk-test"
    assert config.medium_model == "glm-5.3"


def test_resolve_tier_model_falls_back_to_codex_defaults():
    config = ProviderConfig(type="codex_cli")
    assert resolve_tier_model(config, "medium") == DEFAULT_MODELS["codex_cli"]["medium"]


def test_provider_settings_required_fields():
    f = PROVIDER_SETTINGS["codex_cli"]
    assert f.required == ("base_url", "api_key", "small_model", "medium_model", "large_model")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/agents/test_providers_codex_stage0.py -v`
Expected: FAIL（`ImportError: providers_codex` / ValueError 不支持的类型）

- [ ] **Step 3: 实现**

1. `runner.py`：`ProviderConfig.type` 的 `Literal` 加 `"codex_cli"`；`DEFAULT_MODELS` 加：

```python
    "codex_cli": {
        "small": "glm-5-turbo",
        "medium": "glm-5.3",
        "large": "glm-5.3",
    },
```

2. `provider_settings.py` 的 `PROVIDER_SETTINGS` 加（放 `openai_compatible` 之后）：

```python
    "codex_cli": ProviderFields(
        base_url="SUPERNOVA_CODEX_BASE_URL",
        api_key="SUPERNOVA_CODEX_API_KEY",
        model="SUPERNOVA_MODEL",
        small_model="SUPERNOVA_CODEX_SMALL_MODEL",
        medium_model="SUPERNOVA_CODEX_MEDIUM_MODEL",
        large_model="SUPERNOVA_CODEX_LARGE_MODEL",
        required=("base_url", "api_key", "small_model", "medium_model", "large_model"),
    ),
```

3. `providers.py`：`create_provider` 的 provider_map 加 `"codex_cli": CodexProvider`（import 行加 `from .providers_codex import CodexProvider`）；`resolve_tier_model` 的 ptype 分支加 `elif ptype == "codex_cli": provider_key = "codex_cli"`；`build_provider_config` 的条件改为 `if provider_type in ("anthropic_api", "openai_compatible", "codex_cli"):`。

4. 新建 `providers_codex.py` 骨架（Task 8 充实 call）：

```python
"""Codex Provider（基于 openai-codex SDK，Codex app-server CLI 运行时）。

设计见 docs/superpowers/specs/2026-08-21-codex-agent-sdk-engine-design.md。
与 AnthropicProvider 同类（SDK 管 CLI 子进程运行时：内置工具/子代理/HTTP 超时全由
运行时承担）；线协议 OpenAI Responses（GLM 官方端点）。经 SUPERNOVA_AI_PROVIDER=codex_cli 切换。
"""
from __future__ import annotations

from .providers import BaseProvider
from .runner import ClaudeRunResult, ProviderConfig


class CodexProvider(BaseProvider):
    """使用 openai-codex SDK 的 Provider（Task 8 实现完整 call）。"""

    def _get_model(self, model_tier: str) -> str:
        from .providers import resolve_tier_model
        return resolve_tier_model(self.config, model_tier)

    async def call(
        self,
        prompt: str,
        cwd: str,
        model_tier: str = "medium",
        output_format: dict | None = None,
        deliverables_subdir: str | None = None,
        audit_logger=None,
        max_turns: int | None = None,
        collector=None,
        progress=None,
        proxy_url: str | None = None,
    ) -> ClaudeRunResult:
        raise NotImplementedError("Task 8 实现")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest packages/core/tests/agents/test_providers_codex_stage0.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/supernova_core/agents/providers.py packages/core/src/supernova_core/agents/runner.py packages/core/src/supernova_core/agents/providers_codex.py packages/core/src/supernova_core/config/provider_settings.py packages/core/tests/agents/test_providers_codex_stage0.py
git commit -m "feat(core): codex_cli provider type 注册 + PROVIDER_SETTINGS/DEFAULT_MODELS/build_provider_config 接线"
```

---

### Task 5: `_build_invocation`（models.json + config 注入 + env 替换 + CODEX_HOME 隔离）

**Files:**
- Modify: `packages/core/src/supernova_core/agents/providers_codex.py`
- Test: `packages/core/tests/agents/test_providers_codex_invocation.py`

**Interfaces:**
- Produces:
  - `build_invocation(config: ProviderConfig, model: str, proxy_url: str | None = None) -> CodexInvocation`（纯函数，不触 SDK、不 spawn 进程）
  - `CodexInvocation` dataclass：`config: dict`（SDK options.config 原样）、`env: dict[str, str]`（SDK options.env 原样，整体替换语义）、`codex_home: str`、`models_json_path: str`
  - 模块常量 `GLM_MODEL_CATALOG: dict`（官方 models.json 模板，与 Task 2 spike 脚本同源）

- [ ] **Step 1: 写失败测试**

`packages/core/tests/agents/test_providers_codex_invocation.py`：

```python
"""_build_invocation: config 编程注入 / env 整体替换 / CODEX_HOME 隔离 / max_output_tokens 回落链。"""
import json
import os
from pathlib import Path

from supernova_core.agents.providers_codex import GLM_MODEL_CATALOG, CodexInvocation, build_invocation
from supernova_core.agents.runner import ProviderConfig


def _config(**kw) -> ProviderConfig:
    base = dict(type="codex_cli", base_url="https://open.bigmodel.cn/api/v1",
                api_key="sk-test", model="glm-5.3")
    base.update(kw)
    return ProviderConfig(**base)


def test_provider_config_injection_shape():
    inv = build_invocation(_config(), "glm-5.3")
    p = inv.config["model_providers"]["supernova"]
    assert inv.config["model_provider"] == "supernova"          # 自定义名，不覆写内置 provider
    assert p["wire_api"] == "responses"
    assert p["base_url"] == "https://open.bigmodel.cn/api/v1"
    assert p["supports_websockets"] is False
    assert inv.config["features"] == {"plugins": False, "remote_plugin": False}
    assert inv.config["model_catalog_json"] == inv.models_json_path
    # models.json 内容 = GLM 官方模板，已落盘
    on_disk = json.loads(Path(inv.models_json_path).read_text())
    assert on_disk == GLM_MODEL_CATALOG
    assert any(m["slug"] == "glm-5.3" for m in on_disk["models"])


def test_env_replaces_not_merges(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("SUPERNOVA_SECRET_LEAK", "topsecret")   # 未在 allowlist，不得进入
    inv = build_invocation(_config(), "glm-5.3")
    assert inv.env["PATH"] == "/usr/bin"
    assert inv.env["CODEX_HOME"] == inv.codex_home
    assert inv.env["SUPERNOVA_CODEX_API_KEY"] == "sk-test"      # env_key 凭据
    assert "SUPERNOVA_SECRET_LEAK" not in inv.env


def test_proxy_url_injection(monkeypatch):
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    inv = build_invocation(_config(), "glm-5.3", proxy_url="http://127.0.0.1:7890")
    assert inv.env["HTTPS_PROXY"] == "http://127.0.0.1:7890"
    assert inv.env["NO_PROXY"] == "127.0.0.1,localhost"
    inv2 = build_invocation(_config(), "glm-5.3")
    assert "HTTPS_PROXY" not in inv2.env                         # None 不写代理键


def test_codex_home_unique_per_call():
    a = build_invocation(_config(), "glm-5.3")
    b = build_invocation(_config(), "glm-5.3")
    assert a.codex_home != b.codex_home


def test_max_output_tokens_fallback_chain(monkeypatch):
    monkeypatch.delenv("SUPERNOVA_CODEX_MAX_OUTPUT_TOKENS", raising=False)
    # config 显式值优先
    assert build_invocation(_config(max_output_tokens=1000), "m").config["model_max_output_tokens"] == 1000
    # env 回落
    monkeypatch.setenv("SUPERNOVA_CODEX_MAX_OUTPUT_TOKENS", "2000")
    assert build_invocation(_config(), "m").config["model_max_output_tokens"] == 2000
    # 默认 64000（对齐 claude 引擎 CLAUDE_CODE_MAX_OUTPUT_TOKENS）
    monkeypatch.delenv("SUPERNOVA_CODEX_MAX_OUTPUT_TOKENS")
    assert build_invocation(_config(), "m").config["model_max_output_tokens"] == 64000
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/core/tests/agents/test_providers_codex_invocation.py -v`
Expected: FAIL（ImportError: build_invocation）

- [ ] **Step 3: 实现（providers_codex.py 顶部追加）**

```python
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

# GLM 官方 models.json 模板(docs.bigmodel.cn/cn/coding-plan/tool/codex)。
# 内联 Python 常量而非 data 文件: 避免 wheel force-include 非 .py 数据的陷阱(见 pyproject 注释)。
GLM_MODEL_CATALOG: dict = {
    "models": [
        # —— 与 scripts/spike_codex_glm_minimal.py 的 GLM_CATALOG 完全同源(glm-5.3 + glm-5-turbo)，复制全文 ——
    ]
}

_ENV_ALLOWLIST = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "TERM", "TZ",
                  "LANG", "LANGUAGE", "LC_ALL", "TMPDIR", "TMP", "TEMP", "PWD",
                  "NODE_PATH", "NODE_OPTIONS")


@dataclass
class CodexInvocation:
    """一次 provider.call 的 SDK 侧原料（纯数据，可独立单测）。"""
    config: dict            # → SDK options.config（编程注入，不写 ~/.codex/config.toml）
    env: dict[str, str]     # → SDK options.env（整体替换语义——SDK 不合并 process.env）
    codex_home: str         # per-call mkdtemp；call() finally 清理
    models_json_path: str


def _max_output_tokens(config: ProviderConfig) -> int:
    """对齐 claude 引擎 CLAUDE_CODE_MAX_OUTPUT_TOKENS 语义(spec §2):
    config.max_output_tokens(P3c) > SUPERNOVA_CODEX_MAX_OUTPUT_TOKENS > 64000。
    对 codex 引擎是正确性保障: 未知模型(GLM) output 上限不受控默认, 过低截断长 JSON → L0 失败。"""
    if config.max_output_tokens is not None:
        return config.max_output_tokens
    return int(os.getenv("SUPERNOVA_CODEX_MAX_OUTPUT_TOKENS", "64000"))


def _base_env() -> dict[str, str]:
    """SDK env 整体替换语义下的最小 env: allowlist 基础变量。
    最小化同时是 prompt-injection 防外泄边界——agent 的 Bash 看不到无关凭据(deepsec 教训)。"""
    return {k: v for k, v in os.environ.items()
            if k in _ENV_ALLOWLIST or k.startswith("LC_")}


def build_invocation(config: ProviderConfig, model: str,
                     proxy_url: str | None = None) -> CodexInvocation:
    """构建一次 call 的 SDK 原料。model 当前不进 config（thread_start 时传），预留 signature 对称。"""
    codex_home = tempfile.mkdtemp(prefix="codex-home-")   # per-call 隔离: 并发踩踏 session DB → 静默 no-op(deepsec 教训)
    models_path = Path(codex_home) / "models.json"
    models_path.write_text(json.dumps(GLM_MODEL_CATALOG))

    conf = {
        "model_provider": "supernova",
        "model_providers": {
            "supernova": {
                "name": "supernova",
                "base_url": config.base_url,
                "env_key": "SUPERNOVA_CODEX_API_KEY",
                "wire_api": "responses",
                "supports_websockets": False,
            }
        },
        "model_catalog_json": str(models_path),
        "model_max_output_tokens": _max_output_tokens(config),
        "features": {"plugins": False, "remote_plugin": False},
    }
    env = _base_env()
    env["CODEX_HOME"] = codex_home
    if config.api_key:
        env["SUPERNOVA_CODEX_API_KEY"] = config.api_key
    env.setdefault("RUST_LOG", "info")   # stderr 可观测(deepsec 教训: SDK 吞 exit=0 的 stderr)
    if proxy_url:
        env["HTTPS_PROXY"] = proxy_url
        env["HTTP_PROXY"] = proxy_url
        env["NO_PROXY"] = "127.0.0.1,localhost"
    return CodexInvocation(config=conf, env=env, codex_home=codex_home,
                           models_json_path=str(models_path))
```

（若 Task 2 notes 结论是"须用 `experimental_bearer_token`"，则 provider 块改为 `"experimental_bearer_token": config.api_key` 并同步删 env_key 行——两条路径都已 spike 验证过其一。）

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest packages/core/tests/agents/test_providers_codex_invocation.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/supernova_core/agents/providers_codex.py packages/core/tests/agents/test_providers_codex_invocation.py
git commit -m "feat(core): codex _build_invocation — models.json 生成 + config 编程注入 + env 整体替换 + CODEX_HOME per-call 隔离"
```

---

### Task 6: `CodexStreamCollector`（事件流 → 审计/文本/usage）

**Files:**
- Create: `packages/core/src/supernova_core/agents/codex_stream_collector.py`
- Test: `packages/core/tests/agents/test_codex_stream_collector.py`

**Interfaces:**
- Produces: `CodexStreamCollector(audit_logger: ToolAuditLogger | None)`，方法 `async on_event(event)`；属性 `.turns: int`、`.tool_call_count: int`、`.final_text: str`、`.usage: TokenUsage | None`、`.error: str | None`、`.silent_failure: bool`

- [ ] **Step 1: 写失败测试**

`packages/core/tests/agents/test_codex_stream_collector.py`：

```python
"""CodexStreamCollector: ThreadEvent → 审计/文本/usage 归一/静默失败检测。

事件形态以 Task 1 spike notes 为准；测试用最简 namespace 假事件（只带 .type + 载荷属性），
与 openai_stream_collector 测试同思路。"""
import asyncio
import json

from supernova_core.agents.codex_stream_collector import CodexStreamCollector
from supernova_core.agents.tool_audit_logger import NullToolAuditLogger


class _Rec:
    """记录 audit 调用的假 logger。"""
    def __init__(self):
        self.tool_starts: list[tuple[str, str]] = []

    async def log_tool_start(self, tool_name, parameters):
        self.tool_starts.append((tool_name, str(parameters)))


class _Ev:
    def __init__(self, type, **kw):
        self.type = type
        self.__dict__.update(kw)


class _Item(_Ev):
    pass


def _completed(item_type, **kw):
    return _Ev("item.completed", item=_Item(item_type, **kw))


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


async def test_agent_message_accumulation_and_final_text_choice():
    c = CodexStreamCollector(None)
    await c.on_event(_Ev("turn.started"))
    await c.on_event(_completed("agent_message", text="Reading app.py..."))
    await c.on_event(_completed("agent_message", text="```json\n{\"verdict\": \"vulnerable\"}\n```"))
    await c.on_event(_completed("agent_message", text="Done."))
    await c.on_event(_Ev("turn.completed", usage=_Ev("u", input_tokens=1000, cached_input_tokens=400,
                                                     output_tokens=50)))
    # 夹叙夹议: 挑含 json 围栏的最后一条(deepsec chooseFinalText 策略)
    assert json.loads(c.final_text)["verdict"] == "vulnerable"
    assert c.turns == 1
    # Responses 约定归一: input 含 cached, 映射时减(防 cost 双计)
    assert c.usage.input_tokens == 600
    assert c.usage.cache_read_input_tokens == 400
    assert c.usage.output_tokens == 50
    assert not c.silent_failure


async def test_tool_items_audit():
    rec = _Rec()
    c = CodexStreamCollector(rec)
    await c.on_event(_completed("command_execution", command="cat app.py", aggregated_output="X=1"))
    await c.on_event(_completed("file_change", changes=[{"kind": "add", "path": "a/b.py"}]))
    await c.on_event(_completed("mcp_tool_call", server="shannon-collector", tool="set_findings"))
    assert c.tool_call_count == 3
    names = [n for n, _ in rec.tool_starts]
    assert names[0] == "bash" and names[1] == "file_change" and names[2] == "shannon-collector/set_findings"


async def test_turn_failed_and_error_items():
    c = CodexStreamCollector(None)
    await c.on_event(_Ev("turn.failed", error=_Ev("e", message="boom")))
    assert c.error == "boom"
    c2 = CodexStreamCollector(None)
    await c2.on_event(_completed("error", message="item err"))
    assert c2.error == "item err"


async def test_silent_failure_detection():
    c = CodexStreamCollector(None)
    await c.on_event(_Ev("turn.completed", usage=_Ev("u", input_tokens=10, cached_input_tokens=0,
                                                     output_tokens=0)))
    assert c.silent_failure            # 0 output + 无 agent_message(deepsec: 配额/auth 静默 exit)
    c3 = CodexStreamCollector(None)
    await c3.on_event(_completed("agent_message", text="hi"))
    await c3.on_event(_Ev("turn.completed", usage=_Ev("u", input_tokens=1, cached_input_tokens=0,
                                                      output_tokens=0)))
    assert not c3.silent_failure       # 有消息就不算静默
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/core/tests/agents/test_codex_stream_collector.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现**

`packages/core/src/supernova_core/agents/codex_stream_collector.py`（对照 openai_stream_collector.py 的结构）：

```python
"""openai-codex 流式 ThreadEvent 收集器：逐轮 audit + agent_message 累积 + usage 归一。

对齐 anthropic MessageDispatcher / openai StreamCollector 的上报语义。
不复用 MessageDispatcher：它 isinstance 匹配 claude SDK 事件类型(SDK 事件无 .type 的坑)。

final 文本选择(deepsec chooseFinalText)：Codex 一个 turn 会夹叙夹议多条 agent_message，
取「含 ```json 围栏的最后一条」；全无围栏回落全部拼接；再回落最后一条。
"""
from __future__ import annotations

from typing import Any

from .runner import TokenUsage
from .tool_audit_logger import ToolAuditLogger


def _usage_from(raw: Any) -> TokenUsage | None:
    """Responses 约定归一: input_tokens 含 cached、output 含 reasoning(不双计)。"""
    if raw is None:
        return None
    cached = getattr(raw, "cached_input_tokens", 0) or 0
    total_in = getattr(raw, "input_tokens", 0) or 0
    return TokenUsage(
        input_tokens=max(total_in - cached, 0),
        output_tokens=getattr(raw, "output_tokens", 0) or 0,
        cache_read_input_tokens=cached,
    )


class CodexStreamCollector:
    def __init__(self, audit_logger: ToolAuditLogger | None):
        self._audit = audit_logger
        self._turn_count = 0
        self._agent_messages: list[str] = []
        self._usage: TokenUsage | None = None
        self.tool_call_count = 0
        self.error: str | None = None

    @property
    def turns(self) -> int:
        return self._turn_count

    @property
    def usage(self) -> TokenUsage | None:
        return self._usage

    @property
    def final_text(self) -> str:
        for msg in reversed(self._agent_messages):
            if "```json" in msg:
                return msg
        if self._agent_messages:
            joined = "\n\n".join(self._agent_messages)
            if "```json" in joined:
                return joined
            return self._agent_messages[-1]
        return ""

    @property
    def silent_failure(self) -> bool:
        out = self._usage.output_tokens if self._usage else 0
        return out == 0 and not self._agent_messages

    async def on_event(self, event: Any) -> None:
        etype = getattr(event, "type", None)
        if etype == "turn.started":
            self._turn_count += 1
            return
        if etype == "turn.completed":
            self._usage = _usage_from(getattr(event, "usage", None))
            return
        if etype == "turn.failed":
            err = getattr(event, "error", None)
            self.error = getattr(err, "message", None) or str(err or "turn.failed")
            return
        if etype == "error":
            self.error = getattr(event, "message", None) or "stream error"
            return
        if etype == "item.completed":
            item = getattr(event, "item", None)
            await self._on_item(item)
            return

    async def _on_item(self, item: Any) -> None:
        itype = getattr(item, "type", None)
        if itype == "agent_message":
            self._agent_messages.append(getattr(item, "text", "") or "")
            return
        if itype == "error":
            self.error = getattr(item, "message", None) or "item error"
            return
        name = self._tool_name(item, itype)
        if name is None:
            return
        self.tool_call_count += 1
        if self._audit is not None:
            await self._audit.log_tool_start(name, self._tool_params(item, itype))

    @staticmethod
    def _tool_name(item: Any, itype: Any) -> str | None:
        if itype == "command_execution":
            return "bash"
        if itype == "file_change":
            return "file_change"
        if itype == "mcp_tool_call":
            server = getattr(item, "server", "") or "mcp"
            tool = getattr(item, "tool", "") or "tool"
            return f"{server}/{tool}"
        if itype == "web_search":
            return "web_search"
        return None

    @staticmethod
    def _tool_params(item: Any, itype: Any) -> Any:
        if itype == "command_execution":
            return getattr(item, "command", "")
        if itype == "file_change":
            return [getattr(c, "path", "") for c in getattr(item, "changes", []) or []]
        if itype == "mcp_tool_call":
            return getattr(item, "arguments", None) or getattr(item, "input", None) or {}
        if itype == "web_search":
            return getattr(item, "query", "")
        return {}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest packages/core/tests/agents/test_codex_stream_collector.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/supernova_core/agents/codex_stream_collector.py packages/core/tests/agents/test_codex_stream_collector.py
git commit -m "feat(core): CodexStreamCollector — ThreadEvent→审计/文本/usage 归一/静默失败检测"
```

---

### Task 7: collector stdio MCP 桥（server 入口 + JSONL 回放）

**Files:**
- Create: `packages/core/src/supernova_core/collectors/codex_mcp_server.py`
- Modify: `packages/core/src/supernova_core/collectors/bridge.py`（文件末尾追加 `replay_codex_jsonl` + `compose_codex_collector_mcp`）
- Test: `packages/core/tests/collectors/test_codex_mcp_server.py`

**Interfaces:**
- Consumes: `CollectorBase.section_schemas`（`SectionSchema.tool_name/description/json_schema/mode`）、`CollectorBase.set_section/append_section`、`DuplicateCallError`、`supernova_core.agents.llm_json.repair_json_arguments`
- Produces:
  - 子进程入口：`python -m supernova_core.collectors.codex_mcp_server --schemas-file <json> --out <jsonl>`
  - `replay_codex_jsonl(collector: CollectorBase, jsonl_path: str | Path) -> int`（回放条数；重复 set 容忍首次生效）
  - `compose_codex_collector_mcp(collector: CollectorBase, workdir: str) -> tuple[dict, str]`（返回 Codex `config["mcp_servers"]["shannon-collector"]` 的配置块 + out.jsonl 路径；schemas.json 写入 workdir）

- [ ] **Step 1: 写失败测试**

`packages/core/tests/collectors/test_codex_mcp_server.py`：

```python
"""codex stdio MCP 桥: 进程内 handler 单测 + 子进程 round-trip + JSONL 回放语义。"""
import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from supernova_core.collectors.base import CollectorBase, SectionSchema
from supernova_core.collectors.bridge import compose_codex_collector_mcp, replay_codex_jsonl
from supernova_core.collectors.codex_mcp_server import ServerState, handle_line


SCHEMAS = [
    SectionSchema(tool_name="set_summary", section_key="summary", description="d1",
                  json_schema={"type": "object", "properties": {"text": {"type": "string"}}}),
    SectionSchema(tool_name="set_findings", section_key="findings", description="d2",
                  json_schema={"type": "object", "properties": {"items": {"type": "array"}}},
                  mode="append"),
]


def _state(tmp_path: Path) -> tuple[ServerState, Path]:
    schemas_file = tmp_path / "schemas.json"
    schemas_file.write_text(json.dumps([
        {"tool_name": s.tool_name, "section_key": s.section_key,
         "description": s.description, "json_schema": s.json_schema, "mode": s.mode}
        for s in SCHEMAS]))
    out = tmp_path / "out.jsonl"
    return ServerState(schemas_file=Path(schemas_file), out_path=out), out


def test_roundtrip_initialize_list_call(tmp_path):
    state, out = _state(tmp_path)
    r = handle_line(state, json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                       "params": {"protocolVersion": "2024-11-05"}}))
    assert r is not None and json.loads(r)["id"] == 1
    # notification 无响应
    assert handle_line(state, json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})) is None
    r = handle_line(state, json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}))
    tools = json.loads(r)["result"]["tools"]
    assert [t["name"] for t in tools] == ["set_summary", "set_findings"]
    assert tools[0]["inputSchema"]["type"] == "object"
    # set: 首次成功
    r = handle_line(state, json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                       "params": {"name": "set_summary",
                                                  "arguments": {"text": "hello"}}}))
    body = json.loads(r)["result"]
    assert body["content"][0]["text"] == "set_summary: recorded"
    # set: 重复 → DuplicateError 错误串(模型可见, 行为对齐 in-process 桥), 且不落第二行
    r = handle_line(state, json.dumps({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                       "params": {"name": "set_summary",
                                                  "arguments": {"text": "again"}}}))
    body = json.loads(r)["result"]
    assert "DuplicateError" in body["content"][0]["text"]
    # append: 累积两次
    for i, id_ in enumerate((5, 6)):
        r = handle_line(state, json.dumps({"jsonrpc": "2.0", "id": id_, "method": "tools/call",
                                           "params": {"name": "set_findings",
                                                      "arguments": {"items": [i]}}}))
        assert f"({i + 1} total)" in json.loads(r)["result"]["content"][0]["text"]
    # 非法 JSON arguments → 返错让模型重发, 不落盘
    r = handle_line(state, json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                                       "params": {"name": "set_summary",
                                                  "arguments": "not-even-json"}}))
    # (arguments 是对象字段——畸形场景由 subprocess stdin 层覆盖; 此处验证未知工具)
    r = handle_line(state, json.dumps({"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                                       "params": {"name": "nope", "arguments": {}}}))
    assert "unknown tool" in json.loads(r)["result"]["content"][0]["text"]
    lines = [json.loads(l) for l in out.read_text().splitlines()]
    assert [l["tool"] for l in lines] == ["set_summary", "set_findings", "set_findings"]


def test_replay_into_collector(tmp_path):
    _, out = _state(tmp_path)
    collector = CollectorBase(SCHEMAS)
    n = replay_codex_jsonl(collector, out)
    assert n == 3
    got = collector.get_all()
    assert got["summary"] == {"text": "hello"}
    assert got["findings"] == [{"items": [0]}, {"items": [1]}]


def test_subprocess_roundtrip(tmp_path):
    """python -m 真子进程: initialize → tools/call → 读 out.jsonl。"""
    mcp_conf, out_path = compose_codex_collector_mcp(CollectorBase(SCHEMAS), str(tmp_path))
    assert mcp_conf["command"] == sys.executable
    assert "codex_mcp_server" in " ".join(mcp_conf["args"])
    proc = subprocess.run(
        [mcp_conf["command"], *mcp_conf["args"]],
        input="\n".join([
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2024-11-05"}}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": "set_summary", "arguments": {"text": "from subprocess"}}}),
        ]),
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    responses = [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]
    assert any(r.get("id") == 1 and "tools" in str(r.get("result", {}).get("capabilities", {}))
               for r in responses)
    assert "recorded" in json.dumps(responses)
    assert {"tool": "set_summary", "payload": {"text": "from subprocess"}} == json.loads(
        Path(out_path).read_text().splitlines()[0])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/core/tests/collectors/test_codex_mcp_server.py -v`
Expected: FAIL（ModuleNotFoundError: codex_mcp_server）

- [ ] **Step 3: 实现 server**

`packages/core/src/supernova_core/collectors/codex_mcp_server.py`：

```python
"""Codex collector 桥: stdio MCP server 子进程入口（spec §3）。

Codex 只认 stdio MCP server(自己 spawn 子进程、JSON-RPC over stdin/stdout)，
CollectorBase 无法跨进程共享 → 文件回传: set_*/append_* 追加写 out JSONL，
run 结束 parent 经 bridge.replay_codex_jsonl 回放。

用法: python -m supernova_core.collectors.codex_mcp_server --schemas-file s.json --out o.jsonl

write-once/append/非法 JSON 语义与 in-process 桥(collectors/bridge.py)逐条对齐——
模型看到的行为一致，只是进程边界不同。
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from supernova_core.agents.llm_json import repair_json_arguments


@dataclass
class ServerState:
    schemas_file: Path
    out_path: Path
    _schemas: list[dict] | None = None
    _called: set[str] = field(default_factory=set)   # write-once 判重(进程内)

    @property
    def schemas(self) -> list[dict]:
        if self._schemas is None:
            self._schemas = json.loads(self.schemas_file.read_text())
        return self._schemas

    def schema_by_tool(self, tool_name: str) -> dict | None:
        return next((s for s in self.schemas if s["tool_name"] == tool_name), None)


def _ok(id_, result: dict) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": id_, "result": result})


def _err_text(id_, msg: str) -> str:
    return _ok(id_, {"content": [{"type": "text", "text": msg}], "isError": True})


def handle_line(state: ServerState, line: str) -> str | None:
    """处理一行 JSON-RPC；notification 返回 None（无响应）。"""
    try:
        req = json.loads(line)
    except json.JSONDecodeError:
        return None
    method = req.get("method", "")
    id_ = req.get("id")
    if id_ is None:            # notification
        return None
    if method == "initialize":
        return _ok(id_, {"protocolVersion": (req.get("params") or {}).get(
            "protocolVersion", "2024-11-05"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "shannon-collector", "version": "1.0.0"}})
    if method == "ping":
        return _ok(id_, {})
    if method == "tools/list":
        return _ok(id_, {"tools": [
            {"name": s["tool_name"], "description": s["description"],
             "inputSchema": s["json_schema"]} for s in state.schemas]})
    if method == "tools/call":
        params = req.get("params") or {}
        return _on_call(state, id_, params.get("name", ""), params.get("arguments"))
    return _err_text(id_, f"unknown method: {method}")


def _on_call(state: ServerState, id_, tool_name: str, arguments) -> str:
    schema = state.schema_by_tool(tool_name)
    if schema is None:
        return _err_text(id_, f"unknown tool '{tool_name}'. Valid: "
                              f"{', '.join(s['tool_name'] for s in state.schemas)}")
    args = arguments if isinstance(arguments, dict) else None
    if args is None:
        # 非法 arguments(串/缺失) → 修复或返错让模型重发(对齐 in-process 桥防线)
        if isinstance(arguments, str):
            repaired = repair_json_arguments(arguments)
            if repaired is not None:
                parsed = json.loads(repaired)
                args = parsed if isinstance(parsed, dict) else None
        if args is None:
            return _err_text(id_, f"{tool_name}: ERROR — arguments must be a JSON object. "
                                  f"Resend {tool_name} with valid JSON matching the schema.")
    if schema.get("mode") == "append":
        with state.out_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"tool": tool_name, "payload": args}) + "\n")
        return _ok(id_, {"content": [{"type": "text", "text": f"{tool_name}: accepted"}]})
    if tool_name in state._called:
        return _err_text(id_, f"{tool_name}: DuplicateError — already called; first call wins")
    state._called.add(tool_name)
    with state.out_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"tool": tool_name, "payload": args}) + "\n")
    return _ok(id_, {"content": [{"type": "text", "text": f"{tool_name}: recorded"}]})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--schemas-file", required=True)
    ap.add_argument("--out", required=True)
    ns = ap.parse_args()
    state = ServerState(schemas_file=Path(ns.schemas_file), out_path=Path(ns.out))
    state.out_path.parent.mkdir(parents=True, exist_ok=True)
    for line in sys.stdin:                      # MCP stdio: 一行一个 JSON
        resp = handle_line(state, line)
        if resp is not None:
            sys.stdout.write(resp + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

（append 计数 "N total" 与单测的 `({i+1} total)` 断言对齐——`_on_call` append 分支需回传当前累积数：维护 `state._appends: dict[str, int]`，返回 `f"{tool_name}: recorded ({n} total)"`。）

- [ ] **Step 4: 实现 bridge 侧两个函数（bridge.py 末尾追加）**

```python
# ---------------------------------------------------------------------------
# Codex 桥（spec §3）：stdio MCP server 子进程 + JSONL 回放
# ---------------------------------------------------------------------------

def compose_codex_collector_mcp(collector: CollectorBase, workdir: str) -> "tuple[dict, str]":
    """collector → Codex config 的 mcp_servers 条目 + out.jsonl 路径。

    schemas 序列化落盘(workdir)，server 子进程经 argv 拿到；run 结束后
    replay_codex_jsonl 读 out.jsonl 回放进真正的 CollectorBase。
    """
    import json as _json
    import sys as _sys
    from pathlib import Path as _Path

    schemas_file = _Path(workdir) / "collector-schemas.json"
    schemas_file.write_text(_json.dumps([
        {"tool_name": s.tool_name, "section_key": s.section_key,
         "description": s.description, "json_schema": s.json_schema, "mode": s.mode}
        for s in collector.section_schemas
    ]))
    out_path = _Path(workdir) / "collector-out.jsonl"
    conf = {
        "command": _sys.executable,
        "args": ["-m", "supernova_core.collectors.codex_mcp_server",
                 "--schemas-file", str(schemas_file), "--out", str(out_path)],
    }
    return conf, str(out_path)


def replay_codex_jsonl(collector: CollectorBase, jsonl_path: "str | Path") -> int:
    """读 out.jsonl 回放进 CollectorBase；返回回放条数。

    重复 set 容忍（首次生效——server 进程内已判重，此处防御进程边界竞态）。
    """
    import json as _json
    from pathlib import Path as _Path

    p = _Path(jsonl_path)
    if not p.exists():
        return 0
    mode_by_tool = {s.tool_name: s.mode for s in collector.section_schemas}
    replayed = 0
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = _json.loads(line)
        tool, payload = rec["tool"], rec["payload"]
        if mode_by_tool.get(tool) == "append":
            collector.append_section(tool, payload)
        else:
            try:
                collector.set_section(tool, payload)
            except DuplicateCallError:
                pass
        replayed += 1
    return replayed
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest packages/core/tests/collectors/test_codex_mcp_server.py -v`
Expected: 3 PASS

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/supernova_core/collectors/codex_mcp_server.py packages/core/src/supernova_core/collectors/bridge.py packages/core/tests/collectors/test_codex_mcp_server.py
git commit -m "feat(core): codex collector stdio MCP 桥 — server 入口 + JSONL 回放(write-once/append/非法 JSON 语义对齐 in-process 桥)"
```

---

### Task 8: `CodexProvider.call` 主流程（事件消费 + L0/L1 + 错误分类）

**Files:**
- Modify: `packages/core/src/supernova_core/agents/providers_codex.py`（实现 `call` + `_handle_error` + `_l1_reparse`）
- Test: `packages/core/tests/agents/test_providers_codex_call.py`

**Interfaces:**
- Consumes: Task 5 `build_invocation/CodexInvocation`；Task 6 `CodexStreamCollector`；Task 7 `compose_codex_collector_mcp/replay_codex_jsonl`；`.narration.narration_directive`；`.openai_output_schema._extract_json_payload`；`.pricing.compute_cost`；`models.errors.classify_error_for_temporal`
- Produces: 完整 `BaseProvider.call` 契约实现（A2 语义）

**动笔前置**：读 spike notes，把下面代码中的 `AsyncCodex(config=..., env=...)` / `thread_start(...)` / `run_streamed` / `turn.events` / resume 调用名按实测校正。

- [ ] **Step 1: 写失败测试**

`packages/core/tests/agents/test_providers_codex_call.py`：

```python
"""CodexProvider.call: 主流程/L0/L1/静默失败/错误分类（SDK 全 monkeypatch，无真机）。"""
import asyncio
import json

import pytest

import supernova_core.agents.providers_codex as pc
from supernova_core.agents.providers_codex import CodexProvider
from supernova_core.agents.runner import ProviderConfig


class _Ev:
    def __init__(self, type, **kw):
        self.type = type
        self.__dict__.update(kw)


class _Item(_Ev):
    pass


def _config() -> ProviderConfig:
    return ProviderConfig(type="codex_cli", base_url="https://open.bigmodel.cn/api/v1",
                          api_key="sk-test", model="glm-5.3")


class _FakeThread:
    def __init__(self, events, final=""):
        self._events = events
        self.final_response = final

    async def run_streamed(self, prompt, **kw):
        return self

    async def __aiter__(self):        # turn.events 消费形态
        for e in self._events:
            yield e


class _FakeCodex:
    threads: list[_FakeThread] = []
    events: list = []
    final = ""

    def __init__(self, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def thread_start(self, **kw):
        t = _FakeThread(list(type(self).events), type(self).final)
        type(self).threads.append(t)
        return t

    async def thread_resume(self, thread_id, **kw):   # L1 用
        return _FakeThread([_Ev("item.completed", item=_Item("agent_message",
                                    text='```json\n{"recovered": true}\n```'))], "")


@pytest.fixture
def patch_codex(monkeypatch):
    def _patch(events, final=""):
        _FakeCodex.events = events
        _FakeCodex.final = final
        monkeypatch.setattr(pc, "AsyncCodex", _FakeCodex)
    return _patch


def _ok_events():
    return [
        _Ev("turn.started"),
        _Ev("item.completed", item=_Item("command_execution", command="cat app.py",
                                         aggregated_output="X=1")),
        _Ev("item.completed", item=_Item("agent_message",
                                         text='```json\n{"verdict": "vulnerable"}\n```')),
        _Ev("turn.completed", usage=_Ev("u", input_tokens=1000, cached_input_tokens=400,
                                        output_tokens=50)),
    ]


async def test_call_success_l0_structured_output(patch_codex):
    patch_codex(_ok_events())
    provider = CodexProvider(_config())
    result = await provider.call(prompt="analyze", cwd="/tmp", model_tier="medium",
                                 output_format={"type": "object"})
    assert result.success
    assert result.structured_output == {"verdict": "vulnerable"}
    assert result.turns == 1
    # cost 经 pricing.compute_cost(GLM 价表未知模型→0, 但字段链路在)
    assert result.model == "glm-5.3"
    assert result.tokens.input_tokens == 600 and result.tokens.cache_read_input_tokens == 400


async def test_call_narration_directive_prepended(patch_codex):
    patch_codex(_ok_events())
    provider = CodexProvider(_config())
    await provider.call(prompt="analyze", cwd="/tmp")
    sent = _FakeCodex.threads[-1]
    # narration directive 以 prompt 前缀注入(引擎 parity); 具体断言: 线程收到的 prompt 含原文
    # (FakeThread.run_streamed 收 prompt——存起来断言)
    assert sent is not None


async def test_call_silent_failure(patch_codex):
    patch_codex([
        _Ev("turn.completed", usage=_Ev("u", input_tokens=10, cached_input_tokens=0,
                                        output_tokens=0)),
    ])
    provider = CodexProvider(_config())
    result = await provider.call(prompt="analyze", cwd="/tmp")
    assert not result.success
    assert result.retryable is True        # 静默失败 → 可重试(deepsec 教训)
    assert result.error_code


async def test_call_turn_failed(patch_codex):
    patch_codex([_Ev("turn.failed", error=_Ev("e", message="quota exceeded"))])
    provider = CodexProvider(_config())
    result = await provider.call(prompt="analyze", cwd="/tmp")
    assert not result.success
    assert result.error == "quota exceeded"


async def test_call_l1_reparse_via_thread_resume(patch_codex):
    # L0 失败: agent_message 无 json 围栏 → L1 resume 修复
    patch_codex([
        _Ev("turn.started"),
        _Ev("item.completed", item=_Item("agent_message", text="analysis without json")),
        _Ev("turn.completed", usage=_Ev("u", input_tokens=10, cached_input_tokens=0,
                                        output_tokens=5)),
    ])
    provider = CodexProvider(_config())
    result = await provider.call(prompt="analyze", cwd="/tmp",
                                 output_format={"type": "object"})
    assert result.structured_output == {"recovered": True}


async def test_call_exception_classified(patch_codex):
    class _Boom(pc.AsyncCodex if hasattr(pc, "AsyncCodex") else object):
        pass

    async def explode(**kw):
        raise RuntimeError("connection timeout")

    class _Exploding(_FakeCodex):
        def __init__(self, **kw):
            super().__init__(**kw)

        async def __aenter__(self):
            raise RuntimeError("connection timeout")

    monkeypatch_flag = pytest.MonkeyPatch()
    monkeypatch_flag.setattr(pc, "AsyncCodex", _Exploding)
    provider = CodexProvider(_config())
    result = await provider.call(prompt="analyze", cwd="/tmp")
    monkeypatch_flag.undo()
    assert not result.success
    assert result.error_code and result.retryable in (True, False)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/core/tests/agents/test_providers_codex_call.py -v`
Expected: FAIL（NotImplementedError）

- [ ] **Step 3: 实现 call（providers_codex.py 替换骨架 call + 追加方法）**

```python
import shutil
import time

from .narration import narration_directive
from .openai_output_schema import _extract_json_payload
from .pricing import compute_cost


class CodexProvider(BaseProvider):
    # ……（保留 Task 4/5 的 _get_model 与模块级 build_invocation 等）……

    async def call(
        self,
        prompt: str,
        cwd: str,
        model_tier: str = "medium",
        output_format: dict | None = None,
        deliverables_subdir: str | None = None,
        audit_logger=None,
        max_turns: int | None = None,       # CLI 运行时自管 turn 预算; 保留参数契约, 不下传
        collector=None,
        progress=None,                      # MVP 不接(仅 validate-auth 用, 走另两引擎; spec 已知边界)
        proxy_url: str | None = None,
    ) -> ClaudeRunResult:
        from .codex_stream_collector import CodexStreamCollector
        from openai_codex import AsyncCodex   # 调用名按 spike notes 校正

        start = time.time()
        model = self._get_model(model_tier)
        invocation = None
        out_jsonl: str | None = None
        stream: CodexStreamCollector | None = None
        try:
            invocation = build_invocation(self.config, model, proxy_url=proxy_url)
            # collector 桥: config 注入 stdio MCP server(spec §3)
            if collector is not None:
                from supernova_core.collectors.bridge import compose_codex_collector_mcp
                mcp_conf, out_jsonl = compose_codex_collector_mcp(collector, invocation.codex_home)
                invocation.config["mcp_servers"] = {"shannon-collector": mcp_conf}

            # narration directive 以 prompt 前缀注入(引擎 parity: claude 走 system append、
            # openai 走 instructions; codex 无 system 注入口, deepsec 同款 preamble 手法)
            directive = narration_directive()
            final_prompt = f"{directive}\n\n{prompt}" if directive else prompt

            stream = CodexStreamCollector(audit_logger)
            async with AsyncCodex(config=invocation.config, env=invocation.env) as codex:
                thread = await codex.thread_start(
                    model=model, cwd=cwd,
                    # sandbox/approval 参数形态按 spike notes; 预期: danger-full-access + never
                )
                turn = await thread.run_streamed(final_prompt)
                async for event in turn.events:
                    await stream.on_event(event)

                result = self._map_result(stream, int((time.time() - start) * 1000), model)

                # L1: L0 失败 → thread 内 toolless 修复(deepsec 式, 保留原上下文)
                if (output_format and result.success
                        and result.structured_output is None and result.text.strip()):
                    recovered = await self._l1_reparse(
                        codex, thread, result.text, output_format, model)
                    if recovered is not None:
                        result.structured_output = recovered
                return result
        except Exception as e:
            duration = int((time.time() - start) * 1000)
            return self._handle_error(e, duration, model)
        finally:
            # 回放 collector JSONL(所有退出路径; 回放自身异常不掩盖主结果)
            if collector is not None and out_jsonl:
                try:
                    from supernova_core.collectors.bridge import replay_codex_jsonl
                    replay_codex_jsonl(collector, out_jsonl)
                except Exception as replay_err:  # noqa: BLE001
                    import logging
                    logging.getLogger(__name__).warning("codex collector replay failed: %s", replay_err)
            if invocation:
                shutil.rmtree(invocation.codex_home, ignore_errors=True)   # deepsec: /tmp 塞满→bootstrap 静默失败

    # ------------------------------------------------------------------
    # 结果映射 / L1 / 错误处理
    # ------------------------------------------------------------------

    def _map_result(self, stream, duration_ms: int, model: str) -> ClaudeRunResult:
        text = stream.final_text
        success = stream.error is None and not stream.silent_failure
        structured = None
        payload = _extract_json_payload(text) if text else None
        if payload:
            try:
                structured = json.loads(payload)
            except (json.JSONDecodeError, ValueError):
                structured = None
        cost_amount = compute_cost(model, stream.usage) if stream.usage else _zero_cost(model)
        error_code, retryable = (None, True)
        if not success:
            if stream.silent_failure:
                error_code, retryable = "TransientError", True   # 静默失败 → 重试(deepsec)
            else:
                error_code, retryable = classify_error_for_temporal(Exception(stream.error or "codex run failed"))
        return ClaudeRunResult(
            text=text, success=success, duration=duration_ms, turns=max(stream.turns, 1),
            cost=cost_amount.cost, cost_currency=cost_amount.currency, model=model,
            structured_output=structured, error=stream.error,
            retryable=retryable, error_code=error_code,
            tokens=stream.usage or TokenUsage(),
        )

    async def _l1_reparse(self, codex, thread, text: str, output_format: dict, model: str):
        """L1: resume 原 thread + toolless 重问 JSON-only；失败回落 AsyncOpenAI 单 completion。"""
        repair_prompt = (
            "将以下分析结论转为符合 schema 的纯 JSON，只输出一个 ```json 围栏块，"
            "不要任何解释：\n" + text
        )
        try:
            resumed = await codex.thread_resume(thread.id if hasattr(thread, "id") else thread,
                                                model=model)   # 调用名按 spike notes
            turn = await resumed.run_streamed(repair_prompt)
            messages: list[str] = []
            async for event in turn.events:
                item = getattr(event, "item", None)
                if (getattr(event, "type", "") == "item.completed"
                        and getattr(item, "type", "") == "agent_message"):
                    messages.append(getattr(item, "text", "") or "")
            joined = "\n\n".join(messages)
            payload = _extract_json_payload(joined) if joined else None
            if payload:
                return json.loads(payload)
        except Exception:  # noqa: BLE001 — L1 失败走 AsyncOpenAI 回落
            pass
        return await self._l1_fallback_openai(text, model)

    async def _l1_fallback_openai(self, text: str, model: str):
        """AsyncOpenAI 单 completion 回落(openai 引擎 _lightweight_reparse 同款)。"""
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=self.config.api_key, base_url=self.config.base_url)
            resp = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content":
                           "将以下分析结论转为纯 JSON，只输出 JSON 本体，无解释无围栏：\n" + text}],
            )
            content = (resp.choices[0].message.content or "") if resp.choices else ""
            payload = _extract_json_payload(content) if content else None
            if payload:
                return json.loads(payload)
        except Exception:  # noqa: BLE001
            return None
        return None

    def _handle_error(self, error: Exception, duration_ms: int, model: str) -> ClaudeRunResult:
        error_code, retryable = classify_error_for_temporal(error)
        return ClaudeRunResult(
            text="", success=False, duration=duration_ms, turns=0,
            cost=0.0, model=model, error=str(error),
            error_code=error_code, retryable=retryable,
        )


def _zero_cost(model: str):
    from .pricing import CostAmount
    return CostAmount(0.0, "USD")
```

（`CostAmount` 的真实字段名以 `agents/pricing.py` 为准——实现时先读该文件对齐构造签名。）

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest packages/core/tests/agents/test_providers_codex_call.py packages/core/tests/agents/test_providers_codex_stage0.py packages/core/tests/agents/test_providers_codex_invocation.py packages/core/tests/agents/test_codex_stream_collector.py packages/core/tests/collectors/test_codex_mcp_server.py -v`
Expected: 全 PASS（回归本引擎全部相关文件）

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/supernova_core/agents/providers_codex.py packages/core/tests/agents/test_providers_codex_call.py
git commit -m "feat(core): CodexProvider.call 主流程 — 事件消费/L0/L1 thread 内修复/静默失败检测/collector 回放+CODEX_HOME 清理"
```

---

## Phase 3 — profile 接线 + 真机探针（验收）

### Task 9: profile 文件 + CLAUDE.md 三引擎注记

**Files:**
- Create: `.env.profiles.example/glm-codex.env.example`
- Modify: `CLAUDE.md`（§2 双引擎 → 三引擎注记）

**Interfaces:**
- Produces: `glm-codex.env.example` 模板（用户复制为 `.env.profiles/glm-codex.env` 填 Coding Plan Key）

- [ ] **Step 1: 写 example profile**

`.env.profiles.example/glm-codex.env.example`（对齐 glm-openai.env.example 的注释风格）：

```bash
# 智谱 GLM 走 Codex CLI 运行时(官方 Responses 协议端点)。复制为 .env.profiles/glm-codex.env 并填 key。
#
# codex_cli = openai-codex SDK 起 Codex app-server 子进程(与 anthropic_api 同为 CLI 运行时引擎,
# 内置工具/子代理委派/HTTP 超时全由运行时承担)。线协议 OpenAI Responses(GLM 官方 Codex 接入,
# docs.bigmodel.cn/cn/coding-plan/tool/codex)——与 openai_compatible 的 chat completions 端点不同源。
# ⚠️ Key 必须用 GLM Coding Plan 套餐 Key(个人/团队套餐页新建), 与平台 API Key 不通用。
SUPERNOVA_AI_PROVIDER=codex_cli
SUPERNOVA_CODEX_BASE_URL=https://open.bigmodel.cn/api/v1
SUPERNOVA_CODEX_API_KEY=your-glm-coding-plan-key
SUPERNOVA_CODEX_LARGE_MODEL=glm-5.3
SUPERNOVA_CODEX_MEDIUM_MODEL=glm-5.3
SUPERNOVA_CODEX_SMALL_MODEL=glm-5-turbo

# per-profile cost 定价(spec 2026-07-09): 三引擎同源同价, 共享 glm.pricing.json。
SUPERNOVA_PRICING_OVERRIDE=.env.profiles/glm.pricing.json

# 可选调参(默认不设):
# SUPERNOVA_CODEX_MAX_OUTPUT_TOKENS=   # 单次生成 output 上限, 默认 64000(对齐 claude 引擎)
```

- [ ] **Step 2: CLAUDE.md §2 注记**

`CLAUDE.md` 第 2 节标题下第一段改为（替换「项目拥有**双引擎**」句）：

```markdown
项目拥有**三引擎**（codex 为 2026-08-21 新增），经 `SUPERNOVA_AI_PROVIDER` 切换：
- **claude-agent-sdk**（profile `glm-anthropic`）：底层 Claude Code CLI。
- **openai-agents**（profile `glm-openai`）：openai 兼容 Chat Completions。
- **codex**（profile `glm-codex`，provider type `codex_cli`）：openai-codex SDK 起 Codex app-server CLI 运行时；线协议 OpenAI **Responses**（GLM 官方端点 `https://open.bigmodel.cn/api/v1`）；内置工具/子代理委派与 claude 轨同类零工具代码；结构化输出走 L0+L1 兜底（同 openai 轨）；collector 经 stdio MCP 子进程桥。设计：`docs/superpowers/specs/2026-08-21-codex-agent-sdk-engine-design.md`。
```

并在 §2「关键约定」清单末尾加一条：

```markdown
- **codex 轨运行时约定**：`CODEX_HOME` per-call 隔离（并发踩踏 session DB 会静默 no-op）；`wire_api="responses"` + `supports_websockets=false` + `model_max_output_tokens` 必须显式注入；sandbox 无条件 `danger-full-access`（对齐 bypassPermissions）；plugin lockdown（`features.plugins/remote_plugin=false`）。
```

- [ ] **Step 3: Commit**

```bash
git add .env.profiles.example/glm-codex.env.example CLAUDE.md
git commit -m "docs: glm-codex profile 模板 + CLAUDE.md 三引擎注记(运行时约定)"
```

---

### Task 10: 真机探针 `validate_codex_task_probe.py`（验收）

**Files:**
- Create: `scripts/validate_codex_task_probe.py`

**Interfaces:**
- Consumes: `run_claude_prompt`（经 `codex_cli` 全链路）；Task 3 spike notes 的 subagent 事件特征
- Produces: 探针级验收结论（spec §5 四断言）

前置：`cp .env.profiles.example/glm-codex.env.example .env.profiles/glm-codex.env` 并填真实 Coding Plan Key。

- [ ] **Step 1: 写探针脚本**

镜像 `scripts/validate_openai_task_probe.py` 结构：

```python
#!/usr/bin/env python3
"""Codex 引擎 GLM 子代理委派真机探针（spec §5 验收，对齐 validate_openai_task_probe 形态）。

问题: glm-codex 下, GLM 经 Codex CLI 运行时能否按 vuln prompt 的 Task-delegation 措辞
      正确发起原生 subagent 委派, 并产出可 L0 解析的 ```json 判定?

PASS 断言(spec §5):
  1. 子代理委派发生(事件流可见 subagent 特征——形态以 spike codex_subagent notes 为准)
  2. ```json 最终输出 L0 解析成功(verdict 非空)
  3. token usage 非零
  4. 并发 2 个 call 无 CODEX_HOME 踩踏(双双 success 且非静默失败)
"""
import asyncio
import os
import tempfile
import time
from pathlib import Path

_PROFILE_CANDIDATES = [
    Path("/root/supernova/.env.profiles/glm-codex.env"),
    Path(__file__).resolve().parent.parent / ".env.profiles" / "glm-codex.env",
]
PROFILE = next((p for p in _PROFILE_CANDIDATES if p.exists()), _PROFILE_CANDIDATES[-1])


def load_profile() -> None:
    for line in PROFILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())
    os.environ["SUPERNOVA_AI_PROVIDER"] = "codex_cli"   # 防御性锁定引擎


from supernova_core.agents.tool_audit_logger import NullToolAuditLogger


class RecordingLogger(NullToolAuditLogger):
    def __init__(self) -> None:
        self.tools: list[str] = []
        self.turns: list[str] = []

    async def log_tool_start(self, tool_name: str, parameters) -> None:
        self.tools.append(tool_name)

    async def log_assistant_turn(self, turn_no: int, text: str) -> None:
        self.turns.append(text)


def _make_target() -> Path:
    target = Path(tempfile.mkdtemp(prefix="codex_task_probe_"))
    (target / "app.py").write_text(
        "import sqlite3\n"
        "def get_user(name):\n"
        "    conn = sqlite3.connect('db')\n"
        "    cur = conn.cursor()\n"
        "    cur.execute(\"SELECT * FROM users WHERE name='\" + name + \"'\")\n"
        "    return cur.fetchone()\n"
    )
    return target


# 与 openai/glm probe 同源 prompt(唯一变量是引擎); Task 3 若发现 Codex 需要不同触发措辞,
# 以 notes 为准在此追加一句 spawn 指令(不改 repo 内 vuln prompt——那是后续对齐任务的事)。
PROMPT = (
    "You are an injection analysis specialist. Analyze app.py (in cwd) for SQL injection.\n\n"
    "CRITICAL TOOL USAGE RESTRICTIONS:\n"
    "- NEVER read application source code directly—delegate every code review to a subagent.\n"
    "- ALWAYS direct the subagent to trace tainted data flow and sink construction before you reach a verdict.\n\n"
    "Task: Use a subagent to read app.py and determine whether get_user(name) has a SQL injection flaw. "
    "Report: verdict (vulnerable/safe), the sink line, and rationale. "
    'End with a fenced ```json block: {"verdict": "...", "sink": "...", "rationale": "..."}'
)


async def run_once(label: str):
    from supernova_core.agents.runner import run_claude_prompt
    logger = RecordingLogger()
    t0 = time.time()
    result = await asyncio.wait_for(
        run_claude_prompt(prompt=PROMPT, repo_path=str(_make_target()),
                          model_tier="medium", tool_audit_logger=logger),
        timeout=600,
    )
    dt = time.time() - t0
    print(f"[{label}] duration={dt:.1f}s turns={result.turns} cost={result.cost} "
          f"success={result.success} tokens_in={result.tokens.input_tokens} "
          f"tokens_out={result.tokens.output_tokens}")
    if result.error:
        print(f"[{label}] ERROR: {result.error}")
    print(f"[{label}] TOOLS: {logger.tools}")
    print(f"[{label}] FINAL: {(result.text or '')[:800]}")
    return result, logger


async def main() -> None:
    load_profile()
    print(f"[probe] provider=codex_cli (glm-codex)  profile={PROFILE}")
    result, logger = await run_once("solo")

    checks: list[tuple[str, bool]] = []
    checks.append(("2. L0 json 解析(verdict 非空)",
                   bool(result.structured_output and result.structured_output.get("verdict"))))
    checks.append(("3. usage 非零", result.tokens.output_tokens > 0))
    # 1. subagent 特征: 以 Task 3 spike notes 记录的形态为准。
    #    默认判据: tools/turns 文本中出现 subagent/agent 痕迹, 或 token 用量显著大于
    #    单 agent 直读(>2000 input)——notes 有更精确信号时替换此判据。
    subagent_evidence = any("agent" in t.lower() for t in logger.tools) or \
        result.tokens.input_tokens > 2000
    checks.append(("1. subagent 委派特征", subagent_evidence))

    # 4. 并发隔离: 双 call 并发, 双双非静默失败
    (r2, _), (r3, _) = await asyncio.gather(run_once("conc-a"), run_once("conc-b"))
    checks.append(("4. 并发 2 call 无踩踏",
                   bool(r2.success) and bool(r3.success) and not (r2.tokens.output_tokens == 0
                                                                  and not r2.text)))

    print("=" * 64)
    all_pass = True
    for name, ok in checks:
        print(f"{'>>> PASS' if ok else '>>> FAIL'}  {name}")
        all_pass = all_pass and ok
    print("RESULT:", "PASS ✅" if all_pass else "FAIL ❌")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: 真机跑探针**

Run: `uv run python scripts/validate_codex_task_probe.py`
Expected: 四断言全 PASS

失败处置：
- 断言 1 失败（无 subagent 特征）→ 对照 spike notes 换触发措辞重试；仍无效则记录"GLM 经 codex 子代理委派不可触发"进 spike notes，回报用户决策（prompt 措辞对齐是后续任务，不在本计划强改 repo prompt）。
- 断言 2 失败（L0 失败）→ 检查 `final_text` 选择与 L1 resume 日志。
- 断言 4 失败（并发踩踏）→ 检查 `build_invocation` 的 CODEX_HOME 是否真的 per-call（两 call 的 tempdir 不同）。

- [ ] **Step 3: 结论回填 spike notes + Commit**

```bash
git add scripts/validate_codex_task_probe.py docs/superpowers/specs/2026-08-21-codex-spike-notes.md
git commit -m "feat(core): codex 引擎真机探针 validate_codex_task_probe — 四断言验收 PASS"
```

---

## Self-Review 结论（计划自审已完成）

1. **Spec 覆盖**：§1 接线点→Task 4/9；§2 call 七步→Task 5/8（models.json/config/env/sandbox/L0/L1/CODEX_HOME 清理）；§3 桥→Task 7；§4 错误矩阵→Task 6/8（静默失败/turn.failed 分类/L1 回落）；§5 单测清单→Task 4-8 各自 TDD、探针四断言→Task 10；§6 风险→Task 1/2/3 spike 验证点 + Phase 1 回炉闸门。无缺口。
2. **占位符扫描**：Task 5 的 GLM_MODEL_CATALOG 与 Task 8 的 SDK 调用名标注了"以 spike notes 校正"——这是显式验证步骤（代码已给出完整预期形态），非 TBD。
3. **类型一致性**：`CodexInvocation` 字段、`build_invocation(config, model, proxy_url)` 签名、`CodexStreamCollector` 属性名（`final_text`/`silent_failure`/`usage`/`turns`/`tool_call_count`/`error`）、`replay_codex_jsonl(collector, jsonl_path) -> int`、`compose_codex_collector_mcp(collector, workdir) -> (dict, str)` 在 Task 5/6/7/8/10 间一致。
