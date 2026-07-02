# authz GitNexus 轨深度判定（spec-1a） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 把 authz GitNexus 轨判定从轻量单次升级为吃 IDOR 候选的多轮深度 agent（带 grep/read 自主追 owner 检查），候选空时自主探索，喂给 LLM 的候选补 SourcePoint 参数细节。

**Architecture:** spec-0 已就位 `run_gitnexus_verdict_agent`（多轮入口）+ `GITNEXUS_VERDICT_RETRY`。本 plan：(1) render 补 SourcePoint 参数列（G3）；(2) verdict_agent 补 audit_session（F1，对齐 run_agent 的 SessionToolAuditLogger）；(3) authz_judge candidate_count>0 切多轮判定（G1）；(4) candidate_count==0 改自主探索（G2，新 prompt）；(5) authz_judge retry 切 gitnexus-verdict + 更新 spec-0 的 guardrail。补候选来源（扩框架/OpenAPI/fusion 解耦）是 spec-1b，不在本 plan。

**Tech Stack:** Python / temporalio / pytest / 双引擎

**Spec:** `docs/superpowers/specs/2026-07-02-gitnexus-authz-deep-agent-design.md`（G1/G2/G3 + F1）

## Global Constraints

- **不改候选生成算法**：`find_unguarded_sink_paths` / `find_framework_idor_candidates` 逻辑不变；只改判定深度 + 喂 LLM 的信息 + 候选空处理。
- **不改 LLM 轨 `vuln-authz.txt`**（保留为可选增强，双轨 OR）。
- **不改双轨 merger**：`authz_gitnexus_queue.json` schema 不变。
- **双引擎**：走 `run_claude_prompt` 统一抽象。
- **测试**：`uv run pytest <path> -v`，只跑改动相关（全套 hang）。
- **commit**：conventional commits；`git add` 只 named 文件。
- **双轨铁律**：不喂确定性产物给 LLM 轨 prompt；GitNexus 轨吃自己产的候选是其本职（不改向）。

---

## File Structure

| 文件 | 责任 | 本 plan 改动 |
|---|---|---|
| `packages/core/src/shannon_core/code_index/authz_gitnexus_track.py` | IDOR 候选 + render | T1：render 补 SourcePoint 列 |
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | activity | T2：`run_gitnexus_verdict_agent` 补 audit；T3/T4：`run_authz_gitnexus_judge` 切多轮/探索 |
| `prompts/authz_gitnexus_explore.txt` | 探索 prompt | T4：新建 |
| `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | workflow | T5：authz_judge retry 切 gitnexus-verdict |
| `packages/whitebox/tests/pipeline/test_workflows_safety.py` | guardrail | T5：更新 guardrail 允许 gitnexus-verdict |
| `packages/core/tests/code_index/test_authz_render.py` | render 测试 | T1 测试 |
| `packages/whitebox/tests/pipeline/test_gitnexus_verdict_agent.py` | verdict agent 测试 | T2 测试（扩） |
| `packages/whitebox/tests/pipeline/test_authz_judge_deep.py` | judge 多轮/探索 测试 | T3/T4 测试（新建） |

---

### Task 1: render 补 SourcePoint 参数列（G3）

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/authz_gitnexus_track.py:309-347`（render dominance 表格段）
- Test: `packages/core/tests/code_index/test_authz_render.py`（若不存在新建）

**Interfaces:**
- Consumes：`index.source_points`（`SourcePoint` 列表，已产，含 id/param_name/expression/source_type）；`IDORCandidateChain.source_point_ids`
- Produces：render 的 dominance 表格多一列 "Params"，含命中 SourcePoint 的 `param_name(source_type): expression`

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/code_index/test_authz_render.py
from shannon_core.code_index.authz_gitnexus_track import render_authz_gitnexus_candidates, IDORCandidateChain
from shannon_core.code_index.parameter_models import SourcePoint
from shannon_core.code_index.models import EntryPoint

def _sp(i, name, st, expr):
    return SourcePoint(id=i, entry_point_id="ep1", param_name=name, source_type=st,
                       expression=expr, file_path="a.py", line=1)

