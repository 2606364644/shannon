# Host-Rendered Deliverables — Plan 4（exploit agent，5 class，append collector）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Prerequisite:** Plan 1（collector 框架 + 双引擎桥 + pre-recon 端到端）+ Plan 3（5 vuln class）+ **a5be2b71（CollectorBase append 语义 + recon set_endpoints）** 已落地。

**Goal:** 5 个 exploit agent（injection/xss/auth/ssrf/authz）调 `add_exploit`（**append** 语义，复用 a5be2b71 的 `CollectorBase` `mode="append"`）结构化工具，core 收集 + `validate_exploit_verdicts`（4 档）+ `render_exploit` 渲染 `{vt}_exploitation_evidence.md`（5 section：Exploited / Blocked / Other / Unverified-rejected / Unprocessed）。**本质=把 exploit 从 blackbox structured-output→`ExploitEvidenceRenderer` 通道迁移到 core append-collector 通道**。

**Architecture:** exploit 产物是 verdict list（`ExploitVerdictBatch.verdicts: list`，对齐 TS `getAll(): AddExploitInput[]`），用 **a5be2b71 已落地的 `CollectorBase` `mode="append"` 机制**——一个 `add_exploit` append section（`SectionSchema(mode="append")`），复用 generic mode-aware `build_openai_tools`/`build_claude_mcp_server` + `make_collector`/`render_deliverable` 现有分发器。**不新建独立 collector 类、不新建独立 bridge 函数、不改 provider**（collector 仍是 `CollectorBase`，provider 无感）。renderer 双输入（entries + queue 的 id_to_type）经 `render_deliverable` 扩 `deliverables_path` 参数读 queue。与 recon 的 `set_endpoints`（a5be2b71）完全同构。

**Tech Stack:** pydantic、pytest、claude-agent-sdk、openai-agents。

**Spec:** `docs/superpowers/specs/2026-07-17-host-rendered-deliverables-design.md`（§6 Plan 4 裁定注记，commit c84cc8c9）。

## Global Constraints

- **append 复用 CollectorBase mode=append（a5be2b71）**：exploit 用 `SectionSchema(tool_name="add_exploit", section_key="verdicts", mode="append", json_schema=<单条 verdict union>)` + `make_exploit_collector(vc) -> CollectorBase`（同 `make_vuln_collector`/`ReconCollector` 模式）。**不新建独立 collector 类、不新建 `build_exploit_*` 桥、不改 provider**——generic `build_openai_tools`/`build_claude_mcp_server`（a5be2b71 已 mode-aware）自动生成 append 工具，`make_collector`/`render_deliverable` 现有分发器加 `-exploit` 分支即可。`get_all()` 返 `{"verdicts": [...]}`（dict，append section value 是 list[dict]）。
- **4 档对齐现有**：verdict status ∈ {`exploited`/`blocked_by_security`/`out_of_scope_internal`/`false_positive`}，**复用 `packages/core/src/shannon_core/models/exploit_verdict_schemas.py` 的 4 个 Verdict 类**——**不新建 entry model**（避免字段漂移）。
- **queue 是 vuln 的，只读不改**：`{vt}_exploitation_queue.json` 由 vuln agent 产。结构 `{"vulnerabilities":[{"ID":"...","vulnerability_type":"...",...}]}`（`parse_lenient`，每 entry 有 `.ID`）。exploit renderer 只读取 `valid_ids`/`id_to_type`，**不写**。
- **单通道**：exploit agent 只产 `{vt}_exploitation_evidence.md`（不产 queue）。迁移后 **blackbox 不再传 `structured_output_schema=ExploitVerdictBatch`**（verdicts 改由 add_exploit 采集）。
- **全迁移保留验证**：`validate_exploit_verdicts`（L0 normalize / L1 schema / L2 queue-ID 防幻觉 / L3 去重）从 blackbox 迁 core；renderer 渲 **5 section**（Exploited/Blocked/Other/Unverified-rejected/Unprocessed），Rejected 与 Unprocessed 正交（rejected=调了 add_exploit 但验证失败；unprocessed=没调）。
- **§1 双轨独立 / §2 双引擎**：renderer 读 queue 是读 LLM 产物（vuln queue），不引 GitNexus 确定性层；generic mode-aware bridge 双引擎对称（a5be2b71 已验证）。
- **TS 对齐 1:1**：`add_exploit` schema（discriminated union on status）、renderer section、prompt 文案移植 TS `exploit-collector.ts`/`exploit-renderer.ts`（TS 字段以 `exploit_verdict_schemas.py` 4 档为准）。
- **诊断暂不移除**：`_enrich_missing_deliverable_error`（executor.py）保留到 Plan 5。blackbox `ExploitEvidenceRenderer`（旧 3-section renderer）迁移后留死代码，**Plan 5 删**。
- **TDD + 测试陷阱**（CLAUDE.md §3）：每 task 先失败测试；只跑改动子集，勿广跑全套（预存挂起/失败）。
- **分支** `feat/fork-py`；每 task 末 commit。

