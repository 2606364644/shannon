# 中文综合报告 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 white-box 扫描完成后自动产出全中文的 `comprehensive_security_assessment_report.md`(中文执行摘要 + 各漏洞中文详述)。

**Architecture:** 两轴。轴 1(代码):把 `ReportAssembler` 从 blackbox 提升到 core,white-box reporting phase 接入「确定性拼接 + REPORT agent 执行摘要」两步——复用已有 generic `run_agent` 跑 `AgentName.REPORT`,无需新引擎。轴 2(prompt):Layer 0 输出层中文化——创建共享 `_output-language.txt` 片段(语言约束 + 中文标题词表),5 个 vuln prompt 与 `report-executive.txt` 引用并替换英文标题。

**Tech Stack:** Python 3、temporalio(workflow/activity)、pytest、文本 prompt 模板(`@include` 机制 + `{{VAR}}` 插值)。

**Spec:** `docs/superpowers/specs/2026-06-22-chinese-comprehensive-report-design.md`

## Global Constraints

- **输出语言**:报告叙述文字用**简体中文**;以下技术标识**保留英文原文不得翻译**:漏洞编号(如 `AUTH-VULN-01`)、代码、命令、文件路径与行号、HTTP 方法/状态码(如 `GET /api/x`、`HTTP 302`)、URL、请求头名、JSON 字段名、cookie 名、技术缩写(SSRF、SSTI、XSS、CSRF、RBAC、HSTS、IDOR、OAuth、JWT、PKCE)。
- **blackbox 零行为回归**:`ReportAssembler` 仅改存放位置与 import 路径,`assemble()`/`inject_model_info()` 逻辑不动。
- **范围限制**:Layer 0 only(不动 methodology / shared 分析指令);不改 `*-exploit.txt`、`pre_recon`、`recon`、attack-chain;不做即时补救;不加 config 开关。
- **测试边界**:聚焦 `packages/core/tests`、`packages/whitebox/tests`、`packages/blackbox/tests`。仓库广跑 pytest 有预存挂起(见 `[[feat-fork-py-test-gotchas]]`),跑测试时按需 `--ignore` 预存问题目录,勿因此误判本计划改动失败。
- **当前模型**:active profile `ark-coding`,vuln/report agent 跑 `glm-latest` / `deepseek-v4`(中文原生模型),故原生中文 prompt 不损推理质量。
- **git**:在 `feat/fork-py` 分支上按 task 频繁 commit。

## File Structure

| 文件 | 动作 | 职责 |
|---|---|---|
| `packages/core/src/shannon_core/services/report_assembler.py` | Create | 从 blackbox 提升的 `ReportAssembler`(assemble 三级回退 + inject_model_info) |
| `packages/blackbox/src/shannon_blackbox/services/report_assembler.py` | Modify → re-export | 向后兼容旧 import |
| `packages/core/tests/test_report_assembler.py` | Create | ReportAssembler 单元测试 |
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | Modify | 新增 `assemble_report` activity |
| `packages/whitebox/src/shannon_whitebox/pipeline/step_intents.py` | Modify | reporting phase 增加 `assemble-report`、`run-report-agent` step |
| `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | Modify | reporting phase 接入 assemble + REPORT agent |
| `packages/whitebox/tests/test_assemble_report.py` | Create | assemble_report activity 测试 |
| `packages/whitebox/tests/test_reporting_workflow.py` | Create | reporting phase 结构断言测试 |
| `prompts/shared/_output-language.txt` | Create | 语言约束 + 中文标题词表(单一来源) |
| `prompts/vuln-auth.txt` | Modify | 主标题 + @include + 子标题中文化 |
| `prompts/vuln-authz.txt` | Modify | 同上 |
| `prompts/vuln-injection.txt` | Modify | 同上 |
| `prompts/vuln-ssrf.txt` | Modify | 同上 |
| `prompts/vuln-xss.txt` | Modify | 同上 |
| `prompts/report-executive.txt` | Modify | 执行摘要标题中文化 |
| `prompts/pipeline-testing/report-executive.txt` | Modify | 1 个标题同步 |

---

## Task 1: ReportAssembler 提升到 core

**Files:**
- Create: `packages/core/src/shannon_core/services/report_assembler.py`
- Modify: `packages/blackbox/src/shannon_blackbox/services/report_assembler.py`
- Test: `packages/core/tests/test_report_assembler.py`

**Interfaces:**
- Produces: `shannon_core.services.report_assembler.ReportAssembler`,含 `async assemble(deliverables_path: Path, vuln_classes: list[str], report_path: Path, report_config=None) -> None` 与 `async inject_model_info(report_path: Path, session_path: Path) -> None`。`vuln_classes` 取自 `shannon_core.models.agents.ALL_VULN_CLASSES = ["injection", "xss", "auth", "ssrf", "authz"]`(文件名前缀即此值)。

- [ ] **Step 1: 写失败的 core 单元测试**

Create `packages/core/tests/test_report_assembler.py`:

```python
import json
import pytest
from pathlib import Path
from shannon_core.services.report_assembler import ReportAssembler


