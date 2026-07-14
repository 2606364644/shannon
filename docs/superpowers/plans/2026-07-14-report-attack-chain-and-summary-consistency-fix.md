# 报告攻击链章节丢失 + 类型汇总口径脱节 修复 Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让白盒报告的「单点漏洞数 / 按类型汇总 / 攻击链」三者自洽且不误导——攻击链章节不再被 report-executive agent 覆盖丢失，类型汇总只统计单点卡片，前端能正确解析中文类型汇总。

**Architecture:** 三层修复，互相耦合但可独立测试。(P0) 后端：把攻击链章节注入从 `assemble_report`（在 report-executive **之前**）挪到新增的 `inject_attack_chains` activity（在 report-executive **之后**），确定性注入、不被 LLM 重写覆盖。(P1) prompt：`report-executive.txt` + `_build_vuln_summary_subsections` 明确「类型汇总只数 ### 单点卡片，攻击链缺陷不计入」。(P2) 前端：`parseStructure` 的 `Count` 正则兼容中文「数量:」，`computeStats` 用 displayName→prefix 反向映射补全零计数类型卡。

**Tech Stack:** Python 3 (temporalio activity)、vitest (React/TS 前端)、pytest。

## 根因上下文（调查结论，实现者必读）

现场 session `hr_20260713-104726` 报告页现象：单点漏洞显示 5，按类型汇总却写 inj2+xss1+auth5+authz11+ssrf1，单点漏洞块下方孤零零一张「Auth 5」卡。根因三层：

1. **攻击链章节丢失**：`workflows.py` 报告阶段顺序是 `assemble-report`（含追加 `## 攻击链` 章节）→ `run-report-agent`(report-executive)。report-executive 的 `deliverable_filename` 与 assemble 同为 `comprehensive_security_assessment_report.md`（`agents.py:143`），重写时**覆盖**了 assemble 追加的攻击链章节——尽管 `report-executive.txt:96` 要求保留。GLM 在执行强势的「REMOVE ANY OTHER SECTIONS」时丢了它。证据：`attack_chains.json` 有 13 条 `{"chains":[...]}`（id `llm-chain-1~13`），`render_attack_chains` 本应成功生成章节，但最终报告 84 行无 `## 攻击链`。
2. **类型汇总口径脱节**：`_build_vuln_summary_subsections`（`manager.py:249-254`）模板让 agent 统计「confirmed vulnerabilities」，agent 把攻击链里发现的缺陷（"来自攻击链分析"）也数了进去，但这些缺陷没有 ### 单点卡片 → 单点数(5) ≠ 汇总数(20)。
3. **前端只显一张 Auth 卡**：`parseStructure`（`MarkdownView.tsx:136`）的 `Count` 正则只认英文，但 narration 中文指令让 GLM 写成「数量:」→ typeSummaries 解析失败（count=0、prefix="")→ `computeStats`（`report-stats.ts:137`）的零计数补全因 `if (ts.prefix && …)` 为假而跳过 → TypeSummaryCards 只剩 blocks 驱动的 Auth 一张。攻击链块也因 `attackChainCount=0`（无 ## 攻击链 章节）不渲染。

**P3（不在本 plan 范围）**：该 session inj/xss/ssrf/authz 无 findings 是配置特例（旧版 `SHANNON_LLM_TRACK_ENABLED=0` 全关 LLM 轨 + GitNexus 对 TS/Egg.js 仓 sink 召回 0），靠 07-14 新语义与 sink 规则改善，非报告组装层 bug。

## Global Constraints

- **不破坏双轨铁律**：不动 LLM 轨 prompt 的确定性 hints 桥梁；本 plan 只改 report-executive（汇总措辞）与报告组装顺序，不涉及 vuln agent prompt。
- **攻击链章节注入须幂等**：resume/重跑不能重复追加 `## 攻击链`（用标题存在性守卫）。
- **攻击链注入失败不阻塞主报告**（保持现有 non-fatal 语义）。
- **前端正则向后兼容**：既认英文 `Count:`（模板原文）也认中文「数量:」（GLM narration 产物），不破坏现有英文报告。
- **测试隔离**：只跑改动相关测试文件（CLAUDE.md：全套 pytest 有预存挂起/失败）。
- 前端测试：`cd packages/web/frontend && npx vitest run <file>`（vitest）。
- 后端测试：`cd /root/shannon-py && python -m pytest packages/whitebox/tests/<file>::<test> -xvs`。