## 现有接口事实（controller 已核查，implementer 直接用）

- **a5be2b71 `collectors/base.py`**：`SectionSchema` 加 `mode: str = "set"`（`"set"` write-once / `"append"` 累积）；`CollectorBase.append_section(tool_name, item)`（累积进 `_appends[section_key]`，不抛 DuplicateCallError）；`set_section` 对 append section 抛 TypeError（反之亦然，误用保护）；`get_all()` 返 dict——set section 是 dict、append section 是 `list[dict]`（空则不含键，与 skipped 同语义）。
- **a5be2b71 `collectors/bridge.py`**：`build_openai_tools`/`build_claude_mcp_server` **按 `schema.mode` 分支**——append 闭包调 `collector.append_section`、返 `"{tool}: recorded (N total)"`、不抛 DuplicateError；set 闭包不变（write-once）。**generic 桥已 mode-aware，exploit append 工具自动生成，无需新桥。**
- **a5be2b71 `collectors/recon.py`**：`set_endpoints` section = `SectionSchema(tool_name="set_endpoints", section_key="endpoints", json_schema=ENDPOINTS, mode="append")`，追加进 `RECON_SECTIONS`；`ReconCollector(CollectorBase)` 无参构造 `super().__init__(RECON_SECTIONS)`。**这是 exploit add_exploit section 的精确模板。**
- **`models/agents.py`**：5 个 exploit 成员存在（`INJECTION_EXPLOIT="injection-exploit"` 等，对称 `-vuln`）；`deliverable_filename` = `{vt}_exploitation_evidence.md`；`prompt_template` = `{vt}-exploit`。
- **`collectors/__init__.py::make_collector(agent_name)`**：当前认 PRE_RECON + `endswith("-vuln")`，对 `-exploit` 返 None。`-vuln` 分支模式：`vc = agent_name.value.removesuffix("-vuln"); from .vuln import make_vuln_collector; return make_vuln_collector(vc)`。
- **`agents/executor.py`**：L112 `collector = make_collector(agent_name)`；L169-172 `if not skip_artifact_postprocess and collector is not None: md = render_deliverable(agent_name, collector.get_all()); write_text(md)`。
- **`agents/runner.py`/`providers_*`**：collector 透传 + provider 调 generic `build_*`——**exploit 用 CollectorBase，provider 无感，不改。**
- **`renderers/__init__.py::render_deliverable(agent_name, data)`**：当前认 PRE_RECON + `-vuln`，对 `-exploit` 返 None。单输入（需扩 `deliverables_path`）。
- **`models/exploit_verdict_schemas.py`**：4 档 discriminated union；`ExploitVerdictBatch.model_json_schema()["properties"]["verdicts"]["items"]` = **单条 union schema**（`oneOf:[4 个 $ref] + discriminator mapping`，`$defs` 含 4 verdict 字段）——这是 add_exploit section 的 `json_schema` 来源。
- **blackbox `agents/exploit_executor.py`**：L40-49 读 queue → `valid_ids={v.ID for v in parsed.queue.vulnerabilities}`；L62-75 调 executor 传 `structured_output_schema=ExploitVerdictBatch` + `skip_artifact_postprocess=True`；L78-101 structured_output 兜底 + validate + render + write_verdicts_json。
- **blackbox `services/exploit_verdict_validator.py`**：`validate_exploit_verdicts(raw: list[dict], valid_ids: set[str]) -> VerdictValidation`。消费者：exploit_executor.py + 2 个 blackbox 测试。
- **`models/queue_schemas.py::VulnerabilityQueue.parse_lenient(content: str) -> LenientParseResult`**（`.queue.vulnerabilities` list，每 entry 有 `.ID`；`.warnings`）。

