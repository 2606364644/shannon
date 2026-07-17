# GitNexus 轨 fail-fast 改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** GitNexus 轨判定失败时 fail-fast 显式暴露(删跨轨降级兜底):开轨标红+其他类继续,关轨终止扫描;合法空结果(跑通 0 findings)不误伤。

**Architecture:** 业务 fail(GitNexus 判定流程断裂)放 activity 返回值不 raise;workflow 读返回值汇总写 `gitnexus_track_status.json`,按 `is_llm_track_enabled()` 决策——**关轨 + `DEGRADABLE`(inj/xss/ssrf)fail → raise 终止**(这些类关轨无 LLM 兜底);**authz fail 永不终止**(authz-vuln LLM 关轨仍跑);开轨 → 标红继续。merger/报告读状态产物呈现标红。系统 error 仍 raise `ApplicationFailure`。

**Tech Stack:** Python 3 / Temporalio(workflow+activity)/ pytest。改 `packages/whitebox`(activities/workflows)+ `packages/core`(merger + 新状态产物模块)。

## Global Constraints

- **铁律**:`gitnexus_track_status.json` 只给 workflow/merger/report 编排用,**绝不喂 LLM 轨 prompt / 不被 vuln collector 或 LLM 轨 agent import**(对齐 `test_static_dataflow_hints_decoupling.py` 风格,Task 7 锁定)。
- **业务 fail 不 raise**(状态产物 + workflow 决策);**系统 error raise `ApplicationFailure`**(Temporal,不变)。
- **fail 判据 = 流程完整性**:前置缺/parse 失败/builder 或 verdict agent 异常/LLM 三层防线后仍坏 = `failed`;跑通 0 findings/0 候选/探索产软候选 = `ok`(不 fail)。
- **authz 0 候选→自主探索保留**(概念 A,内部 LLM 补召回),不视为 fail。
- **不改** source/sink/route/attack_chain 环节;不动 LLM 轨 collector/renderer(host-rendered);不涉双引擎差异。
- 测试只跑改动相关文件(勿广跑全套,feat/fork-py 有预存挂起)。

---

## File Structure

- **Create** `packages/core/src/shannon_core/code_index/gitnexus_track_status.py` — 状态产物 read/write helper(纯函数,无 GitNexus import)。
- **Create** `packages/core/tests/code_index/test_gitnexus_track_status.py` — helper 测试。
- **Modify** `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` — `run_gitnexus_chain_verdict`(返 fail 信息)、`run_authz_gitnexus_judge`(返 fail 信息)、`run_merge_dual_track_queues`(记 gitnexus_status)、`assemble_report`(标红注记)。
- **Modify** `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` — 删两处 try/except 降级吞异常,改读返回值写状态产物 + 开/关轨决策。
- **Create/Modify** whitebox 侧测试:`tests/test_gitnexus_chain_verdict_failfast.py`、`tests/test_authz_gitnexus_judge_failfast.py`、`tests/test_workflow_gitnexus_failfast.py`、`tests/test_merge_dual_track_status.py`、`tests/test_assemble_report_track_status.py`、`tests/test_track_status_decoupling.py`。

---

## Task 1: 状态产物 read/write helper

**Files:**
- Create: `packages/core/src/shannon_core/code_index/gitnexus_track_status.py`
- Test: `packages/core/tests/code_index/test_gitnexus_track_status.py`

**Interfaces:**
- Produces: `write_track_status(deliverables: Path, statuses: dict) -> None`、`read_track_status(deliverables: Path) -> dict`。`statuses` 形如 `{"injection":{"status":"ok","findings":3}, "xss":{"status":"failed","reason":"..."}, ...}`,`status ∈ {"ok","failed"}`;`read_track_status` 文件缺/损坏返 `{}`(不抛)。

- [ ] **Step 1: Write failing test**

