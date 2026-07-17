# Host-Rendered Deliverables — Plan 4（exploit agent，5 class，append collector）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Prerequisite:** Plan 1（collector 框架 + 双引擎桥 + pre-recon 端到端）+ Plan 3（5 vuln class）已落地。

**Goal:** 5 个 exploit agent（injection/xss/auth/ssrf/authz）调 `add_exploit`（**append** 语义）结构化工具，core 收集 + `validate_exploit_verdicts`（4 档）+ `render_exploit` 渲染 `{vt}_exploitation_evidence.md`（5 section：Exploited / Blocked / Other / Unverified-rejected / Unprocessed）。**本质=把 exploit 从 blackbox structured-output→`ExploitEvidenceRenderer` 通道迁移到 core append-collector 通道**。

**Architecture:** exploit 与 Plan 1/3 的 set_*（write-once section bag）**本质不同**：exploit 产物是 verdict list（`ExploitVerdictBatch.verdicts: list`，对齐 TS `getAll(): AddExploitInput[]`），用**独立 `ExploitCollector`（append，`get_all()->list[dict]`）+ 独立 `build_exploit_*` 桥 + provider `isinstance` 分支 + `render_deliverable` 扩签名读 queue**。不复用 `CollectorBase`（write-once + `get_all()->dict` 语义冲突）。接点裁定见父 spec §6（commit c84cc8c9）。

**Tech Stack:** pydantic、pytest、claude-agent-sdk、openai-agents。

**Spec:** `docs/superpowers/specs/2026-07-17-host-rendered-deliverables-design.md`（§6 Plan 4 裁定注记）。

## Global Constraints

- **append ≠ set**：`ExploitCollector` 独立类（`add(entry)->None` append、`get_all()->list[dict]`），**不继承 `CollectorBase`**、不暴露 `section_schemas`/`tool_names()`（append 无 section 概念）；generic `build_openai_tools`/`build_claude_mcp_server` 不处理它（由专用 `build_exploit_*` 处理）。
- **4 档对齐现有**：verdict status ∈ {`exploited`/`blocked_by_security`/`out_of_scope_internal`/`false_positive`}，**复用 `packages/core/src/shannon_core/models/exploit_verdict_schemas.py` 的 4 个 Verdict 类**（ExploitedVerdict/BlockedVerdict/OutOfScopeVerdict/FalsePositiveVerdict）——**不新建 entry model**（避免字段漂移；原 plan 4 的 2 档 ExploitEntry/BlockedEntry 已废弃）。
- **queue 是 vuln 的，只读不改**：`{vt}_exploitation_queue.json` 由 vuln agent 产（structured_output 落盘）。结构 `{"vulnerabilities":[{"ID":"...","vulnerability_type":"...",...}]}`。exploit renderer 只读取 `valid_ids`/`id_to_type`，**不写**。
- **单通道**：exploit agent 只产 `{vt}_exploitation_evidence.md`（不产 queue）。迁移后 **blackbox 不再传 `structured_output_schema=ExploitVerdictBatch`**（verdicts 改由 add_exploit 采集）。
- **全迁移保留验证**：`validate_exploit_verdicts`（L0 normalize / L1 schema / L2 queue-ID 防幻觉 / L3 去重）从 blackbox 迁 core；renderer 渲 **5 section**（Exploited/Blocked/Other/Unverified-rejected/Unprocessed），Rejected 与 Unprocessed 正交（rejected=调了 add_exploit 但验证失败；unprocessed=没调）。
- **§1 双轨独立 / §2 双引擎**：renderer 读 queue 是读 LLM 产物（vuln queue），不引 GitNexus 确定性层；`build_exploit_openai_tools`+`build_exploit_claude_mcp_server` 双引擎对称，provider isinstance 分支双引擎一致。
- **TS 对齐 1:1**：`add_exploit` schema（discriminated union on status）、renderer section 标题、prompt 文案移植 TS `exploit-collector.ts`/`exploit-renderer.ts`（TS 字段以 `exploit_verdict_schemas.py` 4 档为准，二者已对齐）。
- **诊断暂不移除**：`_enrich_missing_deliverable_error`（executor.py）保留到 Plan 5。blackbox `ExploitEvidenceRenderer`（旧 3-section renderer）迁移后留死代码，**Plan 5 删**（对齐父 spec §4.5 节奏）。
- **TDD + 测试陷阱**（CLAUDE.md §3）：每 task 先失败测试；只跑改动子集，勿广跑全套（预存挂起/失败）。
- **分支** `feat/fork-py`；每 task 末 commit。

## 现有接口事实（controller 已核查，implementer 直接用）

- **`models/agents.py`**：5 个 exploit 成员存在（`INJECTION_EXPLOIT="injection-exploit"` 等，对称 `-vuln`）；`deliverable_filename` = `{vt}_exploitation_evidence.md`；`prompt_template` = `{vt}-exploit`。
- **`collectors/__init__.py::make_collector(agent_name)`**：当前认 PRE_RECON + `endswith("-vuln")`，对 `-exploit` 返 None。
- **`collectors/bridge.py`**：generic `build_openai_tools(collector)`/`build_claude_mcp_server(collector, server_name="shannon-collector")`，按 `collector.section_schemas` 循环生成 set_* 工具（write-once，DuplicateCallError）。
- **`agents/executor.py`**：L112 `collector = make_collector(agent_name)`；L115-126 透传 `collector=collector`；L169-172 `if not skip_artifact_postprocess and collector is not None: md = render_deliverable(agent_name, collector.get_all()); write_text(md)`；L174 `if not skip_artifact_postprocess: validate_deliverable`。
- **`agents/runner.py::run_claude_prompt(..., collector=None)`**：kwarg 透传 `provider.call(collector=...)`。
- **`agents/providers_anthropic.py`**：L109-114 `if collector is not None: build_claude_mcp_server(collector); allowed_tools = collector.tool_names()`；L320-323 注入 options。
- **`agents/providers_openai.py`**：L201/208-212 `if collector is not None: build_openai_tools(collector)` → `build_agent(..., extra_tools=)`。
- **`renderers/__init__.py::render_deliverable(agent_name, data)`**：当前认 PRE_RECON + `-vuln`，对 `-exploit` 返 None。单输入。
- **`models/exploit_verdict_schemas.py`**：4 档 discriminated union；`ExploitVerdictBatch.model_json_schema()["properties"]["verdicts"]["items"]` = **单条 union schema**（`oneOf:[4 个 $ref] + discriminator mapping`，`$defs` 含 4 verdict 字段）——这是 add_exploit input_schema 来源。
- **blackbox `agents/exploit_executor.py`**：L40-49 读 queue → `valid_ids={v.ID for v in parsed.queue.vulnerabilities}`；L62-75 调 executor 传 `structured_output_schema=ExploitVerdictBatch` + `skip_artifact_postprocess=True`；L78-101 structured_output 兜底 + validate + render + write_verdicts_json。
- **blackbox `services/exploit_verdict_validator.py`**：`validate_exploit_verdicts(raw: list[dict], valid_ids: set[str]) -> VerdictValidation`（accepted/rejected）。消费者：exploit_executor.py + 2 个 blackbox 测试（test_exploit_verdict_validator.py + test_exploit_evidence_renderer.py）。
- **`models/queue_schemas.py::VulnerabilityQueue.parse_lenient(content: str) -> LenientParseResult`**（`.queue.vulnerabilities` list，每 entry 有 `.ID`；`.warnings`）。

