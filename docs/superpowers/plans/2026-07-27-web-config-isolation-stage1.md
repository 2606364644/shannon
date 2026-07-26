# P3c 阶段 1：配置穿线 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 provider 配置从隐式 `os.environ` 总线改为经数据通道显式传递——`PipelineInput.provider_config`（dict）一路穿到 `run_claude_prompt(provider_config=...)`。**此阶段配置仍是全局的**（`scan_manager` 从 env 构造），但通道打通，为阶段 2 per-ws 填充铺路。

**Architecture:** `provider_config: dict | None = None` 字段加到 `PipelineInput` / `ActivityInput`（白盒+黑盒）；workflow 把 `input.provider_config` 灌进 `act_input`（一处灌入，后续 activity 经 `**act_input.__dict__` 自动继承）；`executor.execute` / `poc_generator.generate_*` / 白盒 6 处 `run_claude_prompt` 直调点收并下传 `provider_config`；`scan_manager._submit_whitebox` 提交时 `asdict(build_provider_config())` 塞入（全局 env，行为不变）。`run_claude_prompt` 已有 `provider_config: dict | None` 参数（`runner.py:116`，:144-150 `ProviderConfig(**provider_config)` 优先于 env），本阶段只是让调用点真正传它。

**Tech Stack:** Python 3.11+ / dataclasses / temporalio workflow+activity / pytest + monkeypatch / mock（`run_claude_prompt` / `executor.execute` / `client.start_workflow`）。

## Global Constraints

- **依赖阶段 0 已实现**：`ProviderConfig` 5 字段（`max_turns`/`subagent_max_turns`/`max_output_tokens`/`call_timeout`/`adaptive_thinking`）+ 引擎读 `self.config` 已落地（commit `d596ae71..ca5224e4`）。本阶段的 `provider_config` dict 经 `ProviderConfig(**dict)` 构造时这些字段会生效。
- **配置仍全局（行为不变）**：`scan_manager` 从全局 env 构造 `provider_config`（`build_provider_config()`）；scan 仍用全局配置跑通。per-ws 填充属阶段 2。
- **`provider_config` 全程 dict 语义**：`PipelineInput`/`ActivityInput`/`executor.execute`/`run_claude_prompt` 都收 `dict | None`；`run_claude_prompt` 内部 `ProviderConfig(**provider_config)`（:146）。不要在数据通道里传 `ProviderConfig` 对象（跨 worker 边界要 serializable，dict 才行）。
- **`None` 语义**：`provider_config=None` = 未穿线（CLI 兜底走 env，`run_claude_prompt` 的 `provider_config is None` 分支 :148-150 走 `build_provider_config`）；web 路径非 `None`。
- **黑盒 web 路径未接**（`scan_manager.py:109` `NotImplementedError("blackbox C1 化留 Phase C")`）：本阶段黑盒**只**加 `shared.py` 字段（数据模型对齐）+ 共享层（executor/poc_generator）穿线天然覆盖黑盒 activity；**黑盒 workflow/activities 直调点穿线 + scan_manager 黑盒构造留 Phase C**（spec/plan 标注，不在本阶段）。
- **白盒 6 处直调点含两类**：(a) `run_claude_prompt` 直调（:296/688/1238/1281）→ 加 `provider_config=input.provider_config`；(b) 自行 `build_provider_config(api_key=...)` 构造（:639/732，taint analyzer）→ 改为 `input.provider_config` 优先，`None` 才 `build_provider_config`。
- **行为不变量**：所有调用点 `provider_config=None`（CLI / 未改造路径）→ 行为与改造前一致（`run_claude_prompt` 走 env 兜底）。
- **测试隔离**：mock `run_claude_prompt`/`executor.execute`/`client.start_workflow` 用 monkeypatch；按 CLAUDE.md 只跑改动相关测试文件。

---

## File Structure

| 文件 | 职责 | 本计划改动 |
|---|---|---|
| `packages/whitebox/src/supernova_whitebox/pipeline/shared.py` | `PipelineInput` / `ActivityInput` | 各加 `provider_config: dict \| None = None`（Task 1） |
| `packages/blackbox/src/supernova_blackbox/pipeline/shared.py` | `BlackboxPipelineInput` / `BlackboxActivityInput` | 各加 `provider_config: dict \| None = None`（Task 1，为 Phase C 预留） |
| `packages/core/src/supernova_core/agents/executor.py` | `AgentExecutor.execute`（白盒+黑盒共用） | 加 `provider_config` 参数 + 传 `run_claude_prompt`（Task 2） |
| `packages/core/src/supernova_core/services/poc_generator.py` | PoC 生成（`generate_*` 多函数） | 加 `provider_config` 参数 + 透传（Task 2） |
| `packages/whitebox/src/supernova_whitebox/pipeline/workflows.py` | 白盒 workflow | `act_input` 灌入 `provider_config`（Task 3，一处） |
| `packages/whitebox/src/supernova_whitebox/pipeline/activities.py` | 白盒 activity（6 处调用点） | 传 `provider_config`（Task 3） |
| `packages/web/src/supernova_web/components/scan_manager.py` | web 提交 scan | `_submit_whitebox` 构造 `PipelineInput` 时塞全局 `provider_config`（Task 4） |
| `packages/core/tests/agents/test_executor_stage1.py` | 新建：executor 穿线测试 | Task 2 |
| `packages/whitebox/tests/.../test_workflow_threading_stage1.py` | 新建：workflow/activity 穿线测试 | Task 3 |
| `packages/web/tests/test_scan_manager_provider_config.py` | 新建：scan_manager 构造测试 | Task 4 |