```python
# packages/core/tests/code_index/test_gitnexus_track_status.py
from pathlib import Path
from shannon_core.code_index.gitnexus_track_status import write_track_status, read_track_status

def test_write_then_read_roundtrip(tmp_path):
    statuses = {
        "injection": {"status": "ok", "findings": 3},
        "xss": {"status": "failed", "reason": "builder raised: KeyError"},
        "authz": {"status": "ok", "findings": 0},
    }
    write_track_status(tmp_path, statuses)
    assert read_track_status(tmp_path) == statuses

def test_read_missing_file_returns_empty(tmp_path):
    assert read_track_status(tmp_path) == {}

def test_read_corrupt_file_returns_empty(tmp_path):
    (tmp_path / "gitnexus_track_status.json").write_text("{not json", encoding="utf-8")
    assert read_track_status(tmp_path) == {}

def test_write_overwrites(tmp_path):
    write_track_status(tmp_path, {"injection": {"status": "ok", "findings": 1}})
    write_track_status(tmp_path, {"injection": {"status": "failed", "reason": "x"}})
    assert read_track_status(tmp_path) == {"injection": {"status": "failed", "reason": "x"}}
```

- [ ] **Step 2: Run test → FAIL**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_gitnexus_track_status.py -v`
Expected: FAIL `ModuleNotFoundError: shannon_core.code_index.gitnexus_track_status`

- [ ] **Step 3: Implement helper**

```python
# packages/core/src/shannon_core/code_index/gitnexus_track_status.py
"""GitNexus 轨 per-class 状态产物(fail-fast 编排用)。

workflow 写、merger/report 读。纯函数,不 import GitNexus/确定性层符号。
铁律:本产物只给 workflow/merger/report 编排用,绝不喂 LLM 轨 prompt。
"""
from __future__ import annotations

import json
from pathlib import Path

FILENAME = "gitnexus_track_status.json"


def write_track_status(deliverables: Path, statuses: dict) -> None:
    """原子写 per-class 状态。statuses = {vc: {"status":"ok"|"failed", ...}}。"""
    path = Path(deliverables) / FILENAME
    path.write_text(json.dumps(statuses, ensure_ascii=False, indent=2), encoding="utf-8")


def read_track_status(deliverables: Path) -> dict:
    """读 per-class 状态;文件缺/损坏返 {}(不抛,merger/report 容错)。"""
    path = Path(deliverables) / FILENAME
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
```

- [ ] **Step 4: Run test → PASS**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_gitnexus_track_status.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/gitnexus_track_status.py packages/core/tests/code_index/test_gitnexus_track_status.py
git commit -m "feat(code_index): gitnexus_track_status 读写 helper—fail-fast 编排状态产物"
```

---

## Task 2: chain_verdict 返回 fail 信息(不 raise)+ 删降级文案

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`(`run_gitnexus_chain_verdict`,~1180-1303)
- Test: `packages/whitebox/tests/test_gitnexus_chain_verdict_failfast.py`

**Interfaces:**
- Consumes: Task 1 `write_track_status`(workflow 侧用,本 task 只改返回值)。
- Produces: `run_gitnexus_chain_verdict` 返回值新增 `failed_classes: list[str]`、`fail_reasons: dict[str,str]`。前置缺/无效 → `failed_classes=["injection","xss","ssrf"]`;builder 异常 → 该类进 `failed_classes`;跑通 → `per_class[vc]=N`(不进 failed)。

- [ ] **Step 1: Write failing test**

```python
# packages/whitebox/tests/test_gitnexus_chain_verdict_failfast.py
import pytest
from unittest.mock import patch, AsyncMock

# 三个场景:前置缺(3 类 failed)、builder 异常(该类 failed 其他 ok)、跑通 0(全 ok 不 failed)

@pytest.mark.asyncio
async def test_missing_parameter_graph_returns_failed_classes(tmp_path, monkeypatch):
    from shannon_whitebox.pipeline import activities
    act_input = type("I", (), {"__dict__": {}})()
    # _get_paths 返 (repo, deliverables=tmp_path, scratch)
    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, tmp_path, tmp_path))
    monkeypatch.setattr(activities, "get_audit_session", lambda: _FakeSession())
    # 无 parameter_graph.json
    result = await activities.run_gitnexus_chain_verdict(act_input)
    assert set(result["failed_classes"]) == {"injection", "xss", "ssrf"}
    assert result["per_class"] == {}

@pytest.mark.asyncio
async def test_zero_findings_is_ok_not_failed(tmp_path, monkeypatch):
    # parameter_graph 存在有效、builder 返 [] → per_class={} 但 failed_classes=[]
    ...  # 同上 mock _get_paths + 写一个空 parameter_graph + builder 返 []
    assert result["failed_classes"] == []
