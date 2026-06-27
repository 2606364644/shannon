# GitNexus 轨生命周期完善 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 GitNexus 轨在真机真正跑完全流程——接通断裂的 activity 注册、修复接通后暴露的 logger NameError、让 auth scan 失败不中断、修 merger 丢弃 GitNexus-only、加可观测日志，使其与 LLM 轨一样端到端独立产出。

**Architecture:** 5 个定点 task，不重构：(1) worker.py 注册 2 个 activity + anchor 测试防复发；(2) activities.py 加模块级 logger 修复接通后暴露的 NameError；(3) workflows.py 让 run_auth_config_scan 对齐 non-fatal；(4) activities.py merger 在 LLM queue 缺席时仍并入 GitNexus-only；(5) 加诊断日志。全程 follow 现有 anchor test / merger test 模式。

**Tech Stack:** Python 3, temporalio, pytest (asyncio), pydantic。

## Global Constraints

- **只跑改动相关测试文件，勿跑全套**（pytest 全量会 hang，见 memory `pytest-whitebox-hang`）。每个 task 给了精确的 pytest 命令。
- commit message 遵循项目 conventional 风格（`scope: 描述`），每个 task 末尾 commit。
- follow 现有代码模式：worker anchor test = 读 `worker.py` 源码 `src.count(name) >= 2`；merger activity test = `_RecordingSession` mock + `monkeypatch._get_paths` + 写 deliverables JSON。
- **`externally_exploitable` 不被 verdict 覆写**（双轨铁律，`dual_track_merger.py:52-57`）——本次任何改动不得违反。
- 分支：`feat/fork-py`。
- spec 文档：`docs/superpowers/specs/2026-06-27-gitnexus-track-lifecycle-completion-design.md`。

## File Structure

| 文件 | 责任 | 改动 |
|---|---|---|
| `packages/whitebox/src/shannon_whitebox/worker.py` | Temporal worker activity 注册 | import(13-34) + activities(93-102) 各加 2 activity |
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | activity 实现 | 加模块级 logger；merger 循环 A4 改；GitNexus-only 日志 |
| `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | workflow 编排 | auth scan non-fatal；chain verdict/auth scan 成功日志 |
| `packages/whitebox/tests/test_worker_registers_gitnexus_verdict.py` | 新建 | worker 注册 anchor |
| `packages/whitebox/tests/test_activities_module_logger.py` | 新建 | 模块级 logger anchor |
| `packages/whitebox/tests/test_workflows_auth_scan_nonfatal.py` | 新建 | auth scan non-fatal source anchor |
| `packages/whitebox/tests/test_run_merge_dual_track.py` | 扩展 | A4 场景 + GitNexus-only 日志测试 |

---

## Task 1: 接通 worker 注册（run_gitnexus_chain_verdict + run_auth_config_scan）

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/worker.py`（import 块 13-34 + activities 列表 93-102）
- Test: `packages/whitebox/tests/test_worker_registers_gitnexus_verdict.py`（新建）

**Interfaces:**
- Consumes: `run_gitnexus_chain_verdict`（activities.py:730）、`run_auth_config_scan`（activities.py:879）——均已定义。
- Produces: 两 activity 在 worker 注册，`workflow.execute_activity(...)` 可被 worker dispatch；下游 `<vuln>_gitnexus_queue.json` / `auth_gitnexus_queue.json` 真产出。

- [ ] **Step 1: 写失败 anchor test**

新建 `packages/whitebox/tests/test_worker_registers_gitnexus_verdict.py`：