## File Structure

| 文件 | 改动 | 职责 |
|---|---|---|
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | Modify | 新增 `inject_attack_chains` activity；从 `assemble_report` 删除攻击链追加块（977-985） |
| `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | Modify | 报告阶段 `run-report-agent` 之后插入 `inject_attack_chains` 调用（~518 行后） |
| `packages/whitebox/src/shannon_whitebox/pipeline/step_intents.py` | Modify | reporting phase 加 `inject-attack-chains` StepSpec |
| `packages/whitebox/tests/test_assemble_report.py` | Modify | 加 `inject_attack_chains` 测试 + assemble 不再追加攻击链的回归测试 |
| `packages/core/src/shannon_core/prompts/manager.py` | Modify | `_build_vuln_summary_subsections` 措辞改为「只数单点卡片」 |
| `prompts/report-executive.txt` | Modify | 「按漏洞类型汇总」指令明确口径（只数 ### 单点，攻击链不计入） |
| `packages/core/tests/test_prompt_manager.py`（若无则创建） | Modify/Create | 断言新措辞含单点口径限定 |
| `packages/web/frontend/src/components/MarkdownView.tsx` | Modify | `parseStructure` Count 正则兼容中文「数量:」 |
| `packages/web/frontend/src/lib/report-stats.ts` | Modify | 加 displayName→prefix 反向映射；computeStats 给空 prefix 的 typeSummaries 补全 |
| `packages/web/frontend/src/lib/report-stats.test.ts` | Modify | 加中文「数量:」解析 + 零计数补全测试 |
| `packages/web/frontend/src/components/MarkdownView.test.tsx` | Modify | 加中文类型汇总端到端解析测试 |

---

### Task 1: P0-后端 — 新增 `inject_attack_chains` activity，从 assemble_report 移除攻击链追加

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`（新增 activity 于 `assemble_report` 之后；删除 `assemble_report` 内 977-985 的攻击链追加块）
- Test: `packages/whitebox/tests/test_assemble_report.py`

**Interfaces:**
- Consumes: `ReportAssembler.render_attack_chains(deliverables_path) -> str`（`report_assembler.py:34`，已存在）；`_get_paths(input)`、`ActivityInput`（activities.py 内已有）。
- Produces: 新 activity `inject_attack_chains(input: ActivityInput) -> None`，被 workflow（Task 2）调用；读 `deliverables/comprehensive_security_assessment_report.md` + `attack_chains.json`，幂等追加 `## 攻击链` 章节。

- [ ] **Step 1: 写失败测试 — inject_attack_chains 追加攻击链章节到最终报告**

在 `packages/whitebox/tests/test_assemble_report.py` 末尾追加（沿用该文件已有的 tmp deliverables + monkeypatch 风格；若该文件用 fixture 构建 deliverables，复用之）：

```python
import json
from pathlib import Path


def test_inject_attack_chains_appends_section(tmp_path: Path) -> None:
    """report-executive 之后注入：attack_chains.json → ## 攻击链 章节追加到最终报告。"""
    from shannon_whitebox.pipeline import activities

    deliverables = tmp_path / "whitebox"
    deliverables.mkdir()
    report = deliverables / "comprehensive_security_assessment_report.md"
    report.write_text("# 安全评估报告\n\n## 执行摘要\n\n正文...\n", encoding="utf-8")
    (deliverables / "attack_chains.json").write_text(
        json.dumps({"chains": [
            {"id": "llm-chain-1", "name": "enum→idor", "vuln_type": "authz",
             "severity": "critical", "confidence": "high",
             "steps": [{"order": 1, "endpoint": "/api/x", "method": "GET", "description": "d"}]},
        ]}),
        encoding="utf-8",
    )

    act_input = _make_act_input(deliverables)  # 见下方辅助；沿用文件现有构造方式
    import asyncio
    asyncio.run(activities.inject_attack_chains(act_input))

    content = report.read_text(encoding="utf-8")
    assert "## 攻击链（多步利用路径）" in content
    assert "### llm-chain-1: enum→idor" in content
    # 原文保留
    assert "## 执行摘要" in content
```

注：`_make_act_input` 若文件中无此辅助函数，改用文件现有构造 `ActivityInput` 的方式（参考该文件已有 `test_assemble_report_*`）。实现者须先读 `test_assemble_report.py` 现有 fixture/辅助，对齐风格——不要发明新构造。