```

> 注:测试具体 mock(builder/parameter_graph 构造)参照现有 `tests/test_code_index_*.py` 的 mock 风格;核心断言是 `failed_classes` 语义。

- [ ] **Step 2: Run test → FAIL**

Run: `cd /root/shannon-py && uv run pytest packages/whitebox/tests/test_gitnexus_chain_verdict_failfast.py -v`
Expected: FAIL(返回值无 `failed_classes` 键)

- [ ] **Step 3: Modify chain_verdict**

在 `run_gitnexus_chain_verdict`(`activities.py:1180`)中:

(a) 函数体顶部 `per_class: dict[str, int] = {}` 旁加:
```python
        failed_classes: list[str] = []
        fail_reasons: dict[str, str] = {}
```

(b) 前置缺分支(~1213,`if not pgraph_path.exists():`),把 `return {"per_class": {}, "skipped": "no parameter_graph.json"}` 改为:
```python
            _reason = "parameter_graph.json missing"
            for _vc in ("injection", "xss", "ssrf"):
                failed_classes.append(_vc)
                fail_reasons[_vc] = _reason
            return {"per_class": {}, "failed_classes": failed_classes, "fail_reasons": fail_reasons}
```

(c) 前置无效分支(~1224,parse except),同样改为填 `failed_classes`(3 类)+ `fail_reasons[vc]="parameter_graph.json invalid"`,return 带 `failed_classes`。

(d) builder 异常分支(~1255,`except Exception as exc:` 内的 `logger.warning + continue`),改为:
```python
                except Exception as exc:
                    failed_classes.append(vc)
                    fail_reasons[vc] = f"builder raised: {exc}"
                    logger.warning("gitnexus chain-verdict %s failed: %s", vc, exc)
                    continue
```

(e) 删/改"靠 LLM 轨兜底"文案:3 类全 0 的 `log_info`(~1276,文案含"→ 靠 LLM 轨兜底。常因 parameter_graph 空壳")改为陈述事实:
```python
                    await _sess.log_info(
                        f"GitNexus 注入轨：3 类 0 findings（taint_flows={taint_flows_count}，"
                        f"sink_call_sites={sink_call_sites_count}）— 合法结论（流程跑通，本类无 taint）。"
                        f"下游按 fail-fast 策略编排。",
                        "info",
                    )
```

(f) 末尾 return(~1294)改为:
```python
        return {"per_class": per_class, "failed_classes": failed_classes, "fail_reasons": fail_reasons}
```

- [ ] **Step 4: Run test → PASS**

Run: `cd /root/shannon-py && uv run pytest packages/whitebox/tests/test_gitnexus_chain_verdict_failfast.py -v`
Expected: PASS(三个场景断言成立)

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/tests/test_gitnexus_chain_verdict_failfast.py
git commit -m "feat(gitnexus): chain_verdict 返 failed_classes(不 raise)+ 删「靠 LLM 轨兜底」文案"
```

---

## Task 3: authz 返回 fail 信息(不 raise)+ 保留探索 + 删降级文案

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`(`run_authz_gitnexus_judge`,~420-588)
- Test: `packages/whitebox/tests/test_authz_gitnexus_judge_failfast.py`

**Interfaces:**
- Produces: `run_authz_gitnexus_judge` 返回值新增 `failed: bool`、`fail_reason: str | None`。业务 fail(code_index/framework 缺、verdict agent 异常、parse 三层防线后仍坏)→ `failed=True` 不 raise;0 候选→探索产软候选 → `failed=False`;真系统异常仍 raise `ApplicationFailure`。

- [ ] **Step 1: Write failing test**

```python
# packages/whitebox/tests/test_authz_gitnexus_judge_failfast.py
import pytest

@pytest.mark.asyncio
async def test_build_track_failure_returns_failed_not_raise(tmp_path, monkeypatch):
    from shannon_whitebox.pipeline import activities
    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, tmp_path, tmp_path))
    monkeypatch.setattr(activities, "get_audit_session", lambda: _FakeSession())
    # build_authz_gitnexus_track 抛(模拟 code_index/framework 缺)
    monkeypatch.setattr("shannon_core.code_index.authz_gitnexus_track.build_authz_gitnexus_track",
                        lambda d: (_ for _ in ()).throw(FileNotFoundError("code_index.json missing")))
    result = await activities.run_authz_gitnexus_judge(_ActInput())
    assert result["failed"] is True
    assert "code_index" in result["fail_reason"]