```python
"""Regression anchor: GitNexus-track chain verdict + auth config scan activities
must be registered with the Temporal worker (define/call/register 3-point sync).
A unit test patches activities so they never exercise real dispatch; this
source-level check is the only thing that catches a missing registration before
a real run silently no-ops the GitNexus track (the df33ec5 bug)."""
from pathlib import Path


def test_gitnexus_chain_verdict_registered_in_worker():
    worker = Path(__file__).resolve().parents[1] / "src/shannon_whitebox/worker.py"
    src = worker.read_text()
    # Must appear in BOTH the import block AND the activities=[...] list.
    assert src.count("run_gitnexus_chain_verdict") >= 2, (
        "run_gitnexus_chain_verdict must be imported AND listed in worker.py activities"
    )


def test_auth_config_scan_registered_in_worker():
    worker = Path(__file__).resolve().parents[1] / "src/shannon_whitebox/worker.py"
    src = worker.read_text()
    assert src.count("run_auth_config_scan") >= 2, (
        "run_auth_config_scan must be imported AND listed in worker.py activities"
    )
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest packages/whitebox/tests/test_worker_registers_gitnexus_verdict.py -v`
Expected: FAIL（当前两 activity 各 count=0 < 2）

- [ ] **Step 3: 注册两 activity 到 worker.py**

在 import 块（`worker.py:13-34`）加入两行。`run_auth_config_scan` 放在 `run_auth_validation,`（行 17）之前；`run_gitnexus_chain_verdict` 放在 `run_frontend_mapping,`（行 30）之后、`run_route_chain_building,` 之前。修改后 import 块含：

```python
from .pipeline.activities import (
    render_findings,
    assemble_report,
    run_agent,
    run_auth_config_scan,
    run_auth_validation,
    run_authz_gitnexus_judge,
    run_code_index,
    run_credential_check,
    run_merge_dual_track_queues,
    run_merge_sink_reports,
    run_entry_point_fusion,
    run_preflight,
    run_risk_scoring,
    run_save_adjudication,
    run_vuln_agent,
    run_attack_chain_assembly,
    run_framework_analysis,
    run_frontend_mapping,
    run_gitnexus_chain_verdict,
    run_route_chain_building,
    log_phase_start_activity,
    log_phase_complete_activity,
)
```

在 activities 列表（`worker.py:93-102`）同步加入这两个名字（位置任意，建议同样顺序）：

```python
        activities=[
            render_findings, assemble_report, run_agent, run_auth_validation,
            run_auth_config_scan,
            run_authz_gitnexus_judge, run_code_index,
            run_credential_check, run_merge_dual_track_queues,
            run_merge_sink_reports, run_entry_point_fusion,
            run_preflight, run_risk_scoring,
            run_save_adjudication, run_vuln_agent, run_attack_chain_assembly,
            run_framework_analysis, run_frontend_mapping,
            run_gitnexus_chain_verdict,
            run_route_chain_building,
            log_phase_start_activity, log_phase_complete_activity,
        ],
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest packages/whitebox/tests/test_worker_registers_gitnexus_verdict.py -v`
Expected: PASS（两 activity 各 count>=2）

- [ ] **Step 5: 回归——现有 worker anchor test 不破坏**

Run: `pytest packages/whitebox/tests/test_worker_registers_authz_judge.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/worker.py packages/whitebox/tests/test_worker_registers_gitnexus_verdict.py
git commit -m "fix(whitebox): register run_gitnexus_chain_verdict + run_auth_config_scan in worker"
```

---

## Task 2: 修复 activities.py 模块级 logger NameError（接通配套）

**背景:** Task 1 接通后 `run_gitnexus_chain_verdict` 真跑。其内 `activities.py:780` 与 `:801` 调用 `logger.warning(...)`，但该函数作用域内**无 `logger` 定义**（模块级也无；`activities.py:342` 的 `logger` 是 `run_code_index` 的局部变量，不跨函数）。故 `code_index.json` 解析失败（780）或某 vuln class builder 抛错（801）时触发 `NameError` → 一个 class 失败整批中断，违背 per-class 隔离设计。本 task 是接通的必要配套。

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`（顶部 imports 1-4 + 模块级 logger 定义，置于 imports 之后、`_get_paths`(26) 之前）
- Test: `packages/whitebox/tests/test_activities_module_logger.py`（新建）

**Interfaces:**
- Produces: 模块级 `logger = logging.getLogger(__name__)`，被 activities.py 内所有 `logger.warning/info` 引用（含 780/801）。

- [ ] **Step 1: 写失败 test**

新建 `packages/whitebox/tests/test_activities_module_logger.py`：

```python
"""Anchor: activities 模块必须有模块级 logger。
run_gitnexus_chain_verdict 的错误降级路径（code_index.json parse 失败 @780、
per-class builder 失败 @801）调用 logger.warning；若无模块级 logger 会 NameError，
导致接通后一个 class 失败整批中断（违背 per-class 隔离设计）。"""
import logging