- [ ] **Step 2: 写失败测试 — 幂等（重复调用不重复追加）**

```python
def test_inject_attack_chains_idempotent(tmp_path: Path) -> None:
    from shannon_whitebox.pipeline import activities
    import asyncio

    deliverables = tmp_path / "whitebox"
    deliverables.mkdir()
    report = deliverables / "comprehensive_security_assessment_report.md"
    report.write_text("body\n", encoding="utf-8")
    (deliverables / "attack_chains.json").write_text(
        json.dumps({"chains": [{"id": "llm-chain-1", "name": "n"}]}), encoding="utf-8",
    )
    act_input = _make_act_input(deliverables)
    asyncio.run(activities.inject_attack_chains(act_input))
    asyncio.run(activities.inject_attack_chains(act_input))  # 再跑一次

    content = report.read_text(encoding="utf-8")
    assert content.count("## 攻击链（多步利用路径）") == 1
```

- [ ] **Step 3: 写失败测试 — 无 attack_chains.json / 无报告 / 空 chains 时安全 no-op**

```python
def test_inject_attack_chains_noop_when_missing(tmp_path: Path) -> None:
    from shannon_whitebox.pipeline import activities
    import asyncio

    deliverables = tmp_path / "whitebox"
    deliverables.mkdir()
    report = deliverables / "comprehensive_security_assessment_report.md"
    report.write_text("body\n", encoding="utf-8")
    # 无 attack_chains.json
    act_input = _make_act_input(deliverables)
    asyncio.run(activities.inject_attack_chains(act_input))
    assert report.read_text(encoding="utf-8") == "body\n"  # 不变

    # 空 chains
    (deliverables / "attack_chains.json").write_text(
        json.dumps({"chains": []}), encoding="utf-8")
    asyncio.run(activities.inject_attack_chains(act_input))
    assert report.read_text(encoding="utf-8") == "body\n"
```

- [ ] **Step 4: 写失败测试 — assemble_report 不再追加攻击链（回归）**

```python
def test_assemble_report_no_longer_appends_attack_chains(tmp_path: Path) -> None:
    """assemble_report 移除了攻击链追加；攻击链章节由 inject_attack_chains 负责。"""
    from shannon_whitebox.pipeline import activities
    import asyncio

    deliverables = tmp_path / "whitebox"
    deliverables.mkdir()
    (deliverables / "auth_findings.md").write_text("## Authentication Vulnerabilities\n\n### AUTH-VULN-01\n", encoding="utf-8")
    (deliverables / "attack_chains.json").write_text(
        json.dumps({"chains": [{"id": "llm-chain-1", "name": "n"}]}), encoding="utf-8",
    )
    act_input = _make_act_input(deliverables)
    asyncio.run(activities.assemble_report(act_input))
    report = (deliverables / "comprehensive_security_assessment_report.md").read_text(encoding="utf-8")
    assert "## 攻击链" not in report  # assemble 不再碰攻击链
```

- [ ] **Step 5: 运行测试确认失败**

```
cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_assemble_report.py::test_inject_attack_chains_appends_section -xvs
```
Expected: FAIL（`inject_attack_chains` 不存在 / AttributeError）。

- [ ] **Step 6: 实现 — 新增 inject_attack_chains activity**

在 `activities.py` 的 `assemble_report` 函数**之后**（约 992 行后）新增：

```python
@activity.defn
async def inject_attack_chains(input: ActivityInput) -> None:
    """报告阶段最后注入：attack_chains.json → ## 攻击链 章节追加到最终报告。

    必须在 run-report-agent 之后运行——report-executive agent 重写
    comprehensive_security_assessment_report.md（同 deliverable_filename），
    若在此之前追加攻击链章节会被覆盖丢失（回归 hr_20260713-104726）。
    放最后注入，确保攻击链章节留存。幂等（标题已存在则跳过）。失败不阻塞。
    """
    import logging
    log = logging.getLogger(__name__)
    try:
        from shannon_core.services.report_assembler import ReportAssembler
        from shannon_core.utils.file_io import (
            async_path_exists, async_read_file, async_write_file,
        )

        _, deliverables, _ = _get_paths(input)
        report_path = deliverables / "comprehensive_security_assessment_report.md"
        if not await async_path_exists(report_path):
            return  # 主报告不存在，无处追加
        chains_md = await ReportAssembler.render_attack_chains(deliverables)
        if not chains_md:
            return  # 无攻击链 / 渲染为空
        content = await async_read_file(report_path)
        if "## 攻击链（多步利用路径）" in content:
            return  # 幂等：已注入（resume/重跑）
        await async_write_file(report_path, content + chains_md)
    except Exception as exc:  # noqa: BLE001 — 攻击链注入失败不阻塞主报告
        log.warning("inject_attack_chains failed (non-blocking): %s", exc)
```