## File Structure

- Create: `packages/core/src/shannon_core/collectors/exploit.py`（`EXPLOIT_VERDICTS_SECTION`（mode="append"）+ `make_exploit_collector(vc) -> CollectorBase` + 迁入的 `validate_exploit_verdicts`/`VerdictValidation`）
- Create: `packages/core/src/shannon_core/renderers/exploit.py`（`render_exploit` 5 section）
- Modify: `packages/core/src/shannon_core/collectors/__init__.py`（make_collector 加 `-exploit` 分支）
- Modify: `packages/core/src/shannon_core/renderers/__init__.py`（render_deliverable 扩 `deliverables_path=None` + `-exploit` 分支读 queue）
- Migrate: `validate_exploit_verdicts` + `VerdictValidation` 从 blackbox → core（放 `collectors/exploit.py`）；blackbox 改 re-export
- Modify: `packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py`（迁移接线）
- Modify: `packages/blackbox/tests/test_exploit_verdict_validator.py`（改 import core）
- Modify: `prompts/exploit-{injection,xss,auth,ssrf,authz}.txt`
- **不改**：`collectors/bridge.py`（generic mode-aware 已支持）、`agents/providers_*.py`、`agents/runner.py`、`agents/executor.py`（L169 已是 `render_deliverable(agent_name, collector.get_all())`，仅需 render_deliverable 扩签名——executor 调用点多传 `deliverables`）

---

### Task 1: exploit append section + `make_exploit_collector` + validator 迁移到 core

**Files:**
- Create: `packages/core/src/shannon_core/collectors/exploit.py`
- Migrate: `validate_exploit_verdicts` + `VerdictValidation`（从 blackbox `services/exploit_verdict_validator.py` 移入 exploit.py）
- Modify: `packages/blackbox/src/shannon_blackbox/services/exploit_verdict_validator.py`（re-export from core）
- Modify: `packages/blackbox/tests/test_exploit_verdict_validator.py`（import core）
- Test: `packages/core/tests/collectors/test_exploit_collector.py`

**Interfaces:**
- Consumes: `shannon_core.collectors.base.CollectorBase`/`SectionSchema`（a5be2b71）、`shannon_core.models.exploit_verdict_schemas.ExploitVerdictBatch`（取单条 union schema）、`ExploitVerdict`。
- Produces: `EXPLOIT_VERDICTS_SECTION`（`SectionSchema(mode="append")`）、`make_exploit_collector(vc) -> CollectorBase`、`validate_exploit_verdicts(raw, valid_ids)->VerdictValidation`、`VerdictValidation`。

- [ ] **Step 1: Write failing test**

