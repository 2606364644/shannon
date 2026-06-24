# 通用双轨合并器实现计划（Plan 3）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把现有"仅 sink 文本合并"的 `merge_sink_reports`（`sink_merger.py`）抽象/扩展为**通用双轨合并器**，覆盖 spec §4 的全部产物类型：(1) 给 findings schema 加 `source_track`/`evidence_chain`/`merge_source`/`confidence` 四字段；(2) 通用合并函数 `merge_dual_track_queues`（去重键 + verdict OR + 字段危险侧 + 来源/置信度标记）；(3) wiring 到 pipeline（vuln 阶段合并两轨 queue）。

**Architecture:** 现状：`run_merge_sink_reports`（activities.py:359-402）只合并 **sink**（pre-recon 层的 `SinkCallSite`，从 LLM markdown 用 regex 抽 file:line），**不**合并 vuln verdict queue。本 plan 新增一条**独立的通用 verdict 合并通道**（不复用 sink 逻辑，因为产物类型不同）：

- vuln 类双轨各自产 `<vuln>_llm_queue.json` 与 `<vuln>_gitnexus_queue.json`（两轨 queue 文件；**本 plan 假设 GitNexus 轨产物由后续环节 Plan 产生，缺失时优雅降级为空**）→ `merge_dual_track_queues` 读两轨 → 按 `(vuln_type, sink/location, source)` 去重 → verdict OR（任一 vulnerable → vulnerable）+ 字段危险侧（auth 任一无→无）→ 标 `merge_source`（both/llm-only/gitnexus-only）+ `confidence`（both=high / single=needs_review）→ 写出 `<vuln>_exploitation_queue.json`（下游 `findings_renderer` 现有消费不变）。
- findings 四字段加在 `BaseVulnerability`（所有 vuln 子类继承）上，默认值保证向后兼容（旧 queue 文件无这四字段仍能解析）。

**Tech Stack:** Python 3.12, pydantic v2, pytest, pytest-asyncio

## Global Constraints

- **不破坏现有消费方**：`findings_renderer.py:201-269` 的 `FindingsRenderer` 读 `<vuln>_exploitation_queue.json` → `VulnerabilityQueue.parse_lenient`；新增的四字段全部默认值（None/None），旧 queue 无这四字段仍能 `parse_lenient` 通过。renderer 也**无需改**（新字段不在 render path）。
- **四字段语义**（spec §4.1）：
  - `source_track: "llm" | "gitnexus" | None`（**单轨 finding 的产出轨道**；合并后改用 `merge_source`）
  - `evidence_chain: str | None`（GitNexus 轨必填：source→sink 路径 + sanitizer 标注）
  - `merge_source: "both" | "llm-only" | "gitnexus-only" | None`（**合并后**的来源标记）
  - `confidence: "high" | "needs_review" | None`（**合并后**的置信度；注意 `BaseVulnerability.confidence` 已存在是 LLM 自报置信度——本 plan 新增字段**复用 `confidence` 语义**：合并时覆写为 high/needs_review，因为 spec §4.1 把 confidence 定义为合并产物字段）
- **verdict OR 语义**（spec §4.2）：verdict ∈ {`"safe"`, `"vulnerable"`}（见 `vuln-injection.txt:116`）；`"vulnerable" OR`：任一轨 vulnerable → 合并 vulnerable；两轨都 safe → safe。**单轨无 verdict 字段的类（auth/authz/ssrf 的 `BaseVulnerability` 无 verdict）→ 只靠 externally_exploitable 做 OR**（`externally_exploitable=True OR`）。
- **字段危险侧**（spec §4.3）：仅 recon 情报合并适用（auth/framework/ownership 字段冲突取危险侧）。**本 plan 的 verdict 合并器只做 verdict OR + 来源标记**；字段危险侧作为通用合并器的**可选 intel 字段合并模式**实现（供 recon 阶段未来调用），但 pipeline wiring（Task 3）**只接 vuln verdict 模式**，recon 情报合并 wiring 留后续 plan。
- **优雅降级**：GitNexus 轨 queue 文件不存在 → 当作空轨，合并器只产 LLM 轨 finding（全 `llm-only`/`needs_review`），不崩（spec §6 GitNexus 索引降级前置，但本 plan 的合并器本身就该容错空轨）。
- **不接真实两轨产物生成**：本 plan 只做"合并器 + schema + wiring 占位"；`<vuln>_gitnexus_queue.json` 的实际生成（GitNexus 轨 LLM 链判定 pass）是后续 Plan（spec §5 逐环节）的工作。本 plan Task 3 的 wiring 在 gitnexus queue 不存在时降级为空，**pipeline 行为与现状等价**（只产 LLM 轨 queue，重命名为 exploitation_queue）。
- **不删 `merge_sink_reports`**：sink 合并（pre-recon 层）与 verdict 合并（vuln 层）是两个不同阶段，`run_merge_sink_reports` 保留不动。
- TDD + frequent commits（`feat(models):` / `feat(code_index):` / `feat(whitebox):`）；真实双轨产物合并由手动冒烟验证（本 plan 单元测试用合成双轨 queue）。