@pytest.mark.asyncio
async def test_explore_branch_not_failed(tmp_path, monkeypatch):
    # 0 候选 → 探索 → 返 failed=False(概念 A 保留)
    ...  # mock build_authz_gitnexus_track 返 (md="", dom=0, fw=0, ...) + explore agent 返 []
    assert result["failed"] is False
```

> mock 细节参照现有 `tests/test_*gitnexus*.py`;核心断言是 `failed` 语义(业务 fail=True/探索=False)。

- [ ] **Step 2: Run test → FAIL**

Run: `cd /root/shannon-py && uv run pytest packages/whitebox/tests/test_authz_gitnexus_judge_failfast.py -v`
Expected: FAIL(返回值无 `failed` 键)

- [ ] **Step 3: Modify authz judge**

在 `run_authz_gitnexus_judge`(`activities.py:420`)中:

(a) `try:` 体顶部加:
```python
        failed = False
        fail_reason: str | None = None
```

(b) 包裹 `build_authz_gitnexus_track` 调用(~447),业务异常转 `failed`:
```python
            try:
                md, dom_cands, fw_cands, http_route_count, entry_point_total = build_authz_gitnexus_track(str(deliverables))
            except Exception as exc:
                failed = True
                fail_reason = f"build_authz_gitnexus_track failed: {exc}"
                logger.warning("authz gitnexus build track failed: %s", exc)
                atomic_write_json(deliverables / "authz_gitnexus_queue.json", {"vulnerabilities": []})
                return {"candidate_count": 0, "verdict_count": 0, "dominance_candidates": 0,
                        "framework_candidates": 0, "failed": True, "fail_reason": fail_reason}
```

(c) 包裹两个 `run_gitnexus_verdict_agent` 调用(候选>0 分支 ~483、0 候选探索分支 ~532),agent 异常转 `failed`(候选>0 分支)/ 探索分支 agent 异常也 `failed`:
```python
                try:
                    result = await run_gitnexus_verdict_agent(prompt=prompt, repo_path=str(repo), ...)
                except Exception as exc:
                    failed = True
                    fail_reason = f"verdict agent failed: {exc}"
                    logger.warning("authz gitnexus verdict agent failed: %s", exc)
                    vulnerabilities = []
                else:
                    raw = result.structured_output
                    ...  # 原 parse 逻辑保留(parse_lenient 已容错,不抛)
```
(探索分支同理:`try ... except → failed=True, fail_reason="explore agent failed: ..."`)。

(d) 删"全靠 LLM 轨兜底"文案(~459,0 候选 log 含"authz 全靠 LLM 轨兜底")改为:
```python
                    await _session.log_info(
                        f"authz GitNexus 轨：0 候选（dominance={len(dom_cands)}, "
                        f"framework={len(fw_cands)}）→ 进入自主探索分支（GitNexus 轨内部补召回）。",
                        "info",
                    )
```

(e) 末尾正常 return(~576)加 `failed` 字段:
```python
            return {
                "candidate_count": candidate_count,
                "verdict_count": len(vulnerabilities),
                "dominance_candidates": len(dom_cands),
                "framework_candidates": len(fw_cands),
                "failed": failed,
                "fail_reason": fail_reason,
            }
```

(f) 外层 `except PentestError / Exception → raise ApplicationFailure`(:582-587)**保留**(真系统异常仍 raise)。

- [ ] **Step 4: Run test → PASS**

Run: `cd /root/shannon-py && uv run pytest packages/whitebox/tests/test_authz_gitnexus_judge_failfast.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/tests/test_authz_gitnexus_judge_failfast.py
git commit -m "feat(gitnexus): authz judge 返 failed(业务 fail 不 raise/探索保留)+ 删降级文案"
```

---

## Task 4: workflow 统一编排(写状态产物 + 开轨标红/关轨终止)

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`(~382-430)
- Test: `packages/whitebox/tests/test_workflow_gitnexus_failfast.py`