```python
# packages/core/tests/collectors/test_exploit_collector.py
from shannon_core.collectors.base import CollectorBase
from shannon_core.collectors.exploit import (
    EXPLOIT_VERDICTS_SECTION, make_exploit_collector,
    validate_exploit_verdicts, VerdictValidation,
)


def test_exploit_collector_is_append_mode_collectorbase():
    c = make_exploit_collector("injection")
    assert isinstance(c, CollectorBase)  # 复用 CollectorBase，非独立类
    assert EXPLOIT_VERDICTS_SECTION.mode == "append"
    assert EXPLOIT_VERDICTS_SECTION.tool_name == "add_exploit"
    assert EXPLOIT_VERDICTS_SECTION.section_key == "verdicts"
    # 工具名暴露给 provider allowed_tools
    assert c.tool_names() == ["add_exploit"]


def test_append_section_accumulates_into_verdicts_list():
    c = make_exploit_collector("injection")
    c.append_section("add_exploit", {"vulnerability_id": "INJ-1", "status": "exploited",
        "severity": "high", "impact": "i", "exploitation_steps": ["s"], "proof_of_impact": "p"})
    c.append_section("add_exploit", {"vulnerability_id": "INJ-2", "status": "blocked_by_security",
        "confidence": "high", "current_blocker": "b", "what_we_tried": "t",
        "evidence_of_vulnerability": "e", "expected_impact": "ei"})
    data = c.get_all()
    assert list(data.keys()) == ["verdicts"]
    assert len(data["verdicts"]) == 2
    assert data["verdicts"][0]["vulnerability_id"] == "INJ-1"


def test_get_all_empty_when_no_append():
    assert make_exploit_collector("injection").get_all() == {}  # append section 空则不含键


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

`cd packages/core && uv run pytest tests/collectors/test_exploit_collector.py -q` → FAIL（ImportError）。

- [ ] **Step 3: Implement**

创建 `packages/core/src/shannon_core/collectors/exploit.py`：

```python
# packages/core/src/shannon_core/collectors/exploit.py
"""exploit append collector（mode="append" SectionSchema）+ verdict 校验（L0-L3）。

复用 a5be2b71 的 CollectorBase mode="append" 机制（同 recon set_endpoints）：
add_exploit 是 append 工具，多次调累积 verdict 进 get_all()["verdicts"]（list[dict]）。
不新建独立 collector 类、不新建 bridge（generic mode-aware 桥自动生成）。
validate_exploit_verdicts 2026-07-17 从 blackbox 迁入 core（blackbox 改 re-export）。
4 档 verdict 复用 models/exploit_verdict_schemas.py 的 discriminated union。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from pydantic import ValidationError

from shannon_core.collectors.base import CollectorBase, SectionSchema
from shannon_core.models.exploit_verdict_schemas import ExploitVerdict, ExploitVerdictBatch


# ── add_exploit append section（对齐 recon set_endpoints 的 SectionSchema 模式）──
# 单条 verdict union schema（ExploitVerdictBatch.verdicts.items = oneOf + discriminator + $defs）。
_SINGLE_VERDICT_SCHEMA = ExploitVerdictBatch.model_json_schema()["properties"]["verdicts"]["items"]

EXPLOIT_VERDICTS_SECTION = SectionSchema(
    tool_name="add_exploit",
    section_key="verdicts",
    description=(
        "Record one exploitation verdict for a single vulnerability (call ONCE per queue ID; "
        "call multiple times to record all verdicts). status ∈ {exploited, "
        "blocked_by_security, out_of_scope_internal, false_positive} selects the field set. "
        "vulnerability_id MUST be one of the IDs from your input queue. The host renders the "
        "exploitation evidence deliverable from your calls — there is no Markdown to write."
    ),
    json_schema=_SINGLE_VERDICT_SCHEMA,
    mode="append",
)


def make_exploit_collector(vuln_class: str) -> CollectorBase:
    """5 个 exploit agent（``<vc>-exploit``）共用的 append collector。

    单个 add_exploit append section。vc 参数保留对称（make_vuln_collector(vc) 模式），
    当前 schema 跨 class 一致（per-class 只差 queue filename / renderer title）。
    """
    return CollectorBase([EXPLOIT_VERDICTS_SECTION])


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
```

blackbox `packages/blackbox/src/shannon_blackbox/services/exploit_verdict_validator.py` 改 re-export：

```python
# packages/blackbox/src/shannon_blackbox/services/exploit_verdict_validator.py
"""validator 已迁 core（Plan 4）。本文件 re-export 保旧 import 路径兼容，Plan 5 删。"""
from shannon_core.collectors.exploit import (  # noqa: F401
    VerdictValidation,
    validate_exploit_verdicts,
)
```

blackbox `packages/blackbox/tests/test_exploit_verdict_validator.py` import 改 core：

```python
# 旧：from shannon_blackbox.services.exploit_verdict_validator import VerdictValidation, validate_exploit_verdicts
# 新：
from shannon_core.collectors.exploit import VerdictValidation, validate_exploit_verdicts
```

- [ ] **Step 4: Run — verify PASS**

`cd packages/core && uv run pytest tests/collectors/test_exploit_collector.py -q` → 4 passed。
`cd packages/blackbox && uv run pytest tests/test_exploit_verdict_validator.py -q` → 仍 PASS。

- [ ] **Step 5: Commit**

`git add packages/core/src/shannon_core/collectors/exploit.py packages/core/tests/collectors/test_exploit_collector.py packages/blackbox/src/shannon_blackbox/services/exploit_verdict_validator.py packages/blackbox/tests/test_exploit_verdict_validator.py && git commit -m "feat(collectors): exploit add_exploit append section(mode=append)+validate_exploit_verdicts 迁 core(blackbox re-export)"`

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

### Task 3: make_collector `-exploit` 分支 + render_deliverable 扩签名读 queue

**Files:**
- Modify: `packages/core/src/shannon_core/collectors/__init__.py`（make_collector 加 `-exploit` 分支）
- Modify: `packages/core/src/shannon_core/renderers/__init__.py`（render_deliverable 扩 `deliverables_path=None` + `-exploit` 分支读 queue）
- Modify: `packages/core/src/shannon_core/agents/executor.py:170`（render_deliverable 调用点多传 `deliverables`）
- Test: `packages/core/tests/renderers/test_render_deliverable_exploit.py`

**Interfaces:**
- Consumes: `make_exploit_collector`、`render_exploit`、`validate_exploit_verdicts`（Task 1/2）。
- Produces: `render_deliverable(agent_name, data, deliverables_path=None)`、make_collector 对 `-exploit` 返 CollectorBase。

> **不改 bridge/providers/runner**——exploit 用 `mode="append"` 的 CollectorBase，generic mode-aware 桥（a5be2b71）自动生成 add_exploit 工具，provider 调 generic `build_*`，无感。

- [ ] **Step 1: Write failing test**

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
    # collector.get_all() 形态：{"verdicts": [...]}（append section）
    data = {"verdicts": [{"vulnerability_id": "INJ-1", "status": "exploited", "severity": "critical",
                          "impact": "i", "exploitation_steps": ["s"], "proof_of_impact": "p"}]}
    md = render_deliverable(AgentName.INJECTION_EXPLOIT, data, deliverables_path=tmp_path)
    assert md is not None
    assert "## Successfully Exploited" in md and "INJ-1" in md
    assert "## Unprocessed Vulnerabilities" in md and "INJ-9" in md


def test_render_deliverable_exploit_handles_missing_queue(tmp_path):
    # queue 不存在：valid_ids 空 → 所有 verdict 因 L2 进 rejected（id不在queue）
    data = {"verdicts": [{"vulnerability_id": "X-1", "status": "exploited", "severity": "low",
                          "impact": "i", "exploitation_steps": ["s"], "proof_of_impact": "p"}]}
    md = render_deliverable(AgentName.INJECTION_EXPLOIT, data, deliverables_path=tmp_path)
    assert md is not None  # 不崩，X-1 落 Unverified


def test_render_deliverable_vuln_ignores_deliverables_path(tmp_path):
    # vuln renderer 单输入，deliverables_path 多传不影响（向后兼容）
    md = render_deliverable(AgentName.INJECTION_VULN, {}, deliverables_path=tmp_path)
    assert md is not None  # render_vuln 对空 data 渲 placeholder


def test_make_collector_returns_append_collectorbase_for_exploit():
    from shannon_core.collectors import make_collector
    from shannon_core.collectors.base import CollectorBase
    c = make_collector(AgentName.INJECTION_EXPLOIT)
    assert isinstance(c, CollectorBase)
    assert c.tool_names() == ["add_exploit"]
```

- [ ] **Step 2: Run — verify FAIL**

`cd packages/core && uv run pytest tests/renderers/test_render_deliverable_exploit.py -q` → FAIL（render_deliverable 不认 -exploit / 无 deliverables_path 参数）。

- [ ] **Step 3: Implement**

**(a) collectors/__init__.py make_collector 加 `-exploit` 分支**（在 `-vuln` 分支后、`return None` 前）：

```python
    if isinstance(agent_name, AgentName) and agent_name.value.endswith("-exploit"):
        vc = agent_name.value.removesuffix("-exploit")
        from shannon_core.collectors.exploit import make_exploit_collector
        return make_exploit_collector(vc)
    return None
```
（docstring 同步加："Plan 4: 5 个 exploit agent（`<vc>-exploit`）共用 append collector（mode='append'）。"）

**(b) renderers/__init__.py render_deliverable 扩签名 + `-exploit` 分支**：

```python
# packages/core/src/shannon_core/renderers/__init__.py
from shannon_core.renderers.pre_recon import render_pre_recon

__all__ = ["render_pre_recon", "render_deliverable"]


def render_deliverable(agent_name, data, deliverables_path=None):
    """按 agent 分发 renderer。

    - Plan 1: pre-recon / Plan 3: 5 vuln agent（``<vc>-vuln``）：data = collector.get_all()（dict bag，
      set section）。deliverables_path 不用（向后兼容，默认 None）。
    - Plan 4: 5 exploit agent（``<vc>-exploit``）：data = collector.get_all()（含 "verdicts" list，
      append section）。需 deliverables_path 读 ``{vt}_exploitation_queue.json`` 取 valid_ids +
      id_to_type，跑 validate_exploit_verdicts → render_exploit。
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