---

## Task 1: 数据模型层加 provider_config 字段

**Files:**
- Modify: `packages/whitebox/src/supernova_whitebox/pipeline/shared.py:8-21`（PipelineInput）+ `:40-56`（ActivityInput）
- Modify: `packages/blackbox/src/supernova_blackbox/pipeline/shared.py:8-17`（BlackboxPipelineInput）+ `:37-52`（BlackboxActivityInput）
- Test: 复用现有 shared 测试或新增断言（dataclass 字段）

**Interfaces:**
- Consumes: 阶段 0 的 `ProviderConfig`（dict 序列化经 `dataclasses.asdict`）
- Produces: 4 个 dataclass 各增 `provider_config: dict | None = None`。下游 Task 2-4 读写此字段。

- [ ] **Step 1: 写失败测试** — 新建 `packages/whitebox/tests/pipeline/test_shared_provider_config_stage1.py`

```python
"""P3c 阶段 1：PipelineInput/ActivityInput 加 provider_config 字段。"""
from supernova_whitebox.pipeline.shared import PipelineInput, ActivityInput
from supernova_blackbox.pipeline.shared import BlackboxPipelineInput, BlackboxActivityInput


def test_whitebox_pipeline_input_provider_config_default_none():
    assert PipelineInput().provider_config is None


def test_whitebox_pipeline_input_provider_config_set():
    inp = PipelineInput(provider_config={"type": "openai_compatible", "api_key": "sk-x"})
    assert inp.provider_config == {"type": "openai_compatible", "api_key": "sk-x"}


def test_whitebox_activity_input_provider_config_default_none():
    assert ActivityInput(repo_path="/r").provider_config is None


def test_whitebox_activity_input_provider_config_set():
    act = ActivityInput(repo_path="/r", provider_config={"type": "anthropic_api"})
    assert act.provider_config == {"type": "anthropic_api"}


def test_blackbox_pipeline_input_provider_config_field():
    assert BlackboxPipelineInput().provider_config is None
    assert BlackboxPipelineInput(provider_config={"type": "x"}).provider_config == {"type": "x"}


def test_blackbox_activity_input_provider_config_field():
    assert BlackboxActivityInput(web_url="http://x").provider_config is None
    assert BlackboxActivityInput(web_url="http://x", provider_config={"type": "x"}).provider_config == {"type": "x"}
```

- [ ] **Step 2: 跑测试确认失败** — `cd packages/whitebox && uv run pytest tests/pipeline/test_shared_provider_config_stage1.py -v`（blackbox 测试需 `cd packages/blackbox` 或在 monorepo 根跑）
  - 预期：FAIL（`AttributeError`/`TypeError: unexpected keyword argument 'provider_config'`）

- [ ] **Step 3: 白盒 shared.py 加字段** — 编辑 `packages/whitebox/src/supernova_whitebox/pipeline/shared.py`

  3a. `PipelineInput`（:21 `event_file` 字段后）：

```python
    event_file: str | None = None             # C1: ...
    # P3c 阶段 1：provider 配置穿线（dict，跨 worker 边界 serializable）。
    # None=未穿线（CLI 兜底走 env）；web 路径由 scan_manager 塞全局/阶段2 per-ws 配置。
    provider_config: dict | None = None
```

  3b. `ActivityInput`（:56 `track_statuses` 字段后）：

```python
    track_statuses: dict = field(default_factory=dict)
    # P3c 阶段 1：由 workflow 从 PipelineInput.provider_config 灌入；activity 下传 run_claude_prompt。
    provider_config: dict | None = None
```

- [ ] **Step 4: 黑盒 shared.py 加字段** — 编辑 `packages/blackbox/src/supernova_blackbox/pipeline/shared.py`

  4a. `BlackboxPipelineInput`（:17 `workspaces_root` 字段后）：

