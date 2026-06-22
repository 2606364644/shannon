# Retry Policy 对齐 TS — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 PY 所有 `workflow.execute_activity` 调用都带显式 `retry_policy`,通过集中 helper `retry_for(category, mode)` 与 TS 对齐,并用 AST 锚点测试永久防回归。

**Architecture:** 在 `models/retry.py` 新增 `VULN_RETRY` 常量 + `retry_for(category, mode)` category 映射(`standard` 委托既有的 mode 工厂 `get_retry_policy`)。白盒/黑盒两个 `workflows.py` 把每个 `execute_activity` 改走 `retry_for(...)`。每个包加一个 AST 锚点测试,断言所有 `execute_activity` 都带 `retry_policy=`。

**Tech Stack:** Python 3.13、temporalio 1.27.2(`RetryPolicy`、`workflow.execute_activity(retry_policy=...)`)、pytest。

## Global Constraints

- **temporalio==1.27.2**:retry policy 通过 `workflow.execute_activity(..., retry_policy=RetryPolicy(...))` 挂载;不传则吃 Temporal server 默认(≈无限重试)——本计划要消灭所有这类裸奔。
- **Python 3.13**:`typing.Literal`、`str | None` 语法可用。
- **分支 `feat/fork-py`**:在此分支实现,每个 task 末尾提交。
- **只跑改动相关测试子集**:全量 pytest 会 hang 在 Temporal/网络慢测试(memory 锚点)。每个 task 给了精确的 pytest 路径。
- **注释中文**:与现有 `workflows.py` / `retry.py` 风格一致。
- **纯对齐 TS**:**不动** `non_retryable_error_types`、**不做** 529/429 区分优化(那是另一个 spec)。
- **不引入 whitebox mode 感知**:whitebox 调 `retry_for("standard")`(不传 mode → 默认 production),保持现状。

---

## File Structure

| 文件 | 责任 | 本计划动作 |
|---|---|---|
| `packages/core/src/shannon_core/models/retry.py` | retry tier 常量 + 工厂 | 新增 `VULN_RETRY`、`Category`、`retry_for()` |
| `packages/core/tests/test_retry_profiles.py` | retry 选择逻辑单测 | 扩展:`VULN_RETRY` 参数 + `retry_for` 映射 |
| `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | 白盒 workflow | 所有 `execute_activity` 改走 `retry_for`;清理 import |
| `packages/whitebox/tests/test_retry_policy_coverage.py` | **新建** AST 锚点 | 断言白盒每个 `execute_activity` 带 `retry_policy=` |
| `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` | 黑盒 workflow | 所有 `execute_activity` 改走 `retry_for`;清理 import |
| `packages/blackbox/tests/test_retry_policy_coverage.py` | **新建** AST 锚点 | 断言黑盒每个 `execute_activity` 带 `retry_policy=` |

---

## Task 1: `retry.py` — `VULN_RETRY` + `retry_for(category, mode)` helper

**Files:**
- Modify: `packages/core/src/shannon_core/models/retry.py`
- Test: `packages/core/tests/test_retry_profiles.py`

**Interfaces:**
- Consumes: 既有 `PRODUCTION_RETRY/TESTING_RETRY/SUBSCRIPTION_RETRY/PREFLIGHT_RETRY/AUTH_VALIDATION_RETRY` 常量、`get_retry_policy(mode)` 工厂、`NON_RETRYABLE`。
- Produces: `VULN_RETRY: RetryPolicy`、`Category = Literal[...]`、`retry_for(category: Category, mode: str | None = None) -> RetryPolicy`。Task 2/3 依赖 `retry_for`。

- [ ] **Step 1: 写失败测试** — 在 `test_retry_profiles.py` 末尾追加(并更新顶部 import):

```python
# 顶部 import 改为:
from shannon_core.models.retry import (
    PRODUCTION_RETRY,
    TESTING_RETRY,
    SUBSCRIPTION_RETRY,
    PREFLIGHT_RETRY,
    AUTH_VALIDATION_RETRY,
    VULN_RETRY,
    get_retry_policy,
    retry_for,
)


class TestVulnRetry:
    def test_vuln_retry_params(self):
        assert VULN_RETRY.maximum_attempts == 5
        assert VULN_RETRY.initial_interval == timedelta(minutes=1)
        assert VULN_RETRY.maximum_interval == timedelta(minutes=5)
        assert VULN_RETRY.backoff_coefficient == 2.0
        assert VULN_RETRY.non_retryable_error_types  # 共享 NON_RETRYABLE