def _render_exploit_deliverable(vc, data, deliverables_path):
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
    entries = (data or {}).get("verdicts", []) if isinstance(data, dict) else (data or [])
    validation = validate_exploit_verdicts(entries, valid_ids)
    return render_exploit(vc, validation, id_to_type)
```

**(c) executor.py L170 render_deliverable 调用点多传 deliverables**：

```python
# executor.py（原 L169-172）
        if not skip_artifact_postprocess and collector is not None:
            md = render_deliverable(agent_name, collector.get_all(), deliverables)
            if md is not None:
                (deliverables / defn.deliverable_filename).write_text(md, encoding="utf-8")
```

- [ ] **Step 4: Run — verify PASS**

`cd packages/core && uv run pytest tests/renderers/test_render_deliverable_exploit.py tests/renderers/ tests/collectors/ -q` → passed（含 set_* renderer 回归无漂移：render_deliverable 对 vuln/pre_recon 多传 deliverables_path 但不用，向后兼容）。

- [ ] **Step 5: Commit**

`git add packages/core/src/shannon_core/collectors/__init__.py packages/core/src/shannon_core/renderers/__init__.py packages/core/src/shannon_core/agents/executor.py packages/core/tests/renderers/test_render_deliverable_exploit.py && git commit -m "feat(exploit): make_collector -exploit 分支+render_deliverable 扩 deliverables_path 读 queue+executor 传 deliverables"`

---

### Task 4: 5 个 exploit prompt 改 `add_exploit`（4 档 append）

**Files:**
- Modify: `prompts/exploit-injection.txt`、`exploit-xss.txt`、`exploit-auth.txt`、`exploit-ssrf.txt`、`exploit-authz.txt`

**前提核查：** 5 个 prompt 现已是 structured verdicts JSON 通道（`<system_architecture>` 的 "Your Output: structured verdicts — ... JSON object of shape `{"verdicts":[...]}`"）+ 已禁 Write（"Do NOT write a free-text markdown file"）+ 已读 queue。本 task 只把 verdict **产出方式**从 "emit JSON" 改为 "call add_exploit per ID"，4 档字段说明保留。

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

**保留不动**：queue 读取（`{{DELIVERABLES_PATH}}/{vt}_exploitation_queue.json`）、TodoWrite 指示、severity 排序指示、4 档字段语义说明。

**per-class 差异**：只有 `{vt}` 占位不同（queue filename `injection_exploitation_queue.json` 等）。

- [ ] **Step 2: 校验 + Commit**

校验插值 + 无残留 `Produce a JSON object of shape`：
`cd packages/core && uv run pytest tests/prompts/ -q`（断言 5 个 exploit prompt 仍可插值 + 含 `add_exploit` + 不含 `Produce a JSON object of shape`；若无 exploit 专用插值测试，跑全 prompts 插值子集 + grep 校验）。

`git add prompts/exploit-injection.txt prompts/exploit-xss.txt prompts/exploit-auth.txt prompts/exploit-ssrf.txt prompts/exploit-authz.txt && git commit -m "feat(prompts): 5 exploit prompt 改 add_exploit(4档 append),删 emit JSON verdicts 指示"`

---

### Task 5: blackbox `ExploitExecutor` 迁移 + GLM 真机冒烟

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py`（迁移接线）
- Modify: `packages/blackbox/tests/test_exploit_evidence_renderer.py`（VerdictValidation import 改 core，若 Task 1 未覆盖）