**Interfaces:**
- Consumes: Task 1 `write_track_status`、Task 2/3 返回值(`failed_classes`/`fail_reasons`、`failed`/`fail_reason`)、`is_llm_track_enabled()`(`concurrency.py:40`)。
- Produces: workflow 在 GitNexus 两 activity 后写 `gitnexus_track_status.json`;**关轨 + `DEGRADABLE`(inj/xss/ssrf)任一 failed → raise 终止**(这些类关轨无 LLM 兜底);**authz GitNexus fail 永不终止**(authz-vuln LLM 关轨仍跑,做 Vertical/Context 兜底)→ 仅标红;开轨 → 继续。

- [ ] **Step 1: Write failing test**

```python
# packages/whitebox/tests/test_workflow_gitnexus_failfast.py
import pytest
from unittest.mock import patch, AsyncMock

@pytest.mark.asyncio
async def test_disabled_track_terminates_on_failure(...):
    # is_llm_track_enabled=False + chain_verdict 返 failed_classes=["xss"] → workflow raise
    ...

@pytest.mark.asyncio
async def test_enabled_track_continues_and_writes_status(...):
    # is_llm_track_enabled=True + 有 failed → 不 raise,继续 merger,且写 gitnexus_track_status.json
    ...

@pytest.mark.asyncio
async def test_disabled_track_authz_failure_does_not_terminate(...):
    # 关轨 + 仅 authz GitNexus failed(inj/xss/ssrf 全 ok)→ 不 raise!
    # 因 authz-vuln LLM 轨关轨时仍跑(DEGRADABLE 只含 inj/xss/ssrf),authz 有 LLM 兜底。
    # 扫描继续 merger,状态产物标 authz=failed 供报告标红。
    ...
```

> 用现有 workflow 测试框架(参照 `tests/test_*workflow*.py` 的 `workflow.execute` + activity mock 模式)。

- [ ] **Step 2: Run test → FAIL**

Run: `cd /root/shannon-py && uv run pytest packages/whitebox/tests/test_workflow_gitnexus_failfast.py -v`
Expected: FAIL(workflow 仍 try/except 吞异常,不写状态产物)

- [ ] **Step 3: Modify workflow**

在 `workflows.py` GitNexus 编排段(~382-430),**删两处 try/except 降级吞异常**,改读返回值:

```python
            # === Authz GitNexus track ===
            _authz_gn = await workflow.execute_activity(
                activities.run_authz_gitnexus_judge, act_input,
                start_to_close_timeout=timedelta(minutes=30),
                retry_policy=retry_for("gitnexus-verdict"),
            )
            # === GitNexus chain verdict: inj/xss/ssrf ===
            _gn_verdict = await workflow.execute_activity(
                activities.run_gitnexus_chain_verdict, act_input,
                start_to_close_timeout=timedelta(minutes=15),
                retry_policy=retry_for("standard"),
            )

            # === fail-fast 编排:汇总两轨状态,写 gitnexus_track_status.json ===
            _statuses: dict = {}
            for _vc, _n in (_gn_verdict or {}).get("per_class", {}).items():
                _statuses[_vc] = {"status": "ok", "findings": _n}
            for _vc in (_gn_verdict or {}).get("failed_classes", []):
                _statuses[_vc] = {"status": "failed",
                                  "reason": (_gn_verdict or {}).get("fail_reasons", {}).get(_vc, "unknown")}
            if _authz_gn is not None:
                if _authz_gn.get("failed"):
                    _statuses["authz"] = {"status": "failed", "reason": _authz_gn.get("fail_reason", "unknown")}
                else:
                    _statuses["authz"] = {"status": "ok", "findings": _authz_gn.get("verdict_count", 0)}
            await workflow.execute_activity(
                activities.write_track_status_activity,
                ActivityInput(**{**act_input.__dict__, "track_statuses": _statuses}),
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=retry_for("log"),
            )

            # 关轨终止:仅 DEGRADABLE(inj/xss/ssrf)的 GitNexus fail 是真·无 LLM 兜底 → 终止。
            # authz 的 LLM 轨(authz-vuln)关轨时仍跑(DEGRADABLE 只含 inj/xss/ssrf,
            # authz-vuln 做 GitNexus 做不了的 Vertical/Context),故 authz GitNexus fail 仅标红不终止。
            if not is_llm_track_enabled():
                _no_fallback_failed = [
                    vc for vc in ("injection", "xss", "ssrf")  # = DEGRADABLE_VULN_CLASSES
                    if _statuses.get(vc, {}).get("status") == "failed"
                ]
                if _no_fallback_failed:
                    raise ApplicationFailure(
                        f"GitNexus 轨 fail-fast(关轨模式):{_no_fallback_failed} 判定失败,"
                        f"且这些类关轨后无 LLM 轨兜底 → 终止扫描。",
                        type="GitNexusTrackFailure", non_retryable=True,
                    )
            # authz GitNexus fail(任何模式)+ 开轨的 inj/xss/ssrf fail → 标红继续(merger/report 读状态产物)
```

