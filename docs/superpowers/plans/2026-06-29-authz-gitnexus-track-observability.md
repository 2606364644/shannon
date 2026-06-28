# authz GitNexus 轨可观测性 + AZ-4 防回退 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除 authz GitNexus 轨在 `code_index` 空壳时的静默空转（candidate_count==0 经 InfoEvent 发 warning），并加 AZ-4 防回退测试锁定"recon 逐方法列 DELETE"。

**Architecture:** core 层 `build_authz_gitnexus_track` 返回类型从 3-tuple 升级为 `NamedTuple`（新增 `http_route_count`/`entry_point_total` 诊断字段，零额外 I/O）；whitebox 层 `run_authz_gitnexus_judge` 用这两个字段经 `get_audit_session().log_info()` 发 InfoEvent（best-effort）。AZ-4 是纯 prompt 防回退测试（prompt 已合规）。

**Tech Stack:** Python 3, temporalio（activity）, pytest + pytest-asyncio, pydantic, NamedTuple。

## Global Constraints

- **双轨独立性**（CLAUDE.md §1）：不改 `prompts/vuln-authz.txt`、不喂确定性产物给 LLM 轨。本次只动 GitNexus 轨 activity 可观测性 + core 返回类型 + prompt 防回退测试。
- **best-effort log**：`log_info` 失败用 `try/except` 吞掉，绝不影响扫描（对齐 `log_info_activity` 的防御）。
- **不改合并**：`run_merge_dual_track_queues` / `dual_track_merger.py` 不碰；`externally_exploitable` 仍是不被覆写的可达性标签。
- **lenient 不变**：`code_index.json`/`framework_analysis.json` 缺失仍走空候选，不崩。
- **只跑改动相关测试**（CLAUDE.md §3）：勿广跑全套，有预存 hang。
- 测试解包口径对齐 `find_unguarded_sink_paths` 的 http_route 过滤：`entry_type == "http_route" and route is not None`。

**Spec:** `docs/superpowers/specs/2026-06-29-authz-gitnexus-track-observability-design.md`

---

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `packages/core/src/shannon_core/code_index/authz_gitnexus_track.py` | IDOR 候选生成 + 渲染 + build | Modify：定义 `AuthzTrackBuildResult` NamedTuple；`build_authz_gitnexus_track` 返回它（带诊断字段） |
| `packages/core/tests/code_index/test_authz_build_track.py` | build 单元测试 | Modify：7 处解包适配 + 新增诊断字段断言 |
| `packages/core/tests/code_index/test_authz_track_integration.py` | build e2e 测试 | Modify：3 处解包适配 |
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | `run_authz_gitnexus_judge` activity | Modify：解包适配（Task1）+ 加 `log_info`（Task2） |
| `packages/whitebox/tests/test_run_authz_gitnexus_judge.py` | judge activity 测试 | Modify：mock 加 `log_info=AsyncMock` + 新增 log 行为断言 |
| `packages/core/tests/prompts/test_endpoint_method_enumeration.py` | AZ-4 防回退 | Create |

---

## Task 1: `build_authz_gitnexus_track` 返回诊断 NamedTuple + 全调用点适配

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/authz_gitnexus_track.py`（import 区 + `:49` 附近 + `:285-333` build 函数）
- Modify: `packages/core/tests/code_index/test_authz_build_track.py:49/61/76/84`
- Modify: `packages/core/tests/code_index/test_authz_track_integration.py:62/79/96`
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:250`

**Interfaces:**
- Produces: `build_authz_gitnexus_track(deliverables_dir: str) -> AuthzTrackBuildResult`，其中
  `AuthzTrackBuildResult(markdown: str, dominance_candidates: list[IDORCandidateChain], framework_candidates: list[FrameworkIDORCandidate], http_route_count: int, entry_point_total: int)`。Task 2 消费 `http_route_count`/`entry_point_total`。

- [ ] **Step 1: 写失败测试（诊断字段断言）**

在 `packages/core/tests/code_index/test_authz_build_track.py` 末尾追加：