class TestRetryFor:
    def test_standard_delegates_to_get_retry_policy(self):
        assert retry_for("standard") == get_retry_policy(None)
        assert retry_for("standard", "production") == PRODUCTION_RETRY
        assert retry_for("standard", "testing") == TESTING_RETRY
        assert retry_for("standard", "subscription") == SUBSCRIPTION_RETRY

    def test_standard_default_is_production(self):
        assert retry_for("standard") == PRODUCTION_RETRY

    def test_vuln_category(self):
        assert retry_for("vuln") == VULN_RETRY

    def test_log_category(self):
        assert retry_for("log") == PREFLIGHT_RETRY

    def test_preflight_category(self):
        assert retry_for("preflight") == PREFLIGHT_RETRY

    def test_auth_validation_category(self):
        assert retry_for("auth-validation") == AUTH_VALIDATION_RETRY

    def test_unknown_category_raises(self):
        import pytest
        with pytest.raises(ValueError):
            retry_for("bogus")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest packages/core/tests/test_retry_profiles.py -v`
Expected: FAIL — `ImportError: cannot import name 'VULN_RETRY'` / `'retry_for'`。

- [ ] **Step 3: 实现** — 改 `packages/core/src/shannon_core/models/retry.py`:

顶部加 `Literal` import:
```python
from datetime import timedelta
from typing import Literal

from temporalio.common import RetryPolicy

from shannon_core.models.errors import NON_RETRYABLE_TYPES
```

在 `SUBSCRIPTION_RETRY` 定义之后、`get_retry_policy` 之前插入 `VULN_RETRY`;在文件末尾加 `Category` + `retry_for`:

```python
# vuln agent 专用:per-vt fan-out 下封顶 ~12min,有意分歧于 TS PRODUCTION_RETRY。
# 详见 docs/superpowers/specs/2026-06-22-retry-policy-alignment-design.md §2.3。
VULN_RETRY = RetryPolicy(
    maximum_attempts=5,
    initial_interval=timedelta(minutes=1),
    maximum_interval=timedelta(minutes=5),
    backoff_coefficient=2.0,
    non_retryable_error_types=NON_RETRYABLE,
)


def get_retry_policy(mode: str | None = None) -> RetryPolicy:
    """Select a retry policy by mode name.

    Returns PRODUCTION_RETRY when *mode* is ``None`` or unrecognised.
    """
    profiles = {
        "production": PRODUCTION_RETRY,
        "testing": TESTING_RETRY,
        "subscription": SUBSCRIPTION_RETRY,
    }
    return profiles.get(mode or "production", PRODUCTION_RETRY)


Category = Literal["standard", "vuln", "log", "preflight", "auth-validation"]


def retry_for(category: Category, mode: str | None = None) -> RetryPolicy:
    """按 activity 类别选 retry policy(单一映射源)。

    - standard: LLM agent + 确定性处理。委托 get_retry_policy(mode) 保留 mode 感知
      (testing/subscription);不传 mode 默认 production。
    - vuln:     per-vt vuln agent,有界 VULN_RETRY。
    - log:      phase log marker(10s 写),短 policy。
    - preflight / auth-validation: 现有短 tier。
    """
    if category == "standard":
        return get_retry_policy(mode)
    if category == "vuln":
        return VULN_RETRY
    if category == "log":
        return PREFLIGHT_RETRY
    if category == "preflight":
        return PREFLIGHT_RETRY
    if category == "auth-validation":
        return AUTH_VALIDATION_RETRY
    raise ValueError(f"unknown activity category: {category!r}")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest packages/core/tests/test_retry_profiles.py -v`
Expected: PASS(原 5 个 + 新 8 个全绿)。

- [ ] **Step 5: 提交**

```bash
git add packages/core/src/shannon_core/models/retry.py packages/core/tests/test_retry_profiles.py
git commit -m "feat(retry): 新增 VULN_RETRY + retry_for(category,mode) 集中映射"
```

---

## Task 2: 白盒 workflow 迁移 + AST 锚点测试

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`
- Test: `packages/whitebox/tests/test_retry_policy_coverage.py`(新建)

**Interfaces:**
- Consumes: Task 1 的 `retry_for`。
- Produces: 白盒所有 `execute_activity` 带 `retry_policy=retry_for(...)`;锚点测试。

> ⚠️ reporting phase 在并行开发(见 git log `d31dd0e`/`1e06816`/`fa5de44`),行号会漂移。**以 activity 名为锚**,AST 锚点测试是最终正确性保证。

- [ ] **Step 1: 写失败锚点测试** — 新建 `packages/whitebox/tests/test_retry_policy_coverage.py`:

