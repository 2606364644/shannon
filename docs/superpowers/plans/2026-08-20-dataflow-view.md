# 漏洞数据流视图（剪枝树）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 白盒扫描详情页新增「数据流」tab：每个 sink 一棵剪枝树，双轨枝条共存（GitNexus 枝带 verdict，LLM 枝带 dataflow_steps），枝条带打通/剪断判定，节点带防护标注与代码预览；auth/authz 以关卡链降级形态展示；safe-only sink 与 safe_vectors 也入页。core 写时组装唯一产物 `dataflow_view.json`，web 只消费这一个 schema。

**Architecture:** 方案 B 写时组装。管线落盘三类结构化源（GitNexus chain_verdicts 含 safe 枝 + LLM dataflow_steps 三处 append-only + safe_vectors 落 json）→ core 新增纯函数组装器 `services/dataflow_view.py::assemble_dataflow_view` 读 5 类产物组装成树 → whitebox merge 活动之后接 `run_assemble_dataflow_view` 活动（失败不阻塞）→ web 端点经 `resolve_intermediate` 读 `dataflow_view.json` → 前端 DataFlowTab 自研 SVG 剪枝树（不引可视化库）。

**Tech Stack:** Python 3.12 / pytest（core + whitebox）· FastAPI（web 端点）· React 19 + Vite + Tailwind + SWR + i18next + vitest + msw（前端）。零新依赖（前端不引 reactflow/d3）。

**Spec:** `docs/superpowers/specs/2026-08-20-dataflow-view-design.md`（定稿 commit db6b01d8 + 8e1c1514 + 788a317f）。姊妹在途工作：`docs/superpowers/plans/2026-08-20-vuln-queue-hardening-phase2.md`（Phase 2，dataflow_steps 是 append-only 字段不冲突）。

## Global Constraints

- **双轨铁律**（CLAUDE.md §1）：LLM 轨 prompt 仍不引确定性产物——本计划只往 `vuln-{injection,xss,ssrf}.txt` 的 `finding_submission` 段加「提交时列 dataflow_steps 节点」说明（append-only），不喂 hints。`tests/prompts/test_static_dataflow_hints_decoupling.py` 维持绿。auth/authz prompt 不动（无 taint 流）。
- **双引擎零新代码**：`dataflow_steps` 经 bridge.py 现有 collector `SectionSchema` 单点定义 → 同一份 `json_schema` 出 openai `FunctionTool`（`params_json_schema`，`strict_json_schema=False`）+ claude `SdkMcpTool`（`input_schema`）。既有防御不动：`repair_json_arguments` + 顶层 dict 检查 + `strict=False`。
- **必须进 pydantic 模型**：`dataflow_steps` 字段加进 `InjectionVulnerability`/`XssVulnerability`/`SsrfVulnerability`——否则双轨合并 `merge_dual_track_queues` 用 `finding.model_dump()` 会丢字段。
- **写时组装**：`dataflow_view.json` 是唯一产出物，由 core 组装器写时拼装；web 不做读时 join（避免 tiering 错位覆辙——见 memory `report-page-analysis-deliverables-tiering-misread-fix`）。
- **失败不阻塞扫描**：组装器活动 `run_assemble_dataflow_view` 任何异常 → warning + 不产文件，不终止管线（对齐 attack-chain 阶段 non-fatal 模式）。
- **tiering 读侧 fallback**：web 经 `resolve_intermediate(whitebox_dir, "dataflow_view.json")` 读（先 `intermediate/` 再平铺兜底）。
- **测试只跑改动相关文件**（CLAUDE.md §3：全套 pytest 有预存挂起，禁止广跑）。前端测试用 `./node_modules/.bin/vitest`（不用 pnpm test）。
- **白话文案**（spec §5 对照表）：贯通→打通、safe→剪断（在 X 被拦下）、safe_vectors→排查过的入口、auth/authz→认证/授权风险、未被触及→无输入到达、LLM 轨无源码→「LLM 扫描的节点不带源码，agent 原话」。展示文案与工程字段解耦。
- commit 信息用中文、尾注 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。
- 包根：core `/root/shannon-py/packages/core/`；whitebox `/root/shannon-py/packages/whitebox/`；web `/root/shannon-py/packages/web/`；prompts 仓库根 `/root/shannon-py/prompts/`。pytest 从对应包目录跑。

---

## 文件结构（实施索引）

**core（P1–P4）**
- `models/deliverables.py` — `INTERMEDIATE_FILE_PATTERNS` 加 `dataflow_view.json`（P1 pattern 注册）
- `code_index/chain_verdict.py` — GitNexus 判定收集点加 safe 链落盘（P1）
- `whitebox/pipeline/activities.py`（whitebox 包） — `run_gitnexus_chain_verdict` 收集 + 落 `{vc}_chain_verdicts.json`（P1）；新增 `run_assemble_dataflow_view` 活动（P4）
- `whitebox/pipeline/workflows.py`（whitebox 包） — merge 后插 `run_assemble_dataflow_view` 调用（P4）
- `collectors/vuln.py` — `_finding_props` 基线加 `dataflow_steps` schema 片段（P2）
- `models/queue_schemas.py` — 三模型加 `dataflow_steps: list[dict] | None`；`parse_lenient` 宽容归一（P2）
- `agents/executor.py` — 落 queue 时同步落 `{vc}_safe_vectors.json`（P3）
- `services/dataflow_view.py` — **新增**组装器纯函数（P4）

**web（P5）**
- `api/scans.py` — `GET /{ws}/scans/{scan_id}/dataflow` 端点
- `components/deliverables_reader.py` — `dataflow_view.json` kind 分类（如需）

**前端（P-Front）**
- `frontend/src/routes/.../router.tsx` — ScanDetail 加 `dataflow` child
- `frontend/src/routes/WorkspaceDetail/DataFlowTab.tsx` — **新增**页面骨架+两栏
- `frontend/src/components/dataflow/*` — **新增** TocSideBar / PruningTreeFig / BranchRow / GuardChain / SafeEntries
- `frontend/src/components/.../VulnCard.tsx` — 加「查看数据流」链接
- `frontend/src/api/client.ts` + `api/types.ts` — dataflow 类型 + fetcher
- `frontend/src/locales/{zh,en}` — 白话文案

**探针（P6）**
- `scripts/validate_claude_dataflow_probe.py` / `scripts/validate_openai_dataflow_probe.py` — **新增**

---

### Task 1: core pattern 注册 — `dataflow_view.json` 纳入 intermediate tiering

**Files:**
- Modify: `packages/core/src/supernova_core/models/deliverables.py`（`INTERMEDIATE_FILE_PATTERNS` 元组，约 L45）
- Test: `packages/core/tests/models/test_deliverables_tiering.py`（新建）

**Interfaces:**
- Consumes: 无。
- Produces: `classify_tier`（L71，路径含 `intermediate/` 段即归 `intermediate`；否则 fnmatch `INTERMEDIATE_FILE_PATTERNS`）对 `dataflow_view.json` 与 `{vc}_chain_verdicts.json` 命中 `intermediate` tier，防 web 读侧 tiering 错位。后续 P4 落盘、P5 读侧均依赖此 tier 归类。

- [ ] **Step 1: Write the failing test**

`packages/core/tests/models/test_deliverables_tiering.py`：

```python
from supernova_core.models.deliverables import classify_tier


def test_dataflow_view_classified_intermediate():
    assert classify_tier("whitebox/intermediate/dataflow_view.json") == "intermediate"


def test_chain_verdicts_classified_intermediate():
    assert classify_tier("whitebox/intermediate/injection_chain_verdicts.json") == "intermediate"


def test_dataflow_view_flat_fallback_intermediate():
    # 平铺兜底（无 intermediate/ 段）时靠 pattern 命中
    assert classify_tier("whitebox/dataflow_view.json") == "intermediate"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py/packages/core && python -m pytest tests/models/test_deliverables_tiering.py -v`
Expected: 第 3 个用例 FAIL（`dataflow_view.json` 平铺无 `intermediate/` 段、又不在 patterns 元组 → 归 `deliverable`）。

- [ ] **Step 3: Write minimal implementation**

`packages/core/src/supernova_core/models/deliverables.py` — 在 `INTERMEDIATE_FILE_PATTERNS` 元组末尾（现有末项 `".*checkpoint*.json"` 之后）加两行：