from shannon_whitebox.pipeline import activities


def test_activities_has_module_logger():
    assert hasattr(activities, "logger"), (
        "activities 模块必须定义模块级 logger（run_gitnexus_chain_verdict 的 "
        "logger.warning 降级路径依赖它）"
    )
    assert isinstance(activities.logger, logging.Logger)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest packages/whitebox/tests/test_activities_module_logger.py -v`
Expected: FAIL（`hasattr(activities, "logger")` 为 False）

- [ ] **Step 3: 加模块级 logger**

`activities.py:1-4` 现有：
```python
import json
import time
from datetime import timedelta
from pathlib import Path
```
改为（加 `import logging`，字母序置于 `import json` 后、`import time` 前）：
```python
import json
import logging
import time
from datetime import timedelta
from pathlib import Path
```

在 imports 块结尾（`from .step_intents import intent_for` 之后，`def _get_paths` 之前）加模块级 logger：

```python

logger = logging.getLogger(__name__)
```

（注：`activities.py:342` `run_code_index` 内的局部 `logger = logging.getLogger(__name__)` **保留不动**——它 shadow 模块级、值相同、无害；最小改动不碰 `run_code_index`。）

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest packages/whitebox/tests/test_activities_module_logger.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/tests/test_activities_module_logger.py
git commit -m "fix(whitebox): add module-level logger to activities (NameError on gitnexus verdict degradation)"
```

---

## Task 3: run_auth_config_scan 对齐 non-fatal（workflows.py）

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py:291-296`
- Test: `packages/whitebox/tests/test_workflows_auth_scan_nonfatal.py`（新建，source-level anchor）

**Interfaces:**
- Consumes: `run_auth_config_scan` 已注册（Task 1）。
- Produces: auth scan 失败只 warning 降级，不中断 vulnerability-analysis 阶段（与 `run_gitnexus_chain_verdict` / `run_authz_gitnexus_judge` 一致）。

- [ ] **Step 1: 写失败 source anchor test**

Temporal workflow 集成测试过重，用 source-level anchor 防 try/except 回退。新建 `packages/whitebox/tests/test_workflows_auth_scan_nonfatal.py`：

```python
"""Anchor: run_auth_config_scan 的 workflow 调用必须被 try/except 包裹（non-fatal），
与同轨 run_authz_gitnexus_judge / run_gitnexus_chain_verdict 一致。
df33ec5 时它无 try/except，失败会中断整个 vulnerability-analysis 阶段。"""
from pathlib import Path


def test_auth_config_scan_call_is_non_fatal():
    wf = Path(__file__).resolve().parents[1] / "src/shannon_whitebox/pipeline/workflows.py"
    src = wf.read_text()
    idx = src.find("activities.run_auth_config_scan")
    assert idx != -1, "run_auth_config_scan 调用未找到"
    # 调用前 200 字符内应有 try:；调用后 400 字符内应有 warning 标注 non-fatal
    before = src[max(0, idx - 200):idx]
    after = src[idx:idx + 400]
    assert "try:" in before, (
        "run_auth_config_scan 调用必须包裹在 try: 中（non-fatal），"
        "与 run_authz_gitnexus_judge / run_gitnexus_chain_verdict 一致"
    )
    assert "Auth config scan failed" in after, (
        "run_auth_config_scan 失败应有 warning 日志"
    )
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest packages/whitebox/tests/test_workflows_auth_scan_nonfatal.py -v`
Expected: FAIL（当前无 try/except）

- [ ] **Step 3: 加 try/except（workflows.py:291-296）**

现有：
```python
            if "auth" in [str(vt) for vt in selected_classes]:
                await workflow.execute_activity(
                    activities.run_auth_config_scan, act_input,
                    start_to_close_timeout=timedelta(minutes=3),
                    retry_policy=retry_for("standard"),
                )