- [ ] **Step 7: 从 assemble_report 删除攻击链追加块**

删除 `assemble_report` 内（activities.py:977-985）这一段：

```python
        # 追加攻击链独立章节（非 fatal）
        try:
            chains_md = await ReportAssembler.render_attack_chains(deliverables)
            if chains_md:
                from shannon_core.utils.file_io import async_read_file, async_write_file
                content = await async_read_file(report_path)
                await async_write_file(report_path, content + chains_md)
        except Exception:
            pass  # 攻击链渲染失败不阻塞主报告
```

同时把 `assemble_report` docstring 里「拼接完成后追加攻击链独立章节」一句改为「攻击链章节由后续 inject_attack_chains activity 注入（report-executive 之后），避免被覆盖」。

- [ ] **Step 8: 运行全部新测试确认通过**

```
cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_assemble_report.py -xvs -k "inject_attack_chains or assemble_report_no_longer"
```
Expected: 4 个新测试 PASS。

- [ ] **Step 9: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/tests/test_assemble_report.py
git commit -m "fix(report): 攻击链章节后注入——新增 inject_attack_chains activity, 从 assemble_report 移除(被 report-executive 覆盖丢失)"
```

---

### Task 2: P0-workflow — 报告阶段接线 inject_attack_chains + step 注册

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`（`run-report-agent` 之后，约 518 行后插入）
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/step_intents.py`（reporting tuple 加 StepSpec）
- Test: `packages/whitebox/tests/test_reporting_workflow.py`

**Interfaces:**
- Consumes: `activities.inject_attack_chains`（Task 1 产出）；`retry_for("standard")`、`timedelta`（workflows.py 已用）。
- Produces: workflow 报告阶段顺序变为 `render-findings → assemble-report → run-report-agent → inject-attack-chains → generate-poc`。

- [ ] **Step 1: 写失败测试 — reporting phase 含 inject-attack-chains step**

在 `packages/whitebox/tests/test_reporting_workflow.py` 加（沿用其现有 step_names 断言风格）：

```python
def test_reporting_phase_has_inject_attack_chains_after_run_report_agent() -> None:
    """攻击链注入必须在 run-report-agent 之后（顺序硬约束，防覆盖回归）。"""
    from shannon_whitebox.pipeline.step_intents import step_names
    steps = step_names("reporting")
    assert "inject-attack-chains" in steps
    assert steps.index("inject-attack-chains") > steps.index("run-report-agent")
```

- [ ] **Step 2: 运行确认失败**

```
cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_reporting_workflow.py::test_reporting_phase_has_inject_attack_chains_after_run_report_agent -xvs
```
Expected: FAIL（`inject-attack-chains` not in steps）。

- [ ] **Step 3: 实现 — step_intents reporting 加 StepSpec**

`packages/whitebox/src/shannon_whitebox/pipeline/step_intents.py` reporting tuple（50-54）改为：

```python
    "reporting": (
        StepSpec("render-findings",   "渲染漏洞条目(若存在队列)"),
        StepSpec("assemble-report",   "拼接各分项报告"),
        StepSpec("run-report-agent",  "撰写执行摘要并清理报告"),
        StepSpec("inject-attack-chains", "注入攻击链章节(report-executive 之后,防覆盖)"),
    ),
```

- [ ] **Step 4: 实现 — workflows.py 插入 inject_attack_chains 调用**

在 `workflows.py` 的 `run-report-agent` activity 调用之后（即第 518 行 `)` 之后、519 行 `self._state.current_agent = None` 之前），插入：

```python
            # 攻击链章节最后注入（report-executive 之后），避免被 agent 重写覆盖丢失
            self._state.current_agent = "inject-attack-chains"
            await workflow.execute_activity(
                activities.inject_attack_chains, act_input,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=retry_for("standard"),
            )
            self._state.current_agent = None
