# LLM 轨弱项修复 Plan 2（双轨 attack chain）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新建双轨多步组合 attack chain（stored XSS / IDOR 链 / 跨服务链），vuln 之后产，喂报告。LLM 轨 Agent（创意驱动）+ GitNexus 轨确定性组装（证据驱动）差异化，专用合并函数 OR。

**Architecture:** 新增 `ATTACK_CHAIN` agent（LLM 轨，`attack-chain.txt` prompt，复用 `run_agent` 执行）产 `attack_chains_llm_queue.json`；新增 `attack_chain_assembler.py`（GitNexus 轨，从 `{vt}_gitnexus_queue.json` 的 findings 跨端点关联）产 `attack_chains_gitnexus_queue.json`；新增 `merge_attack_chains`（专用合并，endpoint-sequence 去重）→ `attack_chains.json`。删当前 dead-end 的 `run_attack_chain_assembly`。

**Tech Stack:** Python（新 agent + assembler + merger + activity + workflow）、prompt .txt（attack-chain.txt）、`AgentExecutor` / `run_claude_prompt`、pytest。

## Global Constraints

- **守 CLAUDE.md §1 双轨铁律**：LLM 轨 attack-chain agent 数据源只能是 LLM 轨自产（`recon_deliverable.md` + `{vt}_exploitation_queue.json` + 代码 grep），**绝不引 GitNexus 确定性层产物**（`parameter_graph.json` / `SinkCallSite`）。GitNexus 轨 assembler 读 `{vt}_gitnexus_queue.json`（确定性层自己的合法产出，与 `gitnexus_queue.json` 同性质），**只经合并器 OR 进 attack_chains.json，绝不反向注入 LLM 轨 prompt**。
- **白盒纯静态为主用例**：GitNexus 经常超时/不可用（CLAUDE.md §3），GitNexus 轨 assembler 必须降级为空、LLM 轨独立兜底——双轨意义所在。
- **attack chain 在 vuln 后产**（吃 vuln 双轨 queue 的 confirmed 单步链组装），**不注入 vuln prompt**（vuln 已跑完）。
- **frequent commits + TDD**。
- **依赖 Plan 1**：本 plan 假定 Plan 1 的 recon-static 对账已落地（attack chain assembler 受益于更全的 recon，但非硬依赖——assembler 主要读 vuln queue）。

---

## File Structure

| 文件 | 责任 | 改动 |
|---|---|---|
| `packages/core/src/shannon_core/models/agents.py` | AgentName 枚举 + AGENTS dict | 加 `ATTACK_CHAIN`（Task 1） |
| `prompts/attack-chain.txt` | LLM 轨 attack chain Agent 方法论（新建） | Task 2 |
| `packages/core/src/shannon_core/code_index/attack_chain_assembler.py` | GitNexus 轨确定性组装（新建） | Task 3 |
| `packages/core/src/shannon_core/code_index/dual_track_merger.py` | 双轨合并 | 加 `merge_attack_chains`（Task 4） |
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | activity | 加 `run_attack_chain_llm_agent` + `run_attack_chain_assembly_v2`，删旧 `run_attack_chain_assembly`（Task 5-6） |
| `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | workflow 编排 | vuln 后调双轨 attack chain（Task 5） |
| `packages/whitebox/src/shannon_whitebox/worker.py` | activity 注册 | 注册新 activity、删旧（Task 5-6） |
| `packages/core/tests/code_index/test_attack_chain_assembler.py` | assembler 单测（新建） | Task 3 |
| `packages/core/tests/code_index/test_merge_attack_chains.py` | 合并单测（新建） | Task 4 |
| `packages/core/tests/prompts/test_attack_chain_decoupling.py` | 解耦 + 内容断言（新建） | Task 2, 7 |

---

## Task 1: 新增 `ATTACK_CHAIN` agent 定义

**Files:**
- Modify: `packages/core/src/shannon_core/models/agents.py`

**Interfaces:**
- Produces: `AgentName.ATTACK_CHAIN` + AGENTS dict defn + AGENT_PHASE_MAP 映射，供 Task 5 的 `run_attack_chain_llm_agent` 复用 `run_agent` 执行。

**Why:** attack-chain agent 是 vuln-style agent（复用 `run_agent` activity，无需新 activity——`run_vuln_agent` 即 `return await run_agent(input)` 模式，`activities.py:232-233`）。只需在 agents.py 注册 defn，workflow 经 `ActivityInput(agent_name="attack-chain")` 触发。

- [ ] **Step 1: 写失败测试**

```python
# packages/core/tests/models/test_attack_chain_agent.py
from shannon_core.models.agents import AgentName, AGENTS, AGENT_PHASE_MAP


def test_attack_chain_agent_registered():
    assert hasattr(AgentName, "ATTACK_CHAIN")
    defn = AGENTS[AgentName.ATTACK_CHAIN]
    assert defn.prompt_template == "attack-chain"
    # 依赖 vuln 产出（attack chain 在 vuln 后跑，吃 vuln queue）
    prereq_names = {p.name for p in defn.prerequisites}
    assert "INJECTION_VULN" in prereq_names or "XSS_VULN" in prereq_names