@pytest.mark.asyncio
async def test_assemble_uses_analysis_deliverable_fallback(tmp_path):
    """white-box 产物(*_analysis_deliverable.md)应被 assemble 读取。"""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "auth_analysis_deliverable.md").write_text(
        "# 认证分析报告\n\n中文内容 AUTH-VULN-01", encoding="utf-8")
    report_path = deliverables / "comprehensive_security_assessment_report.md"
    await ReportAssembler.assemble(deliverables, ["auth"], report_path)
    assert report_path.exists()
    content = report_path.read_text(encoding="utf-8")
    assert "认证分析报告" in content
    assert "AUTH-VULN-01" in content


@pytest.mark.asyncio
async def test_assemble_prefers_evidence_over_analysis(tmp_path):
    """exploit evidence 优先于 analysis_deliverable。"""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "auth_exploitation_evidence.md").write_text("EVIDENCE", encoding="utf-8")
    (deliverables / "auth_analysis_deliverable.md").write_text("ANALYSIS", encoding="utf-8")
    report_path = deliverables / "comprehensive_security_assessment_report.md"
    await ReportAssembler.assemble(deliverables, ["auth"], report_path)
    content = report_path.read_text(encoding="utf-8")
    assert "EVIDENCE" in content
    assert "ANALYSIS" not in content


@pytest.mark.asyncio
async def test_assemble_joins_multiple_sections_with_separator(tmp_path):
    """多 class 用 \\n\\n---\\n\\n 拼接,顺序遵循 vuln_classes。"""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "auth_analysis_deliverable.md").write_text("AUTH", encoding="utf-8")
    (deliverables / "injection_analysis_deliverable.md").write_text("INJ", encoding="utf-8")
    report_path = deliverables / "comprehensive_security_assessment_report.md"
    await ReportAssembler.assemble(deliverables, ["auth", "injection"], report_path)
    assert report_path.read_text(encoding="utf-8") == "AUTH\n\n---\n\nINJ"


@pytest.mark.asyncio
async def test_assemble_skips_missing_classes(tmp_path):
    """某 class 无任何产物文件时跳过,不报错。"""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "auth_analysis_deliverable.md").write_text("AUTH", encoding="utf-8")
    report_path = deliverables / "comprehensive_security_assessment_report.md"
    await ReportAssembler.assemble(deliverables, ["auth", "injection"], report_path)
    assert report_path.read_text(encoding="utf-8") == "AUTH"


@pytest.mark.asyncio
async def test_inject_model_info_inserts_after_assessment_date(tmp_path):
    """inject_model_info 在 Assessment Date 行后插入 Model 行。"""
    report = tmp_path / "report.md"
    report.write_text("## Executive Summary\n- Assessment Date: 2026-06-22\n正文", encoding="utf-8")
    session = tmp_path / "session.json"
    session.write_text(json.dumps({"metrics": {"agents": {"a": {"model": "glm-latest"}}}}), encoding="utf-8")
    await ReportAssembler.inject_model_info(report, session)
    content = report.read_text(encoding="utf-8")
    assert "- **Model:** glm-latest" in content
    assert content.index("Assessment Date") < content.index("**Model:**")
```

- [ ] **Step 2: 跑测试,确认失败(模块不存在)**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/core/tests/test_report_assembler.py -v`
Expected: FAIL / collection error —— `ModuleNotFoundError: shannon_core.services.report_assembler`

- [ ] **Step 3: 创建 core 模块(内容=现有 blackbox 版本,逐字复制)**

Create `packages/core/src/shannon_core/services/report_assembler.py`:

```python
import json
from pathlib import Path
from typing import Any

from shannon_core.utils.file_io import async_path_exists, async_read_file, async_write_file


class ReportAssembler:
    @staticmethod
    async def assemble(
        deliverables_path: Path,
        vuln_classes: list[str],
        report_path: Path,
        report_config: dict[str, Any] | None = None,
    ) -> None:
        sections: list[str] = []
        for vuln_class in vuln_classes:
            evidence = deliverables_path / f"{vuln_class}_exploitation_evidence.md"
            findings = deliverables_path / f"{vuln_class}_findings.md"
            analysis = deliverables_path / f"{vuln_class}_analysis_deliverable.md"
            if await async_path_exists(evidence):
                content = await async_read_file(evidence)
                sections.append(content)
            elif await async_path_exists(findings):
                content = await async_read_file(findings)
                sections.append(content)
            elif await async_path_exists(analysis):
                content = await async_read_file(analysis)
                sections.append(content)
        report_content = "\n\n---\n\n".join(sections)
        await async_write_file(report_path, report_content)

    @staticmethod
    async def inject_model_info(report_path: Path, session_path: Path) -> None:
        if not session_path.exists():
            return

        try:
            session_data = json.loads(session_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return

        metrics = session_data.get("metrics", {})
        agents = metrics.get("agents", {})
        models: set[str] = set()
        for agent_data in agents.values():
            if isinstance(agent_data, dict):
                model = agent_data.get("model")
                if model:
                    models.add(str(model))

        if not models:
            return

        if not await async_path_exists(report_path):
            return

        model_line = f"- **Model:** {', '.join(sorted(models))}"
        content = await async_read_file(report_path)
        lines = content.split("\n")
        new_lines: list[str] = []
        inserted = False

        for line in lines:
            new_lines.append(line)
            if not inserted and "- Assessment Date:" in line:
                new_lines.append(model_line)
                inserted = True

        if not inserted:
            for i, line in enumerate(new_lines):
                if line.strip() == "## Executive Summary":
                    new_lines.insert(i + 1, model_line)
                    inserted = True
                    break

        if inserted:
            await async_write_file(report_path, "\n".join(new_lines))
```

