# Spec C — vuln agent 消费确定性数据流产物（LLM Consumption）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Spec B 的 `SinkCallSite` 与 Spec A 的 `TaintFlow`/`ParameterPropagationGraph` 格式化成一份 LLM 友好的 `static_dataflow_hints.md`，在 vuln agents 启动前生成，并通过 prompt 把它作为"可选补充线索"喂给 vuln/recon agents，加速追链起点、提供交叉验证，但**不改变研判归 LLM** 的分工。

**Architecture:**
- **纯函数格式化层**（`audit_input_builder.py`）：新增 `build_static_dataflow_hints(index, pgraph, audit_plan) -> str`，由 4 个私有渲染段组成（header / sink inventory / taint flows / coverage disclaimer）。sink 清单按 `AuditPlan` 的 tier 排序——通过从 `index.chains` + `audit_plan.scores` 重建 `func_id → tier` 映射实现（因为 `ChainRiskScore` 只存截断的 `chain_id`）。
- **产出层**（新 activity `run_render_dataflow_hints`）：在 `run_risk_scoring` 之后、`run_vuln_agent` 之前执行，读 `code_index.json`/`parameter_graph.json`/`audit_plan.json`，调格式化函数，用 `atomic_write_text` 写 `static_dataflow_hints.md`。`pipeline_testing_mode=True` 时跳过（CI 关闭摘要）。
- **消费层**（prompt）：新建 `prompts/shared/_static-dataflow-hints.txt`（静态指引文本，声明"线索非结论"+"怎么用"），6 个 prompt（5 个 vuln + recon）用 `@include` 引入。摘要 `.md` 文件由 LLM 自己读。
- **诚实边界**：`needs_review`/`has_sanitizer_hint`/`skipped_languages`/`confidence` 全部在摘要与 prompt 中渲染成对应的"需复核/不代表有效/未覆盖/过近似"指引语。

**Tech Stack:** Python 3.12+、pydantic v2、pytest、Temporal activity（`@activity.defn`）、PromptManager（`@include` + `{{KEY}}` 插值）。

**前置依赖（开工前必须核对）：**
- Spec B 已合入：`SinkCallSite`/`DangerousSlot`/`SlotContext`/`SinkCategory` 可用（`packages/core/src/shannon_core/code_index/parameter_models.py:100-139`），`CodeIndex.sink_call_sites` 已被 `build_code_index` 填充（`packages/core/src/shannon_core/code_index/models.py:83`）。
- Spec A 已合入：`TaintFlow` 含 `sink_call_site_id`/`sink_slot`/`tainted_arg_index`/`confidence`/`has_sanitizer_hint`/`notes`（`parameter_models.py:52-82`）；`ParameterPropagationGraph` 含 `language_coverage`/`skipped_languages`（`:84-93`）；`parameter_graph.json` 由 `run_risk_scoring` 之前的 `run_code_index` 产出。
- `AuditPlan` 含 `scores: list[ChainRiskScore]`，`ChainRiskScore` 有 `chain_id`（`"→".join(chain.path[:4])`）、`total`、`tier`（`tiered_audit.py:23`、`risk_scorer.py:53`）。

**重要时序说明（影响 recon）：**
`recon` agent 在 `run_risk_scoring` **之前**跑（`workflows.py:138`），而摘要生成在 `run_risk_scoring` 之后。所以 **recon 跑时摘要文件尚未存在**。这正好命中 spec §6.2 的"早期阶段文件不存在→降级"设计：recon 的 prompt 仍 `@include` 指引文本，但 LLM 读不到 `.md` 文件时自然忽略。对 5 个 vuln agent 无此问题（它们在摘要生成之后跑）。

**范围边界（Out of scope）：**
spec §4.4 提到 `audit-tier1.txt`（tier1 轻量扫描 agent）"同样消费摘要"。但据 spec §1.2，`audit-tier1` agent 当前"未完全接线"（主流程审计由 5 个 vuln agents 承担），且 `audit-tier1.txt` 无 `<starting_context>` 段、不在 §4.3 的 prompt 改动列表内。故本计划**不修改 `audit-tier1.txt`**——待 tier1 audit 正式接入主流程时，再按 Task 5 同款 `@include` 补上。

---

## File Structure

| 文件 | 操作 | 职责 |
|---|---|---|
| `packages/core/src/shannon_core/utils/atomic_write.py` | **修改** | 新增 `atomic_write_text(path, text)` —— markdown deliverable 的原子写入，对称于 `atomic_write_json` |
| `packages/core/src/shannon_core/code_index/audit_input_builder.py` | **修改** | 新增 `build_static_dataflow_hints` + 私有渲染函数（`_func_id_to_tier`/`_header`/`_sink_inventory`/`_taint_flows`/`_coverage_disclaimer`） |
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | **修改** | 新增 `run_render_dataflow_hints` activity；读三份 json → 调格式化 → 写 md；`pipeline_testing_mode` 跳过 |
| `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | **修改** | 在 `run_risk_scoring`（`:147`）之后、vuln tasks（`:153`）之前插入 `run_render_dataflow_hints` 调用 |
| `prompts/shared/_static-dataflow-hints.txt` | **新建** | 静态指引文本（线索非结论 + 怎么用），各 vuln/recon prompt `@include` |
| `prompts/vuln-injection.txt`、`vuln-xss.txt`、`vuln-ssrf.txt`、`vuln-auth.txt`、`vuln-authz.txt`、`recon.txt` | **修改** | 在 `</starting_context>` 后新增 `@include(shared/_static-dataflow-hints.txt)` |
| `packages/core/tests/code_index/test_atomic_write.py` | **新建** | `atomic_write_text` 单测 |
| `packages/core/tests/code_index/test_static_dataflow_hints.py` | **新建** | `build_static_dataflow_hints` 及各私有段单测（含 tier 排序、needs_review、sanitizer_hint、skipped_languages） |
| `packages/core/tests/prompts/test_static_hints_render.py` | **新建** | `PromptManager.load_sync` 渲染 vuln-injection 时 `@include` 展开；pipeline-testing 下不含该段 |
| `packages/whitebox/tests/pipeline/test_render_dataflow_hints.py` | **新建** | activity 集成单测：fixture repo → 生成 `static_dataflow_hints.md`；`pipeline_testing_mode` 跳过 |

---

## Task 1: 新增 `atomic_write_text` 原子写入工具

**Files:**
- Modify: `packages/core/src/shannon_core/utils/atomic_write.py`
- Test: `packages/core/tests/code_index/test_atomic_write.py` (新建)

**Why:** `static_dataflow_hints.md` 是 markdown，需原子写入（tmp + rename），保证 vuln agents 永不读到半截文件。与现有 `atomic_write_json` 对称，可被 Spec C 及后续 deliverable 复用。

- [x] **Step 1: 写失败测试 — 原子写入文本 + 不留 tmp**

新建 `packages/core/tests/code_index/test_atomic_write.py`：

```python
"""Spec C: atomic_write_text —— markdown deliverable 的原子写入。"""
from pathlib import Path