加 import(文件顶部):`from temporalio.exceptions import ApplicationFailure`(若未 import)、`from shannon_core.config.concurrency import is_llm_track_enabled`。

新增薄 activity `write_track_status_activity`(在 `activities.py`,Task 1 helper 的 activity 包装):
```python
@activity.defn
async def write_track_status_activity(input: ActivityInput) -> dict:
    from shannon_core.code_index.gitnexus_track_status import write_track_status
    _, deliverables, _ = _get_paths(input)
    write_track_status(deliverables, getattr(input, "track_statuses", {}))
    return {"written": True}
```
(`ActivityInput` 需容忍 `track_statuses` 字段——若它是严格 dataclass,加 `track_statuses: dict = {}` 可选字段,或用 `getattr` 容错。)

- [ ] **Step 4: Run test → PASS**

Run: `cd /root/shannon-py && uv run pytest packages/whitebox/tests/test_workflow_gitnexus_failfast.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/workflows.py packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/tests/test_workflow_gitnexus_failfast.py
git commit -m "feat(workflow): GitNexus 轨 fail-fast 编排—删降级吞异常/写 track_status/关轨终止+开轨标红"
```

---

## Task 5: merger 记 gitnexus_status(供报告)

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`(`run_merge_dual_track_queues`,~824-895)
- Test: `packages/whitebox/tests/test_merge_dual_track_status.py`

**Interfaces:**
- Consumes: Task 1 `read_track_status`、Task 4 写的 `gitnexus_track_status.json`。
- Produces: `run_merge_dual_track_queues` 返回的 `per_class_counts[vc]` 新增 `gitnexus_status ∈ {"ok","failed","absent"}`。合并逻辑不变(failed 类自然退 llm-only)。

- [ ] **Step 1: Write failing test**

```python
# packages/whitebox/tests/test_merge_dual_track_status.py
import json
@pytest.mark.asyncio
async def test_failed_class_tagged_in_counts(tmp_path, monkeypatch):
    from shannon_whitebox.pipeline import activities
    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, tmp_path, tmp_path))
    monkeypatch.setattr(activities, "get_audit_session", lambda: _FakeSession())
    (tmp_path / "gitnexus_track_status.json").write_text(
        json.dumps({"xss": {"status": "failed", "reason": "builder raised"}}), encoding="utf-8")
    (tmp_path / "injection_gitnexus_queue.json").write_text('{"vulnerabilities":[]}', encoding="utf-8")
    (tmp_path / "injection_exploitation_queue.json").write_text('{"vulnerabilities":[]}', encoding="utf-8")
    result = await activities.run_merge_dual_track_queues(_ActInput())
    # injection ok(空)、xss failed(无 queue)
    ...
```

- [ ] **Step 2: Run test → FAIL**

Run: `cd /root/shannon-py && uv run pytest packages/whitebox/tests/test_merge_dual_track_status.py -v`
Expected: FAIL(per_class_counts 无 `gitnexus_status`)

- [ ] **Step 3: Modify merger**

在 `run_merge_dual_track_queues`(`activities.py:824`)中:

(a) `try:` 体顶部读状态:
```python
        from shannon_core.code_index.gitnexus_track_status import read_track_status
        track_status = read_track_status(deliverables)