```python
def test_build_returns_diagnostic_fields(tmp_path):
    handler = _block("u.js:update:10", "async function update(req){ await repo.update(req.params.id); }")
    sink = _block("repo.js:update:1", "function update(){ db.user.update(); }")
    _write_index(tmp_path, [_ep("u.js:update:10", "/api/u/:id", "PUT")], [handler, sink])

    result = build_authz_gitnexus_track(str(tmp_path))

    assert result.entry_point_total == 1
    assert result.http_route_count == 1
    assert result.markdown and "PUT /api/u/:id" in result.markdown
    assert len(result.dominance_candidates) == 1
    assert result.dominance_candidates[0].sink_id == "repo.js:update:1"


def test_build_diagnostic_fields_zero_when_empty(tmp_path):
    # code_index 缺失 → 空 CodeIndex → 诊断字段皆为 0
    result = build_authz_gitnexus_track(str(tmp_path))
    assert result.entry_point_total == 0
    assert result.http_route_count == 0
    assert result.dominance_candidates == []
    assert result.framework_candidates == []
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/core && python -m pytest tests/code_index/test_authz_build_track.py::test_build_returns_diagnostic_fields -v`
Expected: FAIL — `AttributeError: 'tuple' object has no attribute 'entry_point_total'`（build 仍返回 3-tuple）。

- [ ] **Step 3: 定义 NamedTuple**

在 `authz_gitnexus_track.py` import 区（`from dataclasses import dataclass` 之后）加：

```python
from typing import NamedTuple
```

在 `IDORCandidateChain` dataclass 定义之后（`FrameworkIDORCandidate` 之前的位置即可，紧邻 `:56` 之后）加：

```python
class AuthzTrackBuildResult(NamedTuple):
    """build_authz_gitnexus_track 的返回：候选 + 入口点诊断（spec §3.1）。"""
    markdown: str
    dominance_candidates: list[IDORCandidateChain]
    framework_candidates: list[FrameworkIDORCandidate]
    http_route_count: int       # entry_type=="http_route" 且 route 非空的入口点数（dominance 直接输入）
    entry_point_total: int      # code_index entry_points 总数（含 gitnexus 合成项）
```

- [ ] **Step 4: 改 build 返回 NamedTuple**

在 `build_authz_gitnexus_track`（`:285-333`）末尾，把现有的：

```python
    md = render_authz_gitnexus_candidates(
        dominance_cands, framework_cands, index=index, entry_points=entry_points,
    )
    logger.info(
        "authz GitNexus track built: %d dominance + %d framework candidates",
        len(dominance_cands), len(framework_cands),
    )
    return md, dominance_cands, framework_cands
```

替换为：

```python
    md = render_authz_gitnexus_candidates(
        dominance_cands, framework_cands, index=index, entry_points=entry_points,
    )
    entry_point_total = len(index.entry_points)
    http_route_count = sum(
        1 for ep in index.entry_points
        if ep.entry_type == "http_route" and ep.route is not None
    )
    logger.info(
        "authz GitNexus track built: %d dominance + %d framework candidates "
        "(http_route entry points: %d/%d)",
        len(dominance_cands), len(framework_cands),
        http_route_count, entry_point_total,
    )
    return AuthzTrackBuildResult(
        markdown=md,
        dominance_candidates=dominance_cands,
        framework_candidates=framework_cands,
        http_route_count=http_route_count,
        entry_point_total=entry_point_total,
    )
```

同时把 build 函数签名返回注解（`:287`）从 `-> tuple[str, list[IDORCandidateChain], list[FrameworkIDORCandidate]]:` 改为 `-> AuthzTrackBuildResult:`。

- [ ] **Step 5: 适配 7 处 tuple 解包（统一模式）**

统一改法（before → after）：

```python
# before
md, dom_cands, fw_cands = build_authz_gitnexus_track(str(tmp_path))
# after
md, dom_cands, fw_cands, http_route_count, entry_point_total = build_authz_gitnexus_track(str(tmp_path))
```

> 注：各处变量名（`dom`/`dom_cands`、`fw`/`fw_cands`）保持原样，只追加后两个变量。

需改的精确位置：
- `packages/core/tests/code_index/test_authz_build_track.py:49`、`:61`、`:76`、`:84`
- `packages/core/tests/code_index/test_authz_track_integration.py:62`、`:79`、`:96`
- `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:250`（`md, dom_cands, fw_cands = build_authz_gitnexus_track(str(deliverables))` → 追加 `, http_route_count, entry_point_total`；其下 `:251` `candidate_count = len(dom_cands) + len(fw_cands)` **不变**）