```python
    ".*checkpoint*.json",
    "dataflow_view.json",
    "*_chain_verdicts.json",
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py/packages/core && python -m pytest tests/models/test_deliverables_tiering.py -v`
Expected: 3 PASS。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/supernova_core/models/deliverables.py packages/core/tests/models/test_deliverables_tiering.py
git commit -m "feat(core): dataflow_view.json + chain_verdicts pattern 注册 intermediate tiering"
```

---

### Task 2: P1 — GitNexus chain verdicts 落盘（safe 链也进产物）

**Files:**
- Modify: `packages/whitebox/src/supernova_whitebox/pipeline/activities.py`（`run_gitnexus_chain_verdict`，L1401–；落盘点 L1560–1565 `atomic_write_json`）
- Modify: `packages/core/src/supernova_core/code_index/chain_verdict.py`（收集每条候选链判定，L377 `judge_chain_verdict`；`CandidateChain` L141、`ChainVerdict` L158）
- Test: `packages/whitebox/tests/pipeline/test_gitnexus_chain_verdicts_dump.py`（新建）

**Interfaces:**
- Consumes: `extract_candidate_chains(pgraph, *, vuln_class, sink_call_sites=None) -> list[CandidateChain]`（chain_verdict.py:291）；`judge_chain_verdict(candidate, *, llm_client) -> ChainVerdict`（L377）。`CandidateChain`（L141 frozen dataclass）含 `flow_id`/`sink_call_site_id`/`propagation_steps`/`sanitizer_annotations` 等；`ChainVerdict`（L158）含 `verdict`/`mismatch_reason`/`witness_payload`/`evidence_chain`/`confidence`/`title`。`intermediate_path(track_dir, filename) -> Path`（paths.py:151）；`atomic_write_json(path, data, *, indent=2)`（atomic_write.py:7）。
- Produces: 每类漏洞落盘 `intermediate/{vc}_chain_verdicts.json`，shape `[{"flow_id","verdict","reason","sanitizer_annotations","confidence","sink_call_site_id","vuln_class"}]`，**safe 链 verdict="safe" 也进**。后续 P4 组装器按 `flow_id` + `sink_call_site_id` 读此产物构建 GitNexus 枝 + safe-only 树。

- [ ] **Step 1: Write the failing test**

`packages/whitebox/tests/pipeline/test_gitnexus_chain_verdicts_dump.py`：

```python
"""P1: chain_verdicts 落盘，safe 链也进产物。"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def deliverables(tmp_path: Path) -> Path:
    (tmp_path / "intermediate").mkdir()
    return tmp_path


class _FakeCandidate:
    """Minimal stand-in for CandidateChain fields the dump reads."""
    def __init__(self, flow_id, sink_call_site_id, verdict, reason="",
                 sanitizer_annotations=None, confidence="high", vuln_class="injection"):
        self.flow_id = flow_id
        self.sink_call_site_id = sink_call_site_id
        self.vuln_class = vuln_class
        self.sanitizer_annotations = sanitizer_annotations or []


class _FakeVerdict:
    def __init__(self, verdict, mismatch_reason, confidence="high"):
        self.verdict = verdict
        self.mismatch_reason = mismatch_reason
        self.confidence = confidence


def test_chain_verdicts_dump_includes_safe_chains(deliverables: Path):
    """safe 链也落盘，不再用完即丢。"""
    from supernova_whitebox.pipeline import activities

    candidates = [
        _FakeCandidate("u1->s1", "s1", "vulnerable", "none"),
        _FakeCandidate("u2->s1", "s1", "safe", "shlex.quote 覆盖"),
    ]
    verdicts = [_FakeVerdict("vulnerable", "none"), _FakeVerdict("safe", "shlex.quote 覆盖")]

    # 触发落盘逻辑（直接调被测纯函数，见 Step 3 产出）
    from supernova_whitebox.pipeline.activities import _dump_chain_verdicts
    _dump_chain_verdicts(deliverables, "injection",
                         list(zip(candidates, verdicts)))

    path = deliverables / "intermediate" / "injection_chain_verdicts.json"
    assert path.exists()
    rows = json.loads(path.read_text(encoding="utf-8"))
    assert len(rows) == 2
    assert {r["verdict"] for r in rows} == {"vulnerable", "safe"}
    assert rows[1]["reason"] == "shlex.quote 覆盖"


def test_chain_verdicts_dump_empty_when_no_candidates(deliverables: Path):
    """零候选不落盘（不产空文件）。"""
    from supernova_whitebox.pipeline.activities import _dump_chain_verdicts
    _dump_chain_verdicts(deliverables, "ssrf", [])
    assert not (deliverables / "intermediate" / "ssrf_chain_verdicts.json").exists()
```

> 注：测试用假对象只模拟被测 dump 函数读的字段。若 Step 3 实现选择就地内联（不抽 `_dump_chain_verdicts`），则改为 monkeypatch `judge_chain_verdict` + 构造最小 `parameter_graph.json` 走活动层——但抽纯函数更可测，推荐抽。

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py/packages/whitebox && python -m pytest tests/pipeline/test_gitnexus_chain_verdicts_dump.py -v`
Expected: FAIL（`_dump_chain_verdicts` 不存在 / safe 链未落盘）。

- [ ] **Step 3: Write minimal implementation**

`packages/whitebox/src/supernova_whitebox/pipeline/activities.py` — 在 `run_gitnexus_chain_verdict` 内（L1541–1565 循环 `("injection", build_injection_findings), ...`），落 `gitnexus_queue` 之前，对每类收集 `(candidate, verdict)` 对并落 `{vc}_chain_verdicts.json`。抽纯函数：

```python
def _dump_chain_verdicts(
    deliverables: Path,
    vc: str,
    pairs: list[tuple],
) -> None:
    """落 intermediate/{vc}_chain_verdicts.json；safe 链也进。零候选不落盘。"""
    if not pairs:
        return
    rows = []
    for cand, verdict in pairs:
        rows.append({
            "flow_id": getattr(cand, "flow_id", ""),
            "sink_call_site_id": getattr(cand, "sink_call_site_id", ""),
            "vuln_class": vc,
            "verdict": getattr(verdict, "verdict", ""),
            "reason": getattr(verdict, "mismatch_reason", "") or "",
            "sanitizer_annotations": list(getattr(cand, "sanitizer_annotations", []) or []),
            "confidence": getattr(verdict, "confidence", ""),
        })
    from supernova_core.utils.paths import intermediate_path
    from supernova_core.utils.atomic_write import atomic_write_json
    atomic_write_json(
        intermediate_path(deliverables, f"{vc}_chain_verdicts.json"),
        {"verdicts": rows},
    )
```

在 `run_gitnexus_chain_verdict` 的 per-vc 循环里，把当前已有的「extract → judge → build findings」流程改成同时收集 `pairs`（candidate + verdict），循环末尾调 `_dump_chain_verdicts(deliverables, vc, pairs)`。`sanitizer_annotations` 已在 `CandidateChain`（L141）上，原 build 路径未把它复制进 finding——本任务补落盘，不改 build 行为。

> 兼容在途 Phase 2：`{vc}_chain_verdicts.json` 是新文件名，与 `{vc}_gitnexus_queue.json` / `{vc}_exploitation_queue.json` 不冲突；roster 对账按 finding ID 不按字段。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py/packages/whitebox && python -m pytest tests/pipeline/test_gitnexus_chain_verdicts_dump.py -v`
Expected: 2 PASS。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/whitebox/src/supernova_whitebox/pipeline/activities.py packages/whitebox/tests/pipeline/test_gitnexus_chain_verdicts_dump.py
git commit -m "feat(whitebox): P1 GitNexus chain verdicts 落盘（safe 链也进产物）"
```

---

### Task 3: P2 — LLM `dataflow_steps` schema（collector）+ 双引擎一致性测试

**Files:**
- Modify: `packages/core/src/supernova_core/collectors/vuln.py`（`_finding_props` 基线 L324–345；`_obj` L26–30、`_str_field` L22–23）
- Test: `packages/core/tests/collectors/test_vuln_models.py`（既有断言更新）
- Test: `packages/core/tests/agents/test_providers_anthropic_output_format.py`（双引擎 schema 一致性，既有文件）

**Interfaces:**
- Consumes: `SectionSchema`（collectors/base.py:22，frozen dataclass，字段 `tool_name`/`section_key`/`description`/`json_schema`/`mode`）；`_finding_props(class_props) -> dict`（vuln.py:324，合并基线+类 props）；`make_vuln_sections(vc)`（L451）。
- Produces: `submit_finding` 的 `json_schema.properties` 多一个 `dataflow_steps`（array of object，元素 `{label:str, file:str, line:int|null, protection:str|null}`，全 optional 不进 required）。bridge.py `build_openai_tools`/`build_claude_mcp_server` 原样透传——`FunctionTool.params_json_schema` 与 `SdkMcpTool.input_schema` 自动同含此字段。P5 真机探针验证 GLM 产出。

- [ ] **Step 1: Write the failing test**

`packages/core/tests/collectors/test_vuln_models.py`——找到现有校验 `submit_finding` schema 的断言（`make_vuln_sections` 返回的 section 里 `json_schema.properties` 字段集合），追加：

```python
def test_submit_finding_has_dataflow_steps_field():
    """P2: dataflow_steps 扁平数组字段进 submit_finding schema（inj/xss/ssrf）。"""
    from supernova_core.collectors.vuln import make_vuln_sections
    for vc in ("injection", "xss", "ssrf"):
        sections = make_vuln_sections(vc)
        submit = next(s for s in sections if s.tool_name == "submit_finding")
        props = submit.json_schema["properties"]
        assert "dataflow_steps" in props, f"{vc} submit_finding missing dataflow_steps"
        ds = props["dataflow_steps"]
        assert ds["type"] == "array"
        item = ds["items"]
        assert set(item["properties"].keys()) >= {"label", "file", "line", "protection"}
        # 不进 required（全 optional）
        assert "dataflow_steps" not in submit.json_schema.get("required", [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py/packages/core && python -m pytest tests/collectors/test_vuln_models.py::test_submit_finding_has_dataflow_steps_field -v`
Expected: FAIL（`dataflow_steps` 不在 props）。

- [ ] **Step 3: Write minimal implementation**

`packages/core/src/supernova_core/collectors/vuln.py` — 在 `_finding_props`（L324–345）的通用 `props` 字典里（`props.update(class_props)` 之前或之后均可，基线对所有类生效）加一个属性条目。用现有 `_obj`/`_str_field` 风格手写片段（数组无现成 helper）：

```python
    # P2 dataflow_steps：扁平数组（压缩 GLM 结构化输出失败面），元素全 optional。
    # 仅 inj/xss/ssrf 有 taint 流；auth/authz 基线也带但 prompt 不引导→agent 不产。
    props["dataflow_steps"] = {
        "type": "array",
        "description": "按传播顺序列 source→sink 经过的节点；防护节点标 protection。元素全 optional。",
        "items": {
            "type": "object",
            "properties": {
                "label": {"type": "string", "minLength": 1, "description": "函数名或调用点描述"},
                "file": {"type": "string", "description": "文件路径"},
                "line": {"anyOf": [{"type": "integer"}, {"type": "null"}], "description": "行号，未知填 null"},
                "protection": {"anyOf": [{"type": "string"}, {"type": "null"}], "description": "该节点防护（sanitizer 名）；无防护填 null"},
            },
            "required": ["label"],
        },
    }
```

> 放 `_finding_props` 基线意味着 auth/authz 的 `submit_finding` 也会带此字段。这是有意的——schema 统一便于双引擎、auth/authz agent 不产就不填（字段 optional），且组装器对 auth/authz 走 control_findings 分支不读 dataflow_steps。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py/packages/core && python -m pytest tests/collectors/test_vuln_models.py -v`
Expected: PASS（含既有断言 + 新断言）。若既有断言因新字段变化失败，更新断言集合而非回退。

- [ ] **Step 5: 双引擎 schema 一致性测试**

`packages/core/tests/agents/test_providers_anthropic_output_format.py`（既有文件，校验 bridge 双引擎 schema）——追加：

```python
def test_bridge_dataflow_steps_in_both_engines():
    """P2: dataflow_steps 一份 schema 出两套工具（bridge 单点定义不变量）。"""
    from supernova_core.collectors.vuln import make_vuln_sections
    from supernova_core.collectors.bridge import build_openai_tools, build_claude_mcp_server

    sections = make_vuln_sections("injection")
    submit = [s for s in sections if s.tool_name == "submit_finding"]

    oai = build_openai_tools(submit)
    claude = build_claude_mcp_server(submit)

    oai_props = oai[0].params_json_schema["properties"]
    claude_props = claude[0].input_schema["properties"]
    assert "dataflow_steps" in oai_props
    assert "dataflow_steps" in claude_props
    assert oai_props["dataflow_steps"] == claude_props["dataflow_steps"]  # 同一份 dict
```

Run: `cd /root/shannon-py/packages/core && python -m pytest tests/agents/test_providers_anthropic_output_format.py::test_bridge_dataflow_steps_in_both_engines -v`
Expected: PASS（`strict_json_schema=False` 让 openai 宽容解析；bridge 透传同一 dict）。

- [ ] **Step 6: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/supernova_core/collectors/vuln.py packages/core/tests/collectors/test_vuln_models.py packages/core/tests/agents/test_providers_anthropic_output_format.py
git commit -m "feat(core): P2 collector dataflow_steps schema（扁平数组，双引擎透传）"
```

---

### Task 4: P2 — pydantic 三模型 `dataflow_steps` 字段 + `parse_lenient` 宽容归一

**Files:**
- Modify: `packages/core/src/supernova_core/models/queue_schemas.py`（三模型 L23–77；`parse_lenient` L123–208，循环 L193–200）
- Test: `packages/core/tests/agents/test_executor_vuln_queue_reconcile.py`（既有文件，追加 parse_lenient 用例）

**Interfaces:**
- Consumes: `parse_lenient(cls, content, vuln_class=None) -> LenientParseResult`（queue_schemas.py:123）；`_CLASS_ADAPTERS`（L98）按 vc 选 `TypeAdapter`。
- Produces: `InjectionVulnerability`/`XssVulnerability`/`SsrfVulnerability` 各增 `dataflow_steps: list[dict] | None = None`。`parse_lenient` 在 `adapter.validate_python(entry)` 前（L193–200 循环内）预处理 `dataflow_steps`：非 list→None、元素非 dict→丢弃、字段类型错→忽略——畸形不拒收 finding。P4 组装器经 `finding.dataflow_steps` 读 LLM 枝节点。

- [ ] **Step 1: Write the failing test**

`packages/core/tests/agents/test_executor_vuln_queue_reconcile.py`（既有文件）追加：

```python
def test_dataflow_steps_survives_model_dump_merge():
    """P2: dataflow_steps 必须进 pydantic 模型，否则 merge model_dump() 丢字段。"""
    from supernova_core.models.queue_schemas import (
        VulnerabilityQueue, InjectionVulnerability,
    )
    f = InjectionVulnerability(
        ID="INJ-VULN-01", vulnerability_type="injection",
        externally_exploitable=True, confidence="high",
        dataflow_steps=[{"label": "UserController.list", "file": "a.py", "line": 25, "protection": None}],
    )
    dumped = f.model_dump()
    assert dumped["dataflow_steps"] == [{"label": "UserController.list", "file": "a.py", "line": 25, "protection": None}]


def test_parse_lenient_normalizes_dataflow_steps_malformed():
    """P2: 畸形 dataflow_steps 不拒收 finding——非 list→None、元素非 dict→丢弃、字段类型错→忽略。"""
    from supernova_core.models.queue_schemas import VulnerabilityQueue
    import json

    content = json.dumps({"vulnerabilities": [
        {"ID": "V1", "vulnerability_type": "injection", "externally_exploitable": True,
         "confidence": "high", "dataflow_steps": "not-a-list"},          # 非 list → None
        {"ID": "V2", "vulnerability_type": "injection", "externally_exploitable": True,
         "confidence": "high", "dataflow_steps": [{"label": "ok"}, "not-a-dict", 42]},  # 混杂 → 留 {label:ok}
        {"ID": "V3", "vulnerability_type": "injection", "externally_exploitable": True,
         "confidence": "high", "dataflow_steps": [{"label": 123, "file": "a.py"}]},   # label 类型错 → 忽略该字段，元素留
    ]})
    rec = VulnerabilityQueue.parse_lenient(content, vuln_class="injection")
    assert len(rec.queue.vulnerabilities) == 3  # 都没被丢
    v1, v2, v3 = rec.queue.vulnerabilities
    assert v1.dataflow_steps is None
    assert v2.dataflow_steps == [{"label": "ok"}]  # 非 dict 元素丢弃
    # label 类型错 → 该字段忽略，元素保留（label 缺失）
    assert v3.dataflow_steps == [{}]
```

> 最后一个断言取决于 Step 3 归一策略：若选择"字段类型错→整元素丢弃"则改为 `== []`；若"忽略错字段留元素"则 `== [{}]`。spec 原文是"字段类型错→忽略"（忽略字段不是丢元素），故取 `[{}]`。实现与断言对齐即可。

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py/packages/core && python -m pytest tests/agents/test_executor_vuln_queue_reconcile.py::test_dataflow_steps_survives_model_dump_merge tests/agents/test_executor_vuln_queue_reconcile.py::test_parse_lenient_normalizes_dataflow_steps_malformed -v`
Expected: FAIL（模型无 `dataflow_steps` 字段 → AttributeError / parse_lenient 无归一）。

- [ ] **Step 3: Write minimal implementation**

`packages/core/src/supernova_core/models/queue_schemas.py`：

(a) 三个模型各加字段。在 `InjectionVulnerability`（L23–44）、`XssVulnerability`（L46–57）、`SsrfVulnerability`（L66–77）的末尾字段区各加：

```python
    dataflow_steps: list[dict] | None = None
```

> 也加到 `BaseVulnerability`（L7–22）更省事且对所有子类生效——**推荐放基类**，与 collector `_finding_props` 基线一致（auth/authz 也带，agent 不产就 None）。

(b) `parse_lenient` 循环内（L193–200，`adapter.validate_python(entry)` 之前）加预处理：

```python
        for entry in entries:
            if not isinstance(entry, dict):
                dropped += 1
                continue
            _normalize_dataflow_steps(entry)  # P2 宽容归一：畸形不拒收
            try:
                vulns.append(adapter.validate_python(entry))
            except Exception:
                dropped += 1
```

模块级新函数（放在 `parse_lenient` 上方）：

```python
def _normalize_dataflow_steps(entry: dict) -> None:
    """P2: dataflow_steps 宽容归一——畸形不拒收 finding。

    非 list → 删键（pydantic 默认 None）；元素非 dict → 丢弃该元素；
    字段类型错 → 忽略该字段（留 dict 壳）。None 缺省不动。
    """
    if "dataflow_steps" not in entry:
        return
    raw = entry["dataflow_steps"]
    if raw is None:
        return
    if not isinstance(raw, list):
        del entry["dataflow_steps"]  # 非 list → 当作未提供
        return
    kept: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue  # 非 dict 元素丢弃
        clean: dict = {}
        if isinstance(item.get("label"), str) and item["label"]:
            clean["label"] = item["label"]
        if isinstance(item.get("file"), str):
            clean["file"] = item["file"]
        line = item.get("line")
        if isinstance(line, int) or line is None:
            clean["line"] = line
        prot = item.get("protection")
        if isinstance(prot, str) or prot is None:
            clean["protection"] = prot
        kept.append(clean)
    entry["dataflow_steps"] = kept or None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py/packages/core && python -m pytest tests/agents/test_executor_vuln_queue_reconcile.py -v`
Expected: PASS。确认 `tests/collectors/test_vuln_models.py`（Task 3）仍绿。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/supernova_core/models/queue_schemas.py packages/core/tests/agents/test_executor_vuln_queue_reconcile.py
git commit -m "feat(core): P2 pydantic dataflow_steps 字段 + parse_lenient 宽容归一"
```

---

### Task 5: P2 — prompts `dataflow_steps` 提交说明（inj/xss/ssrf）

**Files:**
- Modify: `prompts/vuln-injection.txt`（`<finding_submission>` 段 L112–L137）
- Modify: `prompts/vuln-xss.txt`（`<finding_submission>` 段 L111–L135）
- Modify: `prompts/vuln-ssrf.txt`（`<finding_submission>` 段 L99–L119）
- Test: `packages/core/tests/prompts/test_vuln_host_rendered.py`（既有文件，prompt↔schema 锁定）

**Interfaces:**
- Consumes: Task 3 的 collector `dataflow_steps` schema（inj/xss/ssrf）。
- Produces: 三 prompt 的 `finding_submission` JSON 示例块 + 说明段出现 `dataflow_steps` 字段及「按传播顺序列节点、防护节点标 protection」指引。auth/authz prompt 不动（确认无 taint 流段）。

- [ ] **Step 1: Write the failing test**

`packages/core/tests/prompts/test_vuln_host_rendered.py`（既有 prompt↔schema 锁定测试）追加——校验三 prompt 的 finding_submission 段提到 dataflow_steps，且 auth/authz 不提：

```python
import pytest
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[3].parent / "prompts"  # 仓库根/prompts


@pytest.mark.parametrize("vc", ["injection", "xss", "ssrf"])
def test_taint_prompt_mentions_dataflow_steps(vc):
    txt = (PROMPTS_DIR / f"vuln-{vc}.txt").read_text(encoding="utf-8")
    assert "dataflow_steps" in txt, f"{vc} prompt 未提 dataflow_steps"
    # 出现在 finding_submission 段附近（粗校验：该段存在且含字段名）
    assert "finding_submission" in txt or "submit_finding" in txt


@pytest.mark.parametrize("vc", ["auth", "authz"])
def test_control_prompt_no_dataflow_steps(vc):
    """auth/authz 无 taint 流，不引导 dataflow_steps。"""
    txt = (PROMPTS_DIR / f"vuln-{vc}.txt").read_text(encoding="utf-8")
    assert "dataflow_steps" not in txt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py/packages/core && python -m pytest tests/prompts/test_vuln_host_rendered.py -k dataflow_steps -v`
Expected: 3 个 taint prompt FAIL（未提 dataflow_steps）；auth/authz PASS（已不含）。

- [ ] **Step 3: Write minimal implementation**

三个 prompt 各自在 `<finding_submission>` 段的 JSON 示例块末尾加字段 + 段尾加一句说明。**以 vuln-injection.txt 为例**（xss/ssrf 照抄，字段名对齐各自 finding schema——xss 用 `sink_function`、ssrf 用 `vulnerable_parameter`，但 `dataflow_steps` 字段名三处相同）：

在 JSON 示例块（injection 约 L118–L136）的最后一个字段后加：

```json
  "dataflow_steps": [
    {"label": "UserController.list", "file": "app/controllers/userController.js", "line": 25, "protection": null},
    {"label": "orm.escape", "file": "app/helpers/query.js", "line": 30, "protection": "orm.escape"}
  ]
```

在 `<finding_submission>` 段末尾（JSON 块之后、`</finding_submission>` 之前）加说明段：

```
<dataflow_steps_guidance>
`dataflow_steps`：按污点传播顺序，列出从 source 到 sink 经过的每个节点。
- 每个节点给 `label`（函数名或调用点）、`file`、`line`（未知填 null）。
- 若该节点有防护（sanitizer / 编码 / 校验），在 `protection` 填防护名；无防护填 null。
- 防护有效则该节点之后不应再出现未防护的传播——防护被绕过则继续列后续节点。
- 只列你实际追踪到的节点；没有完整路径时留空数组或省略。
</dataflow_steps_guidance>
```

> 守铁律：说明只引导 agent「自己追的链填 steps」，不引任何确定性层产物（parameter_graph 等）。`tests/prompts/test_static_dataflow_hints_decoupling.py` 维持绿（该测试断言 prompt 不含确定性 hints 文件名/产物，dataflow_steps 是 agent 自产字段不触发）。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py/packages/core && python -m pytest tests/prompts/test_vuln_host_rendered.py tests/prompts/test_static_dataflow_hints_decoupling.py -v`
Expected: 全 PASS（含铁律锁定测试）。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add prompts/vuln-injection.txt prompts/vuln-xss.txt prompts/vuln-ssrf.txt packages/core/tests/prompts/test_vuln_host_rendered.py
git commit -m "feat(prompts): P2 inj/xss/ssrf finding_submission 加 dataflow_steps 提交说明"
```

---

### Task 6: P3 — executor 落 `{vc}_safe_vectors.json`（同步落盘）

**Files:**
- Modify: `packages/core/src/supernova_core/agents/executor.py`（queue 落盘 L421–429；payload_bag L333）
- Test: `packages/core/tests/agents/test_executor_safe_vectors_dump.py`（新建）

**Interfaces:**
- Consumes: `payload_bag = collector.get_all()`（executor.py:333），`payload_bag["safe_vectors"]`（collectors/vuln.py:476 `set_safe_vectors` section_key=`safe_vectors`，数据 shape `{"vectors":[{subject,location,defense_mechanism,render_context?}]}`）；`intermediate_path`（paths.py:151）；`atomic_write_json`（atomic_write.py:7）。落盘门控同 queue-write（L336–342：`not skip_artifact_postprocess AND agent_name.value.endswith("-vuln") AND result.structured_output is None`）。
- Produces: `intermediate/{vc}_safe_vectors.json`（`vc = agent_name.value.removesuffix("-vuln")`），shape = collector `safe_vectors` 原样（`{"vectors":[...]}`）。P4 组装器读此构建 `safe_vectors` 顶层区 + 匹配 sink 树的单节点 safe 枝。

- [ ] **Step 1: Write the failing test**

`packages/core/tests/agents/test_executor_safe_vectors_dump.py`：

```python
"""P3: executor 落 queue 时同步落 {vc}_safe_vectors.json。"""
import json
from pathlib import Path
from unittest.mock import MagicMock


def test_safe_vectors_dumped_alongside_queue(tmp_path: Path):
    from supernova_core.agents import executor

    deliverables = tmp_path
    (deliverables / "intermediate").mkdir()
    # collector payload bag with safe_vectors
    collector = MagicMock()
    collector.get_all.return_value = {
        "submitted_findings": [{"ID": "V1", "vulnerability_type": "injection",
                                "externally_exploitable": True, "confidence": "high"}],
        "findings_summary": {"finding_roster": [{"id": "V1", "title": "t"}]},
        "safe_vectors": {"vectors": [
            {"subject": "req.query.id", "location": "a.js:10", "defense_mechanism": "parseInt"},
        ]},
    }

    # 调被测落盘纯函数（见 Step 3）
    from supernova_core.agents.executor import _dump_safe_vectors
    _dump_safe_vectors(deliverables, "injection", collector.get_all())

    sv_path = deliverables / "intermediate" / "injection_safe_vectors.json"
    assert sv_path.exists()
    data = json.loads(sv_path.read_text(encoding="utf-8"))
    assert data["vectors"][0]["defense_mechanism"] == "parseInt"


def test_safe_vectors_skipped_when_empty(tmp_path: Path):
    """safe_vectors 缺失/空 → 不落盘（不产空文件）。"""
    from supernova_core.agents.executor import _dump_safe_vectors
    _dump_safe_vectors(tmp_path, "ssrf", {"safe_vectors": {"vectors": []}})
    assert not (tmp_path / "intermediate" / "ssrf_safe_vectors.json").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py/packages/core && python -m pytest tests/agents/test_executor_safe_vectors_dump.py -v`
Expected: FAIL（`_dump_safe_vectors` 不存在）。

- [ ] **Step 3: Write minimal implementation**

`packages/core/src/supernova_core/agents/executor.py` — 在 queue-write 块（L421–429）之后、同一 collector 分支内，调纯函数：

模块级新增：

```python
def _dump_safe_vectors(deliverables: Path, vc: str, payload_bag: dict) -> None:
    """P3: 同步落 intermediate/{vc}_safe_vectors.json（组装器需结构化源）。

    空/缺失不落盘。门控由调用方（execute 的 collector 分支）保证。
    """
    sv = payload_bag.get("safe_vectors")
    if not sv:
        return
    vectors = sv.get("vectors") if isinstance(sv, dict) else sv
    if not vectors:
        return
    from supernova_core.utils.paths import intermediate_path
    from supernova_core.utils.atomic_write import atomic_write_json
    atomic_write_json(
        intermediate_path(deliverables, f"{vc}_safe_vectors.json"),
        {"vectors": vectors},
    )
```

在 `execute` 的 queue-write 之后（L429 `logger.info(...)` 之后、collector 分支闭合 `}` 之前）：

```python
                _dump_safe_vectors(deliverables, agent_name.value.removesuffix("-vuln"),
                                   payload_bag)
```

> `vc` 派生用 `agent_name.value.removesuffix("-vuln")`（与 executor 现有 `_targeted_recheck` 同款写法 L158）。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py/packages/core && python -m pytest tests/agents/test_executor_safe_vectors_dump.py -v`
Expected: 2 PASS。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/supernova_core/agents/executor.py packages/core/tests/agents/test_executor_safe_vectors_dump.py
git commit -m "feat(core): P3 executor 同步落 {vc}_safe_vectors.json（组装器结构化源）"
```

---

### Task 7: P4 — core 组装器 `services/dataflow_view.py`（纯函数，双轨×降级矩阵）

**Files:**
- Create: `packages/core/src/supernova_core/services/dataflow_view.py`
- Test: `packages/core/tests/services/test_dataflow_view_assemble.py`（新建）

**Interfaces:**
- Consumes: 5 类产物（均经 `resolve_intermediate(whitebox_dir, name)` 读，缺失返回 None 降级）：
  1. `{vc}_exploitation_queue.json`（SSOT 合并 queue，`{"vulnerabilities":[{...model_dump, dataflow_steps?}]}`）——LLM finding 兜底源（也是合并后 base）
  2. `{vc}_chain_verdicts.json`（Task 2 产出，`{"verdicts":[{flow_id,verdict,reason,sanitizer_annotations,confidence,sink_call_site_id}]}`）——GitNexus 枝 verdict
  3. `parameter_graph.json`（`taint_flows:[{flow_id,source_param,source_type,propagation_steps:[{code_location,transformation,intermediate_vars,from_func_id,to_func_id}],sink_call_site_id}]`）
  4. `code_index.json`（`blocks:[{FuncBlock: id,file_path,function_name,start_line,end_line,source_code}]` + `sink_call_sites:[{id,callee_name,category,rule_id,file_path,line}]` + `source_points:[{id,param_name,source_type,expression,file_path,line}]`）
  5. `{vc}_safe_vectors.json`（Task 6 产出，`{"vectors":[{subject,location,defense_mechanism,render_context?}]}`）
  - LLM 原始兜底：`{vc}_llm_queue.json`（merge 前原始 LLM 产物，`run_merge_dual_track_queues` L954–957 保的副本）
- Produces: `dict`（`dataflow_view.json` shape，spec §3 schema_version=1：`summary`/`trees`/`control_findings`/`safe_vectors`），由 P4 活动调 `atomic_write_json` 落盘。`trees[].branches[].track ∈ {"gitnexus","llm"}`，`verdict ∈ {"vulnerable","safe","unknown"}`。

**聚合规则**（spec §3 组装器核心逻辑，本任务逐条实现）：
1. 树粒度 = sink。GitNexus 枝按 `sink_call_site_id` 精确聚合；chain_verdicts 的 safe 枝也进树（safe-only 树 `findings: []`）。
2. LLM finding 挂树：取 `dataflow_steps` 末节点为 sink 位，按 `(vuln_class, sink file:line 规范化)` 与 GitNexus sink 对齐（location 规范化复用 `dual_track_merger._finding_key` 思路——按多字段元组，非严格 file:line）；对不上则自立 `track=llm` 树。LLM safe 枝来自 safe_vectors：匹配到 sink 树→挂单节点 safe 枝；匹配不上→顶层 `safe_vectors` 区。
3. 代码片段：节点 `code` 从 `code_index.blocks` 按 `code_location`（`file:line`）截 ±5 行（≤10 行）。体积控制：只给有故事的节点存 code（source/sink/transformation 非空/sanitizer 所在步），纯透传步 `has_code:false`。LLM 枝节点无源码→`has_code:false`。
4. 二阶链（`2ND-GN-*`）：挂 read-side sink 树，`source.type="storage"`。
5. auth/authz（`control_findings`）：从 exploitation_queue 的 auth/authz finding 取 `endpoint`/`guard_evidence`/`missing_defense`/`mismatch_reason` + `vulnerable_code_location` 组关卡链（`chain[].status ∈ {ok,missing,ineffective}`）。

**降级矩阵**（spec §6）：parameter_graph 缺→GitNexus 枝保留无中间节点；code_index 缺/纯透传→`has_code:false`；LLM finding 无 steps→source→sink 直连；全部产物缺→返回 `None`（不产文件）。

- [ ] **Step 1: Write the failing test — fixture 矩阵**

`packages/core/tests/services/test_dataflow_view_assemble.py`——用最小 fixture 覆盖双轨全量 / 单轨缺位 / safe-only 树 / control_findings / 降级。先写双轨全量 + 降级两个（其余在 Step 3 补）：

```python
"""P4 组装器 fixture 矩阵。"""
import json
from pathlib import Path

import pytest


def _write_intermediate(d: Path, name: str, obj):
    (d / "intermediate").mkdir(exist_ok=True)
    (d / "intermediate" / name).write_text(json.dumps(obj), encoding="utf-8")


@pytest.fixture
def deliverables(tmp_path: Path) -> Path:
    (tmp_path / "intermediate").mkdir()
    return tmp_path


def test_dual_track_full(deliverables: Path):
    """GitNexus 枝（verdict from chain_verdicts）+ LLM 枝（dataflow_steps）同树。"""
    _write_intermediate(deliverables, "injection_chain_verdicts.json", {"verdicts": [
        {"flow_id": "u1->s1", "sink_call_site_id": "s1", "verdict": "vulnerable",
         "reason": "", "sanitizer_annotations": [], "confidence": "high", "vuln_class": "injection"},
    ]})
    _write_intermediate(deliverables, "parameter_graph.json", {"taint_flows": [{
        "flow_id": "u1->s1", "entry_point_id": "ep1", "source_param": "name",
        "source_type": "query", "sink_call_site_id": "s1",
        "propagation_steps": [
            {"from_func_id": "ep1", "to_func_id": "ctrl", "code_location": "c.js:25",
             "transformation": "concat", "intermediate_vars": ["q"]},
        ],
    }]})
    _write_intermediate(deliverables, "code_index.json", {
        "blocks": [{"id": "c.js:ctrl:25", "file_path": "c.js", "function_name": "ctrl",
                    "start_line": 20, "end_line": 30, "source_code": "\n".join(f"l{i}" for i in range(20, 31))}],
        "sink_call_sites": [{"id": "s1", "callee_name": "execute", "category": "SQL",
                             "rule_id": "py-sql-raw", "file_path": "app/db.py", "line": 42, "column": 0}],
        "source_points": [],
    })
    _write_intermediate(deliverables, "injection_exploitation_queue.json", {"vulnerabilities": [
        {"ID": "INJ-VULN-01", "vulnerability_type": "injection", "externally_exploitable": True,
         "confidence": "high", "merge_source": "both", "title": "sqli",
         "dataflow_steps": [{"label": "ctrl", "file": "c.js", "line": 25, "protection": None}]},
    ]})
    _write_intermediate(deliverables, "injection_safe_vectors.json", {"vectors": []})

    from supernova_core.services.dataflow_view import assemble_dataflow_view
    view = assemble_dataflow_view(deliverables)
    assert view is not None
    assert view["schema_version"] == 1
    assert view["summary"]["vulnerable_sinks"] >= 1
    tree = view["trees"][0]
    assert tree["sink"]["label"] == "execute"
    tracks = {b["track"] for b in tree["branches"]}
    assert tracks == {"gitnexus", "llm"}
    gn = next(b for b in tree["branches"] if b["track"] == "gitnexus")
    assert gn["verdict"] == "vulnerable"
    assert len(gn["nodes"]) == 1


def test_all_products_missing_returns_none(deliverables: Path):
    """全部产物缺 → None（不产文件）。"""
    from supernova_core.services.dataflow_view import assemble_dataflow_view
    assert assemble_dataflow_view(deliverables) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py/packages/core && python -m pytest tests/services/test_dataflow_view_assemble.py -v`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: Write minimal implementation**

`packages/core/src/supernova_core/services/dataflow_view.py`：

```python
"""数据流视图组装器（方案 B 写时组装）。