---

### Task 1: `BaseVulnerability` 加四字段（source_track/evidence_chain/merge_source/confidence）

**Files:**
- Modify: `packages/core/src/shannon_core/models/queue_schemas.py:7-12`（`BaseVulnerability`）
- Test: `packages/core/tests/models/test_dual_track_fields.py`（Create）

**Interfaces:**
- Produces: `BaseVulnerability.source_track: str | None`、`evidence_chain: str | None`、`merge_source: str | None`（向后兼容，默认 None）；`confidence` 已存在（重定义语义为合并产物字段，值域补 `needs_review`）

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/models/test_dual_track_fields.py
from shannon_core.models.queue_schemas import (
    BaseVulnerability,
    InjectionVulnerability,
    VulnerabilityQueue,
)


def _base(**kw):
    return BaseVulnerability(ID="V1", vulnerability_type="injection",
                             externally_exploitable=True, confidence="high", **kw)


def test_base_vulnerability_has_source_track_defaults_none():
    v = _base()
    assert v.source_track is None
    assert v.evidence_chain is None
    assert v.merge_source is None


def test_base_vulnerability_accepts_new_fields():
    v = _base(source_track="gitnexus", evidence_chain="q -> db.exe(L42)",
              merge_source="both")
    assert v.source_track == "gitnexus"
    assert v.evidence_chain == "q -> db.exe(L42)"
    assert v.merge_source == "both"


def test_subclass_inherits_new_fields():
    v = InjectionVulnerability(ID="I1", vulnerability_type="injection",
                               externally_exploitable=True, confidence="high",
                               source_track="llm", merge_source="llm-only")
    assert v.source_track == "llm"
    assert v.merge_source == "llm-only"


def test_legacy_queue_without_new_fields_parses():
    """旧 queue 文件（无四字段）仍能 parse_lenient 通过（向后兼容）。"""
    content = '{"vulnerabilities":[{"ID":"L1","vulnerability_type":"injection","externally_exploitable":true,"confidence":"high"}]}'
    result = VulnerabilityQueue.parse_lenient(content)
    assert result.warnings == [] or all("dropped" not in w for w in result.warnings)
    assert len(result.queue.vulnerabilities) == 1
    v = result.queue.vulnerabilities[0]
    assert v.merge_source is None  # 缺失 → 默认 None，未丢


def test_needs_review_confidence_value_allowed():
    v = _base(confidence="needs_review")
    assert v.confidence == "needs_review"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/models/test_dual_track_fields.py -v`
Expected: FAIL — `BaseVulnerability.__init__()` got unexpected keyword `source_track` / `evidence_chain` / `merge_source`

- [ ] **Step 3: Add the three new fields to `BaseVulnerability`**

Edit `packages/core/src/shannon_core/models/queue_schemas.py:7-12`:

```python
class BaseVulnerability(BaseModel):
    ID: str
    vulnerability_type: str
    externally_exploitable: bool
    confidence: str
    notes: str | None = None
    # Spec §4.1 dual-track merge fields (all optional for backward compat).
    # source_track: which track produced this finding pre-merge ("llm" | "gitnexus").
    # evidence_chain: GitNexus-track required (source→sink path + sanitizer annotation).
    # merge_source: post-merge origin tag ("both" | "llm-only" | "gitnexus-only").
    # confidence is reused as the post-merge confidence ("high" | "needs_review" | ...).
    source_track: str | None = None
    evidence_chain: str | None = None
    merge_source: str | None = None
```

> `confidence` 字段已存在于 `:11`（`confidence: str`），不新增，只扩展其语义（合并器覆写为 high/needs_review，见 Task 2）。值域不加 Literal 约束（保持 pydantic 宽松，向后兼容旧值 medium/low）。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/models/test_dual_track_fields.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Run findings_renderer tests to confirm no regression**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/ -k "findings_renderer or queue_schemas" -v 2>/dev/null || python -m pytest packages/core/tests/models/ -v`
Expected: PASS（新字段默认 None，不破坏现有 renderer/parse_lenient）