def test_render_includes_sourcepoint_params():
    cand = IDORCandidateChain(
        endpoint_id="ep1", handler_id="h1", sink_id="s1", sink_step_idx=1,
        path=("h1", "s1"), guard_nodes_on_path=(), source_point_ids=("ep1::userId::5",),
    )
    sp = _sp("ep1::userId::5", "userId", "path", "req.params.userId")
    # 构造最小 index/entry_points（用最小 mock 或真实 CodeIndex 字段子集）
    class _Blk:
        def __init__(self, src): self.source_code = src
    class _Idx:
        blocks = [_Blk("handler()"), _Blk("db.update()")]
        source_points = [sp]
    ep = EntryPoint(func_block_id="ep1", route="/x/:userId", http_method="GET")
    md = render_authz_gitnexus_candidates([cand], [], index=_Idx, entry_points=[ep])
    assert "userId" in md and "req.params.userId" in md and "path" in md
```

> EntryPoint/SourcePoint 字段以 repo 实际为准（`parameter_models.py` / `models.py`）；若必填字段不符，按实际补全测试 fixture（不要删断言）。

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/code_index/test_authz_render.py::test_render_includes_sourcepoint_params -v`
Expected: FAIL — render 输出无 "userId"/"req.params.userId"

- [ ] **Step 3: Write minimal implementation**

`authz_gitnexus_track.py` render 函数（:327 `blocks_by_id = ...` 后）加 source_points 索引；dominance 表格表头加 "Params" 列、行加 params：

```python
    blocks_by_id = {b.id: b for b in index.blocks}
    source_by_id = {sp.id: sp for sp in index.source_points}   # 新增
    ...
    # dominance 表头（:334）改为：
    lines.append("| Endpoint | Handler | Sink | 调用路径 | Params | Handler 片段 | Sink 片段 |")
    lines.append("|---|---|---|---|---|---|---|")
    for c in dominance_cands:
        label = _endpoint_label(c.endpoint_id, entry_points)
        handler_src = _snippet(blocks_by_id[c.handler_id].source_code, max_snippet) \
            if c.handler_id in blocks_by_id else "—"
        sink_src = _snippet(blocks_by_id[c.sink_id].source_code, max_snippet) \
            if c.sink_id in blocks_by_id else "—"
        path_str = " → ".join(c.path)
        # 新增：Params 列
        sps = [source_by_id[sid] for sid in c.source_point_ids if sid in source_by_id]
        params = "; ".join(f"{sp.param_name}({sp.source_type}): {sp.expression}" for sp in sps) or "—"
        lines.append(
            f"| `{label}` | `{c.handler_id}` | `{c.sink_id}` | `{path_str}` "
            f"| `{params}` | `{handler_src}` | `{sink_src}` |"
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/code_index/test_authz_render.py -v`
Expected: PASS

- [ ] **Step 5: 回归——现有 render 测试不破**

Run: `uv run pytest packages/core/tests/code_index/ -v -k "authz"`
Expected: PASS（现有 authz render/build 测试，表头多一列不破断言——若有断言列数，更新对齐）

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/code_index/authz_gitnexus_track.py packages/core/tests/code_index/test_authz_render.py
git commit -m "feat(authz): render 候选补 SourcePoint 参数列（喂 LLM 判定）"
```

---

### Task 2: `run_gitnexus_verdict_agent` 补 audit_session（F1）

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`（`run_gitnexus_verdict_agent`，spec-0 新增处 ~:869）
- Test: `packages/whitebox/tests/pipeline/test_gitnexus_verdict_agent.py`（扩）

**Interfaces:**
- Consumes：`SessionToolAuditLogger`（`shannon_whitebox.audit.session_tool_audit_logger`）；`audit_session`（`get_audit_session()` 返回）
- Produces：`run_gitnexus_verdict_agent(*, prompt, repo_path, structured_output_schema=None, audit_session=None)`——audit_session 非 None 时构造 logger + initialize + 传 tool_audit_logger + close（对齐 `run_agent:167/183/198`）

- [ ] **Step 1: Write the failing test**