```

(b) for 循环内,构建 `per_class_counts[vuln_class]` 时(~878)加:
```python
                _ts = track_status.get(vuln_class, {})
                per_class_counts[vuln_class]["gitnexus_status"] = _ts.get("status", "absent")
                if _ts.get("status") == "failed":
                    per_class_counts[vuln_class]["gitnexus_fail_reason"] = _ts.get("reason")
                    logger.info("merge: vuln=%s GitNexus track failed (%s) — 标红供报告,合并退 llm-only",
                                vuln_class, _ts.get("reason"))
```

(合并逻辑 867-875 不变——failed 类无 gitnexus queue,自然 `gitnexus_findings=[]`,llm 有则 llm-only、llm 无则 `continue` 跳过。)

- [ ] **Step 4: Run test → PASS**

Run: `cd /root/shannon-py && uv run pytest packages/whitebox/tests/test_merge_dual_track_status.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/tests/test_merge_dual_track_status.py
git commit -m "feat(merger): per_class_counts 记 gitnexus_status—failed 类标红供报告(合并逻辑不变)"
```

---

## Task 6: 报告标红(assemble_report 读状态产物注记)

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`(`assemble_report`,~996)
- Test: `packages/whitebox/tests/test_assemble_report_track_status.py`

**Interfaces:**
- Consumes: Task 1 `read_track_status`。
- Produces: `assemble_report` 在渲染含 failed 类的报告时,加注记"GitNexus 轨判定失败(reason),结果由 LLM 轨提供"。

- [ ] **Step 1: Write failing test**

```python
# packages/whitebox/tests/test_assemble_report_track_status.py
import json, pytest

@pytest.mark.asyncio
async def test_report_includes_gitnexus_failed_note(tmp_path, monkeypatch):
    from shannon_whitebox.pipeline import activities
    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, tmp_path, tmp_path))
    monkeypatch.setattr(activities, "get_audit_session", lambda: _FakeSession())
    (tmp_path / "gitnexus_track_status.json").write_text(
        json.dumps({"xss": {"status": "failed", "reason": "builder raised: KeyError"}}), encoding="utf-8")
    # ... 构造 assemble_report 所需的最小 deliverables(参照现有 assemble_report 测试)
    await activities.assemble_report(_ActInput())
    report = (tmp_path / "<report-filename>").read_text(encoding="utf-8")
    assert "GitNexus 轨判定失败" in report
    assert "xss" in report
```

> 先读 `assemble_report`(`activities.py:996`)的现有实现 + 现有报告测试,确定 report 文件名与渲染注入点(通常在 vuln 章节汇总处),再补测试构造。

- [ ] **Step 2: Run test → FAIL**

Run: `cd /root/shannon-py && uv run pytest packages/whitebox/tests/test_assemble_report_track_status.py -v`
Expected: FAIL(报告无 GitNexus 失败注记)

- [ ] **Step 3: Modify assemble_report**

先 Read `assemble_report`(`activities.py:996-`)确定 vuln 章节渲染处与 report 输出路径。在渲染前读状态产物,若有 failed 类,在报告对应位置(章节头部或报告顶部摘要)注入注记:

```python
        from shannon_core.code_index.gitnexus_track_status import read_track_status
        track_status = read_track_status(deliverables)
        failed_notes = [
            f"- {vc}: GitNexus 轨判定失败({s.get('reason', 'unknown')}),结果由 LLM 轨提供"
            for vc, s in track_status.items() if s.get("status") == "failed"
        ]
        if failed_notes:
            # 注入到报告(具体注入点据 assemble_report 现有渲染结构调整):
            # 例如拼接到 vuln 汇总章节头部,或报告顶部 "Scan Notes" 摘要。
            gitnexus_banner = "## GitNexus 轨判定状态\n\n" + "\n".join(failed_notes) + "\n"
            # ... 将 gitnexus_banner 并入报告渲染输出
```

(注入点:优先并入报告已有的"扫描说明/摘要"区块;若无,在 vuln 章节前插一节。执行者据当前 assemble_report 结构选最小侵入点。)

- [ ] **Step 4: Run test → PASS**

Run: `cd /root/shannon-py && uv run pytest packages/whitebox/tests/test_assemble_report_track_status.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/tests/test_assemble_report_track_status.py
git commit -m "feat(report): assemble_report 读 gitnexus_track_status 标红 failed 类注记"
```

---