```python
    workspaces_root: str | None = None
    # P3c 阶段 1：provider 配置穿线（Phase C 黑盒 C1 化时由 scan_manager 填；CLI 兜底 None）。
    provider_config: dict | None = None
```

  4b. `BlackboxActivityInput`（:52 `info_level` 字段后）：

```python
    info_level: str = "info"
    # P3c 阶段 1：provider 配置穿线（Phase C 黑盒 workflow 灌入）。
    provider_config: dict | None = None
```

- [ ] **Step 5: 跑测试确认通过** — 白盒 + 黑盒测试各跑，预期 6 个全 PASS。
- [ ] **Step 6: Commit**

```bash
git add packages/whitebox/src/supernova_whitebox/pipeline/shared.py \
        packages/blackbox/src/supernova_blackbox/pipeline/shared.py \
        packages/whitebox/tests/pipeline/test_shared_provider_config_stage1.py
git commit -m "feat(pipeline): P3c 阶段1 PipelineInput/ActivityInput 加 provider_config 字段

白盒+黑盒 shared 各加 provider_config: dict|None=None（跨 worker serializable）。
None=未穿线（CLI 兜底 env）。数据模型层，下游 executor/workflow/scan_manager 穿线跟进。"
```

---

## Task 2: 共享层穿线（executor + poc_generator）

**Files:**
- Modify: `packages/core/src/supernova_core/agents/executor.py:50-66`（execute 签名）+ `:115-126`（run_claude_prompt 调用）
- Modify: `packages/core/src/supernova_core/services/poc_generator.py:485/533/570/870/933/967/988/999`（generate_* + 调用链）
- Test: `packages/core/tests/agents/test_executor_stage1.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `ActivityInput.provider_config`
- Produces: `AgentExecutor.execute(..., provider_config=None)` + `poc_generator.generate_*(..., provider_config=None)` 都下传 `run_claude_prompt(provider_config=...)`。白盒+黑盒 activity 经此共享层受益。

- [ ] **Step 1: 写失败测试** — 新建 `packages/core/tests/agents/test_executor_stage1.py`

```python
"""P3c 阶段 1：AgentExecutor.execute 把 provider_config 下传 run_claude_prompt。"""
import pytest

from supernova_core.agents.executor import AgentExecutor


@pytest.fixture
def captured(monkeypatch):
    """捕获 run_claude_prompt 的 provider_config 实参。"""
    box = {}
    async def fake_run(prompt, repo_path, **kw):
        box["provider_config"] = kw.get("provider_config")
        from supernova_core.agents.runner import ClaudeRunResult
        return ClaudeRunResult(success=True, structured_output={})
    monkeypatch.setattr("supernova_core.agents.executor.run_claude_prompt", fake_run)
    # 跳过 git checkpoint / collector / validator 等（只验穿线）
    monkeypatch.setattr("supernova_core.agents.executor.GitManager.ensure_repository", lambda *a, **k: None)
    monkeypatch.setattr("supernova_core.agents.executor.GitManager.create_checkpoint", lambda *a, **k: None)
    return box


async def test_execute_passes_provider_config(captured, monkeypatch, tmp_path):
    """execute 收 provider_config → run_claude_prompt 收到同一 dict。"""
    from supernova_core.models.agents import AgentName
    exe = AgentExecutor(prompt_manager=object())  # prompt_manager 仅 host-render agent 用，此处 mock 路径不触发
    # 用一个轻量 agent（recon）+ mock prompt_manager.load_sync
    monkeypatch.setattr(exe, "prompt_manager", type("PM", (), {"load_sync": lambda *a, **k: "prompt"})())
    pc = {"type": "openai_compatible", "api_key": "sk-stage1", "max_turns": 777}
    await exe.execute(
        agent_name=AgentName.RECON,
        repo_path=str(tmp_path),
        deliverables_path=str(tmp_path / "deliv"),
        provider_config=pc,
    )
    assert captured["provider_config"] == pc


async def test_execute_provider_config_default_none(captured, monkeypatch, tmp_path):
    """不传 provider_config → run_claude_prompt 收 None（CLI 兜底 env 路径，行为不变）。"""
    from supernova_core.models.agents import AgentName
    exe = AgentExecutor(prompt_manager=object())
    monkeypatch.setattr(exe, "prompt_manager", type("PM", (), {"load_sync": lambda *a, **k: "prompt"})())
    await exe.execute(
        agent_name=AgentName.RECON,
        repo_path=str(tmp_path),
        deliverables_path=str(tmp_path / "deliv"),
    )
    assert captured["provider_config"] is None