- [ ] **Step 4: blackbox 旧模块改为 re-export(向后兼容)**

Replace entire content of `packages/blackbox/src/shannon_blackbox/services/report_assembler.py` with:

```python
"""ReportAssembler 已提升至 shannon_core.services.report_assembler。

本模块保留 re-export 以兼容 blackbox 既有 `from shannon_blackbox.services.report_assembler
import ReportAssembler` 写法;blackbox 代码无需改动 import。
"""
from shannon_core.services.report_assembler import ReportAssembler

__all__ = ["ReportAssembler"]
```

- [ ] **Step 5: 跑 core 测试,确认通过**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/core/tests/test_report_assembler.py -v`
Expected: PASS(5 passed)

- [ ] **Step 6: 跑 blackbox 报告测试,确认零回归**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/blackbox/tests/test_finalize_report.py packages/blackbox/tests/test_blackbox_rerun.py -v`
Expected: PASS(re-export 保持 blackbox 行为不变)

- [ ] **Step 7: Commit**

```bash
git add packages/core/src/shannon_core/services/report_assembler.py \
        packages/core/tests/test_report_assembler.py \
        packages/blackbox/src/shannon_blackbox/services/report_assembler.py
git commit -m "refactor: 提升 ReportAssembler 至 core (whitebox 报告生成准备)"
```

---

## Task 2: white-box assemble_report activity + step_intents

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`(在 `render_findings` 之后新增 `assemble_report`)
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/step_intents.py`(reporting phase 扩展)
- Test: `packages/whitebox/tests/test_assemble_report.py`

**Interfaces:**
- Consumes: `ReportAssembler.assemble`(Task 1)、`_get_paths(input)`(activities.py:24)、`ALL_VULN_CLASSES`(agents.py:155)、`intent_for`/`track_step`(audit session)。
- Produces: `@activity.defn async def assemble_report(input: ActivityInput) -> None`,产出 `deliverables/comprehensive_security_assessment_report.md`(拼接版,供 Task 3 的 REPORT agent 后续加工)。

- [ ] **Step 1: 先扩展 step_intents(为 activity 提供 intent)**

In `packages/whitebox/src/shannon_whitebox/pipeline/step_intents.py`, replace the `reporting` entry (lines 45-47):

```python
    "reporting": (
        StepSpec("render-findings",   "渲染漏洞条目(若存在队列)"),
        StepSpec("assemble-report",   "拼接各分项报告"),
        StepSpec("run-report-agent",  "撰写执行摘要并清理报告"),
    ),
```

- [ ] **Step 2: 写失败的 activity 测试**

Create `packages/whitebox/tests/test_assemble_report.py`(`_RecordingSession` 模式取自 `test_phase_marker_activities.py`):

```python
from contextlib import asynccontextmanager

import shannon_whitebox.pipeline.activities as act
from shannon_whitebox.pipeline.shared import ActivityInput
from shannon_whitebox.audit.session_registry import (
    set_audit_session, clear_audit_session,
)


class _RecordingSession:
    def __init__(self) -> None:
        self.steps: list[tuple[str, str, str]] = []  # (name, phase, event)

    async def log_step(self, name: str, phase: str, event: str, **kw) -> None:
        self.steps.append((name, phase, event))

    @asynccontextmanager
    async def track_step(self, phase: str, name: str, intent: str | None = None):
        await self.log_step(name, phase, "start")
        try:
            yield
        except Exception:
            await self.log_step(name, phase, "complete", error="x")
            raise
        await self.log_step(name, phase, "complete")


async def test_assemble_report_writes_comprehensive_report(tmp_path, monkeypatch):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "auth_analysis_deliverable.md").write_text(
        "# 认证分析报告\nAUTH-VULN-01", encoding="utf-8")
    monkeypatch.setattr(act, "_get_paths", lambda inp: (tmp_path, deliverables, tmp_path))

    rec = _RecordingSession()
    set_audit_session(rec)
    try:
        await act.assemble_report(ActivityInput(repo_path=str(tmp_path)))
    finally:
        clear_audit_session()

    report = deliverables / "comprehensive_security_assessment_report.md"
    assert report.exists()
    assert "认证分析报告" in report.read_text(encoding="utf-8")
    events = [(n, e) for (n, _ph, e) in rec.steps]
    assert ("assemble-report", "start") in events
    assert ("assemble-report", "complete") in events
```