- [ ] **Step 6: 跑全部相关测试验证通过**

Run:
```bash
cd packages/core && python -m pytest tests/code_index/test_authz_build_track.py tests/code_index/test_authz_track_integration.py -v
cd ../whitebox && python -m pytest tests/test_run_authz_gitnexus_judge.py -v
```
Expected: 全 PASS（含新诊断断言 + 现有测试解包适配后仍绿）。

- [ ] **Step 7: Commit**

```bash
git add packages/core/src/shannon_core/code_index/authz_gitnexus_track.py \
        packages/core/tests/code_index/test_authz_build_track.py \
        packages/core/tests/code_index/test_authz_track_integration.py \
        packages/whitebox/src/shannon_whitebox/pipeline/activities.py
git commit -m "refactor(authz-gitnexus): build 返回 NamedTuple 含入口点诊断字段

build_authz_gitnexus_track 返回类型 3-tuple → AuthzTrackBuildResult
(+http_route_count/entry_point_total)，为 Task2 可观测性铺路；零额外
I/O（数据已在 index）。7 处调用点解包适配。"
```

---

## Task 2: `run_authz_gitnexus_judge` 加 InfoEvent log（消除静默空转）

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:251` 之后（candidate_count 计算后）+ `:286` 之后（verdict 收集后）
- Modify: `packages/whitebox/tests/test_run_authz_gitnexus_judge.py`（mock 加 `log_info` + 新增 log 断言）

**Interfaces:**
- Consumes: Task 1 的 `AuthzTrackBuildResult.http_route_count` / `entry_point_total`（已在 `:250` 解包为同名变量）。
- Produces: `run_authz_gitnexus_judge` 经 `get_audit_session().log_info()` 发 InfoEvent（candidate_count==0 → warning；>0 → info + verdict 数）。

- [ ] **Step 1: 写失败测试（0 候选拍 warning）**

在 `packages/whitebox/tests/test_run_authz_gitnexus_judge.py` 顶部把 mock import 改为：

```python
from unittest.mock import patch, AsyncMock
```

在文件末尾（`_noop_cm_factory` 之前）追加：

```python
@pytest.mark.asyncio
async def test_judge_logs_warning_when_no_candidates(tmp_path):
    """0 候选 → 发 warning（经 InfoEvent），点明 http_route 入口点数。"""
    (tmp_path / "code_index.json").write_text(json.dumps({
        "repository": "r", "language": "typescript", "total_blocks": 0,
        "total_entry_points": 0, "total_chains": 0, "blocks": [], "edges": [],
        "entry_points": [], "chains": [],
    }))

    async def fake_run(prompt, **kwargs):
        return type("R", (), {"success": True, "structured_output": {"vulnerabilities": []}})()

    with patch.object(activities, "_get_paths", return_value=(tmp_path, tmp_path, tmp_path)):
        with patch("shannon_whitebox.pipeline.activities.run_claude_prompt", new=fake_run):
            with patch("shannon_whitebox.audit.session_registry.get_audit_session") as gs:
                inst = gs.return_value
                inst.track_step = _noop_cm_factory()
                inst.log_info = AsyncMock()
                await activities.run_authz_gitnexus_judge(_FakeInput(tmp_path))

    levels = [call.args[1] for call in inst.log_info.call_args_list]
    msgs = [call.args[0] for call in inst.log_info.call_args_list]
    assert "warning" in levels
    assert any("0 候选" in m and "http_route" in m for m in msgs)