def test_attack_chain_phase_mapped():
    assert AgentName.ATTACK_CHAIN in AGENT_PHASE_MAP or "attack-chain" in str(AGENT_PHASE_MAP.values())
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/models/test_attack_chain_agent.py -v`
Expected: FAIL — `AttributeError: ATTACK_CHAIN`。

- [ ] **Step 3: 实现——agents.py 注册**

在 `AgentName` 枚举（`agents.py:8-24`）末尾加：

```python
    ATTACK_CHAIN = "attack-chain"
```

在 `AGENTS` dict 加 defn（参照 injection defn `agents.py:51-57`，放在 REPORT 之前）：

```python
    AgentDefinition(
        name=AgentName.ATTACK_CHAIN,
        display_name="Attack Chain Analysis",
        prerequisites=[AgentName.INJECTION_VULN, AgentName.XSS_VULN,
                       AgentName.SSRF_VULN, AgentName.AUTHZ_VULN],
        prompt_template="attack-chain",
        deliverable_filename=None,   # 产 queue（attack_chains_llm_queue.json），不产 md
        model_tier="medium",
    ),
```

在 `AGENT_PHASE_MAP`（`agents.py:164-181`）加：

```python
    AgentName.ATTACK_CHAIN: "attack-chain",
```

> 注：`BROWSER_SESSION_MAPPING`（`agents.py:157`）由 `enumerate(AgentName, ...)` 自动生成，加 enum 即自动。`_vuln_max_turns` / `_vuln_output_schema`（activities.py）需确认是否覆盖 ATTACK_CHAIN——见 Task 5 Step 3。

- [ ] **Step 4: 跑测试验证通过**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/models/test_attack_chain_agent.py -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/models/agents.py packages/core/tests/models/test_attack_chain_agent.py
git commit -m "feat(core): 注册 ATTACK_CHAIN agent（attack-chain prompt_template）"
```

---

## Task 2: 新增 `attack-chain.txt` prompt（LLM 轨 Agent 方法论）

**Files:**
- Create: `prompts/attack-chain.txt`
- Test: `packages/core/tests/prompts/test_attack_chain_decoupling.py`（新建）

**Interfaces:**
- Produces: LLM 轨 attack chain agent 的方法论 prompt，产结构化多步链。

**Why:** 多步组合链（stored XSS / IDOR 链 / 跨服务链）是单 vuln 类链不覆盖的维度。LLM 轨 Agent 创意驱动（recon 启发 + grep 推断），与 GitNexus 轨证据驱动差异化。守铁律：数据源 LLM 轨自产。

- [ ] **Step 1: 写失败测试**

```python
# packages/core/tests/prompts/test_attack_chain_decoupling.py
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"


def test_attack_chain_prompt_exists():
    content = (PROMPTS_DIR / "attack-chain.txt").read_text("utf-8")
    assert "attack chain" in content.lower() or "攻击链" in content
    # 多步链类型
    assert "stored XSS" in content or "storage" in content.lower()
    assert "IDOR" in content or "privilege" in content.lower()


def test_attack_chain_prompt_reads_llm_track_sources():
    """守铁律：prompt 指示读 LLM 轨产物（recon + exploitation_queue），不引确定性层。"""
    content = (PROMPTS_DIR / "attack-chain.txt").read_text("utf-8")
    assert "recon_deliverable.md" in content or "exploitation_queue" in content
    for tok in ("parameter_graph", "SinkCallSite", "static_dataflow_hints", "gitnexus_queue"):
        assert tok not in content, f"attack-chain.txt 引确定性 token: {tok}"


def test_attack_chain_prompt_decoupled_global():
    """解耦测试 rglob 覆盖：无 FORBIDDEN token。"""
    content = (PROMPTS_DIR / "attack-chain.txt").read_text("utf-8")
    for tok in ("parameter_graph", "SinkCallSite", "static_dataflow_hints"):
        assert tok not in content
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/prompts/test_attack_chain_decoupling.py -v`
Expected: FAIL — 文件不存在。

- [ ] **Step 3: 实现——新建 attack-chain.txt**