- [ ] **Step 6: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/models/queue_schemas.py packages/core/tests/models/test_dual_track_fields.py
git commit -m "feat(models): add dual-track merge fields to BaseVulnerability (spec §4.1)"
```

---

### Task 2: 通用合并函数 `merge_dual_track_queues`（verdict OR + 字段危险侧 + 来源/置信度标记）

**Files:**
- Create: `packages/core/src/shannon_core/code_index/dual_track_merger.py`
- Test: `packages/core/tests/code_index/test_dual_track_merger.py`（Create）

**Interfaces:**
- Consumes: 两轨 `list[Vulnerability]`（从 `VulnerabilityQueue.parse_lenient` 解析出）
- Produces: `merge_dual_track_queues(llm_findings, gitnexus_findings, *, mode="verdict") -> list[Vulnerability]`（mode: `"verdict"` = vuln OR；`"intel"` = recon 字段危险侧）；每条带 `merge_source` + 覆写后的 `confidence`

**去重键**（spec §4.2）：verdict 模式按 `(vulnerability_type, source_or_endpoint_or_location, sink_call_or_path)` 去重——取 finding 上"能定位到同一条目"的字段并集（不同 vuln 类定位字段不同，用 helper 抽）。

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/code_index/test_dual_track_merger.py
from shannon_core.code_index.dual_track_merger import merge_dual_track_queues
from shannon_core.models.queue_schemas import InjectionVulnerability, AuthzVulnerability


def _inj(ID, verdict, source="q", sink_call="db.exec", **kw):
    return InjectionVulnerability(ID=ID, vulnerability_type="injection",
                                  externally_exploitable=(verdict == "vulnerable"),
                                  confidence="high", verdict=verdict,
                                  source=source, sink_call=sink_call, **kw)


def test_both_tracks_vulnerable_merges_high_confidence():
    llm = [_inj("L1", "vulnerable")]
    gn = [_inj("G1", "vulnerable")]
    out = merge_dual_track_queues(llm, gn, mode="verdict")
    assert len(out) == 1
    assert out[0].merge_source == "both"
    assert out[0].confidence == "high"
    assert out[0].verdict == "vulnerable"


def test_one_vulnerable_one_safe_or_takes_vulnerable():
    """verdict OR: 任一 vulnerable → vulnerable (保守，宁过报不漏报)。"""
    llm = [_inj("L1", "safe")]
    gn = [_inj("G1", "vulnerable")]
    out = merge_dual_track_queues(llm, gn, mode="verdict")
    assert len(out) == 1
    assert out[0].merge_source == "both"
    assert out[0].verdict == "vulnerable"


def test_both_safe_stays_safe():
    llm = [_inj("L1", "safe")]
    gn = [_inj("G1", "safe")]
    out = merge_dual_track_queues(llm, gn, mode="verdict")
    assert len(out) == 1
    assert out[0].merge_source == "both"
    assert out[0].verdict == "safe"


def test_llm_only_marked_needs_review():
    llm = [_inj("L1", "vulnerable")]
    gn = []  # gitnexus 轨空（优雅降级）
    out = merge_dual_track_queues(llm, gn, mode="verdict")
    assert len(out) == 1
    assert out[0].merge_source == "llm-only"
    assert out[0].confidence == "needs_review"


def test_gitnexus_only_marked_needs_review():
    llm = []
    gn = [_inj("G1", "vulnerable")]
    out = merge_dual_track_queues(llm, gn, mode="verdict")
    assert len(out) == 1
    assert out[0].merge_source == "gitnexus-only"
    assert out[0].confidence == "needs_review"


def test_dedup_key_collapses_same_finding_across_tracks():
    """同一条 finding（同 source+sink）在两轨各出现一次 → 合并为一条 both。"""
    llm = [_inj("L1", "vulnerable", source="q", sink_call="db.exec")]
    gn = [_inj("G1", "safe", source="q", sink_call="db.exec")]
    out = merge_dual_track_queues(llm, gn, mode="verdict")
    assert len(out) == 1  # 去重
    assert out[0].verdict == "vulnerable"  # OR


def test_distinct_findings_kept_separately():
    llm = [_inj("L1", "vulnerable", source="q", sink_call="db.exec")]
    gn = [_inj("G1", "vulnerable", source="id", sink_call="os.system")]
    out = merge_dual_track_queues(llm, gn, mode="verdict")
    assert len(out) == 2
    sources = {getattr(f, "source") for f in out}
    assert sources == {"q", "id"}


def test_no_verdict_field_uses_externally_exploitable_for_or():
    """authz/auth/ssrf 类无 verdict 字段 → 靠 externally_exploitable 做 OR。"""
    def _authz(ID, exploitable):
        return AuthzVulnerability(ID=ID, vulnerability_type="authz",
                                  externally_exploitable=exploitable,
                                  confidence="high", endpoint="DELETE /api/x")
    llm = [_authz("L1", False)]
    gn = [_authz("G1", True)]
    out = merge_dual_track_queues(llm, gn, mode="verdict")
    assert len(out) == 1
    assert out[0].externally_exploitable is True  # OR


def test_union_no_finding_lost():
    """合并后 = 两轨并集（去重后），不丢任一轨的项。"""
    llm = [_inj("L1", "vulnerable", source="q", sink_call="s1"),
           _inj("L2", "safe", source="w", sink_call="s2")]
    gn = [_inj("G1", "vulnerable", source="q", sink_call="s1"),  # dup of L1
          _inj("G2", "vulnerable", source="z", sink_call="s3")]
    out = merge_dual_track_queues(llm, gn, mode="verdict")
    assert len(out) == 3  # q/s1(dup→1) + w/s2 + z/s3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_dual_track_merger.py -v`