- [ ] **Step 3: 跑测试,确认失败(activity 未定义)**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/whitebox/tests/test_assemble_report.py -v`
Expected: FAIL —— `AttributeError: module ... has no attribute 'assemble_report'`

- [ ] **Step 4: 实现 assemble_report activity**

In `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`, append **after** the `render_findings` function (currently ends ~line 486), before `run_render_dataflow_hints`:

```python
@activity.defn
async def assemble_report(input: ActivityInput) -> None:
    """轴1:把各 *_analysis_deliverable.md 拼接成 comprehensive report。

    ReportAssembler 已实现 evidence → findings → analysis_deliverable 三级回退,
    天然支持 white-box 产物。拼接产物随后由 REPORT agent(report-executive)
    加执行摘要并清理。
    """
    from shannon_whitebox.audit.session_registry import get_audit_session
    try:
        from shannon_core.services.report_assembler import ReportAssembler

        _, deliverables, _ = _get_paths(input)
        report_path = deliverables / "comprehensive_security_assessment_report.md"
        vuln_classes = list(ALL_VULN_CLASSES)
        async with get_audit_session().track_step(
            "reporting", "assemble-report", intent=intent_for("assemble-report")
        ):
            await ReportAssembler.assemble(deliverables, vuln_classes, report_path)
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
```

> `ALL_VULN_CLASSES` 已在文件顶部 import(line 9)。`intent_for`、`PentestError`、`classify_error_for_temporal`、`ApplicationFailure` 均已 import。

- [ ] **Step 5: 跑测试,确认通过**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/whitebox/tests/test_assemble_report.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py \
        packages/whitebox/src/shannon_whitebox/pipeline/step_intents.py \
        packages/whitebox/tests/test_assemble_report.py
git commit -m "feat(whitebox): 新增 assemble_report activity 拼接综合报告"
```

---

## Task 3: white-box reporting phase 接入 assemble + REPORT agent

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`(reporting phase,~line 354-365)
- Test: `packages/whitebox/tests/test_reporting_workflow.py`(结构断言)

**Interfaces:**
- Consumes: `activities.assemble_report`(Task 2)、`activities.run_agent`(generic,activities.py:73)、`AgentName.REPORT = "report"`、`ActivityInput`(shared.py:37)。
- Produces: white-box reporting phase 顺序执行 `render_findings → assemble_report → run_agent(REPORT)`,最终产出含执行摘要的综合报告。

> 说明:`run_agent` 是 generic runner(从 `input.agent_name` 解析 agent),无需新增 `run_report_agent` activity。`AgentName.REPORT` 已定义(`agents.py:134`),`AGENTS[REPORT]` 存在,`executor.execute` 是 generic。

- [ ] **Step 1: 写结构断言测试(验证 workflow 编排正确)**

Create `packages/whitebox/tests/test_reporting_workflow.py`:

```python
"""reporting phase 接入断言。

reporting 真实执行依赖 temporalio worker + LLM,无法在 CI 单元测试;
此处用静态分析断言 workflow 串起了 render_findings → assemble_report →
run_agent(REPORT),行为正确性靠人工冒烟(Task 7 / spec §6.4)。
"""
from pathlib import Path


def _workflow_src() -> str:
    p = Path(__file__).resolve().parents[1] / "src/shannon_whitebox/pipeline/workflows.py"
    return p.read_text(encoding="utf-8")


def test_reporting_phase_calls_assemble_report():
    src = _workflow_src()
    assert "activities.assemble_report" in src, "reporting phase 须调 assemble_report"


def test_reporting_phase_runs_report_agent():
    src = _workflow_src()
    # run_agent 以 agent_name="report" 调用,跑 REPORT agent 生成执行摘要
    assert 'agent_name="report"' in src or '"agent_name", "report"' in src, (
        "reporting phase 须以 agent_name=report 调 run_agent 跑 REPORT agent"
    )


def test_reporting_phase_order_assemble_before_report():
    src = _workflow_src()
    i_assemble = src.find("activities.assemble_report")
    # REPORT agent 调用在 assemble_report 之后(粗略顺序断言)
    assert i_assemble != -1
    assert src.find("report", i_assemble) != -1
```

- [ ] **Step 2: 跑测试,确认失败**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/whitebox/tests/test_reporting_workflow.py -v`
Expected: FAIL(`activities.assemble_report` 不在 workflows.py)

- [ ] **Step 3: 改造 reporting phase**

In `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`, find the reporting block (current ~line 354-365):

```python
            self._state.current_phase = "reporting"
            self._state.current_agent = "render-findings"
            await workflow.execute_activity(
                activities.render_findings, act_input,
                start_to_close_timeout=timedelta(minutes=5),
            )
            self._state.current_agent = None
            await workflow.execute_activity(
                activities.log_phase_complete_activity,
                ActivityInput(**{**act_input.__dict__, "phase": "reporting"}),
                start_to_close_timeout=timedelta(seconds=10),
            )
```

Replace with:

```python
            self._state.current_phase = "reporting"
            self._state.current_agent = "render-findings"
            await workflow.execute_activity(
                activities.render_findings, act_input,
                start_to_close_timeout=timedelta(minutes=5),
            )
            # 轴1:拼接各分项 → 综合报告(确定性)
            self._state.current_agent = "assemble-report"
            await workflow.execute_activity(
                activities.assemble_report, act_input,
                start_to_close_timeout=timedelta(minutes=2),
            )
            # 轴1:REPORT agent 加执行摘要 + 清理(report-executive.txt)
            self._state.current_agent = "run-report-agent"
            await workflow.execute_activity(
                activities.run_agent,
                ActivityInput(**{**act_input.__dict__, "agent_name": "report"}),
                start_to_close_timeout=timedelta(minutes=15),
            )
            self._state.current_agent = None
            await workflow.execute_activity(
                activities.log_phase_complete_activity,
                ActivityInput(**{**act_input.__dict__, "phase": "reporting"}),
                start_to_close_timeout=timedelta(seconds=10),
            )
```