```python
# packages/whitebox/tests/pipeline/test_gitnexus_verdict_agent.py（追加）
import pytest
from unittest.mock import MagicMock, AsyncMock

@pytest.mark.asyncio
async def test_verdict_agent_attaches_tool_audit_logger(monkeypatch):
    """传 audit_session 时构造 SessionToolAuditLogger 并传给 run_claude_prompt。"""
    captured: dict = {}
    async def fake_run(**kwargs):
        captured.update(kwargs)
        return MagicMock(text="ok", success=True, turns=1)
    monkeypatch.setattr("shannon_core.agents.runner.run_claude_prompt", fake_run)

    logger_instance = MagicMock()
    logger_instance.initialize = AsyncMock()
    logger_instance.close = AsyncMock()
    def fake_logger_cls(session, name, attempt):
        assert name == "gitnexus-verdict"
        return logger_instance
    monkeypatch.setattr(
        "shannon_whitebox.pipeline.activities.SessionToolAuditLogger", fake_logger_cls, raising=False)
    # 若 SessionToolAuditLogger 在 verdict_agent 内延迟 import,patch 源模块:
    monkeypatch.setattr(
        "shannon_whitebox.audit.session_tool_audit_logger.SessionToolAuditLogger", fake_logger_cls)

    import shannon_whitebox.pipeline.activities as act
    await act.run_gitnexus_verdict_agent(prompt="p", repo_path="/r", audit_session=MagicMock())

    assert captured.get("tool_audit_logger") is logger_instance
    logger_instance.initialize.assert_awaited_once()
    logger_instance.close.assert_awaited_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/whitebox/tests/pipeline/test_gitnexus_verdict_agent.py::test_verdict_agent_attaches_tool_audit_logger -v`
Expected: FAIL — 现状 audit_session 参数不存在 / tool_audit_logger 未传

- [ ] **Step 3: Write minimal implementation**

`activities.py` `run_gitnexus_verdict_agent` 改为：

```python
async def run_gitnexus_verdict_agent(
    *,
    prompt: str,
    repo_path: str,
    structured_output_schema: dict | None = None,
    audit_session=None,
) -> "ClaudeRunResult":
    """GitNexus 多轮 verdict agent：带 grep/read 自主追链，吃确定性候选做深度判定。

    audit_session 非 None 时构造 SessionToolAuditLogger（对齐 run_agent），多轮工具调用经逐轮审计。
    """
    from shannon_core.agents.runner import run_claude_prompt
    tool_audit_logger = None
    if audit_session is not None:
        from shannon_whitebox.audit.session_tool_audit_logger import SessionToolAuditLogger
        tool_audit_logger = SessionToolAuditLogger(audit_session, "gitnexus-verdict", attempt=1)
        await tool_audit_logger.initialize()
    try:
        return await run_claude_prompt(
            prompt=prompt,
            repo_path=repo_path,
            model_tier="medium",
            max_turns=int(os.getenv("SHANNON_GITNEXUS_VERDICT_MAX_TURNS", "30")),
            structured_output_schema=structured_output_schema,
            tool_audit_logger=tool_audit_logger,
        )
    finally:
        if tool_audit_logger is not None:
            await tool_audit_logger.close(success=True)
```

> `attempt=1` 简化（verdict_agent 非 per-attempt activity 重试模型；若需 attempt 透传，judge 调用时从 `activity.info().attempt` 传入——本 plan 用 1 足够审计标签）。close 的 success：正常返回 True；异常时 finally 里无法知 success，保守传 True（审计 best-effort，对齐 run_agent except 分支才传 False——本函数异常会向上抛，由 caller 处理）。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/whitebox/tests/pipeline/test_gitnexus_verdict_agent.py -v`
Expected: PASS（新测试 + spec-0 两测试仍过——audit_session=None 时 tool_audit_logger=None，行为同前）

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/tests/pipeline/test_gitnexus_verdict_agent.py
git commit -m "feat(whitebox): run_gitnexus_verdict_agent 补 audit_session（SessionToolAuditLogger）"
```

---

### Task 3: authz_judge candidate_count>0 切多轮判定（G1）

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:322-354`（判定段）
- Test: `packages/whitebox/tests/pipeline/test_authz_judge_deep.py`（新建）

**Interfaces:**
- Consumes：`run_gitnexus_verdict_agent`（T2 后含 audit_session）；`render_authz_gitnexus_candidates`（T1 后含 Params 列）
- Produces：candidate_count>0 时经 `run_gitnexus_verdict_agent`（多轮）替代单次 `run_claude_prompt`；产 `authz_gitnexus_queue.json` schema 不变

- [ ] **Step 1: Write the failing test**

```python
# packages/whitebox/tests/pipeline/test_authz_judge_deep.py
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