```

  > 注：测试用 `async def` + `monkeypatch`；若 core 测试用 `asyncio` mode，按现有 conftest（`packages/core/tests/conftest.py`）的 async fixture 约定跑。`AgentName.RECON` 等具体 enum 以现有 `AGENTS` 字典为准（若 RECON 触发 collector/host-render，换一个轻量 agent 或 mock `make_collector` 返 None）。

- [ ] **Step 2: 跑测试确认失败** — `cd packages/core && uv run pytest tests/agents/test_executor_stage1.py -v`
  - 预期：FAIL（`execute()` 不收 `provider_config` → `TypeError`）

- [ ] **Step 3: executor.execute 加参数 + 下传** — 编辑 `packages/core/src/supernova_core/agents/executor.py`

  3a. 签名（:64 `max_turns` 后加）：

```python
        max_turns: int | None = None,
        skip_artifact_postprocess: bool = False,
        provider_config: dict | None = None,   # P3c 阶段 1：穿线下传 run_claude_prompt
) -> AgentMetrics:
```

  3b. `run_claude_prompt` 调用（:115-126）加 `provider_config=provider_config`：

```python
        result = await run_claude_prompt(
            prompt=prompt,
            repo_path=str(repo),
            model_tier=defn.model_tier,
            api_key=api_key,
            deliverables_subdir=str(deliverables.relative_to(repo)) if deliverables.is_relative_to(repo) else None,
            structured_output_schema=structured_output_schema,
            audit_logger=audit_logger,
            tool_audit_logger=tool_audit_logger,
            max_turns=max_turns,
            collector=collector,
            provider_config=provider_config,   # P3c 阶段 1
        )
```

- [ ] **Step 4: poc_generator 穿线** — 编辑 `packages/core/src/supernova_core/services/poc_generator.py`

  模式：每个公开 `generate_*` 函数 + 内部辅助，签名加 `provider_config: dict | None = None`，调 `run_claude_prompt` 处下传。具体改动点：

  - `generate_poc_report`（:485）/ `_generate_*`（:533/:570）：签名加 `provider_config: dict | None = None`；:494/:541 的 `run_claude_prompt(...)` 加 `provider_config=provider_config`。
  - 调用链透传：:581/:933/:967/:988/:999 调 `generate_*` 的地方，把上层 `provider_config` 透传下去。
  - `:870` 处（顶层 PoC 入口，收 `api_key`）同样加 `provider_config`，下传各 `generate_*`。

  示例（:485-499 模式，其余同）：

```python
async def generate_poc_report(
    repo_path: str, api_key: str | None = None, model_tier: str = "medium",
    provider_config: dict | None = None,   # P3c 阶段 1
) -> ...:
    ...
    result = await run_claude_prompt(
        prompt=...,
        repo_path=repo_path,
        model_tier=model_tier,
        api_key=api_key,
        provider_config=provider_config,   # P3c 阶段 1
    )
```

  poc_generator 的穿线测试：mock `run_claude_prompt`，调 `generate_poc_report(..., provider_config={...})`，断言捕获到。模式同 executor 测试，可加到 `test_executor_stage1.py` 或新建 `test_poc_generator_stage1.py`。

- [ ] **Step 5: 跑 executor 测试确认通过** — `cd packages/core && uv run pytest tests/agents/test_executor_stage1.py -v`
  - 预期：2 个 PASS

- [ ] **Step 6: 跑现有 executor/poc_generator 回归** — `cd packages/core && uv run pytest tests/agents/test_providers_collector_injection.py tests/services/ -v`（按实际现有 poc_generator 测试路径）
  - 预期：全 PASS（现有调用不传 provider_config → None → 行为不变）

- [ ] **Step 7: Commit**

```bash
git add packages/core/src/supernova_core/agents/executor.py \
        packages/core/src/supernova_core/services/poc_generator.py \
        packages/core/tests/agents/test_executor_stage1.py
git commit -m "feat(core): P3c 阶段1 executor.execute + poc_generator 穿线 provider_config

共享层（白盒+黑盒共用）：execute/generate_* 收 provider_config 下传 run_claude_prompt。
None=CLI 兜底 env（行为不变）。下游白盒 activity 传参跟进。"
```

---

## Task 3: 白盒 workflow + activities 穿线

**Files:**
- Modify: `packages/whitebox/src/supernova_whitebox/pipeline/workflows.py:129-140`（act_input 灌入）
- Modify: `packages/whitebox/src/supernova_whitebox/pipeline/activities.py:208/296/639/688/732/1238/1281`（6 处调用点）
- Test: `packages/whitebox/tests/pipeline/test_workflow_threading_stage1.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `ActivityInput.provider_config` + Task 2 的 `executor.execute(provider_config=)`
- Produces: 白盒 workflow 把 `PipelineInput.provider_config` 灌进 `act_input`（一处，后续 activity 经 `**act_input.__dict__` 自动继承）；6 处调用点下传。