Expected: FAIL — `ModuleNotFoundError: shannon_core.code_index.dual_track_merger`

- [ ] **Step 3: Implement `merge_dual_track_queues`**

```python
# packages/core/src/shannon_core/code_index/dual_track_merger.py
"""General dual-track merger (spec §4).

Merges LLM-track and GitNexus-track vulnerability findings into a unified
exploitation queue. Two modes:

- mode="verdict" (vuln phase, §4.2): dedup by (vuln_type, location, sink),
  verdict OR (any vulnerable → vulnerable), mark merge_source + confidence.
- mode="intel" (recon phase, §4.3): field-level "dangerous side" merge
  (auth any-missing→missing; framework origin any-auto→auto; ownership
  any-none→none). Marked for future recon wiring.

Union semantics: merged = LLM ∪ GitNexus (deduped); no finding is lost.
Graceful degradation: empty GitNexus track → all findings are llm-only /
needs_review; empty LLM track → all gitnexus-only.
"""

import logging
from shannon_core.models.queue_schemas import Vulnerability

logger = logging.getLogger(__name__)

# Fields that can identify a finding's "location" across vuln classes.
# Different classes carry different keys; union the present ones.
_LOCATION_FIELDS = ("source", "endpoint", "source_endpoint",
                    "vulnerable_code_location", "path")
_SINK_FIELDS = ("sink_call", "sink_function", "vulnerable_parameter")


def _finding_key(f: Vulnerability) -> tuple:
    """Build a dedup key from (vuln_type, location tuple, sink tuple).

    Missing fields → None, so two findings missing the same field still
    dedup. This is intentionally loose: over-collapsing is acceptable
    (verdict OR is conservative), but we prefer distinct findings stay
    distinct.
    """
    loc = tuple(getattr(f, fld, None) for fld in _LOCATION_FIELDS)
    sink = tuple(getattr(f, fld, None) for fld in _SINK_FIELDS)
    return (getattr(f, "vulnerability_type", None), loc, sink)


def _get_verdict_or_exploitable(f: Vulnerability) -> bool:
    """True if the finding is 'vulnerable'.

    Prefers the explicit `verdict` field ("vulnerable" / "safe") when
    present (injection/xss). Falls back to `externally_exploitable`
    (auth/authz/ssrf have no verdict field).
    """
    verdict = getattr(f, "verdict", None)
    if verdict is not None:
        return str(verdict).strip().lower() == "vulnerable"
    return bool(getattr(f, "externally_exploitable", False))


def _clone_with_merge_fields(
    f: Vulnerability, merge_source: str, confidence: str, vulnerable: bool
) -> Vulnerability:
    """Return a copy of f with merge_source/confidence set and OR-applied."""
    data = f.model_dump()
    data["merge_source"] = merge_source
    data["confidence"] = confidence
    # Apply verdict OR on both representations for downstream consistency.
    if "verdict" in data and data.get("verdict") is not None:
        data["verdict"] = "vulnerable" if vulnerable else "safe"
    data["externally_exploitable"] = bool(data.get("externally_exploitable")) or vulnerable
    return type(f).model_validate(data)


def merge_dual_track_queues(
    llm_findings: list[Vulnerability],
    gitnexus_findings: list[Vulnerability],
    *,
    mode: str = "verdict",
) -> list[Vulnerability]:
    """Merge LLM-track and GitNexus-track findings (spec §4).

    mode="verdict": vuln phase — verdict OR + source/confidence marking.
    mode="intel": recon phase — field-level dangerous-side merge (auth/
        framework/ownership). NOTE: intel mode is implemented for future
        recon wiring; this plan only wires verdict mode.

    Returns the union (deduped by _finding_key); no finding is dropped.
    """
    if mode == "intel":
        return _merge_intel(llm_findings, gitnexus_findings)

    # --- verdict mode ---
    llm_by_key: dict[tuple, Vulnerability] = {}
    for f in llm_findings:
        llm_by_key.setdefault(_finding_key(f), f)
    gn_by_key: dict[tuple, Vulnerability] = {}
    for f in gitnexus_findings:
        gn_by_key.setdefault(_finding_key(f), f)

    all_keys = list(llm_by_key.keys()) + [k for k in gn_by_key if k not in llm_by_key]
    merged: list[Vulnerability] = []

    for key in all_keys:
        l = llm_by_key.get(key)
        g = gn_by_key.get(key)
        if l is not None and g is not None:
            vuln = _get_verdict_or_exploitable(l) or _get_verdict_or_exploitable(g)
            base = l  # prefer LLM-track finding as the carrier (richer free-text fields)
            # Preserve GitNexus evidence_chain if the LLM one lacks it.
            evidence = getattr(base, "evidence_chain", None) or getattr(g, "evidence_chain", None)
            out = _clone_with_merge_fields(base, "both",
                                           "high" if vuln else "high", vuln)
            if evidence and not getattr(out, "evidence_chain", None):
                out = type(out).model_validate({**out.model_dump(), "evidence_chain": evidence})
            merged.append(out)
        elif l is not None:
            merged.append(_clone_with_merge_fields(l, "llm-only", "needs_review",
                                                   _get_verdict_or_exploitable(l)))
        else:  # g is not None
            merged.append(_clone_with_merge_fields(g, "gitnexus-only", "needs_review",
                                                   _get_verdict_or_exploitable(g)))

    logger.info(
        "dual-track merge: %d llm + %d gitnexus → %d merged "
        "(both=%d, llm-only=%d, gitnexus-only=%d)",
        len(llm_findings), len(gitnexus_findings), len(merged),
        sum(1 for m in merged if m.merge_source == "both"),
        sum(1 for m in merged if m.merge_source == "llm-only"),
        sum(1 for m in merged if m.merge_source == "gitnexus-only"),
    )
    return merged


def _merge_intel(
    llm_findings: list[Vulnerability], gitnexus_findings: list[Vulnerability]
) -> list[Vulnerability]:
    """recon intel merge (§4.3): field-level dangerous side.

    Dangerous-side rules:
    - auth: any track missing auth → missing
    - framework origin: any track auto-generated → auto-generated
    - ownership: any track none → none

    These are advisory fields carried in notes/free-text; this is a
    best-effort string-presence merge. Full recon wiring is a later plan.
    """
    llm_by_key = {(_finding_key(f)): f for f in llm_findings}
    gn_by_key = {(_finding_key(f)): f for f in gitnexus_findings}
    all_keys = list(llm_by_key.keys()) + [k for k in gn_by_key if k not in llm_by_key]
    out: list[Vulnerability] = []
    for key in all_keys:
        l = llm_by_key.get(key)
        g = gn_by_key.get(key)
        if l and g:
            data = l.model_dump()
            data["merge_source"] = "both"
            data["confidence"] = "high"
            # Dangerous-side on notes (advisory): keep the more alarming note.
            for field in ("notes", "role_context", "guard_evidence", "missing_defense"):
                lv = (data.get(field) or "").lower() if isinstance(data.get(field), str) else ""
                gv = (getattr(g, field, "") or "").lower() if isinstance(getattr(g, field, ""), str) else ""
                danger_keywords = ("none", "missing", "no ", "auto-generated", "absent")
                if any(k in gv for k in danger_keywords) and not any(k in lv for k in danger_keywords):
                    data[field] = getattr(g, field, None)
            out.append(type(l).model_validate(data))
        elif l:
            out.append(_clone_with_merge_fields(l, "llm-only", "needs_review",
                                                _get_verdict_or_exploitable(l)))
        elif g:
            out.append(_clone_with_merge_fields(g, "gitnexus-only", "needs_review",
                                                _get_verdict_or_exploitable(g)))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_dual_track_merger.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/code_index/dual_track_merger.py packages/core/tests/code_index/test_dual_track_merger.py
git commit -m "feat(code_index): add general dual-track merger (verdict OR + intel dangerous-side)"
```