```

注意：原 519 行 `self._state.current_agent = None` 保留（在新插入块之后）。`act_input` 变量沿用同一作用域内已有的（assemble/run-report-agent 用的同一 `act_input`）。

- [ ] **Step 5: 运行测试确认通过**

```
cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_reporting_workflow.py -xvs
```
Expected: PASS（含新测试 + 不破坏现有 reporting 测试）。

- [ ] **Step 6: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/workflows.py packages/whitebox/src/shannon_whitebox/pipeline/step_intents.py packages/whitebox/tests/test_reporting_workflow.py
git commit -m "fix(report): workflow 报告阶段 run-report-agent 之后注入攻击链章节 + step 注册"
```

---

### Task 3: P1-prompt — 类型汇总只统计单点卡片

**Files:**
- Modify: `packages/core/src/shannon_core/prompts/manager.py:244-255`（`_build_vuln_summary_subsections`）
- Modify: `prompts/report-executive.txt:66-70`（「按漏洞类型汇总」指令段）
- Test: `packages/core/tests/test_prompt_manager.py`（若无，创建；先 grep 确认现有 manager 测试位置）

**Interfaces:**
- Consumes: `config.vuln_classes: list[str]`（manager 已收）。
- Produces: 类型汇总 prompt 措辞明确「只数 ### 单点卡片，攻击链不计入」。

- [ ] **Step 1: 定位 manager 现有测试**

```
cd /root/shannon-py && ls packages/core/tests/ | grep -i "manager\|prompt"
grep -rln "_build_vuln_summary_subsections\|VULN_SUMMARY_SUBSECTIONS" packages/core/tests/
```
确认测试文件路径。若无针对该方法的测试，在最近的 manager 测试文件（或新建 `test_prompt_manager.py`）加。

- [ ] **Step 2: 写失败测试 — 子节模板含「只数单点卡片」口径限定**

在 manager 测试文件加：

```python
def test_vuln_summary_subsections_counts_only_single_point_cards() -> None:
    """类型汇总模板须明确：只数 ### 单点卡片，攻击链(llm-chain)不计入。"""
    from shannon_core.prompts.manager import PromptManager
    from pathlib import Path
    mgr = PromptManager(Path("/nonexistent"))  # 只调方法，不读文件
    out = mgr._build_vuln_summary_subsections(["injection", "xss"])
    # 含单点卡片口径限定
    assert "PREFIX-VULN-NN" in out or "PREFIX-GN-NN" in out or "单点" in out
    # 含「攻击链不计入」类限定
    assert "攻击链" in out or "llm-chain" in out or "不计入" in out
    # 仍为每个 class 生成子节
    assert "### Injection" in out
    assert "### Xss" in out
```

- [ ] **Step 3: 运行确认失败**

```
cd /root/shannon-py && python -m pytest packages/core/tests/test_prompt_manager.py::test_vuln_summary_subsections_counts_only_single_point_cards -xvs
```
Expected: FAIL（现模板无单点/攻击链口径限定词）。

- [ ] **Step 4: 实现 — _build_vuln_summary_subsections 措辞**

`manager.py:244-255` 改为：

```python
    def _build_vuln_summary_subsections(self, vuln_classes: list[str]) -> str:
        """Generate per-class summary subsection templates.

        口径（2026-07-14，修 hr_20260713-104726 口径脱节）：Count 只数报告正文里的
        ### 单点漏洞卡片（ID 形如 PREFIX-VULN-NN / PREFIX-GN-NN）。攻击链
        （## 攻击链 章节 / llm-chain-N）里发现的缺陷【不计入】此处——它们在攻击链
        章节单独体现，避免「单点漏洞总数」与「类型汇总」口径脱节。
        """
        lines = []
        for vc in vuln_classes:
            label = vc.replace("-", " ").title()
            lines.append(
                f"### {label}\n"
                f"Count: {{只数本报告正文 ### 单点漏洞卡片（ID 形如 PREFIX-VULN-NN 或 PREFIX-GN-NN，属于 {label} 类）的数量。"
                f"攻击链（## 攻击链 / llm-chain-N）里发现的缺陷【不计入】此处——它们单独成章。"
                f"若该类无单点卡片，写 0}}\n"
                f"Severity range: {{仅基于上述单点卡片的 range；无单点卡片则 N/A}}\n"
                f"Key findings: {{1-2 句，仅概述单点卡片；勿混入攻击链内容}}"
            )
        return "\n\n".join(lines)
```

