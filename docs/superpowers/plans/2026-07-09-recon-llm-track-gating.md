# 侦察层 LLM 纳入 SHANNON_LLM_TRACK_ENABLED 开关 — 实现计划

> ⚠️ **本 plan 已部分回退（2026-07-14，plan smooth-wandering-dolphin）。** 「关掉 pre-recon/recon」的前提「GitNexus 兜底 recon」对 authz 证伪 —— authz Vertical/Context 依赖 recon 的角色模型（§7）/ 多步工作流（§8.3），GitNexus 完全不产这些语义。故 pre-recon / recon / merge_sink_reports 重新移出 `SHANNON_LLM_TRACK_ENABLED` 门控（始终跑），开关收窄为「只关 inj/xss/ssrf vuln agent」（`DEGRADABLE_VULN_CLASSES`）；authz/auth vuln agent 也保留。下文「关掉 pre-recon/recon」的 task 均已过时，保留作历史记录。详见 plan smooth-wandering-dolphin + spec 顶部标注。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `SHANNON_LLM_TRACK_ENABLED=0` 除关 vuln agent 外,也关掉 pre-recon / recon 两个纯 LLM 侦察 agent,使「关 LLM 轨靠 GitNexus 确定性轨兜底」名副其实。

**Architecture:** 在 `workflows.py` 用现有 `input.enable_llm_track` 把 pre-recon 的 `asyncio.gather(code_index, PRE_RECON)` 拆成 `if/else`(开轨 gather、关轨只跑 `code_index` + 打 info),把 recon 的 `run_agent` 整段包进 gate(关轨只打 info)。零新开关、零新抽象,复用 `is_llm_track_enabled()`。不变量用**源码级 AST 测试**锁定(对齐 `pipeline/test_workflows_safety.py` 既有模式,无需 Temporal)。

**Tech Stack:** Python 3.12、temporalio、pytest、`ast`(标准库,源码级断言)。

## Global Constraints

- **零新 env 开关**:复用 `SHANNON_LLM_TRACK_ENABLED`(`shannon_core.config.concurrency.is_llm_track_enabled`,默认 `True`,`"0"/"false"/"no"/"off"` → `False`)。语义向后兼容扩展:`=0` 从「只关 vuln」扩为「关 pre-recon + recon + vuln」。
- **3 个 GitNexus judge 绝不受 `enable_llm_track` 控制**:`run_auth_gitnexus_judge` / `run_authz_gitnexus_judge` / `run_gitnexus_chain_verdict` 是确定性轨,误关塌双轨(防回退测试必须锁)。
- **仅 whitebox**:blackbox 无确定性轨兜底,不动。
- **scope 限定**:`run_code_index`、`entry_point_fusion`、3 个 judge、`run_merge_dual_track_queues`、`run_attack_chain_llm_agent`、`report` 全部保持运行(不改)。
- **resume 语义**:关轨 skip 时不把 `PRE_RECON`/`RECON` 标进 `completed_agents`(开轨重跑/resume 会补跑)。
- **测试纪律**:`feat/fork-py` 全套 pytest 有预存挂起/失败(test_integration / test_worker_progress 等挂起),**只跑本计划新建的测试文件**,绝不广跑全套。
- **commit 纪律**:每 task 末尾 commit 到 `feat/fork-py`(当前分支,非 default,直接 commit)。

## File Structure

- **Create** `packages/whitebox/tests/pipeline/test_workflows_llm_track_gating.py` — 源码级 AST 不变量测试:gate 覆盖矩阵 + resume 语义 + 防回退。单一职责:断言 `workflows.py` 里 LLM gate 的编排不变量。
- **Modify** `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` — pre-recon gather 拆 if/else(:155-171 区域)、recon 包 gate(:225-252 区域)。两处改动,各自独立测试周期。

`workflows.py` 是大文件,但本次只动 pre-recon / recon 两段,不重构整体(遵循「existing codebases 不 unilateral 重构」)。

---

### Task 1: pre-recon LLM agent 纳入 gate(+ 测试基础设施)

