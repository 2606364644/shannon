# GitNexus 轨多轮 agent 基础设施（spec-0） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 GitNexus 轨 LLM 调用层具备跑多轮 agent（带 grep/read 自主追链）的能力，为 spec-1（authz 深度判定）提供脚手架。

**Architecture:** `run_claude_prompt` 已支持 `max_turns`（多轮），本 plan 只做四件基础设施事：(1) 补 `BaseProvider.call()` ABC 的 `max_turns` 签名（Liskov）；(2) 新增 `GITNEXUS_VERDICT_RETRY` + `"gitnexus-verdict"` category；(3) 新增 `run_gitnexus_verdict_agent` 入口（读 `SHANNON_GITNEXUS_VERDICT_MAX_TURNS` env）；(4) 增大 GitNexus verdict activity 超时。不改判定逻辑（留给 spec-1）。

**Tech Stack:** Python 3.x / temporalio / pytest / 双引擎（claude-agent-sdk + openai-agents）

**Spec:** `docs/superpowers/specs/2026-07-02-gitnexus-deep-agent-infra-design.md`

## Global Constraints

- **不改判定逻辑**：本 plan 只让调用层"能"多轮；authz_judge 实际多轮用法是 spec-1。现有单次调用路径（`_make_verdict_llm_client` / `_make_gitnexus_llm_client` / `run_authz_gitnexus_judge` 单次）**行为不变**（回归锚点）。
- **不改 `chain_verdict`（inj/xss/ssrf）判定深度**：epic 非目标。
- **双引擎都要支持**：改动走 `run_claude_prompt` 统一抽象，glm-anthropic / glm-openai 都生效。
- **测试只跑改动相关文件**：勿广跑全套（CLAUDE.md §3：全套 pytest 有预存 hang）。
- **commit 风格**：conventional commits（feat/fix/refactor/test/docs），对齐 repo 现有 `git log`。

---

## File Structure

| 文件 | 责任 | 本 plan 改动 |
|---|---|---|
| `packages/core/src/shannon_core/agents/providers.py` | `BaseProvider` ABC + 工厂 | Task 1：ABC `call()` 补 `max_turns` |
| `packages/core/src/shannon_core/models/retry.py` | retry policy 单一映射源 | Task 2：加 `GITNEXUS_VERDICT_RETRY` + category |
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | temporalio activity | Task 3：加 `run_gitnexus_verdict_agent` |
| `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | temporalio workflow 编排 | Task 4：增大 authz_judge / chain_verdict 超时 |
| `packages/core/tests/agents/test_providers.py` | provider 测试 | Task 1 测试 |
| `packages/core/tests/models/test_retry.py` | retry 测试 | Task 2 测试 |
| `packages/whitebox/tests/pipeline/test_gitnexus_verdict_agent.py` | verdict agent 测试 | Task 3 测试（新建） |

> 若某个 test 文件路径在 repo 里不存在，执行时先 `find packages -name 'test_*.py' -path '*对应目录*'` 确认现有 test 位置，加入既有文件而非盲目新建。

---

### Task 1: `BaseProvider.call()` ABC 补 `max_turns` 签名

**Files:**
- Modify: `packages/core/src/shannon_core/agents/providers.py:66-90`
- Test: `packages/core/tests/agents/test_providers.py`

**Interfaces:**
- Consumes: 无（纯签名补齐）
- Produces: `BaseProvider.call` 签名含 `max_turns: int | None = None`；后续 task 不依赖此签名变更的行为（两实现已有该参数，纯 Liskov 补齐）

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/agents/test_providers.py
import inspect
from shannon_core.agents.providers import BaseProvider

def test_base_provider_call_has_max_turns_parameter():
    """ABC 签名须含 max_turns（两实现已有，补齐 Liskov）。"""
    sig = inspect.signature(BaseProvider.call)
    assert "max_turns" in sig.parameters
    assert sig.parameters["max_turns"].default is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest packages/core/tests/agents/test_providers.py::test_base_provider_call_has_max_turns_parameter -v`
Expected: FAIL — `max_turns` not in signature（ABC 现状无此参数）

- [ ] **Step 3: Write minimal implementation**

修改 `providers.py:66-75` 的 ABC 签名，`audit_logger` 后加 `max_turns`：

```python
@abstractmethod
async def call(
    self,
    prompt: str,
    cwd: str,
    model_tier: str = "medium",
    output_format: dict | None = None,
    deliverables_subdir: str | None = None,
    audit_logger: ToolAuditLogger | None = None,
    max_turns: int | None = None,           # 新增：对齐 AnthropicProvider/OpenAIProvider 实现
) -> ClaudeRunResult:
```