## File Structure

- Create: `packages/core/src/shannon_core/collectors/exploit.py`（`ExploitCollector` append + `make_exploit_collector()`）
- Create: `packages/core/src/shannon_core/renderers/exploit.py`（`render_exploit` 5 section）
- Modify: `packages/core/src/shannon_core/collectors/bridge.py`（加 `build_exploit_openai_tools` / `build_exploit_claude_mcp_server`）
- Modify: `packages/core/src/shannon_core/collectors/__init__.py`（make_collector 加 `-exploit` 分支）
- Modify: `packages/core/src/shannon_core/renderers/__init__.py`（render_deliverable 扩签名 + `-exploit` 分支）
- Modify: `packages/core/src/shannon_core/agents/executor.py`（L169 多传 `deliverables`）
- Modify: `packages/core/src/shannon_core/agents/providers_anthropic.py` + `providers_openai.py`（isinstance 分支）
- Migrate: `validate_exploit_verdicts` + `VerdictValidation` 从 blackbox `services/exploit_verdict_validator.py` → core（放 `packages/core/src/shannon_core/collectors/exploit.py` 内同模块，渲染依赖紧密）；blackbox 改 import core
- Modify: `packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py`（迁移接线）
- Modify: `packages/blackbox/tests/test_exploit_verdict_validator.py` + `test_exploit_evidence_renderer.py`（改 import core）
- Modify: `prompts/exploit-{injection,xss,auth,ssrf,authz}.txt`

---

### Task 1: `ExploitCollector`（append）+ validator 迁移到 core

**Files:**
- Create: `packages/core/src/shannon_core/collectors/exploit.py`
- Migrate: `validate_exploit_verdicts` + `VerdictValidation`（从 `packages/blackbox/src/shannon_blackbox/services/exploit_verdict_validator.py` 移入 exploit.py）
- Modify: `packages/blackbox/src/shannon_blackbox/services/exploit_verdict_validator.py`（改为 re-export from core，保旧 import 路径兼容）
- Test: `packages/core/tests/collectors/test_exploit_collector.py`

**Interfaces:**
- Consumes: `shannon_core.models.exploit_verdict_schemas.ExploitVerdict`（4 档 union，discriminated on status）。
- Produces: `ExploitCollector`（`add(entry: dict)->None` append、`get_all()->list[dict]`）、`make_exploit_collector()->ExploitCollector`、`validate_exploit_verdicts(raw, valid_ids)->VerdictValidation`、`VerdictValidation`（accepted: list[ExploitVerdict], rejected: list[tuple[dict,str]]）。

- [ ] **Step 1: Write failing test**

```python
# packages/core/tests/collectors/test_exploit_collector.py
from shannon_core.collectors.exploit import (
    ExploitCollector, make_exploit_collector,
    validate_exploit_verdicts, VerdictValidation,
)


def test_append_accumulates_list_in_order():
    c = ExploitCollector()
    c.add({"vulnerability_id": "INJ-1", "status": "exploited", "severity": "high",
           "impact": "i", "exploitation_steps": ["s1"], "proof_of_impact": "p"})
    c.add({"vulnerability_id": "INJ-2", "status": "blocked_by_security", "confidence": "high",
           "current_blocker": "b", "what_we_tried": "t",
           "evidence_of_vulnerability": "e", "expected_impact": "ei"})
    entries = c.get_all()
    assert len(entries) == 2
    assert entries[0]["vulnerability_id"] == "INJ-1"
    assert entries[1]["status"] == "blocked_by_security"


def test_get_all_returns_copy_and_empty_default():
    c = ExploitCollector()
    assert c.get_all() == []
    c.add({"vulnerability_id": "X-1", "status": "false_positive", "reason": "r", "evidence": "e"})
    got = c.get_all()
    got.clear()
    assert c.get_all()  # 内部 list 不受外部 mutate 影响


def test_validate_accepts_4_tiers_and_rejects_phantom_id():
    raw = [
        {"vulnerability_id": "INJ-1", "status": "exploited", "severity": "critical",
         "impact": "i", "exploitation_steps": ["s"], "proof_of_impact": "p"},
        {"vulnerability_id": "INJ-2", "status": "out_of_scope_internal", "reason": "r", "evidence": "e"},
        {"vulnerability_id": "PHANTOM", "status": "exploited", "severity": "low",
         "impact": "i", "exploitation_steps": ["s"], "proof_of_impact": "p"},
    ]
    res = validate_exploit_verdicts(raw, valid_ids={"INJ-1", "INJ-2"})
    assert [v.vulnerability_id for v in res.accepted] == ["INJ-1", "INJ-2"]
    assert len(res.rejected) == 1 and "PHANTOM" in res.rejected[0][1]
    assert isinstance(res, VerdictValidation)
```

- [ ] **Step 2: Run — verify FAIL**

`cd packages/core && uv run pytest tests/collectors/test_exploit_collector.py -q` → FAIL（ImportError: cannot import ExploitCollector）。

- [ ] **Step 3: Implement**

创建 `packages/core/src/shannon_core/collectors/exploit.py`。**把 blackbox `exploit_verdict_validator.py` 的 `VerdictValidation` + `_SEVERITY_MAP` + `_normalize_verdict` + `validate_exploit_verdicts` 整体迁入**（逐字搬，只改 import 来源：`from shannon_core.models.exploit_verdict_schemas import ExploitVerdict` 不变；pydantic ValidationError import 不变）。再加 ExploitCollector：