**迁移要点：** blackbox `ExploitExecutor` 当前传 `structured_output_schema=ExploitVerdictBatch` + `skip_artifact_postprocess=True` + L78-101 兜底/validate/render/write_verdicts_json。迁移后 verdicts 由 `add_exploit` 采集、core renderer 渲染，blackbox 这套接线删除/改 false。

- [ ] **Step 1: 改 ExploitExecutor.execute**

`packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py`：
- L62-75 调 `self._executor.execute(...)` 时：**删 `structured_output_schema=ExploitVerdictBatch.model_json_schema()`**、**改 `skip_artifact_postprocess=False`**（让 core renderer 读 queue + validate + 渲 `{vt}_exploitation_evidence.md`）。保留 `prompt_variables`（含 queue `vulnerability_entries`）、agent_name、deliverables_path 等。
- **删 L77-101**（structured_output 兜底 + validate_exploit_verdicts + ExploitEvidenceRenderer.render + write_verdicts_json + blackbox_dir 写 evidence.md）。
- 保留 queue 读取（L40-49）注入 `prompt_variables["vulnerability_entries"]`。
- 删顶部 `ExploitEvidenceRenderer` / `validate_exploit_verdicts` / `ExploitVerdictBatch` import。
- `return metrics` 保留。

迁移后 execute：

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

    # verdicts 改由 add_exploit 工具采集（core CollectorBase mode=append）；core renderer
    # 读 queue + validate + 渲 {vt}_exploitation_evidence.md（skip_artifact_postprocess=False 触发）。
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

