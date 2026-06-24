# framework-analyzer 接通实现计划（Plan 2）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把已生成的 `framework_analysis.json`（确定性 finale-rest/epilogue 自动端点）接通到 recon §4.2，让 framework origin 从"LLM 自填"变成"确定性下限 + LLM 独立补全"。

**Architecture:** 新写 `render_framework_endpoints`（确定性 markdown）→ `run_agent` 仅对 RECON agent 注入 `prompt_variables={"framework_endpoints_summary": md}` → `recon.txt` §4.2 用 `{{FRAMEWORK_ENDPOINTS_SUMMARY}}` 占位符填充。`framework_analysis.json` 已在 recon 之前落盘（workflows L188-200 < L228），时序无需调。

**Tech Stack:** Python 3.12, pytest, pytest-asyncio

## Global Constraints

- **仅 RECON agent 注入**（`run_agent` 是通用入口，recon/vuln 都走）；非 RECON 不注入（避免污染 vuln prompt）
- `framework_analysis.json` 不存在时**跳过**（不崩；framework_analysis 失败时 recon 仍能跑）
- **recon-static 不加占位符**（它无 §4.2）；注入对 recon-static 是 no-op（`variables` 多余键不报错，manager L154-157 `if token in result`）
- framework origin 是确定性**事实**，作 recon §4.2 的"下限"（recon LLM 仍须独立检查其他端点，符合双轨"下限非上限"）
- **authz 的 framework 接通（GitNexus 轨独立判 IDOR）不在本 plan**，留 Phase 1 authz plan
- TDD + frequent commits（`feat(services):` / `feat(whitebox):` / `feat(prompt):`）
- 复用 `activities.py` 现有 `_to_endpoint`（dict → `InferredEndpoint`，见 :646-659）

---

### Task 1: 写 `render_framework_endpoints` renderer

**Files:**
- Create: `packages/core/src/shannon_core/services/framework_endpoint_renderer.py`
- Test: `packages/core/tests/services/test_framework_endpoint_renderer.py`（Create）

**Interfaces:**
- Consumes: `InferredEndpoint`（`services/framework_analyzer.py:44-53`：`method/path/source/model/middleware/vulnerability_indicators`）
- Produces: `render_framework_endpoints(endpoints: list[InferredEndpoint]) -> str`（markdown，含"下限非上限"声明）

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/services/test_framework_endpoint_renderer.py
from shannon_core.services.framework_analyzer import InferredEndpoint
from shannon_core.services.framework_endpoint_renderer import render_framework_endpoints


def _ep(**overrides):
    base = dict(method="DELETE", path="/api/Feedbacks/:id", source="framework-auto-generated",
                model="Feedback", middleware=("isAuthenticated",),
                vulnerability_indicators=("no-ownership-check",))
    base.update(overrides)
    return InferredEndpoint(**base)


def test_render_empty_endpoints():
    out = render_framework_endpoints([])
    assert "无" in out or "no" in out.lower()


def test_render_lists_endpoints_with_origin():
    out = render_framework_endpoints([_ep()])
    assert "DELETE /api/Feedbacks/:id" in out
    assert "framework-auto-generated" in out
    assert "Feedback" in out


def test_render_includes_lower_bound_disclaimer():
    out = render_framework_endpoints([_ep()])
    # 下限非上限：recon LLM 仍须独立检查其他端点
    assert "下限" in out or "独立" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/services/test_framework_endpoint_renderer.py -v`
Expected: FAIL — `ModuleNotFoundError: shannon_core.services.framework_endpoint_renderer`

- [ ] **Step 3: Implement the renderer**

```python
# packages/core/src/shannon_core/services/framework_endpoint_renderer.py
"""Render framework-inferred endpoints (finale-rest/epilogue) as markdown
for injection into recon §4.2 via {{FRAMEWORK_ENDPOINTS_SUMMARY}}.

These are deterministic facts (framework-analyzer output), surfaced to the
recon LLM as a LOWER BOUND for §4.2 Framework Origin — the LLM must still
independently check other endpoints (dual-track: lower bound, not ceiling).
"""

from shannon_core.services.framework_analyzer import InferredEndpoint


