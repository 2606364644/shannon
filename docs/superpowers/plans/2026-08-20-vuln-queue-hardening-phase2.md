# vuln findings 交付通道加固 Phase 2（submit_finding 单条上交 + roster 对账 + 定向重查）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** vuln agent 每确认一个 vulnerable finding 立即经 `submit_finding` 工具单条上交（host 进程内落袋），session 末尾 `finding_roster` 声明全量名单，executor 确定性对账 + 定向重查补漏——vuln queue 彻底脱离末条大 JSON 单点通道（B 拓扑）。

**Architecture:** collector 新增 `submit_finding` append section（per-class schema，走 bridge.py 现有 append 桥，双引擎零新代码）+ `set_findings_summary` 加 `finding_roster` required 字段；对账纯函数独立成 `agents/vuln_queue_reconcile.py`；executor 写盘分支按六分支对账表改造，漏交时追发廉价重查 agent（`run_claude_prompt` 单次，structured_output 小 payload 返回）；5 个 `vuln-*.txt` 删 final structured output 指引、加 submit_finding/roster 指令；whitebox `_vuln_output_schema` 停传。

**Tech Stack:** Python 3.12 / pytest（monkeypatch + MagicMock，无真机 LLM）。零新依赖。

**Spec:** `docs/superpowers/specs/2026-08-19-truncated-json-recovery-and-finding-submission-design.md` §3.3（Phase 2a）+ §3.4（Phase 2b）+ §3.5（Phase 2c）。姊妹 plan：`docs/superpowers/plans/2026-08-19-vuln-queue-hardening-phase1.md`（Phase 1）。

## Global Constraints

- **前置：Phase 1（plan1）已合入**——Task 3 消费其产物：executor 写盘点的 if/elif 结构（跳过分支带 `NOT written` warning）与 `_validation_error_context(result) -> dict`（含恒 0 的 `collector_submitted_count` / `collector_roster_count` 键）。若未合入，先执行 plan1。
- **双引擎零新代码**：`submit_finding` 经 bridge.py 现有 `mode="append"` 闭包（返 `f"{tool_name}: recorded (N total)"`），不写引擎特定工具代码。
- **双轨铁律**（CLAUDE.md §1）：prompt 改造只加工具指令/删通道表述，不引任何确定性层产物；重查 agent 输入 = LLM 自身产物（deliverable md）+ repo 代码。`tests/prompts/test_static_dataflow_hints_decoupling.py` 维持绿。
- **queue 格式不变**：写盘仍为 `{"vulnerabilities": [...]}`，下游 `VulnerabilityQueue.parse_lenient` 零改动。
- **单条上交**：`submit_finding` schema 是单个 finding object（非 array），prompt 明令 one finding per call / 不攒批 / 不攒尾。
- **重查一轮封顶**：重查后仍缺 → warning + 降级接受，不整跑重试（spec §3.4）。
- **测试只跑改动相关文件**（CLAUDE.md §3：全套 pytest 有预存挂起，禁止广跑）。
- commit 信息用中文、尾注 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。
- 包根：core 路径相对 `/root/shannon-py/packages/core/`；whitebox 相对 `/root/shannon-py/packages/whitebox/`；pytest 分别从对应包目录跑；prompts 在仓库根 `/root/shannon-py/prompts/`。

---

### Task 1: collector 扩展 — `submit_finding` append section + `finding_roster` 声明字段

**Files:**
- Modify: `packages/core/src/supernova_core/collectors/vuln.py`
- Test: `packages/core/tests/collectors/test_vuln_models.py`（更新既有断言 + 追加用例）

**Interfaces:**
- Consumes: `CollectorBase.append_section`（base.py，append 模式已支持）；`SectionSchema(mode="append")`。
- Produces: `make_vuln_sections(vc)` 返回 **5** 个 section——`submit_finding`（section_key=`submitted_findings`，mode=append，json_schema=单 finding object）居首，4 个 `set_*` 原样；`FINDINGS_SUMMARY` 新增 required 字段 `finding_roster`（`list[{id,title}]`）。executor（Task 3/4）经 `collector.get_all()` 读 `submitted_findings`（`list[dict]`，未调时键缺失）与 `findings_summary.finding_roster`（未调时 `findings_summary` 键缺失）。

- [ ] **Step 1: Write the failing test**

`packages/core/tests/collectors/test_vuln_models.py`——顶部 `EXPECTED_TOOL_NAMES` / `EXPECTED_SECTION_KEYS` 常量替换为：

```python
EXPECTED_TOOL_NAMES = [
    "submit_finding",
    "set_findings_summary",
    "set_strategic_intelligence",
    "set_safe_vectors",
    "set_blind_spots",
]
EXPECTED_SECTION_KEYS = [
    "submitted_findings",
    "findings_summary",
    "strategic_intelligence",
    "safe_vectors",
    "blind_spots",
]
```

文件末尾追加：

```python
# ── Phase 2（spec 2026-08-19 §3.3）：submit_finding 单条上交 + finding_roster ──

def test_submit_finding_is_append_mode_single_object_schema():
    """submit_finding：mode=append（可多次调累积）、schema 是单个 finding object（非 array）。"""
    from supernova_core.collectors.base import CollectorBase

    for vc in VULN_CLASSES:
        sections = make_vuln_sections(vc)
        sub = sections[0]
        assert sub.tool_name == "submit_finding" and sub.section_key == "submitted_findings"
        assert sub.mode == "append", vc
        assert sub.json_schema["type"] == "object"  # 单条，非 array
        # 基线 required（title 进 required——roster 对账依赖）
        for f in ("ID", "vulnerability_type", "externally_exploitable", "confidence", "title"):
            assert f in sub.json_schema["required"], (vc, f)
        # collector 实际可累积多条（append 语义）
        c = CollectorBase(section_schemas=make_vuln_sections(vc))
        c.append_section("submit_finding", {"ID": "AUTH-VULN-01", "title": "a", "notes": "n"})
        c.append_section("submit_finding", {"ID": "AUTH-VULN-02", "title": "b"})
        assert [f["ID"] for f in c.get_all()["submitted_findings"]] == ["AUTH-VULN-01", "AUTH-VULN-02"]


def test_finding_schema_class_specific_fields():
    """per-class finding schema：class 特有字段各不相同（宽松 optional，无 enum）。"""
    def _props(vc):
        sub = make_vuln_sections(vc)[0]
        return set(sub.json_schema["properties"]) - {
            "ID", "vulnerability_type", "externally_exploitable", "confidence", "title", "notes"}

    assert _props("auth") == {
        "source_endpoint", "vulnerable_code_location", "missing_defense",
        "exploitation_hypothesis", "suggested_exploit_technique"}
    assert _props("ssrf") == _props("auth") | {"vulnerable_parameter"}
    assert _props("authz") == {
        "endpoint", "vulnerable_code_location", "role_context", "guard_evidence",
        "side_effect", "reason", "minimal_witness"}
    # injection 与 xss 共用 XSS 风格字段（对齐 queue_schemas.py 注释：两轨同 schema）
    assert _props("injection") == _props("xss") == {
        "source", "source_detail", "path", "sink_function", "render_context",
        "encoding_observed", "verdict", "mismatch_reason", "witness_payload"}


def test_findings_summary_roster_required():
    """set_findings_summary 的 finding_roster：required、list[{id,title}] 结构。"""
    props = FINDINGS_SUMMARY["properties"]
    assert "finding_roster" in FINDINGS_SUMMARY["required"]
    item = props["finding_roster"]["items"]
    assert set(item["required"]) == {"id", "title"}
    # 空数组合法（= 声明无漏洞）
    assert props["finding_roster"]["type"] == "array"
    assert props["finding_roster"].get("minItems") is None
```