```text
<role>
You are an Attack Chain Analysis Agent. Your job is to discover MULTI-STEP attack
chains — sequences of endpoints/vulnerabilities that combine into a complete exploit
scenario that single-vuln analysis misses. You run AFTER the per-class vuln agents
(injection/xss/ssrf/authz) have produced their findings, and you synthesize across them.
</role>

<objective>
Identify multi-step combination chains:
- **Stored XSS chains**: an input endpoint writes user data to storage (DB/file) WITHOUT
  sufficient sanitization, and a DIFFERENT endpoint reads that stored data and renders it
  in HTML without encoding. input → storage → retrieval → render.
- **IDOR chains**: a sequence of object-id-parameterized endpoints where ownership
  validation is missing or bypassable at one step, enabling horizontal escalation across
  the chain (A→B→C).
- **Cross-service / cross-context chains**: data flows from one service/context to another
  where trust assumptions differ.
- **Business-logic chains**: multi-step workflows (checkout, password reset, onboarding)
  where a later step fails to verify prior state.
</objective>

@include(shared/_target.txt)
@include(shared/_code-path-rules.txt)

<context>
Recon deliverable: `{{DELIVERABLES_PATH}}/recon_deliverable.md`
  - Section 4 (API Endpoint Inventory) + 4.2 (Endpoint Security Context)
  - Section 5 (Input Vectors / Parameter Completeness)
  - Section 8 (Authorization Vulnerability Candidates)

Per-class vuln findings (confirmed single source→sink chains from prior agents):
  - `{{DELIVERABLES_PATH}}/injection_exploitation_queue.json`
  - `{{DELIVERABLES_PATH}}/xss_exploitation_queue.json`
  - `{{DELIVERABLES_PATH}}/ssrf_exploitation_queue.json`
  - `{{DELIVERABLES_PATH}}/authz_exploitation_queue.json`
  (Read those that exist; skip missing ones silently.)
</context>

<methodology>
1. Load the recon deliverable and the per-class exploitation queues. These are your
   STARTING POINT, not a complete picture — extend with your own grep.

2. Launch parallel Task Agents (delegate via the Task Agent tool) to trace each chain type:
   - **Stored XSS tracer**: find input endpoints writing user data to storage (grep ORM
     save/insert/update calls reachable from a route), then find render endpoints reading
     that storage (grep template/render calls). Correlate by the storage model/collection.
   - **IDOR chain tracer**: from Section 8.1 candidates, find sequences where one endpoint
     mutates state referenced by object-id and a subsequent endpoint reads it without
     re-validating ownership.
   - **Cross-service tracer**: grep inter-service calls (HTTP clients, RPC, message queue
     producers) where user-controlled data crosses a service boundary.

3. For each candidate chain, record:
   - The ordered steps (endpoint METHOD /path + file:line + phase)
   - The vuln classes combined (e.g. injection + xss)
   - The linking data store / object / parameter
   - Confidence: confirmed (all steps verified in code) / probable / theoretical
</methodology>

<output>
Produce a JSON object (write to `{{DELIVERABLES_PATH}}/attack_chains_llm_queue.json`):
{
  "chains": [
    {
      "id": "llm-chain-1",
      "name": "Stored XSS: POST /api/profile → GET /api/profile/:id render",
      "description": "Profile bio stored unescaped, rendered on view endpoint",
      "vuln_type": "xss",
      "severity": "high",
      "confidence": "probable",
      "steps": [
        {"order": 1, "phase": "input", "endpoint": "POST /api/profile", "method": "POST",
         "description": "bio field written to profiles table (profile_controller.js:42)"},
        {"order": 2, "phase": "storage", "endpoint": "DB profiles.bio", "method": "-",
         "description": "stored without encoding"},
        {"order": 3, "phase": "retrieval", "endpoint": "GET /api/profile/:id", "method": "GET",
         "description": "fetches bio (profile_controller.js:88)"},
        {"order": 4, "phase": "render", "endpoint": "GET /api/profile/:id", "method": "GET",
         "description": "rendered into HTML without encoding (profile_view.ejs:12)"}
      ]
    }
  ]
}
Only emit chains with at least 2 steps spanning 2+ endpoints/phases. Omit single-point findings.
</output>

<critical>
- Source ONLY from recon_deliverable.md + exploitation_queue.json + your own grep.
  Do NOT read parameter_graph.json / SinkCallSite / any GitNexus-track artifact.
- Every step must have a file:line code anchor.
- Distinguish confirmed (code-verified all steps) vs probable vs theoretical in confidence.
</critical>
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/prompts/test_attack_chain_decoupling.py -v`
Expected: PASS（3 用例）。

- [ ] **Step 5: 跑解耦测试 rglob（确认全局解耦测试覆盖新文件）**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py -v`
Expected: PASS（attack-chain.txt 无 FORBIDDEN token）。

- [ ] **Step 6: Commit**

```bash
cd /root/shannon-py
git add prompts/attack-chain.txt packages/core/tests/prompts/test_attack_chain_decoupling.py
git commit -m "feat(prompt): 新增 attack-chain.txt（LLM 轨多步组合链 Agent 方法论）"
```

---

## Task 3: 新增 `attack_chain_assembler.py`（GitNexus 轨确定性组装）

**Files:**
- Create: `packages/core/src/shannon_core/code_index/attack_chain_assembler.py`
- Test: `packages/core/tests/code_index/test_attack_chain_assembler.py`（新建）

**Interfaces:**
- Consumes: `{vt}_gitnexus_queue.json`（已判定 findings，含 path/source/sink/evidence_chain）。
- Produces: `assemble_attack_chains(gitnexus_findings_by_class: dict[str, list], logger) -> list[AttackChain]`。

**Why:** GitNexus 轨证据驱动——从已判定的单步链（findings）确定性跨端点关联，产有据可溯的多步链。与 LLM 轨创意驱动差异化。`CandidateChain` 不含 endpoint path（只有 `entry_point_id`），故读已判定 findings（含 path/source/sink）而非原始 CandidateChain。

- [ ] **Step 1: 写失败测试**

```python
# packages/core/tests/code_index/test_attack_chain_assembler.py
import logging
from shannon_core.code_index.attack_chain_assembler import assemble_attack_chains


def _finding(vt, source, sink, path, evidence="src→sink"):
    return {
        "vulnerability_type": vt,
        "source": source,
        "sink_call": sink,
        "path": path,
        "evidence_chain": evidence,
        "verdict": "vulnerable",
        "externally_exploitable": True,
    }