---

### Task 3: Pipeline wiring — vuln 阶段合并两轨 queue

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`（新增 `run_merge_dual_track_queues` activity，接在 vuln 阶段后）
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py:305-328`（vuln 阶段并行后调合并 activity）
- Test: `packages/whitebox/tests/test_run_merge_dual_track.py`（Create）

**Interfaces:**
- Consumes: `<vuln>_llm_queue.json` + `<vuln>_gitnexus_queue.json`（gitnexus 轨不存在时降级为空）；`merge_dual_track_queues`（Task 2）
- Produces: `<vuln>_exploitation_queue.json`（每条带 `merge_source` + 覆写后 `confidence`）；下游 `findings_renderer` 消费不变

**关键行为**：当前 vuln agent（`run_agent` → executor.py:130-133）直接把 LLM 产出写成 `<vuln>_exploitation_queue.json`。本 task 改为：executor 仍写 `<vuln>_exploitation_queue.json`（LLM 轨产出），合并 activity 把它**重命名为** `<vuln>_llm_queue.json`（保留 LLM 轨原始），再与 `<vuln>_gitnexus_queue.json` 合并，结果写回 `<vuln>_exploitation_queue.json`。gitnexus 轨不存在 → 合并结果 = LLM 轨（全 llm-only），行为与现状等价。

- [ ] **Step 1: Write the failing test**