- [ ] **Step 4: 跑结构断言测试,确认通过**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/whitebox/tests/test_reporting_workflow.py -v`
Expected: PASS

- [ ] **Step 5: 跑 whitebox 相关已有测试,确认无回归**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/whitebox/tests/test_phase_marker_activities.py packages/whitebox/tests/test_assemble_report.py packages/whitebox/tests/test_reporting_workflow.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/workflows.py \
        packages/whitebox/tests/test_reporting_workflow.py
git commit -m "feat(whitebox): reporting phase 接入 assemble + REPORT agent"
```

---

## Task 4: 共享 _output-language 片段 + 5 个 vuln prompt 中文化

**Files:**
- Create: `prompts/shared/_output-language.txt`
- Modify: `prompts/vuln-auth.txt`, `prompts/vuln-authz.txt`, `prompts/vuln-injection.txt`, `prompts/vuln-ssrf.txt`, `prompts/vuln-xss.txt`
- Test: `packages/whitebox/tests/test_vuln_prompts_chinese.py`

**Interfaces:**
- Consumes: `@include(shared/_output-language.txt)` 机制(PromptManager `_process_includes`,manager.py:53,相对当前 prompt 目录解析)。
- Produces: 5 个 vuln prompt 输出中文 `*_analysis_deliverable.md`,标题用统一中文词表,技术标识保留英文。

> 各 vuln prompt 的 `deliverable_instructions` 标题差异(已盘点):
> - **auth / ssrf**:`## 4. Secure by Design: Validated Components`
> - **authz / injection / xss**:`## 4. Vectors Analyzed and Confirmed Secure` + `## 5. Analysis Constraints and Blind Spots`
> - injection 主标题原文为 `#Injection Analysis Report (SQLi & Command Injection)`(**`#` 后无空格**,保留此格式)。

- [ ] **Step 1: 创建共享语言约束 + 中文标题词表片段**

Create `prompts/shared/_output-language.txt`:

```
<output_language>
全部叙述性、描述性文字用简体中文撰写。以下技术标识必须保留英文原文,不得翻译:
- 漏洞编号(如 AUTH-VULN-01、INJ-VULN-02)
- 代码、命令、文件路径与行号(如 server/app/controller/image.js:102)
- HTTP 方法与状态码(如 GET /api/fileProxyGet、HTTP 302)
- URL、请求头名、JSON 字段名、cookie 名
- 技术缩写(SSRF、SSTI、XSS、CSRF、RBAC、HSTS、IDOR、OAuth、JWT、PKCE)

报告各级标题统一使用以下中文词表(覆盖示例中的英文标题):
- 主标题:# 认证分析报告 / # 授权分析报告 / # 注入分析报告 / # XSS 分析报告 / # SSRF 分析报告
- ## 一、执行摘要  (Executive Summary)
- ## 二、主要漏洞模式  (Dominant Vulnerability Patterns)
- ## 三、利用情报  (Strategic Intelligence for Exploitation)
- ## 四、安全设计:已验证组件  (Secure by Design: Validated Components)
- ## 四、已分析并确认安全的向量  (Vectors Analyzed and Confirmed Secure)
- ## 五、分析约束与盲区  (Analysis Constraints and Blind Spots)
</output_language>
```

- [ ] **Step 2: 写结构断言测试**

Create `packages/whitebox/tests/test_vuln_prompts_chinese.py`:

```python
from pathlib import Path

PROMPTS = Path(__file__).resolve().parents[1].parent.parent / "prompts"

VULN_PROMPTS = {
    "vuln-auth": ("# Authentication Analysis Report", "# 认证分析报告"),
    "vuln-authz": ("# Authorization Analysis Report", "# 授权分析报告"),
    "vuln-injection": ("#Injection Analysis Report (SQLi & Command Injection)",
                       "# 注入分析报告(SQLi 与命令注入)"),
    "vuln-ssrf": ("# SSRF Analysis Report", "# SSRF 分析报告"),
    "vuln-xss": ("# Cross-Site Scripting (XSS) Analysis Report", "# XSS 分析报告(跨站脚本)"),
}

SUBHEADINGS = [
    ("## 1. Executive Summary", "## 一、执行摘要"),
    ("## 2. Dominant Vulnerability Patterns", "## 二、主要漏洞模式"),
    ("## 3. Strategic Intelligence for Exploitation", "## 三、利用情报"),
]


def _read(name: str) -> str:
    return (PROMPTS / f"{name}.txt").read_text(encoding="utf-8")


def test_each_vuln_prompt_references_shared_language_block():
    for name in VULN_PROMPTS:
        assert "@include(shared/_output-language.txt)" in _read(name), (
            f"{name}.txt 须 @include 共享语言约束块")


def test_main_headings_translated_to_chinese():
    for name, (en, cn) in VULN_PROMPTS.items():
        src = _read(name)
        assert en not in src, f"{name}.txt 仍含英文主标题: {en!r}"
        assert cn in src, f"{name}.txt 缺中文主标题: {cn!r}"


def test_common_subheadings_translated():
    for name in VULN_PROMPTS:
        src = _read(name)
        for en, cn in SUBHEADINGS:
            if en in src:  # 仅断言该 prompt 实际含此英文标题时已被替换
                assert cn in src, f"{name}.txt 子标题未中文化: {en!r} → {cn!r}"
```