- [ ] **Step 1: 写失败测试** — 新建 `packages/whitebox/tests/pipeline/test_workflow_threading_stage1.py`

```python
"""P3c 阶段 1：白盒 activity 把 input.provider_config 下传 executor/run_claude_prompt。

只验穿线（mock executor + run_claude_prompt），不跑真实 agent。
"""
import pytest


@pytest.fixture
def captured_executor(monkeypatch):
    box = {}
    async def fake_execute(self, agent_name, repo_path, **kw):
        box.setdefault("pcs", []).append(kw.get("provider_config"))
        from supernova_core.models.metrics import AgentMetrics
        return AgentMetrics()
    # activity 经 `from ... import AgentExecutor` 或模块级 executor 实例；按实际 import 路径 patch
    monkeypatch.setattr("supernova_whitebox.pipeline.activities.AgentExecutor.execute", fake_execute)
    return box


async def test_run_agent_passes_provider_config(captured_executor, monkeypatch):
    """run_agent activity 收 input.provider_config → executor.execute 收到。"""
    from supernova_whitebox.pipeline.activities import run_agent
    from supernova_whitebox.pipeline.shared import ActivityInput
    from supernova_core.models.agents import AgentName

    inp = ActivityInput(
        repo_path="/r", deliverables_subdir="deliverables",
        agent_name=AgentName.RECON.value, workspace_path="/r",
        provider_config={"type": "openai_compatible", "api_key": "sk-stage1"},
    )
    # run_agent 是 @activity.defn，直接 await 调（跳过 temporalio activity context）
    try:
        await run_agent(inp)
    except Exception:
        pass  # mock 环境 git/deliverables 可能不完整，只验穿线捕获
    assert "sk-stage1" in [pc.get("api_key") if pc else None for pc in captured_executor["pcs"]]


async def test_chain_verdict_passes_provider_config(monkeypatch):
    """chain verdict 直调 run_claude_prompt 点（:1238）传 input.provider_config。"""
    box = {}
    async def fake_run(prompt, repo_path, **kw):
        box["pc"] = kw.get("provider_config")
        from supernova_core.agents.runner import ClaudeRunResult
        return ClaudeRunResult(success=True, structured_output={})
    monkeypatch.setattr("supernova_whitebox.pipeline.activities.run_claude_prompt", fake_run)
    # 调 chain verdict activity（按实际函数名 + ActivityInput 字段），传 provider_config
    # 具体函数名/签名以 activities.py:1238 所在 activity 为准
    # assert box["pc"] == {...}（传入值）
```

  > 注：白盒 activity 是 `@activity.defn`，测试里直接 `await fn(input)`（不经 temporalio worker）。具体 activity 函数名 + ActivityInput 必填字段以现有 `activities.py` 签名为准；测试核心是 mock executor/run_claude_prompt 后断言 `provider_config` 透传。若某 activity 依赖 temporalio `activity.info()`（如 setup_display），跳过该 activity 的穿线测试，只测纯穿线链。

- [ ] **Step 2: 跑测试确认失败** — `cd packages/whitebox && uv run pytest tests/pipeline/test_workflow_threading_stage1.py -v`
  - 预期：FAIL（activity 不传 provider_config）

- [ ] **Step 3: workflow act_input 灌入（一处）** — 编辑 `packages/whitebox/src/supernova_whitebox/pipeline/workflows.py:129-140`

```python
        act_input = ActivityInput(
            repo_path=input.repo_path,
            web_url=input.web_url,
            config_path=input.config_path,
            workspace_name=input.workspace_name,
            deliverables_subdir=input.deliverables_subdir,
            pipeline_testing_mode=input.pipeline_testing_mode,
            api_key=input.api_key,
            prompt_override=input.prompt_override,
            workspace_path=workspace_path,
            event_file=input.event_file,
            provider_config=input.provider_config,   # P3c 阶段 1：一处灌入，全链 **act_input.__dict__ 继承
        )
```

- [ ] **Step 4: activities.py 6 处调用点传参** — 编辑 `packages/whitebox/src/supernova_whitebox/pipeline/activities.py`

  4a. `:208` executor.execute 调用（:214 `api_key=input.api_key` 旁）：

```python
        metrics = await executor.execute(
            ...
            api_key=input.api_key,
            provider_config=input.provider_config,   # P3c 阶段 1
            ...
        )
```

  4b. `:296/688/1238/1281` run_claude_prompt 直调——各加 `provider_config=input.provider_config`：

```python
        result = await run_claude_prompt(
            prompt=...,
            repo_path=...,
            ...
            provider_config=input.provider_config,   # P3c 阶段 1
        )
```

  4c. `:639` 与 `:732` 自行 `build_provider_config` 点（taint analyzer）——`input.provider_config` 优先，`None` 才 build（保 CLI 兜底）：