docstring 的 `Args:` 段补一行：
```python
            max_turns: agent 最大轮数（None=引擎默认 200）；>1 启用多轮 agent
```

> **不碰 `structured_output_schema`**：它在 provider 层的状态未在本 plan 调查确认，留 spec-1 实际用时按需评估。`run_claude_prompt` 层已支持该参数（`runner.py:111`），不依赖 provider ABC 补。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest packages/core/tests/agents/test_providers.py::test_base_provider_call_has_max_turns_parameter -v`
Expected: PASS

- [ ] **Step 5: 回归——两实现签名未破**

Run: `pytest packages/core/tests/agents/ -v -k "provider"`
Expected: PASS（两实现已有 `max_turns`，ABC 补齐零行为变化）

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/agents/providers.py packages/core/tests/agents/test_providers.py
git commit -m "refactor(agents): BaseProvider.call ABC 补 max_turns 签名（Liskov 补齐）"
```

---

### Task 2: `GITNEXUS_VERDICT_RETRY` + `"gitnexus-verdict"` category

**Files:**
- Modify: `packages/core/src/shannon_core/models/retry.py:77`(后插)、`:93`(Category)、`:96-118`(retry_for)
- Test: `packages/core/tests/models/test_retry.py`

**Interfaces:**
- Consumes: 无
- Produces: `retry_for("gitnexus-verdict")` 返回 `GITNEXUS_VERDICT_RETRY`（max_attempts=3）；`Category` Literal 含 `"gitnexus-verdict"`

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/models/test_retry.py
from shannon_core.models.retry import GITNEXUS_VERDICT_RETRY, retry_for

def test_gitnexus_verdict_retry_policy():
    p = GITNEXUS_VERDICT_RETRY
    assert p.maximum_attempts == 3          # 多轮 agent 贵，不像 PRODUCTION_RETRY 重试 50 次

def test_retry_for_gitnexus_verdict_category():
    assert retry_for("gitnexus-verdict") is GITNEXUS_VERDICT_RETRY
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest packages/core/tests/models/test_retry.py::test_gitnexus_verdict_retry_policy -v`
Expected: FAIL — `ImportError: cannot import name 'GITNEXUS_VERDICT_RETRY'`

- [ ] **Step 3: Write minimal implementation**

(a) `retry.py` 第 77 行后（`CODE_INDEX_RETRY` 之后）插入新 policy：

```python
# GitNexus 多轮 verdict agent 专用:短重试 + 多轮。
# 多轮 agent(带 grep/read 追链)比单次贵,max 3 避免幂等失败被放大;
# 区别于 PRODUCTION_RETRY(max 50,给单次 LLM agent)。详见
# docs/superpowers/specs/2026-07-02-gitnexus-deep-agent-infra-design.md §3.3。
GITNEXUS_VERDICT_RETRY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=30),
    maximum_interval=timedelta(minutes=2),
    backoff_coefficient=2.0,
    non_retryable_error_types=NON_RETRYABLE,
)
```

(b) 第 93 行 `Category` Literal 加 `"gitnexus-verdict"`：

```python
Category = Literal["standard", "vuln", "log", "preflight", "auth-validation", "code-index", "gitnexus-verdict"]
```

(c) `retry_for`（:106-118）的 if 链加分支（在 `raise` 前）：

```python
    if category == "gitnexus-verdict":
        return GITNEXUS_VERDICT_RETRY
```

docstring 的 category 列表补 `- gitnexus-verdict: 多轮 verdict agent，有界 GITNEXUS_VERDICT_RETRY。`

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest packages/core/tests/models/test_retry.py -v -k "gitnexus_verdict"`
Expected: PASS（两个测试都过）

- [ ] **Step 5: 回归——现有 category 不破**

Run: `pytest packages/core/tests/models/test_retry.py -v`
Expected: PASS（standard/vuln/code-index/log/preflight/auth-validation 全部不变）

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/models/retry.py packages/core/tests/models/test_retry.py
git commit -m "feat(retry): 新增 GITNEXUS_VERDICT_RETRY + gitnexus-verdict category（多轮 verdict 专用）"
```

---

### Task 3: `run_gitnexus_verdict_agent` 入口 + `SHANNON_GITNEXUS_VERDICT_MAX_TURNS` env

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`（加新函数，位置在 `_make_verdict_llm_client` 附近，:866 后）
- Test: `packages/whitebox/tests/pipeline/test_gitnexus_verdict_agent.py`（新建）

**Interfaces:**
- Consumes: `run_claude_prompt`（`shannon_core.agents.runner`，延迟 import 对齐 `_make_verdict_llm_client:859`）；`SHANNON_GITNEXUS_VERDICT_MAX_TURNS` env（默认 30）
- Produces: `async def run_gitnexus_verdict_agent(*, prompt: str, repo_path: str, structured_output_schema: dict | None = None) -> ClaudeRunResult`——spec-1 的 authz_judge 调此函数跑多轮判定