def render_framework_endpoints(endpoints: list[InferredEndpoint]) -> str:
    """Render framework-inferred endpoints as markdown for recon §4.2."""
    if not endpoints:
        return "（无确定性检测到的框架自动生成端点。）"

    lines = [
        "## Framework Endpoints（确定性检测：finale-rest/epilogue 自动生成）",
        "",
        "| Method | Path | Framework Origin | Model | Middleware | Vulnerability Indicators |",
        "|---|---|---|---|---|---|",
    ]
    for ep in endpoints:
        mw = ", ".join(ep.middleware) if ep.middleware else "—"
        ind = ", ".join(ep.vulnerability_indicators) if ep.vulnerability_indicators else "—"
        model = ep.model or "—"
        lines.append(
            f"| `{ep.method}` | `{ep.path}` | {ep.source} | {model} | {mw} | {ind} |"
        )
    lines.extend([
        "",
        "⚠️ 以上为**确定性检测**的框架自动生成端点（framework-analyzer）。"
        "§4.2 的 Framework Origin 据此填充；"
        "**仍须独立检查其他端点的 framework origin（下限非上限，确定性未列出 ≠ 无框架端点）**。",
    ])
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/services/test_framework_endpoint_renderer.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/services/framework_endpoint_renderer.py packages/core/tests/services/test_framework_endpoint_renderer.py
git commit -m "feat(services): add render_framework_endpoints for recon §4.2 injection"
```

---

### Task 2: `run_agent` 对 RECON 注入 framework endpoints summary

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:74-102`（`run_agent`，在 `executor.execute` 调用前注入）
- Test: `packages/whitebox/tests/test_run_agent_framework_injection.py`（Create）

**Interfaces:**
- Consumes: `render_framework_endpoints`（Task 1）、`_to_endpoint`（activities.py:646-659，dict → `InferredEndpoint`）、`executor.execute` 的 `prompt_variables` 通道（executor.py:85-86）
- Produces: RECON agent 跑时收到填充好的 `{{FRAMEWORK_ENDPOINTS_SUMMARY}}`；非 RECON 不受影响；`framework_analysis.json` 缺失时跳过

- [ ] **Step 1: Write the failing test**

```python
# packages/whitebox/tests/test_run_agent_framework_injection.py
import json
from unittest.mock import AsyncMock, patch

import pytest

from shannon_whitebox.pipeline import activities


@pytest.mark.asyncio
async def test_recon_agent_gets_framework_endpoints_summary(tmp_path):
    """RECON agent: framework_analysis.json present → prompt_variables injected."""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    # Minimal framework_analysis.json
    (deliverables / "framework_analysis.json").write_text(json.dumps({
        "detected_framework": None,
        "inferred_endpoints": [
            {"method": "DELETE", "path": "/api/Feedbacks/:id", "source": "framework-auto-generated",
             "model": "Feedback", "middleware": ["isAuthenticated"],
             "vulnerability_indicators": ["no-ownership-check"]},
        ],
        "recommendations": [],
    }))

    captured = {}

    class FakeInput:
        agent_name = "recon"
        web_url = None
        repo_path = str(tmp_path)
        config_path = None
        api_key = None
        pipeline_testing_mode = False
        prompt_override = None

    async def fake_execute(**kwargs):
        captured.update(kwargs)
        return type("M", (), {"to_dict": lambda self: {}})()

    with patch.object(activities, "_get_paths", return_value=(tmp_path, deliverables, tmp_path)):
        with patch.object(activities, "executor") as mock_exec:
            mock_exec.execute = fake_execute
            await activities.run_agent(FakeInput())

    assert captured.get("prompt_variables") is not None
    summary = captured["prompt_variables"].get("framework_endpoints_summary", "")
    assert "DELETE /api/Feedbacks/:id" in summary
    assert "framework-auto-generated" in summary


@pytest.mark.asyncio
async def test_non_recon_agent_not_injected(tmp_path):
    """Non-RECON agent (e.g. vuln) → no framework injection."""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "framework_analysis.json").write_text(json.dumps({"inferred_endpoints": []}))

    captured = {}

    class FakeInput:
        agent_name = "injection"  # non-recon
        web_url = None
        repo_path = str(tmp_path)
        config_path = None
        api_key = None
        pipeline_testing_mode = False
        prompt_override = None

    async def fake_execute(**kwargs):
        captured.update(kwargs)
        return type("M", (), {"to_dict": lambda self: {}})()

    with patch.object(activities, "_get_paths", return_value=(tmp_path, deliverables, tmp_path)):
        with patch.object(activities, "executor") as mock_exec:
            mock_exec.execute = fake_execute
            await activities.run_agent(FakeInput())

    pv = captured.get("prompt_variables")
    assert pv is None or "framework_endpoints_summary" not in (pv or {})


@pytest.mark.asyncio
async def test_recon_agent_without_framework_json_skips(tmp_path):
    """RECON agent + framework_analysis.json missing → no crash, no injection."""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    # no framework_analysis.json

    captured = {}

    class FakeInput:
        agent_name = "recon"
        web_url = None
        repo_path = str(tmp_path)
        config_path = None
        api_key = None
        pipeline_testing_mode = False
        prompt_override = None

    async def fake_execute(**kwargs):
        captured.update(kwargs)
        return type("M", (), {"to_dict": lambda self: {}})()

    with patch.object(activities, "_get_paths", return_value=(tmp_path, deliverables, tmp_path)):
        with patch.object(activities, "executor") as mock_exec:
            mock_exec.execute = fake_execute
            await activities.run_agent(FakeInput())

    assert captured.get("prompt_variables") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_run_agent_framework_injection.py -v`