import 区补 `FINDINGS_SUMMARY`（追加到现有 `from supernova_core.collectors.vuln import (...)` 列表）。

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /root/shannon-py/packages/core && python -m pytest tests/collectors/test_vuln_models.py -v
```
Expected: FAIL——`test_each_class_has_four_set_tools_in_ts_order` 断言 4≠5；新用例 `IndexError`/`KeyError`（sections[0] 是 set_findings_summary、无 FINDINGS_SUMMARY import）。

- [ ] **Step 3: Write minimal implementation**

`packages/core/src/supernova_core/collectors/vuln.py` 三处改动：

**3a.** `FINDINGS_SUMMARY`（现 `:65-85`）的 properties 加 `finding_roster`、required 加该键：

```python
# set_findings_summary (§1 + §2 + roster 对账声明, spec 2026-08-19 §3.3)
FINDINGS_SUMMARY: dict = _obj(
    {
        "key_outcome": _str_field(
            "One to two sentences capturing the headline result of your analysis — what was "
            "found and its severity profile (e.g. \"Several high-confidence SQL injection "
            'vulnerabilities were identified; all findings have been passed to the exploitation '
            'phase"). Becomes Section 1 of the rendered deliverable.'
        ),
        "patterns": {
            "type": "array",
            "items": _PATTERN,
            "description": (
                "Complete list of dominant patterns observed across findings. Pass all patterns "
                "in one call. Empty array is acceptable if no recurring patterns were observed — "
                'the deliverable will render "No dominant patterns identified" for Section 2 in '
                "that case."
            ),
        },
        "finding_roster": {
            "type": "array",
            "items": _obj(
                {
                    "id": _str_field(
                        'Finding ID exactly as submitted via submit_finding (e.g. "AUTH-VULN-01").'
                    ),
                    "title": _str_field("Finding title exactly as submitted via submit_finding."),
                },
                ["id", "title"],
            ),
            "description": (
                "Reconciliation roster: the COMPLETE list of {id, title} for EVERY finding you "
                "submitted via submit_finding this session — one entry per submission, IDs "
                "matching exactly. Empty array if and only if you found no vulnerabilities. "
                "The host reconciles this roster against your submissions to catch lost ones."
            ),
        },
    },
    ["key_outcome", "patterns", "finding_roster"],
)
```

**3b.** per-class finding schema（插在 `_STRATEGIC_INTEL_SCHEMAS` 之后、`_section` helper 之前）：

```python
# ============================================================================
# submit_finding per-class finding schemas（spec 2026-08-19 §3.3）
# 单条 finding object（append item），基线 required + class 特有 optional（无 enum，
# 宽松优先——下游 parse_lenient 容错解析，enum 反而拒收合法变体）。
# ============================================================================

def _finding_props(class_props: dict) -> dict:
    props = {
        "ID": _str_field('Unique ID for this finding (e.g. "AUTH-VULN-01"); '
                         "reuse the same ID in finding_roster."),
        "vulnerability_type": _str_field(
            "Vulnerability subtype label for this class (free-form from the methodology, "
            'e.g. "Authentication_Bypass", "Session_Management_Flaw").'),
        "externally_exploitable": {
            "type": "boolean",
            "description": ("true if reachable from the public internet without prior "
                            "authentication state; false for internal/cross-service only."),
        },
        "confidence": _str_field('"High" | "Medium" | "Low".'),
        "title": _str_field(
            "一句话描述性标题，编码缺陷 + 位置，用简体中文撰写（漏洞类型/参数/路径/端点保留英文），"
            "如 'POST /login 缺少速率限制，可被暴力破解'。不要只写裸标签。"),
        "notes": _str_field(
            "Relevant details: required session state, applicable roles, observed headers, "
            "links to related findings."),
    }
    props.update(class_props)
    return props


_FINDING_BASE_REQUIRED = ["ID", "vulnerability_type", "externally_exploitable",
                          "confidence", "title"]

_INJ_XSS_FINDING_PROPS: dict = {
    "source": _str_field("The tainted input vector (parameter/field/body path)."),
    "source_detail": _str_field("Where the input enters (route + handler)."),
    "path": _str_field("Source→sink dataflow path summary; HTTP-reachable时以 METHOD /route 开头."),
    "sink_function": _str_field("The dangerous sink call (function + file:line)."),
    "render_context": _str_field("XSS-only: render context (HTML_BODY/HTML_ATTRIBUTE/JAVASCRIPT_STRING/URL_PARAM/CSS_VALUE)."),
    "encoding_observed": _str_field("Encoding/sanitization observed on the path (or none)."),
    "verdict": _str_field('"vulnerable" | "safe" — only vulnerable findings are submitted.'),
    "mismatch_reason": _str_field("Why the defense fails / mismatches."),
    "witness_payload": _str_field("Minimal concrete payload value proving the flaw (payload 值本身，无前缀无说明)."),
}

_AUTH_FINDING_PROPS: dict = {
    "source_endpoint": _str_field('"{HTTP_METHOD} {endpoint_path}".'),
    "vulnerable_code_location": _str_field("Exact file:line of the flawed logic or missing check."),
    "missing_defense": _str_field("Concise core problem (e.g. 'No rate limit on POST /login')."),
    "exploitation_hypothesis": _str_field("Active attack outcome on success (not just confirmation)."),
    "suggested_exploit_technique": _str_field("Attack pattern to attempt (e.g. 'brute_force_login')."),
}

_SSRF_FINDING_PROPS: dict = {
    **_AUTH_FINDING_PROPS,
    "vulnerable_parameter": _str_field("The outbound-request parameter carrying attacker-controlled input."),
}

_AUTHZ_FINDING_PROPS: dict = {
    "endpoint": _str_field("Affected endpoint (e.g. 'POST /api/auth/logout')."),
    "vulnerable_code_location": _str_field("Guard location (file:line)."),
    "role_context": _str_field("Roles involved (owner/victim or role pair)."),
    "guard_evidence": _str_field("What the guard checks vs. omits (ownership re-validation gap)."),
    "side_effect": _str_field("State-changing effect reachable without authorization."),
    "reason": _str_field("Why this is exploitable (missing check / broken object-level auth)."),
    "minimal_witness": _str_field("Minimal request pair or ID substitution demonstrating the flaw."),
}

_FINDING_SCHEMAS: dict[str, dict] = {
    "injection": _obj(_finding_props(_INJ_XSS_FINDING_PROPS), _FINDING_BASE_REQUIRED),
    "xss": _obj(_finding_props(_INJ_XSS_FINDING_PROPS), _FINDING_BASE_REQUIRED),
    "auth": _obj(_finding_props(_AUTH_FINDING_PROPS), _FINDING_BASE_REQUIRED),
    "ssrf": _obj(_finding_props(_SSRF_FINDING_PROPS), _FINDING_BASE_REQUIRED),
    "authz": _obj(_finding_props(_AUTHZ_FINDING_PROPS), _FINDING_BASE_REQUIRED),
}
```

**3c.** `_section` helper 加 mode 参数，`make_vuln_sections` 返回 5 个 section（submit_finding 居首）：

```python
def _section(tool_name: str, key: str, desc: str, schema: dict,
             mode: str = "set") -> SectionSchema:
    return SectionSchema(
        tool_name=tool_name, section_key=key, description=desc,
        json_schema=schema, mode=mode
    )


def make_vuln_sections(vuln_class: str) -> list[SectionSchema]:
    """5 个 section（spec 2026-08-19 §3.3 后）：submit_finding（append，数据主通道）居首
    + 4 个 set_*（write-once，md 渲染通道，顺序对齐 TS VULN_TOOLS）。
    strategic_intelligence 按 class 选 schema。"""
    if vuln_class not in _STRATEGIC_INTEL_SCHEMAS:
        raise ValueError(f"unknown vuln class: {vuln_class!r}")
    intel_schema = _STRATEGIC_INTEL_SCHEMAS[vuln_class]
    finding_schema = _FINDING_SCHEMAS[vuln_class]
    return [
        _section(
            "submit_finding",
            "submitted_findings",
            "Submit ONE confirmed vulnerable finding IMMEDIATELY when its verdict is "
            "vulnerable — one finding per call, never batched. The host assembles the "
            "exploitation queue from these submissions.",
            finding_schema,
            mode="append",
        ),
        _section(
            "set_findings_summary",
            "findings_summary",
            "Headline result (Section 1) + dominant patterns (Section 2) + finding_roster "
            '(reconciliation roster). Empty patterns array renders "No dominant patterns '
            'identified".',
            FINDINGS_SUMMARY,
        ),
        _section(
            "set_strategic_intelligence",
            "strategic_intelligence",
            f"{vuln_class} strategic intelligence (Section 3). Per-class schema.",
            intel_schema,
        ),
        _section(
            "set_safe_vectors",
            "safe_vectors",
            'Vectors/components confirmed secure (Section 4). Empty renders "No vectors confirmed '
            'secure during analysis".',
            SAFE_VECTORS,
        ),
        _section(
            "set_blind_spots",
            "blind_spots",
            'Analysis constraints or blind spots (Section 5). Empty renders "No analysis '
            'constraints or blind spots identified".',
            BLIND_SPOTS,
        ),
    ]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /root/shannon-py/packages/core && python -m pytest tests/collectors/ -v