@pytest.mark.asyncio
async def test_authz_judge_uses_multiturn_verdict_when_candidates(tmp_path, monkeypatch):
    """candidate_count>0 时调 run_gitnexus_verdict_agent（多轮），非单次 run_claude_prompt。"""
    import shannon_whitebox.pipeline.activities as act

    # 准备：code_index.json + framework_analysis.json 造 1 个 dominance 候选
    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    # 写最小 code_index.json 让 build_authz_gitnexus_track 返回 1 候选
    # （用现成 fixture 或构造最小 CodeIndex JSON；若复杂，patch build_authz_gitnexus_track 返回固定候选）
    fake_result = MagicMock()
    fake_result.dominance_candidates = [MagicMock(endpoint_id="ep1", handler_id="h1",
        sink_id="s1", sink_step_idx=1, path=("h1","s1"), guard_nodes_on_path=(),
        source_point_ids=())]
    fake_result.framework_candidates = []
    fake_result.http_route_count = 1
    fake_result.entry_point_total = 1
    fake_result.markdown = "## 候选"
    monkeypatch.setattr(act, "build_authz_gitnexus_track", lambda d: fake_result, raising=False)
    monkeypatch.setattr("shannon_core.code_index.authz_gitnexus_track.build_authz_gitnexus_track",
                        lambda d: fake_result)

    verdict_called = {"n": 0}
    async def fake_verdict(*, prompt, repo_path, structured_output_schema=None, audit_session=None):
        verdict_called["n"] += 1
        r = MagicMock()
        r.structured_output = {"vulnerabilities": []}
        r.text = "{}"
        return r
    monkeypatch.setattr(act, "run_gitnexus_verdict_agent", fake_verdict)

    single_called = {"n": 0}
    async def fake_single(**kw):
        single_called["n"] += 1
        return MagicMock(structured_output={}, text="{}")
    monkeypatch.setattr("shannon_core.agents.runner.run_claude_prompt", fake_single)

    inp = MagicMock()
    inp.workspace_name = "ws"; inp.api_key = None
    # _get_paths 依赖——patch 返回 (repo, deliverables, ...)
    monkeypatch.setattr(act, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))

    await act.run_authz_gitnexus_judge(inp)
    assert verdict_called["n"] == 1, "应用 run_gitnexus_verdict_agent 多轮"
    assert single_called["n"] == 0, "不应再走单次 run_claude_prompt"
```

> fixture 细节（code_index.json 结构、ActivityInput 必填字段）以 repo 实际为准；patch `build_authz_gitnexus_track` 是最稳路径（绕开 JSON 构造）。若 `_get_paths`/session 调用需更多 mock，补上（不要削弱核心断言 verdict_called==1）。

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/whitebox/tests/pipeline/test_authz_judge_deep.py::test_authz_judge_uses_multiturn_verdict_when_candidates -v`
Expected: FAIL — 现状走单次 run_claude_prompt（single_called>0 / verdict_called==0）

- [ ] **Step 3: Write minimal implementation**

`activities.py:322-354` 判定段，把 `result = await run_claude_prompt(...)` 整块替换为 `run_gitnexus_verdict_agent`：

```python
            if candidate_count > 0:
                prompts_dir = Path(__file__).resolve().parents[5] / "prompts"
                prompt_manager = PromptManager(prompts_dir)
                prompt = prompt_manager.load_sync(
                    "authz_gitnexus_judge",
                    variables={"authz_gitnexus_candidates": md},
                )
                # spec-1a G1：切多轮深度判定（吃候选 + grep/read 追 owner 检查）
                result = await run_gitnexus_verdict_agent(
                    prompt=prompt,
                    repo_path=str(repo),
                    structured_output_schema={
                        "type": "object",
                        "properties": {"vulnerabilities": {"type": "array"}},
                    },
                    audit_session=get_audit_session(),
                )
                raw = result.structured_output
                if raw is None and result.text:
                    raw = result.text
                parsed = VulnerabilityQueue.parse_lenient(
                    raw if isinstance(raw, str) else json.dumps(raw) if raw is not None else "{}"
                )
                for v in parsed.queue.vulnerabilities:
                    data = v.model_dump()
                    data["source_track"] = "gitnexus"
                    if not data.get("evidence_chain"):
                        data["evidence_chain"] = "gitnexus track candidate (dominance/framework)"
                    vulnerabilities.append(data)
```