```
改为（照搬 `run_authz_gitnexus_judge`@338-347 模式）：
```python
            if "auth" in [str(vt) for vt in selected_classes]:
                try:
                    await workflow.execute_activity(
                        activities.run_auth_config_scan, act_input,
                        start_to_close_timeout=timedelta(minutes=3),
                        retry_policy=retry_for("standard"),
                    )
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).warning(
                        "Auth config scan failed (non-fatal, auth track degrades to LLM-only): %s", exc)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest packages/whitebox/tests/test_workflows_auth_scan_nonfatal.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/workflows.py packages/whitebox/tests/test_workflows_auth_scan_nonfatal.py
git commit -m "fix(whitebox): make run_auth_config_scan non-fatal in workflow (align with chain verdict)"
```

---

## Task 4: A4 merger 独立产出（LLM queue 缺席仍并入 GitNexus-only）

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:534-573`（`run_merge_dual_track_queues` 循环）
- Test: `packages/whitebox/tests/test_run_merge_dual_track.py`（追加 A4 场景）

**Interfaces:**
- Consumes: `merge_dual_track_queues`（既有纯函数，已支持 `gitnexus-only` 分支）。
- Produces: LLM queue 缺席时，GitNexus-only 发现仍写回 `<vuln>_exploitation_queue.json` 进报告（`merge_source="gitnexus-only"` / `confidence="needs_review"` / `externally_exploitable` 取 GitNexus 轨值）。

- [ ] **Step 1: 写失败 test（A4 核心场景）**

追加到 `packages/whitebox/tests/test_run_merge_dual_track.py`：

```python
@pytest.mark.asyncio
async def test_merge_keeps_gitnexus_only_when_llm_queue_absent(tmp_path, monkeypatch):
    """A4: LLM queue 缺席时，GitNexus-only 发现仍并入报告（真兜底）。
    df33ec5 时此场景 continue 跳过，GitNexus 产物被丢。"""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    # 注意：不写 injection_exploitation_queue.json（LLM 轨缺席）
    (deliverables / "injection_gitnexus_queue.json").write_text(
        json.dumps(
            {
                "vulnerabilities": [
                    {
                        "ID": "G1",
                        "vulnerability_type": "injection",
                        "externally_exploitable": True,
                        "confidence": "high",
                        "verdict": "vulnerable",
                        "source": "q",
                        "sink_call": "db.exec",
                        "evidence_chain": "q -> db.exec(L42)",
                    }
                ]
            }
        )
    )

    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    set_audit_session(_RecordingSession())
    try:
        result = await activities.run_merge_dual_track_queues(_input(tmp_path))
    finally:
        clear_audit_session()

    assert "injection" in result["merged_classes"]
    out = json.loads((deliverables / "injection_exploitation_queue.json").read_text())
    assert len(out["vulnerabilities"]) == 1
    v = out["vulnerabilities"][0]
    assert v["merge_source"] == "gitnexus-only"
    assert v["confidence"] == "needs_review"
    assert v["externally_exploitable"] is True  # 取 GitNexus 轨值，不被覆写
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest packages/whitebox/tests/test_run_merge_dual_track.py::test_merge_keeps_gitnexus_only_when_llm_queue_absent -v`
Expected: FAIL（当前 LLM 缺席 → `continue` → `"injection" not in merged_classes`）

- [ ] **Step 3: 改 merger 循环（activities.py:534-573）**