```
Expected: 全部 PASS（test_vuln_models 更新后 4 例 + 新 3 例；test_vuln_registry / 其余 collector 测试无回归——renderer 只读固定 4 section_key，`submitted_findings` 多余键不影响渲染）。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py && git add packages/core/src/supernova_core/collectors/vuln.py packages/core/tests/collectors/test_vuln_models.py && git commit -m "feat(collectors): submit_finding 单条上交 section + finding_roster 声明字段 — spec 2026-08-19 §3.3

submit_finding=append 模式 per-class 单条 schema（title 进 required）；
set_findings_summary.finding_roster 全量对账名单（required，空数组=无漏洞）。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: 对账纯函数 `vuln_queue_reconcile.py`

**Files:**
- Create: `packages/core/src/supernova_core/agents/vuln_queue_reconcile.py`
- Create: `packages/core/tests/agents/test_vuln_queue_reconcile.py`

**Interfaces:**
- Consumes: 无（纯函数，无 SDK / 无 LLM 依赖）。
- Produces:
  - `dedupe_by_id(items: list[dict]) -> tuple[list[dict], list[str]]`——by-ID 后交覆盖先，返回（去重列表, 被覆盖的重复 ID）；
  - `reconcile_findings(submitted_items: list[dict] | None, roster: list[dict] | None) -> ReconcileResult`；
  - `ReconcileResult`（dataclass）字段：`merged: list[dict]`、`missing: list[dict]`（roster 有提交无的 `[{id,title}]`）、`extra_ids: list[str]`、`roster_present: bool`、`write_empty_queue: bool`、`skip_write: bool`。Task 3/4 消费。

- [ ] **Step 1: Write the failing test**

创建 `packages/core/tests/agents/test_vuln_queue_reconcile.py`：

```python
"""roster 对账纯函数（spec 2026-08-19 §3.4 六分支表）。

submitted = collector 的 submit_finding 累积（get_all()['submitted_findings']）；
roster = set_findings_summary.finding_roster（None=没调/没给字段）。
"""
from supernova_core.agents.vuln_queue_reconcile import (
    ReconcileResult,
    dedupe_by_id,
    reconcile_findings,
)


def _sub(i: int) -> dict:
    return {"ID": f"AUTH-VULN-{i:02d}", "title": f"t{i}"}


def _roster(i: int) -> dict:
    return {"id": f"AUTH-VULN-{i:02d}", "title": f"t{i}"}


def test_dedupe_by_id_later_wins():
    items = [_sub(1), _sub(2), {**_sub(1), "title": "revised"}]
    merged, dup = dedupe_by_id(items)
    assert [f["ID"] for f in merged] == ["AUTH-VULN-01", "AUTH-VULN-02"]
    assert merged[0]["title"] == "revised"  # 后交覆盖
    assert dup == ["AUTH-VULN-01"]


def test_full_match_passes():
    rec = reconcile_findings([_sub(i) for i in (1, 2, 3)],
                             [_roster(i) for i in (1, 2, 3)])
    assert isinstance(rec, ReconcileResult)
    assert len(rec.merged) == 3 and not rec.missing and not rec.extra_ids
    assert rec.roster_present and not rec.write_empty_queue and not rec.skip_write


def test_missing_detected():
    rec = reconcile_findings([_sub(1), _sub(2)],
                             [_roster(1), _roster(2), _roster(7)])
    assert rec.missing == [_roster(7)]
    assert not rec.skip_write


def test_extra_kept_recall_first():
    rec = reconcile_findings([_sub(1), _sub(9)],
                             [_roster(1)])
    assert rec.extra_ids == ["AUTH-VULN-09"]
    assert any(f["ID"] == "AUTH-VULN-09" for f in rec.merged)  # 保留


def test_true_zero_vulns_writes_empty_queue():
    rec = reconcile_findings([], [])
    assert rec.roster_present and rec.write_empty_queue and not rec.skip_write
    assert rec.merged == [] and rec.missing == []


def test_total_defiance_skips_write():
    """无 roster 无提交 → skip_write=True（不写盘，validator 防线整跑重试）。"""
    rec = reconcile_findings(None, None)
    assert rec.skip_write and not rec.write_empty_queue
    assert rec.merged == [] and not rec.missing


def test_no_roster_but_submitted_writes_and_skips_reconcile():
    rec = reconcile_findings([_sub(1), _sub(2)], None)
    assert not rec.roster_present and not rec.skip_write and not rec.write_empty_queue
    assert len(rec.merged) == 2 and rec.missing == []  # 跳过对账，已收数据不丢


def test_none_submitted_with_roster_means_all_missing():
    rec = reconcile_findings(None, [_roster(1), _roster(2)])
    assert rec.missing == [_roster(1), _roster(2)] and not rec.skip_write
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /root/shannon-py/packages/core && python -m pytest tests/agents/test_vuln_queue_reconcile.py -v
```
Expected: FAIL（`ModuleNotFoundError: supernova_core.agents.vuln_queue_reconcile`）。

- [ ] **Step 3: Write minimal implementation**

创建 `packages/core/src/supernova_core/agents/vuln_queue_reconcile.py`：

```python
"""vuln queue roster 对账（spec 2026-08-19 §3.4）——纯函数，无 SDK / LLM 依赖。

collector 单条上交的丢失源是模型行为遗漏（研判了忘调 submit_finding，内容从未到达
host）；session 末尾的 finding_roster 声明（几百字符小 payload）是全量账本。本模块
把两份账确定性对齐，产出六分支判定（写盘 / 空写 / 不写 / 漏交清单 / 多交清单）。

分支表（spec §3.4）：
- roster N = submitted N（ID 一致）      → 完整写盘
- roster N > submitted（缺 ID）          → missing 非空（executor 定向重查后写盘）
- submitted 多出 roster                  → 保留（召回优先）+ extra_ids（warning）
- roster=[] 且 submitted=[]              → write_empty_queue（真·无漏洞）
- roster 缺失（summary 没调）且 submitted=[] → skip_write（防线整跑重试）
- roster 缺失且 submitted 非空           → 写盘 + 跳过对账（已收数据不丢）
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ReconcileResult:
    merged: list[dict] = field(default_factory=list)
    missing: list[dict] = field(default_factory=list)   # [{id, title}] 待定向重查
    extra_ids: list[str] = field(default_factory=list)  # submitted 有 roster 无（保留）
    roster_present: bool = False
    write_empty_queue: bool = False
    skip_write: bool = False


def dedupe_by_id(items: list[dict]) -> tuple[list[dict], list[str]]:
    """submit_finding 累积按 ID 去重、后交覆盖（模型修正场景）；返回 (去重列表, 被覆盖 ID)。"""
    by_id: dict[str, dict] = {}
    overwritten: list[str] = []
    for it in items or []:
        fid = str(it.get("ID", ""))
        if not fid:
            continue
        if fid in by_id:
            overwritten.append(fid)
        by_id[fid] = it
    return list(by_id.values()), overwritten


def reconcile_findings(
    submitted_items: list[dict] | None, roster: list[dict] | None
) -> ReconcileResult:
    res = ReconcileResult()
    res.merged, _overwritten = dedupe_by_id(submitted_items or [])
    res.roster_present = roster is not None

    if not res.roster_present:
        # 分支：roster 缺失——submitted 非空则写盘跳过对账；空则交给防线
        res.skip_write = not res.merged
        return res

    merged_ids = {str(f.get("ID", "")) for f in res.merged}
    roster_ids = {str(r.get("id", "")) for r in roster}
    res.missing = [
        {"id": str(r.get("id", "")), "title": str(r.get("title", ""))}
        for r in roster if str(r.get("id", "")) not in merged_ids
    ]
    res.extra_ids = sorted(merged_ids - roster_ids)
    res.write_empty_queue = (not roster) and (not res.merged)
    return res
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /root/shannon-py/packages/core && python -m pytest tests/agents/test_vuln_queue_reconcile.py -v
```
Expected: 8 例全部 PASS。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py && git add packages/core/src/supernova_core/agents/vuln_queue_reconcile.py packages/core/tests/agents/test_vuln_queue_reconcile.py && git commit -m "feat(agents): roster 对账纯函数 vuln_queue_reconcile — spec 2026-08-19 §3.4

dedupe_by_id（后交覆盖）+ reconcile_findings 六分支判定（missing/extra/
空写/不写），executor 写盘与定向重查的消费接口。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: executor 写盘改造 — vuln 分支接对账（missing 暂降级）

**Files:**
- Modify: `packages/core/src/supernova_core/agents/executor.py`（import 区 + `_validation_error_context` 签名 + queue 写盘区域）
- Create: `packages/core/tests/test_executor_vuln_queue_reconcile.py`

**Interfaces:**
- Consumes: Task 1 的 `submitted_findings` / `findings_summary.finding_roster`（经 `collector.get_all()`）；Task 2 的 `reconcile_findings`；Phase 1 的 `_validation_error_context(result)` 与写盘点 if/elif 结构。
- Produces: vuln agent 的 queue 写盘新语义——`agent_name.value.endswith("-vuln")` 走 collector 对账分支（skip_write 不写 / write_empty_queue 写空 / merged 写盘 / missing 非空**暂** warning 降级，Task 4 升级为定向重查）；`_validation_error_context(result, collector_counts=None)` 新签名（第二个参数 `dict | None`，覆盖 `collector_submitted_count`/`collector_roster_count` 两键）。非 vuln 路径行为不变。

- [ ] **Step 1: Write the failing test**

创建 `packages/core/tests/test_executor_vuln_queue_reconcile.py`：

```python
"""executor vuln queue 写盘对账（spec 2026-08-19 §3.4）：collector 主通道六分支。