> `api_key=input.api_key` 不再透传（run_gitnexus_verdict_agent 走 run_claude_prompt 的 provider 自取 api_key；若 repo 里 run_claude_prompt 需显式 api_key，给 verdict_agent 加 api_key 参数透传——执行时确认 runner.py 签名）。
>
> **候选分发**（候选量大时分批）：本 plan 先全量塞 prompt（单次多轮判定所有候选，max_turns=30 封顶）。若实测候选>10 超时，spec-1b 或 follow-up 加分批 fan-out（对齐 vuln agent Semaphore 模式）。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/whitebox/tests/pipeline/test_authz_judge_deep.py -v`
Expected: PASS

- [ ] **Step 5: 回归——authz judge 现有测试**

Run: `uv run pytest packages/whitebox/tests/pipeline/ -v -k "authz_judge or authz_gitnexus"`
Expected: PASS（candidate_count==0 路径不变；若有断言"用 run_claude_prompt"的旧测试，更新为 verdict_agent）

- [ ] **Step 6: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/tests/pipeline/test_authz_judge_deep.py
git commit -m "feat(authz): candidate_count>0 切 run_gitnexus_verdict_agent 多轮深度判定"
```

---

### Task 4: candidate_count==0 自主探索（G2）

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:303-311`（0 候选分支）
- Create: `prompts/authz_gitnexus_explore.txt`
- Test: `packages/whitebox/tests/pipeline/test_authz_judge_deep.py`（扩）

**Interfaces:**
- Consumes：`run_gitnexus_verdict_agent`；entry_points/routes 摘要（从 code_index）；新 prompt `authz_gitnexus_explore`
- Produces：candidate_count==0 时不再静默写空 queue——调 verdict_agent 跑探索 prompt，产软候选（`needs_review=True`）

- [ ] **Step 1: Write the failing test**

```python
# test_authz_judge_deep.py 追加
@pytest.mark.asyncio
async def test_authz_judge_explores_when_zero_candidates(tmp_path, monkeypatch):
    """candidate_count==0 时调 verdict_agent 探索（非静默写空 queue）。"""
    import shannon_whitebox.pipeline.activities as act
    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    fake_result = MagicMock()
    fake_result.dominance_candidates = []      # 0 候选
    fake_result.framework_candidates = []
    fake_result.http_route_count = 0
    fake_result.entry_point_total = 0
    fake_result.markdown = ""
    monkeypatch.setattr("shannon_core.code_index.authz_gitnexus_track.build_authz_gitnexus_track",
                        lambda d: fake_result)

    explored = {"n": 0}
    async def fake_verdict(*, prompt, repo_path, structured_output_schema=None, audit_session=None):
        explored["n"] += 1
        assert "explore" in prompt.lower() or "route" in prompt.lower(), "应用探索 prompt"
        r = MagicMock()
        r.structured_output = {"vulnerabilities": []}
        r.text = "{}"
        return r
    monkeypatch.setattr(act, "run_gitnexus_verdict_agent", fake_verdict)

    monkeypatch.setattr(act, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    inp = MagicMock(); inp.workspace_name = "ws"; inp.api_key = None

    await act.run_authz_gitnexus_judge(inp)
    assert explored["n"] == 1, "0 候选时应触发自主探索"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/whitebox/tests/pipeline/test_authz_judge_deep.py::test_authz_judge_explores_when_zero_candidates -v`
Expected: FAIL — 现状 0 候选直接写空 queue（explored==0）

- [ ] **Step 3: Write minimal implementation**

(a) 新建 `prompts/authz_gitnexus_explore.txt`：

```
你是 IDOR/越权审计 agent。GitNexus 确定性层未产候选（入口点未识别或无 side-effect sink 命中）。
自主探索仓库找潜在 IDOR：

1. 用 grep 找所有路由定义（app.get/post/put/delete、@GetMapping、Route::、router.* 等）。
2. 对每个处理对象的端点（路径含 {id}/:id/参数），用 read 看其 handler：
   - 是否直接用请求参数取对象（req.params.id → DB 查询）？
   - handler 或调用链上是否有 ownership/role 守卫（where userId=、req.user.id 比较、@PreAuthorize）？
3. 无守卫且取外部可控对象引用 → IDOR 候选。

可用端点摘要（确定性层识别的，可能不全，自行 grep 补）：
{{entry_points_summary}}

输出 JSON：{"vulnerabilities": [{"endpoint": "...", "vulnerability_type": "Horizontal",
"externally_exploitable": true, "vulnerable_code_location": "file:line",
"reason": "...", "minimal_witness": "...", "confidence": "low", "notes": "explore-discovered, needs review"}]}
保守：不确定判 vulnerable。所有探索发现标 confidence=low（候选未经确定性 dominance 验证）。
```

(b) `activities.py` 0 候选分支（:303-311 的 `if candidate_count == 0:` 内，log warning 后）加探索：

```python
                if candidate_count == 0:
                    await _session.log_info(
                        f"authz GitNexus 轨：0 候选（...）→ 触发自主探索（多轮 agent 读 route 找 IDOR）。",
                        "warning",
                    )
                    # spec-1a G2：0 候选不静默空——agent 自主探索
                    explore_prompt = prompt_manager.load_sync(
                        "authz_gitnexus_explore",
                        variables={"entry_points_summary": _entry_points_brief(entry_point_total, http_route_count)},
                    )
                    result = await run_gitnexus_verdict_agent(
                        prompt=explore_prompt, repo_path=str(repo),
                        structured_output_schema={"type": "object",
                            "properties": {"vulnerabilities": {"type": "array"}}},
                        audit_session=_session,
                    )
                    raw = result.structured_output or result.text
                    parsed = VulnerabilityQueue.parse_lenient(
                        raw if isinstance(raw, str) else json.dumps(raw) if raw is not None else "{}")
                    for v in parsed.queue.vulnerabilities:
                        data = v.model_dump()
                        data["source_track"] = "gitnexus"
                        data["needs_review"] = True      # 探索发现，软候选
                        if not data.get("evidence_chain"):
                            data["evidence_chain"] = "gitnexus explore-discovered (0 deterministic candidates)"
                        vulnerabilities.append(data)
```

> `prompt_manager` 需在 0 候选分支可见——若 `prompts_dir`/`prompt_manager` 现仅在 `if candidate_count > 0` 内构造（:323-324），提到 `candidate_count = ...` 之后、分支之前构造一次，两分支共用。`_entry_points_brief` 是简单格式化（`f"{http_route_count} http_route / {entry_point_total} total"`），内联或 helper 均可。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/whitebox/tests/pipeline/test_authz_judge_deep.py -v`
Expected: PASS（两测试：多轮 + 探索）

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py prompts/authz_gitnexus_explore.txt packages/whitebox/tests/pipeline/test_authz_judge_deep.py
git commit -m "feat(authz): 0 候选时触发多轮 agent 自主探索 IDOR（G2）"
```

---

### Task 5: authz_judge retry 切 gitnexus-verdict + 更新 guardrail

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py:373`（authz_judge retry_policy）
- Modify: `packages/whitebox/tests/pipeline/test_workflows_safety.py`（spec-0 T4 加的 guardrail）
- Test: 同 guardrail 文件

**Interfaces:**
- Consumes：`GITNEXUS_VERDICT_RETRY`（spec-0 T2 已建）
- Produces：authz_judge 用 `retry_for("gitnexus-verdict")`（max 3）；guardrail 从"禁止 gitnexus-verdict"改为"authz_judge 用 gitnexus-verdict"

- [ ] **Step 1: Write the failing test**

```python
# test_workflows_safety.py：更新 spec-0 T4 的 guardrail 测试
def test_authz_judge_uses_gitnexus_verdict_retry():
    """authz_judge retry 切 gitnexus-verdict（多轮 agent，max 3）。"""
    import inspect
    from shannon_whitebox.pipeline import workflows
    src = inspect.getsource(workflows)
    # authz_judge activity 块内须用 gitnexus-verdict（不是 standard）
    import re
    # 找 run_authz_gitnexus_judge activity 后的 retry_policy
    m = re.search(r"run_authz_gitnexus_judge[\s\S]*?retry_policy=retry_for\(\"(\w[\w-]*)\"\)", src)
    assert m is not None, "找不到 authz_judge retry_policy"
    assert m.group(1) == "gitnexus-verdict", f"authz_judge 应切 gitnexus-verdict，实际 {m.group(1)}"
```

> 同时**更新或删除** spec-0 T4 的 `test_verdict_activities_keep_standard_retry_policy`（它断言 authz_judge 仍 standard + gitnexus-verdict 不在 workflows.py——现在两者都变了，旧断言会 FAIL）。改为断言"chain_verdict 仍 standard"（chain_verdict 不在本 plan 切）。

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/whitebox/tests/pipeline/test_workflows_safety.py -v -k "authz_judge_uses_gitnexus or verdict_activities"`
Expected: FAIL — authz_judge 现仍 standard；旧 guardrail（keep standard）也需更新

- [ ] **Step 3: Write minimal implementation**

(a) `workflows.py:373` authz_judge 的 retry_policy：
```python
start_to_close_timeout=timedelta(minutes=30),
retry_policy=retry_for("gitnexus-verdict"),   # 原 standard；spec-1a 切（多轮 agent，max 3）
```

(b) `test_workflows_safety.py` 更新旧 guardrail：把 `test_verdict_activities_keep_standard_retry_policy` 改为只锁 chain_verdict 仍 standard（去掉 authz_judge standard 断言 + 去掉"gitnexus-verdict 不在 workflows.py"断言）：
```python
def test_chain_verdict_keeps_standard_retry():
    """chain_verdict (inj/xss/ssrf) 仍 standard（spec-1a 只切 authz_judge）。"""
    import inspect, re
    from shannon_whitebox.pipeline import workflows
    src = inspect.getsource(workflows)
    m = re.search(r"run_gitnexus_chain_verdict[\s\S]*?retry_policy=retry_for\(\"(\w[\w-]*)\"\)", src)
    assert m is not None
    assert m.group(1) == "standard"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/whitebox/tests/pipeline/test_workflows_safety.py -v`
Expected: PASS（新 authz_judge_uses_gitnexus_verdict_retry + 更新后 chain_verdict_keeps_standard_retry）

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/workflows.py packages/whitebox/tests/pipeline/test_workflows_safety.py
git commit -m "feat(whitebox): authz_judge retry 切 gitnexus-verdict（多轮 agent）+ guardrail 更新"
```

---

## 验证（真机，task 全过后）

- **多轮实跑**：真仓库（NodeGoat）白盒跑，`authz_gitnexus_judge` 经 `run_gitnexus_verdict_agent` 跑多轮（`result.turns > 1`），产 `authz_gitnexus_queue.json`。观察 audit log 有 gitnexus-verdict 工具调用记录。
- **0 候选探索**：`entry_points=0` 的目标，judge 触发探索 prompt，queue 非空（或"已探索无发现"证据，非静默空）。
- **R3 token 实测（epic 关键）**：GitNexus 深度 agent（吃候选）vs LLM 轨 vuln-authz（从零）的 token/召回对比——确认杠杆成立。证伪则回 epic 评估。

---

## Self-Review

**Spec coverage**（spec-1 G1/G2/G3 + F1）：
- G1（判定加深）→ T3 ✓
- G2（候选空探索）→ T4 ✓
- G3（补 render SourcePoint）→ T1 ✓
- F1（verdict_agent 补 audit）→ T2 ✓
- retry 切 gitnexus-verdict（spec §3.4 留 spec-1）→ T5 ✓
- 非目标（不改候选算法/LLM 轨 vuln-authz/merger）→ Global Constraints 锁 ✓

**Placeholder 扫描**：fixture细节（EntryPoint/SourcePoint 字段、ActivityInput 必填、_get_paths mock）标注"以 repo 实际为准，按实际补全 fixture，不删断言"——是 TDD fixture 适配指引，非占位空话。`_entry_points_brief` 标注内联或 helper 均可。候选分批标"先全量，超时分批留 follow-up"。无 TBD/TODO。

**类型一致**：`run_gitnexus_verdict_agent` 签名（T2 加 audit_session）跨 T3/T4 调用一致（keyword-only prompt/repo_path/structured_output_schema/audit_session）。`render_authz_gitnexus_candidates` 签名 T1 不变（只表格内容变）。retry_for("gitnexus-verdict") T5 用、spec-0 T2 已定义。