## Task 7: 铁律不变量测试(track_status 不喂 LLM 轨)

**Files:**
- Test: `packages/core/tests/code_index/test_track_status_decoupling.py`

**Interfaces:**
- Produces: AST/grep 锁定 `gitnexus_track_status` 不被 LLM 轨 prompt partial / vuln collector / LLM 轨 agent import。

- [ ] **Step 1: Write test**

```python
# packages/core/tests/code_index/test_track_status_decoupling.py
"""铁律:gitnexus_track_status 只给 workflow/merger/report 编排用,
绝不喂 LLM 轨 prompt / 不被 vuln collector 或 LLM 轨 agent import(守 CLAUDE.md §1)。"""
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
LLM_TRACK_DOMAINS = [
    "packages/core/src/shannon_core/collectors",   # vuln collector(LLM 轨 set_* 工具)
    "packages/core/src/shannon_core/renderers",     # LLM 轨 deliverable 渲染
    "prompts",                                       # LLM 轨 prompt(含 partial)
]
ALLOWED = {"pipeline/workflows.py", "pipeline/activities.py",
           "code_index/gitnexus_track_status.py", "code_index/dual_track_merger.py"}

def _python_files(root: Path):
    return [p for p in root.rglob("*.py") if p.is_file()]

def test_track_status_not_imported_in_llm_track():
    bad = []
    for domain in LLM_TRACK_DOMAINS:
        d = REPO / domain
        if not d.exists():
            continue
        for py in _python_files(d):
            rel = py.relative_to(REPO).as_posix()
            if rel in ALLOWED:
                continue
            txt = py.read_text(encoding="utf-8", errors="ignore")
            if "gitnexus_track_status" in txt or "track_statuses" in txt:
                bad.append(rel)
    assert not bad, f"铁律违反:gitnexus_track_status 泄漏进 LLM 轨域 {bad}"

def test_track_status_not_in_prompts():
    prompts_dir = REPO / "prompts"
    if not prompts_dir.exists():
        return
    bad = []
    for p in prompts_dir.rglob("*.txt"):
        if "gitnexus_track_status" in p.read_text(encoding="utf-8", errors="ignore"):
            bad.append(str(p.relative_to(REPO)))
    assert not bad, f"铁律违反:track_status 出现在 LLM 轨 prompt {bad}"
```

- [ ] **Step 2: Run test → verify PASS(不变量当前应成立)**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_track_status_decoupling.py -v`
Expected: PASS(若 FAIL,说明前序 task 误把 track_status 引入 collector/renderer/prompt,需回退修正)

- [ ] **Step 3: Commit**

```bash
git add packages/core/tests/code_index/test_track_status_decoupling.py
git commit -m "test(decoupling): 锁定 gitnexus_track_status 不喂 LLM 轨(铁律不变量)"
```

---

## 风险 / 注意

- **Task 4 workflow 测试框架**:参照现有 `tests/test_*workflow*.py` 的 activity mock 模式;若 temporal workflow 单测在该分支有预存问题,改用活动级 mock + `is_llm_track_enabled` monkeypatch。
- **`workflows.py` 并行改动**:`workflows.py` 当前有其他终端的在途改动(source/sink 相关)。执行前先 `git log workflows.py` + Read 当前内容,本 task 改动聚焦 GitNexus 编排段(~382-430),避免冲突;merge 时按上下文适配行号。
- **`ActivityInput` 扩展**:`write_track_status_activity` 传 `track_statuses`——若 `ActivityInput` 是严格 dataclass,加可选字段 `track_statuses: dict = field(default_factory=dict)`(全局约束:不改 LLM 轨路径,此字段仅 workflow→activity)。
- **关轨行为变化(期望,非回归)**:关轨 + GitNexus 不稳 → 扫描会 fail-fast 终止(诚实暴露),需告知用户这是设计意图。
- **真机冒烟**:全 7 task 绿后,`SHANNON_LLM_TRACK_ENABLED=0` 跑一个小仓(NodeGoat):验证 **inj/xss/ssrf GitNexus fail → 扫描终止**;验证 **authz GitNexus fail → 不终止**(标红,authz-vuln LLM 兜底)。`SHANNON_LLM_TRACK_ENABLED=1` 验证所有 GitNexus fail → 标红报告。