```python
# packages/whitebox/tests/test_run_merge_dual_track.py
import json
from pathlib import Path

import pytest

from shannon_whitebox.pipeline import activities


def _input(repo, deliverables):
    class FakeInput:
        agent_name = None
        web_url = None
        repo_path = str(repo)
        config_path = None
        api_key = None
        pipeline_testing_mode = False
        prompt_override = None
    return FakeInput()


@pytest.mark.asyncio
async def test_merge_writes_exploitation_queue_from_llm_only(tmp_path, monkeypatch):
    """LLM 轨有 queue、GitNexus 轨不存在 → 合并结果 = LLM 轨 (全 llm-only)。"""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    # executor 已写的 LLM 产出
    (deliverables / "injection_exploitation_queue.json").write_text(json.dumps({
        "vulnerabilities": [{
            "ID": "L1", "vulnerability_type": "injection",
            "externally_exploitable": True, "confidence": "high",
            "verdict": "vulnerable", "source": "q", "sink_call": "db.exec",
        }],
    }))

    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    await activities.run_merge_dual_track_queues(_input(tmp_path, deliverables))

    out = json.loads((deliverables / "injection_exploitation_queue.json").read_text())
    assert len(out["vulnerabilities"]) == 1
    v = out["vulnerabilities"][0]
    assert v["merge_source"] == "llm-only"
    assert v["confidence"] == "needs_review"
    # LLM 轨原始保留
    assert (deliverables / "injection_llm_queue.json").exists()


@pytest.mark.asyncio
async def test_merge_combines_both_tracks(tmp_path, monkeypatch):
    """两轨都有 → 合并，both=high。"""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "injection_exploitation_queue.json").write_text(json.dumps({
        "vulnerabilities": [{
            "ID": "L1", "vulnerability_type": "injection",
            "externally_exploitable": True, "confidence": "high",
            "verdict": "vulnerable", "source": "q", "sink_call": "db.exec",
        }],
    }))
    (deliverables / "injection_gitnexus_queue.json").write_text(json.dumps({
        "vulnerabilities": [{
            "ID": "G1", "vulnerability_type": "injection",
            "externally_exploitable": True, "confidence": "high",
            "verdict": "vulnerable", "source": "q", "sink_call": "db.exec",
            "evidence_chain": "q -> db.exec(L42)",
        }],
    }))

    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    await activities.run_merge_dual_track_queues(_input(tmp_path, deliverables))

    out = json.loads((deliverables / "injection_exploitation_queue.json").read_text())
    assert len(out["vulnerabilities"]) == 1
    v = out["vulnerabilities"][0]
    assert v["merge_source"] == "both"
    assert v["confidence"] == "high"
    assert v["evidence_chain"] == "q -> db.exec(L42)"  # 从 gitnexus 轨补


@pytest.mark.asyncio
async def test_merge_skips_vuln_classes_with_no_llm_queue(tmp_path, monkeypatch):
    """某 vuln 类 LLM 轨没产出（file 不存在）→ 跳过，不崩。"""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    # 无任何 *_exploitation_queue.json
    result = await activities.run_merge_dual_track_queues(_input(tmp_path, deliverables))
    assert result["merged_classes"] == []


@pytest.mark.asyncio
async def test_merge_handles_invalid_llm_queue_leniently(tmp_path, monkeypatch):
    """LLM queue 损坏 → parse_lenient 吸收，不崩，warning 记录。"""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "injection_exploitation_queue.json").write_text("not json")
    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    result = await activities.run_merge_dual_track_queues(_input(tmp_path, deliverables))
    # lenient 解析 → 空 queue → 输出空 exploitation_queue，class 仍计入 merged
    assert "injection" in result["merged_classes"]
    out = json.loads((deliverables / "injection_exploitation_queue.json").read_text())
    assert out["vulnerabilities"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_run_merge_dual_track.py -v`
Expected: FAIL — `AttributeError: module ...activities has no attribute 'run_merge_dual_track_queues'`

- [ ] **Step 3: Implement the activity**

Add to `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`（在 `run_merge_sink_reports` 之后，约 :403 附近）：