现有循环体：
```python
            for vuln_class in ("injection", "xss", "ssrf", "authz", "auth"):
                exploitation_path = deliverables / f"{vuln_class}_exploitation_queue.json"
                if not exploitation_path.exists():
                    continue

                llm_path = deliverables / f"{vuln_class}_llm_queue.json"
                llm_path.write_text(exploitation_path.read_text(encoding="utf-8"), encoding="utf-8")
                llm_parsed = VulnerabilityQueue.parse_lenient(llm_path.read_text(encoding="utf-8"))
                llm_findings = llm_parsed.queue.vulnerabilities

                gitnexus_findings = []
                gitnexus_path = deliverables / f"{vuln_class}_gitnexus_queue.json"
                if gitnexus_path.exists():
                    gitnexus_parsed = VulnerabilityQueue.parse_lenient(
                        gitnexus_path.read_text(encoding="utf-8")
                    )
                    gitnexus_findings = gitnexus_parsed.queue.vulnerabilities

                merged = merge_dual_track_queues(
                    llm_findings,
                    gitnexus_findings,
                    mode="verdict",
                )
                atomic_write_json(
                    exploitation_path,
                    {"vulnerabilities": [finding.model_dump() for finding in merged]},
                )

                merged_classes.append(vuln_class)
                per_class_counts[vuln_class] = {
                    "llm": len(llm_findings),
                    "gitnexus": len(gitnexus_findings),
                    "merged": len(merged),
                    "both": sum(1 for finding in merged if finding.merge_source == "both"),
                    "llm_only": sum(1 for finding in merged if finding.merge_source == "llm-only"),
                    "gitnexus_only": sum(
                        1 for finding in merged if finding.merge_source == "gitnexus-only"
                    ),
                    "warnings": llm_parsed.warnings,
                }
```
改为（A4：LLM 缺席用空 list，仅两轨都空才 continue）：
```python
            for vuln_class in ("injection", "xss", "ssrf", "authz", "auth"):
                exploitation_path = deliverables / f"{vuln_class}_exploitation_queue.json"
                gitnexus_path = deliverables / f"{vuln_class}_gitnexus_queue.json"

                # GitNexus-track findings (may exist independently of LLM track)
                gitnexus_findings = []
                if gitnexus_path.exists():
                    gitnexus_parsed = VulnerabilityQueue.parse_lenient(
                        gitnexus_path.read_text(encoding="utf-8")
                    )
                    gitnexus_findings = gitnexus_parsed.queue.vulnerabilities

                # LLM-track findings. A4: LLM queue absent -> empty list, still merge
                # (GitNexus-only must reach the report, not be dropped). Skip only
                # when BOTH tracks are empty.
                llm_findings = []
                llm_warnings = []
                if exploitation_path.exists():
                    llm_path = deliverables / f"{vuln_class}_llm_queue.json"
                    llm_path.write_text(exploitation_path.read_text(encoding="utf-8"), encoding="utf-8")
                    llm_parsed = VulnerabilityQueue.parse_lenient(llm_path.read_text(encoding="utf-8"))
                    llm_findings = llm_parsed.queue.vulnerabilities
                    llm_warnings = llm_parsed.warnings
                elif not gitnexus_findings:
                    continue  # both tracks empty

                merged = merge_dual_track_queues(
                    llm_findings,
                    gitnexus_findings,
                    mode="verdict",
                )
                atomic_write_json(
                    exploitation_path,
                    {"vulnerabilities": [finding.model_dump() for finding in merged]},
                )

                merged_classes.append(vuln_class)
                per_class_counts[vuln_class] = {
                    "llm": len(llm_findings),
                    "gitnexus": len(gitnexus_findings),
                    "merged": len(merged),
                    "both": sum(1 for finding in merged if finding.merge_source == "both"),
                    "llm_only": sum(1 for finding in merged if finding.merge_source == "llm-only"),
                    "gitnexus_only": sum(
                        1 for finding in merged if finding.merge_source == "gitnexus-only"
                    ),
                    "warnings": llm_warnings,
                }
```