```python
"""回归锚点:每个 workflow.execute_activity 必须声明 retry_policy。

防止"裸奔→Temporal 默认≈无限重试"回归。详见
docs/superpowers/specs/2026-06-22-retry-policy-alignment-design.md。
"""
import ast
from pathlib import Path

WORKFLOW_FILE = (
    Path(__file__).resolve().parents[1]
    / "src" / "shannon_whitebox" / "pipeline" / "workflows.py"
)


def _execute_activity_calls(source: str) -> list[ast.Call]:
    tree = ast.parse(source)
    calls = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute_activity"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "workflow"
        ):
            calls.append(node)
    return calls


def test_every_execute_activity_has_retry_policy():
    source = WORKFLOW_FILE.read_text()
    calls = _execute_activity_calls(source)
    assert calls, "no execute_activity calls found — 锚点测试接线坏了"
    missing = []
    for call in calls:
        if "retry_policy" not in {kw.arg for kw in call.keywords}:
            missing.append(ast.get_source_segment(source, call))
    assert not missing, (
        f"{len(missing)} 个 execute_activity 缺 retry_policy="
        f"(会落 Temporal 默认≈无限重试):\n"
        + "\n---\n".join(str(m) for m in missing)
    )
```

- [ ] **Step 2: 跑锚点测试确认失败(列出所有裸奔点)**

Run: `pytest packages/whitebox/tests/test_retry_policy_coverage.py -v`
Expected: FAIL — 列出 ~19 个缺 `retry_policy=` 的调用(code_index / merge_sink_reports / ... / RECON / vuln / assemble_report / run_agent(report) / 14 个 log marker)。**记下这个清单作为迁移核对表。**

- [ ] **Step 3: 改 import** — `workflows.py` 顶部:

把(约 L6):
```python
from temporalio.common import RetryPolicy
```
**删除**(迁移后 whitebox 不再内联构造 RetryPolicy)。

把(约 L26-28):
```python
    from shannon_core.models.retry import (
        PREFLIGHT_RETRY, AUTH_VALIDATION_RETRY, PRODUCTION_RETRY, NON_RETRYABLE,
    )
```
改为:
```python
    from shannon_core.models.retry import retry_for
```

- [ ] **Step 4: 迁移各 `execute_activity`** — 按下表逐个改。**ADD** = 在 `start_to_close_timeout=...` 之后、闭合 `)` 之前插一行 `retry_policy=retry_for("<cat>"),`;**MIGRATE** = 把现有 `retry_policy=<旧常量>` 换成 `retry_policy=retry_for("<cat>")`;**VULN** = 特殊,见 Step 5。

| activity | 动作 | category |
|---|---|---|
| `run_preflight` | MIGRATE | preflight |
| `run_credential_check` | MIGRATE | preflight |
| `run_auth_validation` | MIGRATE | auth-validation |
| `run_agent`(PRE_RECON) | MIGRATE(原 PRODUCTION_RETRY) | standard |
| `run_code_index` | ADD | standard |
| `run_merge_sink_reports` | ADD | standard |
| `run_entry_point_fusion` | ADD | standard |
| `run_save_adjudication` | ADD | standard |
| `run_framework_analysis` | ADD | standard |
| `run_frontend_mapping` | ADD | standard |
| `run_route_chain_building` | ADD | standard |
| `run_agent`(RECON)🔥 | ADD | standard |
| `run_risk_scoring` | ADD | standard |
| `run_render_dataflow_hints` | ADD | standard |
| `run_attack_chain_assembly` | ADD | standard |
| `render_findings` | ADD | standard |
| `assemble_report` | ADD | standard |
| `run_agent`(agent_name="report") | ADD | standard |
| 所有 `log_phase_start_activity` / `log_phase_complete_activity`(~14 处) | ADD | log |

ADD 模式示例(RECON agent —— 最大的火),前:
```python
                metrics = await workflow.execute_activity(
                    activities.run_agent,
                    ActivityInput(**{**act_input.__dict__, "agent_name": AgentName.RECON.value}),
                    start_to_close_timeout=timedelta(hours=2),
                )
```
后:
```python
                metrics = await workflow.execute_activity(
                    activities.run_agent,
                    ActivityInput(**{**act_input.__dict__, "agent_name": AgentName.RECON.value}),
                    start_to_close_timeout=timedelta(hours=2),
                    retry_policy=retry_for("standard"),
                )
```

ADD 模式示例(log marker):
```python
        await workflow.execute_activity(
            activities.log_phase_start_activity,
            args=[...],
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=retry_for("log"),
        )
```

