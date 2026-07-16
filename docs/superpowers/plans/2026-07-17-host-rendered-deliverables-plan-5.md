# Host-Rendered Deliverables — Plan 5（report 加固 + 收尾：移除诊断、修正 spec）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Prerequisite:** Plan 1-4 已落地（pre-recon/recon/vuln/exploit 的 host 渲染）。本 plan 是收尾。

**⚠️ 诚实修正（查证推翻 spec 假设）：** spec §6 把 report 列入「全部 md agent 治本 scope」。但查证 PY report 机制后，**report 不需要 collector+renderer 治本**：

- PY report = `assemble_report`（`ReportAssembler.assemble`，host 拼接，**三级回退** evidence→findings→analysis_deliverable）先产生 `comprehensive_security_assessment_report.md` 底稿 → report-executive agent 覆写（`activities.py:1027` 注释明说 agent「重写」该文件）。
- 即 **comprehensive report 由 host assemble 兜底产生**，agent 只是覆写增强。即使 agent（GLM）失忆没正确覆写，**底稿文件仍在** → `validate_deliverable` 检查存在性**通过** → **report 不触发 Missing deliverable**。
- 这与前 4 个 agent（agent Write 新建→失忆丢 Write→文件不存在→Missing）**本质不同**。

**Goal:** (1) 加固 assemble 底稿的容错（部分 per-class deliverable 缺也一定产 report）；(2) report-executive prompt 明确「Edit 修改优先、不破坏底稿」；(3) Plan 1-4 治本完成后，移除临时诊断 `_enrich_missing_deliverable_error`；(4) 修正 spec 的 report 章节 + 收尾 memory。

**Architecture:** report 保持「host assemble 底稿 + agent 覆写」架构（不引入 collector）。治本 = 底稿容错加固 + agent 覆写语义清晰化 + 移除临时诊断。

**Tech Stack:** pytest。

**Spec:** `docs/superpowers/specs/2026-07-17-host-rendered-deliverables-design.md`（§6 Plan 5 修正为「report 加固 + 移除诊断」）

## Global Constraints

- **report 不引入 collector**：机制已兜底（assemble 底稿），不套 collector+renderer。
- **不动 assemble_report 三级回退**（已正确）；只补测试覆盖 + 容错边界。
- **§1/§2 不变量**：不受影响。
- **TDD + 测试陷阱**：每 task 先失败测试；只跑改动子集。

## File Structure

- Verify/Test: `packages/core/tests/services/test_report_assembler.py`（assemble 容错测试）
- Modify: `prompts/report-executive.txt`（Edit 优先、不破坏底稿语义）
- Modify: `packages/core/src/shannon_core/agents/executor.py`（移除 `_enrich_missing_deliverable_error` + try/except）
- Delete/Modify: `packages/core/tests/test_executor_missing_deliverable_diagnostics.py`（治本后诊断移除，测试随之移除）
- Modify: `docs/superpowers/specs/2026-07-17-host-rendered-deliverables-design.md`（§6 Plan 5 + §4.5 修正）

---

### Task 1: 验证 + 加固 assemble 底稿容错

**Files:**
- Test: `packages/core/tests/services/test_report_assembler.py`（若不存在则建）

**目标：** 确认 `ReportAssembler.assemble` 在部分 per-class deliverable 缺失时**仍产出** comprehensive report（底稿兜底，不抛错）。

- [ ] **Step 1: Write failing/covering test**

```python
# packages/core/tests/services/test_report_assembler.py
import pytest
from pathlib import Path
from shannon_core.services.report_assembler import ReportAssembler


@pytest.mark.asyncio
async def test_assemble_produces_report_even_when_some_classes_missing(tmp_path):
    """部分 per-class deliverable 缺 → assemble 仍产 comprehensive report(底稿兜底)。"""
    deliverables = tmp_path / "deliverables"; deliverables.mkdir()
    # 只给一个 class 的 analysis deliverable,其余缺
    (deliverables / "injection_analysis_deliverable.md").write_text("# INJ findings\n...")
    report_path = deliverables / "comprehensive_security_assessment_report.md"

    await ReportAssembler.assemble(deliverables, ["injection", "xss", "auth"], report_path)

    assert report_path.exists()  # 底稿一定产生
    content = report_path.read_text()
    assert "INJ findings" in content  # 给了的 class 进报告


@pytest.mark.asyncio
async def test_assemble_falls_back_through_three_levels(tmp_path):
    """三级回退:evidence → findings → analysis_deliverable(注释 line 999)。"""
    deliverables = tmp_path / "deliverables"; deliverables.mkdir()
    (deliverables / "xss_findings.md").write_text("# XSS findings\n")  # 只有 findings 级
    report_path = deliverables / "comprehensive_security_assessment_report.md"

    await ReportAssembler.assemble(deliverables, ["xss"], report_path)
    assert report_path.exists() and "XSS findings" in report_path.read_text()
```

- [ ] **Step 2: Run — verify PASS or identify gap**

`cd packages/core && uv run pytest tests/services/test_report_assembler.py -q`

> **若 PASS**：assemble 容错已具备，Task 1 仅增加回归覆盖（commit 测试）。
> **若 FAIL**（assemble 在 per-class 缺时抛错）：这是 report 的真实 Missing 风险点——修 `ReportAssembler.assemble` 让缺失 class 跳过（记 warning）而非抛错，保底稿一定产生。

- [ ] **Step 3: Commit**

`git add packages/core/tests/services/test_report_assembler.py && git commit -m "test(report): assemble 底稿容错回归(per-class 缺仍产 report)"`

---

### Task 2: report-executive prompt 明确「Edit 优先、不破坏底稿」