```python
@activity.defn
async def run_merge_dual_track_queues(input: ActivityInput) -> dict:
    """Merge LLM-track and GitNexus-track vuln queues (spec §4).

    For each vuln class present in deliverables (injection/xss/ssrf/authz/auth):
    1. Rename the existing `<vuln>_exploitation_queue.json` (LLM-track output
       written by executor) to `<vuln>_llm_queue.json` (preserve LLM original).
    2. Load `<vuln>_gitnexus_queue.json` if present (GitNexus-track output;
       absent → empty track, graceful degradation).
    3. merge_dual_track_queues(llm, gitnexus, mode="verdict") → write merged
       result back to `<vuln>_exploitation_queue.json` (findings_renderer
       consumes this unchanged).

    Downstream behavior is equivalent to current when gitnexus track is empty
    (all findings become llm-only / needs_review).
    """
    from shannon_whitebox.audit.session_registry import get_audit_session
    try:
        from shannon_core.code_index.dual_track_merger import merge_dual_track_queues
        from shannon_core.models.queue_schemas import VulnerabilityQueue
        from shannon_core.utils.file_io import atomic_write_json

        repo, deliverables, _ = _get_paths(input)

        merged_classes: list[str] = []
        per_class_counts: dict[str, dict] = {}

        async with get_audit_session().track_step(
            "vulnerability-analysis", "merge-dual-track",
            intent=intent_for("merge-dual-track"),
        ):
            for vc in ("injection", "xss", "ssrf", "authz", "auth"):
                expl_path = deliverables / f"{vc}_exploitation_queue.json"
                if not expl_path.exists():
                    continue  # this vuln class produced nothing

                # Preserve LLM-track original (rename, don't overwrite original yet).
                llm_path = deliverables / f"{vc}_llm_queue.json"
                llm_path.write_text(expl_path.read_text())

                # Parse LLM track leniently (executor output may be free-form).
                llm_parsed = VulnerabilityQueue.parse_lenient(llm_path.read_text())
                llm_findings = llm_parsed.queue.vulnerabilities

                # Load GitNexus track if present (graceful: absent → empty).
                gn_path = deliverables / f"{vc}_gitnexus_queue.json"
                gn_findings = []
                if gn_path.exists():
                    gn_parsed = VulnerabilityQueue.parse_lenient(gn_path.read_text())
                    gn_findings = gn_parsed.queue.vulnerabilities

                merged = merge_dual_track_queues(llm_findings, gn_findings, mode="verdict")

                # Write merged exploitation queue (findings_renderer consumes this).
                atomic_write_json(
                    expl_path,
                    {"vulnerabilities": [m.model_dump() for m in merged]},
                )

                merged_classes.append(vc)
                per_class_counts[vc] = {
                    "llm": len(llm_findings),
                    "gitnexus": len(gn_findings),
                    "merged": len(merged),
                    "both": sum(1 for m in merged if m.merge_source == "both"),
                    "llm_only": sum(1 for m in merged if m.merge_source == "llm-only"),
                    "gitnexus_only": sum(1 for m in merged if m.merge_source == "gitnexus-only"),
                }

        return {"merged_classes": merged_classes, "per_class_counts": per_class_counts}
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
```

> 注：`atomic_write_json` 已在 activities.py 用（见 `run_merge_sink_reports:390`），`intent_for` 同。`VulnerabilityQueue.parse_lenient` 不 raise（lenient，见 queue_schemas.py:84-148）。

- [ ] **Step 4: Wire the activity into the workflow (after vuln phase, before attack-chain)**

Edit `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`，在 vuln 阶段并行 gather 完成、`log_phase_complete_activity`（vulnerability-analysis）**之前**（约 :323）插入：

```python
            # Dual-track merge: combine LLM-track + GitNexus-track vuln queues
            # (spec §4). GitNexus track absent → degrades to LLM-only (current behavior).
            await workflow.execute_activity(
                activities.run_merge_dual_track_queues, act_input,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=retry_for("standard"),
            )
            await workflow.execute_activity(
                activities.log_phase_complete_activity,
                ActivityInput(**{**act_input.__dict__, "phase": "vulnerability-analysis"}),
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=retry_for("log"),
            )
```