纯函数：读 5 类 intermediate 产物 + LLM 兜底，组装 dataflow_view.json schema。
失败由调用方（whitebox 活动）兜——本函数不抛扫描级异常，但产物缺全返 None。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from supernova_core.utils.paths import resolve_intermediate

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
_VULN_CLASSES = ("injection", "xss", "ssrf")
_CONTROL_CLASSES = ("auth", "authz")


def assemble_dataflow_view(whitebox_dir: Path) -> dict | None:
    """读 5 类产物组装 dataflow_view。全部缺 → None。"""
    code_index = _load_json(whitebox_dir, "code_index.json")
    pgraph = _load_json(whitebox_dir, "parameter_graph.json")
    if code_index is None and pgraph is None:
        # 没有确定性层 → 只靠 LLM 产物（可能仍有 LLM 枝）
        pass

    blocks_by_loc = _index_blocks(code_index) if code_index else {}
    sinks = {s["id"]: s for s in code_index.get("sink_call_sites", [])} if code_index else {}
    flows = {f["flow_id"]: f for f in pgraph.get("taint_flows", [])} if pgraph else {}

    trees: list[dict] = []
    control_findings: list[dict] = []

    for vc in _VULN_CLASSES:
        verdicts = _load_json(whitebox_dir, f"{vc}_chain_verdicts.json") or {}
        vrows = {v["flow_id"]: v for v in verdicts.get("verdicts", [])}
        queue = (_load_json(whitebox_dir, f"{vc}_exploitation_queue.json")
                 or _load_json(whitebox_dir, f"{vc}_llm_queue.json") or {})
        findings = queue.get("vulnerabilities", []) if isinstance(queue, dict) else []

        trees += _build_taint_trees(vc, findings, vrows, flows, sinks, blocks_by_loc)

        sv = _load_json(whitebox_dir, f"{vc}_safe_vectors.json")
        if sv:
            _attach_safe_vectors(trees, sv, vc)

    for vc in _CONTROL_CLASSES:
        queue = (_load_json(whitebox_dir, f"{vc}_exploitation_queue.json")
                 or _load_json(whitebox_dir, f"{vc}_llm_queue.json") or {})
        findings = queue.get("vulnerabilities", []) if isinstance(queue, dict) else []
        control_findings += _build_control_findings(vc, findings)

    safe_vectors_top = _collect_safe_vectors_top(whitebox_dir, trees)

    if not trees and not control_findings and not safe_vectors_top:
        return None

    return {
        "schema_version": SCHEMA_VERSION,
        "summary": _summarize(trees, control_findings),
        "trees": trees,
        "control_findings": control_findings,
        "safe_vectors": safe_vectors_top,
    }