from shannon_core.utils.atomic_write import atomic_write_text


def test_writes_text_content(tmp_path: Path):
    target = tmp_path / "out.md"
    atomic_write_text(target, "# Hello\n- a\n- b\n")
    assert target.read_text(encoding="utf-8") == "# Hello\n- a\n- b\n"


def test_creates_parent_dirs(tmp_path: Path):
    target = tmp_path / "nested" / "deep" / "out.md"
    atomic_write_text(target, "x")
    assert target.read_text(encoding="utf-8") == "x"


def test_no_tmp_file_left_behind(tmp_path: Path):
    target = tmp_path / "out.md"
    atomic_write_text(target, "payload")
    # 成功写入后不应残留 .tmp 文件
    assert not (tmp_path / "out.md.tmp").exists()
    assert not (tmp_path / "out.tmp").exists()


def test_overwrite_replaces_existing(tmp_path: Path):
    target = tmp_path / "out.md"
    target.write_text("OLD", encoding="utf-8")
    atomic_write_text(target, "NEW")
    assert target.read_text(encoding="utf-8") == "NEW"
```

- [x] **Step 2: 运行测试确认失败**

Run: `uv run pytest packages/core/tests/code_index/test_atomic_write.py -v`
Expected: FAIL — `ImportError: cannot import name 'atomic_write_text'`

- [x] **Step 3: 实现 `atomic_write_text`**

在 `packages/core/src/shannon_core/utils/atomic_write.py` 末尾追加（与 `atomic_write_json` 同模式：parent mkdir → 写 `.tmp` → rename，失败清理 tmp）：

```python
def atomic_write_text(path: Path, text: str) -> None:
    """Atomically write a text file: write to .tmp then rename.

    Symmetric to atomic_write_json; use for markdown/plain deliverables so
    concurrent readers (e.g. vuln agents starting after risk scoring) never
    observe a partially-written file.

    Args:
        path: Target file path.
        text: Text content to write (UTF-8).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_text(text, encoding="utf-8")
        tmp_path.rename(path)  # POSIX rename is atomic
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
```

- [x] **Step 4: 运行测试确认通过**

Run: `uv run pytest packages/core/tests/code_index/test_atomic_write.py -v`
Expected: PASS (4 passed)

- [x] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/utils/atomic_write.py packages/core/tests/code_index/test_atomic_write.py
git commit -m "feat(spec-c): add atomic_write_text for markdown deliverables"
```

---

## Task 2: tier 映射 + Sink Inventory + Header（`audit_input_builder.py`）

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/audit_input_builder.py:13-18`（imports）+ 文件末尾追加
- Test: `packages/core/tests/code_index/test_static_dataflow_hints.py` (新建)

**Why:** Spec §4.1.1/§4.1.2 —— 摘要的覆盖范围声明 + 按 tier 排序的 sink 清单。这是把 `SinkCallSite` 关联到审计优先级的核心逻辑。`AuditPlan` 只存 `ChainRiskScore`（`chain_id` 截断），故需从 `index.chains` 重建 `func_id → tier`。

- [x] **Step 1: 写失败测试 — `_func_id_to_tier` 重建正确**

新建 `packages/core/tests/code_index/test_static_dataflow_hints.py`：

```python
"""Spec C: build_static_dataflow_hints —— 确定性数据流摘要渲染。"""
from shannon_core.code_index.audit_input_builder import (
    _func_id_to_tier,
    _header,
    _sink_inventory,
)
from shannon_core.code_index.models import CallChain, CodeIndex
from shannon_core.code_index.parameter_models import (
    DangerousSlot,
    ParameterPropagationGraph,
    SinkCallSite,
    SinkCategory,
    SlotContext,
)
from shannon_core.code_index.risk_scorer import ChainRiskScore
from shannon_core.code_index.tiered_audit import AuditPlan


def _site(
    site_id: str, caller_id: str, *, category=SinkCategory.SQL,
    subtype="sql_raw", callee="execute", receiver="cursor",
    file_path="db.py", line=10, column=4, rule_id="py-db-cursor-execute",
    needs_review=False, slots=None,
) -> SinkCallSite:
    return SinkCallSite(
        id=site_id, caller_id=caller_id, callee_name=callee,
        callee_receiver=receiver, category=category, sink_subtype=subtype,
        file_path=file_path, line=line, column=column,
        dangerous_slots=slots or [DangerousSlot(arg_index=0, slot=SlotContext.SQL_VALUE, expression="q", is_entry_hint=True)],
        rule_id=rule_id, needs_review=needs_review,
    )


def _index(chains, sites) -> CodeIndex:
    return CodeIndex(
        repository="demo", language="python",
        total_blocks=1, total_entry_points=1, total_chains=len(chains),
        blocks=[], edges=[], entry_points=[], chains=chains,
        sink_call_sites=sites,
    )


class TestFuncIdToTier:
    def test_maps_func_to_highest_tier_across_chains(self):
        # chain A: tier3（total=30），含 f1、f2
        # chain B: tier1（total=5），含 f2、f3
        chains = [
            CallChain(entry_point_id="a.py:f1:1", path=["a.py:f1:1", "a.py:f2:1"],
                      depth=1, has_unresolved=False),
            CallChain(entry_point_id="a.py:f2:1", path=["a.py:f2:1", "a.py:f3:1"],
                      depth=1, has_unresolved=False),
        ]
        scores = [
            ChainRiskScore(chain_id="a.py:f1:1→a.py:f2:1", sink_danger=10,
                           taint_completeness=10, auth_gap=8, depth=2),   # total=30 → tier3
            ChainRiskScore(chain_id="a.py:f2:1→a.py:f3:1", sink_danger=2,
                           taint_completeness=0, auth_gap=0, depth=2),    # total=4 → tier1
        ]
        plan = AuditPlan(total_chains=2, scores=scores)
        index = _index(chains, [])
        mapping = _func_id_to_tier(index, plan)
        assert mapping["a.py:f1:1"] == 3
        assert mapping["a.py:f3:1"] == 1
        # f2 同时在 tier3 链和 tier1 链上 → 取最高优先级 tier3
        assert mapping["a.py:f2:1"] == 3

    def test_func_not_in_any_chain_absent(self):
        chains = [CallChain(entry_point_id="a.py:f1:1", path=["a.py:f1:1"],
                            depth=0, has_unresolved=False)]
        plan = AuditPlan(total_chains=1, scores=[
            ChainRiskScore(chain_id="a.py:f1:1", sink_danger=10, taint_completeness=10,
                           auth_gap=8, depth=1),  # tier3
        ])
        index = _index(chains, [])
        mapping = _func_id_to_tier(index, plan)
        assert "a.py:orphan:1" not in mapping


class TestHeader:
    def test_renders_coverage_and_disclaimer(self):
        text = _header(["python", "typescript"], ["go", "java", "php"])
        assert "Static Dataflow Hints" in text
        assert "python" in text and "typescript" in text
        assert "go" in text and "java" in text and "php" in text
        assert "线索" in text  # 线索非结论提醒

    def test_empty_skipped_languages(self):
        text = _header(["python"], [])
        assert "python" in text
        # 无未覆盖语言时不应崩
        assert "未覆盖语言" in text or "无" in text


class TestSinkInventory:
    def test_higher_tier_sinks_ranked_first(self):
        chains = [
            CallChain(entry_point_id="db.py:h3:1", path=["db.py:h3:1"],
                      depth=0, has_unresolved=False),
            CallChain(entry_point_id="db.py:h1:1", path=["db.py:h1:1"],
                      depth=0, has_unresolved=False),
        ]
        scores = [
            ChainRiskScore(chain_id="db.py:h3:1", sink_danger=10, taint_completeness=10,
                           auth_gap=8, depth=1),   # tier3
            ChainRiskScore(chain_id="db.py:h1:1", sink_danger=2, taint_completeness=2,
                           auth_gap=0, depth=1),    # tier1
        ]
        plan = AuditPlan(total_chains=2, scores=scores)
        index = _index(chains, [
            _site("db.py:h3:execute:10:4", "db.py:h3:1"),
            _site("db.py:h1:execute:20:4", "db.py:h1:1"),
        ])
        func_to_tier = _func_id_to_tier(index, plan)
        text = _sink_inventory(index.sink_call_sites, func_to_tier)
        pos3 = text.index("Tier 3")
        pos1 = text.index("Tier 1")
        assert pos3 < pos1
        # tier3 的 sink 出现在 tier1 之前
        assert text.index("db.py:h3:execute:10:4") < text.index("db.py:h1:execute:20:4")

    def test_needs_review_marked(self):
        func_to_tier = {"db.py:h:1": 3}
        sites = [_site("db.py:h:execute:10:4", "db.py:h:1", needs_review=True)]
        text = _sink_inventory(sites, func_to_tier)
        assert "needs_review" in text

    def test_slot_and_rule_rendered(self):
        func_to_tier = {"db.py:h:1": 3}
        sites = [_site("db.py:h:execute:10:4", "db.py:h:1")]
        text = _sink_inventory(sites, func_to_tier)
        assert "sql_value" in text          # 槽位
        assert "py-db-cursor-execute" in text  # rule_id
        assert "cursor.execute" in text     # receiver.callee

    def test_orphan_sink_in_unranked_section(self):
        func_to_tier = {}  # 无 chain 关联
        sites = [_site("db.py:orphan:execute:99:4", "db.py:orphan:1")]
        text = _sink_inventory(sites, func_to_tier)
        # 未归类到任何 tier 的 sink 仍应出现（避免静默丢弃）
        assert "db.py:orphan:execute:99:4" in text
```

- [x] **Step 2: 运行测试确认失败**

Run: `uv run pytest packages/core/tests/code_index/test_static_dataflow_hints.py -v`
Expected: FAIL — `ImportError: cannot import name '_func_id_to_tier'`

- [x] **Step 3: 扩展 imports**

在 `packages/core/src/shannon_core/code_index/audit_input_builder.py` 顶部，把现有 import 块（`:15-16`）替换为：

```python
from shannon_core.code_index.models import CallChain, CodeIndex, FuncBlock
from shannon_core.code_index.parameter_models import (
    DangerousSlot,
    ParameterPropagationGraph,
    SinkCallSite,
    TaintFlow,
)
from shannon_core.code_index.tiered_audit import AuditPlan
```

- [x] **Step 4: 实现私有渲染函数（tier 映射 + header + sink inventory）**

在 `audit_input_builder.py` 末尾追加：

```python
# === Spec C: static dataflow hints (consumption-side) ===

_TIER_TITLES = {
    3: "Tier 3（高风险链）",
    2: "Tier 2（中风险链）",
    1: "Tier 1（低风险链）",
    0: "未归类（无 chain 关联）",
}


def _func_id_to_tier(index: CodeIndex, audit_plan: AuditPlan) -> dict[str, int]:
    """Map each FuncBlock.id to the highest-priority tier of any chain whose
    path contains it. Drives tier-sorted sink inventory (Spec §4.4).

    AuditPlan stores ChainRiskScore (chain_id = '→'.join(path[:4])), so we
    re-derive the same key from index.chains to look up each chain's tier.
    A func that sits on both a tier3 and a tier1 chain keeps tier3 (max).
    """
    tier_by_chain_key = {score.chain_id: score.tier for score in audit_plan.scores}
    func_to_tier: dict[str, int] = {}
    for chain in index.chains:
        key = "→".join(chain.path[:4])
        tier = tier_by_chain_key.get(key)
        if tier is None:
            continue
        for func_id in chain.path:
            prev = func_to_tier.get(func_id, 0)
            if tier > prev:
                func_to_tier[func_id] = tier
    return func_to_tier


def _header(language_coverage: list[str], skipped_languages: list[str]) -> str:
    covered = ", ".join(language_coverage) if language_coverage else "（无）"
    skipped = ", ".join(skipped_languages) if skipped_languages else "无"
    return (
        "# Static Dataflow Hints（确定性静态线索，需 LLM 验证）\n\n"
        "## 覆盖范围\n"
        f"- 已静态分析语言：{covered}\n"
        f"- 未覆盖语言（无静态污点线索，请自行追链）：{skipped}\n"
        "- ⚠️ 本文件是【线索】非【结论】。静态未列出的 sink/路径不代表安全。"
    )


def _format_callee(site: SinkCallSite) -> str:
    if site.callee_receiver:
        return f"{site.callee_receiver}.{site.callee_name}"
    return site.callee_name


def _format_slots(slots: list[DangerousSlot]) -> str:
    """Render dangerous slots as '(arg_index, slot_value); ...'."""
    parts = [f"({s.arg_index}, {s.slot.value})" for s in slots]
    return "; ".join(parts) if parts else "—"


def _sink_inventory(
    sink_call_sites: list[SinkCallSite],
    func_to_tier: dict[str, int],
) -> str:
    """Render sink call sites grouped by audit tier (tier3 first)."""
    if not sink_call_sites:
        return "## Sink 调用点\n（本仓库无静态命中的 sink 调用点。）"

    buckets: dict[int, list[SinkCallSite]] = {3: [], 2: [], 1: [], 0: []}
    for site in sink_call_sites:
        tier = func_to_tier.get(site.caller_id, 0)
        buckets.setdefault(tier, []).append(site)

    lines = ["## Sink 调用点（按审计优先级）"]
    for tier in (3, 2, 1, 0):
        sites = buckets.get(tier, [])
        if not sites:
            continue
        lines.append(f"### {_TIER_TITLES[tier]}")
        for s in sites:
            review = " · ⚠️needs_review" if s.needs_review else ""
            lines.append(
                f"- `{s.file_path}:{s.line}:{s.column}` "
                f"{s.category.value}/{s.sink_subtype} @ `{_format_callee(s)}` "
                f"· 危险槽: {_format_slots(s.dangerous_slots)} · rule={s.rule_id}"
                f"{review}"
            )
    return "\n".join(lines)
```

- [x] **Step 5: 运行测试确认通过**

Run: `uv run pytest packages/core/tests/code_index/test_static_dataflow_hints.py -v`
Expected: PASS（`TestFuncIdToTier` / `TestHeader` / `TestSinkInventory` 全绿）

- [x] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/code_index/audit_input_builder.py packages/core/tests/code_index/test_static_dataflow_hints.py
git commit -m "feat(spec-c): tier-sorted sink inventory + coverage header in audit_input_builder"
```

---

## Task 3: Taint Flows + Disclaimer + `build_static_dataflow_hints` 组合入口

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/audit_input_builder.py`（末尾追加）
- Test: `packages/core/tests/code_index/test_static_dataflow_hints.py`（追加）

**Why:** Spec §4.1.3/§4.1.4 —— source→sink 链渲染 + 边界免责声明；§4.1 组合入口把四段拼成全文。

- [x] **Step 1: 写失败测试 — taint flows / disclaimer / 组合入口**

在 `packages/core/tests/code_index/test_static_dataflow_hints.py` 末尾追加：

```python
from shannon_core.code_index.audit_input_builder import (
    _coverage_disclaimer,
    _taint_flows,
    build_static_dataflow_hints,
)
from shannon_core.code_index.models import ParameterSource
from shannon_core.code_index.parameter_models import (
    ParameterPropagationGraph,
    PropagationStep,
    TaintFlow,
)


def _flow(
    *, entry="routes.py:getUser:10", source_param="uid",
    source_type=ParameterSource.QUERY_PARAM, sink_id="db.py:exec:execute:42:18",
    slot=SlotContext.SQL_VALUE, arg=0, confidence=0.6, sanitizer=False,
    notes="", steps=None,
) -> TaintFlow:
    return TaintFlow(
        flow_id=f"{entry}->{sink_id}",
        entry_point_id=entry, source_param=source_param, source_type=source_type,
        propagation_steps=steps or [], sink_call_site_id=sink_id,
        sink_slot=slot, tainted_arg_index=arg, confidence=confidence,
        has_sanitizer_hint=sanitizer, notes=notes,
    )


class TestTaintFlows:
    def test_renders_entry_to_sink_with_slot_and_arg(self):
        flows = [_flow(steps=[
            PropagationStep(step_id="s1", from_func_id="routes.py:getUser:10",
                            from_param="uid", to_func_id="db.py:exec:42",
                            to_param="sql", transformation="concat",
                            code_location="routes.py:12"),
        ])]
        text = _taint_flows(flows)
        assert "routes.py:getUser:10" in text
        assert "uid" in text
        assert "query" in text
        assert "sql_value" in text
        assert "arg0" in text or "arg=0" in text
        assert "concat" in text  # transformation 步骤

    def test_sanitizer_hint_flagged_as_not_effective(self):
        flows = [_flow(sanitizer=True, steps=[
            PropagationStep(step_id="s1", from_func_id="a.py:f:1", from_param="x",
                            to_func_id="b.py:g:1", to_param="y",
                            transformation="sanitize_hint:escape", code_location="a.py:3"),
        ])]
        text = _taint_flows(flows)
        assert "sanitize_hint" in text
        assert "不代表有效" in text  # 显式声明 sanitizer hint 非有效性

    def test_confidence_and_notes_rendered(self):
        flows = [_flow(confidence=0.4, notes="容器字段过近似")]
        text = _taint_flows(flows)
        assert "0.40" in text
        assert "容器字段过近似" in text

    def test_no_flows_message(self):
        text = _taint_flows([])
        assert "无" in text or "no" in text.lower()

    def test_flow_renders_sink_call_site_id_directly(self):
        # flow 的 sink_call_site_id 直接渲染进输出（无需额外反查）
        flows = [_flow(sink_id="db.py:missing:execute:1:1")]
        text = _taint_flows(flows)
        assert "db.py:missing:execute:1:1" in text


class TestCoverageDisclaimer:
    def test_lists_skipped_languages_and_caveats(self):
        pgraph = ParameterPropagationGraph(
            taint_flows=[], language_coverage=["python"], skipped_languages=["go", "java"],
        )
        text = _coverage_disclaimer(pgraph)
        assert "go" in text and "java" in text
        assert "needs_review" in text
        assert "sanitize_hint" in text
        assert "confidence" in text


class TestBuildStaticDataflowHints:
    def test_assembles_all_sections(self):
        chains = [CallChain(entry_point_id="db.py:h:1", path=["db.py:h:1"],
                            depth=0, has_unresolved=False)]
        plan = AuditPlan(total_chains=1, scores=[
            ChainRiskScore(chain_id="db.py:h:1", sink_danger=10, taint_completeness=10,
                           auth_gap=8, depth=1),  # tier3
        ])
        index = _index(chains, [_site("db.py:h:execute:10:4", "db.py:h:1")])
        pgraph = ParameterPropagationGraph(
            taint_flows=[_flow(entry="db.py:h:1")],
            language_coverage=["python"], skipped_languages=["go"],
        )
        md = build_static_dataflow_hints(index, pgraph, plan)
        # 四段都在
        assert "# Static Dataflow Hints" in md
        assert "## Sink 调用点" in md
        assert "## 污点流" in md
        assert "## 边界与局限" in md
        # tier3 段标题
        assert "Tier 3" in md

    def test_empty_index_still_renders_disclaimer(self):
        index = _index([], [])
        pgraph = ParameterPropagationGraph(taint_flows=[], language_coverage=[],
                                           skipped_languages=["go", "java", "php"])
        plan = AuditPlan()
        md = build_static_dataflow_hints(index, pgraph, plan)
        assert "# Static Dataflow Hints" in md
        assert "## 边界与局限" in md
        assert "go" in md
```

- [x] **Step 2: 运行测试确认失败**

Run: `uv run pytest packages/core/tests/code_index/test_static_dataflow_hints.py -v`
Expected: FAIL — `ImportError: cannot import name '_coverage_disclaimer' / '_taint_flows' / 'build_static_dataflow_hints'`

- [x] **Step 3: 实现 `_taint_flows` / `_coverage_disclaimer` / 组合入口**

在 `audit_input_builder.py` 末尾追加：

```python
def _format_steps(steps: list) -> str:
    """Render propagation steps as 'transform@location · ...'."""
    if not steps:
        return "（无中间步骤）"
    parts = []
    for st in steps:
        tag = f"{st.transformation}@{st.code_location}" if st.transformation else st.code_location
        parts.append(tag)
    return " · ".join(parts)


def _taint_flows(flows: list[TaintFlow]) -> str:
    """Render source→sink flows with slot/arg/confidence/sanitizer caveats.

    Flow already carries sink_slot/tainted_arg_index/confidence/has_sanitizer_hint,
    so no SinkCallSite lookup is needed here.
    """
    if not flows:
        return "## 污点流（entry → sink）\n（本仓库无可达 sink 的污点流。）"

    lines = ["## 污点流（entry → sink）"]
    for flow in flows:
        sink_loc = flow.sink_call_site_id or "（未定位 sink）"
        slot = flow.sink_slot.value if flow.sink_slot else "generic"
        lines.append(
            f"- entry `{flow.entry_point_id}` "
            f"(param `{flow.source_param}`, source={flow.source_type.value})\n"
            f"  → {sink_loc} slot={slot} arg={flow.tainted_arg_index}\n"
            f"  · steps: {_format_steps(flow.propagation_steps)}"
        )
        if flow.has_sanitizer_hint:
            lines.append(
                "  · ⚠️sanitize_hint 出现疑似 sanitizer（不代表有效，请复核 concat-after-sanitize）"
            )
        notes_bits = [f"confidence={flow.confidence:.2f}"]
        if flow.notes:
            notes_bits.append(f"notes: {flow.notes}")
        lines.append("  · " + " · ".join(notes_bits))
    return "\n".join(lines)


def _coverage_disclaimer(pgraph: ParameterPropagationGraph) -> str:
    skipped = ", ".join(pgraph.skipped_languages) if pgraph.skipped_languages else "无"
    return (
        "## 边界与局限\n"
        f"- 动态调用、模板 XSS、未覆盖语言（{skipped}）不在静态覆盖内，仍须用 Task agent 自主覆盖。\n"
        "- `needs_review` 的 sink 需重点复核转义/上下文。\n"
        "- `sanitize_hint` 仅表示路径出现疑似 sanitizer，不代表有效——须按 slot 上下文判定并检查 concat-after-sanitize。\n"
        "- `confidence` 仅反映静态映射可信度，低或过近似时以 LLM 自己的数据流追踪为准。"
    )


def build_static_dataflow_hints(
    index: CodeIndex,
    pgraph: ParameterPropagationGraph,
    audit_plan: AuditPlan,
) -> str:
    """Produce the full `static_dataflow_hints.md` text (Spec §4.1).

    Consumes Spec B (SinkCallSite via index.sink_call_sites) + Spec A
    (TaintFlow / coverage via pgraph) + tiered audit priority (audit_plan),
    and emits LLM-friendly markdown with honest static-boundary caveats.
    """
    func_to_tier = _func_id_to_tier(index, audit_plan)
    parts = [
        _header(pgraph.language_coverage, pgraph.skipped_languages),
        _sink_inventory(index.sink_call_sites, func_to_tier),
        _taint_flows(pgraph.taint_flows),
        _coverage_disclaimer(pgraph),
    ]
    return "\n\n".join(parts) + "\n"
```

- [x] **Step 4: 运行测试确认通过**

Run: `uv run pytest packages/core/tests/code_index/test_static_dataflow_hints.py -v`
Expected: PASS（全部 class 绿）

- [x] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/audit_input_builder.py packages/core/tests/code_index/test_static_dataflow_hints.py
git commit -m "feat(spec-c): build_static_dataflow_hints assembles sink/flow/disclaimer markdown"
```

---

## Task 4: `run_render_dataflow_hints` activity + workflow 接线

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`（追加 activity；参考 `run_risk_scoring` `:221`)
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py:150-153`（插入调用）
- Test: `packages/whitebox/tests/pipeline/test_render_dataflow_hints.py` (新建)

**Why:** Spec §4.2 —— 在 risk_scoring 产出 `AuditPlan` 后、vuln agents 启动前生成摘要文件，确保 vuln agents 启动时摘要已就绪。

- [x] **Step 1: 写失败测试 — activity 生成 md + pipeline_testing 跳过**

新建 `packages/whitebox/tests/pipeline/test_render_dataflow_hints.py`：

```python
"""Spec C: run_render_dataflow_hints activity —— 生成 static_dataflow_hints.md。"""
import json

import pytest

from shannon_core.code_index.models import CallChain, CodeIndex
from shannon_core.code_index.parameter_models import (
    DangerousSlot, ParameterPropagationGraph, SinkCallSite, SinkCategory,
    SlotContext,
)
from shannon_core.code_index.risk_scorer import ChainRiskScore
from shannon_core.code_index.tiered_audit import AuditPlan
from shannon_whitebox.pipeline import activities
from shannon_whitebox.pipeline.shared import ActivityInput


def _write_fixture(deliverables):
    """Write minimal code_index.json / parameter_graph.json / audit_plan.json."""
    chains = [CallChain(entry_point_id="db.py:h:1", path=["db.py:h:1"],
                        depth=0, has_unresolved=False)]
    index = CodeIndex(
        repository="demo", language="python",
        total_blocks=1, total_entry_points=1, total_chains=1,
        blocks=[], edges=[], entry_points=[], chains=chains,
        sink_call_sites=[
            SinkCallSite(
                id="db.py:h:execute:10:4", caller_id="db.py:h:1",
                callee_name="execute", callee_receiver="cursor",
                category=SinkCategory.SQL, sink_subtype="sql_raw",
                file_path="db.py", line=10, column=4,
                dangerous_slots=[DangerousSlot(arg_index=0, slot=SlotContext.SQL_VALUE,
                                               expression="q", is_entry_hint=True)],
                rule_id="py-db-cursor-execute",
            ),
        ],
    )
    (deliverables / "code_index.json").write_text(index.model_dump_json(indent=2),
                                                   encoding="utf-8")

    pgraph = ParameterPropagationGraph(
        taint_flows=[], language_coverage=["python"], skipped_languages=["go"],
    )
    (deliverables / "parameter_graph.json").write_text(pgraph.model_dump_json(indent=2),
                                                       encoding="utf-8")

    plan = AuditPlan(
        total_chains=1,
        scores=[ChainRiskScore(chain_id="db.py:h:1", sink_danger=10,
                               taint_completeness=10, auth_gap=8, depth=1)],
    )
    (deliverables / "audit_plan.json").write_text(plan.to_json(indent=2),
                                                   encoding="utf-8")


def _make_input(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    return ActivityInput(repo_path=str(repo))


def _deliverables(input):
    _, deliverables, _ = activities._get_paths(input)
    deliverables.mkdir(parents=True, exist_ok=True)
    return deliverables


@pytest.mark.asyncio
async def test_writes_static_dataflow_hints_md(tmp_path):
    input = _make_input(tmp_path)
    deliverables = _deliverables(input)
    _write_fixture(deliverables)

    result = await activities.run_render_dataflow_hints(input)

    md_path = deliverables / "static_dataflow_hints.md"
    assert md_path.exists()
    md = md_path.read_text(encoding="utf-8")
    assert "# Static Dataflow Hints" in md
    assert "## Sink 调用点" in md
    assert result["written"] is True


@pytest.mark.asyncio
async def test_skips_in_pipeline_testing_mode(tmp_path):
    input = _make_input(tmp_path)
    input.pipeline_testing_mode = True
    deliverables = _deliverables(input)
    _write_fixture(deliverables)

    result = await activities.run_render_dataflow_hints(input)

    # pipeline-testing 下不写文件
    assert not (deliverables / "static_dataflow_hints.md").exists()
    assert result["written"] is False


@pytest.mark.asyncio
async def test_missing_code_index_returns_not_written(tmp_path):
    input = _make_input(tmp_path)
    deliverables = _deliverables(input)
    # 不写任何 fixture —— code_index.json 不存在

    result = await activities.run_render_dataflow_hints(input)

    assert result["written"] is False
    assert not (deliverables / "static_dataflow_hints.md").exists()
```

> **Note:** 若仓库使用 `asyncio_mode = auto`（见 `pyproject.toml` 的 `[tool.pytest.ini_options]`），`@pytest.mark.asyncio` 可省略但保留无害。如缺少 `pytest-asyncio` 依赖导致收集失败，先确认 `packages/whitebox/tests/` 现有 async 测试如何运行（参考其 `conftest.py`）。

- [x] **Step 2: 运行测试确认失败**

Run: `uv run pytest packages/whitebox/tests/pipeline/test_render_dataflow_hints.py -v`
Expected: FAIL — `AttributeError: module ...activities has no attribute 'run_render_dataflow_hints'`

- [x] **Step 3: 实现 activity**

在 `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` 末尾追加（紧跟 `render_findings` 之后）。模式与 `run_risk_scoring` 一致（读 json → 处理 → 写 deliverable）：

```python
@activity.defn
async def run_render_dataflow_hints(input: ActivityInput) -> dict:
    """Spec C: render static_dataflow_hints.md from Spec B/A products.

    Runs after run_risk_scoring (which writes audit_plan.json) and before
    run_vuln_agent, so vuln agents see the summary as ready. Skipped in
    pipeline_testing_mode (CI does not feed hints to LLMs).
    """
    try:
        if input.pipeline_testing_mode:
            return {"written": False}

        from shannon_core.code_index.audit_input_builder import build_static_dataflow_hints
        from shannon_core.code_index.models import CodeIndex
        from shannon_core.code_index.parameter_models import ParameterPropagationGraph
        from shannon_core.code_index.tiered_audit import AuditPlan
        from shannon_core.utils.atomic_write import atomic_write_text

        repo, deliverables, _ = _get_paths(input)

        code_index_path = deliverables / "code_index.json"
        param_graph_path = deliverables / "parameter_graph.json"
        audit_plan_path = deliverables / "audit_plan.json"
        if not code_index_path.exists():
            return {"written": False}

        index = CodeIndex.model_validate_json(code_index_path.read_text())
        pgraph = (
            ParameterPropagationGraph.model_validate_json(param_graph_path.read_text())
            if param_graph_path.exists()
            else ParameterPropagationGraph()
        )
        audit_plan = (
            AuditPlan.model_validate_json(audit_plan_path.read_text())
            if audit_plan_path.exists()
            else AuditPlan()
        )

        md = build_static_dataflow_hints(index, pgraph, audit_plan)
        atomic_write_text(deliverables / "static_dataflow_hints.md", md)
        return {"written": True}
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
```

> **Note:** `AuditPlan` 已有 `to_json()`（见 `tiered_audit.py:44`）；`AuditPlan.model_validate_json` 是 pydantic v2 标准反序列化，与 `run_risk_scoring` 写入的 JSON 对称。

- [x] **Step 4: 运行测试确认通过**

Run: `uv run pytest packages/whitebox/tests/pipeline/test_render_dataflow_hints.py -v`
Expected: PASS（3 passed）

- [x] **Step 5: 在 workflow 中接线**

在 `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` 中，定位 `run_risk_scoring` 调用块（`:147-151`）：

```python
            risk_result = await workflow.execute_activity(
                activities.run_risk_scoring, act_input,
                start_to_close_timeout=timedelta(minutes=5),
            )
            self._state.audit_plan_stats = risk_result
```

在其后、`vuln_tasks = []`（`:153`）之前插入：

```python
            # Spec C: render static dataflow hints for vuln agents (after audit plan)
            await workflow.execute_activity(
                activities.run_render_dataflow_hints, act_input,
                start_to_close_timeout=timedelta(minutes=2),
            )
```

- [x] **Step 6: 运行 whitebox pipeline 测试确认未破坏现有 workflow**

Run: `uv run pytest packages/whitebox/tests/ -v -k "workflow or pipeline"`
Expected: PASS（现有 workflow 测试不应受影响——新 activity 在 risk_scoring 与 vuln 之间，无返回值消费）

- [x] **Step 7: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/src/shannon_whitebox/pipeline/workflows.py packages/whitebox/tests/pipeline/test_render_dataflow_hints.py
git commit -m "feat(spec-c): run_render_dataflow_hints activity wired after risk scoring"
```

---

## Task 5: `shared/_static-dataflow-hints.txt` + 6 个 prompt `@include`

**Files:**
- Create: `prompts/shared/_static-dataflow-hints.txt`
- Modify: `prompts/vuln-injection.txt`、`prompts/vuln-xss.txt`、`prompts/vuln-ssrf.txt`、`prompts/vuln-auth.txt`、`prompts/vuln-authz.txt`、`prompts/recon.txt`
- Test: `packages/core/tests/prompts/test_static_hints_render.py` (新建)

**Why:** Spec §4.3 —— 把"线索非结论 + 怎么用"的静态指引通过 `@include` 注入 vuln/recon prompts（单一来源，改一处全生效）。摘要 `.md` 由 LLM 自行读取；这段 partial 只是指引。

- [x] **Step 1: 新建共享指引 partial**

创建 `prompts/shared/_static-dataflow-hints.txt`：

```markdown
<static_dataflow_hints>
可选的确定性静态线索位于 `.shannon/deliverables/static_dataflow_hints.md`（若该文件不存在，跳过本段，仍按既有流程自主分析）：
- 把它作为追链【起点】与【交叉验证】：静态已定位的 sink 调用点、source→sink 流，优先据此展开验证。
- ⚠️ 它是线索，不是结论：
  - 静态【未列出】的 sink/路径 ≠ 安全——仍须用 Task agent 自主覆盖（动态调用、模板、未覆盖语言）。
  - `needs_review` 的 sink 请重点复核转义/上下文。
  - `sanitize_hint` 仅表示路径出现疑似 sanitizer，【不代表有效】——仍须按 slot 上下文判定，并检查 concat-after-sanitize。
  - `confidence` 低或 notes 提示过近似时，以你自己的数据流追踪为准。
- 你的最终判定（slot 匹配、verdict）始终以代码事实为准，静态线索仅供参考与加速。
</static_dataflow_hints>
```

- [x] **Step 2: 写失败测试 — 主路径 include 展开 + pipeline-testing 下不含**

新建 `packages/core/tests/prompts/test_static_hints_render.py`：

```python
"""Spec C: static dataflow hints partial included into vuln/recon prompts."""
from pathlib import Path

from shannon_core.prompts.manager import PromptManager

PROMPTS_DIR = Path(__file__).resolve().parents[4] / "prompts"


def _manager() -> PromptManager:
    return PromptManager(PROMPTS_DIR)


VULN_PROMPTS = [
    "vuln-injection", "vuln-xss", "vuln-ssrf", "vuln-auth", "vuln-authz",
]


class TestStaticHintsInclude:
    def test_vuln_prompts_include_static_hints_partial(self):
        for name in VULN_PROMPTS:
            rendered = _manager().load_sync(name, {"repo_path": "/repo"}, pipeline_testing=False)
            assert "<static_dataflow_hints>" in rendered, f"{name} missing static hints block"
            assert "线索，不是结论" in rendered, f"{name} missing disclaimer"

    def test_recon_prompt_includes_static_hints_partial(self):
        rendered = _manager().load_sync("recon", {"repo_path": "/repo"}, pipeline_testing=False)
        assert "<static_dataflow_hints>" in rendered

    def test_pipeline_testing_mode_excludes_hints(self):
        # pipeline-testing 下的 vuln prompt 不 include 该 partial → 摘要关闭（Spec §6.4）
        rendered = _manager().load_sync(
            "vuln-injection", {"repo_path": "/repo"}, pipeline_testing=True,
        )
        assert "<static_dataflow_hints>" not in rendered
```

> **Note:** `PROMPTS_DIR` 路径：本测试位于 `packages/core/tests/prompts/`，`parents[4]` 回到仓库根（`prompts/`/`tests/`/`core/`/`src/`/`packages/`→ 根）。若路径解析不符，改用 `Path(__file__).resolve().parents[N]` 调试打印确认。`PromptManager.load_sync` 签名见 `packages/core/src/shannon_core/prompts/manager.py:26`。

- [x] **Step 3: 运行测试确认失败**

Run: `uv run pytest packages/core/tests/prompts/test_static_hints_render.py -v`
Expected: FAIL — `AssertionError: 'vuln-injection' missing static hints block`

- [x] **Step 4: 确认每个目标 prompt 恰有一处 `</starting_context>`**

Run: `grep -n "</starting_context>" prompts/vuln-injection.txt prompts/vuln-xss.txt prompts/vuln-ssrf.txt prompts/vuln-auth.txt prompts/vuln-authz.txt prompts/recon.txt`
Expected: 每个文件恰好输出 **一行**（各 prompt 只有一段 `<starting_context>`）。

- [x] **Step 5: 对 6 个 prompt 各做一次 Edit（在 `</starting_context>` 后插入 `@include`）**

对以下每个文件执行 Edit，`old_string` = `</starting_context>`（各文件唯一），`new_string` = `</starting_context>\n\n@include(shared/_static-dataflow-hints.txt)`：

1. `prompts/vuln-injection.txt`
2. `prompts/vuln-xss.txt`
3. `prompts/vuln-ssrf.txt`
4. `prompts/vuln-auth.txt`
5. `prompts/vuln-authz.txt`
6. `prompts/recon.txt`

每个 Edit 形如：

```
old_string:
</starting_context>

new_string:
</starting_context>

@include(shared/_static-dataflow-hints.txt)
```

> **Note:** `</starting_context>` 在每个文件内唯一（Step 4 已确认），故 Edit 可安全单次替换。`@include(...)` 由 `PromptManager._process_includes`（`manager.py:53`）在 `prompts/shared/` 下解析（非 pipeline-testing 模式）。pipeline-testing 模式基目录是 `prompts/pipeline-testing/`，其 `shared/` 无此文件 → include 返回空串（`manager.py:68-70`），实现 §6.4 的"CI 关闭摘要"。

- [x] **Step 6: 运行测试确认通过**

Run: `uv run pytest packages/core/tests/prompts/test_static_hints_render.py -v`
Expected: PASS（3 passed）

- [x] **Step 7: 全量回归 —— 确保未破坏现有 prompt 渲染**

Run: `uv run pytest packages/core/tests/prompts/ -v`
Expected: PASS（现有 prompt 测试全绿；新增 partial 不应影响其它 `@include`）

- [x] **Step 8: Commit**

```bash
git add prompts/shared/_static-dataflow-hints.txt prompts/vuln-injection.txt prompts/vuln-xss.txt prompts/vuln-ssrf.txt prompts/vuln-auth.txt prompts/vuln-authz.txt prompts/recon.txt packages/core/tests/prompts/test_static_hints_render.py
git commit -m "feat(spec-c): inject static-dataflow-hints guidance into vuln/recon prompts"
```

---

## Task 6: 端到端 + 回归（pipeline-testing 关闭 / 现有行为不破坏）

**Files:**
- Test: `packages/core/tests/code_index/test_static_dataflow_hints_e2e.py` (新建)

**Why:** Spec §6.3 / §6.4 —— 验证"三份产物 → 摘要 → prompt 含线索段"的完整链路，以及 pipeline-testing 降级与现有 vuln 两层模型不破坏。

- [x] **Step 1: 写端到端测试 — 产物链路 + prompt 含线索段**

新建 `packages/core/tests/code_index/test_static_dataflow_hints_e2e.py`：

```python
"""Spec C end-to-end: Spec B/A products → static_dataflow_hints.md → prompt."""
from pathlib import Path

from shannon_core.code_index.audit_input_builder import build_static_dataflow_hints
from shannon_core.code_index.models import CallChain, CodeIndex, ParameterSource
from shannon_core.code_index.parameter_models import (
    DangerousSlot, ParameterPropagationGraph, PropagationStep, SinkCallSite,
    SinkCategory, SlotContext, TaintFlow,
)
from shannon_core.code_index.risk_scorer import ChainRiskScore
from shannon_core.code_index.tiered_audit import AuditPlan
from shannon_core.prompts.manager import PromptManager

PROMPTS_DIR = Path(__file__).resolve().parents[4] / "prompts"


def _full_fixture():
    """A tier3 SQLi chain: query param → concat → execute sink, with sanitizer hint."""
    caller = "src/db/user.py:getUser:10"
    chains = [CallChain(entry_point_id=caller, path=[caller],
                        depth=0, has_unresolved=False)]
    index = CodeIndex(
        repository="demo", language="python",
        total_blocks=1, total_entry_points=1, total_chains=1,
        blocks=[], edges=[], entry_points=[], chains=chains,
        sink_call_sites=[
            SinkCallSite(
                id="src/db/user.py:getUser:execute:42:18", caller_id=caller,
                callee_name="execute", callee_receiver="cursor",
                category=SinkCategory.SQL, sink_subtype="sql_raw",
                file_path="src/db/user.py", line=42, column=18,
                dangerous_slots=[DangerousSlot(arg_index=0, slot=SlotContext.SQL_VALUE,
                                               expression="q", is_entry_hint=True)],
                rule_id="py-db-cursor-execute",
            ),
        ],
    )
    pgraph = ParameterPropagationGraph(
        taint_flows=[
            TaintFlow(
                flow_id=f"{caller}->src/db/user.py:getUser:execute:42:18",
                entry_point_id=caller, source_param="uid",
                source_type=ParameterSource.QUERY_PARAM,
                propagation_steps=[
                    PropagationStep(step_id="s1", from_func_id=caller, from_param="uid",
                                    to_func_id=caller, to_param="q",
                                    transformation="concat", code_location="src/db/user.py:40"),
                ],
                sink_call_site_id="src/db/user.py:getUser:execute:42:18",
                sink_slot=SlotContext.SQL_VALUE, tainted_arg_index=0,
                confidence=0.6, has_sanitizer_hint=True,
                notes="容器字段过近似",
            ),
        ],
        language_coverage=["python"], skipped_languages=["go", "java", "php"],
    )
    plan = AuditPlan(
        total_chains=1,
        scores=[ChainRiskScore(chain_id=caller, sink_danger=10,
                               taint_completeness=10, auth_gap=8, depth=1)],  # tier3
    )
    return index, pgraph, plan


def test_e2e_md_contains_tier3_sink_and_honest_caveats():
    index, pgraph, plan = _full_fixture()
    md = build_static_dataflow_hints(index, pgraph, plan)
    # tier3 sink 置顶
    assert "Tier 3" in md
    assert "src/db/user.py:42:18" in md
    assert "sql_value" in md
    # 污点流
    assert "src/db/user.py:getUser:10" in md
    assert "concat" in md
    assert "0.60" in md
    # 诚实边界
    assert "sanitize_hint" in md and "不代表有效" in md
    assert "go" in md and "java" in md and "php" in md


def test_e2e_vuln_prompt_renders_hints_block():
    """Non-pipeline-testing vuln-injection prompt contains the static hints guidance."""
    mgr = PromptManager(PROMPTS_DIR)
    rendered = mgr.load_sync("vuln-injection", {"repo_path": "/repo"}, pipeline_testing=False)
    assert "<static_dataflow_hints>" in rendered
    assert "线索，不是结论" in rendered


def test_e2e_pipeline_testing_excludes_hints_block():
    """pipeline-testing mode → no hints block in vuln prompt (Spec §6.4)."""
    mgr = PromptManager(PROMPTS_DIR)
    rendered = mgr.load_sync("vuln-injection", {"repo_path": "/repo"}, pipeline_testing=True)
    assert "<static_dataflow_hints>" not in rendered


def test_e2e_vuln_prompt_preserves_two_layer_model():
    """Regression: the main vuln agent still must delegate code reads to Task agent
    (Spec §4.3 — two-layer model unchanged). The hints block does not replace it."""
    mgr = PromptManager(PROMPTS_DIR)
    rendered = mgr.load_sync("vuln-injection", {"repo_path": "/repo"}, pipeline_testing=False)
    # 原有"禁止主 agent 直接 Read 源码、委派 Task agent"约束仍在
    assert "Task Agent" in rendered or "Task agent" in rendered
    assert "recon_deliverable.md" in rendered  # single source of truth 不变
```

- [x] **Step 2: 运行端到端测试**

Run: `uv run pytest packages/core/tests/code_index/test_static_dataflow_hints_e2e.py -v`
Expected: PASS（4 passed）

- [x] **Step 3: 全量回归 —— Spec C 相关 + 现有 audit_input_builder / prompts 不破坏**

Run: `uv run pytest packages/core/tests/code_index/test_audit_input_builder.py packages/core/tests/code_index/test_static_dataflow_hints.py packages/core/tests/code_index/test_static_dataflow_hints_e2e.py packages/core/tests/prompts/ packages/whitebox/tests/pipeline/test_render_dataflow_hints.py -v`
Expected: PASS（Spec C 新测试全绿；现有 `build_chain_audit_input`/`format_taint_flow_summary`/prompt 渲染测试不破坏）

- [x] **Step 4: 手动验证说明（非自动化，记录在 commit message / PR）**

真实 pipeline 验证（spec §6.3"抓取 vuln agent 归档 prompt"）需跑一次实际 whitebox pipeline（依赖 LLM/Temporal worker，不适合 CI）。验证要点：
1. 样例仓库跑完后，`<repo>/.shannon/deliverables/static_dataflow_hints.md` 存在且含 tier 排序的 sink。
2. `workspaces/<agent>/prompts/`（vuln agent 归档 prompt）中含 `<static_dataflow_hints>` 段。
3. 加 `--pipeline-testing` 跑时，归档 prompt 不含该段。

- [x] **Step 5: Commit**

```bash
git add packages/core/tests/code_index/test_static_dataflow_hints_e2e.py
git commit -m "test(spec-c): end-to-end product→hints→prompt chain + pipeline-testing regression"
```

---

## 完成准则

- [x] `static_dataflow_hints.md` 由 `run_render_dataflow_hints` 在 risk_scoring 后、vuln 前生成；`pipeline_testing_mode` 下跳过。
- [x] 摘要按 tier 排序 sink；`needs_review`/`has_sanitizer_hint`/`skipped_languages`/`confidence` 均渲染为对应指引语。
- [x] 5 个 vuln prompt + recon prompt 含 `<static_dataflow_hints>` 指引段；pipeline-testing 下不含（§6.4）。
- [x] 现有 vuln 两层模型（主 agent 委派 Task agent）、`recon_deliverable.md` 作为 single source of truth 均不破坏。
- [x] 全量 `uv run pytest packages/core packages/whitebox` 绿。