（原 :323-328 的 `log_phase_complete_activity` 调用保留，新 activity 插在其前。）

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_run_merge_dual_track.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Run broader whitebox pipeline test subset to confirm no regression**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/ -k "merge or workflow or activities" -v --ignore=packages/whitebox/tests/test_cli.py 2>/dev/null | tail -30`
Expected: PASS（注意 memory 记录 test_worker_progress / test_cli follow / integration 等有预存挂起，按需 --ignore）

- [ ] **Step 7: Commit**

```bash
cd /root/shannon-py
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/src/shannon_whitebox/pipeline/workflows.py packages/whitebox/tests/test_run_merge_dual_track.py
git commit -m "feat(whitebox): wire dual-track queue merger into vuln phase (spec §4.2)"
```

> **手动冒烟（本 plan 外）**：真实双轨产物（GitNexus 轨 queue）的生成是后续 Plan（spec §5 逐环节）。本 plan wiring 后，GitNexus 轨不存在时行为与现状等价（全 llm-only/needs_review）。等 GitNexus 轨 Plan 落地后跑一次真实白盒扫描，确认 `<vuln>_exploitation_queue.json` 含 both/llm-only/gitnexus-only 三类来源标记。

---

## Self-Review

**1. Spec coverage**（对照 spec §4 / §4.1 / §4.2 / §4.3 / §9）：
- §4.1 findings 四字段（source_track/evidence_chain/merge_source/confidence）→ Task 1 ✓
- §4 通用合并器（覆盖 sink/entry/taint/authz/config 全产物类型）→ Task 2 `merge_dual_track_queues`（verdict + intel 双模式）✓
- §4.2 vuln verdict 合并（OR）→ Task 2 verdict 模式 + Task 3 wiring ✓
- §4.3 recon 情报合并（字段危险侧）→ Task 2 intel 模式**实现**（_merge_intel），但**recon wiring 留后续 plan**（Global Constraint 已声明）⚠️
- §9 验收 #2 每条带 merge_source + confidence → Task 1 字段 + Task 2 标记 ✓
- §9 验收 #3 冲突规则（verdict OR + 字段危险侧 + both→high/single→needs_review）→ Task 2 ✓
- §9 验收 #4 并集不丢 → `test_union_no_finding_lost` ✓
- §9 验收 #5 GitNexus 失败优雅降级 → Task 3 gitnexus queue 不存在 → 空轨 → 全 llm-only ✓（索引降级本身是 Plan 4）
- §11 Phase 0「合并器扩展」→ Task 1-3 ✓

**2. Placeholder scan**：无 TBD/TODO。`intent_for("merge-dual-track")` 的 key 在 `step_intents.py:20-50` 的 `PHASE_STEPS` 表里**不存在**（当前 `vulnerability-analysis` phase 无 StepSpec 条目）——但 `intent_for` 返回 `str | None`（`step_intents.py:68`），`track_step(intent=None)` 合法，**不会 KeyError**（已验证 `intent_for` 签名）。为让 dashboard 显示有意义的中文文案，建议在 `step_intents.py` 的 `"vulnerability-analysis"` 下补一行 `StepSpec("merge-dual-track", "双轨合并 LLM/GitNexus 漏洞队列")`（trivial，非阻塞；现状 `vulnerability-analysis` phase 本就无 StepSpec）。

**3. Type consistency**：
- `BaseVulnerability` 四字段在 Task 1（schema）/ Task 2（合并器读写）/ Task 3（wiring）一致。
- `merge_dual_track_queries` 签名 `(list[Vulnerability], list[Vulnerability], *, mode="verdict") -> list[Vulnerability]` 在 Task 2/3 一致。
- `_clone_with_merge_fields` 用 `type(f).model_validate` 保留子类类型（InjectionVulnerability 不会退化成 BaseVulnerability），与 queue_schemas.py:61 Union 一致。
- Task 3 wiring 的 `<vuln>_llm_queue.json`/`<vuln>_gitnexus_queue.json`/`<vuln>_exploitation_queue.json` 三文件命名与 executor.py:130-133（写 exploitation_queue）+ findings_renderer.py:142-178（读 exploitation_queue）一致，下游消费不变。

**4. verdict OR 正确性**：
- 有 verdict 字段的类（injection/xss）：`_get_verdict_or_exploitable` 读 verdict（"vulnerable"→True）✓
- 无 verdict 字段的类（auth/authz/ssrf）：回退 `externally_exploitable` ✓（test_no_verdict_field_uses_externally_exploitable_for_or）
- OR 在 `_clone_with_merge_fields` 双写（verdict + externally_exploitable）保证下游两条消费路径一致 ✓

**需人决策点**：
- **A. `intent_for("merge-dual-track")` 文案缺失（非阻塞）**：已确认 `intent_for` 返回 `str | None`（`step_intents.py:68`），key 不存在时返回 None，`track_step(intent=None)` 合法，**不会 KeyError**。但 `step_intents.py:20-50` 的 `PHASE_STEPS["vulnerability-analysis"]` 当前无任何 StepSpec（该 phase 的 step_intents 在 workflows.py:286 是现场 `[f"分析 {vt} 漏洞" for vt in ...]` 临时造的）。建议补一行 `StepSpec("merge-dual-track", "双轨合并 LLM/GitNexus 漏洞队列")` 让 dashboard 显示中文文案——trivial，不影响合并器逻辑，可在 Task 3 Step 3 实现时顺手加或留作小补丁。
- **B. intel 模式 wiring（recon §4.3）**：Task 2 实现了 `_merge_intel` 但无 wiring。spec §5.1-5.3 的 recon 情报双轨合并在 Phase 1（逐环节），本 plan 只交付合并器能力 + vuln verdict wiring。**诚实标注**：recon 情报合并 wiring 不在本 plan。
- **C. GitNexus 轨 queue 生成**：本 plan 假设 `<vuln>_gitnexus_queue.json` 由后续 Plan（spec §5.4-5.8 逐 vuln 类 GitNexus 轨 LLM 链判定 pass）产生。本 plan wiring 在该文件不存在时降级为空，pipeline 行为与现状等价（全 llm-only）。真实双轨合并效果需后续 Plan 落地后手动冒烟验证。

**已知缺口（诚实）**：
- intel 模式的"字段危险侧"目前是**字符串关键词 best-effort**（notes/role_context/guard_evidence 中含 "none"/"missing"/"auto-generated" 即取危险侧），**非结构化字段级**精确合并。spec §4.3 的 auth/framework/ownership 字段在当前 schema 里是 free-text（role_context/guard_evidence/notes），无独立结构化字段，故只能 best-effort。若后续 recon 情报产物改成结构化（auth: bool / framework_origin: enum），intel 模式需重写为字段级。此为 schema 演进问题，非本 plan 阻塞项。
- 真实双轨合并（both/llm-only/gitnexus-only 实际比例分布）需 GitNexus 轨 Plan 落地后手动冒烟，本 plan 单元测试用合成双轨 queue 验证逻辑正确性。