```python
# packages/core/src/shannon_core/collectors/exploit.py
"""exploit append collector + verdict 校验（L0-L3）。

append 语义：agent 多次调 add_exploit 累积 verdict list（对齐 TS getAll(): AddExploitInput[]）。
与 CollectorBase(write-once set_* section bag)本质不同——不继承、不复用。
validate_exploit_verdicts 2026-07-17 从 blackbox 迁入 core（blackbox 改 re-export）。
4 档 verdict 复用 models/exploit_verdict_schemas.py 的 discriminated union。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from pydantic import ValidationError

from shannon_core.models.exploit_verdict_schemas import ExploitVerdict


# ── verdict 校验（从 blackbox services/exploit_verdict_validator.py 逐字迁入）──
@dataclass
class VerdictValidation:
    accepted: list[ExploitVerdict] = field(default_factory=list)
    rejected: list[tuple[dict, str]] = field(default_factory=list)


_SEVERITY_MAP = {"critical": "critical", "high": "high", "medium": "medium", "low": "low"}


def _normalize_verdict(item: dict) -> dict:
    """L0 lenient normalize：把 agent 不严格的产出归一化到 ExploitVerdict schema。"""
    v = dict(item)
    status = v.get("status")
    sev = v.get("severity")
    if isinstance(sev, str):
        v["severity"] = _SEVERITY_MAP.get(sev.lower(), "low")
    if status == "exploited":
        steps = v.get("exploitation_steps")
        if isinstance(steps, list) and steps and not isinstance(steps[0], str):
            v["exploitation_steps"] = [
                s.get("action") if isinstance(s, dict) else str(s) for s in steps
            ]
        if isinstance(v.get("proof_of_impact"), (dict, list)):
            v["proof_of_impact"] = json.dumps(v["proof_of_impact"], ensure_ascii=False)
    elif status in ("false_positive", "out_of_scope_internal"):
        if isinstance(v.get("evidence"), (dict, list)):
            v["evidence"] = json.dumps(v["evidence"], ensure_ascii=False)
    elif status == "blocked_by_security":
        wwt = v.get("what_we_tried")
        if isinstance(wwt, list):
            v["what_we_tried"] = "; ".join(str(x) for x in wwt)
    return v


def validate_exploit_verdicts(
    raw: list[dict], valid_ids: set[str]
) -> VerdictValidation:
    """L0 lenient normalize → L1 pydantic discriminated union → L2 id ∈ valid_ids → L3 去重。"""
    seen: set[str] = set()
    accepted: list[ExploitVerdict] = []
    rejected: list[tuple[dict, str]] = []
    for item in raw:
        norm = _normalize_verdict(item) if isinstance(item, dict) else item
        try:
            v = ExploitVerdict.model_validate(norm)  # L1
        except ValidationError as exc:
            rejected.append((norm, f"L1 schema: {exc}"))
            continue
        if v.vulnerability_id not in valid_ids:  # L2 防幻觉
            rejected.append((norm, f"L2 id不在queue: {v.vulnerability_id}"))
            continue
        if v.vulnerability_id in seen:  # L3 去重
            rejected.append((norm, f"L3 重复id: {v.vulnerability_id}"))
            continue
        seen.add(v.vulnerability_id)
        accepted.append(v)
    return VerdictValidation(accepted=accepted, rejected=rejected)


# ── append collector ──────────────────────────────────────────────────────────
class ExploitCollector:
    """per-agent-run 的 verdict append 收集器（非全局，对齐 TS per-agent collector）。

    append 语义：多次 add 累积 list（get_all()->list[dict]），无 DuplicateCallError。
    不继承 CollectorBase（write-once section bag 语义冲突）。
    """

    def __init__(self) -> None:
        self._entries: list[dict] = []

    def add(self, entry: dict) -> None:
        self._entries.append(dict(entry))

    def get_all(self) -> list[dict]:
        return [dict(e) for e in self._entries]


def make_exploit_collector() -> ExploitCollector:
    return ExploitCollector()
```

blackbox `packages/blackbox/src/shannon_blackbox/services/exploit_verdict_validator.py` 改为 re-export（保旧 import 路径 `from shannon_blackbox.services.exploit_verdict_validator import ...` 兼容）：

```python
# packages/blackbox/src/shannon_blackbox/services/exploit_verdict_validator.py
"""validator 已迁 core（Plan 4）。本文件 re-export 保旧 import 路径兼容，Plan 5 删。"""
from shannon_core.collectors.exploit import (  # noqa: F401
    VerdictValidation,
    validate_exploit_verdicts,
)
```

blackbox 测试改 import core（`packages/blackbox/tests/test_exploit_verdict_validator.py`）：

```python
# 旧：from shannon_blackbox.services.exploit_verdict_validator import VerdictValidation, validate_exploit_verdicts
# 新：
from shannon_core.collectors.exploit import VerdictValidation, validate_exploit_verdicts
```

- [ ] **Step 4: Run — verify PASS**

`cd packages/core && uv run pytest tests/collectors/test_exploit_collector.py -q` → 3 passed。
`cd packages/blackbox && uv run pytest tests/test_exploit_verdict_validator.py -q` → 仍 PASS（re-export 兼容 + 测试改 import）。

- [ ] **Step 5: Commit**

`git add packages/core/src/shannon_core/collectors/exploit.py packages/core/tests/collectors/test_exploit_collector.py packages/blackbox/src/shannon_blackbox/services/exploit_verdict_validator.py packages/blackbox/tests/test_exploit_verdict_validator.py && git commit -m "feat(collectors): ExploitCollector(append)+validate_exploit_verdicts 迁 core(blackbox re-export)"`

---

### Task 2: `render_exploit`（5 section，接 VerdictValidation）

**Files:**
- Create: `packages/core/src/shannon_core/renderers/exploit.py`
- Test: `packages/core/tests/renderers/test_exploit.py`

**Interfaces:**
- Consumes: `shannon_core.collectors.exploit.VerdictValidation`（Task 1 产）。
- Produces: `render_exploit(vuln_class: str, validation: VerdictValidation, id_to_type: dict[str,str]) -> str`。

- [ ] **Step 1: Write failing test**