- [ ] **Step 5: 实现 — report-executive.txt 类型汇总指令段**

`prompts/report-executive.txt` 第 66-70 行（`## 按漏洞类型汇总` 段）改为：

```
## 按漏洞类型汇总

{For each vulnerability type below, count ONLY standalone single-point vulnerability cards in the report body — headings matching `### PREFIX-VULN-NN` or `### PREFIX-GN-NN`. DO NOT count defects that appear only inside the `## 攻击链` section (llm-chain-N); those are multi-step chain findings, counted separately in their own section, NOT as single-point vulns. If a type has zero single-point cards, write Count: 0 and note that any findings for that type live in the 攻击链 section.}

{{VULN_SUMMARY_SUBSECTIONS}}
```

- [ ] **Step 6: 运行测试确认通过**

```
cd /root/shannon-py && python -m pytest packages/core/tests/test_prompt_manager.py -xvs
```
Expected: PASS。

- [ ] **Step 7: Commit**

```bash
git add packages/core/src/shannon_core/prompts/manager.py prompts/report-executive.txt packages/core/tests/test_prompt_manager.py
git commit -m "fix(report): 类型汇总只统计单点卡片——攻击链缺陷不计入,消除单点数与汇总口径脱节"
```

---

### Task 4: P2-前端 — 中文「数量:」解析 + 零计数类型补全

**Files:**
- Modify: `packages/web/frontend/src/components/MarkdownView.tsx:136,139`（`parseStructure` Count/prefix 正则）
- Modify: `packages/web/frontend/src/lib/report-stats.ts`（加 DISPLAY_TO_PREFIX 反向映射 + computeStats 补 prefix）
- Test: `packages/web/frontend/src/lib/report-stats.test.ts`
- Test: `packages/web/frontend/src/components/MarkdownView.test.tsx`

**Interfaces:**
- Consumes: `TYPE_DISPLAY`（report-stats.ts:23）；`ParsedTypeSummary`（report-stats.ts:38）。
- Produces: `parseStructure` 认中文「数量:」；`computeStats` 给空 prefix 的 typeSummary 用 displayName 反查补全 → 零计数类型卡正常渲染（INJ/XSS/AUTHZ/SSRF 显 0）。

- [ ] **Step 1: 写失败测试 — computeStats 给空 prefix 的 typeSummary 用 displayName 反查补全**

`packages/web/frontend/src/lib/report-stats.test.ts` 加：

```typescript
  it("typeSummaries 空 prefix 时按 displayName 反查补全（中文报告场景）", () => {
    // 中文「按类型汇总」只给 displayName(Injection/XSS/...)，prefix 空 → 须反查 INJ/XSS
    const blocks: ParsedVulnBlock[] = [
      { id: "AUTH-VULN-01", prefix: "AUTH", title: "t", starred: false, vulnType: "",
        fields: [], externallyExploitable: null, authRequired: null, confidence: null, verdict: null, raw: "" },
    ];
    const typeSummaries: ParsedTypeSummary[] = [
      { prefix: "", displayName: "Injection", count: 0, severityRangeRaw: "" },
      { prefix: "", displayName: "XSS", count: 0, severityRangeRaw: "" },
      { prefix: "", displayName: "Auth", count: 5, severityRangeRaw: "" },
      { prefix: "", displayName: "Authz", count: 0, severityRangeRaw: "" },
      { prefix: "", displayName: "SSRF", count: 0, severityRangeRaw: "" },
    ];
    const aggs = computeStats(blocks, new Set(), [], typeSummaries).typeAggs;
    const byPrefix = Object.fromEntries(aggs.map((a) => [a.prefix, a.count]));
    expect(byPrefix).toEqual({ AUTH: 5, INJ: 0, XSS: 0, AUTHZ: 0, SSRF: 0 });
  });