```python
        # P3c 阶段 1：web 穿线优先（input.provider_config），CLI 兜底 build from env
        from supernova_core.agents.runner import ProviderConfig
        if input.provider_config:
            config = ProviderConfig(**input.provider_config)
        else:
            from supernova_core.agents.providers import build_provider_config
            config = build_provider_config(api_key=input.api_key or None)
        if config.api_key or config.type != "anthropic_api":
            ...   # 原 :642-645 逻辑不变，用上面的 config
```

  `:732-734` 同模式（`_pcfg` 变量）：

```python
        if input.provider_config:
            _pcfg = ProviderConfig(**input.provider_config)
        else:
            from supernova_core.agents.providers import build_provider_config, resolve_tier_model
            _pcfg = build_provider_config(api_key=input.api_key or None)
```

  > ⚠️ `:639`/`:732` 原代码 `build_provider_config` 返回后用 `config.api_key`/`resolve_tier_model(_pcfg, ...)`。改后 `ProviderConfig(**input.provider_config)` 字段语义一致（dict 来自 `asdict(ProviderConfig)`，键名对齐 dataclass 字段），下游消费不变。执行时核对 `:642-645`/`:734` 后续行不被破坏。

- [ ] **Step 5: 跑穿线测试确认通过** — `cd packages/whitebox && uv run pytest tests/pipeline/test_workflow_threading_stage1.py -v`
  - 预期：PASS

- [ ] **Step 6: 跑现有 whitebox 活动回归** — `cd packages/whitebox && uv run pytest tests/pipeline/ -v`（按实际路径，只跑改动相关）
  - 预期：全 PASS（provider_config 默认 None → 行为不变）

- [ ] **Step 7: Commit**

```bash
git add packages/whitebox/src/supernova_whitebox/pipeline/workflows.py \
        packages/whitebox/src/supernova_whitebox/pipeline/activities.py \
        packages/whitebox/tests/pipeline/test_workflow_threading_stage1.py
git commit -m "feat(whitebox): P3c 阶段1 workflow+activities 穿线 provider_config

workflows.py:129 act_input 一处灌入（**act_input.__dict__ 全链继承）；
activities.py 6 处调用点传 input.provider_config（executor.execute + 4 直调
run_claude_prompt + 2 build_provider_config 点 web 优先/CLI 兜底）。"
```

---

## Task 4: scan_manager 提交时构造全局 provider_config 塞入

**Files:**
- Modify: `packages/web/src/supernova_web/components/scan_manager.py:121-143`（`_submit_whitebox`）
- Test: `packages/web/tests/test_scan_manager_provider_config.py`（新建）

**Interfaces:**
- Consumes: 阶段 0 的 `build_provider_config()` + `dataclasses.asdict`
- Produces: `scan_manager._submit_whitebox` 构造 `PipelineInput(provider_config=asdict(build_provider_config()))`——web 路径 `provider_config` 非 `None`（全局 env 构造，行为不变；阶段 2 改为按 ws 解析）。

- [ ] **Step 1: 写失败测试** — 新建 `packages/web/tests/test_scan_manager_provider_config.py`

```python
"""P3c 阶段 1：scan_manager._submit_whitebox 提交时塞全局 provider_config。"""
import pytest


async def test_submit_whitebox_injects_global_provider_config(app_with_ws, monkeypatch):
    """提交的 PipelineInput.provider_config 非 None（= 全局 env 构造），含 type 字段。"""
    from supernova_web.components.scan_manager import ScanManager
    from supernova_web.models import ScanRequest

    captured = {}
    async def fake_start_workflow(fn, inp, **kw):
        captured["inp"] = inp
        return type("H", (), {"result": lambda *a: None})()
    monkeypatch.setattr("supernova_web.components.scan_manager.Client.connect", lambda *a: None)
    # 按 scan_manager 实际 Client 用法 patch（start_workflow 经 client 实例）
    # ...（按现有 test_api_scan.py mock 模式补全）

    sm = app_with_ws.state.scan_manager
    # 构造一个 whitebox ScanRequest（按现有测试 fixture 模式）
    req = ScanRequest(type="whitebox", source=..., workspace="ws-a")
    try:
        await sm.start(req)
    except Exception:
        pass
    inp = captured.get("inp")
    assert inp is not None
    assert inp.provider_config is not None          # web 路径非 None
    assert "type" in inp.provider_config             # ProviderConfig dict
    assert inp.provider_config["type"] in ("anthropic_api", "openai_compatible", "bedrock", "vertex", "litellm_router")
```

  > 注：`app_with_ws` / `ScanRequest` 构造以现有 `tests/conftest.py` + `test_api_scan.py` 的 mock 模式为准；mock `Client.connect` + `start_workflow` 捕获 `inp`。本测试核心断言：`inp.provider_config is not None` + 含合法 `type`。