@pytest.mark.asyncio
async def test_judge_logs_info_when_candidates(tmp_path):
    """有候选 → 发 info（调 LLM + 产出 verdict 数）。"""
    _write_index_with_candidate(tmp_path)

    async def fake_run(prompt, **kwargs):
        return type("R", (), {
            "success": True, "error": None, "retryable": False, "turns": 1,
            "cost": 0.0, "text": "", "model": "m", "stop_reason": "end",
            "tokens": None,
            "structured_output": {"vulnerabilities": [{
                "ID": "AUTHZ-GN-01", "vulnerability_type": "Horizontal",
                "externally_exploitable": True, "endpoint": "PUT /api/u/:id",
                "vulnerable_code_location": "u.js:update:10", "role_context": "user",
                "guard_evidence": "none", "side_effect": "update", "reason": "no ownership",
                "minimal_witness": "x", "confidence": "high", "notes": "",
            }]},
        })()

    with patch.object(activities, "_get_paths", return_value=(tmp_path, tmp_path, tmp_path)):
        with patch("shannon_whitebox.pipeline.activities.run_claude_prompt", new=fake_run):
            with patch("shannon_whitebox.audit.session_registry.get_audit_session") as gs:
                inst = gs.return_value
                inst.track_step = _noop_cm_factory()
                inst.log_info = AsyncMock()
                await activities.run_authz_gitnexus_judge(_FakeInput(tmp_path))

    levels = [call.args[1] for call in inst.log_info.call_args_list]
    msgs = [call.args[0] for call in inst.log_info.call_args_list]
    assert "info" in levels
    assert any("候选" in m for m in msgs)
    assert any("verdict" in m for m in msgs)  # 判定后那条
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/whitebox && python -m pytest tests/test_run_authz_gitnexus_judge.py::test_judge_logs_warning_when_no_candidates -v`
Expected: FAIL — `inst.log_info` 从未被调用（assert "warning" in levels 失败，levels 为空）。

- [ ] **Step 3: 给现有 mock 加 log_info（防止 await MagicMock 崩）**

在 `test_run_authz_gitnexus_judge.py` 现有 3 个测试（`test_judge_writes_gitnexus_queue_from_candidates`、`test_judge_skips_llm_when_no_candidates`、`test_judge_lenient_on_invalid_llm_output`）里，每处 `inst.track_step = _noop_cm_factory()` 之后追加一行：

```python
                inst.log_info = AsyncMock()
```

> 必须做：Task 2 加 `await get_audit_session().log_info(...)` 后，若 mock 未设 `log_info`，`gs.return_value.log_info` 是普通 MagicMock，`await` 它会 `TypeError`，3 个现有测试会崩。

- [ ] **Step 4: 在 activity 加 log_info（candidate_count 分支）**

在 `activities.py` 的 `run_authz_gitnexus_judge`，定位 `candidate_count = len(dom_cands) + len(fw_cands)`（Task 1 后约 `:252`）。在其后、`if candidate_count > 0:`（约 `:255`）之前插入：

```python
            # 可观测性（spec §3.2）：GitNexus 轨候选状态经 InfoEvent 通道，避免静默空转。
            # best-effort：显示通道失败绝不影响扫描（对齐 log_info_activity 防御）。
            try:
                _session = get_audit_session()
                if candidate_count == 0:
                    await _session.log_info(
                        f"authz GitNexus 轨：0 候选（dominance={len(dom_cands)}, "
                        f"framework={len(fw_cands)}；http_route 入口点="
                        f"{http_route_count}/{entry_point_total}）→ 跳过 LLM 判定，"
                        f"authz 全靠 LLM 轨兜底。http_route=0 常因 code_index 入口点未识别"
                        f"（语言误判/调用图未就绪/纯静态页）。",
                        "warning",
                    )
                else:
                    await _session.log_info(
                        f"authz GitNexus 轨：{candidate_count} 候选（dominance="
                        f"{len(dom_cands)}, framework={len(fw_cands)}）→ 调 LLM 判定。",
                        "info",
                    )
            except Exception:
                pass
```

`get_audit_session` 已在函数顶部（约 `:241`）import，无需新增 import。

- [ ] **Step 5: 在 activity 加判定后 log（candidate_count>0 分支末）**

在同一函数的 `if candidate_count > 0:` block 内，定位 `vulnerabilities.append(data)` 循环结束后、`atomic_write_json(...)`（约 `:289`）之前插入：

```python
                try:
                    await get_audit_session().log_info(
                        f"authz GitNexus 轨：产出 {len(vulnerabilities)} 条 verdict。",
                        "info",
                    )
                except Exception:
                    pass
```

- [ ] **Step 6: 跑全部 judge 测试验证通过**

Run: `cd packages/whitebox && python -m pytest tests/test_run_authz_gitnexus_judge.py -v`
Expected: 全 PASS（5 个测试：原 3 个 + 新增 2 个 log 断言）。

- [ ] **Step 7: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py \
        packages/whitebox/tests/test_run_authz_gitnexus_judge.py
git commit -m "feat(authz-gitnexus): judge 加 InfoEvent 可观测性，消除静默空转

candidate_count==0 发 warning（点明 http_route 入口点数，引导到空壳真
根因）；>0 发 info + verdict 数。经 log_info→InfoEvent→renderer 进
workflow.log。best-effort try/except 不影响扫描。"
```