Expected: FAIL — `prompt_variables` not passed（或 None）

- [ ] **Step 3: Inject prompt_variables for RECON in `run_agent`**

Edit `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` 的 `run_agent`（约 L74-102）。在 `executor.execute(...)` 调用**前**插入注入逻辑，并在 `executor.execute(...)` 调用中加 `prompt_variables=prompt_variables`：

```python
        repo, deliverables, _ = _get_paths(input)

        # Dual-track lower bound: inject deterministic framework endpoints into
        # recon §4.2 Framework Origin (recon LLM still independently checks others).
        prompt_variables = None
        if input.agent_name == "recon":
            fa_path = deliverables / "framework_analysis.json"
            if fa_path.exists():
                import json
                from shannon_core.services.framework_analyzer import InferredEndpoint  # noqa: F401
                from shannon_core.services.framework_endpoint_renderer import render_framework_endpoints

                data = json.loads(fa_path.read_text())
                endpoints = [_to_endpoint(ep) for ep in data.get("inferred_endpoints", [])]
                prompt_variables = {
                    "framework_endpoints_summary": render_framework_endpoints(endpoints)
                }

        metrics = await executor.execute(
            agent_name=input.agent_name,
            repo_path=str(repo),
            web_url=input.web_url,
            deliverables_path=str(deliverables),
            config_path=input.config_path,
            api_key=input.api_key,
            pipeline_testing=input.pipeline_testing_mode,
            prompt_override=input.prompt_override,
            prompt_variables=prompt_variables,
            tool_audit_logger=tool_audit_logger,
        )
```

> 注：`_to_endpoint` 复用 activities.py 现有函数（:646-659，dict → `InferredEndpoint`）。若其签名/位置不同，先 `grep "def _to_endpoint" packages/whitebox/src` 确认后调整。`executor.execute` 的 `prompt_variables` 参数已由 executor.py:85-86 支持。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_run_agent_framework_injection.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/tests/test_run_agent_framework_injection.py
git commit -m "feat(whitebox): inject framework endpoints summary into recon agent"
```

---

### Task 3: recon.txt §4.2 接入占位符

**Files:**
- Modify: `/root/shannon-py/prompts/recon.txt`（§4.2 区，约 :291-309）
- Test: 集成验证（手动跑一次 recon，确认 prompt 含渲染内容）—— 见 Step 4

**Interfaces:**
- Consumes: `{{FRAMEWORK_ENDPOINTS_SUMMARY}}`（Task 2 注入的 `framework_endpoints_summary`）
- Produces: recon §4.2 的 Framework Origin 从确定性 summary 填充（下限），LLM 独立补全其他端点

- [ ] **Step 1: Insert the placeholder + directive in recon.txt §4.2**

Edit `/root/shannon-py/prompts/recon.txt`。在 `## 4.2 Endpoint Security Context` 标题之后（:291 附近）、示例表格之前，插入：