- [ ] **Step 4: 跑新测试确认通过**

Run: `pytest packages/whitebox/tests/test_run_merge_dual_track.py::test_merge_keeps_gitnexus_only_when_llm_queue_absent -v`
Expected: PASS

- [ ] **Step 5: 回归——所有 merger 测试不破坏**

Run: `pytest packages/whitebox/tests/test_run_merge_dual_track.py -v`
Expected: 全 PASS。重点确认既有 4 个：
- `test_merge_writes_exploitation_queue_from_llm_only`（LLM-only 仍进 llm-only 分支）
- `test_merge_combines_both_tracks`（两轨都在 → both）
- `test_merge_skips_vuln_classes_with_no_llm_queue`（**两轨都无 → 仍 skip**，A4 保持）
- `test_merge_handles_invalid_llm_queue_leniently`（畸形 JSON 容错）

- [ ] **Step 6: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/tests/test_run_merge_dual_track.py
git commit -m "fix(whitebox): merger keeps gitnexus-only findings when LLM queue absent (A4 independent fallback)"
```

---

## Task 5: 可观测加固（成功日志 + GitNexus-only 并入日志）

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`（merger 循环内加 GitNexus-only 日志）
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`（chain verdict + auth scan 成功日志）
- Test: `packages/whitebox/tests/test_run_merge_dual_track.py`（追加 caplog 测试）

**Interfaces:**
- Produces: 诊断日志——`run_gitnexus_chain_verdict` / `run_auth_config_scan` 成功 + merger GitNexus-only 并入。对症「静默降级 → 没人发现没跑通」。

- [ ] **Step 1: 写失败 test（GitNexus-only 并入日志）**

追加到 `packages/whitebox/tests/test_run_merge_dual_track.py`：

```python
@pytest.mark.asyncio
async def test_merge_logs_gitnexus_only_findings(tmp_path, monkeypatch, caplog):
    """可观测: GitNexus-only 发现并入时打 info 日志（A4 生效的直接信号）。"""
    import logging
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "injection_gitnexus_queue.json").write_text(
        json.dumps(
            {"vulnerabilities": [{
                "ID": "G1", "vulnerability_type": "injection",
                "externally_exploitable": True, "confidence": "high",
                "verdict": "vulnerable", "source": "q", "sink_call": "db.exec",
            }]}
        )
    )
    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    set_audit_session(_RecordingSession())
    with caplog.at_level(logging.INFO):
        try:
            await activities.run_merge_dual_track_queues(_input(tmp_path))
        finally:
            clear_audit_session()
    assert any(
        "gitnexus-only" in r.getMessage() and "injection" in r.getMessage()
        for r in caplog.records
    ), "GitNexus-only 并入时应打 info 日志（含 vuln 类名）"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest packages/whitebox/tests/test_run_merge_dual_track.py::test_merge_logs_gitnexus_only_findings -v`
Expected: FAIL（当前无该日志）

- [ ] **Step 3: 加 merger GitNexus-only 日志（activities.py merger 循环内）**

在 Task 4 改后的循环内，`per_class_counts[vuln_class] = {...}` 赋值之后、循环体结束（下一次迭代）之前，插入：

```python
                gn_only = sum(1 for f in merged if f.merge_source == "gitnexus-only")
                if gn_only:
                    import logging
                    logging.getLogger(__name__).info(
                        "merge: vuln=%s merged %d gitnexus-only findings (LLM track did not cover)",
                        vuln_class, gn_only)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest packages/whitebox/tests/test_run_merge_dual_track.py::test_merge_logs_gitnexus_only_findings -v`
Expected: PASS

- [ ] **Step 5: 加 chain verdict / auth scan 成功日志（workflows.py）**

**chain verdict**（`workflows.py:354-363`，Task 1 后未变）现有：
```python
            try:
                await workflow.execute_activity(
                    activities.run_gitnexus_chain_verdict, act_input,
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=retry_for("standard"),
                )
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning(
                    "GitNexus chain verdict failed (non-fatal, LLM-only track continues): %s", exc)