**Files:**
- Create: `packages/whitebox/tests/pipeline/test_workflows_llm_track_gating.py`
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`(pre-recon gather 块,约 :155-171)

**Interfaces:**
- Consumes: `input.enable_llm_track`(已存在于 `PipelineInput`,`pipeline/shared.py:20`);`activities.run_code_index` / `activities.run_agent` / `activities.log_info_activity`(均已存在);`retry_for("code-index"|"standard"|"log")`(已存在)。
- Produces: 测试 helper `_llm_track_branch_activity_calls` / `_completed_appends_by_branch`(后续 task 复用)。

- [ ] **Step 1: 写失败测试 + AST helper(完整文件)**

创建 `packages/whitebox/tests/pipeline/test_workflows_llm_track_gating.py`,完整内容:

```python
# packages/whitebox/tests/pipeline/test_workflows_llm_track_gating.py
"""LLM-track gating 不变量: 源码级 AST 断言, 无需起 Temporal.

对齐 pipeline/test_workflows_safety.py 的 source-level 模式。断言
workflows.py 里 `if input.enable_llm_track:` 的编排:
  - PRE_RECON / RECON / vuln agent 只在 gate body(开轨)调度
  - run_code_index 在 body + else 两分支都调度(无条件兜底)
  - 3 个 GitNexus judge + merge 完全在 gate 外(误关塌双轨)
  - completed_agents.append(PRE_RECON/RECON) 只在 body(关轨不标 completed → resume 语义)
  - 关轨 else 分支打 log_info_activity 提示 skip

spec: docs/superpowers/specs/2026-07-09-recon-llm-track-gating-design.md
"""
import ast
import inspect

from shannon_whitebox.pipeline import workflows


def _src() -> str:
    return inspect.getsource(workflows)


def _is_llm_track_test(test: ast.expr) -> bool:
    """True for `input.enable_llm_track` (ast.Attribute with attr enable_llm_track)."""
    return isinstance(test, ast.Attribute) and test.attr == "enable_llm_track"