`packages/blackbox/tests/test_exploit_evidence_renderer.py`：`VerdictValidation` import 改 `from shannon_core.collectors.exploit import VerdictValidation`。`ExploitEvidenceRenderer` import 保留（测旧 renderer 死代码，Plan 5 删前仍绿）。

- [ ] **Step 3: 跑 blackbox exploit 测试子集**

`cd packages/blackbox && uv run pytest tests/test_exploit_evidence_renderer.py tests/test_exploit_verdict_validator.py -q` → PASS。

- [ ] **Step 4: Commit**

`git add packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py packages/blackbox/tests/test_exploit_evidence_renderer.py && git commit -m "feat(blackbox): ExploitExecutor 迁 core append 通道—删 structured_output/兜底/render,skip=False"`

- [ ] **Step 5: GLM 真机冒烟（需 glm-anthropic env + 仓库 + 已有 queue）**

跑一个 exploit agent（如 injection-exploit，前提 injection-vuln 已产 `injection_exploitation_queue.json`），确认：
- `injection_exploitation_evidence.md` 由 **core host 渲染**（5 section：Successfully Exploited / Potential Blocked / Other / Unverified / Unprocessed）
- agent 多次调 `add_exploit`（append，工具返 "add_exploit: recorded (N total)"）
- Unprocessed section 正确反映 queue 里没 attempt 的 ID
- workflow.log 无 `Missing deliverable: injection_exploitation_evidence.md`
- verdicts 不丢（invite_code_center 回归点）