```python
# packages/core/tests/renderers/test_exploit.py
from shannon_core.collectors.exploit import VerdictValidation
from shannon_core.models.exploit_verdict_schemas import (
    BlockedVerdict, ExploitedVerdict, OutOfScopeVerdict, FalsePositiveVerdict,
)
from shannon_core.renderers.exploit import render_exploit


def _val():
    accepted = [
        ExploitedVerdict(vulnerability_id="INJ-1", status="exploited", severity="critical",
                         impact="db dump", exploitation_steps=["s1", "s2"], proof_of_impact="p1"),
        BlockedVerdict(vulnerability_id="INJ-2", status="blocked_by_security", confidence="high",
                       current_blocker="cb", what_we_tried="wt",
                       evidence_of_vulnerability="ev", expected_impact="ei"),
        OutOfScopeVerdict(vulnerability_id="INJ-3", status="out_of_scope_internal",
                          reason="r3", evidence="e3"),
        FalsePositiveVerdict(vulnerability_id="INJ-4", status="false_positive",
                             reason="r4", evidence="e4"),
    ]
    rejected = [({"vulnerability_id": "GHOST"}, "L2 id不在queue: GHOST")]
    return VerdictValidation(accepted=accepted, rejected=rejected)


def test_5_sections_present_with_fields():
    md = render_exploit("injection", _val(), {"INJ-1": "injection", "INJ-2": "injection",
                                               "INJ-3": "injection", "INJ-4": "injection"})
    assert "# Injection Exploitation Report" in md
    assert "## Successfully Exploited" in md and "INJ-1" in md and "s1" in md and "s2" in md
    assert "## Potential Vulnerabilities (Validation Blocked)" in md and "INJ-2" in md and "cb" in md
    assert "## Other Verdicts" in md and "INJ-3" in md and "INJ-4" in md
    assert "## Unverified Findings" in md and "GHOST" in md


def test_exploited_sorted_by_severity_desc():
    md = render_exploit("injection", VerdictValidation(accepted=[
        ExploitedVerdict(vulnerability_id="LOW-1", status="exploited", severity="low",
                         impact="i", exploitation_steps=["s"], proof_of_impact="p"),
        ExploitedVerdict(vulnerability_id="CRIT-1", status="exploited", severity="critical",
                         impact="i", exploitation_steps=["s"], proof_of_impact="p"),
    ], rejected=[]), {"LOW-1": "injection", "CRIT-1": "injection"})
    assert md.find("CRIT-1") < md.find("LOW-1")


def test_unprocessed_surfaces_queue_ids_never_attempted():
    # INJ-9 在 queue(id_to_type) 但 accepted/rejected 都没有 → Unprocessed
    md = render_exploit("injection", VerdictValidation(accepted=[], rejected=[]),
                        {"INJ-9": "injection"})
    assert "## Unprocessed Vulnerabilities" in md and "INJ-9" in md


def test_empty_state_when_no_queue_and_no_verdicts():
    md = render_exploit("injection", VerdictValidation(accepted=[], rejected=[]), {})
    assert "No vulnerabilities were available" in md
```

- [ ] **Step 2: Run — verify FAIL**

`cd packages/core && uv run pytest tests/renderers/test_exploit.py -q` → FAIL（ImportError）。

- [ ] **Step 3: Implement**

```python
# packages/core/src/shannon_core/renderers/exploit.py
"""exploit evidence renderer（纯函数，5 section，对齐 TS exploit-renderer.ts）。

5 section：Successfully Exploited / Potential(Blocked) / Other Verdicts /
Unverified(Rejected) / Unprocessed。Rejected 与 Unprocessed 正交：
rejected=调了 add_exploit 但验证失败；unprocessed=queue 有但没调。
输入 validation(VerdictValidation) + id_to_type(queue ID→type)。
"""
from __future__ import annotations

from shannon_core.collectors.exploit import VerdictValidation

TITLES: dict[str, str] = {
    "injection": "Injection Exploitation Report",
    "xss": "Cross-Site Scripting (XSS) Exploitation Report",
    "auth": "Authentication Exploitation Report",
    "ssrf": "SSRF Exploitation Report",
    "authz": "Authorization (Authz) Exploitation Report",
}

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _exploited(entries):
    out = []
    for v in sorted(entries, key=lambda v: _SEVERITY_ORDER.get(getattr(v, "severity", "low"), 99)):
        out.append(f"### {v.vulnerability_id}")
        out.append(f"- **Severity:** {v.severity}")
        out.append(f"- **Impact:** {v.impact}")
        out.append(f"- **Exploitation Steps:** {'; '.join(v.exploitation_steps)}")
        out.append(f"- **Proof of Impact:** {v.proof_of_impact}")
    return "\n".join(out)


def _blocked(entries):
    out = []
    for v in entries:
        out.append(f"### {v.vulnerability_id}")
        out.append(f"- **Confidence:** {v.confidence}")
        out.append(f"- **Current Blocker:** {v.current_blocker}")
        out.append(f"- **What We Tried:** {v.what_we_tried}")
        out.append(f"- **Evidence of Vulnerability:** {v.evidence_of_vulnerability}")
        out.append(f"- **Expected Impact:** {v.expected_impact}")
    return "\n".join(out)


def _other(entries):
    out = []
    for v in entries:
        out.append(f"### {v.vulnerability_id}")
        out.append(f"- **Status:** {v.status}")
        out.append(f"- **Reason:** {v.reason}")
        out.append(f"- **Evidence:** {v.evidence}")
    return "\n".join(out)


def _unverified(rejected):
    out = []
    for raw, reason in rejected:
        vid = raw.get("vulnerability_id", "<unknown>") if isinstance(raw, dict) else "<unknown>"
        out.append(f"### {vid}")
        out.append(f"- **Reason:** {reason}")
    return "\n".join(out)


def render_exploit(vuln_class: str, validation: VerdictValidation,
                   id_to_type: dict[str, str]) -> str:
    title = f"# {TITLES[vuln_class]}"
    if not validation.accepted and not validation.rejected and not id_to_type:
        return f"{title}\n\n*No vulnerabilities were available in the queue for exploitation.*\n"

    exploited = [v for v in validation.accepted if v.status == "exploited"]
    blocked = [v for v in validation.accepted if v.status == "blocked_by_security"]
    other = [v for v in validation.accepted
             if v.status in ("out_of_scope_internal", "false_positive")]

    attempted = {v.vulnerability_id for v in validation.accepted} | \
                {raw.get("vulnerability_id") for raw, _ in validation.rejected if isinstance(raw, dict)}
    unprocessed = [i for i in id_to_type if i not in attempted]

    parts = [title, ""]
    if exploited:
        parts += ["## Successfully Exploited", "", _exploited(exploited), ""]
    if blocked:
        parts += ["## Potential Vulnerabilities (Validation Blocked)", "", _blocked(blocked), ""]
    if other:
        parts += ["## Other Verdicts", "", _other(other), ""]
    if validation.rejected:
        parts += ["## Unverified Findings (校验未通过，待人工复核)", "", _unverified(validation.rejected), ""]
    if unprocessed:
        items = "\n".join(f"- `{i}` ({id_to_type.get(i, '')})" for i in unprocessed)
        parts += ["## Unprocessed Vulnerabilities", "", items, ""]
    return "\n".join(parts).rstrip() + "\n"
```