**Files:**
- Modify: `prompts/report-executive.txt`

- [ ] **Step 1: 微调 prompt**

`report-executive.txt` 已是「MODIFYING an existing file」语义，但 `inject_attack_chains` 注释（activities.py:1027）说 agent「重写」整个文件。为降低 GLM 覆写时丢内容的风险，在 prompt 强化：

- 明确「**用 Edit 工具修改,不要用 Write 整文件覆写**；若必须 Write，必须保留 assemble 拼接的所有 per-class section 原文，只在顶部加执行摘要、清理冗余」
- 明确「**绝不删除** per-class 的漏洞证据 section（ID/代码/命令/路径/行号保留）」

> 这不改 report 机制（仍是 assemble 底稿 + agent 增强），只降低 agent 覆写破坏底稿的概率。即使 agent 破坏，底稿文件仍存在（不 Missing）——这是质量优化，非 Missing 修复。

- [ ] **Step 2: Commit**

`git add prompts/report-executive.txt && git commit -m "feat(prompts): report-executive 强化 Edit 优先 + 不破坏底稿语义"`

---

### Task 3: 移除临时诊断 `_enrich_missing_deliverable_error`

**Files:**
- Modify: `packages/core/src/shannon_core/agents/executor.py`
- Delete: `packages/core/tests/test_executor_missing_deliverable_diagnostics.py`

**背景：** Plan 1-4 治本后，所有 md agent（pre-recon/recon/vuln/exploit）都由 host 渲染（collector+renderer），不再触发 Missing deliverable；report 由 assemble 底稿兜底。临时诊断 `_enrich_missing_deliverable_error`（2026-07-17 加的「先诊断不改行为」过渡物）失去意义，移除。

- [ ] **Step 1: 移除诊断代码**

`executor.py`：把 Plan 1 加的 try/except enrich 还原为直接调用：

```python
# executor.py —— 还原 validate_deliverable 调用(移除 enrich try/except):
if not skip_artifact_postprocess:
    await validate_deliverable(deliverables, agent_name)
```

删除 `_enrich_missing_deliverable_error` 函数定义（executor.py 末尾）。

- [ ] **Step 2: 删除诊断测试**

`git rm packages/core/tests/test_executor_missing_deliverable_diagnostics.py`

- [ ] **Step 3: Run — verify PASS**

`cd packages/core && uv run pytest tests/test_executor_artifact_postprocess.py tests/collectors/ tests/renderers/ -q` → passed（诊断移除后 executor 正常，collector 路径不受影响）。

- [ ] **Step 4: Commit**

`git rm packages/core/tests/test_executor_missing_deliverable_diagnostics.py && git add packages/core/src/shannon_core/agents/executor.py && git commit -m "refactor(executor): 移除临时诊断 _enrich_missing_deliverable_error(治本后无用)"`

---

### Task 4: 修正 spec + 收尾 memory

**Files:**
- Modify: `docs/superpowers/specs/2026-07-17-host-rendered-deliverables-design.md`
- Update memory: `pre-recon-md-deliverable-glm-forget-write.md`

- [ ] **Step 1: 修正 spec**

`§4.5 诊断去留` + `§6 分阶段`：
- §4.5：诊断已在 Plan 5 Task 3 移除（治本后无用）。
- §6 Plan 5：从「report（findings-renderer）+ 移除诊断」**修正**为「report 加固（assemble 底稿容错，**不引入 collector**）+ 移除诊断」。补一段说明：查证发现 report 机制是「host assemble 底稿 + agent 覆写」，Missing 风险低于前 4 个，故不做 collector 治本。

- [ ] **Step 2: 更新 memory**

`pre-recon-md-deliverable-glm-forget-write.md`：更新「治本方向」为「Plan 1-4 已恢复 host 渲染（pre-recon/recon/vuln/exploit）；Plan 5 修正 report 不需 collector（assemble 底稿兜底）+ 移除诊断」。

- [ ] **Step 3: Commit**

`git add docs/superpowers/specs/2026-07-17-host-rendered-deliverables-design.md && git commit -m "docs(spec): 修正 Plan 5 — report 不需 collector(assemble 底稿兜底)"`

---

## Self-Review

**Spec coverage:** §6 Plan 5 修正后（report 加固 + 移除诊断）→ Task 1-4 ✓；§4.5 诊断移除 → Task 3 ✓。

**诚实修正：** spec 原把 report 列入 collector 治本 scope，查证 PY report 机制（assemble 底稿 + agent 覆写）后修正为「不需 collector」。这是查证驱动的 spec 修正，已在 Architecture + Task 4 说明。

**report 风险再评估：** report 的真实风险不是 Missing（assemble 底稿兜底），而是 agent 覆写破坏底稿质量——Task 2 优化（Edit 优先），但即使破坏也不 Missing（底稿文件在）。

**已知执行期风险：**
- Task 1 若发现 assemble 不容错（per-class 缺抛错）→ 这是 report 真 Missing 风险，修 ReportAssembler（Task 1 Step 2 标注）。
- Task 3 移除诊断后，确认无其他代码引用 `_enrich_missing_deliverable_error`（grep 验证）。

---

## 全局收尾（5 plan 完成后）

- **端到端真机验证**：跑一次完整白盒（NodeGoat，glm-anthropic），确认 pre-recon/recon/vuln/exploit 全部 host 渲染 + report assemble 底稿，workflow.log 无任何 `Missing deliverable`。
- **更新 memory**：[[pre-recon-md-deliverable-glm-forget-write]] 标记治本全部完成（Plan 1-5）。
- **commit spec + 5 plan**（若用户指示）。