```

> 实现体量大，**按 spec §3 聚合规则逐条实现**辅助函数 `_build_taint_trees` / `_attach_safe_vectors` / `_build_control_findings` / `_collect_safe_vectors_top` / `_index_blocks` / `_summarize` / `_load_json` / `_code_snippet`（±5 行截取）。每个辅助函数对应 spec 一条规则。**实现细节多但都是机械的 dict 组装**——参考 spec §3 数据契约的字段名。location 规范化复用 `dual_track_merger._finding_key` 的多字段元组思路（按 `vulnerability_type` + `(source, endpoint, source_endpoint, vulnerable_code_location, path)` + `(sink_call, sink_function, vulnerable_parameter)` 元组对齐，非严格 file:line）。

- [ ] **Step 4: 补 fixture 矩阵测试（safe-only 树 / control / 降级）**

补用例（最小）：

```python
def test_safe_only_tree_has_empty_findings(deliverables: Path):
    """safe 枝也进树 → safe-only 树 findings=[]。"""
    _write_intermediate(deliverables, "injection_chain_verdicts.json", {"verdicts": [
        {"flow_id": "u2->s2", "sink_call_site_id": "s2", "verdict": "safe",
         "reason": "shlex.quote", "sanitizer_annotations": [], "confidence": "high", "vuln_class": "injection"},
    ]})
    _write_intermediate(deliverables, "parameter_graph.json", {"taint_flows": [
        {"flow_id": "u2->s2", "sink_call_site_id": "s2", "source_param": "q", "source_type": "query",
         "propagation_steps": [{"code_location": "h.js:22", "transformation": "sanitize_hint:shlex.quote"}]}]})
    _write_intermediate(deliverables, "code_index.json", {
        "blocks": [], "sink_call_sites": [{"id": "s2", "callee_name": "exec", "category": "COMMAND",
                                          "rule_id": "r", "file_path": "d.py", "line": 5, "column": 0}], "source_points": []})
    from supernova_core.services.dataflow_view import assemble_dataflow_view
    view = assemble_dataflow_view(deliverables)
    tree = next(t for t in view["trees"] if t["sink"]["line"] == 5)
    assert tree["findings"] == []
    safe_branch = tree["branches"][0]
    assert safe_branch["verdict"] == "safe"