- [ ] **Step 6: 记 memory**

记录 Plan 4 落地（exploit 复用 a5be2b71 CollectorBase mode=append + 全迁移 blackbox→core + 5-section renderer）到 memory `[[pre-recon-md-deliverable-glm-forget-write]]`，更新 `[[blackbox-exploit-verdict-drop-fix]]`。

---

## Self-Review

**Spec coverage:** 父 spec §6 Plan 4 裁定注记 → Task 1-5 ✓。
- append（非 write-once set_*）：Task 1（复用 a5be2b71 mode=append）✓
- 复用 generic mode-aware bridge：Task 1（EXPLOIT_VERDICTS_SECTION mode=append，generic 桥自动生成）✓ —— **不改 bridge/providers**
- make_collector/render_deliverable `-exploit` 分支：Task 3 ✓
- 4 档对齐 exploit_verdict_schemas：Task 1/2 ✓
- validator 迁 core：Task 1 ✓
- 5 section renderer（Rejected + Unprocessed 正交）：Task 2 ✓
- blackbox ExploitExecutor 迁移（skip=False、删 structured_output/兜底/render）：Task 5 ✓
- 5 prompt 改 add_exploit：Task 4 ✓

**Placeholder scan:** Task 4 prompt 给完整 before/after；Task 5 给迁移后完整 execute；validator 逐字搬。无 TBD。

**Type consistency:** `EXPLOIT_VERDICTS_SECTION`(mode=append) → `make_exploit_collector(vc)->CollectorBase`（Task 1）→ `make_collector -exploit` 分支（Task 3）→ `get_all()["verdicts"]` list → `render_deliverable` exploit 分支取 `data["verdicts"]`（Task 3）→ `validate_exploit_verdicts(entries, valid_ids)->VerdictValidation` → `render_exploit(vc, validation, id_to_type)`（Task 2/3）签名一致。

**a5be2b71 对齐核验：**
- exploit add_exploit section 与 recon set_endpoints 同构（`SectionSchema(mode="append")` + `_obj`/json_schema + tool_name/section_key）✓
- 不碰 bridge.py（a5be2b71 已 mode-aware，generic `build_openai_tools`/`build_claude_mcp_server` 自动给 append section 生成 append 闭包）✓
- 不碰 providers_anthropic.py / providers_openai.py（collector 是 CollectorBase，provider 调 generic build_* 无感）✓
- `get_all()` exploit 形态 = `{"verdicts": [...]}`（append section 累积成 list value，空则不含键）✓

**已知执行期风险：**
- `add_exploit` discriminated union schema（oneOf + discriminator + $defs）在 GLM/双引擎接受度 → Task 5 probe 验证。schema 已是 discriminated（非裸 oneOf），比裸 oneOf 稳。
- a5be2b71 回归：exploit 用 mode=append 不破坏 set_* agent（pre_recon/vuln）——Task 1/3 测试覆盖 append 路径 + Task 3 回归 set_* renderer。
- Task 5 blackbox 迁移回归：verdicts 改由 add_exploit 采集 + core validate 兜底，真机冒烟验证不丢（invite_code_center 回归点）。