- [ ] **Step 4: Run — verify PASS**

`cd packages/core && uv run pytest tests/renderers/test_exploit.py -q` → 4 passed。

- [ ] **Step 5: Commit**

`git add packages/core/src/shannon_core/renderers/exploit.py packages/core/tests/renderers/test_exploit.py && git commit -m "feat(renderers): render_exploit 5 section(exploited/blocked/other/unverified/unprocessed)"`

---

### Task 3: bridge append 工具 + make_collector 分支 + render_deliverable 扩签名 + provider isinstance 分支 + executor 多传 deliverables

**Files:**
- Modify: `packages/core/src/shannon_core/collectors/bridge.py`（加 `build_exploit_openai_tools` / `build_exploit_claude_mcp_server`）
- Modify: `packages/core/src/shannon_core/collectors/__init__.py`（make_collector 加 `-exploit` 分支）
- Modify: `packages/core/src/shannon_core/renderers/__init__.py`（render_deliverable 扩 `deliverables_path=None` + `-exploit` 分支读 queue）
- Modify: `packages/core/src/shannon_core/agents/executor.py:169`（多传 `deliverables`）
- Modify: `packages/core/src/shannon_core/agents/providers_anthropic.py`（isinstance 分支）
- Modify: `packages/core/src/shannon_core/agents/providers_openai.py`（isinstance 分支）
- Test: `packages/core/tests/collectors/test_bridge_exploit.py`、`packages/core/tests/renderers/test_render_deliverable_exploit.py`

**Interfaces:**
- Consumes: `ExploitCollector`、`make_exploit_collector`、`render_exploit`、`validate_exploit_verdicts`（Task 1/2）。
- Produces: `build_exploit_openai_tools(collector)->list[FunctionTool]`、`build_exploit_claude_mcp_server(collector, server_name="exploit")`、`render_deliverable(agent_name, data, deliverables_path=None)`。

- [ ] **Step 1: Write failing tests**

```python
# packages/core/tests/collectors/test_bridge_exploit.py
import asyncio
import json

from shannon_core.collectors.bridge import (
    build_exploit_openai_tools, build_exploit_claude_mcp_server,
)
from shannon_core.collectors.exploit import ExploitCollector


def test_openai_add_exploit_appends_each_call():
    c = ExploitCollector()
    tools = build_exploit_openai_tools(c)
    assert len(tools) == 1 and tools[0].name == "add_exploit"
    payload = {"vulnerability_id": "X-1", "status": "exploited", "severity": "high",
               "impact": "i", "exploitation_steps": ["s"], "proof_of_impact": "p"}
    # on_invoke_tool 是 async —— 必须 await（正确驱动）
    ret1 = asyncio.run(tools[0].on_invoke_tool(None, json.dumps(payload)))
    ret2 = asyncio.run(tools[0].on_invoke_tool(None, json.dumps({**payload, "vulnerability_id": "X-2"})))
    entries = c.get_all()
    assert len(entries) == 2  # append：两次调用都生效（无 write-once DuplicateError）
    assert entries[0]["vulnerability_id"] == "X-1" and entries[1]["vulnerability_id"] == "X-2"
    assert "added exploit" in ret1 and "X-1" in ret1


def test_claude_exploit_server_has_single_add_exploit_tool():
    c = ExploitCollector()
    server = build_exploit_claude_mcp_server(c)
    assert server is not None
```

```python
# packages/core/tests/renderers/test_render_deliverable_exploit.py
import json

from shannon_core.models.agents import AgentName
from shannon_core.renderers import render_deliverable


def test_render_deliverable_exploit_reads_queue_and_renders(tmp_path):
    # queue（vuln agent 产）：INJ-1 + INJ-9（INJ-9 未 attempt → Unprocessed）
    (tmp_path / "injection_exploitation_queue.json").write_text(json.dumps(
        {"vulnerabilities": [{"ID": "INJ-1", "vulnerability_type": "SQLi"},
                             {"ID": "INJ-9", "vulnerability_type": "SQLi"}]}))
    # collector entries（add_exploit 产）：只 attempt 了 INJ-1
    entries = [{"vulnerability_id": "INJ-1", "status": "exploited", "severity": "critical",
                "impact": "i", "exploitation_steps": ["s"], "proof_of_impact": "p"}]
    md = render_deliverable(AgentName.INJECTION_EXPLOIT, entries, deliverables_path=tmp_path)
    assert md is not None
    assert "## Successfully Exploited" in md and "INJ-1" in md
    assert "## Unprocessed Vulnerabilities" in md and "INJ-9" in md


def test_render_deliverable_vuln_ignores_deliverables_path(tmp_path):
    # vuln renderer 单输入，deliverables_path 多传不影响（向后兼容）
    md = render_deliverable(AgentName.INJECTION_VULN, {}, deliverables_path=tmp_path)
    # render_vuln 对空 data 仍渲染（含 placeholder），不报错
    assert md is not None
```

- [ ] **Step 2: Run — verify FAIL**

`cd packages/core && uv run pytest tests/collectors/test_bridge_exploit.py tests/renderers/test_render_deliverable_exploit.py -q` → FAIL（ImportError: build_exploit_*）。

- [ ] **Step 3: Implement**

**(a) bridge.py 追加 append 工具桥**（generic set_* 桥不动）：