MIGRATE 模式示例(PRE_RECON),前 `retry_policy=PRODUCTION_RETRY,` → 后 `retry_policy=retry_for("standard"),`。preflight/auth 同理换成对应 category。

> 注:`asyncio.gather(...)` 里嵌套的 `execute_activity`(code_index∥PRE_RECON、framework∥frontend)同样在闭合 `)` 前加 `retry_policy=retry_for("standard"),`——它们是独立的 Call 节点,锚点测试会覆盖。

- [ ] **Step 5: vuln 特殊处理** — 把内联 RetryPolicy 换成 `retry_for("vuln")`。前(约 L281-292):
```python
                    coro = workflow.execute_activity(
                        activities.run_vuln_agent,
                        ActivityInput(**{**act_input.__dict__, "agent_name": agent_name.value}),
                        start_to_close_timeout=timedelta(hours=2),
                        retry_policy=RetryPolicy(
                            maximum_attempts=3,
                            initial_interval=timedelta(seconds=30),
                            maximum_interval=timedelta(minutes=5),
                            backoff_coefficient=2.0,
                            non_retryable_error_types=NON_RETRYABLE,
                        ),
                    )
```
后:
```python
                    coro = workflow.execute_activity(
                        activities.run_vuln_agent,
                        ActivityInput(**{**act_input.__dict__, "agent_name": agent_name.value}),
                        start_to_close_timeout=timedelta(hours=2),
                        retry_policy=retry_for("vuln"),
                    )
```

- [ ] **Step 6: 跑锚点测试确认通过**

Run: `pytest packages/whitebox/tests/test_retry_policy_coverage.py -v`
Expected: PASS(0 个 missing)。

- [ ] **Step 7: 跑白盒相关回归子集**

Run: `pytest packages/whitebox/tests/test_workflows.py packages/whitebox/tests/test_reporting_workflow.py packages/whitebox/tests/test_phase_marker_activities.py packages/whitebox/tests/test_log_phase_start_args.py -v`
Expected: PASS(迁移不改语义;无测试断言 vuln 旧 policy,已核实)。

- [ ] **Step 8: 提交**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/workflows.py packages/whitebox/tests/test_retry_policy_coverage.py
git commit -m "feat(whitebox): 所有 execute_activity 走 retry_for + AST 锚点防裸奔"
```

---

## Task 3: 黑盒 workflow 迁移 + AST 锚点测试

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`
- Test: `packages/blackbox/tests/test_retry_policy_coverage.py`(新建)

**Interfaces:**
- Consumes: Task 1 的 `retry_for`。
- Produces: 黑盒所有 `execute_activity` 带 `retry_policy=`;锚点测试。

- [ ] **Step 1: 写失败锚点测试** — 新建 `packages/blackbox/tests/test_retry_policy_coverage.py`,与白盒同构,仅路径不同:

```python
"""回归锚点:每个 workflow.execute_activity 必须声明 retry_policy。

防止"裸奔→Temporal 默认≈无限重试"回归。详见
docs/superpowers/specs/2026-06-22-retry-policy-alignment-design.md。
"""
import ast
from pathlib import Path

WORKFLOW_FILE = (
    Path(__file__).resolve().parents[1]
    / "src" / "shannon_blackbox" / "pipeline" / "workflows.py"
)


def _execute_activity_calls(source: str) -> list[ast.Call]:
    tree = ast.parse(source)
    calls = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute_activity"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "workflow"
        ):
            calls.append(node)
    return calls


def test_every_execute_activity_has_retry_policy():
    source = WORKFLOW_FILE.read_text()
    calls = _execute_activity_calls(source)
    assert calls, "no execute_activity calls found — 锚点测试接线坏了"
    missing = []
    for call in calls:
        if "retry_policy" not in {kw.arg for kw in call.keywords}:
            missing.append(ast.get_source_segment(source, call))
    assert not missing, (
        f"{len(missing)} 个 execute_activity 缺 retry_policy="
        f"(会落 Temporal 默认≈无限重试):\n"
        + "\n---\n".join(str(m) for m in missing)
    )
```

- [ ] **Step 2: 跑锚点测试确认失败**

Run: `pytest packages/blackbox/tests/test_retry_policy_coverage.py -v`
Expected: FAIL — 列出 `assemble_report`、`finalize_report`、5 个 log marker 缺 `retry_policy=`。