monkeypatch 模式对齐 test_executor_validation_diagnostics.py：fake run_claude_prompt
返回无 structured_output 的成功 result（Phase 2 后 vuln agent 停传 schema，
structured_output 恒 None），collector 预填 submitted_findings / findings_summary。
"""
import asyncio
import json

import pytest

from supernova_core.collectors.base import CollectorBase
from supernova_core.collectors.vuln import make_vuln_sections
from supernova_core.models.errors import PentestError


def _run(coro):
    return asyncio.run(coro)


class _R:
    success = True
    turns = 2
    cost = 0.1
    cost_currency = "CNY"
    text = "done"
    error = None
    retryable = False
    model = "stub"
    stop_reason = "end_turn"

    class tokens:
        input_tokens = 10
        output_tokens = 5
        cache_read_input_tokens = 0
        cache_creation_input_tokens = 0

    structured_output = None  # B 拓扑：vuln queue 不再走 structured_output


def _prefilled_collector(submitted: list[dict], roster=None) -> CollectorBase:
    c = CollectorBase(section_schemas=make_vuln_sections("auth"))
    for it in submitted:
        c.append_section("submit_finding", it)
    summary = {"key_outcome": "ko", "patterns": []}
    if roster is not None:
        summary["finding_roster"] = roster
    c.set_section("set_findings_summary", summary)
    return c


def _setup(tmp_path, monkeypatch, collector):
    from supernova_core.agents import executor as exec_mod
    from supernova_core.models.agents import AGENTS, AgentName
    from supernova_core.prompts.manager import PromptManager

    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    defn = AGENTS[AgentName.AUTH_VULN]
    (deliverables / defn.deliverable_filename).write_text("placeholder", encoding="utf-8")

    async def fake_run(**kw):
        return _R()

    monkeypatch.setattr(exec_mod, "run_claude_prompt", fake_run)
    monkeypatch.setattr(exec_mod, "make_collector", lambda name: collector)
    monkeypatch.setattr(exec_mod.GitManager, "ensure_repository",
                        classmethod(lambda cls, p: asyncio.sleep(0)))
    monkeypatch.setattr(exec_mod.GitManager, "create_checkpoint",
                        lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(exec_mod.GitManager, "commit",
                        lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(exec_mod, "render_deliverable", lambda *a, **k: None)

    pm = PromptManager.__new__(PromptManager)
    pm.prompts_dir = tmp_path
    monkeypatch.setattr(pm, "load_sync", lambda *a, **k: "PROMPT")
    return exec_mod.AgentExecutor(pm), exec_mod, deliverables


def _queue_path(deliverables):
    from supernova_core.utils.paths import intermediate_path
    return intermediate_path(deliverables, "auth_exploitation_queue.json")


def _execute(ax, exec_mod, deliverables):
    return _run(ax.execute(
        agent_name=exec_mod.AgentName.AUTH_VULN,
        repo_path=str(deliverables), deliverables_path=str(deliverables),
    ))


def test_full_match_writes_queue_from_collector(tmp_path, monkeypatch):
    submitted = [{"ID": f"AUTH-VULN-{i:02d}", "title": f"t{i}"} for i in (1, 2)]
    roster = [{"id": f["ID"], "title": f["title"]} for f in submitted]
    ax, exec_mod, deliverables = _setup(
        tmp_path, monkeypatch, _prefilled_collector(submitted, roster))
    _execute(ax, exec_mod, deliverables)
    data = json.loads(_queue_path(deliverables).read_text("utf-8"))
    assert [f["ID"] for f in data["vulnerabilities"]] == ["AUTH-VULN-01", "AUTH-VULN-02"]


def test_true_zero_vulns_writes_empty_queue(tmp_path, monkeypatch):
    ax, exec_mod, deliverables = _setup(
        tmp_path, monkeypatch, _prefilled_collector([], []))
    _execute(ax, exec_mod, deliverables)
    assert json.loads(_queue_path(deliverables).read_text("utf-8")) == {"vulnerabilities": []}


def test_total_defiance_still_hits_validator_line(tmp_path, monkeypatch):
    """无 roster 无提交 → 不写盘 → validate 防线 raise（整跑重试语义保留）。"""
    c = CollectorBase(section_schemas=make_vuln_sections("auth"))  # 什么都没调
    ax, exec_mod, deliverables = _setup(tmp_path, monkeypatch, c)
    with pytest.raises(PentestError) as ei:
        _execute(ax, exec_mod, deliverables)
    assert "Missing exploitation queue" in str(ei.value)
    ctx = ei.value.context
    assert ctx["collector_submitted_count"] == 0
    assert ctx["collector_roster_count"] == 0


def test_missing_subset_still_writes_and_warns(tmp_path, monkeypatch, caplog):
    """漏交 1 条（Task 3 阶段：warning 降级、写 11 条；Task 4 升级为定向重查）。"""
    import logging
    submitted = [{"ID": f"AUTH-VULN-{i:02d}", "title": f"t{i}"} for i in range(1, 12)]
    roster = [{"id": f["ID"], "title": f["title"]} for f in submitted] + [
        {"id": "AUTH-VULN-12", "title": "lost one"}]
    ax, exec_mod, deliverables = _setup(
        tmp_path, monkeypatch, _prefilled_collector(submitted, roster))
    with caplog.at_level(logging.WARNING, logger="supernova_core.agents.executor"):
        _execute(ax, exec_mod, deliverables)
    data = json.loads(_queue_path(deliverables).read_text("utf-8"))
    assert len(data["vulnerabilities"]) == 11
    assert any("missing" in r.getMessage() and "AUTH-VULN-12" in r.getMessage()
               for r in caplog.records)


def test_no_roster_but_submitted_writes(tmp_path, monkeypatch):
    c = CollectorBase(section_schemas=make_vuln_sections("auth"))
    c.append_section("submit_finding", {"ID": "AUTH-VULN-01", "title": "t"})
    ax, exec_mod, deliverables = _setup(tmp_path, monkeypatch, c)
    _execute(ax, exec_mod, deliverables)
    data = json.loads(_queue_path(deliverables).read_text("utf-8"))
    assert [f["ID"] for f in data["vulnerabilities"]] == ["AUTH-VULN-01"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /root/shannon-py/packages/core && python -m pytest tests/test_executor_vuln_queue_reconcile.py -v
```
Expected: 全部 FAIL——现状写盘条件是 `result.structured_output is not None`（fake result 恒 None）→ queue 不写 → 前 4 例 FileNotFoundError / 防线 raise 早于断言。

- [ ] **Step 3: Write minimal implementation**

`packages/core/src/supernova_core/agents/executor.py` 三处改动：

**3a.** import 区（`from .validators import ...` 一组附近）加：

```python
from .vuln_queue_reconcile import reconcile_findings
```

**3b.** `_validation_error_context` 加第二参（Phase 1 产物的函数，签名升级）：

```python
def _validation_error_context(result, collector_counts: dict | None = None) -> dict:
    """validate_deliverable 防线 raise 时的诊断 context（spec 2026-08-19 §3.2）。

    ……（原 docstring 保留）…… collector 计数 Phase 2 起由调用方从对账结果传入。
    """
    ctx = _result_cost_context(result)
    text = getattr(result, "text", "") or ""
    ctx.update({
        "stop_reason": getattr(result, "stop_reason", None),
        "collected_text_len": len(text),
        "collected_text_tail": text[-200:] if text else "",
        "structured_output_present": getattr(result, "structured_output", None) is not None,
        "collector_submitted_count": (collector_counts or {}).get("submitted", 0),
        "collector_roster_count": (collector_counts or {}).get("roster", 0),
    })
    return ctx
```

**3c.** queue 写盘区域（Phase 1 改造后的 `if/elif` 结构）改为三段：

```python
        queue_filename = get_queue_filename(agent_name)
        payload_bag = collector.get_all() if collector is not None else {}
        if (
            not skip_artifact_postprocess
            and queue_filename
            and isinstance(agent_name, AgentName)
            and agent_name.value.endswith("-vuln")
        ):
            # Phase 2 B 拓扑（spec 2026-08-19 §3.4）：vuln queue 走 collector 主通道
            # （submit_finding 单条上交）+ finding_roster 确定性对账；
            # structured_output 通道对 vuln 已停用（activities 停传 schema）。
            roster = (payload_bag.get("findings_summary") or {}).get("finding_roster")
            rec = reconcile_findings(payload_bag.get("submitted_findings"), roster)
            if rec.skip_write:
                logger.warning(
                    "agent %s: no submit_finding submissions and no finding_roster — "
                    "queue NOT written (validator line will retry the whole agent)",
                    agent_name.value,
                )
            else:
                if rec.extra_ids:
                    logger.warning(
                        "agent %s: %d submitted findings not on roster (kept, "
                        "recall-first): %s",
                        agent_name.value, len(rec.extra_ids), rec.extra_ids,
                    )
                if rec.missing:
                    # Task 4 起此处升级为定向重查；当前降级：warning + 接受缺口
                    logger.warning(
                        "agent %s: %d roster findings missing from submissions "
                        "(targeted recheck pending in Phase 2 Task 4): %s",
                        agent_name.value, len(rec.missing),
                        [m["id"] for m in rec.missing],
                    )
                queue_path = intermediate_path(deliverables, queue_filename)
                atomic_write_json(
                    queue_path, {"vulnerabilities": rec.merged})
                logger.info(
                    "agent %s queue written from collector: submitted=%d "
                    "roster=%d merged=%d missing=%d",
                    agent_name.value, len(payload_bag.get("submitted_findings") or []),
                    len(roster or []), len(rec.merged), len(rec.missing),
                )
        elif (
            not skip_artifact_postprocess
            and result.structured_output is not None
            and queue_filename
        ):
            # 旧通道（-exploit 等其余 agent；vuln 已由上方分支接管）。
            queue_path = intermediate_path(deliverables, queue_filename)
            atomic_write_json(queue_path, result.structured_output)
        elif not skip_artifact_postprocess and queue_filename:
            # 诊断（spec 2026-08-19 §3.2）：静默跳过零日志 → warning 留第一现场。
            logger.warning(
                "agent %s produced no structured output — queue %s NOT written "
                "(text_len=%d, stop_reason=%r)",
                agent_name.value, queue_filename,
                len(getattr(result, "text", "") or ""), result.stop_reason,
            )
```

**3d.** validate 包装处（Phase 1 的 try/except）把 collector 计数传入：

```python
        if not skip_artifact_postprocess:
            try:
                await validate_deliverable(deliverables, agent_name)
            except PentestError as exc:
                # 诊断增补（spec 2026-08-19 §3.2/§3.4）：防线 raise 原地补 result 级
                # 证据 + collector 对账计数，再上抛（分类字段不动）。
                submitted = payload_bag.get("submitted_findings") or []
                roster = (payload_bag.get("findings_summary") or {}).get("finding_roster")
                exc.context.update(_validation_error_context(
                    result,
                    {"submitted": len(submitted), "roster": len(roster) if roster is not None else 0},
                ))
                raise
```

（`payload_bag` 在写盘区域已定义于 validate 之前，同一函数作用域内可用。）

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /root/shannon-py/packages/core && python -m pytest tests/test_executor_vuln_queue_reconcile.py -v
```
Expected: 5 例全部 PASS。

- [ ] **Step 5: Run neighbor executor tests (regression)**

```bash
cd /root/shannon-py/packages/core && python -m pytest \
  tests/test_executor_validation_diagnostics.py \
  tests/test_executor_artifact_postprocess.py \
  tests/test_executor_error_code_passthrough.py \
  tests/test_executor_vuln_render.py -v
```
Expected: 全部 PASS（Phase 1 诊断用例的 `_validation_error_context` 默认参数路径不回归；`test_skip_postprocess_avoids_queue_write` 的 skip=True 场景三段分支全不进）。

- [ ] **Step 6: Commit**

```bash
cd /root/shannon-py && git add packages/core/src/supernova_core/agents/executor.py packages/core/tests/test_executor_vuln_queue_reconcile.py && git commit -m "feat(executor): vuln queue 写盘接 roster 对账（collector 主通道）— spec 2026-08-19 §3.4

六分支：完整/空写/不写(防线)/漏交(暂warning降级)/多交保留/无roster写盘跳对账；
_validation_error_context 接 collector 真值计数；非 vuln 路径不变。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: 定向重查 agent — missing 补救（一轮封顶，失败降级）

**Files:**
- Modify: `packages/core/src/supernova_core/agents/executor.py`（`_targeted_recheck` + vuln 分支 missing 接线）
- Test: `packages/core/tests/test_executor_vuln_queue_reconcile.py`（追加用例）

**Interfaces:**
- Consumes: Task 3 的 `rec.missing`（`[{id,title}]`）与写盘分支；模块内已有的 `run_claude_prompt`、`intermediate_path`、`AgentMetrics` 调用模式。
- Produces: `_targeted_recheck(agent_name, repo, deliverables, missing, model_tier, api_key, provider_config, proxy_url) -> list[dict]`（模块级私有协程；任何失败返回 `[]` 不 raise）。vuln 分支 missing 非空时先重查、产出按 ID `setdefault` 并入（不覆盖已交）、仍缺 warning 降级。

- [ ] **Step 1: Write the failing test**

`packages/core/tests/test_executor_vuln_queue_reconcile.py` 末尾追加：

```python
# ── Task 4：定向重查（spec 2026-08-19 §3.4）──────────────────────────────────

class _RecheckR(_R):
    """重查 agent 的假 result：structured_output 返回补交条目。"""
    success = True
    structured_output = {"vulnerabilities": [
        {"ID": "AUTH-VULN-12", "title": "lost one", "vulnerability_type": "X",
         "externally_exploitable": False, "confidence": "Medium", "notes": "rechecked"},
    ]}


def _setup_recheck(monkeypatch, recheck_result):
    """fake run_claude_prompt：第 1 次主 agent、第 2 次定向重查。"""
    from supernova_core.agents import executor as exec_mod
    calls = {"n": 0}

    async def fake_run(prompt=None, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _R()
        assert "AUTH-VULN-12" in prompt and "lost one" in prompt, (
            "recheck prompt must carry missing (ID, title) clues")
        return recheck_result

    monkeypatch.setattr(exec_mod, "run_claude_prompt", fake_run)
    return calls


def test_missing_triggers_recheck_and_merges(tmp_path, monkeypatch):
    submitted = [{"ID": f"AUTH-VULN-{i:02d}", "title": f"t{i}"} for i in range(1, 12)]
    roster = [{"id": f["ID"], "title": f["title"]} for f in submitted] + [
        {"id": "AUTH-VULN-12", "title": "lost one"}]
    c = _prefilled_collector(submitted, roster)
    ax, exec_mod, deliverables = _setup(tmp_path, monkeypatch, c)
    calls = _setup_recheck(monkeypatch, _RecheckR())
    _execute(ax, exec_mod, deliverables)
    assert calls["n"] == 2  # 主 agent + 一次定向重查（一轮封顶）
    data = json.loads(_queue_path(deliverables).read_text("utf-8"))
    assert len(data["vulnerabilities"]) == 12
    merged12 = next(f for f in data["vulnerabilities"] if f["ID"] == "AUTH-VULN-12")
    assert merged12["notes"] == "rechecked"


def test_recheck_failure_degrades_to_warning(tmp_path, monkeypatch, caplog):
    """重查 agent 整体失败（raise / 无 structured_output）→ 降级：写 11 条 + warning。"""
    import logging

    class _BrokenR(_R):
        structured_output = None

    submitted = [{"ID": f"AUTH-VULN-{i:02d}", "title": f"t{i}"} for i in range(1, 12)]
    roster = [{"id": f["ID"], "title": f["title"]} for f in submitted] + [
        {"id": "AUTH-VULN-12", "title": "lost one"}]
    c = _prefilled_collector(submitted, roster)
    ax, exec_mod, deliverables = _setup(tmp_path, monkeypatch, c)
    _setup_recheck(monkeypatch, _BrokenR())
    with caplog.at_level(logging.WARNING, logger="supernova_core.agents.executor"):
        _execute(ax, exec_mod, deliverables)
    data = json.loads(_queue_path(deliverables).read_text("utf-8"))
    assert len(data["vulnerabilities"]) == 11  # 已到手 11 条不置于风险
    assert any("still missing" in r.getMessage() and "AUTH-VULN-12" in r.getMessage()
               for r in caplog.records)


def test_recheck_output_outside_missing_appended_with_warning(tmp_path, monkeypatch, caplog):
    """重查产出非 missing ID → 追加（召回优先）+ warning；仍不覆盖已交条目。"""
    import logging

    class _OffTargetR(_R):
        structured_output = {"vulnerabilities": [
            {"ID": "AUTH-VULN-99", "title": "off target"}]}

    submitted = [{"ID": "AUTH-VULN-01", "title": "t1"}]
    roster = [{"id": "AUTH-VULN-01", "title": "t1"},
              {"id": "AUTH-VULN-02", "title": "lost"}]
    c = _prefilled_collector(submitted, roster)
    ax, exec_mod, deliverables = _setup(tmp_path, monkeypatch, c)
    _setup_recheck(monkeypatch, _OffTargetR())
    with caplog.at_level(logging.WARNING, logger="supernova_core.agents.executor"):
        _execute(ax, exec_mod, deliverables)
    data = json.loads(_queue_path(deliverables).read_text("utf-8"))
    assert [f["ID"] for f in data["vulnerabilities"]] == ["AUTH-VULN-01", "AUTH-VULN-99"]
    assert any("still missing" in r.getMessage() and "AUTH-VULN-02" in r.getMessage()
               for r in caplog.records)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /root/shannon-py/packages/core && python -m pytest tests/test_executor_vuln_queue_reconcile.py -v -k "recheck"
```
Expected: 3 例 FAIL——Task 3 的降级路径没有重查调用（`calls["n"] == 2` 断言失败得 1；后两例 vulnerability 数为 11/1 条而非 12/2 条）。

- [ ] **Step 3: Write minimal implementation**

`packages/core/src/supernova_core/agents/executor.py` 两处改动：

**3a.** 模块级常量与协程（`_validation_error_context` 之后）：

```python
# 定向重查输出 schema（spec §3.4）：宽松基线（ID required + 自由字段）——
# 下游 parse_lenient 逐条校验；比 vuln 主 schema 宽，重查只补 ID+内容。
_RECHECK_OUTPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "vulnerabilities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ID": {"type": "string", "minLength": 1,
                           "description": "The missing finding's ID, exactly as given."},
                    "title": {"type": "string", "minLength": 1},
                    "vulnerability_type": {"type": "string"},
                    "externally_exploitable": {"type": "boolean"},
                    "confidence": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["ID", "title"],
            },
        }
    },
    "required": ["vulnerabilities"],
}

_RECHECK_MAX_TURNS = 60


async def _targeted_recheck(
    agent_name,
    repo: str,
    deliverables,
    missing: list[dict],
    model_tier: str,
    api_key: str | None,
    provider_config: dict | None,
    proxy_url: str | None,
) -> list[dict]:
    """漏交条目的定向重查小 agent（spec 2026-08-19 §3.4）。

    输入只有 LLM 自身产物（主 agent 的 deliverable md）+ repo 代码 + (ID,title)
    线索——守双轨铁律。一轮封顶；任何失败返回 []（降级由调用方 warning 记录）。
    """
    vc = agent_name.value.removesuffix("-vuln")
    md_path = deliverables / f"{vc}_analysis_deliverable.md"
    missing_lines = "\n".join(
        f'- ID: {m["id"]} — title: {m["title"]}' for m in missing)
    prompt = (
        "You are a security analyst performing a TARGETED RE-SUBMISSION pass.\n"
        f"During a prior {vc} vulnerability analysis of this repository, the "
        "following confirmed findings were lost in transit before their "
        "structured submissions reached the host:\n\n"
        f"{missing_lines}\n\n"
        "A full analysis deliverable from the prior pass is available for "
        f"context at: {md_path}\n\n"
        "For each missing finding above: locate the relevant code in this "
        "repository, re-derive the finding (same ID and title), and return it "
        "in your structured output. Return ONLY the missing findings via the "
        'structured output {"vulnerabilities": [...]}; do not re-report '
        "findings outside the missing list."
    )
    try:
        result = await run_claude_prompt(
            prompt=prompt,
            repo_path=repo,
            model_tier=model_tier,
            api_key=api_key,
            structured_output_schema=_RECHECK_OUTPUT_SCHEMA,
            max_turns=_RECHECK_MAX_TURNS,
            provider_config=provider_config,
            proxy_url=proxy_url,
        )
    except Exception:
        logger.warning("targeted recheck agent failed for %s (degraded)",
                       agent_name.value, exc_info=True)
        return []
    so = getattr(result, "structured_output", None)
    items = so.get("vulnerabilities") if isinstance(so, dict) else None
    if not isinstance(items, list):
        return []
    return [it for it in items if isinstance(it, dict) and it.get("ID")]
```

**3b.** vuln 分支的 missing 段（Task 3 的 `if rec.missing:` 块）替换为：

```python
                merged_by_id: dict[str, dict] = {str(f.get("ID", "")): f for f in rec.merged}
                if rec.missing:
                    recheck_items = await _targeted_recheck(
                        agent_name, str(repo), deliverables, rec.missing,
                        defn.model_tier, api_key, provider_config, proxy_url,
                    )
                    for it in recheck_items:
                        rid = str(it.get("ID", ""))
                        if rid and rid not in merged_by_id:
                            merged_by_id[rid] = it  # 不覆盖已交；off-target 追加
                    still = [m["id"] for m in rec.missing if m["id"] not in merged_by_id]
                    if still:
                        logger.warning(
                            "agent %s: %d findings still missing after targeted "
                            "recheck (accepted with degradation, no full retry): %s",
                            agent_name.value, len(still), still,
                        )
                rec.merged = list(merged_by_id.values())
```

（`defn` 在 execute 内已有——`AGENTS[agent_name]`；`api_key` / `provider_config` / `proxy_url` 是 execute 的参数。）

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /root/shannon-py/packages/core && python -m pytest tests/test_executor_vuln_queue_reconcile.py -v
```
Expected: 8 例全部 PASS（Task 3 的 5 例 + 新 3 例；`test_missing_subset_still_writes_and_warns` 在重查接入后 fake_run 第 2 次返回 `_R()`（structured_output=None）→ 重查产出 [] → 仍 11 条 + missing warning，断言不破）。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py && git add packages/core/src/supernova_core/agents/executor.py packages/core/tests/test_executor_vuln_queue_reconcile.py && git commit -m "feat(executor): 漏交条目定向重查小 agent — spec 2026-08-19 §3.4

(ID,title) 线索+自家 deliverable md+repo 代码，一轮封顶；产出 setdefault
并入不覆盖已交；off-target 追加+warning；失败降级 [] 不整跑重试。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: prompt 改造 ×5 — `finding_submission` 节 + 删 final structured output 指引

**Files:**
- Modify: `prompts/vuln-injection.txt`、`prompts/vuln-xss.txt`、`prompts/vuln-auth.txt`、`prompts/vuln-ssrf.txt`、`prompts/vuln-authz.txt`（5 文件同构改造，锚点以 vuln-auth.txt 行号示意，其余文件按标记定位）
- Test: `packages/core/tests/prompts/test_vuln_host_rendered.py`（更新断言）

**Interfaces:**
- Consumes: Task 1 的工具名 `submit_finding`、roster 字段名 `finding_roster`。
- Produces: 5 个 prompt 的新不变量——`<finding_submission>` 节在、`<exploitation_queue_format>` 节不在、submit_finding 单条立即上交指令在、roster 指令在、"final structured output" 表述不在。Task 6 的 schema 停传与之配套（prompt 不再要求结构化输出）。

每个文件的改造是同一组编辑（per-class 差异只在保留的字段表原文）：

- [ ] **Step 1: Write the failing test**

`packages/core/tests/prompts/test_vuln_host_rendered.py`——docstring 末尾追加一行说明 + 文件末尾追加：

```python
# ── Phase 2（spec 2026-08-19 §3.5）：submit_finding 单条上交 + roster，删 final 结构化输出 ──

FINAL_OUTPUT_PATTERNS = (
    "final structured output",
    "structured object of the form",
    "<exploitation_queue_format>",
    "captured automatically",
)


def test_finding_submission_block_present():
    """每个 vuln prompt：finding_submission 节 + 单条立即上交指令。"""
    for name, vuln_class, _ in VULN_PROMPTS:
        text = _read(name)
        assert "<finding_submission>" in text and "</finding_submission>" in text, (
            f"{name}: missing <finding_submission> block")
        assert "submit_finding" in text, f"{name}: missing submit_finding tool name"
        assert "one finding per call" in text, f"{name}: missing one-per-call rule"
        assert "IMMEDIATELY" in text, f"{name}: missing immediacy rule"


def test_roster_instruction_present():
    """每个 vuln prompt：set_findings_summary 携带 finding_roster 全量名单指令。"""
    for name, _, _ in VULN_PROMPTS:
        text = _read(name)
        assert "finding_roster" in text, f"{name}: missing finding_roster instruction"
        assert "empty array" in text.lower(), f"{name}: missing empty-array semantics"


def test_final_structured_output_gone():
    """每个 vuln prompt：final structured output 通道表述无残留（B 拓扑）。"""
    for name, _, _ in VULN_PROMPTS:
        text = _read(name)
        for pat in FINAL_OUTPUT_PATTERNS:
            assert pat not in text, f"{name}: final-output pattern still present: {pat!r}"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /root/shannon-py/packages/core && python -m pytest tests/prompts/test_vuln_host_rendered.py -v -k "finding_submission or roster or final_structured"
```
Expected: 3 例 FAIL（无 `<finding_submission>`、无 submit_finding/finding_roster、final-output 表述仍在）。

- [ ] **Step 3: Apply the prompt edits（5 文件 × 同构 6 编辑）**

以 `prompts/vuln-auth.txt` 为例（其余 4 文件同样编辑，标记定位：`grep -n "exploitation_queue_format\|final structured output\|Relationship to the exploitation queue\|Your Output" prompts/vuln-<class>.txt`）：

**E1 — Your Output 行（:59）**：
```
旧: **Your Output:** `{{DELIVERABLES_PATH}}/auth_analysis_deliverable.md` (host-rendered from your `set_*` tool calls) + `{{DELIVERABLES_PATH}}/auth_exploitation_queue.json` (captured from your final structured output)
新: **Your Output:** `{{DELIVERABLES_PATH}}/auth_analysis_deliverable.md` (host-rendered from your `set_*` tool calls) + `{{DELIVERABLES_PATH}}/auth_exploitation_queue.json` (assembled by the host from your `submit_finding` calls)
```

**E2 — queue format 节标签改名（`<exploitation_queue_format>` → `<finding_submission>`，开闭两个标签）**，节首两行替换：
```
旧: **Purpose:** Defines the format of the exploitation queue JSON.
    **Structure:** The `vulnerability` JSON object MUST follow this exact format:
新: **Purpose:** Defines the fields of a single finding submission.
    **Structure:** Each `submit_finding` argument MUST follow this exact format:
```
（节内 per-class 字段表**原样保留**——它就是 submit_finding 的字段规范人类可读版。）

**E3 — 节首 Purpose 行之前插入上交指令段**（`<finding_submission>` 标签之后第一段）：

```
**SUBMIT IMMEDIATELY, ONE PER CALL:** As soon as you conclude an endpoint/component is **vulnerable**, call the `submit_finding` tool with that single finding — one finding per call. Do NOT batch multiple findings into one call; do NOT hold findings until the end of the session. The host assembles the exploitation queue from your `submit_finding` calls; every finding you never submit is lost.

```

**E4 — verdict 分流行（:201-202 一带）**：
```
旧: - If the verdict is **`vulnerable`**, you must include the finding in your exploitation queue.
新: - If the verdict is **`vulnerable`**, you must submit the finding immediately via `submit_finding`.
```
（safe → set_safe_vectors 行不动。）

**E5 — Relationship 节（:240 一带）整段替换**：
```
旧: **Relationship to the exploitation queue:** The exploitation queue (`auth_exploitation_queue.json`) is captured automatically from your final structured output at session end — separate from the 4 `set_*` tools. The 4 tools produce the analysis deliverable Markdown; the structured-output queue follows the `exploitation_queue_format` schema documented above.
新: **Relationship to the exploitation queue:** The exploitation queue (`auth_exploitation_queue.json`) is assembled by the host from your `submit_finding` calls — separate from the 4 `set_*` tools. The 4 tools produce the analysis deliverable Markdown; `submit_finding` carries the finding data itself.
```

**E6 — Deliverable Emission / Note 节（:250-252 一带）**：删除「The exploitation queue ... is captured automatically **from your final structured output at session end** — your session must conclude by returning a structured object of the form `{"vulnerabilities": [...]}` following the `exploitation_queue_format` above. Do NOT write the queue JSON file yourself with the Write tool; the harness captures it from your structured output.」整段，替换为：

```
**Note:** Do NOT write the queue JSON file yourself with the Write tool — the host assembles it from your `submit_finding` calls. When you call `set_findings_summary` at session end, its `finding_roster` field MUST list `{id, title}` for EVERY finding you submitted via `submit_finding` this session (empty array if and only if you found none) — the host reconciles this roster against your submissions to recover any that were lost.
```

5 个文件逐一应用 E1-E6（`{vc}` / 文件名按 class 替换：injection/xss/auth/ssrf/authz）。改完抽查：

```bash
cd /root/shannon-py && grep -c "finding_submission" prompts/vuln-*.txt && grep -l "final structured output" prompts/vuln-*.txt || echo "CLEAN"
```
Expected: 每文件 ≥2；第二个 grep 无输出（echo CLEAN）。

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /root/shannon-py/packages/core && python -m pytest tests/prompts/ -v
```
Expected: 全部 PASS——新 3 例 + 既有 host-rendered 用例（`SET_TOOLS` 4 工具断言不涉及 queue format，不破）+ 铁律锁定测试。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py && git add prompts/vuln-injection.txt prompts/vuln-xss.txt prompts/vuln-auth.txt prompts/vuln-ssrf.txt prompts/vuln-authz.txt packages/core/tests/prompts/test_vuln_host_rendered.py && git commit -m "feat(prompts): 5 vuln prompt 接 submit_finding 单条上交+roster，删 final structured output — spec 2026-08-19 §3.5

exploitation_queue_format 节改名 finding_submission（字段表保留）；
立即单条上交/禁攒批；roster 全量声明指令；B 拓扑删末条大 JSON 通道表述。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: whitebox 停传 vuln output schema

**Files:**
- Modify: `packages/whitebox/src/supernova_whitebox/pipeline/activities.py:138`（`_vuln_output_schema`）
- Create: `packages/whitebox/tests/test_vuln_output_schema_disabled.py`

**Interfaces:**
- Consumes: Task 5 的 prompt 改造（通道停用配套）。
- Produces: `_vuln_output_schema(agent_name) -> None`（恒返 None）——vuln agent 的 `structured_output_schema` 停传，CLI `--json-schema` / collected_text 兜底对 vuln 不再激活（末条大 JSON 断流面消灭）。调用点 `activities.py:236` 不动。其余 schema 消费者（auth gitnexus judge / precheck 等，`activities.py:541/:598`）不受影响。

- [ ] **Step 1: Write the failing test**

创建 `packages/whitebox/tests/test_vuln_output_schema_disabled.py`：

```python
"""Phase 2 B 拓扑（spec 2026-08-19 §3.5）：vuln agent 停传 structured_output_schema。