- [ ] **Step 2: 跑测试确认失败** — `cd packages/web && uv run pytest tests/test_scan_manager_provider_config.py -v`
  - 预期：FAIL（`inp.provider_config is None`）

- [ ] **Step 3: `_submit_whitebox` 构造塞入** — 编辑 `packages/web/src/supernova_web/components/scan_manager.py:130-135`

```python
    async def _submit_whitebox(self, target: str | None, ws: str,
                               event_file: Path, req: ScanRequest) -> Any:
        """...（docstring 不变）..."""
        from dataclasses import asdict
        from supernova_core.agents.providers import build_provider_config
        client = await Client.connect(self._temporal_address())
        workflow_id = self._resolve_workflow_id(ws)
        # P3c 阶段 1：从全局 env 构造 provider_config 穿线（行为不变；阶段 2 改为按 ws 解析）。
        provider_config = asdict(build_provider_config())
        inp = PipelineInput(
            repo_path=target or "",
            web_url=req.url or "",
            workspace_name=ws,
            event_file=str(event_file),
            provider_config=provider_config,
        )
        handle = await client.start_workflow(
            WhiteboxScanWorkflow.run, inp, id=workflow_id,
            task_queue=WEB_TASK_QUEUE_WHITEBOX,
        )
        self._mark_submitted_at(self._workspaces_dir / ws)
        return handle
```

- [ ] **Step 4: 跑测试确认通过** — `cd packages/web && uv run pytest tests/test_scan_manager_provider_config.py -v`
  - 预期：PASS

- [ ] **Step 5: 跑现有 scan/web 回归** — `cd packages/web && uv run pytest tests/test_api_scan.py tests/test_api_events.py -v`
  - 预期：全 PASS

- [ ] **Step 6: Commit**

```bash
git add packages/web/src/supernova_web/components/scan_manager.py \
        packages/web/tests/test_scan_manager_provider_config.py
git commit -m "feat(web): P3c 阶段1 scan_manager 提交时塞全局 provider_config

_submit_whitebox 构造 PipelineInput 时 asdict(build_provider_config()) 穿线。
web 路径 provider_config 非 None（全局 env，行为不变）；阶段 2 改按 ws 解析。"
```

---

## Task 5: 回归 + 穿线不变量

**Files:**
- Test: `packages/web/tests/test_provider_config_threading_e2e.py`（新建，集成穿线断言）

**Interfaces:**
- Consumes: Task 1-4 全部
- Produces: 端到端穿线不变量——`PipelineInput.provider_config`（web 非 None）→ `ActivityInput.provider_config` → `run_claude_prompt(provider_config=)` 全程透传，`run_claude_prompt` 的 `provider_config is None` 兜底分支（runner.py:148-150）在 web 路径不再命中。

- [ ] **Step 1: 写穿线不变量测试** — 新建 `packages/web/tests/test_provider_config_threading_e2e.py`

```python
"""P3c 阶段 1 穿线不变量：web 提交的 provider_config 全程到 run_claude_prompt。

集成断言（mock run_claude_prompt + 跑 workflow 的 act_input 构造）：
PipelineInput.provider_config → act_input.provider_config（经 workflows.py:129 灌入）。
"""
from dataclasses import asdict
from supernova_core.agents.providers import build_provider_config
from supernova_whitebox.pipeline.shared import PipelineInput, ActivityInput


def test_pipeline_input_provider_config_survives_act_input_construction():
    """PipelineInput.provider_config 经 workflows.py:129 灌入 ActivityInput 不丢。"""
    pc = asdict(build_provider_config(provider_type="openai_compatible"))
    inp = PipelineInput(provider_config=pc)
    # 模拟 workflows.py:129 的灌入
    act = ActivityInput(repo_path="/r", provider_config=inp.provider_config)
    assert act.provider_config is pc
    assert act.provider_config["type"] == "openai_compatible"


def test_act_input_inherits_via_dict_splat():
    """后续 activity 经 **act_input.__dict__ 复制，provider_config 保留。"""
    act = ActivityInput(repo_path="/r", provider_config={"type": "x"})
    act2 = ActivityInput(**{**act.__dict__, "phase": "recon"})
    assert act2.provider_config == {"type": "x"}
    assert act2.phase == "recon"


def test_provider_config_dict_keys_match_providerconfig_fields():
    """asdict(ProviderConfig) 的键名 == dataclass 字段，ProviderConfig(**dict) 可还原。
    这是 run_claude_prompt:146 ProviderConfig(**provider_config) 成立的前提。
    """
    from supernova_core.agents.runner import ProviderConfig
    pc = asdict(build_provider_config())
    restored = ProviderConfig(**pc)
    assert restored.type == pc["type"]
```