- [ ] **Step 1: Write the failing test**

```python
# packages/whitebox/tests/pipeline/test_gitnexus_verdict_agent.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from shannon_whitebox.pipeline import activities


@pytest.mark.asyncio
async def test_verdict_agent_reads_max_turns_env(monkeypatch):
    """SHANNON_GITNEXUS_VERDICT_MAX_TURNS env 透传给 run_claude_prompt。"""
    monkeypatch.setenv("SHANNON_GITNEXUS_VERDICT_MAX_TURNS", "7")
    captured: dict = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        result = MagicMock()
        result.text = "ok"
        result.success = True
        result.turns = 1
        return result

    # 延迟 import 从源模块取，patch 源模块有效
    monkeypatch.setattr("shannon_core.agents.runner.run_claude_prompt", fake_run)

    await activities.run_gitnexus_verdict_agent(prompt="p", repo_path="/r")

    assert captured["max_turns"] == 7
    assert captured["model_tier"] == "medium"


@pytest.mark.asyncio
async def test_verdict_agent_default_max_turns(monkeypatch):
    """不设 env 时默认 30。"""
    monkeypatch.delenv("SHANNON_GITNEXUS_VERDICT_MAX_TURNS", raising=False)
    captured: dict = {}
    async def fake_run(**kwargs):
        captured.update(kwargs)
        return MagicMock(text="ok", success=True, turns=1)
    monkeypatch.setattr("shannon_core.agents.runner.run_claude_prompt", fake_run)

    await activities.run_gitnexus_verdict_agent(prompt="p", repo_path="/r")
    assert captured["max_turns"] == 30
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest packages/whitebox/tests/pipeline/test_gitnexus_verdict_agent.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'run_gitnexus_verdict_agent'`

- [ ] **Step 3: Write minimal implementation**

在 `activities.py`（`_make_verdict_llm_client` 之后，约 :866 后）加：

```python
async def run_gitnexus_verdict_agent(
    *,
    prompt: str,
    repo_path: str,
    structured_output_schema: dict | None = None,
) -> "ClaudeRunResult":
    """GitNexus 多轮 verdict agent：带 grep/read 自主追链，吃确定性候选做深度判定。

    max_turns 走 SHANNON_GITNEXUS_VERDICT_MAX_TURNS（默认 30）。返回完整 ClaudeRunResult
    （含 turns/cost/structured_output），不截断为 str——区别于 _make_verdict_llm_client 的单次薄包装。

    供 spec-1 的 run_authz_gitnexus_judge 多轮判定用。单测 mock run_claude_prompt 验证 max_turns 透传。
    """
    from shannon_core.agents.runner import run_claude_prompt  # 延迟 import，对齐 :859
    return await run_claude_prompt(
        prompt=prompt,
        repo_path=repo_path,
        model_tier="medium",
        max_turns=int(os.getenv("SHANNON_GITNEXUS_VERDICT_MAX_TURNS", "30")),
        structured_output_schema=structured_output_schema,
    )
```

> **`ClaudeRunResult` 返回注解**用字符串前向引用（`"ClaudeRunResult"`）避免顶部 import 循环；或若 `activities.py` 顶部已 import 则直接用。执行时确认 `os` 已在 activities.py 顶部 import（现状用了 `os.getenv`，应有）。
>
> **`tool_audit_logger` 本 task 不接**：可观测性接入（对齐 `run_vuln_agent` 的 `SessionToolAuditLogger`，`activities.py:167`）留 spec-1 实际用时按 vuln agent 模式补——本 task 核心是验证多轮能力（max_turns 透传），audit 非阻塞。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest packages/whitebox/tests/pipeline/test_gitnexus_verdict_agent.py -v`
Expected: PASS（两个测试：env=7 透传 / 默认 30）

- [ ] **Step 5: 回归——现有 _make_verdict_llm_client 不破**

Run: `pytest packages/whitebox/tests/pipeline/ -v -k "verdict_llm or gitnexus_llm"`
Expected: PASS（单次 client 路径行为不变）

- [ ] **Step 6: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/tests/pipeline/test_gitnexus_verdict_agent.py
git commit -m "feat(whitebox): 新增 run_gitnexus_verdict_agent 多轮判定入口（spec-0 基础设施）"
```

---