- [ ] **Step 3: 改 import** — `workflows.py` 顶部(约 L29-32):
```python
    from shannon_core.models.retry import (
        PREFLIGHT_RETRY, AUTH_VALIDATION_RETRY, NON_RETRYABLE,
        get_retry_policy,
    )
```
改为:
```python
    from shannon_core.models.retry import retry_for
```
(NON_RETRYABLE/get_retry_policy/PREFLIGHT/AUTH 常量迁移后都不再直接用。)

- [ ] **Step 4: 迁移 mode 变量** — 把(约 L64-66):
```python
        retry_policy = get_retry_policy(
            "testing" if input.pipeline_testing_mode else (input.retry_profile or "production")
        )
```
改为(委托 `retry_for` → `get_retry_policy`,行为等价):
```python
        retry_policy = retry_for(
            "standard",
            "testing" if input.pipeline_testing_mode else (input.retry_profile or "production"),
        )
```
`run_recon` / `run_exploit_agent` / `run_report_agent` 已用 `retry_policy=retry_policy` 变量,**无需改**。

- [ ] **Step 5: 迁移 preflight / auth-validation**

`run_blackbox_preflight`(约 L76)`retry_policy=PREFLIGHT_RETRY,` → `retry_policy=retry_for("preflight"),`。
`run_blackbox_auth_validation`(约 L121)`retry_policy=AUTH_VALIDATION_RETRY,` → `retry_policy=retry_for("auth-validation"),`。

- [ ] **Step 6: 补漏配点**

`assemble_report`(约 L295-298)加 `retry_policy=retry_policy,`(复用 mode 变量,standard)。
`finalize_report`(约 L312-315)加 `retry_policy=retry_policy,`。
示例:
```python
            await workflow.execute_activity(
                activities.assemble_report, act_input,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=retry_policy,
            )
```

- [ ] **Step 7: 补 log marker** — 5 处 `log_phase_start_activity`(preflight/auth-validation/recon-blackbox/exploitation/reporting)各加 `retry_policy=retry_for("log"),`:
```python
        await workflow.execute_activity(
            activities.log_phase_start_activity,
            BlackboxActivityInput(**{**act_input.__dict__, "phase": "preflight"}),
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=retry_for("log"),
        )
```

- [ ] **Step 8: 跑锚点测试确认通过**

Run: `pytest packages/blackbox/tests/test_retry_policy_coverage.py -v`
Expected: PASS(0 个 missing)。

- [ ] **Step 9: 跑黑盒相关回归子集**

Run: `pytest packages/blackbox/tests/test_workflows.py packages/blackbox/tests/test_finalize_report.py packages/blackbox/tests/test_report_assembler.py -v`
Expected: PASS。

- [ ] **Step 10: 提交**

```bash
git add packages/blackbox/src/shannon_blackbox/pipeline/workflows.py packages/blackbox/tests/test_retry_policy_coverage.py
git commit -m "feat(blackbox): 所有 execute_activity 走 retry_for + AST 锚点防裸奔"
```

---

## Self-Review(plan 作者自查)

**1. Spec coverage:**
- §4.1 retry.py(VULN_RETRY + retry_for)→ Task 1 ✓
- §4.2 分类法 → Task 1 测试覆盖每个 category ✓
- §5.1 白盒清单(含补正的 assemble_report + run_agent(report))→ Task 2 表格 ✓
- §5.2 黑盒清单 → Task 3 ✓
- §7 AST 锚点 + category 校验 → Task 2/3 锚点测试 + Task 1 `test_unknown_category_raises` ✓
- §10 验收(现有单测全绿)→ Task 2 Step7 / Task 3 Step9 ✓

**2. Placeholder scan:** 无 TBD/TODO;每个 code step 给了完整代码;site 表格每个 activity 都有明确 category + 动作(ADD/MIGRATE/VULN)。

**3. Type consistency:** `retry_for(category: Category, mode: str | None = None) -> RetryPolicy` 在 Task 1 定义,Task 2/3 调用签名一致(`retry_for("standard")` / `retry_for("standard", mode)` / `retry_for("vuln")` / `retry_for("log")` / `retry_for("preflight")` / `retry_for("auth-validation")`)。`Category` Literal 的 5 个值与所有调用点字符串精确匹配。✓

**4. 行号漂移风险:** reporting phase 并行开发,已在 Task 2 头部警示 + 锚定 activity 名 + AST 兜底。✓

---

## 执行后(本计划不做)

- 人工冒烟:真仓库跑一次 `whitebox start`,确认 retry 行为符合 tier(merge 前验证,与其它 spec 冒烟同批)。
- memory 更新:merge 后把 `retry-policy-py-ts-divergence.md` 标记为"已修复"。
- 后续 spec:529/429 错误分类优化(独立)。