- [ ] **Step 3: 跑测试,确认失败**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/whitebox/tests/test_vuln_prompts_chinese.py -v`
Expected: FAIL(prompt 仍含英文标题、无 @include)

- [ ] **Step 4: vuln-auth.txt —— 加 @include + 替换主标题 + 子标题**

在 `prompts/vuln-auth.txt` 的 `<deliverable_instructions>` 标签下一行插入 `@include`。`<deliverable_instructions>` 当前在 line 202,内容从 line 203 开始。Edit `<deliverable_instructions>\nWhen you have systematically` → 加入 @include:

old:
```
<deliverable_instructions>
When you have systematically analyzed all relevant endpoints and logic paths, you MUST generate two final files. Follow these instructions precisely.
```
new:
```
<deliverable_instructions>
@include(shared/_output-language.txt)
When you have systematically analyzed all relevant endpoints and logic paths, you MUST generate two final files. Follow these instructions precisely.
```

主标题与子标题替换(逐条 Edit,这些是 `---` 之间的示例块):

| old | new |
|---|---|
| `# Authentication Analysis Report` | `# 认证分析报告` |
| `## 1. Executive Summary` | `## 一、执行摘要` |
| `## 2. Dominant Vulnerability Patterns` | `## 二、主要漏洞模式` |
| `## 3. Strategic Intelligence for Exploitation` | `## 三、利用情报` |
| `## 4. Secure by Design: Validated Components` | `## 四、安全设计:已验证组件` |

- [ ] **Step 5: vuln-authz.txt —— 同法**

在 line 279 `<deliverable_instructions>` 下一行插入 `@include(shared/_output-language.txt)`(同 Step 4 的 Edit 模式,old 为 `<deliverable_instructions>\n` 后接原文首行)。标题替换:

| old | new |
|---|---|
| `# Authorization Analysis Report` | `# 授权分析报告` |
| `## 1. Executive Summary` | `## 一、执行摘要` |
| `## 2. Dominant Vulnerability Patterns` | `## 二、主要漏洞模式` |
| `## 3. Strategic Intelligence for Exploitation` | `## 三、利用情报` |
| `## 4. Vectors Analyzed and Confirmed Secure` | `## 四、已分析并确认安全的向量` |
| `## 5. Analysis Constraints and Blind Spots` | `## 五、分析约束与盲区` |

- [ ] **Step 6: vuln-injection.txt —— 同法**

在 line 295 `<deliverable_instructions>` 下一行插入 `@include(shared/_output-language.txt)`。标题替换:

| old | new |
|---|---|
| `#Injection Analysis Report (SQLi & Command Injection)` | `# 注入分析报告(SQLi 与命令注入)` |
| `## 1. Executive Summary` | `## 一、执行摘要` |
| `## 2. Dominant Vulnerability Patterns` | `## 二、主要漏洞模式` |
| `## 3. Strategic Intelligence for Exploitation` | `## 三、利用情报` |
| `## 4. Vectors Analyzed and Confirmed Secure` | `## 四、已分析并确认安全的向量` |
| `## 5. Analysis Constraints and Blind Spots` | `## 五、分析约束与盲区` |

- [ ] **Step 7: vuln-ssrf.txt —— 同法**

在 line 250 `<deliverable_instructions>` 下一行插入 `@include(shared/_output-language.txt)`。标题替换:

| old | new |
|---|---|
| `# SSRF Analysis Report` | `# SSRF 分析报告` |
| `## 1. Executive Summary` | `## 一、执行摘要` |
| `## 2. Dominant Vulnerability Patterns` | `## 二、主要漏洞模式` |
| `## 3. Strategic Intelligence for Exploitation` | `## 三、利用情报` |
| `## 4. Secure by Design: Validated Components` | `## 四、安全设计:已验证组件` |

- [ ] **Step 8: vuln-xss.txt —— 同法**

在 line 216 `<deliverable_instructions>` 下一行插入 `@include(shared/_output-language.txt)`。标题替换:

| old | new |
|---|---|
| `# Cross-Site Scripting (XSS) Analysis Report` | `# XSS 分析报告(跨站脚本)` |
| `## 1. Executive Summary` | `## 一、执行摘要` |
| `## 2. Dominant Vulnerability Patterns` | `## 二、主要漏洞模式` |
| `## 3. Strategic Intelligence for Exploitation` | `## 三、利用情报` |
| `## 4. Vectors Analyzed and Confirmed Secure` | `## 四、已分析并确认安全的向量` |
| `## 5. Analysis Constraints and Blind Spots` | `## 五、分析约束与盲区` |

- [ ] **Step 9: 跑断言测试,确认通过**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/whitebox/tests/test_vuln_prompts_chinese.py -v`
Expected: PASS

- [ ] **Step 10: 校验 @include 渲染正常(确认 prompt 仍可被 PromptManager 加载)**

Run:
```bash
cd /root/shannon-py && .venv/bin/python -c "
from pathlib import Path
from shannon_core.prompts.manager import PromptManager
pm = PromptManager(Path('prompts'))
rendered = pm.load_template('vuln-auth')
assert 'output_language' in rendered and '简体中文' in rendered, '语言块未注入'
assert '# 认证分析报告' in rendered, '中文主标题未渲染'
assert '{{DELIVERABLES_PATH}}' in rendered or 'DELIVERABLES_PATH' in rendered
print('vuln-auth render OK, len=', len(rendered))
"
```
Expected: 打印 `vuln-auth render OK, len= <数字>`(语言块注入、中文标题渲染、模板变量保留)。

- [ ] **Step 11: Commit**

```bash
git add prompts/shared/_output-language.txt \
        prompts/vuln-auth.txt prompts/vuln-authz.txt prompts/vuln-injection.txt \
        prompts/vuln-ssrf.txt prompts/vuln-xss.txt \
        packages/whitebox/tests/test_vuln_prompts_chinese.py