```python
# 追加到 packages/core/src/shannon_core/collectors/bridge.py 末尾
def build_exploit_openai_tools(collector):
    """exploit append collector → 单个 add_exploit openai FunctionTool。

    append 语义：每次调用 append（无 write-once DuplicateError），对齐 TS getAll(): list。
    """
    from agents import FunctionTool
    from shannon_core.models.exploit_verdict_schemas import ExploitVerdictBatch

    # 单条 union schema（ExploitVerdictBatch.verdicts.items = oneOf + discriminator + $defs）
    single_schema = ExploitVerdictBatch.model_json_schema()["properties"]["verdicts"]["items"]

    async def _on_invoke(ctx, input_json: str) -> str:
        try:
            payload = json.loads(input_json) if input_json else {}
        except json.JSONDecodeError:
            payload = {}
        collector.add(payload)
        return f"added exploit {payload.get('vulnerability_id', '')}"

    return [FunctionTool(
        name="add_exploit",
        description="Record one exploitation verdict (call ONCE per vulnerability in your queue). "
                    "status ∈ {exploited, blocked_by_security, out_of_scope_internal, false_positive}.",
        params_json_schema=single_schema,
        on_invoke_tool=_on_invoke,
        strict_json_schema=False,
    )]


def build_exploit_claude_mcp_server(collector, server_name: str = "exploit"):
    """exploit append collector → 单个 add_exploit SdkMcpTool，in-process MCP server。"""
    from claude_agent_sdk import SdkMcpTool, create_sdk_mcp_server
    from shannon_core.models.exploit_verdict_schemas import ExploitVerdictBatch

    single_schema = ExploitVerdictBatch.model_json_schema()["properties"]["verdicts"]["items"]

    async def _handler(args: dict) -> dict:
        args = args or {}
        collector.add(args)
        return {"content": [{"type": "text",
                             "text": f"added exploit {args.get('vulnerability_id', '')}"}]}

    tool = SdkMcpTool(
        name="add_exploit",
        description="Record one exploitation verdict (call ONCE per vulnerability in your queue). "
                    "status ∈ {exploited, blocked_by_security, out_of_scope_internal, false_positive}.",
        input_schema=single_schema,
        handler=_handler,
    )
    return create_sdk_mcp_server(name=server_name, tools=[tool])
```

**(b) collectors/__init__.py make_collector 加 `-exploit` 分支**：

```python
# packages/core/src/shannon_core/collectors/__init__.py
# 在 -vuln 分支后、return None 前追加：
    if isinstance(agent_name, AgentName) and agent_name.value.endswith("-exploit"):
        from shannon_core.collectors.exploit import make_exploit_collector
        return make_exploit_collector()
    return None
```
（docstring 同步更新：加 "Plan 4: 5 个 exploit agent（`<vc>-exploit`）共用 append collector"。）

**(c) renderers/__init__.py render_deliverable 扩签名 + `-exploit` 分支**：

```python
# packages/core/src/shannon_core/renderers/__init__.py
from shannon_core.renderers.pre_recon import render_pre_recon

__all__ = ["render_pre_recon", "render_deliverable"]


def render_deliverable(agent_name, data, deliverables_path=None):
    """按 agent 分发 renderer。

    - Plan 1: pre-recon / Plan 3: 5 vuln agent（``<vc>-vuln``）：data = collector.get_all()（dict bag）。
    - Plan 4: 5 exploit agent（``<vc>-exploit``）：data = collector.get_all()（list[dict]），
      需 deliverables_path 读 ``{vt}_exploitation_queue.json`` 取 valid_ids + id_to_type，
      跑 validate_exploit_verdicts → render_exploit。
    deliverables_path 对 set_* renderer 无意义（默认 None，向后兼容）。
    """
    from shannon_core.models.agents import AgentName

    if agent_name == AgentName.PRE_RECON:
        return render_pre_recon(data)
    if isinstance(agent_name, AgentName) and agent_name.value.endswith("-vuln"):
        vc = agent_name.value.removesuffix("-vuln")
        from shannon_core.renderers.vuln import render_vuln
        return render_vuln(vc, data)
    if isinstance(agent_name, AgentName) and agent_name.value.endswith("-exploit"):
        vc = agent_name.value.removesuffix("-exploit")
        return _render_exploit_deliverable(vc, data, deliverables_path)
    return None


def _render_exploit_deliverable(vc, entries, deliverables_path):
    import json
    from pathlib import Path
    from shannon_core.collectors.exploit import validate_exploit_verdicts
    from shannon_core.renderers.exploit import render_exploit

    valid_ids: set[str] = set()
    id_to_type: dict[str, str] = {}
    if deliverables_path is not None:
        queue_path = Path(deliverables_path) / f"{vc}_exploitation_queue.json"
        if queue_path.exists():
            try:
                from shannon_core.models.queue_schemas import VulnerabilityQueue
                parsed = VulnerabilityQueue.parse_lenient(queue_path.read_text(encoding="utf-8"))
                for v in parsed.queue.vulnerabilities:
                    vid = getattr(v, "ID", None)
                    if vid:
                        valid_ids.add(vid)
                        id_to_type[vid] = getattr(v, "vulnerability_type", vc)
            except (json.JSONDecodeError, OSError):
                pass
    validation = validate_exploit_verdicts(entries or [], valid_ids)
    return render_exploit(vc, validation, id_to_type)
```

**(d) executor.py L169 多传 deliverables**：

```python
# executor.py（原 L169-172）
        if not skip_artifact_postprocess and collector is not None:
            md = render_deliverable(agent_name, collector.get_all(), deliverables)
            if md is not None:
                (deliverables / defn.deliverable_filename).write_text(md, encoding="utf-8")
```

**(e) providers_anthropic.py isinstance 分支**（原 L109-114 块改造）：

```python
            mcp_server = None
            allowed_tools = None
            if collector is not None:
                from shannon_core.collectors.exploit import ExploitCollector
                if isinstance(collector, ExploitCollector):
                    from shannon_core.collectors.bridge import build_exploit_claude_mcp_server
                    mcp_server = build_exploit_claude_mcp_server(collector)
                    allowed_tools = ["add_exploit"]
                else:
                    from shannon_core.collectors.bridge import build_claude_mcp_server
                    mcp_server = build_claude_mcp_server(collector)
                    allowed_tools = collector.tool_names()
```

**(f) providers_openai.py isinstance 分支**（原 L208-212 块改造）：

```python
        extra_tools = None
        if collector is not None:
            from shannon_core.collectors.exploit import ExploitCollector
            if isinstance(collector, ExploitCollector):
                from shannon_core.collectors.bridge import build_exploit_openai_tools
                extra_tools = build_exploit_openai_tools(collector)
            else:
                from shannon_core.collectors.bridge import build_openai_tools
                extra_tools = build_openai_tools(collector)
        agent = self.build_agent(model, output_format, extra_tools=extra_tools)
```

- [ ] **Step 4: Run — verify PASS**