---

## Task 3: AZ-4 防回退测试（锁 recon 逐方法列 DELETE）

**Files:**
- Create: `packages/core/tests/prompts/test_endpoint_method_enumeration.py`

**Interfaces:**
- 无运行时依赖。纯文本断言，锁 `prompts/shared/_endpoint-security-context.txt` + `prompts/recon.txt` + `prompts/recon-static.txt` 当前合规状态不被回退。

- [ ] **Step 1: 写测试（直接验证已合规的 prompt）**

创建 `packages/core/tests/prompts/test_endpoint_method_enumeration.py`：

```python
"""AZ-4 防回退（spec §3.3）：recon 必须 @include endpoint-security-context，
且后者必须禁止 ALL 简写、逐方法列出全部 5 个 HTTP 动词（含 DELETE）。

历史背景：docs/gap/authz-effect-gap-analysis.md AZ-4 曾记"recon 用 ALL
符号掩盖 DELETE"。当前 _endpoint-security-context.txt:11-14 已落地"禁止
ALL / 逐方法列"，本测试锁定该状态不被回退。
"""
from pathlib import Path

# parents[4] = repo root（同 test_static_dataflow_hints_decoupling.py 范式）
PROMPTS_DIR = Path(__file__).resolve().parents[4] / "prompts"
ESC = PROMPTS_DIR / "shared" / "_endpoint-security-context.txt"
INCLUDE_LINE = "@include(shared/_endpoint-security-context.txt)"


def test_endpoint_security_context_forbids_all_shorthand():
    """partial 必须显式禁止 ALL 且逐方法列全 5 动词。"""
    text = ESC.read_text()
    assert "Do NOT use" in text
    assert "ALL shorthand" in text
    assert "List each method explicitly" in text
    for verb in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        assert verb in text, f"_endpoint-security-context.txt 缺少 HTTP 动词 {verb}"


def test_recon_prompts_include_endpoint_security_context():
    """recon + recon-static 都必须 @include endpoint-security-context。"""
    for name in ("recon.txt", "recon-static.txt"):
        content = (PROMPTS_DIR / name).read_text()
        assert INCLUDE_LINE in content, (
            f"{name} 缺少 {INCLUDE_LINE} —— AZ-4 防回退：端点安全上下文必须经 @include 注入"
        )
```

- [ ] **Step 2: 跑测试验证通过（prompt 已合规）**

Run: `cd packages/core && python -m pytest tests/prompts/test_endpoint_method_enumeration.py -v`
Expected: PASS（2 个测试；prompt 当前已合规——若失败说明 prompt 被回退，需先修 prompt 再提交）。

- [ ] **Step 3: Commit**

```bash
git add packages/core/tests/prompts/test_endpoint_method_enumeration.py
git commit -m "test(prompts): AZ-4 防回退——锁 recon 逐方法列 DELETE

锁定 _endpoint-security-context.txt 禁止 ALL/逐方法列 5 动词 + 两 recon
prompt @include 它。gap 文档 AZ-4 已落地，本测试防回退。"
```

---

## Self-Review（plan 作者自查记录）

**Spec 覆盖**：
- spec §3.1（NamedTuple 诊断字段）→ Task 1 ✓
- spec §3.2（judge InfoEvent log）→ Task 2 ✓
- spec §3.3（AZ-4 防回退）→ Task 3 ✓
- spec §4 不变量 → Global Constraints ✓（双轨独立性/best-effort/不改 merge/lenient）
- spec §1 非目标（W1-A/W2 不做）→ 不在 plan 内 ✓

**占位符扫描**：无 TBD/TODO；每步有完整代码。

**类型一致性**：`AuthzTrackBuildResult` 字段名（`http_route_count`/`entry_point_total`）在 Task 1 定义、Task 1 Step 5 解包、Task 2 Step 4 引用——三处一致。`get_audit_session().log_info(msg, level)` 签名与 `log_info_activity:258` 一致。