```
改为（捕获 result + 成功 info 日志）：
```python
            try:
                _gn_verdict = await workflow.execute_activity(
                    activities.run_gitnexus_chain_verdict, act_input,
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=retry_for("standard"),
                )
                workflow.logger.info("GitNexus chain verdict ok: %s", _gn_verdict.get("per_class", {}))
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning(
                    "GitNexus chain verdict failed (non-fatal, LLM-only track continues): %s", exc)
```

**auth scan**（Task 3 改后已有 try/except）现有：
```python
            if "auth" in [str(vt) for vt in selected_classes]:
                try:
                    await workflow.execute_activity(
                        activities.run_auth_config_scan, act_input,
                        start_to_close_timeout=timedelta(minutes=3),
                        retry_policy=retry_for("standard"),
                    )
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).warning(
                        "Auth config scan failed (non-fatal, auth track degrades to LLM-only): %s", exc)
```
改为（捕获 result + 成功 info 日志）：
```python
            if "auth" in [str(vt) for vt in selected_classes]:
                try:
                    _auth_scan = await workflow.execute_activity(
                        activities.run_auth_config_scan, act_input,
                        start_to_close_timeout=timedelta(minutes=3),
                        retry_policy=retry_for("standard"),
                    )
                    workflow.logger.info("Auth config scan ok: %s findings", _auth_scan.get("total_findings", 0))
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).warning(
                        "Auth config scan failed (non-fatal, auth track degrades to LLM-only): %s", exc)
```

- [ ] **Step 6: 回归——相关测试不破坏**

Run: `pytest packages/whitebox/tests/test_run_merge_dual_track.py packages/whitebox/tests/test_workflows_auth_scan_nonfatal.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/workflows.py packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/tests/test_run_merge_dual_track.py
git commit -m "feat(whitebox): GitNexus track observability (success + gitnexus-only merge logs)"
```

---

## Self-Review

**1. Spec coverage（逐条对照 spec §4）：**
- §4.1 接通注册 → Task 1 ✅
- §4.2 run_auth_config_scan 对齐 non-fatal → Task 3 ✅
- §4.3 A4 merger → Task 4 ✅
- §4.4.1 chain verdict / auth scan 成功日志 → Task 5 ✅
- §4.4.2 GitNexus-only 并入日志 → Task 5 ✅
- §4.4.3 worker anchor test → Task 1 ✅
- §7 测试矩阵 → 分散在各 task（worker anchor / module logger anchor / non-fatal source anchor / A4 case 1-3 / caplog 日志）✅
- §8 logger NameError → **Task 2**（spec 列为 follow-up，plan 阶段亲验发现接通后立即暴露、属接通必要配套，故纳入；已在 Task 2 背景说明）

**2. Placeholder scan：** 无 TBD/TODO/"add error handling" 等占位；每个代码 step 含完整代码。

**3. Type consistency：** `logger`（模块级，Task 2 定义，Task 5 引用一致）；`_gn_verdict` / `_auth_scan`（Task 5 workflows 局部）；`gn_only`（Task 5 merger 局部）；`llm_warnings`（Task 4 引入，per_class_counts 消费）；`exploitation_path` / `gitnexus_path` / `merged` 跨 step 一致。

**4. 顺序依赖：** Task 1（注册）→ Task 2（logger，接通配套）→ Task 3（auth scan non-fatal）→ Task 4（A4 merger）→ Task 5（可观测）。Task 5 Step 5 的 auth scan 代码基于 Task 3 已加 try/except 的版本，一致。

**5. spec §8 其余 follow-up（本次不做，plan 不含）：**
- `workflows.py:351-353` 过期注释（Plan 1）——未纳入，plan 不动该注释。
- A3 失败隔离 / detect_language RE-1 / B 类架构演进——spec §2 显式排除。