```markdown
## 4.2 Endpoint Security Context

<framework_endpoints_deterministic>
{{FRAMEWORK_ENDPOINTS_SUMMARY}}

**填充规则**：上表为 framework-analyzer 确定性检测的框架自动生成端点。§4.2 表中这些端点的 **Framework Origin** 列据此填充（finale-rest/epilogue auto-generated）；其 Auth/Middleware/Ownership Check 仍须你独立核实。
**下限非上限**：确定性未列出的端点不代表无框架端点——仍须独立检查路由定义中的框架使用。
</framework_endpoints_deterministic>
```

（保留 §4.2 既有的表格结构与 "Framework Endpoints Detected" 段落作为对其他端点的指引。）

- [ ] **Step 2: Verify placeholder resolves (no residual warning)**

Run: `cd /root/shannon-py && python -c "
from shannon_core.prompts.manager import PromptManager
pm = PromptManager()
tpl = open('prompts/recon.txt').read()
out = pm._interpolate(tpl, {'framework_endpoints_summary': 'TEST_SUMMARY', 'deliverables_path': '/tmp'}, None, 'recon')
assert 'TEST_SUMMARY' in out
assert '{{FRAMEWORK_ENDPOINTS_SUMMARY}}' not in out
print('OK: placeholder resolves')
"`
Expected: `OK: placeholder resolves`（占位符被替换，无残留）

- [ ] **Step 3: Verify recon-static is unaffected (no placeholder there)**

Run: `cd /root/shannon-py && grep -c "FRAMEWORK_ENDPOINTS_SUMMARY" prompts/recon-static.txt`
Expected: `0`（recon-static 无此占位符，注入对其 no-op）

- [ ] **Step 4: Manual smoke (本 plan 外)**

跑一次白盒扫描，确认 recon prompt 实际收到渲染的 framework endpoints（recon_deliverable.md §4.2 含确定性端点）。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add prompts/recon.txt
git commit -m "feat(prompt): recon §4.2 consumes FRAMEWORK_ENDPOINTS_SUMMARY (deterministic lower bound)"
```

---

## Self-Review

**1. Spec coverage**（对照 spec §5.3 recon §4.2 + §6 framework-analyzer 接通）：
- framework-analyzer 接通到 recon §4.2 → Task 1-3 ✓
- framework origin 确定性下限 → Task 1 renderer + Task 3 prompt 指令 ✓
- 不锚定（下限非上限）→ Task 3 prompt 声明 ✓
- authz framework 接通 → **不在本 plan**（Phase 1 authz，GitNexus 轨独立判 IDOR）
- 通用合并器 / GitNexus 索引降级 → **不在本 plan**（Plan 3/4）

**2. Placeholder scan**：无 TBD；Task 2 注明 `_to_endpoint` 复用现有（如签名不符先 grep 确认）——诚实标注，非占位符。

**3. Type consistency**：`InferredEndpoint` 字段（method/path/source/model/middleware/vulnerability_indicators）在 renderer/注入/测试一致；`render_framework_endpoints` 签名一致；`prompt_variables={"framework_endpoints_summary": ...}` 键名与 `{{FRAMEWORK_ENDPOINTS_SUMMARY}}` 占位符（manager L154-157 upper-case 匹配）一致。

**已知缺口（诚实）**：
- Task 3 的占位符填充是 prompt 层，靠手动冒烟验证（Step 4），单元测试只验证 `_interpolate` 替换（Step 2）。
- 补移植缺口（`_discover_models` 的 `endpoints: [...]` 数组 regex，TS 有 Python 无）**不在本 plan**——对 §4.2 接通非阻塞，后续小补丁。
- recon-static 缺 §4.2（白盒 authz 闭环已知缺口）**不在本 plan**——本 plan 只接通主版 recon.txt。