def test_assemble_stored_xss_chain_from_injection_plus_xss():
    """injection 写入 + xss 渲染 = stored XSS 链。"""
    findings = {
        "injection": [_finding("injection", "POST /api/profile.bio", "DB insert profiles",
                               "profile_ctl.js:42 → db.insert")],
        "xss": [_finding("xss", "DB profiles.bio", "GET /api/profile/:id render",
                         "profile_ctl.js:88 → render")],
        "ssrf": [],
        "authz": [],
    }
    chains = assemble_attack_chains(findings, logging.getLogger(__name__))
    assert len(chains) >= 1
    stored = [c for c in chains if c["vuln_type"] == "xss" or "stored" in c["name"].lower()]
    assert len(stored) >= 1
    assert len(stored[0]["steps"]) >= 2  # 多步


def test_assemble_returns_empty_when_gitnexus_unavailable():
    """GitNexus 不可用（无 findings）→ 空链（降级，LLM 轨兜底）。"""
    chains = assemble_attack_chains({}, logging.getLogger(__name__))
    assert chains == []


def test_assemble_returns_empty_when_no_cross_endpoint_link():
    """单端点 findings（无跨端点关联）→ 不组多步链。"""
    findings = {
        "injection": [_finding("injection", "GET /api/x?q", "SQL exec", "x.js:1→sql")],
        "xss": [],
        "ssrf": [],
        "authz": [],
    }
    chains = assemble_attack_chains(findings, logging.getLogger(__name__))
    assert chains == []
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_attack_chain_assembler.py -v`
Expected: FAIL — `ModuleNotFoundError`。

- [ ] **Step 3: 实现——新建 assembler**

```python
# packages/core/src/shannon_core/code_index/attack_chain_assembler.py
"""GitNexus-track attack chain assembler.

Deterministically combines per-class GitNexus findings (already-judged single
source→sink chains in {vt}_gitnexus_queue.json) into multi-step attack chains by
cross-endpoint correlation. Evidence-driven (every step has a GitNexus finding backing it),
complementing the LLM-track's creative inference.

Degradation: if GitNexus findings are absent (GitNexus unavailable / timed out —
CLAUDE.md §3), returns [] — LLM track covers alone.

NOTE: reads GitNexus-track's OWN output (gitnexus_queue findings), NOT feeding it back
into LLM-track prompts (CLAUDE.md §1 ironclad rule). Output only enters attack_chains.json
via the merger.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _endpoint_of(finding: dict) -> str:
    """Best-effort extract the endpoint string from a finding."""
    return (
        finding.get("source_endpoint")
        or finding.get("endpoint")
        or finding.get("source")
        or finding.get("path")
        or ""
    )


def _link_key(finding: dict, *, sink_side: bool) -> str:
    """A storage/model token to join on. Heuristic: sink of a write == source of a read."""
    text = (finding.get("sink_call") if sink_side else finding.get("source", "")) or ""
    text = (text or "").lower()
    # crude storage token extraction (db insert/update profiles → "profiles")
    for marker in ("profiles", "users", "orders", "comments", "posts", "messages"):
        if marker in text:
            return marker
    return text


def assemble_attack_chains(
    gitnexus_findings_by_class: dict[str, list[dict]],
    logger: logging.Logger = logging.getLogger(__name__),
) -> list[dict]:
    """Assemble multi-step chains from per-class GitNexus findings.

    Args:
        gitnexus_findings_by_class: {"injection": [...], "xss": [...], "ssrf": [...],
            "authz": [...]} — each finding is a dict from {vt}_gitnexus_queue.json.

    Returns:
        list of AttackChain dicts (id/name/steps/vuln_type/severity/confidence).
        Empty if no cross-endpoint links found or GitNexus unavailable.
    """
    if not gitnexus_findings_by_class or not any(
        gitnexus_findings_by_class.get(c) for c in ("injection", "xss", "ssrf", "authz")
    ):
        logger.info("attack_chain_assembler: no GitNexus findings, returning [] (LLM track covers)")
        return []

    chains: list[dict] = []

    # Stored XSS: injection write (sink=storage) + xss render (source=storage)
    inj_writes = {
        _link_key(f, sink_side=True): f for f in gitnexus_findings_by_class.get("injection", [])
        if _link_key(f, sink_side=True)
    }
    for xf in gitnexus_findings_by_class.get("xss", []):
        join = _link_key(xf, sink_side=False)
        write = inj_writes.get(join)
        if write and _endpoint_of(write) and _endpoint_of(xf) and _endpoint_of(write) != _endpoint_of(xf):
            chains.append({
                "id": f"gn-stored-xss-{len(chains)+1}",
                "name": f"Stored XSS via {join}: {_endpoint_of(write)} → {_endpoint_of(xf)}",
                "description": f"User input written to {join} (injection) and rendered unescaped (xss).",
                "vuln_type": "xss",
                "severity": "high",
                "confidence": "confirmed",
                "steps": [
                    {"order": 1, "phase": "input", "endpoint": _endpoint_of(write),
                     "method": "-", "description": f"write to {join}: {write.get('evidence_chain','')}"},
                    {"order": 2, "phase": "storage", "endpoint": join, "method": "-",
                     "description": "stored data"},
                    {"order": 3, "phase": "render", "endpoint": _endpoint_of(xf),
                     "method": "-", "description": f"rendered: {xf.get('evidence_chain','')}"},
                ],
            })

    # IDOR chains: authz candidates with object-id params (single-step representation
    # of a chain entry; full A→B→C sequencing needs data-flow which GitNexus findings
    # carry in evidence_chain — left as probable unless multiple authz findings share an object)
    authz = gitnexus_findings_by_class.get("authz", [])
    if len(authz) >= 2:
        chains.append({
            "id": f"gn-idor-chain-{len(chains)+1}",
            "name": f"IDOR chain ({len(authz)} object-id endpoints lacking ownership)",
            "description": "Multiple object-id-parameterized endpoints missing ownership validation.",
            "vuln_type": "authz",
            "severity": "high",
            "confidence": "probable",
            "steps": [
                {"order": i+1, "phase": "authorization", "endpoint": _endpoint_of(f),
                 "method": "-", "description": f"missing ownership: {f.get('evidence_chain','')}"}
                for i, f in enumerate(authz[:4])
            ],
        })

    logger.info("attack_chain_assembler: built %d chain(s) from GitNexus findings", len(chains))
    return chains
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_attack_chain_assembler.py -v`
Expected: PASS（3 用例）。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/code_index/attack_chain_assembler.py packages/core/tests/code_index/test_attack_chain_assembler.py
git commit -m "feat(core): attack_chain_assembler GitNexus 轨确定性组装（跨端点关联）"
```

---

## Task 4: 新增 `merge_attack_chains`（专用合并函数）

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/dual_track_merger.py`
- Test: `packages/core/tests/code_index/test_merge_attack_chains.py`（新建）

**Interfaces:**
- Consumes: Task 3 的 `assemble_attack_chains` 输出（dict 列表）+ LLM 轨的 `attack_chains_llm_queue.json`。
- Produces: `merge_attack_chains(llm_chains: list[dict], gitnexus_chains: list[dict]) -> list[dict]`，`merge_source` 三态。

**Why:** attack-chain schema（steps 列表）与 `Vulnerability` 不同，`merge_dual_track_queues` 的 `_finding_key`（vuln_type+loc+sink）不适用。需按 endpoint-sequence 去重，复用 `merge_source` 三态 + verdict OR 逻辑。

- [ ] **Step 1: 写失败测试**

```python
# packages/core/tests/code_index/test_merge_attack_chains.py
from shannon_core.code_index.dual_track_merger import merge_attack_chains


def _chain(name, endpoints, source="llm", confidence="probable"):
    return {
        "name": name,
        "vuln_type": "xss",
        "confidence": confidence,
        "steps": [{"order": i+1, "endpoint": e, "phase": "x", "method": "-", "description": ""}
                  for i, e in enumerate(endpoints)],
        "_source": source,
    }


def test_merge_or_dedup_by_endpoint_sequence():
    """两轨同一 endpoint 序列 → dedup，merge_source=both。"""
    llm = [_chain("llm-xss", ["POST /a", "GET /b"])]
    gn = [_chain("gn-xss", ["POST /a", "GET /b"], source="gitnexus", confidence="confirmed")]
    merged = merge_attack_chains(llm, gn)
    assert len(merged) == 1
    assert merged[0]["merge_source"] == "both"


def test_merge_keeps_disjoint_chains():
    """不重叠的链 → 都保留，各自 llm-only / gitnexus-only。"""
    llm = [_chain("llm-1", ["POST /a", "GET /b"])]
    gn = [_chain("gn-2", ["POST /c", "GET /d"], source="gitnexus")]
    merged = merge_attack_chains(llm, gn)
    assert len(merged) == 2
    sources = {c["merge_source"] for c in merged}
    assert sources == {"llm-only", "gitnexus-only"}


def test_merge_gitnexus_empty_when_unavailable():
    """GitNexus 空 → 全部 llm-only。"""
    llm = [_chain("llm-1", ["POST /a", "GET /b"])]
    merged = merge_attack_chains(llm, [])
    assert len(merged) == 1
    assert merged[0]["merge_source"] == "llm-only"
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_merge_attack_chains.py -v`
Expected: FAIL — `ImportError: cannot import name 'merge_attack_chains'`。

- [ ] **Step 3: 实现——加 merge_attack_chains**

在 `dual_track_merger.py` 末尾加：

```python
def _chain_endpoint_sequence(chain: dict) -> tuple:
    """Dedup key: ordered tuple of step endpoints (normalized lower)."""
    steps = chain.get("steps", [])
    return tuple((s.get("endpoint", "") or "").strip().lower() for s in steps) + (chain.get("vuln_type", ""),)


def merge_attack_chains(
    llm_chains: list[dict],
    gitnexus_chains: list[dict],
) -> list[dict]:
    """Merge LLM-track and GitNexus-track attack chains by endpoint-sequence dedup.

    Unlike merge_dual_track_queues (which dedups Vulnerability by location+sink),
    attack chains dedup by the ordered endpoint sequence + vuln_type. merge_source:
    both / llm-only / gitnexus-only. When both tracks have the same chain, the
    GitNexus (evidence-driven) confidence wins if higher; the LLM (creative) fills
    coverage GitNexus misses.
    """
    merged: list[dict] = []
    by_seq: dict[tuple, dict] = {}

    for chain in llm_chains:
        chain = dict(chain)
        chain.pop("_source", None)
        seq = _chain_endpoint_sequence(chain)
        chain["merge_source"] = "llm-only"
        by_seq[seq] = chain

    for chain in gitnexus_chains:
        chain = dict(chain)
        chain.pop("_source", None)
        seq = _chain_endpoint_sequence(chain)
        if seq in by_seq:
            existing = by_seq[seq]
            existing["merge_source"] = "both"
            # evidence-driven confidence wins if higher
            rank = {"confirmed": 3, "probable": 2, "theoretical": 1}
            if rank.get(chain.get("confidence", ""), 0) > rank.get(existing.get("confidence", ""), 0):
                existing["confidence"] = chain["confidence"]
            # merge step descriptions (GitNexus adds file:line evidence)
            if chain.get("steps") and not existing.get("_gn_merged"):
                existing["gitnexus_evidence"] = chain.get("steps")
                existing["_gn_merged"] = True
        else:
            chain["merge_source"] = "gitnexus-only"
            by_seq[seq] = chain

    merged = list(by_seq.values())
    logger.info("merge_attack_chains: %d chain(s) (both/llm-only/gitnexus-only)", len(merged))
    return merged
```

> 注：`logger` 已在 dual_track_merger.py:14 定义，复用。

- [ ] **Step 4: 跑测试验证通过**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_merge_attack_chains.py -v`
Expected: PASS（3 用例）。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/code_index/dual_track_merger.py packages/core/tests/code_index/test_merge_attack_chains.py
git commit -m "feat(core): merge_attack_chains 专用合并（endpoint-sequence 去重 + merge_source 三态）"
```

---

## Task 5: 新增双轨 attack chain activity + workflow 编排

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`（加 `run_attack_chain_llm_agent` + `run_attack_chain_assembly_v2`）
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`（vuln 后调双轨）
- Modify: `packages/whitebox/src/shannon_whitebox/worker.py`（注册新 activity）

**Interfaces:**
- Consumes: Task 1（ATTACK_CHAIN agent）、Task 2（attack-chain.txt）、Task 3（assembler）、Task 4（merger）。
- Produces: workflow vuln 后产 `attack_chains.json`（双轨合并）。

**Why:** 把双轨 attack chain 接进 whitebox workflow，vuln 后、reporting 前跑。删旧 dead-end。

- [ ] **Step 1: 写失败测试（activity 烟雾）**

```python
# packages/whitebox/tests/pipeline/test_attack_chain_workflow.py
from shannon_whitebox.pipeline import activities
from shannon_whitebox.pipeline import workflows


def test_attack_chain_activities_exist():
    assert hasattr(activities, "run_attack_chain_llm_agent")
    assert hasattr(activities, "run_attack_chain_assembly_v2")


def test_old_attack_chain_assembly_removed():
    """旧 dead-end run_attack_chain_assembly 必须删除。"""
    assert not hasattr(activities, "run_attack_chain_assembly"), (
        "旧 run_attack_chain_assembly（dead-end）未删除"
    )
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/pipeline/test_attack_chain_workflow.py -v`
Expected: FAIL — 新 activity 不存在。

- [ ] **Step 3: 实现——activities.py 加两个 activity**

在 activities.py 末尾（旧 `run_attack_chain_assembly` 位置，先不删，Task 6 删）加：

```python
@activity.defn
async def run_attack_chain_llm_agent(input: ActivityInput) -> dict:
    """LLM-track attack chain agent (creative-driven, multi-step inference).

    Runs the ATTACK_CHAIN agent (attack-chain.txt prompt) via the shared executor.
    Reads recon + exploitation_queue (LLM-track self-produced) — NEVER GitNexus
    deterministic artifacts (CLAUDE.md §1).
    """
    from shannon_core.models.agents import AgentName
    act_input = replace(input, agent_name=AgentName.ATTACK_CHAIN.value)
    try:
        metrics = await run_agent(act_input)
        return {"chain_count": getattr(metrics, "turns", 0), "track": "llm"}
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def run_attack_chain_assembly_v2(input: ActivityInput) -> dict:
    """GitNexus-track assembly + dual-track merge → attack_chains.json.

    1. Read {vt}_gitnexus_queue.json findings (GitNexus own output).
    2. assemble_attack_chains (deterministic cross-endpoint correlation).
    3. Read attack_chains_llm_queue.json (from run_attack_chain_llm_agent).
    4. merge_attack_chains → attack_chains.json.
    GitNexus unavailable → gitnexus_chains=[] (graceful), LLM track covers.
    """
    import json
    from shannon_core.code_index.attack_chain_assembler import assemble_attack_chains
    from shannon_core.code_index.dual_track_merger import merge_attack_chains
    from shannon_whitebox.audit.session_registry import get_audit_session

    try:
        repo, deliverables, _ = _get_paths(input)
        log = logging.getLogger(__name__)

        # 1. GitNexus findings per class
        gn_by_class: dict[str, list] = {}
        for vt in ("injection", "xss", "ssrf", "authz"):
            qpath = deliverables / f"{vt}_gitnexus_queue.json"
            if qpath.exists():
                try:
                    data = json.loads(qpath.read_text("utf-8"))
                    gn_by_class[vt] = data.get("vulnerabilities", []) or []
                except (json.JSONDecodeError, OSError):
                    gn_by_class[vt] = []

        # 2. Assemble GitNexus chains
        gn_chains = assemble_attack_chains(gn_by_class, log)
        gn_path = deliverables / "attack_chains_gitnexus_queue.json"
        atomic_write_json(gn_path, {"chains": gn_chains})

        # 3. LLM chains
        llm_chains: list = []
        llm_path = deliverables / "attack_chains_llm_queue.json"
        if llm_path.exists():
            try:
                llm_chains = json.loads(llm_path.read_text("utf-8")).get("chains", []) or []
            except (json.JSONDecodeError, OSError):
                llm_chains = []

        # 4. Merge → attack_chains.json
        merged = merge_attack_chains(llm_chains, gn_chains)
        atomic_write_json(deliverables / "attack_chains.json", {"chains": merged})

        return {"chain_count": len(merged),
                "llm_count": len(llm_chains), "gitnexus_count": len(gn_chains)}
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
```

> 实现者核对：`replace`（dataclasses.replace）、`atomic_write_json`、`_get_paths`、`classify_error_for_temporal`、`ApplicationFailure`、`PentestError`、`run_agent` 都已在 activities.py import/定义，grep 确认。`run_agent` 接受 `ActivityInput(agent_name=...)`（参照 `run_vuln_agent` :232-233）。

- [ ] **Step 4: 实现——workflows.py 编排**

在 workflows.py 找到旧 attack-chain phase（约 :449-482，`run_attack_chain_assembly` 调用处）。**整段替换**为双轨编排（仍 vuln 后、reporting 前，仍非致命 try/except）：

```python
        # === Attack chain (dual-track, post-vuln) ===
        try:
            await workflow.execute_activity(
                activities.run_attack_chain_llm_agent, act_input,
                **_activity_retry_kwargs(),
            )
            await workflow.execute_activity(
                activities.run_attack_chain_assembly_v2, act_input,
                **_activity_retry_kwargs(),
            )
        except Exception as e:
            await workflow.execute_activity(
                activities.run_log_info_activity,
                ActivityInput(info_message=f"Attack chain phase failed (non-fatal): {e}",
                              level="warning"),
            )
```

> 实现者核对：`_activity_retry_kwargs()` / `run_log_info_activity` / `act_input` 以 workflows.py 现有 attack-chain phase 的写法为准（原 :449-482 段用的同款脚手架），照搬其 retry/logging 模式即可。

- [ ] **Step 5: 实现——worker.py 注册**

在 worker.py 的 `activities=[...]` 列表（:98-112）加两行（保留旧 `run_attack_chain_assembly` 暂不删——Task 6 删）：

```python
            activities.run_attack_chain_llm_agent,
            activities.run_attack_chain_assembly_v2,
```

并在 worker.py 顶部 import 区（:30 附近）加：

```python
    run_attack_chain_llm_agent,
    run_attack_chain_assembly_v2,
```

- [ ] **Step 6: 跑测试验证通过**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/pipeline/test_attack_chain_workflow.py -v`
Expected: PASS（2 用例）。

- [ ] **Step 7: Commit**

```bash
cd /root/shannon-py
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/src/shannon_whitebox/pipeline/workflows.py packages/whitebox/src/shannon_whitebox/worker.py packages/whitebox/tests/pipeline/test_attack_chain_workflow.py
git commit -m "feat(whitebox): 双轨 attack chain activity + workflow 编排（vuln 后产 attack_chains.json）"
```

---

## Task 6: 删除旧 dead-end `run_attack_chain_assembly`

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`（删 `run_attack_chain_assembly` :1533-1598）
- Modify: `packages/whitebox/src/shannon_whitebox/worker.py`（删 import :30 + 注册 :105）
- Modify: `packages/whitebox/tests/pipeline/test_attack_chain_workflow.py`（Task 5 Step 1 已加 `test_old_attack_chain_assembly_removed`）

**Why:** 旧 `run_attack_chain_assembly` 是 dead-end（读 frontend_mapping 白盒空、产 attack_chains.json 无人读、与 route_chain_building 重复）。Task 5 的 `run_attack_chain_assembly_v2` 替代它。

- [ ] **Step 1: 实现——删旧 activity + 注册 + import**

① activities.py：删除整个 `run_attack_chain_assembly` 函数（:1533-1598）。
② worker.py:30：从 import 列表删 `run_attack_chain_assembly,`。
③ worker.py:105：从 `activities=[...]` 删 `activities.run_attack_chain_assembly,`。

- [ ] **Step 2: 跑测试验证通过**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/pipeline/test_attack_chain_workflow.py packages/core/tests/code_index/test_attack_chain_assembler.py packages/core/tests/code_index/test_merge_attack_chains.py -v`
Expected: PASS（旧删除 + 新功能无回归）。

- [ ] **Step 3: 跑 worker 导入冒烟（确认无 dangling import）**

Run: `cd /root/shannon-py && python -c "from shannon_whitebox import worker; print('worker import OK')"`
Expected: 输出 `worker import OK`（无 ImportError）。

- [ ] **Step 4: Commit**

```bash
cd /root/shannon-py
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/src/shannon_whitebox/worker.py
git commit -m "refactor(whitebox): 删除 dead-end run_attack_chain_assembly（被 v2 双轨替代）"
```

---

## Task 7: 守铁律解耦 + 端到端验证

**Files:**
- Test: `packages/core/tests/prompts/test_attack_chain_decoupling.py`（Task 2 已建，加端到端用例）

**Why:** 确认整条链守铁律（LLM 轨不吃确定性层）+ attack_chains.json 双轨合并产出。

- [ ] **Step 1: 加端到端解耦断言**

在 `test_attack_chain_decoupling.py` 追加：

```python
def test_attack_chain_pipeline_does_not_feed_gitnexus_to_llm_prompt():
    """守铁律：GitNexus 轨产物（gitnexus_queue）不被 attack-chain.txt 引用。"""
    content = (PROMPTS_DIR / "attack-chain.txt").read_text("utf-8")
    # attack-chain.txt 只读 exploitation_queue（LLM 轨），不读 gitnexus_queue
    assert "gitnexus_queue" not in content
    assert "exploitation_queue" in content


def test_assembler_only_reads_gitnexus_own_output():
    """assembler 读 gitnexus_queue（确定性层自己产物），不反向喂 LLM 轨。"""
    import inspect
    from shannon_core.code_index import attack_chain_assembler
    src = inspect.getsource(attack_chain_assembler)
    # assembler 不读 recon_deliverable（LLM 轨）——只读 gitnexus_queue
    # （它由 activity 喂 findings，自身不读文件，source 不含 recon 读文件逻辑）
    assert "recon_deliverable" not in src
```

- [ ] **Step 2: 跑测试验证通过**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/prompts/test_attack_chain_decoupling.py -v`
Expected: PASS（5 用例）。

- [ ] **Step 3: 跑全套相关测试 + 解耦测试**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py packages/core/tests/code_index/test_attack_chain_assembler.py packages/core/tests/code_index/test_merge_attack_chains.py packages/core/tests/models/test_attack_chain_agent.py packages/whitebox/tests/pipeline/test_attack_chain_workflow.py -v`
Expected: PASS。

- [ ] **Step 4: Commit**

```bash
cd /root/shannon-py
git add packages/core/tests/prompts/test_attack_chain_decoupling.py
git commit -m "test: attack chain 双轨守铁律解耦断言（gitnexus 不反向喂 LLM 轨）"
```

---

## Self-Review（plan 完成后自查）

**1. Spec 覆盖**：
- B'1 LLM 轨 Agent：Task 1（agent 注册）+ Task 2（prompt）+ Task 5（activity 执行）✓
- B'1 GitNexus 轨确定性组装：Task 3（assembler）✓
- B'1 专用合并：Task 4（merge_attack_chains）✓
- B'1 时序（vuln 后）+ 删 dead-end：Task 5（workflow 编排）+ Task 6（删旧）✓
- 守铁律：Task 7（解耦）+ 每个 prompt/assembler task 有断言 ✓
- spec §4.6 两轨差异（LLM 创意 / GitNexus 证据）：assembler confidence=confirmed（证据）vs LLM probable（创意）+ merge 时证据 confidence 优先 ✓

**2. Placeholder 扫描**：Task 5 Step 3/4 的 `replace`/`atomic_write_json`/`_get_paths`/`classify_error_for_temporal`/`ApplicationFailure`/`_activity_retry_kwargs`/`run_log_info_activity` 已注明"以现有代码为准 grep 确认"——给出完整代码骨架 + 实现者核对点，非 placeholder。其余 task 均 actual code。✓

**3. 类型/命名一致**：
- `assemble_attack_chains(gitnexus_findings_by_class, logger) -> list[dict]`（Task 3）= Task 5 调用一致 ✓
- `merge_attack_chains(llm_chains, gitnexus_chains) -> list[dict]`（Task 4）= Task 5 调用一致 ✓
- `AgentName.ATTACK_CHAIN`（Task 1）= Task 5 `run_attack_chain_llm_agent` 用 ✓
- attack-chain.txt 产出 `attack_chains_llm_queue.json`（Task 2 output 段）= Task 5 读 ✓
- AttackChain dict schema（id/name/steps/vuln_type/severity/confidence）：assembler（Task 3）+ LLM prompt（Task 2）+ merger（Task 4）一致 ✓

**4. 已知简化（实现者注意）**：
- assembler 的 stored-XSS 关联用启发式 storage token（profiles/users/orders...）—— spec §9 风险表已记"误报率靠 verdict + 报告分级"。生产前可按真机调优 token 列表。
- IDOR 链组装简化为"≥2 个 authz finding 即组 probable 链"——真机后可加更精确的数据依赖串联。
- attack-chain.txt agent 的 `exploitation_queue.json` 文件名（Task 2 context 段）需与实际产出名一致（activities.py 写的 `{vt}_exploitation_queue.json`）——实现者核对 vuln_type 缩写（injection/xss/ssrf/authz）。

---

## 不在本 plan（follow-up）

- **B'3 黑盒按链验证**：黑盒 exploit 读 `attack_chains.json` 按 chain step 验证，升级 confidence。基础设施已就绪（同 workspace + `detect_whitebox_results`），后续接 prompt 段。
- **attack-chain agent 误报调优**：真机后按 verdict 分级 + 报告展示调整。
- **assembler storage token 列表 / IDOR 精确串联**：真机后调优。