```

（`ParsedVulnBlock` 字段以 `api/types.ts` 实际定义为准；实现者先读该类型对齐字段。）

- [ ] **Step 2: 写失败测试 — parseStructure 认中文「数量:」**

`packages/web/frontend/src/components/MarkdownView.test.tsx` 加（沿用其现有 parseStructure 测试导入风格）：

```typescript
  it("parseStructure 解析中文「数量: N」类型汇总（narration 中文产物）", () => {
    const md = [
      "## 按漏洞类型汇总",
      "### Injection",
      "- 数量: 2 个已确认注入漏洞（来自攻击链分析）",
      "- 严重级别: Critical 至 Medium",
      "### Auth",
      "- 数量: 5 个已确认认证漏洞",
    ].join("\n");
    // parseStructure 非导出 → 通过 MarkdownView 渲染后断言 TypeSummaryCards，
    // 或若文件已导出 parseStructure 则直接调。实现者按文件现有导出方式选。
    // 断言：typeSummaries 含 Injection{count:2,prefix:INJ} 与 Auth{count:5,prefix:AUTH}
    // （具体断言形式见文件现有 parseStructure 测试对齐）
  });
```

实现者须先读 `MarkdownView.test.tsx` 现有对 `parseStructure` 的测试方式（直接调导出 vs 渲染断言），对齐写法——`parseStructure` 当前未导出（`MarkdownView.tsx:80` 是文件内函数），故本测试改为**渲染 MarkdownView 后断言 TypeSummaryCards 卡片数与数字**：

```typescript
  it("中文「数量:」类型汇总 → TypeSummaryCards 渲染全 5 类（含 0 计数卡）", async () => {
    const md = [
      "# 安全评估报告", "## 执行摘要", "## 按漏洞类型汇总",
      "### Injection", "- 数量: 2 个", "### XSS", "- 数量: 1 个",
      "### Auth", "- 数量: 5 个", "### Authz", "- 数量: 11 个",
      "### SSRF", "- 数量: 1 个",
      "## Authentication Vulnerabilities", "### AUTH-VULN-01", "### AUTH-VULN-02",
      "### AUTH-VULN-03", "### AUTH-VULN-04", "### AUTH-VULN-05",
    ].join("\n");
    render(<MarkdownView markdown={md} />);
    const cards = screen.getAllByTestId("type-card");
    expect(cards).toHaveLength(5);
    expect(within(cards[0]).getByText("Injection")).toBeInTheDocument();
    // 数量来自 blocks（5 个 AUTH 卡片），非 prose「数量:」（口径由 P1 保证 prose 不再虚高）
  });
```

（`render/screen/within` 来自 @testing-library/react，沿用文件现有导入。）

- [ ] **Step 3: 运行确认失败**

```
cd /root/shannon-py/packages/web/frontend && npx vitest run src/lib/report-stats.test.ts src/components/MarkdownView.test.tsx
```
Expected: FAIL（空 prefix 不补全 / 中文数量不解析）。

- [ ] **Step 4: 实现 — report-stats.ts 加 DISPLAY_TO_PREFIX + computeStats 补 prefix**

`packages/web/frontend/src/lib/report-stats.ts` 在 `TYPE_DISPLAY`（第 29 行）之后加：

```typescript
/** 反向映射：规范显示名 → prefix（中文类型汇总只有 displayName 时反查 prefix）。 */
export const DISPLAY_TO_PREFIX: Record<string, string> = Object.fromEntries(
  Object.entries(TYPE_DISPLAY).map(([prefix, display]) => [display.toLowerCase(), prefix]),
);
```

在 `computeStats`（第 82 行起）函数体最前面（`// 1. 每个 block 的 severity` 之前）加 prefix 补全：

```typescript
  // 0. 给 typeSummaries 里 prefix 为空的项用 displayName 反查补全
  //    （中文「数量:」类型汇总只给 displayName，prefix 缺失 → 补全后零计数卡才能渲染）
  const summaries = typeSummaries?.map((ts) =>
    ts.prefix ? ts : { ...ts, prefix: DISPLAY_TO_PREFIX[ts.displayName.toLowerCase()] ?? "" },
  );
```

然后把函数内所有 `typeSummaries` 引用改为 `summaries`（第 105、121、134、136、138、149 行的 `typeSummaries` → `summaries`）。注意签名参数名仍为 `typeSummaries`（不动签名），仅函数体内用本地 `summaries`。

- [ ] **Step 5: 实现 — MarkdownView.tsx Count 正则兼容中文「数量:」**

`packages/web/frontend/src/components/MarkdownView.tsx` 第 136 行：

```typescript
      const cm = /^(?:-\s*\*\*)?(?:Count|数量)[:：]\s*\*?\*?\s*(\d+)/i.exec(t);
```

（兼容英文 `Count: 2`、`- **Count:** 2`、中文 `- 数量: 2 个…` 三种形式。）