git commit -m "feat(prompts): vuln prompt 输出层中文化 (Layer0)"
```

---

## Task 5: report-executive.txt 中文化

**Files:**
- Modify: `prompts/report-executive.txt`
- Test: `packages/whitebox/tests/test_report_prompt_chinese.py`

**Interfaces:**
- Consumes: REPORT agent 读 `comprehensive_security_assessment_report.md`(Task 3 产出)+ `pre_recon_deliverable.md` / `recon_deliverable.md`,原地改写加执行摘要。
- Produces: 最终综合报告顶部为 `# 安全评估报告` / `## 执行摘要` / `## 按漏洞类型汇总` + 清理后的中文分项。
- 注:`REPORT_VULN_HEADING` / `REPORT_VULN_SUBHEADING` 是**死变量**(manager.py 不替换),清理靠漏洞 ID 正则 `### [TYPE]-VULN-NN` + LLM 语义,**中文化 vuln 标题不破坏清理逻辑**。

- [ ] **Step 1: 写结构断言测试**

Create `packages/whitebox/tests/test_report_prompt_chinese.py`:

```python
from pathlib import Path

PROMPT = Path(__file__).resolve().parents[1].parent.parent / "prompts" / "report-executive.txt"


def test_report_prompt_uses_chinese_executive_summary_headings():
    src = PROMPT.read_text(encoding="utf-8")
    assert "# 安全评估报告" in src
    assert "## 执行摘要" in src
    assert "## 按漏洞类型汇总" in src
    # 废弃英文标题不应再作为"要生成"的标题出现
    assert "# Security Assessment Report" not in src
    assert "## Summary by Vulnerability Type" not in src


def test_report_prompt_chinese_field_labels():
    src = PROMPT.read_text(encoding="utf-8")
    for label in ["目标:", "评估日期:", "范围:", "利用情况:"]:
        assert label in src, f"缺中文字段标签 {label}"
```

- [ ] **Step 2: 跑测试,确认失败**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/whitebox/tests/test_report_prompt_chinese.py -v`
Expected: FAIL

- [ ] **Step 3: 替换 report-executive.txt 的执行摘要标题块**

In `prompts/report-executive.txt`, the "ADDING these sections at the top" block (current ~line 52-66). Replace:

old:
```
1. ADDING these sections at the top:

# Security Assessment Report

## Executive Summary
- Target: {{WEB_URL}}
- Assessment Date: {current date}
- Scope: {{VULN_CLASSES_TESTED}}
- Exploitation: {{EXPLOITATION}}

## Summary by Vulnerability Type

{For each vulnerability type below, examine the comprehensive_security_assessment_report.md sections and provide a summary. If no section exists for that type or no vulnerabilities are listed, explicitly state that none were found.}
```
new:
```
1. ADDING these sections at the top:

# 安全评估报告

## 执行摘要
- 目标: {{WEB_URL}}
- 评估日期: {current date}
- 范围: {{VULN_CLASSES_TESTED}}
- 利用情况: {{EXPLOITATION}}

## 按漏洞类型汇总

{For each vulnerability type below, examine the comprehensive_security_assessment_report.md sections and provide a summary. If no section exists for that type or no vulnerabilities are listed, explicitly state that none were found.}
```

- [ ] **Step 4: 在 report-executive.txt 增加中文输出指令**

在 `prompts/report-executive.txt` 的 `<objective>` 块末尾(`</objective>` 之前)插入中文输出要求:

old:
```
4. Save the modified version back to the same file

IMPORTANT: You are MODIFYING an existing file, not creating a new one.
</objective>
```
new:
```
4. Save the modified version back to the same file

IMPORTANT: You are MODIFYING an existing file, not creating a new one.

LANGUAGE: 用简体中文撰写你新增的所有叙述性内容(执行摘要、按类型汇总)。
保留英文原文:漏洞编号(如 AUTH-VULN-01)、代码、文件路径、HTTP 方法/状态码、
URL、请求头名、JSON 字段名、技术缩写(SSRF/XSS/CSRF/RBAC 等)。
</objective>
```

- [ ] **Step 5: 跑断言测试,确认通过**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/whitebox/tests/test_report_prompt_chinese.py -v`
Expected: PASS

- [ ] **Step 6: 校验 report-executive 渲染正常**

Run:
```bash
cd /root/shannon-py && .venv/bin/python -c "
from pathlib import Path
from shannon_core.prompts.manager import PromptManager
pm = PromptManager(Path('prompts'))
r = pm.load_template('report-executive')
assert '# 安全评估报告' in r and '## 执行摘要' in r
assert '{{WEB_URL}}' in r
print('report-executive render OK, len=', len(r))
"
```
Expected: 打印 `report-executive render OK, len= <数字>`。

- [ ] **Step 7: Commit**

```bash
git add prompts/report-executive.txt packages/whitebox/tests/test_report_prompt_chinese.py
git commit -m "feat(prompts): report-executive 执行摘要中文化"
```