`cd packages/core && uv run pytest tests/collectors/test_bridge_exploit.py tests/renderers/test_render_deliverable_exploit.py -q` → passed。
回归 set_* 路径无漂移：`uv run pytest tests/collectors/ tests/renderers/ -q`（改动子集，应全绿；若遇预存挂起，记 progress ledger）。

- [ ] **Step 5: Commit**

`git add packages/core/src/shannon_core/collectors/bridge.py packages/core/src/shannon_core/collectors/__init__.py packages/core/src/shannon_core/renderers/__init__.py packages/core/src/shannon_core/agents/executor.py packages/core/src/shannon_core/agents/providers_anthropic.py packages/core/src/shannon_core/agents/providers_openai.py packages/core/tests/collectors/test_bridge_exploit.py packages/core/tests/renderers/test_render_deliverable_exploit.py && git commit -m "feat(exploit): append bridge+make_collector/render_deliverable 分支+provider isinstance+executor 传 deliverables"`

---

### Task 4: 5 个 exploit prompt 改 `add_exploit`（4 档 append）

**Files:**
- Modify: `prompts/exploit-injection.txt`、`exploit-xss.txt`、`exploit-auth.txt`、`exploit-ssrf.txt`、`exploit-authz.txt`

**前提核查：** 5 个 prompt 现已是 structured verdicts JSON 通道（`<system_architecture>` 的 "Your Output: structured verdicts — ... JSON object of shape `{"verdicts":[...]}`"）+ 已禁 Write（"Do NOT write a free-text markdown file"）+ 已读 queue（`{{DELIVERABLES_PATH}}/{vt}_exploitation_queue.json`）。本 task 只把 verdict **产出方式**从 "emit JSON" 改为 "call add_exploit per ID"，4 档字段说明保留。

- [ ] **Step 1: 改 5 个 prompt**

每个 `exploit-{class}.txt` 的两处改：

**(1) `<system_architecture>` 的 "Your Output" 段**——把：

```
**Your Output:** structured verdicts — one per vulnerability in your queue. Produce a JSON object of shape `{"verdicts": [ ... ]}` where each element is one of:
- `{"vulnerability_id", "status": "exploited", "severity", "impact", "exploitation_steps": [...], "proof_of_impact"}`
- `{"vulnerability_id", "status": "blocked_by_security", "confidence", "current_blocker", "what_we_tried", "evidence_of_vulnerability", "expected_impact"}`
- `{"vulnerability_id", "status": "out_of_scope_internal", "reason", "evidence"}`
- `{"vulnerability_id", "status": "false_positive", "reason", "evidence"}`

`vulnerability_id` MUST be one of the IDs from your input queue. Do NOT write a free-text markdown file — the system renders evidence from your structured verdicts.
```

改为：

```
**Your Output:** for each vulnerability in your queue, call the `add_exploit` tool ONCE with one of these verdict shapes (the `status` field selects which):
- status="exploited": `vulnerability_id`, `severity`, `impact`, `exploitation_steps` (list), `proof_of_impact`
- status="blocked_by_security": `vulnerability_id`, `confidence`, `current_blocker`, `what_we_tried`, `evidence_of_vulnerability`, `expected_impact`
- status="out_of_scope_internal": `vulnerability_id`, `reason`, `evidence`
- status="false_positive": `vulnerability_id`, `reason`, `evidence`

`vulnerability_id` MUST be one of the IDs from your input queue. Call `add_exploit` once per queue ID — the host renders the exploitation evidence deliverable from your calls. Do NOT write a free-text markdown file; there is no Markdown for you to write yourself.
```

**(2) `<deliverable_instructions>` 的 "emit your structured verdicts"**——把 "You MUST emit your structured verdicts (see **Your Output** above); the system renders the evidence file from them." 改为 "You MUST call the `add_exploit` tool once per queue ID (see **Your Output** above); the host renders the evidence deliverable from your calls."

**保留不动**：queue 读取（`{{DELIVERABLES_PATH}}/{vt}_exploitation_queue.json`）、TodoWrite 指示、severity 排序指示（"Order exploited verdicts by severity"）、4 档字段语义说明（`<methodology_and_domain_expertise>` 内的 verdict 分类解释）。

**per-class 差异**：只有 `{vt}` 占位不同（queue filename `injection_exploitation_queue.json` 等），prompt 主体 5 个一致。

- [ ] **Step 2: 校验 + Commit**

校验插值 + 无残留 `{"verdicts"` JSON shape 指示（保留 `add_exploit`）：
`cd packages/core && uv run pytest tests/prompts/ -q -k "exploit or interpolation" `（若无 exploit 专用插值测试，跑全 prompts 插值子集；断言 5 个 exploit prompt 仍可插值 + 含 `add_exploit` + 不含 `Produce a JSON object of shape`）。

`git add prompts/exploit-injection.txt prompts/exploit-xss.txt prompts/exploit-auth.txt prompts/exploit-ssrf.txt prompts/exploit-authz.txt && git commit -m "feat(prompts): 5 exploit prompt 改 add_exploit(4档 append),删 emit JSON verdicts 指示"`

---