def _execute_activity_call(node: ast.AST):
    """Return the Call node if it is `workflow.execute_activity(...)`, else None."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
            and node.func.attr == "execute_activity":
        return node
    return None


def _literal_agent_name(node: ast.AST) -> str | None:
    """Extract agent marker from `AgentName.PRE_RECON.value` -> 'PRE_RECON',
    or from a string literal. None if not statically extractable."""
    if isinstance(node, ast.Attribute) and node.attr == "value":
        inner = node.value
        if isinstance(inner, ast.Attribute) \
                and isinstance(inner.value, ast.Name) \
                and inner.value.id == "AgentName":
            return inner.attr
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _destructure_activity_call(call: ast.Call):
    """Return (activity_attr|None, agent_marker|None) for an execute_activity call."""
    attr = None
    if call.args and isinstance(call.args[0], ast.Attribute) \
            and isinstance(call.args[0].value, ast.Name) \
            and call.args[0].value.id == "activities":
        attr = call.args[0].attr
    agent = None
    for a in list(call.args) + [kw.value for kw in call.keywords]:
        if isinstance(a, ast.Call) and isinstance(a.func, ast.Name) \
                and a.func.id == "ActivityInput":
            for kw in a.keywords:
                if kw.arg == "agent_name":
                    agent = _literal_agent_name(kw.value)
    return attr, agent


def _llm_track_branch_activity_calls(src: str) -> list[tuple[str, str | None, str]]:
    """For EVERY `if input.enable_llm_track:` block, return
    [(activity_attr, agent_marker|None, branch)] for each
    workflow.execute_activity(activities.<attr>, ...) call, branch in {'body','else'}.
    Multiple such `if` blocks (pre-recon / merge_sink_reports / vuln) are all scanned.
    """
    tree = ast.parse(src)
    out: list[tuple[str, str | None, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _is_llm_track_test(node.test):
            for branch, stmts in (("body", node.body), ("else", node.orelse)):
                for stmt in stmts:
                    for sub in ast.walk(stmt):
                        call = _execute_activity_call(sub)
                        if call:
                            attr, agent = _destructure_activity_call(call)
                            out.append((attr, agent, branch))
    return out


def _completed_appends_by_branch(src: str) -> list[tuple[str | None, str]]:
    """For every `if input.enable_llm_track:` block, return [(agent_marker, branch)]
    for each `self._state.completed_agents.append(...)` call."""
    tree = ast.parse(src)
    out: list[tuple[str | None, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _is_llm_track_test(node.test):
            for branch, stmts in (("body", node.body), ("else", node.orelse)):
                for stmt in stmts:
                    for sub in ast.walk(stmt):
                        if (isinstance(sub, ast.Call)
                                and isinstance(sub.func, ast.Attribute)
                                and sub.func.attr == "append"
                                and isinstance(sub.func.value, ast.Attribute)
                                and sub.func.value.attr == "completed_agents"):
                            marker = _literal_agent_name(sub.args[0]) if sub.args else None
                            out.append((marker, branch))
    return out


# --- pre-recon / recon / vuln agent: 只在开轨(body)调度 ---

def test_pre_recon_agent_only_in_llm_on_body():
    calls = _llm_track_branch_activity_calls(_src())
    branches = {b for attr, agent, b in calls
                if attr == "run_agent" and agent == "PRE_RECON"}
    assert branches == {"body"}, (
        "PRE_RECON agent 必须只在 enable_llm_track body(开轨)调度, "
        f"实际出现在分支: {branches}")


def test_code_index_runs_in_both_branches():
    """code_index 是 GitNexus 确定性兜底根基, 必须开轨/关轨都跑."""
    calls = _llm_track_branch_activity_calls(_src())
    branches = {b for attr, _, b in calls if attr == "run_code_index"}
    assert branches == {"body", "else"}, (
        "run_code_index 必须在 body+else 两分支都调度(无条件兜底), "
        f"实际: {branches}")


def test_pre_recon_completion_only_in_body():
    """resume 语义: 关轨 skip 时不标 completed, 开轨重跑会补."""
    appends = _completed_appends_by_branch(_src())
    branches = {b for m, b in appends if m == "PRE_RECON"}
    assert branches == {"body"}, (
        "completed_agents.append(PRE_RECON) 必须只在 body(关轨不标 completed), "
        f"实际: {branches}")


def test_pre_recon_skip_logs_info():
    """关轨 else 分支必须打 log_info_activity 提示 pre-recon skipped."""
    calls = _llm_track_branch_activity_calls(_src())
    else_activities = {attr for attr, _, b in calls if b == "else"}
    assert "log_info_activity" in else_activities, (
        "关轨(else)分支必须含 log_info_activity 提示 pre-recon skipped")
```

- [ ] **Step 2: 跑测试确认失败(当前 pre-recon 未 gated)**

Run: `uv run pytest packages/whitebox/tests/pipeline/test_workflows_llm_track_gating.py -v`
Expected: `test_pre_recon_agent_only_in_llm_on_body` / `test_code_index_runs_in_both_branches` / `test_pre_recon_completion_only_in_body` / `test_pre_recon_skip_logs_info` 全部 **FAIL**(当前 gather 在 gate 外,`branches` 为空集或不符)。

- [ ] **Step 3: 改 workflows.py — pre-recon gather 拆 if/else**

在 `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`,找到 pre-recon 的 gather 块(当前形如下方 old),用 Edit 精确替换。

**old_string(精确匹配当前 :155-171):**
```python
                # Fail-fast: if either fails, cancel the other and propagate.
                code_index_result, pre_recon_metrics = await asyncio.gather(
                    workflow.execute_activity(
                        activities.run_code_index, act_input,
                        start_to_close_timeout=timedelta(minutes=10),
                        retry_policy=retry_for("code-index"),
                    ),
                    workflow.execute_activity(
                        activities.run_agent,
                        ActivityInput(**{**act_input.__dict__, "agent_name": AgentName.PRE_RECON.value}),
                        start_to_close_timeout=timedelta(hours=2),
                        retry_policy=retry_for("standard"),
                    ),
                )

                self._state.code_index_stats = code_index_result
                self._state.completed_agents.append(AgentName.PRE_RECON.value)
                self._state.agent_metrics[AgentName.PRE_RECON.value] = pre_recon_metrics
```

**new_string:**
```python
                if input.enable_llm_track:
                    # Fail-fast: if either fails, cancel the other and propagate.
                    code_index_result, pre_recon_metrics = await asyncio.gather(
                        workflow.execute_activity(
                            activities.run_code_index, act_input,
                            start_to_close_timeout=timedelta(minutes=10),
                            retry_policy=retry_for("code-index"),
                        ),
                        workflow.execute_activity(
                            activities.run_agent,
                            ActivityInput(**{**act_input.__dict__, "agent_name": AgentName.PRE_RECON.value}),
                            start_to_close_timeout=timedelta(hours=2),
                            retry_policy=retry_for("standard"),
                        ),
                    )
                    self._state.completed_agents.append(AgentName.PRE_RECON.value)
                    self._state.agent_metrics[AgentName.PRE_RECON.value] = pre_recon_metrics
                else:
                    # LLM 轨关闭: pre-recon LLM agent 跳过, 只跑 code_index (GitNexus 确定性兜底).
                    # 不 append PRE_RECON (resume 语义: 开轨重跑会补); entry_point_fusion 内部
                    # 靠 deliverable 存在性 skip LLM 源 (G6 解耦), 故 pre_recon_deliverable.md 缺失安全.
                    code_index_result = await workflow.execute_activity(
                        activities.run_code_index, act_input,
                        start_to_close_timeout=timedelta(minutes=10),
                        retry_policy=retry_for("code-index"),
                    )
                    await workflow.execute_activity(
                        activities.log_info_activity,
                        ActivityInput(**{**act_input.__dict__,
                           "info_message": "llm_track=disabled (SHANNON_LLM_TRACK_ENABLED=0): pre-recon LLM agent skipped; code_index (GitNexus) still runs; entry points degrade to deterministic schema source only",
                           "info_level": "info"}),
                        start_to_close_timeout=timedelta(seconds=10),
                        retry_policy=retry_for("log"),
                    )

                self._state.code_index_stats = code_index_result
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest packages/whitebox/tests/pipeline/test_workflows_llm_track_gating.py -v`
Expected: 4 个测试全 **PASS**。

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/tests/pipeline/test_workflows_llm_track_gating.py packages/whitebox/src/shannon_whitebox/pipeline/workflows.py
git commit -m "feat(whitebox): pre-recon LLM agent 纳入 SHANNON_LLM_TRACK_ENABLED gate

关轨时 pre-recon LLM agent 跳过, 只跑 code_index (GitNexus 确定性兜底) +
打 info; 不 append PRE_RECON (resume 语义)。新增源码级 AST 不变量测试
(test_workflows_llm_track_gating.py) 锁 gate 覆盖。spec 2026-07-09 Task 1。"
```

---

### Task 2: recon LLM agent 纳入 gate

**Files:**
- Modify: `packages/whitebox/tests/pipeline/test_workflows_llm_track_gating.py`(追加 recon 测试)
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`(recon 块,约 :225-252)

**Interfaces:**
- Consumes: Task 1 的 helper(同文件);`activities.run_agent` / `log_phase_start_activity` / `log_phase_complete_activity` / `log_info_activity`(均已存在)。

- [ ] **Step 1: 追加失败测试(recon)**

在 `test_workflows_llm_track_gating.py` 末尾追加:

```python
# --- recon agent: 只在开轨(body)调度 ---

def test_recon_agent_only_in_llm_on_body():
    calls = _llm_track_branch_activity_calls(_src())
    branches = {b for attr, agent, b in calls
                if attr == "run_agent" and agent == "RECON"}
    assert branches == {"body"}, (
        "RECON agent 必须只在 enable_llm_track body(开轨)调度, "
        f"实际出现在分支: {branches}")


def test_recon_completion_only_in_body():
    """resume 语义: 关轨 skip 时不标 RECON completed."""
    appends = _completed_appends_by_branch(_src())
    branches = {b for m, b in appends if m == "RECON"}
    assert branches == {"body"}, (
        "completed_agents.append(RECON) 必须只在 body(关轨不标 completed), "
        f"实际: {branches}")


def test_recon_skip_logs_info():
    """关轨 else 分支必须打 log_info_activity 提示 recon skipped."""
    calls = _llm_track_branch_activity_calls(_src())
    else_activities = {attr for attr, _, b in calls if b == "else"}
    # pre-recon(Task1) 与 recon(Task2) 的 else 各打一条 log_info_activity
    else_log_count = sum(1 for attr, _, b in calls
                         if attr == "log_info_activity" and b == "else")
    assert else_log_count >= 2, (
        "关轨(else)分支应有 >=2 条 log_info_activity(pre-recon + recon skip 提示), "
        f"实际: {else_log_count}")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/whitebox/tests/pipeline/test_workflows_llm_track_gating.py -v`
Expected: 新增 3 个 recon 测试 **FAIL**(`test_recon_agent_only_in_llm_on_body` 等,当前 recon 在 gate 外);Task 1 的 4 个测试仍 PASS。

- [ ] **Step 3: 改 workflows.py — recon 包 gate**

找到 recon 块(当前形如下方 old),用 Edit 精确替换。

**old_string(精确匹配当前 :225-252):**
```python
            if AgentName.RECON.value not in self._state.completed_agents:
                await workflow.execute_activity(
                    activities.log_phase_start_activity,
                    args=[
                        ActivityInput(**{**act_input.__dict__, "phase": "recon"}),
                        list(step_names("recon")),
                        list(step_intents("recon")),
                    ],
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=retry_for("log"),
                )
                self._state.current_phase = "recon"
                self._state.current_agent = AgentName.RECON.value
                metrics = await workflow.execute_activity(
                    activities.run_agent,
                    ActivityInput(**{**act_input.__dict__, "agent_name": AgentName.RECON.value}),
                    start_to_close_timeout=timedelta(hours=2),
                    retry_policy=retry_for("standard"),
                )
                self._state.completed_agents.append(AgentName.RECON.value)
                self._state.agent_metrics[AgentName.RECON.value] = metrics
                self._state.current_agent = None
                await workflow.execute_activity(
                    activities.log_phase_complete_activity,
                    ActivityInput(**{**act_input.__dict__, "phase": "recon"}),
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=retry_for("log"),
                )
```

**new_string:**
```python
            if AgentName.RECON.value not in self._state.completed_agents:
                if input.enable_llm_track:
                    await workflow.execute_activity(
                        activities.log_phase_start_activity,
                        args=[
                            ActivityInput(**{**act_input.__dict__, "phase": "recon"}),
                            list(step_names("recon")),
                            list(step_intents("recon")),
                        ],
                        start_to_close_timeout=timedelta(seconds=10),
                        retry_policy=retry_for("log"),
                    )
                    self._state.current_phase = "recon"
                    self._state.current_agent = AgentName.RECON.value
                    metrics = await workflow.execute_activity(
                        activities.run_agent,
                        ActivityInput(**{**act_input.__dict__, "agent_name": AgentName.RECON.value}),
                        start_to_close_timeout=timedelta(hours=2),
                        retry_policy=retry_for("standard"),
                    )
                    self._state.completed_agents.append(AgentName.RECON.value)
                    self._state.agent_metrics[AgentName.RECON.value] = metrics
                    self._state.current_agent = None
                    await workflow.execute_activity(
                        activities.log_phase_complete_activity,
                        ActivityInput(**{**act_input.__dict__, "phase": "recon"}),
                        start_to_close_timeout=timedelta(seconds=10),
                        retry_policy=retry_for("log"),
                    )
                else:
                    # LLM 轨关闭: recon LLM agent 跳过. 不进 recon phase, 不 append RECON
                    # (resume 语义). GitNexus 轨不依赖 recon_deliverable.md (chain_verdict
                    # 只吃 parameter_graph.json), 故缺失安全; 下游 vuln/attack_chain/PoC 靠
                    # exists() 守卫降级 (spec §1.3 零硬依赖崩).
                    await workflow.execute_activity(
                        activities.log_info_activity,
                        ActivityInput(**{**act_input.__dict__,
                           "info_message": "llm_track=disabled (SHANNON_LLM_TRACK_ENABLED=0): recon LLM agent skipped; GitNexus track continues independently",
                           "info_level": "info"}),
                        start_to_close_timeout=timedelta(seconds=10),
                        retry_policy=retry_for("log"),
                    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest packages/whitebox/tests/pipeline/test_workflows_llm_track_gating.py -v`
Expected: 全部 7 个测试 **PASS**。

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/tests/pipeline/test_workflows_llm_track_gating.py packages/whitebox/src/shannon_whitebox/pipeline/workflows.py
git commit -m "feat(whitebox): recon LLM agent 纳入 SHANNON_LLM_TRACK_ENABLED gate

关轨时 recon LLM agent 跳过 + 打 info; 不进 recon phase, 不 append RECON
(resume 语义)。AST 不变量测试覆盖 recon。spec 2026-07-09 Task 2。"
```

---

### Task 3: GitNexus 轨防回退不变量(vuln in body / judges+merge outside gate)

**Files:**
- Modify: `packages/whitebox/tests/pipeline/test_workflows_llm_track_gating.py`(追加防回退测试)

**Interfaces:**
- Consumes: Task 1 helper;断言对象为既有 activity(`run_vuln_agent` / `run_auth_gitnexus_judge` / `run_authz_gitnexus_judge` / `run_gitnexus_chain_verdict` / `run_merge_dual_track_queues`,均已在 workflow 中且本次不改)。

**说明:** 这些测试在 Task 1/2 改动后应直接 PASS(防回退性质,锁死"GitNexus 轨绝不被 gate"这一塌双轨红线)。先写、跑、确认 PASS,再 commit。

- [ ] **Step 1: 追加防回退测试**

在 `test_workflows_llm_track_gating.py` 末尾追加:

```python
# --- GitNexus 轨防回退: 误关塌双轨 ---

def test_vuln_agent_only_in_llm_on_body():
    """vuln agent (run_vuln_agent) 维持现状: 只在开轨 body (gate :345)."""
    calls = _llm_track_branch_activity_calls(_src())
    branches = {b for attr, _, b in calls if attr == "run_vuln_agent"}
    assert branches == {"body"}, (
        f"run_vuln_agent 应只在 body, 实际: {branches}")


def test_gitnexus_judges_outside_llm_gate():
    """3 个 GitNexus judge 是确定性轨, 绝不能被 enable_llm_track gate(误关塌双轨)."""
    calls = _llm_track_branch_activity_calls(_src())
    gated = {attr for attr, _, b in calls}
    for judge in ("run_auth_gitnexus_judge",
                  "run_authz_gitnexus_judge",
                  "run_gitnexus_chain_verdict"):
        assert judge not in gated, (
            f"{judge} 是 GitNexus 确定性轨, 不得在 enable_llm_track gate 内(会塌双轨)")


def test_merge_dual_track_outside_llm_gate():
    """merge 是纯合并(容忍空轨), 不受 gate 控制."""
    calls = _llm_track_branch_activity_calls(_src())
    gated = {attr for attr, _, b in calls}
    assert "run_merge_dual_track_queues" not in gated, (
        "run_merge_dual_track_queues 不得在 enable_llm_track gate 内")
```

- [ ] **Step 2: 跑测试确认通过(防回退)**

Run: `uv run pytest packages/whitebox/tests/pipeline/test_workflows_llm_track_gating.py -v`
Expected: 全部 10 个测试 **PASS**。若 `test_gitnexus_judges_outside_llm_gate` FAIL,说明有 judge 被误塞进 gate —— 必须修(回退该改动),不得放过。

- [ ] **Step 3: 跑相邻 safety 测试确认无回归**

Run: `uv run pytest packages/whitebox/tests/pipeline/test_workflows_safety.py packages/whitebox/tests/test_workflows.py -v`
Expected: 全 PASS(既有 source-level 不变量不被本次改动破坏;若某个 timeout/retry 断言因 pre-recon 缩进变化误判,核查是否真回归)。

- [ ] **Step 4: Commit**

```bash
git add packages/whitebox/tests/pipeline/test_workflows_llm_track_gating.py
git commit -m "test(whitebox): GitNexus 轨不受 LLM gate 的防回退不变量

锁死 run_vuln_agent 在 gate body、3 个 GitNexus judge + merge 在 gate 外
(误关 judge 塌双轨)。spec 2026-07-09 Task 3。"
```

---

### Task 4: 真机冒烟(手动)

**Files:** 无代码改动。

**说明:** 源码级测试只验证结构不验证运行时;spec §8 要求真机核实。此 task 为手动 checklist,不写代码、不 commit(或仅 commit 一个冒烟记录文档,可选)。

- [ ] **Step 1: 确认 .env 开关**

确认 `/root/shannon-py/.env` 含 `SHANNON_LLM_TRACK_ENABLED=0`(已存在)。顺带修正第 27 行误导注释「开启LLM轨」→「LLM 轨开关(侦察 pre-recon/recon + vuln);0=关靠 GitNexus 兜底」。

- [ ] **Step 2: 起一次 whitebox 扫描**

Run(用户在终端):
```
uv run shannon-whitebox start -r /root/shannon-py/repos/backend/kol_mapping_service/
```

- [ ] **Step 3: 核实侦察层确实跳过**

观察 `shannon-whitebox logs <workflow-id> --follow` 或 `<workspace>/workflow.log`:
- 出现 `llm_track=disabled ...: pre-recon LLM agent skipped` 与 `recon LLM agent skipped` 两条 info。
- `agents/` 目录**不**出现 `*_pre-recon_*.log` / `*_recon_*.log`(侦察 LLM agent 没跑)。
- `agents/` 也不出现 `*_-vuln_*.log`(vuln agent 没跑)。

- [ ] **Step 4: 核实 GitNexus 轨仍产出**

观察:
- `<workspace>/deliverables/` 出现 `parameter_graph.json` / `code_index.json`(code_index 跑了)。
- 出现 `<vuln>_gitnexus_queue.json`(GitNexus 轨 chain_verdict 产出,3 judge 仍跑)。
- 最终仍生成报告(GitNexus queue 兜底)。

- [ ] **Step 5: 核实 token 收益**

对比开关前后 `session.json` 的 `total_input_tokens` / `total_output_tokens`:关轨后应大幅下降(pre-recon 主+6 子 agent + recon + vuln 全省)。

- [ ] **Step 6: 记录冒烟结果**

把冒烟结论(通过/问题)记入对应 memory(`web-...` 或新建 `recon-llm-track-gating-status.md`),更新「待真机冒烟」状态。

---

## Self-Review(plan 作者自查,已执行)

**1. Spec coverage:**
- §4.1 覆盖矩阵 → Task 1/2/3 测试断言 PRE_RECON/RECON/vuln in body、code_index in both、judges/merge outside。
- §4.2 pre-recon gather 拆 if/else → Task 1 Step 3(完整 old/new 代码)。
- §4.3 recon 包 gate → Task 2 Step 3。
- §4.4 resume 语义(skip 不标 completed)→ Task 1/2 的 `test_*_completion_only_in_body`。
- §4.6 可观测性(info_message)→ Task 1/2 的 `test_*_skip_logs_info`。
- §6 测试(含"3 judge 不受影响"防回退)→ Task 3。
- §8 真机冒烟 → Task 4。
- §3 scope(仅 whitebox)→ Global Constraints + 不涉及 blackbox 文件。
- 全覆盖,无遗漏。

**2. Placeholder scan:** 无 TBD/TODO;每步含完整测试代码 + 完整 old/new 代码块 + 精确 pytest 命令与期望。

**3. Type consistency:** helper 名 `_llm_track_branch_activity_calls` / `_completed_appends_by_branch` / `_src` 在 Task 1 定义、Task 2/3 复用,签名一致;`AgentName.PRE_RECON`/`RECON` 枚举名与 `_literal_agent_name` 返回的 marker("PRE_RECON"/"RECON")一致;activity 名(`run_agent`/`run_code_index`/`run_vuln_agent`/`run_auth_gitnexus_judge` 等)与 `workflows.py` 实际符号一致。

**4. 已知风险(execution 时留意):**
- pre-recon `old_string` 必须与当前文件**逐字**一致(含注释 `# Fail-fast: ...`)。若 Edit 报不匹配,先 Read 该段核对再替换。
- AST helper 依赖 `workflow.execute_activity` 与 `activities.<attr>` 的写法;若 workflow 用了别名调用,helper 需相应放宽(当前 grep 确认全是 `workflow.execute_activity(activities.X, ...)`)。
- `test_report_*` 未纳入(report 不动,spec 已确认);若后续要锁 report 在 gate 外,另加测试。