### Task 4: 增大 GitNexus verdict activity 超时

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py:372`(authz_judge 超时)、`:393`(chain_verdict 超时)
- Test: `packages/whitebox/tests/pipeline/test_workflows_safety.py`（或现有 workflow 测试，AST 锚点）

**Interfaces:**
- Consumes: 无
- Produces: authz_judge 超时 10min→30min；chain_verdict 超时 5min→15min（为多轮 agent 留窗口）

- [ ] **Step 1: Write the failing test**

```python
# packages/whitebox/tests/pipeline/test_workflows_safety.py（追加，或现有 AST 锚点测试文件）
def test_authz_judge_timeout_increased_for_multi_turn():
    """authz_judge 超时须 ≥30min（多轮 agent 窗口）。"""
    import inspect
    from shannon_whitebox.pipeline import workflows
    src = inspect.getsource(workflows)
    # 找 run_authz_gitnexus_judge 的 start_to_close_timeout
    assert "timedelta(minutes=30)" in src, "authz_judge 超时应增到 30min"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest packages/whitebox/tests/pipeline/test_workflows_safety.py::test_authz_judge_timeout_increased_for_multi_turn -v`
Expected: FAIL — 现状是 `timedelta(minutes=10)`

- [ ] **Step 3: Write minimal implementation**

`workflows.py:372`（`run_authz_gitnexus_judge` 的 `start_to_close_timeout`）：
```python
start_to_close_timeout=timedelta(minutes=30),   # 原 10；多轮 agent 窗口（spec-0）
```

`workflows.py:393`（`run_gitnexus_chain_verdict` 的 `start_to_close_timeout`）：
```python
start_to_close_timeout=timedelta(minutes=15),   # 原 5；多轮 agent 窗口（spec-0）
```

> **不改 retry_policy**：authz_judge/chain_verdict 仍 `retry_for("standard")`（PRODUCTION_RETRY）。切 `"gitnexus-verdict"` 是 spec-1 的事（spec-1 实际启用多轮时切）。本 task 只开超时窗口。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest packages/whitebox/tests/pipeline/test_workflows_safety.py::test_authz_judge_timeout_increased_for_multi_turn -v`
Expected: PASS

- [ ] **Step 5: 回归——workflow 编排其他部分不破**

Run: `pytest packages/whitebox/tests/pipeline/test_workflows_safety.py -v`
Expected: PASS（其他 AST 锚点 / sandbox 守卫不变）

- [ ] **Step 6: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/workflows.py packages/whitebox/tests/pipeline/test_workflows_safety.py
git commit -m "feat(whitebox): authz_judge/chain_verdict 超时增至 30/15min（多轮 agent 窗口）"
```

---

## 验证（非单测，真机探针——Task 全过后做）

**V-双引擎（spec-0 G4 / V5）**：多轮 verdict agent 在双引擎各跑一次，确认 `result.turns > 1`。

1. 写一个临时探针脚本（参照 `scripts/validate_glm_task_probe.py` 风格），在 glm-anthropic 和 glm-openai 各跑：
   ```python
   # 临时探针：调 run_gitnexus_verdict_agent 跑一个需 grep 追链的 prompt
   result = await run_gitnexus_verdict_agent(
       prompt="在 <repo> 找所有 session.regenerate 调用并报告 file:line",
       repo_path="<test repo>",
   )
   assert result.success and result.turns > 1, f"多轮未触发: turns={result.turns}"
   ```
2. `SHANNON_AI_PROVIDER=anthropic_api python <探针>` + `SHANNON_AI_PROVIDER=openai_compatible python <探针>`，两次都 `turns > 1`。
3. 探针脚本不进 repo（临时验证），或放 `scripts/validate_gitnexus_verdict_probe.py` 留作回归。

---

## Self-Review（plan 写完后自查）

**Spec 覆盖**（spec-0 §1 目标）：
- G1（ABC 签名）→ Task 1 ✓
- G2（多轮 verdict 契约/入口）→ Task 3 ✓
- G3（retry/env/超时）→ Task 2（retry）+ Task 3（env）+ Task 4（超时）✓
- G4（双引擎对齐）→ 验证 section ✓
- 非目标（不改判定逻辑/chain_verdict/LLM 轨）→ Global Constraints 锁定 ✓

**Placeholder 扫描**：无 TBD/TODO。`tool_audit_logger` 明确标"留 spec-1 补"（范围界定，非占位）；`structured_output_schema` 在 provider 层标"未调查确认，不碰"（保守，非占位）。✓

**类型一致**：`run_gitnexus_verdict_agent` 签名在 Task 3 定义、验证 section 引用，一致（`prompt`/`repo_path`/`structured_output_schema` → `ClaudeRunResult`）。`GITNEXUS_VERDICT_RETRY` / `"gitnexus-verdict"` 在 Task 2 定义、spec-1 将引用，一致。✓