def test_control_findings_authz(deliverables: Path):
    """authz → control_findings 关卡链。"""
    _write_intermediate(deliverables, "authz_exploitation_queue.json", {"vulnerabilities": [
        {"ID": "AUTHZ-01", "vulnerability_type": "authz", "endpoint": "PUT /api/orders/:id",
         "externally_exploitable": True, "confidence": "high",
         "guard_evidence": "无 owner 检查", "missing_defense": "owner check",
         "vulnerable_code_location": "c.js:40"}]})
    from supernova_core.services.dataflow_view import assemble_dataflow_view
    view = assemble_dataflow_view(deliverables)
    cf = view["control_findings"][0]
    assert cf["endpoint"] == "PUT /api/orders/:id"
    assert cf["chain"][0]["status"] == "missing"


def test_code_snippet_volume_control(deliverables: Path):
    """纯透传步 has_code:false；有故事的步 has_code:true。"""
    _write_intermediate(deliverables, "parameter_graph.json", {"taint_flows": [
        {"flow_id": "f1", "sink_call_site_id": "sk1",
         "propagation_steps": [
             {"code_location": "p.js:1", "transformation": None},          # 透传
             {"code_location": "p.js:5", "transformation": "concat"}]}}]})
    _write_intermediate(deliverables, "code_index.json", {
        "blocks": [{"id": "p.js:fn:1", "file_path": "p.js", "function_name": "fn",
                    "start_line": 1, "end_line": 10, "source_code": "\n".join(f"l{i}" for i in range(1, 11))}],
        "sink_call_sites": [{"id": "sk1", "callee_name": "x", "category": "SQL",
                             "rule_id": "r", "file_path": "p.js", "line": 9, "column": 0}], "source_points": []})
    _write_intermediate(deliverables, "injection_chain_verdicts.json", {"verdicts": [
        {"flow_id": "f1", "sink_call_site_id": "sk1", "verdict": "vulnerable", "reason": "",
         "sanitizer_annotations": [], "confidence": "high", "vuln_class": "injection"}]})
    from supernova_core.services.dataflow_view import assemble_dataflow_view
    view = assemble_dataflow_view(deliverables)
    nodes = view["trees"][0]["branches"][0]["nodes"]
    assert nodes[0]["has_code"] is False
    assert nodes[1]["has_code"] is True
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /root/shannon-py/packages/core && python -m pytest tests/services/test_dataflow_view_assemble.py -v`
Expected: 全 PASS。逐个 fixture 调通组装器辅助函数。

- [ ] **Step 6: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/supernova_core/services/dataflow_view.py packages/core/tests/services/test_dataflow_view_assemble.py
git commit -m "feat(core): P4 数据流视图组装器（纯函数，双轨×降级矩阵）"
```