queue 数据走 collector（submit_finding），末条大 JSON 通道停用——CLI --json-schema
与 collected_text 兜底对 vuln 不再激活，断流面消灭。
"""
from supernova_core.models.agents import AgentName
from supernova_whitebox.pipeline.activities import _vuln_output_schema

VULN_AGENTS = [
    AgentName.INJECTION_VULN, AgentName.XSS_VULN, AgentName.AUTH_VULN,
    AgentName.SSRF_VULN, AgentName.AUTHZ_VULN,
]


def test_all_vuln_agents_get_no_output_schema():
    for a in VULN_AGENTS:
        assert _vuln_output_schema(a) is None, a


def test_non_vuln_agents_unchanged_none():
    """exploit / 非 vuln agent 原行为就是 None（排除 *-exploit 覆写 queue）。"""
    assert _vuln_output_schema(AgentName.AUTH_EXPLOIT) is None
```

（若 `AgentName` 缺某成员名，以 `models/agents.py` 实际枚举名为准调整——5 个 vuln / 1 个 exploit。）

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /root/shannon-py/packages/whitebox && python -m pytest tests/test_vuln_output_schema_disabled.py -v
```
Expected: `test_all_vuln_agents_get_no_output_schema` FAIL（现返回 schema dict 非 None）。

- [ ] **Step 3: Write minimal implementation**

`activities.py` 的 `_vuln_output_schema` 函数体改为（docstring 顶部追加 Phase 2 说明，函数体仅 `return None`，保留函数与调用点避免波及 :236）：

```python
def _vuln_output_schema(agent_name: AgentName) -> dict | None:
    """Phase 2 B 拓扑（spec 2026-08-19 §3.5）：恒返 None，vuln agent 停传结构化输出。

    queue 数据通道已切换到 collector（submit_finding 单条上交 + finding_roster 对账，
    executor 写盘）；末条大 JSON 通道停用——CLI --json-schema 与 collected_text 兜底
    对 vuln 不再激活，网关断流的原始故障形态（session 正常结束带半截 JSON）在 vuln
    通道不复存在。历史：本函数曾补「schema 未传 → queue 永不落盘」的断线（见原
    docstring），Phase 2 起该断线由 collector 通道接管。

    恒返 None（原 *-vuln / *-exploit 之分随之失效；exploit 仍为 None）。
    """
    return None
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /root/shannon-py/packages/whitebox && python -m pytest tests/test_vuln_output_schema_disabled.py -v
```
Expected: 2 例 PASS。

- [ ] **Step 5: Run whitebox neighbor tests (regression)**

```bash
cd /root/shannon-py/packages/whitebox && python -m pytest tests/ -k "activities or output_schema" -v --co -q | head -20
cd /root/shannon-py/packages/whitebox && python -m pytest tests/ -k "output_schema or vuln" -v
```
Expected: 相关用例 PASS（若既有用例断言 vuln agent 收到 schema，按新语义更新该断言——B 拓扑下预期 None）。

- [ ] **Step 6: Commit**

```bash
cd /root/shannon-py && git add packages/whitebox/src/supernova_whitebox/pipeline/activities.py packages/whitebox/tests/test_vuln_output_schema_disabled.py && git commit -m "feat(whitebox): vuln agent 停传 structured_output_schema — spec 2026-08-19 §3.5 B 拓扑

queue 走 collector 通道；末条大 JSON 断流面消灭；其余 schema 消费者不动。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: 回归收尾（相关测试全集 + 铁律锁定 + 探针说明 + memory）

**Files:**
- 无代码改动（验证性任务；若发现问题就地修复并补进对应 commit）

**Interfaces:**
- Consumes: Task 1-6 的全部产出。
- Produces: Phase 2 完成判定——全部相关测试绿 + 铁律锁定绿 + 双引擎冒烟说明。

- [ ] **Step 1: Run all touched-area tests together**

```bash
cd /root/shannon-py/packages/core && python -m pytest \
  tests/agents/test_llm_json.py \
  tests/agents/test_vuln_queue_reconcile.py \
  tests/agents/test_dual_engine_alignment.py \
  tests/collectors/ \
  tests/prompts/ \
  tests/test_executor_validation_diagnostics.py \
  tests/test_executor_vuln_queue_reconcile.py \
  tests/test_executor_artifact_postprocess.py \
  tests/test_executor_error_code_passthrough.py \
  tests/test_executor_vuln_render.py \
  -v
cd /root/shannon-py/packages/whitebox && python -m pytest tests/test_vuln_output_schema_disabled.py -v
```
Expected: 全部 PASS。任何失败：修复后重跑，并把修复补进对应任务的 commit。

- [ ] **Step 2: Run iron-rule lock test**

```bash
cd /root/shannon-py/packages/core && python -m pytest tests/prompts/test_static_dataflow_hints_decoupling.py -v
```
Expected: PASS（prompt 改造只加工具指令，无确定性产物引入）。

- [ ] **Step 3: Verify bridge needs no engine-specific code**

```bash
cd /root/shannon-py && grep -rn "submit_finding" packages/core/src/supernova_core/agents/providers_openai.py packages/core/src/supernova_core/agents/providers_anthropic.py || echo "CLEAN — submit_finding 全走 bridge 通用桥"
```
Expected: echo CLEAN（双引擎经 bridge.py append 闭包自动对称，无引擎特定补丁）。

- [ ] **Step 4: openai 引擎真机探针（部署前人工验证，需 GLM 环境）**

spec §3.5 要求 Phase 2 收尾在 openai 引擎真机验证一轮 collector 工具调用。执行（需 `.env.profiles` 配好的 glm-openai 环境）：

```bash
cd /root/shannon-py && uv run python scripts/validate_openai_task_probe.py
```

验证点：探针通过（task 子代理委派正常）后，再跑一轮 NodeGoat 白盒 auth 阶段（或最小 vuln agent 冒烟），agents/*.log 搜 `submit_finding` / `queue written from collector` / `finding_roster` 关键字，queue json 落盘且条数 = roster 数。**此项需真实 LLM 环境，若当前环境不可用则明确记录「待部署环境验证」于 Step 5 的 memory，不得默写通过。**

- [ ] **Step 5: Update memory**

向 `/root/.claude/projects/-root-shannon-py/memory/vuln-queue-delivery-hardening-spec.md` 追加 Phase 2 实施状态（commit 主题清单、测试数、探针结论——含真机验证是否完成），并在 `MEMORY.md` 对应行尾补「Phase2 已实施」。

- [ ] **Step 6: Final commit check**

```bash
cd /root/shannon-py && git log --oneline -8
```
Expected: 顶部为 Phase 2 的 6 个 commit（Task 1-6）；无未预期文件混入（`git status --short` 干净或仅剩本次修复产物）。