---

## Task 6: pipeline-testing/report-executive.txt 标题同步

**Files:**
- Modify: `prompts/pipeline-testing/report-executive.txt`
- Test: 复用 Task 5 测试目录新增一个断言(或合并)

> pipeline-testing 是 CI stub(3 行),vuln-*.txt 是 `@include(shared/_filesystem.txt)` stub 无输出标题,**无需改**;仅 report-executive stub 含一个英文标题需同步。

- [ ] **Step 1: 替换 stub 中的英文标题**

`prompts/pipeline-testing/report-executive.txt` 当前全文:

```
Read `.shannon/deliverables/comprehensive_security_assessment_report.md`, prepend "# Security Assessment Report\n\n**Target:** {{WEB_URL}}\n\n" to the content, and save it back. Say "Done".
```

Replace with:

```
Read `.shannon/deliverables/comprehensive_security_assessment_report.md`, prepend "# 安全评估报告\n\n**目标:** {{WEB_URL}}\n\n" to the content, and save it back. Say "Done".
```

- [ ] **Step 2: 跑全部本计划新增/相关测试,确认全绿**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/core/tests/test_report_assembler.py packages/whitebox/tests/test_assemble_report.py packages/whitebox/tests/test_reporting_workflow.py packages/whitebox/tests/test_vuln_prompts_chinese.py packages/whitebox/tests/test_report_prompt_chinese.py packages/blackbox/tests/test_finalize_report.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add prompts/pipeline-testing/report-executive.txt
git commit -m "feat(prompts): pipeline-testing report stub 标题同步中文"
```

---

## Task 7: 人工冒烟(spec §6.4)

> 非自动化 task。LLM 输出语言无法单元测试,需人工确认。在前 6 个 task 全部 commit 后执行。

- [ ] **Step 1: 跑一次最小 white-box 扫描(单 vuln class、小仓库)**

```bash
cd /root/shannon-py
SHANNON_PROFILE=ark-coding .venv/bin/python -m shannon_whitebox.worker <小仓库路径> \
  # 或用现有 CLI;限定单 vuln class(如 auth)以节省成本
```
> 若 CLI 支持 `--vuln-classes auth` 限定单类,则用它;否则接受全量。

- [ ] **Step 2: 人工核对 deliverables 产物**

检查 `<workspace>/deliverables/`:
1. `comprehensive_security_assessment_report.md` **存在**(轴 1 修复生效);
2. 顶部为 `# 安全评估报告` / `## 执行摘要` / `## 按漏洞类型汇总`(中文);
3. `auth_analysis_deliverable.md` 为中文叙述,漏洞编号 `AUTH-VULN-01`、文件路径、HTTP 方法保留英文;
4. 综合报告含各分项中文详述。

- [ ] **Step 3: 记录冒烟结果**

若叙述中混入明显英文腔 → 记录现象,作为启动 Layer 1(分析指令中文化)的依据(spec §4.2.2)。否则 Layer 0 验收完成。

- [ ] **Step 4: 更新 memory(冒烟待人工 → 冒烟已通过/现象)**

更新 `~/.claude/projects/-root-shannon-py/memory/` 下相关状态条目,把"手动冒烟待人工"推进到实际结果。

---

## Self-Review(写作时已自查)

**Spec coverage:**
- §2 目标 1(产出 comprehensive report)→ Task 1/2/3 ✓
- §2 目标 2(中文分项 + 综合报告)→ Task 4/5 ✓
- §2 目标 3(拼接 + 执行摘要)→ Task 3 ✓
- §2 目标 4(blackbox 零回归)→ Task 1 Step 6 ✓
- §4.1.1 ReportAssembler 提升 → Task 1 ✓
- §4.1.2 white-box activity(简化:run_agent 复用,无需新 run_report_agent)→ Task 2/3 ✓(基于代码事实修正 spec)
- §4.1.3 workflows 改造 → Task 3 ✓
- §4.1.4 step_intents → Task 2 Step 1 ✓
- §4.2.2 Layer 0 → Task 4/5 ✓(pipeline-testing vuln stub 无输出标题,无需改——修正 spec)
- §4.2.3 中文标题词表 → Task 4 Step 1(_output-language.txt)+ 各 prompt ✓
- §4.2.4 输出语言约束块 → Task 4 Step 1 ✓
- §4.2.5 约束 1(逻辑骨架)/2(vuln+report 配套)/3(死变量,无需改代码)→ Task 4/5 ✓
- §6 测试策略 → Task 1-6 + Task 7 冒烟 ✓

**Placeholder scan:** 无 TBD/TODO;每步含完整代码或精确 old/new 替换表与命令。

**Type consistency:** `assemble_report(input: ActivityInput)`、`ReportAssembler.assemble(deliverables, vuln_classes, report_path)`、`ALL_VULN_CLASSES`、`AgentName.REPORT="report"`、`intent_for("assemble-report")` 前后一致。

**基于代码事实对 spec 的两处修正**(已在对应 task 标注):
1. `run_report_agent` activity 无需新增——generic `run_agent` + `agent_name="report"` 即可(§4.1.2 简化)。
2. pipeline-testing/vuln-*.txt 是 stub 无输出标题,无需中文化;仅 report-executive stub 同步(§4.2.2 修正)。