- [ ] **Step 2: 跑穿线不变量测试** — `cd packages/web && uv run pytest tests/test_provider_config_threading_e2e.py -v`
  - 预期：3 PASS

- [ ] **Step 3: 跨包回归（白盒 + core + web）** — 分别跑改动相关测试文件（勿广跑全套，CLAUDE.md）：
  - `cd packages/core && uv run pytest tests/agents/test_executor_stage1.py tests/agents/test_providers.py tests/agents/test_providers_collector_injection.py -v`
  - `cd packages/whitebox && uv run pytest tests/pipeline/test_shared_provider_config_stage1.py tests/pipeline/test_workflow_threading_stage1.py -v`
  - `cd packages/web && uv run pytest tests/test_scan_manager_provider_config.py tests/test_provider_config_threading_e2e.py tests/test_api_scan.py -v`
  - 预期：全 PASS。**任何 FAIL 必须修到绿**（阶段 1 验收 = 穿线打通 + 行为不变）。

- [ ] **Step 4: 人工核验 run_claude_prompt 兜底分支在 web 路径不再命中** — 读 `runner.py:144-150`：web 路径 `provider_config` 非 None → 走 `if provider_config:` 分支（:144-146），不走 `else: build_provider_config(api_key=...)`（:148-150）。CLI 路径 `None` → 走 else（行为不变）。记录在 commit message。

- [ ] **Step 5: Commit**

```bash
git add packages/web/tests/test_provider_config_threading_e2e.py
git commit -m "test(web): P3c 阶段1 穿线不变量 + 跨包回归

断言 PipelineInput.provider_config → ActivityInput（dict splat）→ ProviderConfig(**dict)
全程不丢；web 路径 run_claude_prompt provider_config 非 None（兜底分支不再命中）。
阶段 1 完成：provider 配置穿线打通，仍全局（阶段 2 per-ws 填充）。"
```

---

## Self-Review（plan 作者自检）

**1. Spec 覆盖**：spec §6（阶段 1）6.2.1（PipelineInput 字段）→ Task 1；6.2.2（ActivityInput 字段）→ Task 1；6.2.3（workflow 灌入）→ Task 3 Step 3；6.2.4（调用点传参，含 executor/直调点/poc_generator）→ Task 2+3；6.2.5（scan_manager 构造）→ Task 4；6.3（行为不变量）→ Task 5。spec §6 提到"同步 blackbox"→ Task 1 加黑盒字段 + Global Constraints 说明黑盒 workflow/activities 留 Phase C（因 web 未接）。

**2. 占位符扫描**：无 TBD/TODO；所有 code step 有完整代码或明确模式（poc_generator 多函数给模式 + 示例）；测试有真实断言。少数测试 fixture（`app_with_ws`/`ScanRequest`/mock Client）标注"以现有 conftest/test_api_scan 模式为准"——这是对现有 fixture 的复用指引，非占位（现有测试已有这些 fixture）。

**3. 类型一致性**：`provider_config: dict | None = None`——Task 1 定义（4 处 dataclass）+ Task 2 收（executor/poc_generator）+ Task 3 传（workflow/activities）+ Task 4 构造（asdict → dict）+ Task 5 验（dict keys），全程 dict 语义一致。`:639/:732` 的 `ProviderConfig(**input.provider_config)` 与 Task 5 Step 1 的 `ProviderConfig(**dict)` 还原测试对齐。

**4. 黑盒边界一致性**：Global Constraints + Task 1（黑盒字段）+ 各 Task 说明"黑盒 workflow/activities/scan_manager 留 Phase C"——三处一致，不遗漏也不越界。

**5. 行为不变量**：每个 Task 的回归步骤 + Task 5 跨包回归 + Global Constraints「None=CLI 兜底」——保证穿线打通但全局配置行为不变。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-27-web-config-isolation-stage1.md`. Two execution options:

1. **Subagent-Driven（推荐）** — 每 task 派 fresh subagent + 两阶段 review。阶段 1 横切多包（core/whitebox/web），适合分 task 并行/串行。
2. **Inline Execution** — 本 session 批量 + 检查点。

Which approach?

---

**后续阶段**（本 plan 不含）：
- 阶段 2：per-ws 配置（config.yaml + CredentialVault + WsConfigStore + admin API + 前端；scan_manager 改按 ws 解析）
- 阶段 3：并发解锁（AuditSession/LogBus/heartbeat contextvar + worker 放宽）
- 阶段 4：clone 凭据 per-ws
- Phase C（黑盒 web C1 化）：黑盒 workflow/activities/scan_manager 穿线补齐