---

### Task 8: P4 — whitebox `run_assemble_dataflow_view` 活动 + workflow 接线

**Files:**
- Modify: `packages/whitebox/src/supernova_whitebox/pipeline/activities.py`（新增活动，模板 `run_save_adjudication` L838–855；路径 helper `_get_paths` L46–68）
- Modify: `packages/whitebox/src/supernova_whitebox/pipeline/workflows.py`（merge 后接线 L481–486，non-fatal 模式 L510–541）
- Test: `packages/whitebox/tests/pipeline/test_assemble_dataflow_view_activity.py`（新建）

**Interfaces:**
- Consumes: Task 7 `assemble_dataflow_view(whitebox_dir: Path) -> dict | None`；`_get_paths(input) -> (repo, deliverables, workspaces)`（activities.py:46，`deliverables` 已是 `deliverables/whitebox/` 桶根）；`atomic_write_json(path, data)`（atomic_write.py:7）；`intermediate_path(track_dir, filename)`（paths.py:151）；`ActivityInput`。
- Produces: temporalio 活动 `run_assemble_dataflow_view(input: ActivityInput) -> dict`，返回 `{"status": "ok"|"skipped", "trees": N, ...}`。失败 → `log.warning` + 不产文件 + 不抛（non-fatal）。

- [ ] **Step 1: Write the failing test**

`packages/whitebox/tests/pipeline/test_assemble_dataflow_view_activity.py`：

```python
"""P4: 组装活动失败不阻塞，成功落 dataflow_view.json。"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def deliverables_with_products(tmp_path: Path) -> Path:
    (tmp_path / "intermediate").mkdir()
    (tmp_path / "intermediate" / "injection_chain_verdicts.json").write_text(
        json.dumps({"verdicts": []}))
    return tmp_path


def test_activity_writes_dataflow_view(deliverables_with_products: Path):
    from supernova_whitebox.pipeline import activities

    view = {"schema_version": 1, "summary": {}, "trees": [], "control_findings": [], "safe_vectors": []}
    with patch("supernova_core.services.dataflow_view.assemble_dataflow_view", return_value=view):
        with patch.object(activities, "_get_paths", return_value=(Path("/r"), deliverables_with_products, Path("/w"))):
            from supernova_whitebox.pipeline.activities import run_assemble_dataflow_view, _ActivityInputStub
            # ActivityInput 构造较重，用 stub 或直接 monkeypatch
            result = run_assemble_dataflow_view(input=_ActivityInputStub())
    assert result["status"] == "ok"
    assert (deliverables_with_products / "intermediate" / "dataflow_view.json").exists()


def test_activity_non_fatal_on_exception(deliverables_with_products: Path):
    """组装器抛 → warning + 不产文件 + 不阻塞（不 raise ApplicationFailure）。"""
    from supernova_whitebox.pipeline import activities
    with patch("supernova_core.services.dataflow_view.assemble_dataflow_view", side_effect=RuntimeError("boom")):
        with patch.object(activities, "_get_paths", return_value=(Path("/r"), deliverables_with_products, Path("/w"))):
            from supernova_whitebox.pipeline.activities import run_assemble_dataflow_view
            result = run_assemble_dataflow_view(input=object())
    assert result["status"] == "skipped"
    assert not (deliverables_with_products / "intermediate" / "dataflow_view.json").exists()


def test_activity_skipped_when_assembler_returns_none(deliverables_with_products: Path):
    """组装器返 None（全产物缺）→ skipped 不落盘。"""
    from supernova_whitebox.pipeline import activities
    with patch("supernova_core.services.dataflow_view.assemble_dataflow_view", return_value=None):
        with patch.object(activities, "_get_paths", return_value=(Path("/r"), deliverables_with_products, Path("/w"))):
            from supernova_whitebox.pipeline.activities import run_assemble_dataflow_view
            result = run_assemble_dataflow_view(input=object())
    assert result["status"] == "skipped"
```

> `_ActivityInputStub`：若 `run_assemble_dataflow_view` 内只用 `_get_paths(input)`，可直接传 `object()` 并 patch `_get_paths` 忽略 input。调整实现使 input 解耦（`_get_paths` 接受任意带 `workspace_path`/`deliverables_subdir` 的对象，或测试直接 patch `_get_paths` 绕过）。

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py/packages/whitebox && python -m pytest tests/pipeline/test_assemble_dataflow_view_activity.py -v`
Expected: FAIL（`run_assemble_dataflow_view` 不存在）。

- [ ] **Step 3: Write minimal implementation**

`packages/whitebox/src/supernova_whitebox/pipeline/activities.py`——照 `run_save_adjudication`（L838–855）模板，但**不抛 ApplicationFailure**（non-fatal）：

```python
@activity.defn
async def run_assemble_dataflow_view(input: ActivityInput) -> dict:
    """P4: 组装 dataflow_view.json（失败不阻塞扫描）。"""
    try:
        from supernova_core.services.dataflow_view import assemble_dataflow_view
        from supernova_core.utils.paths import intermediate_path
        from supernova_core.utils.atomic_write import atomic_write_json

        _repo, deliverables, _ws = _get_paths(input)
        view = assemble_dataflow_view(deliverables)
        if view is None:
            return {"status": "skipped", "reason": "no products"}
        atomic_write_json(intermediate_path(deliverables, "dataflow_view.json"), view)
        return {"status": "ok", "trees": len(view.get("trees", []))}
    except Exception as exc:  # noqa: BLE001 — non-blocking
        log.warning("run_assemble_dataflow_view failed (non-blocking): %s", exc)
        return {"status": "skipped", "reason": str(exc)}
```

`workflows.py`——在 `run_merge_dual_track_queues`（L481–486）之后插 non-fatal 调用（照 L510–541 attack-chain 模式）：

```python
    try:
        await workflow.execute_activity(
            activities.run_assemble_dataflow_view, act_input,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=retry_for("standard"),
        )
    except Exception as exc:
        await workflow.execute_activity(
            activities.log_info_activity,
            ActivityInput(**{**act_input.__dict__,
                "info_message": f"dataflow view assembly failed (non-fatal): {exc}",
                "info_level": "warning"}),
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=retry_for("log"),
        )
```

> 活动内部已吞异常返 `skipped`，外层 try/except 是双保险（对齐 attack-chain 套路）。`timedelta`/`retry_for` 从 workflows.py 现有 import 取。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py/packages/whitebox && python -m pytest tests/pipeline/test_assemble_dataflow_view_activity.py -v`
Expected: 3 PASS。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/whitebox/src/supernova_whitebox/pipeline/activities.py packages/whitebox/src/supernova_whitebox/pipeline/workflows.py packages/whitebox/tests/pipeline/test_assemble_dataflow_view_activity.py
git commit -m "feat(whitebox): P4 run_assemble_dataflow_view 活动 + workflow 接线（失败不阻塞）"
```

---

### Task 9: P5 — web 端点 `GET /api/workspaces/{ws}/scans/{scan_id}/dataflow`

**Files:**
- Modify: `packages/web/src/supernova_web/api/scans.py`（router L23 prefix `/api/workspaces`；scan 定位 `_scan_dir_or_404` L58–63；读产物惯例 `resolve_intermediate` from supernova_core.utils.paths）
- Test: `packages/web/tests/api/test_scans_dataflow.py`（新建）

**Interfaces:**
- Consumes: `resolve_intermediate(track_dir, filename) -> Path | None`（paths.py:156，先 `intermediate/` 再平铺）；`_scan_dir_or_404(request, ws, scan_id)`（scans.py:58）；`workspace_member` 鉴权依赖。`whitebox_dir` 桶根 = `scan_dir / "deliverables" / "whitebox"`（`WHITEBOX_SUBDIR` 常量 paths.py:109）。
- Produces: `GET /api/workspaces/{ws}/scans/{scan_id}/dataflow` → 200 `{dataflow_view.json 内容}` / 404 `{"detail":"dataflow view not generated"}`。缺产物走 tier fallback：`resolve_intermediate` 返 None → 404。

- [ ] **Step 1: Write the failing test**

`packages/web/tests/api/test_scans_dataflow.py`：

```python
"""P5: dataflow 端点 200 / 404 / tier fallback。"""
import json
from pathlib import Path

from fastapi.testclient import TestClient


def _make_scan(tmp_path: Path, ws="w1", scan_id="s1") -> Path:
    from supernova_web.app import create_app
    # 复用现有测试 fixture 惯例（见 test_scans.py 现有 scan 目录构造）
    ...


def test_dataflow_200(client, scan_with_dataflow):
    # scan_with_dataflow fixture: 在 deliverables/whitebox/intermediate/dataflow_view.json 放内容
    resp = client.get("/api/workspaces/w1/scans/s1/dataflow")
    assert resp.status_code == 200
    assert resp.json()["schema_version"] == 1


def test_dataflow_404_when_missing(client, scan_without_dataflow):
    resp = client.get("/api/workspaces/w1/scans/s1/dataflow")
    assert resp.status_code == 404
    assert "not generated" in resp.json()["detail"].lower()


def test_dataflow_tier_fallback_flat(client, scan_flat_dataflow):
    # 旧扫描平铺：deliverables/whitebox/dataflow_view.json（无 intermediate/）
    resp = client.get("/api/workspaces/w1/scans/s1/dataflow")
    assert resp.status_code == 200
```

> fixture 构造照 `tests/api/test_scans.py` 现有 scan 目录搭建惯例（`_store`/`ScanStore` 注入 tmp workspace）。若现有测试用 conftest fixture 注入 client + scan_dir，复用之。

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py/packages/web && python -m pytest tests/api/test_scans_dataflow.py -v`
Expected: FAIL（端点不存在 → 404 路由错）。

- [ ] **Step 3: Write minimal implementation**

`packages/web/src/supernova_web/api/scans.py`——新增 helper + 端点（照 `scan_deliverables_summary` L374–378 模板）：

模块级 helper：

```python
def _dataflow_view_for(scan_dir: Path):
    from supernova_core.utils.paths import resolve_intermediate, WHITEBOX_SUBDIR
    wb_dir = scan_dir / "deliverables" / WHITEBOX_SUBDIR
    path = resolve_intermediate(wb_dir, "dataflow_view.json")
    if path is None or not path.exists():
        raise HTTPException(404, "dataflow view not generated")
    import json
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(404, "dataflow view not generated")
```

端点：