### Task 5: blackbox `ExploitExecutor` 迁移 + GLM 真机冒烟

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py`（迁移接线）
- Modify: `packages/blackbox/tests/test_exploit_evidence_renderer.py`（改 import core 的 VerdictValidation，若 Task 1 未覆盖）

**迁移要点：** blackbox `ExploitExecutor` 当前传 `structured_output_schema=ExploitVerdictBatch` + `skip_artifact_postprocess=True` + L78-101 兜底/validate/render/write_verdicts_json。迁移后 verdicts 由 `add_exploit` 采集、core renderer 渲染，blackbox 这套接线删除/改 false。

- [ ] **Step 1: 改 ExploitExecutor.execute**

`packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py`：
- L62-75 调 `self._executor.execute(...)` 时：**删 `structured_output_schema=ExploitVerdictBatch.model_json_schema()`**、**改 `skip_artifact_postprocess=False`**（让 core renderer 落盘 `{vt}_exploitation_evidence.md`）。保留 `prompt_variables`（含 queue `vulnerability_entries`）、agent_name、deliverables_path 等。
- **删 L77-101**（structured_output 兜底 + validate_exploit_verdicts + ExploitEvidenceRenderer.render + write_verdicts_json + blackbox_dir 写 evidence.md）——这些迁到 core renderer（Task 2-3 已实现：core 读 queue + validate + render）。
- 保留 queue 读取（L40-49）注入 `prompt_variables["vulnerability_entries"]`（agent 需读 queue 决定 attempt 哪些 ID）。
- 删顶部 `ExploitEvidenceRenderer` / `validate_exploit_verdicts` import（不再用）；`ExploitVerdictBatch` import 删（不再传 schema）。
- `return metrics` 保留。

迁移后 execute 大致：

```python
async def execute(self, agent_name, vuln_type, workspace_path, deliverables_path,
                  web_url, config_path=None, api_key=None, pipeline_testing=False,
                  audit_logger=None, tool_audit_logger=None, correlation_context=None):
    queue_path = resolve_track_deliverable(
        deliverables_path, WHITEBOX_SUBDIR, f"{vuln_type}_exploitation_queue.json")
    prompt_variables = {}
    if await async_path_exists(queue_path):
        prompt_variables["vulnerability_entries"] = await async_read_file(queue_path)
    if correlation_context:
        prompt_variables["cross_service_topology"] = json.dumps(
            correlation_context.get("topology", {}), ensure_ascii=False)
        prompt_variables["trust_boundaries"] = json.dumps(
            correlation_context.get("boundaries", []), ensure_ascii=False)
    prompt_variables["browser_session_id"] = get_session_id(agent_name.value)

    # verdicts 改由 add_exploit 工具采集（core ExploitCollector）；core renderer 读 queue
    # + validate + 渲 {vt}_exploitation_evidence.md（skip_artifact_postprocess=False 触发）。
    metrics = await self._executor.execute(
        agent_name=agent_name,
        repo_path=str(deliverables_path),
        web_url=web_url,
        deliverables_path=str(deliverables_path),
        config_path=config_path,
        api_key=api_key,
        pipeline_testing=pipeline_testing,
        prompt_variables=prompt_variables,
        audit_logger=audit_logger,
        tool_audit_logger=tool_audit_logger,
        skip_artifact_postprocess=False,
    )
    return metrics
```

- [ ] **Step 2: 改 blackbox 测试**

`packages/blackbox/tests/test_exploit_evidence_renderer.py`：`VerdictValidation` import 改 `from shannon_core.collectors.exploit import VerdictValidation`（Task 1 已让 blackbox re-export 兼容，但测试直接 import core 更清晰）。`ExploitEvidenceRenderer` import 保留（测的是旧 renderer 死代码——Plan 5 删前仍要绿，确保未误删）。

- [ ] **Step 3: 跑 blackbox exploit 测试子集**

`cd packages/blackbox && uv run pytest tests/test_exploit_evidence_renderer.py tests/test_exploit_verdict_validator.py -q` → PASS（renderer 死代码仍可测 + validator re-export 兼容）。

- [ ] **Step 4: Commit**

`git add packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py packages/blackbox/tests/test_exploit_evidence_renderer.py && git commit -m "feat(blackbox): ExploitExecutor 迁 core append 通道—删 structured_output/兜底/render,skip=False"`

- [ ] **Step 5: GLM 真机冒烟（需 glm-anthropic env + 仓库 + 已有 queue）**

跑一个 exploit agent（如 injection-exploit，前提 injection-vuln 已产 `injection_exploitation_queue.json`），确认：
- `injection_exploitation_evidence.md` 由 **core host 渲染**（5 section：Successfully Exploited / Potential Blocked / Other / Unverified / Unprocessed）
- agent 多次调 `add_exploit`（append，非 write-once）—— 工具审计日志可见多次调用
- Unprocessed section 正确反映 queue 里没 attempt 的 ID
- workflow.log 无 `Missing deliverable: injection_exploitation_evidence.md`
- verdicts 不丢（invite_code_center 回归点）：exploited/blocked 都落对应 section

- [ ] **Step 6: 记 memory**

记录 Plan 4 落地（exploit append collector + 全迁移 blackbox→core + 5-section renderer）到 memory `[[pre-recon-md-deliverable-glm-forget-write]]`，并新建/更新 `[[blackbox-exploit-verdict-drop-fix]]`（verdicts 采集通道从 structured_output 迁到 add_exploit）。

---

## Self-Review

**Spec coverage:** 父 spec §6 Plan 4 裁定注记 → Task 1-5 ✓。
- append collector（非 write-once）：Task 1 ✓
- 独立 `build_exploit_*` 桥：Task 3 ✓
- provider isinstance 分支：Task 3 ✓
- render_deliverable 扩签名读 queue：Task 3 ✓
- 4 档对齐 exploit_verdict_schemas：Task 1/2 ✓
- validator 迁 core：Task 1 ✓
- 5 section renderer（含 Rejected + Unprocessed）：Task 2 ✓
- blackbox ExploitExecutor 迁移（skip=False、删 structured_output/兜底/render）：Task 5 ✓
- 5 prompt 改 add_exploit：Task 4 ✓
- blackbox ExploitEvidenceRenderer 留死代码 Plan 5 删：Task 5 注明 ✓

**Placeholder scan:** Task 4 prompt 改造给了完整 before/after 文案；Task 5 ExploitExecutor 给了迁移后完整 execute；validator 迁移给逐字搬指令。无 TBD。Task 4 校验断言明确（含 `add_exploit` + 不含 `Produce a JSON object of shape`）。

**Type consistency:** `ExploitCollector.add/get_all`（Task 1）→ bridge `build_exploit_*`（Task 3）→ executor `make_collector`（Task 3）→ `render_deliverable`（Task 3，多传 deliverables）→ `render_exploit`（Task 2）签名一致；`validate_exploit_verdicts(raw, valid_ids)->VerdictValidation` 跨 Task 1/3 一致；`render_exploit(vc, validation, id_to_type)` 跨 Task 2/3 一致。

**已知执行期风险：**
- `add_exploit` discriminated union schema 在 GLM/双引擎接受度 → Task 3 bridge 测试（asyncio.run 正确驱动）+ Task 5 probe 验证。schema 已是 discriminated（oneOf + discriminator + $defs，非裸 oneOf），比原 plan 假设的裸 oneOf 更稳。
- Task 3 回归 set_* 路径：providers isinstance 分支须不破坏 CollectorBase 路径（测试覆盖 + executor 端到端）。
- Task 5 blackbox 迁移回归：structured_output 兜底是 invite_code_center bug 修的——迁移后 verdicts 改由 add_exploit 采集 + core validate 兜底，Task 5 真机冒烟验证不丢。