第 139 行 prefix 正则保留（中文报告 prefix 由 Task4-Step4 的 displayName 反查补全，不依赖此正则），但为稳健可放宽——**不改**（避免过度改动；prefix 走反查路径）。

- [ ] **Step 6: 运行测试确认通过**

```
cd /root/shannon-py/packages/web/frontend && npx vitest run src/lib/report-stats.test.ts src/components/MarkdownView.test.tsx
```
Expected: PASS（含新测试 + 现有 report-stats/MarkdownView 测试不破）。

- [ ] **Step 7: 运行前端 report 目录全量回归**

```
cd /root/shannon-py/packages/web/frontend && npx vitest run src/lib/report-stats.test.ts src/components/MarkdownView.test.tsx src/components/report/
```
Expected: 全绿（ThreatOverview / TypeSummaryCards / AttackChainSection 现有测试不破）。

- [ ] **Step 8: Commit**

```bash
git add packages/web/frontend/src/lib/report-stats.ts packages/web/frontend/src/components/MarkdownView.tsx packages/web/frontend/src/lib/report-stats.test.ts packages/web/frontend/src/components/MarkdownView.test.tsx
git commit -m "fix(web): 前端解析中文「数量:」类型汇总 + displayName→prefix 反查补全零计数卡"
```

---

## Self-Review

**1. Spec coverage（对照三层根因）**：
- P0 攻击链章节丢失 → Task 1（activity）+ Task 2（workflow 接线）✅
- P1 类型汇总口径脱节 → Task 3（prompt 措辞 + 模板）✅
- P2 前端只显一张 Auth 卡 → Task 4（中文数量解析 + prefix 补全）✅
- P3（inj/xss/ssrf/authz 无 findings 配置特例）→ 明确不在范围 ✅

**2. Placeholder scan**：
- Task 1 Step1 的 `_make_act_input` 标注「沿用文件现有构造」——这是合理的（实现者须先读现有 fixture），非 placeholder；已明确要求对齐 `test_assemble_report.py` 现有风格。
- Task 3 Step1 先 grep 定位 manager 测试文件——合理（避免臆造路径）。
- Task 4 的 `ParsedVulnBlock` 字段标注「以 api/types.ts 为准」——合理。
- 所有代码块完整，无 TBD/TODO。

**3. Type/命名一致性**：
- `inject_attack_chains`（activity）↔ `inject-attack-chains`（step slug）↔ `"inject-attack-chains"`（current_agent）——三处一致。
- `render_attack_chains` 复用现有签名。
- `DISPLAY_TO_PREFIX` 在 report-stats.ts 定义、computeStats 内使用——一致。
- `## 攻击链（多步利用路径）` 标题串：`render_attack_chains`（report_assembler.py:60）产出、`inject_attack_chains` 幂等检查、`splitAttackChainSection`（前端 report-sections.ts:41 isAttackChainHeading 匹配「攻击链」）消费——三处一致 ✅。

## 验收（手动冒烟，实现完成后）

1. 真机重扫一个仓（或 resume hr_20260713-104726 的报告阶段），确认最终 `comprehensive_security_assessment_report.md` **含 `## 攻击链（多步利用路径）` + `### llm-chain-N`**（在 report-executive 之后注入）。
2. Web 报告页：ThreatOverview 左列「单点漏洞」数 = TypeSummaryCards 各卡 count 之和（口径自洽）；攻击链橙色块显示 13（attackChainCount）；TypeSummaryCards 显示全 5 类（无单点卡片的类显 0）。
3. 「按漏洞类型汇总」prose 的 Count 只数单点卡片（与卡片一致），不再虚高混入攻击链缺陷。

## 风险

- **report-executive 之后注入的攻击链章节位置**：在报告文末。前端 `splitAttackChainSection` 找首个 `## 攻击链` 切到文末，正常。若 report-executive 自己也写了一段攻击链相关 prose（如执行摘要引用），不影响（那不是 `## 攻击链` 二级章节）。
- **幂等标题守卫**用精确串 `## 攻击链（多步利用路径）`；若未来 `render_attack_chains` 改标题，须同步守卫串（已在 activity 注释标注）。
- **P1 prompt 改动依赖 GLM 遵守**：措辞已尽量强势（DO NOT count / 不计入）。若 GLM 仍偶尔混入，P2 的前端以 blocks 驱动卡片，至少卡片层自洽（prose 可能仍有偏差，但不再误导卡片计数）。