```python
@router.get("/{ws}/scans/{scan_id}/dataflow")
async def scan_dataflow(ws: str, scan_id: str, request: Request,
                        _: User = Depends(workspace_member)):
    return _dataflow_view_for(_scan_dir_or_404(request, ws, scan_id))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py/packages/web && python -m pytest tests/api/test_scans_dataflow.py -v`
Expected: 3 PASS（200 / 404 / tier fallback）。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/web/src/supernova_web/api/scans.py packages/web/tests/api/test_scans_dataflow.py
git commit -m "feat(web): P5 GET /scans/{id}/dataflow 端点（tier fallback + 404）"
```

---

### Task 10: P6 — 双引擎真机探针（dataflow_steps 硬验收）

**Files:**
- Create: `scripts/validate_claude_dataflow_probe.py`
- Create: `scripts/validate_openai_dataflow_probe.py`
- Test: 探针不进 pytest（真机脚本，手动跑）；可在 `scripts/README` 或探针 docstring 记录复现步骤。

**Interfaces:**
- Consumes: Task 3/4 的 `dataflow_steps` schema + parse_lenient；双引擎经 `run_claude_prompt` 统一抽象（CLAUDE.md §2）。探针用最小 vuln prompt 片段驱动 GLM 在两引擎下产 `submit_finding` 含 `dataflow_steps`。
- Produces: 探针 exit code 0 = GLM 在该引擎产含 `dataflow_steps` 的提交（硬验收）；非 0 = 失败。参照 `scripts/validate_*_task_probe.py` 现有探针惯例。

- [ ] **Step 1: 写 claude 探针**

`scripts/validate_claude_dataflow_probe.py`——照 `scripts/validate_glm_task_probe.py`（CLAUDE.md §2 提及）惯例：构造最小 prompt（含 finding_submission 段 + dataflow_steps 指引）→ claude-agent-sdk 跑 → 断言 collector `submitted_findings` 有条目且 `dataflow_steps` 非空 list。

```python
#!/usr/bin/env python3
"""P6 硬验收：claude 引擎下 GLM 产出含 dataflow_steps 的 submit_finding。

复现：SUPERNOVA_AI_PROVIDER=glm-anthropic python scripts/validate_claude_dataflow_probe.py
exit 0 = 通过。
"""
# 详见 scripts/validate_glm_task_probe.py 的 provider 初始化 + run_claude_prompt 调用。
# 断言：result collector.submitted_findings[0]["dataflow_steps"] is a non-empty list
```

> 实现照 `scripts/validate_glm_task_probe.py` 现有结构（provider init、prompt 装载、断言打印）。该探针文件较长，**关键差异点**：prompt 装载 vuln-injection.txt 的 finding_submission 段 + 一个最小 source→sink 场景描述；断言改查 `dataflow_steps` 字段。

- [ ] **Step 2: 写 openai 探针**

`scripts/validate_openai_dataflow_probe.py`——同上但 `SUPERNOVA_AI_PROVIDER=glm-openai`，照 `scripts/validate_openai_task_probe.py` 惯例。断言相同。

- [ ] **Step 3: 真机跑（手动，非 CI）**

```bash
cd /root/shannon-py
SUPERNOVA_AI_PROVIDER=glm-anthropic python scripts/validate_claude_dataflow_probe.py
SUPERNOVA_AI_PROVIDER=glm-openai python scripts/validate_openai_dataflow_probe.py
```
Expected: 两探针 exit 0，GLM 产出含 `dataflow_steps` 的提交。若失败，回查 Task 3/4 schema 与 prompt 指引是否被 GLM 正确解析。

> 真机探针是 spec §4 P6 硬验收项——计划标记 Task 完成需两探针 exit 0。若环境 GLM 不可用，至少 `--dry-run` 校验 prompt 装载 + schema 一致（不调真 LLM）。

- [ ] **Step 4: Commit**

```bash
cd /root/shannon-py
git add scripts/validate_claude_dataflow_probe.py scripts/validate_openai_dataflow_probe.py
git commit -m "feat(scripts): P6 双引擎 dataflow_steps 真机探针（硬验收）"
```

---

### Task 11: 前端 — router + DataFlowTab 骨架 + 两栏布局

**Files:**
- Modify: `packages/web/frontend/src/routes/.../router.tsx`（ScanDetail children，现有 deliverables/report child 作模板）
- Create: `packages/web/frontend/src/routes/WorkspaceDetail/DataFlowTab.tsx`
- Modify: `packages/web/frontend/src/api/client.ts` + `api/types.ts`（dataflow 类型 + fetcher）
- Test: `packages/web/frontend/src/routes/WorkspaceDetail/__tests__/DataFlowTab.test.tsx`（新建）

**Interfaces:**
- Consumes: SWR fetcher（`api/client.ts` 现有 `apiGet<T>(path)`，path 不含 `/api` 前缀）；scan route params `{ws, scanId}`；i18n `useTranslation`。
- Produces: `DataFlowTab` 懒加载路由 `.../dataflow`；`useDataflowView(ws, scanId)` SWR hook 返回 `{data, error, isLoading}`；`DataflowView` TS 类型（对齐 spec §3 schema）。左目录侧栏 + 右内容区两栏骨架。

- [ ] **Step 1: Write the failing test**

`packages/web/frontend/src/routes/WorkspaceDetail/__tests__/DataFlowTab.test.tsx`：

```tsx
import { describe, it, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { DataFlowTab } from "../DataFlowTab";
import { DataflowView } from "../../../api/types";

const mockView: DataflowView = {
  schema_version: 1,
  summary: { total_sinks: 1, vulnerable_sinks: 1, safe_only_sinks: 0 },
  trees: [], control_findings: [], safe_vectors: [],
};

// msw handler: GET /api/workspaces/w1/scans/s1/dataflow → 200 mockView
describe("DataFlowTab", () => {
  it("renders summary bar with counts", async () => {
    render(<DataFlowTab ws="w1" scanId="s1" />);
    await waitFor(() => {
      expect(screen.getByText(/数据流/)).toBeInTheDocument();
    });
  });

  it("shows empty state when 404", async () => {
    // msw handler → 404
    render(<DataFlowTab ws="w1" scanId="s1" />);
    await waitFor(() => {
      expect(screen.getByText(/无数据流视图/)).toBeInTheDocument();
    });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/vitest src/routes/WorkspaceDetail/__tests__/DataFlowTab.test.tsx --run`
Expected: FAIL（DataFlowTab 未定义）。

- [ ] **Step 3: Write minimal implementation**

(a) `api/types.ts`——加类型（对齐 spec §3）：

```ts
export interface DataflowNode {
  func: string; file: string; line: number | null;
  transformation?: string | null; intermediate_vars?: string[];
  code?: string | null; has_code: boolean;
}
export interface DataflowBranch {
  branch_id: string; track: "gitnexus" | "llm";
  verdict: "vulnerable" | "safe" | "unknown"; verdict_reason?: string;
  source: { label: string; type: string; entry?: string; file?: string; line?: number | null };
  nodes: DataflowNode[]; sanitizers?: { name: string; defense_type?: string; file?: string; line?: number | null; effective: boolean }[];
}
export interface DataflowTree {
  tree_id: string; vuln_class: string;
  sink: { label: string; file: string; line: number; rule_id?: string; category?: string; code?: string };
  findings: { id: string; merge_source?: string; title?: string; confidence?: string; witness_payload?: string; mismatch_reason?: string }[];
  branches: DataflowBranch[];
}
export interface ControlFinding {
  id: string; vuln_class: string; endpoint: string;
  chain: { label: string; status: "ok" | "missing" | "ineffective"; detail?: string; file?: string; line?: number | null }[];
}
export interface DataflowView {
  schema_version: number;
  summary: { total_sinks: number; vulnerable_sinks: number; safe_only_sinks: number };
  trees: DataflowTree[]; control_findings: ControlFinding[];
  safe_vectors: { subject: string; location: string; defense_mechanism: string; render_context?: string }[];
}
```

(b) `api/client.ts`——加 fetcher（照现有 scan deliverables fetcher）：

```ts
export const fetchDataflowView = (ws: string, scanId: string) =>
  apiGet<DataflowView>(`/workspaces/${encWs(ws)}/scans/${encWs(scanId)}/dataflow`);
```

(c) `DataFlowTab.tsx`——骨架 + SWR：

```tsx
import useSWR from "swr";
import { fetchDataflowView } from "../../../api/client";
import { TocSideBar } from "../../../components/dataflow/TocSideBar";

export function DataFlowTab({ ws, scanId }: { ws: string; scanId: string }) {
  const { data, error, isLoading } = useSWR(
    ["dataflow", ws, scanId], () => fetchDataflowView(ws, scanId));
  if (error?.status === 404) return <EmptyState />;
  if (isLoading) return <Loading />;
  if (!data) return <EmptyState />;
  return (
    <div className="flex">
      <TocSideBar trees={data.trees} controls={data.control_findings} safeVectors={data.safe_vectors} />
      <main className="flex-1">
        <SummaryBar summary={data.summary} controls={data.control_findings} safeVectors={data.safe_vectors} />
        <DataflowTrees trees={data.trees} />
        <ControlSection controls={data.control_findings} />
        <SafeEntries vectors={data.safe_vectors} />
      </main>
    </div>
  );
}
```

(d) `router.tsx`——ScanDetail children 加：

```tsx
{ path: "dataflow", lazy: { Component: () => import("./DataFlowTab").then(m => ({ default: m.DataFlowTab })) } }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/vitest src/routes/WorkspaceDetail/__tests__/DataFlowTab.test.tsx --run`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/web/frontend/src/routes/WorkspaceDetail/ packages/web/frontend/src/api/
git commit -m "feat(frontend): DataFlowTab 骨架 + 两栏布局 + SWR + router"
```

---

### Task 12: 前端 — 剪枝树 SVG 组件（PruningTreeFig + BranchRow）

**Files:**
- Create: `packages/web/frontend/src/components/dataflow/PruningTreeFig.tsx`
- Create: `packages/web/frontend/src/components/dataflow/BranchRow.tsx`
- Create: `packages/web/frontend/src/components/dataflow/__tests__/PruningTreeFig.test.tsx`
- Modify: `packages/web/frontend/src/styles/tokens.css`（语义色，如不存在则确认现有 tokens 文件）

**Interfaces:**
- Consumes: `DataflowTree`（Task 11）；语义色 cyan=GitNexus / magenta=LLM / red=打通 / green=剪断（tokens.css）。
- Produces: `PruningTreeFig` 自研 SVG——水平汇聚（source 左列 → sink 右靶心）；列对齐（`x = step_index × COL_W`）；打通枝红虚线流动（`stroke-dashoffset` 动画）；剪断枝绿实线至防护节点 + ✂ 残端；黄盾=绕过、绿盾=剪断点；红靶心脉动 / 灰虚线靶心=无输入到达；同名函数青色点线弧。`BranchRow` 枝条明细 + 代码展开（`has_code:false` 降级「LLM 扫描的节点不带源码，agent 原话」）。`prefers-reduced-motion` 全关。

- [ ] **Step 1: Write the failing test**

`packages/web/frontend/src/components/dataflow/__tests__/PruningTreeFig.test.tsx`——用 msw + 最小 tree fixture，断言关键 SVG 元素：

```tsx
import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { PruningTreeFig } from "../PruningTreeFig";
import { DataflowTree } from "../../../api/types";

const vulnerableTree: DataflowTree = { /* 一棵打通树：1 branch track=gitnexus verdict=vulnerable */ } as any;
const safeTree: DataflowTree = { /* 一棵剪断树：verdict=safe，残端不到 sink */ } as any;

describe("PruningTreeFig", () => {
  it("renders vulnerable branch as flowing red dashed path", () => {
    const { container } = render(<PruningTreeFig tree={vulnerableTree} />);
    const path = container.querySelector('path[data-branch="vulnerable"]');
    expect(path).toBeTruthy();
    expect(path?.getAttribute("class")).toContain("flow");  // 流动动画 class
  });
  it("renders safe branch with ✂ marker, stops before sink", () => {
    const { container } = render(<PruningTreeFig tree={safeTree} />);
    const scissors = container.querySelector('[data-branch="safe"] [data-scissors]');
    expect(scissors).toBeTruthy();
  });
  it("aligns nodes to column x = step_index * COL_W", () => {
    const { container } = render(<PruningTreeFig tree={vulnerableTree} />);
    const node0 = container.querySelector('[data-node="0"]');
    expect(parseFloat(node0?.getAttribute("x") || "0")).toBeCloseTo(0 * COL_W);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/vitest src/components/dataflow/__tests__/PruningTreeFig.test.tsx --run`
Expected: FAIL。

- [ ] **Step 3: Write minimal implementation**

`PruningTreeFig.tsx`——自研 SVG（参照 FileTree 组件惯例）。关键结构：

```tsx
const COL_W = 180;
export function PruningTreeFig({ tree }: { tree: DataflowTree }) {
  // 列对齐：同一 step_index 的节点 x = step_index * COL_W
  // 打通枝：path class="branch-vuln flow" + strokeDashoffset 动画（CSS @keyframes flow）
  // 剪断枝：path class="branch-safe" 至防护节点 + ✂ text + 残端虚线
  // sink 靶心：tree.findings.length > 0 ? 红脉动圆环 : 灰虚线圆环
  // 同名函数弧：<path class="sameline" /> + <text class="sameline-txt">同一函数 ⟳</text>
  // 折叠：剪断枝 > 4 折叠为「+N 条枝被剪断」
  // 缩放平移：容器限高 520px + wheel onWheel + 拖拽 onMouseDown/Move
  return (<svg>...</svg>);
}
```

`tokens.css`——确认/加语义色（若现有 tokens 已有 cyan/magenta/red/green 则复用）：

```css
:root {
  --cyan: #06b6d4;     /* GitNexus */
  --magenta: #d946ef;  /* LLM */
  --vuln-red: #dc2626; /* 打通 */
  --safe-green: #16a34a; /* 剪断 */
}
.branch-vuln { stroke: var(--vuln-red); stroke-dasharray: 6 4; animation: flow 1s linear infinite; }
.branch-safe { stroke: var(--safe-green); stroke-width: 2; }
@keyframes flow { to { stroke-dashoffset: -10; } }
@media (prefers-reduced-motion: reduce) { .branch-vuln { animation: none; } }
```

`BranchRow.tsx`——枝条明细列表 + 代码展开：

```tsx
export function BranchRow({ branch }: { branch: DataflowBranch }) {
  // 链级标签：verdict=vulnerable →「打通 · 一路无有效防护」；safe →「剪断 · 在 X 被拦下」
  // 节点点击展开 code；has_code=false →「LLM 扫描的节点不带源码，agent 原话」
  // 与 SVG path 双向高亮联动（hover）
  return (<div className="branch-row">...</div>);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/vitest src/components/dataflow/__tests__/PruningTreeFig.test.tsx --run`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/web/frontend/src/components/dataflow/ packages/web/frontend/src/styles/
git commit -m "feat(frontend): 剪枝树 SVG（列对齐+剪枝视觉+同名函数弧+折叠缩放）"
```

---

### Task 13: 前端 — 目录侧栏 + 关卡链 + 排查过的入口 + VulnCard 跳转 + 白话文案 i18n

**Files:**
- Create: `packages/web/frontend/src/components/dataflow/TocSideBar.tsx`（scrollspy + 定位闪烁）
- Create: `packages/web/frontend/src/components/dataflow/GuardChain.tsx`（认证/授权关卡链 🟢🔴🟡）
- Create: `packages/web/frontend/src/components/dataflow/SafeEntries.tsx`（排查过的入口）
- Modify: `packages/web/frontend/src/components/.../VulnCard.tsx`（展开态加「查看数据流」链接 `.../dataflow?tree={tree_id}`）
- Modify: `packages/web/frontend/src/locales/zh/*.json` + `locales/en/*.json`（白话文案）
- Test: 对应 `__tests__/*.test.tsx`（scrollspy / 关卡链 / 空态 / VulnCard 跳转 / 文案 zh/en 快照）

**Interfaces:**
- Consumes: Task 11/12 组件；VulnCard 现有展开态；i18n namespaces。
- Produces: 三区完整页面（数据流树 / 认证·授权风险 / 排查过的入口）+ 目录 scrollspy + VulnCard 跳转 + 双语白话文案。

- [ ] **Step 1: Write the failing tests**

逐组件最小测试（scrollspy 命中、关卡链 status 颜色、SafeEntries 空态、VulnCard 链接 href 含 `?tree=`、i18n zh/en 快照含「数据流」「打通」「剪断」）。具体测试体照 Task 11/12 模式。

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/vitest src/components/dataflow/__tests__/ --run`
Expected: FAIL。

- [ ] **Step 3: Write minimal implementation**

`TocSideBar.tsx`——sticky 吸顶 + 自身滚动 + IntersectionObserver scrollspy + 点击平滑滚动 + 目标卡 coral 描边闪烁；窄屏 `<1000px` 退化为顶部块。
`GuardChain.tsx`——逐接口关卡卡序列（🟢 正常 / 🔴 缺失 dashed 红边 / 🟡 失效），detail 引 finding 原文 + file:line。
`SafeEntries.tsx`——safe_vectors 平铺（subject + 防护机制 + 位置），区头说明「有起点、无危险终点」。
`VulnCard.tsx`——展开态加 `<Link to={`../dataflow?tree=${tree_id}`}>查看数据流</Link>`（tree_id 经 DeliverablesTab SWR 拉同一 dataflow API 建 finding_id→tree_id 映射传入——见 spec §5 路由与入口）。
`locales/zh,dataflow.json` + `en,dataflow.json`——白话文案（打通/剪断/认证授权风险/排查过的入口/无输入到达/LLM 扫描的节点不带源码 等，对照 spec §5 表）。

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/vitest src/components/dataflow/__tests__/ --run`
Expected: 全 PASS（含 zh/en 快照）。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/web/frontend/src/components/dataflow/ packages/web/frontend/src/components/**/VulnCard.tsx packages/web/frontend/src/locales/
git commit -m "feat(frontend): 目录侧栏+关卡链+排查过的入口+VulnCard跳转+白话文案 i18n"
```

---

## Self-Review

**1. Spec coverage**（逐 spec 小节）：
- §1 问题与目标 → 全 plan 围绕此（trees/控制/safe_vectors 三区）
- §2 双轨×双引擎横切不变量 → Global Constraints + Task 3（双引擎一致性测试）+ Task 10（双引擎探针）
- §3 数据契约 → Task 7 组装器逐条实现聚合规则
- §4 管线 P1–P6 → Task 2/3/4/5/6/7/8/9/10 逐 P 覆盖
- §5 前端 → Task 11/12/13
- §6 降级矩阵 → Task 7 fixture 矩阵（全缺→None / 单缺→降级 / code 体积控制）
- §7 测试矩阵 → 每个 Task 有测试；core pytest（P1/P2/组装器）/ whitebox pytest（活动）/ web pytest（端点）/ 前端 vitest+msw / 真机探针 全覆盖
- §8 涉及文件清单 → 文件结构节逐文件命中

**2. Placeholder scan**：
- Task 7 Step 3 实现体量大但给了骨架 + 辅助函数清单 + 指向 spec §3 字段名——非占位符，是实现指引。组装器辅助函数（`_build_taint_trees` 等）的逐行实现交由执行者按 spec §3 聚合规则机械组装（规则已逐条列出）。
- Task 10 探针照现有 `validate_*_task_probe.py` 惯例——给了入口、复现命令、断言点，执行者参照现有探针文件结构填充。
- Task 13 测试体引用「照 Task 11/12 模式」——已在前 Task 给了完整测试范式，此处不重复全量但给出断言要点。

**3. Type consistency**：
- `dataflow_steps` 字段名贯穿 Task 3（collector schema）/ Task 4（pydantic）/ Task 5（prompt）/ Task 7（组装器读 `finding.dataflow_steps`）/ Task 10（探针断言）一致。
- `assemble_dataflow_view(whitebox_dir: Path) -> dict | None` 在 Task 7 定义、Task 8 消费一致。
- `_dump_chain_verdicts`（Task 2）/ `_dump_safe_vectors`（Task 6）/ `_dump_safe_vectors`（Task 6 executor 调用）签名一致。
- `DataflowView` TS 类型（Task 11）与 spec §3 schema 字段名对齐，Task 12/13 消费一致。
- `run_assemble_dataflow_view(input: ActivityInput) -> dict`（Task 8）与 workflow 接线一致。

**已修**：Task 7 Step 4 的 `test_code_snippet_volume_control` 断言 nodes 顺序依赖组装器实现——若透传步被实现为「不进 nodes」（spec 只说 has_code:false 不说进不进 nodes），执行者需对齐：**纯透传步进 nodes 但 has_code=false**（spec §3「纯透传步只存位置 has_code:false」= 进 nodes 存位置）。已在 Task 7 备注说明。
