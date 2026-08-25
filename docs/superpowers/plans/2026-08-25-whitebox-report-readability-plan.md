# 白盒漏洞报告可读性与专业性改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 白盒漏洞报告达到专业渗透测试报告水准——四要素卡片（是什么/危害/问题代码/修复建议）、接口级归并、速查表、全文风格统一、severity/CVSS/CWE 数据化。

**Architecture:** 三层改动：数据层（queue schema 扩展 + severity 兜底规则）→ 归并层（GN 单位收敛 + merger key 归一化，收口在 `merge_dual_track_queues` 内部使调用方零改动）→ 渲染层（统一 `render_vuln_card` 四要素模板 + 速查表确定性注入）+ prompt 层（风格指南 shared include + collector schema 扩展）+ 前端（severity 读真数据）。

**Tech Stack:** Python 3.12 / pydantic 2 / pytest（core 包）；TypeScript / React / vitest（web 前端）。

**Spec:** `docs/superpowers/specs/2026-08-25-whitebox-report-readability-design.md`

## Global Constraints

- **双轨铁律**：任何 prompt 不得 `@include` 确定性层产物（`tests/prompts/test_static_dataflow_hints_decoupling.py` 锁定，勿破坏）。
- **schema append-only**：`BaseVulnerability` 新字段全部 `| None = None`，旧 queue 文件必须照常解析（`parse_lenient` 不得拒收旧条目）。
- **severity 枚举**：`critical/high/medium/low`（小写字符串，与黑盒 add_exploit 一致）；中文显示映射 严重/高危/中危/低危。
- **语言规则**：叙述内容跟随 `SUPERNOVA_AGENT_NARRATION_LANG`（zh 报告全文中文）；保留英文原文：漏洞编号、代码、命令、文件路径行号、HTTP 方法/状态码、技术缩写（XSS/CSRF/JWT/IDOR…）。
- **测试范围**：只跑本 plan 点名的设计文件——全套 pytest 有预存挂起（memory: feat-fork-py-test-gotchas），勿广跑。
- **前端测试命令**：`cd packages/web/frontend && ./node_modules/.bin/vitest run <file>`（勿用 `pnpm test`，memory: pnpm 陷阱）。
- **后端测试命令**：`cd /root/shannon-py && uv run pytest <file> -v`。
- **commit 规范**：`feat(report): 中文描述`，结尾加 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。

## 关键现状坐标（实现者必读）

| 现状 | 位置 |
|---|---|
| 漏洞 schema（Base + 5 子类） | `packages/core/src/supernova_core/models/queue_schemas.py:7-111` |
| 白盒 findings 渲染（5 个 per-class 函数） | `packages/core/src/supernova_core/services/findings_renderer.py:100-219` |
| 双轨合并（dedup key 整链字符串） | `packages/core/src/supernova_core/code_index/dual_track_merger.py:48-65` |
| GN finding 构造（`path` 已含 `METHOD /path` 前缀；`sink_call`=`file:Handler:sinkFunc:line:col`） | `packages/core/src/supernova_core/code_index/vuln_chain_builders/injection_builder.py:70-91` |
| 报告拼接（evidence→findings→analysis 三级回退） | `packages/core/src/supernova_core/services/report_assembler.py:35-70` |
| 执行摘要 agent prompt | `prompts/report-executive.txt` |
| LLM 轨 finding collector（`submit_finding` schema） | `packages/core/src/supernova_core/collectors/vuln.py:348-454` |
| 黑盒 exploit collector/renderer（5-section，已有 severity/impact） | `packages/core/src/supernova_core/collectors/exploit.py`、`renderers/exploit.py` |
| 语言 include 机制（`_output-language.zh.txt`/`.en.txt` 双文件按 narration lang 选择） | `prompts/shared/` |
| 前端 severity 启发式（注释自述"报告无逐条 severity 字段"） | `packages/web/frontend/src/lib/vuln-block.ts:19,60` |
| prompt↔schema 锁定测试（vuln prompt 改动须同步） | `packages/core/tests/prompts/test_vuln_prompt_schema_contract.py` |

---

### Task 1: schema 扩展 + severity 兜底规则

**Files:**
- Modify: `packages/core/src/supernova_core/models/queue_schemas.py`（`BaseVulnerability` :7-26 追加字段）
- Create: `packages/core/src/supernova_core/services/severity_rules.py`
- Test: `packages/core/tests/models/test_severity_rules.py`（新建）、`packages/core/tests/models/test_dual_track_fields.py`（追加）

**Interfaces:**
- Produces: `BaseVulnerability` 新字段 `severity/cvss/cwe_id/owasp_category/endpoint/affected_parameters/affected_entries/verification/code_snippet`（全 `| None = None`）
- Produces: `severity_rules.py` 导出 `SEVERITY_ORDER: dict`、`derive_fallback_severity(vuln) -> str`、`effective_severity(vuln) -> str`、`max_severity(a, b) -> str`、`SEVERITY_ZH: dict`（critical→严重 等）

- [ ] **Step 1: 写失败测试**

```python
# packages/core/tests/models/test_severity_rules.py
from supernova_core.models.queue_schemas import InjectionVulnerability
from supernova_core.services.severity_rules import (
    SEVERITY_ZH, derive_fallback_severity, effective_severity, max_severity,
)

def _vuln(**kw):
    base = dict(ID="INJ-GN-01", vulnerability_type="injection",
                externally_exploitable=True, confidence="low",
                sink_call="app/routes/contributions.js:ContributionsHandler:eval:32:23")
    base.update(kw)
    return InjectionVulnerability(**base)

def test_fallback_eval_sink_is_critical():
    assert derive_fallback_severity(_vuln()) == "critical"

def test_fallback_injection_generic_is_high():
    v = _vuln(sink_call=None, sink_function="findOne", vulnerability_type="injection")
    assert derive_fallback_severity(v) == "high"

def test_fallback_externally_exploitable_other_class_is_high():
    v = _vuln(vulnerability_type="ssrf", sink_call=None, sink_function="needle.get")
    assert derive_fallback_severity(v) == "high"

def test_fallback_baseline_medium():
    v = _vuln(vulnerability_type="auth", sink_call=None, sink_function=None,
              externally_exploitable=False)
    # auth 无 sink 字段 → medium
    assert derive_fallback_severity(v) == "medium"

def test_effective_severity_prefers_explicit():
    assert effective_severity(_vuln(severity="medium")) == "medium"
    assert effective_severity(_vuln(severity=None)) == "critical"  # eval 兜底
    assert effective_severity(_vuln(severity="bogus")) == "critical"  # 非法值走兜底

def test_max_severity_and_zh_mapping():
    assert max_severity("medium", "critical") == "critical"
    assert SEVERITY_ZH == {"critical": "严重", "high": "高危", "medium": "中危", "low": "低危"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/models/test_severity_rules.py -v`
Expected: FAIL（`ModuleNotFoundError: supernova_core.services.severity_rules`）

- [ ] **Step 3: 最小实现**

```python
# packages/core/src/supernova_core/services/severity_rules.py
"""Severity 兜底规则（spec §4）：LLM 未给 severity 时按确定性规则定档。"""
from __future__ import annotations

SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
SEVERITY_ZH = {"critical": "严重", "high": "高危", "medium": "中危", "low": "低危"}
# RCE 级 sink 关键词（命令/代码执行）——命中即 critical
_RCE_SINK_KEYWORDS = ("eval", "exec", "system(", "popen", "spawn", "child_process")

def derive_fallback_severity(vuln) -> str:
    sink = (getattr(vuln, "sink_function", None)
            or getattr(vuln, "sink_call", None) or "") or ""
    lowered = sink.lower()
    if any(k in lowered for k in _RCE_SINK_KEYWORDS):
        return "critical"
    if getattr(vuln, "vulnerability_type", "") == "injection":
        return "high"
    if getattr(vuln, "externally_exploitable", False):
        return "high"
    return "medium"

def effective_severity(vuln) -> str:
    explicit = getattr(vuln, "severity", None)
    if isinstance(explicit, str) and explicit.strip().lower() in SEVERITY_ORDER:
        return explicit.strip().lower()
    return derive_fallback_severity(vuln)

def max_severity(a: str | None, b: str | None) -> str:
    ea = effective_severity_from_str(a)
    eb = effective_severity_from_str(b)
    return a if ea >= eb else b

def effective_severity_from_str(s: str | None) -> int:
    if isinstance(s, str) and s.strip().lower() in SEVERITY_ORDER:
        return SEVERITY_ORDER[s.strip().lower()]
    return 0
```

`queue_schemas.py` `BaseVulnerability` 在 `sanitizer_annotations` 后追加（注释注明 spec 2026-08-25）：

```python
    # 报告可读性改造（spec 2026-08-25 §4）：全部 append-only，旧 queue 兼容。
    severity: str | None = None            # critical/high/medium/low；缺省渲染层兜底
    cvss: str | None = None                # 如 "AV:N/AC:L/PR:L/UI:N 8.8"
    cwe_id: str | None = None              # "CWE-95"
    owasp_category: str | None = None      # "A03:2021-Injection"
    endpoint: str | None = None            # 归一化 "POST /contributions"
    affected_parameters: list[str] | None = None
    affected_entries: list[dict] | None = None  # {parameter, sink_location, chain_id, track, direct}
    verification: str | None = None        # static_analysis | dynamically_verified
    code_snippet: str | None = None        # 渲染层注入，不落 queue
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/models/test_severity_rules.py packages/core/tests/models/test_dual_track_fields.py -v`
Expected: PASS（全部）

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/supernova_core/models/queue_schemas.py packages/core/src/supernova_core/services/severity_rules.py packages/core/tests/models/test_severity_rules.py
git commit -m "feat(report): 漏洞 schema 报告字段扩展 + severity 确定性兜底规则

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: GN 单位收敛 collapse_gn_entries

**Files:**
- Create: `packages/core/src/supernova_core/code_index/gn_collapse.py`
- Test: `packages/core/tests/code_index/test_gn_collapse.py`（新建）

**Interfaces:**
- Consumes: `BaseVulnerability.affected_entries/affected_parameters/endpoint`（Task 1）
- Produces:
  - `parse_sink_call_site_id(s: str) -> tuple[str | None, str | None]`：`"app/routes/contributions.js:ContributionsHandler:eval:32:23"` → `("eval", "app/routes/contributions.js:32")`（<4 段返回 `(None, None)`）
  - `extract_endpoint(path_or_endpoint: str | None) -> str | None`：正则 `^(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(\S+)` 匹配前缀；非前缀位置时 `re.search` 取首个；归一化大写 method、去 query/尾斜杠
  - `extract_param(source: str | None) -> str | None`：`"preTax (app/...)"` → `"preTax"`
  - `collapse_gn_entries(findings: list) -> list`：按 `(vulnerability_type, endpoint 或 (sink 文件, sink 函数), sink 函数名)` 分组，每组首条为主记录：`affected_entries=[{parameter, sink_location, chain_id, track:"gitnexus"}]`、`affected_parameters=去重列表`、`endpoint=归一值`、`severity=组内 effective 最高`；主记录 ID 保留组内首条（编号最小）；**不判断虚假配对**（Task 4 code_snippet 回填 `direct` 标注）

- [ ] **Step 1: 写失败测试**

```python
# packages/core/tests/code_index/test_gn_collapse.py
from supernova_core.code_index.gn_collapse import (
    collapse_gn_entries, extract_endpoint, extract_param, parse_sink_call_site_id,
)
from supernova_core.models.queue_schemas import InjectionVulnerability

def _gn(id_, param, sink, path="POST /contributions → chain"):
    return InjectionVulnerability(
        ID=id_, vulnerability_type="injection", externally_exploitable=True,
        confidence="low", source=f"{param} (app/routes/contributions.js:ContributionsHandler:7)",
        path=path, sink_call=sink, verdict="vulnerable", source_track="gitnexus")

SINK32 = "app/routes/contributions.js:ContributionsHandler:eval:32:23"
SINK33 = "app/routes/contributions.js:ContributionsHandler:eval:33:25"

def test_parse_sink_call_site_id():
    assert parse_sink_call_site_id(SINK32) == ("eval", "app/routes/contributions.js:32")
    assert parse_sink_call_site_id("short") == (None, None)

def test_extract_endpoint_and_param():
    assert extract_endpoint("POST /contributions → preTax -> x") == "POST /contributions"
    assert extract_endpoint("a → GET /login → b") == "GET /login"
    assert extract_endpoint("no route here") is None
    assert extract_param("preTax (app/routes/contributions.js:7)") == "preTax"

def test_collapse_same_unit_nine_to_three():
    """preTax/afterTax/roth × eval:32/33/34（同接口同 sink 函数）→ 1 主记录 9 入口行。"""
    gn = [_gn(f"INJ-GN-{i:02d}", p, s)
          for i, (p, s) in enumerate(
              [(p, f"app/routes/contributions.js:ContributionsHandler:eval:{ln}:{ln}")
               for p in ("preTax", "afterTax", "roth") for ln in (32, 33, 34)], start=1)]
    out = collapse_gn_entries(gn)
    assert len(out) == 1
    assert out[0].ID == "INJ-GN-01"
    assert out[0].endpoint == "POST /contributions"
    assert set(out[0].affected_parameters) == {"preTax", "afterTax", "roth"}
    assert len(out[0].affected_entries) == 9
    assert out[0].affected_entries[0] == {
        "parameter": "preTax", "sink_location": "app/routes/contributions.js:32",
        "chain_id": "INJ-GN-01", "track": "gitnexus"}

def test_collapse_keeps_different_endpoints_separate():
    a = _gn("XSS-GN-01", "memo", "app/routes/memos.js:MemosHandler:render:27:19",
            path="GET /memos → chain")
    b = _gn("XSS-GN-02", "url", "app/routes/research.js:ResearchHandler:render:31:15",
            path="GET /research → chain")
    out = collapse_gn_entries([a, b])
    assert len(out) == 2  # 不同接口绝不合并（spec §3.1）

def test_collapse_severity_takes_max():
    gn = [_gn("INJ-GN-01", "preTax", SINK32, severity="medium"),
          _gn("INJ-GN-02", "preTax", SINK33, severity=None)]  # 兜底 critical(eval)
    out = collapse_gn_entries(gn)
    assert out[0].severity == "critical"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_gn_collapse.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 最小实现**

```python
# packages/core/src/supernova_core/code_index/gn_collapse.py
"""GN 轨条目按漏洞单位收敛（spec §3）：同 (vuln_class, 接口, sink 函数) 多链 →
一条主记录 + affected_entries 入口列表。不同接口绝不合并。"""
from __future__ import annotations

import re

from supernova_core.services.severity_rules import effective_severity_from_str

_METHOD_PATH = re.compile(
    r"\b(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(/\S*)")

def parse_sink_call_site_id(s: str) -> tuple[str | None, str | None]:
    parts = s.split(":")
    if len(parts) < 4:
        return (None, None)
    return (parts[-3], f"{parts[0]}:{parts[-2]}")

def extract_endpoint(path_or_endpoint: str | None) -> str | None:
    if not isinstance(path_or_endpoint, str):
        return None
    m = _METHOD_PATH.search(path_or_endpoint)
    if not m:
        return None
    route = m.group(2).split("?", 1)[0].rstrip("/") or "/"
    return f"{m.group(1).upper()} {route}"

def extract_param(source: str | None) -> str | None:
    if not isinstance(source, str):
        return None
    head = source.split("(", 1)[0].strip()
    return head or None

def _unit_key(f):
    sink_func, _loc = parse_sink_call_site_id(getattr(f, "sink_call", "") or "")
    endpoint = (getattr(f, "endpoint", None)
                and extract_endpoint(f.endpoint)) or extract_endpoint(getattr(f, "path", None))
    if endpoint:
        return (getattr(f, "vulnerability_type", None), endpoint, sink_func)
    if sink_func and _loc:
        return (getattr(f, "vulnerability_type", None), _loc, sink_func)  # 文件级回退
    return ("__strict__", id(f))

def collapse_gn_entries(findings: list) -> list:
    groups: dict[tuple, list] = {}
    order: list[tuple] = []
    for f in findings:
        key = _unit_key(f)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(f)
    merged: list = []
    for key in order:
        group = groups[key]
        if len(group) == 1 and getattr(group[0], "affected_entries", None):
            merged.append(group[0])
            continue
        primary = group[0]
        entries = []
        params: list[str] = []
        for f in group:
            param = (extract_param(getattr(f, "source", None))
                     or (getattr(f, "affected_parameters", None) or [None])[0])
            _, loc = parse_sink_call_site_id(getattr(f, "sink_call", "") or "")
            entries.append({"parameter": param, "sink_location": loc,
                            "chain_id": getattr(f, "ID", None), "track": "gitnexus"})
            if param and param not in params:
                params.append(param)
        data = primary.model_dump()
        data["affected_entries"] = entries
        data["affected_parameters"] = params or None
        data["endpoint"] = extract_endpoint(getattr(primary, "path", None))
        best = max((getattr(f, "severity", None) for f in group),
                   key=lambda s: effective_severity_from_str(s), default=None)
        data["severity"] = best
        merged.append(type(primary).model_validate(data))
    return merged
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_gn_collapse.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/supernova_core/code_index/gn_collapse.py packages/core/tests/code_index/test_gn_collapse.py
git commit -m "feat(report): GN 条目按漏洞单位收敛(接口级主记录+受影响入口列表)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: merger key 归一化 + 跨轨合并策略 + collapse 接线

**Files:**
- Modify: `packages/core/src/supernova_core/code_index/dual_track_merger.py`（`_finding_key` :48-65、`merge_dual_track_queues` :122 开头接线、`_clone_with_merge_fields` :93 合并策略）
- Test: `packages/core/tests/code_index/test_dual_track_merger.py`（追加用例）

**Interfaces:**
- Consumes: Task 2 的 `parse_sink_call_site_id/extract_endpoint/extract_param/collapse_gn_entries`
- Produces: `merge_dual_track_queues(llm, gitnexus)` 行为变更——入口先 `collapse_gn_entries(gitnexus)`；dedup key 改单位 key；both 分支合并 `severity`（取高）/`affected_entries`（并集）/`affected_parameters`（并集）
- 注意：`Horizontal` authz 既有 endpoint-only dedup 特例**保留不动**（`_finding_key` :58-62 原逻辑）

- [ ] **Step 1: 写失败测试**（追加到 `test_dual_track_merger.py`）

```python
# packages/core/tests/code_index/test_dual_track_merger.py 追加
from supernova_core.models.queue_schemas import InjectionVulnerability

def _llm_inj(**kw):
    base = dict(ID="INJ-VULN-01", vulnerability_type="injection",
                externally_exploitable=True, confidence="high",
                title="命令注入：POST /contributions 直接 eval()（RCE）",
                source="preTax & req.body",
                path="POST /contributions → handleContributionsUpdate → eval(req.body.preTax)",
                sink_function="eval", verdict="vulnerable", severity="high",
                affected_entries=[{"parameter": "preTax",
                                   "sink_location": "app/routes/contributions.js:32",
                                   "chain_id": None, "track": "llm"}])
    base.update(kw)
    return InjectionVulnerability(**base)

def _gn_inj(id_, param, line):
    return InjectionVulnerability(
        ID=id_, vulnerability_type="injection", externally_exploitable=True,
        confidence="low", source=f"{param} (app/routes/contributions.js:ContributionsHandler:7)",
        path="POST /contributions → chain",
        sink_call=f"app/routes/contributions.js:ContributionsHandler:eval:{line}:{line}",
        verdict="vulnerable", source_track="gitnexus")

def test_merger_collapses_and_cross_track_dedup():
    """9 条 GN 同单位收敛后与 LLM 轨同单位条目合并为 1 条 both。"""
    llm = [_llm_inj()]
    gn = [_gn_inj(f"INJ-GN-{i:02d}", p, ln)
          for i, (p, ln) in enumerate([(p, ln) for p in ("preTax", "afterTax", "roth")
                                       for ln in (32, 33, 34)], start=1)]
    merged = merge_dual_track_queues(llm, gn)
    assert len(merged) == 1
    m = merged[0]
    assert m.merge_source == "both"
    assert m.ID == "INJ-VULN-01"            # LLM 轨为 base（叙述权威）
    assert m.severity == "critical"          # GN 兜底 eval=critical 取高
    assert len(m.affected_entries) == 10     # LLM 1 行 + GN 9 行并集
    assert set(m.affected_parameters) == {"preTax", "afterTax", "roth"}

def test_merger_keeps_different_endpoint_separate():
    llm = [_llm_inj()]
    gn = [_gn_inj("INJ-GN-01", "memo", 27)]
    gn[0].path = "GET /memos → chain"
    gn[0].sink_call = "app/routes/memos.js:MemosHandler:render:27:19"
    merged = merge_dual_track_queues(llm, gn)
    assert len(merged) == 2                  # 不同接口不合并
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_dual_track_merger.py -v -k "collapses_and_cross or keeps_different_endpoint"`
Expected: 新用例 FAIL（现有用例必须仍 PASS）

- [ ] **Step 3: 实现**

`dual_track_merger.py` 修改三处：

```python
# 顶部 import 追加
from supernova_core.code_index.gn_collapse import (
    collapse_gn_entries, extract_endpoint, parse_sink_call_site_id,
)
from supernova_core.services.severity_rules import max_severity

# _finding_key 内 loc/sink 构造改为单位归一（Horizontal 特例保留在最前）：
def _finding_key(finding: Vulnerability) -> tuple:
    vtype = getattr(finding, "vulnerability_type", None)
    if vtype == "Horizontal":
        norm = _normalize_endpoint(getattr(finding, "endpoint", None))
        if norm:
            return ("Horizontal", norm)
    # 单位 key（spec §3.3）：endpoint 归一 + sink 函数名——弃整链字符串精确匹配
    endpoint = (extract_endpoint(getattr(finding, "endpoint", None))
                or extract_endpoint(getattr(finding, "path", None))
                or _normalize_endpoint(getattr(finding, "source_endpoint", None))
                or _normalize_endpoint(getattr(finding, "endpoint", None)))
    raw_sink = getattr(finding, "sink_function", None) or getattr(finding, "sink_call", None)
    sink_func, _loc = parse_sink_call_site_id(raw_sink or "")
    if not sink_func and isinstance(raw_sink, str) and raw_sink.strip():
        sink_func = raw_sink.strip()  # LLM 自然语言 sink（如 "eval at file:32"）整体作 key
    return (vtype, endpoint, sink_func)

# _clone_with_merge_fields 增加 both 分支合并策略——函数尾部（verdict 改写前）：
    if merge_source == "both":
        data["severity"] = max_severity(data.get("severity"), other_severity)
        merged_entries = list(data.get("affected_entries") or [])
        seen = {(e.get("parameter"), e.get("sink_location")) for e in merged_entries}
        for e in other_entries or []:
            k = (e.get("parameter"), e.get("sink_location"))
            if k not in seen:
                merged_entries.append(e)
                seen.add(k)
        data["affected_entries"] = merged_entries or None
        params = list(data.get("affected_parameters") or [])
        for p in other_params or []:
            if p not in params:
                params.append(p)
        data["affected_parameters"] = params or None
        data["endpoint"] = data.get("endpoint") or other_endpoint

# _clone_with_merge_fields 签名加 other_* 参数（默认 None，向后兼容单轨分支）
# merge_dual_track_queues 开头接线（函数体第一行）：
    gitnexus_findings = collapse_gn_entries(gitnexus_findings)
# both 分支调用传 other_severity=getattr(gitnexus, "severity", None) 等
```

- [ ] **Step 4: 跑测试确认通过（含既有用例回归）**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_dual_track_merger.py packages/core/tests/code_index/test_gn_collapse.py packages/whitebox/tests/test_run_merge_dual_track.py -v`
Expected: PASS（若有既有用例因 key 变化挂——检查其断言是否依赖"整链精确匹配不合"的旧语义，属本任务**有意变更**，更新该用例并在 commit message 注明）

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/supernova_core/code_index/dual_track_merger.py packages/core/tests/code_index/test_dual_track_merger.py
git commit -m "feat(report): merger dedup key 归一化(接口+sink 函数)+GN 收敛接线+both 分支入口并集

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: code_snippet 确定性提取 + 假配对标注回填

**Files:**
- Create: `packages/core/src/supernova_core/services/code_snippet.py`
- Test: `packages/core/tests/services/test_code_snippet.py`（新建）

**Interfaces:**
- Produces:
  - `async extract_snippet(repo_root: Path | None, sink_location: str | None, width: int = 3) -> str | None`：`sink_location="app/routes/contributions.js:32"` → 该文件 `29-35` 行原文；文件/行缺失 → `None`
  - `annotate_direct(entries: list[dict] | None, snippet: str | None) -> None`：就地回填 `direct` 键——`snippet` 中出现该 `parameter` 字符串 → `True`（直接传递），否则 `False`（疑似间接/虚假配对，spec §3.2 标注不删除）
- 渲染调用方式（Task 5 消费）：卡片渲染时对每条主记录取 `affected_entries[0].sink_location`（LLM-only 条目从 `path` 中正则提取首个 `file.ext:line`）提取一段 snippet

- [ ] **Step 1: 写失败测试**

```python
# packages/core/tests/services/test_code_snippet.py
import pytest
from supernova_core.services.code_snippet import annotate_direct, extract_snippet

@pytest.mark.asyncio
async def test_extract_snippet_reads_range(tmp_path):
    f = tmp_path / "contributions.js"
    f.write_text("\n".join(f"line{i}" for i in range(1, 41)))
    snippet = await extract_snippet(tmp_path, "contributions.js:32")
    assert snippet is not None
    assert "line29" in snippet and "line35" in snippet and "line28" not in snippet

@pytest.mark.asyncio
async def test_extract_snippet_none_cases(tmp_path):
    assert await extract_snippet(None, "x.js:1") is None
    assert await extract_snippet(tmp_path, "missing.js:1") is None
    assert await extract_snippet(tmp_path, None) is None

def test_annotate_direct():
    entries = [{"parameter": "preTax", "sink_location": "a.js:32"},
               {"parameter": "afterTax", "sink_location": "a.js:33"}]
    snippet = "preTax = eval(req.body.preTax);"
    annotate_direct(entries, snippet)
    assert entries[0]["direct"] is True
    assert entries[1]["direct"] is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/services/test_code_snippet.py -v`
Expected: FAIL

- [ ] **Step 3: 实现**

```python
# packages/core/src/supernova_core/services/code_snippet.py
"""问题代码片段确定性提取（spec §5/§10.4）：sink 行 ±width 行，零 LLM 成本。"""
from __future__ import annotations

import re
from pathlib import Path

from supernova_core.utils.file_io import async_read_file

_FILE_LINE = re.compile(r"([\w./-]+\.[A-Za-z]{1,5}):(\d+)")

async def extract_snippet(repo_root: Path | None, sink_location: str | None,
                          width: int = 3) -> str | None:
    if repo_root is None or not isinstance(sink_location, str):
        return None
    m = _FILE_LINE.search(sink_location)
    if not m:
        return None
    path = repo_root / m.group(1)
    try:
        if not path.is_file():
            return None
        content = await async_read_file(path)
    except Exception:  # noqa: BLE001 — snippet 是增强项，任何失败静默跳过
        return None
    lines = content.splitlines()
    line_no = int(m.group(2))
    if not 1 <= line_no <= len(lines):
        return None
    start, end = max(1, line_no - width), min(len(lines), line_no + width)
    return "\n".join(lines[start - 1:end])

def annotate_direct(entries: list[dict] | None, snippet: str | None) -> None:
    if not entries or not snippet:
        return
    for e in entries:
        param = e.get("parameter")
        e["direct"] = bool(isinstance(param, str) and param and param in snippet)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/services/test_code_snippet.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/supernova_core/services/code_snippet.py packages/core/tests/services/test_code_snippet.py
git commit -m "feat(report): sink 行±3 行问题代码片段确定性提取+入口假配对 direct 标注

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: render_vuln_card 四要素统一卡片 + GN 降级 + 技术细节折叠

**Files:**
- Modify: `packages/core/src/supernova_core/services/findings_renderer.py`（重写 :100-219 五函数为统一函数；`_M` 增词条；`render_findings_from_queues` 增 `repo_root: Path | None = None` 参数）
- Modify: `packages/whitebox/src/supernova_whitebox/pipeline/activities.py:1119`（`render_findings` 传 `repo_root`——从 `_get_paths(input)` 的 repo 路径取）
- Test: `packages/core/tests/test_findings_renderer.py`（追加/改造用例）

**Interfaces:**
- Consumes: Task 1 `effective_severity/SEVERITY_ZH`、Task 2 `affected_entries`、Task 4 `extract_snippet/annotate_direct`
- Produces: `render_vuln_card(vuln, vuln_class: str, snippet: str | None) -> str`（核心函数）；卡片结构（spec §5）：`### {ID} {类中文名}：{title}` → 元信息行（`严重程度：X ｜ CWE-xx ｜ 验证：静态分析 ｜ 置信度：Y（双轨确认）`）→ 受影响入口表（有 `affected_entries` 时）→ `**漏洞说明**` → `**危害**` → `**问题代码**`（fence 代码块）→ `**修复建议**` → `#### 技术细节`（现有判定字段全量降级收纳）
- 行为约束：内部标签零出现（`llm-pass-failed`/`needs_review`/`unparseable-llm` 不渲染）；GN-only 卡（`source_track=="gitnexus"`）说明走确定性描述 + 元信息行追加 `待复核`；`verification` 缺省渲染为 `静态分析`
- `render_findings_from_queues(deliverables_path, report_config, *, queue_subdir=None, findings_subdir=None, repo_root=None)` 签名向后兼容

- [ ] **Step 1: 写失败测试**（追加到 `test_findings_renderer.py`，沿用该文件现有 fixture 风格）

```python
# 关键断言用例（写入 packages/core/tests/test_findings_renderer.py）
import pytest
from supernova_core.models.queue_schemas import InjectionVulnerability
from supernova_core.services.findings_renderer import render_vuln_card

def _vuln(**kw):
    base = dict(ID="INJ-VULN-01", vulnerability_type="injection",
                externally_exploitable=True, confidence="high",
                title="命令注入：POST /contributions 直接 eval()（RCE）",
                source="preTax & req.body",
                path="POST /contributions → eval(req.body.preTax)",
                sink_function="eval", verdict="vulnerable", severity="critical",
                cwe_id="CWE-95", merge_source="both",
                affected_parameters=["preTax", "afterTax", "roth"],
                affected_entries=[
                    {"parameter": "preTax", "sink_location": "app/routes/contributions.js:32",
                     "chain_id": "INJ-GN-01", "track": "gitnexus"},
                    {"parameter": "afterTax", "sink_location": "app/routes/contributions.js:33",
                     "chain_id": "INJ-GN-04", "track": "gitnexus"}])
    base.update(kw)
    return InjectionVulnerability(**base)

SNIPPET = "preTax = eval(req.body.preTax);\ncontributions.preTax = preTax;"

def test_card_four_elements_and_meta_line():
    card = render_vuln_card(_vuln(), "injection", SNIPPET)
    assert card.startswith("### INJ-VULN-01 注入漏洞：命令注入")
    assert "严重程度：严重" in card and "CWE-95" in card
    assert "验证：静态分析" in card and "双轨确认" in card
    for section in ("**受影响入口**", "**漏洞说明**", "**危害**", "**问题代码**", "**修复建议**", "#### 技术细节"):
        assert section in card, section
    assert "| preTax | app/routes/contributions.js:32 |" in card
    assert SNIPPET in card  # 问题代码 fence 内

def test_card_no_internal_labels():
    v = _vuln(evidence_chain="preTax -> x (llm-pass-failed, needs_review)")
    card = render_vuln_card(v, "injection", None)
    assert "llm-pass-failed" not in card and "needs_review" not in card

def test_gn_only_card_degrades_gracefully():
    v = _vuln(ID="INJ-GN-01", source_track="gitnexus", confidence="low",
              title=None, severity=None, cwe_id=None, merge_source=None,
              source="preTax (app/routes/contributions.js:ContributionsHandler:7)",
              affected_entries=[{"parameter": "preTax",
                                 "sink_location": "app/routes/contributions.js:32",
                                 "chain_id": "INJ-GN-01", "track": "gitnexus"}])
    card = render_vuln_card(v, "injection", None)
    assert "待复核" in card
    assert "eval" in card  # 确定性说明含 sink 函数名
    assert "静态链路发现，建议人工确认" in card
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/test_findings_renderer.py -v -k card_`
Expected: FAIL

- [ ] **Step 3: 实现**

`findings_renderer.py`：
1. `_M` 增词条：`meta_verification`（"验证"/"Verification"）、`meta_affected_entries`（"受影响入口"/"Affected Entries"）、`sec_description`（"漏洞说明"/"Description"）、`sec_impact`（"危害"/"Impact"）、`sec_code`（"问题代码"/"Vulnerable Code"）、`sec_remediation`（"修复建议"/"Remediation"）、`sec_tech_detail`（"技术细节"/"Technical Detail"）、`gn_pending_review`（"待复核"/"Pending Review"）、`gn_static_hint`（"静态链路发现，建议人工确认后修复。"/"Static chain finding; confirm manually before remediation."）
2. 新增 5 类确定性危害兜底 `_IMPACT_FALLBACK = {"injection": "攻击者可注入恶意代码或查询，可能导致数据泄露、篡改或服务器接管。", ...}`（xss/auth/authz/ssrf 各一句，中文按 `_M` 机制做双语）
3. `render_vuln_card(vuln, vuln_class, snippet)` 实现（伪码即上模板；`title` 缺省时用 `f"{param} 传入 {sink}（{loc}）"` 确定性描述；危害取 `vuln.notes or _IMPACT_FALLBACK[vuln_class]`——Task 7 上线后 LLM 轨有 impact 字段时优先 `impact`）
4. `CLASS_CONFIG.render_entry` 全部替换为 lambda 调 `render_vuln_card`；旧五函数删除
5. `render_findings_from_queues` 增 `repo_root` 参数：渲染每条前，从 `affected_entries[0]["sink_location"]`（或 LLM-only `path` 正则 `_FILE_LINE`）取 `extract_snippet` + `annotate_direct`；`vuln.code_snippet` 赋值后传给卡片
6. `activities.py render_findings`：`repo_root = input.repo_path`（用 `_get_paths` 返回的第一个值），传 `repo_root=repo_root`

- [ ] **Step 4: 跑测试确认通过（含既有渲染用例回归）**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/test_findings_renderer.py -v`
Expected: PASS（既有用例若断言旧字段行——"拼接出现"等——更新为断言其出现在"技术细节"区，属有意变更）

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/supernova_core/services/findings_renderer.py packages/whitebox/src/supernova_whitebox/pipeline/activities.py packages/core/tests/test_findings_renderer.py
git commit -m "feat(report): 白盒四要素卡片统一渲染(说明/危害/问题代码/修复)+GN 降级+技术细节折叠+内部标签剥离

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: 漏洞速查表 + 汇总区渲染层化

**Files:**
- Modify: `packages/core/src/supernova_core/services/report_assembler.py`（`_assemble_sections` 前注入速查表）
- Test: `packages/core/tests/test_report_assembler.py`（追加）

**Interfaces:**
- Produces: `render_summary_table(queues_by_class: dict[str, list[Vulnerability]]) -> str`——输出 `## 漏洞速查表`（zh）/`## Vulnerability Summary Table`（en）+ markdown 表 `| ID | 漏洞 | 接口 | 参数 | 严重度 | 验证 | 置信度 |`，按 `SEVERITY_ORDER` 降序；类别标题渲染层生成（`Injection→注入漏洞`、`Xss→XSS 跨站脚本` 等映射，根治 LLM `### Xss`）
- 速查表插入位置：assemble 拼接产物**最前**（report-executive 后续在其上加执行摘要，速查表成为正文第一章）

- [ ] **Step 1: 写失败测试**

```python
# packages/core/tests/test_report_assembler.py 追加
from supernova_core.services.report_assembler import render_summary_table
from supernova_core.models.queue_schemas import InjectionVulnerability

def _v(id_, severity, params, endpoint="POST /contributions"):
    return InjectionVulnerability(
        ID=id_, vulnerability_type="injection", externally_exploitable=True,
        confidence="high", title="命令注入", severity=severity,
        endpoint=endpoint, affected_parameters=params)

def test_summary_table_sorted_with_endpoint_params():
    table = render_summary_table({"injection": [
        _v("INJ-VULN-02", "medium", ["threshold"], endpoint="GET /allocations/:userId"),
        _v("INJ-VULN-01", "critical", ["preTax", "afterTax", "roth"])]})
    assert "## 漏洞速查表" in table
    assert "| ID | 漏洞 | 接口 | 参数 | 严重度 | 验证 | 置信度 |" in table
    rows = [l for l in table.splitlines() if l.startswith("| INJ-")]
    assert rows[0].startswith("| INJ-VULN-01")     # critical 在前
    assert "POST /contributions" in rows[0] and "preTax" in rows[0]
    assert "严重" in rows[0] and "静态分析" in rows[0]
    assert rows[1].startswith("| INJ-VULN-02")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/test_report_assembler.py -v -k summary_table`
Expected: FAIL

- [ ] **Step 3: 实现**

`report_assembler.py` 加 `render_summary_table`（读全部 queue 后按类聚合：endpoint 回退 `extract_endpoint(path)`；severity 用 `effective_severity`；参数 `affected_parameters` join；空队列输出一行说明"本类无发现"）；`_assemble_sections` 起点处把速查表插到 sections[0]。queue 读取复用 `resolve_intermediate` 与 `VulnerabilityQueue.parse_lenient(vuln_class=...)`（同 `findings_renderer` 的读法）。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/test_report_assembler.py packages/core/tests/services/test_report_assembler.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/supernova_core/services/report_assembler.py packages/core/tests/test_report_assembler.py
git commit -m "feat(report): 漏洞速查表确定性注入(ID/漏洞/接口/参数/严重度/验证/置信度,严重度降序)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: collector schema 扩展 + 报告风格指南 shared include

**Files:**
- Modify: `packages/core/src/supernova_core/collectors/vuln.py`（`_finding_props` :348-454 加字段）
- Create: `prompts/shared/_report-style.zh.txt`、`prompts/shared/_report-style.en.txt`
- Modify: `prompts/vuln-injection.txt`、`vuln-xss.txt`、`vuln-auth.txt`、`vuln-authz.txt`、`vuln-ssrf.txt`（字段表 + include）
- Modify: `prompts/injection-exploit.txt`、`xss-exploit.txt`、`auth-exploit.txt`、`authz-exploit.txt`、`ssrf-exploit.txt`、`attack-chain.txt`（加两个 include）
- Modify: `packages/core/src/supernova_core/collectors/exploit.py`（add_exploit 加 `cwe_id` optional）
- Test: `packages/core/tests/prompts/test_vuln_prompt_schema_contract.py`（同步锁定）、新建 `packages/core/tests/prompts/test_report_style_includes.py`

**Interfaces:**
- Produces: LLM 轨 finding 新输出字段 `severity`（enum critical/high/medium/low）、`impact`（危害一句话）、`remediation`（修复一句话）、`cwe_id`（如 "CWE-95"）——全部 optional（向后兼容旧 collector 消息）
- Produces: `_report-style` include 内容（zh 版见 Step 3，en 版对应翻译）；`@include(shared/_report-style.txt)` 按既有 `_output-language` 双文件机制随 narration lang 解析
- 渲染端衔接：Task 5 的危害优先级变为 `impact > notes > _IMPACT_FALLBACK`

- [ ] **Step 1: 写失败测试**

```python
# packages/core/tests/prompts/test_report_style_includes.py（新建）
from pathlib import Path

PROMPTS = Path(__file__).parents[3] / "prompts"
VULN_PROMPTS = ["vuln-injection.txt", "vuln-xss.txt", "vuln-auth.txt",
                "vuln-authz.txt", "vuln-ssrf.txt"]
EXPLOIT_PROMPTS = ["injection-exploit.txt", "xss-exploit.txt", "auth-exploit.txt",
                   "authz-exploit.txt", "ssrf-exploit.txt"]

def test_vuln_prompts_have_style_and_new_fields():
    for name in VULN_PROMPTS:
        text = (PROMPTS / name).read_text()
        assert "@include(shared/_report-style.txt)" in text, name
        for field in ("severity", "impact", "remediation", "cwe_id"):
            assert field in text, f"{name}: {field}"

def test_exploit_prompts_have_language_and_style():
    for name in EXPLOIT_PROMPTS + ["attack-chain.txt"]:
        text = (PROMPTS / name).read_text()
        assert "@include(shared/_output-language.txt)" in text, name
        assert "@include(shared/_report-style.txt)" in text, name

def test_style_include_exists_both_langs():
    zh = (PROMPTS / "shared" / "_report-style.zh.txt").read_text()
    assert "结论先行" in zh and "不要使用全大写" in zh
    en = (PROMPTS / "shared" / "_report-style.en.txt").read_text()
    assert len(en) > 100

def test_no_deterministic_artifacts_include():  # 守双轨铁律
    for name in VULN_PROMPTS:
        text = (PROMPTS / name).read_text()
        for banned in ("parameter_graph", "SinkCallSite", "gitnexus_queue"):
            assert banned not in text, f"{name}: {banned}"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/prompts/test_report_style_includes.py -v`
Expected: FAIL

- [ ] **Step 3: 实现**

`prompts/shared/_report-style.zh.txt`（en 版对应翻译）：

```text
<report-style>
写作风格（所有叙述性输出必须遵守）：
- 结论先行：每段第一句给出定性结论，证据随后；不要铺垫。
- 一段一事：影响描述不超过 3 句；枚举内容用列表，不要在段落里嵌 (1)(2)(3) 编号。
- 不要使用全大写单词作强调（如 CONFIRM/PROVE），不要戏剧化措辞
  （如 undeniable proof / total compromise 收尾句），不要元话语（如"供领导参考"）。
- 语言通俗且专业：用安全行业通用术语（存储型 XSS、越权、未授权访问、注入），
  避免分析器内部术语（不要写 taint/sink/链路判定这类词进叙述）。
- 修复建议必须代码级具体：写清改哪个函数、换成什么写法，
  不写"建议加强输入校验"这类空话。
- severity 取值只用 critical/high/medium/low，依据实际影响定档，不要一律 critical。
</report-style>
```

改动清单：
1. `collectors/vuln.py` `_finding_props` 共通 props 追加：`"severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]}`、`"impact": {"type": "string"}`、`"remediation": {"type": "string"}`、`"cwe_id": {"type": "string"}`（均 optional，不动 `_FINDING_BASE_REQUIRED`）
2. 5 个 `vuln-*.txt`：字段表各加这 4 个字段的一行说明（severity/impact/remediation/cwe_id，说明文字引用风格指南）+ 文件尾加 `@include(shared/_report-style.txt)`
3. 5 个 `*-exploit.txt` + `attack-chain.txt`：头部 role 段后加 `@include(shared/_output-language.txt)` 与 `@include(shared/_report-style.txt)`
4. `collectors/exploit.py` add_exploit schema 加 `"cwe_id"` optional（黑盒卡片补 CWE 渲染的数据源；黑盒已有 severity/impact 不动）
5. 同步 `test_vuln_prompt_schema_contract.py`：新字段进 prompt↔schema 锁定断言

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/prompts/test_report_style_includes.py packages/core/tests/prompts/test_vuln_prompt_schema_contract.py packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py -v`
Expected: PASS（含铁律锁定测试）

- [ ] **Step 5: Commit**

```bash
git add prompts/ packages/core/src/supernova_core/collectors/vuln.py packages/core/src/supernova_core/collectors/exploit.py packages/core/tests/prompts/
git commit -m "feat(report): 报告风格指南 shared include+collector 新字段(severity/impact/remediation/cwe_id)+exploit/attack-chain prompt 补语言与风格

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: report-executive 摘要指令重写

**Files:**
- Modify: `prompts/report-executive.txt`（objective 段摘要结构指令 + cleanup 规则）
- Test: 新建 `packages/core/tests/prompts/test_report_executive_directives.py`

**Interfaces:**
- Produces: 摘要四段结构指令——①总体态势（保留现有优点）②关键数字（**从「漏洞速查表」章节读取计数，禁止自行数卡片、禁止使用"单点卡片/GN/合并"等内部口径）③最高危攻击面按风险排序（保留）④修复路线（P0=严重+高危且公网可达、P1=其余高危/中危，各一句话依据）
- Cleanup 规则追加：**「漏洞速查表」章节禁止删除或改写**（含表体与标题）；执行摘要禁止出现 `待复核`/`GN-`/`merge_source` 等内部概念；删除 audience 段的元话语指令

- [ ] **Step 1: 写失败测试**

```python
# packages/core/tests/prompts/test_report_executive_directives.py
from pathlib import Path

TEXT = (Path(__file__).parents[3] / "prompts" / "report-executive.txt").read_text()

def test_summary_structure_directives():
    assert "漏洞速查表" in TEXT
    assert "禁止删除" in TEXT or "不得删除" in TEXT
    assert "修复路线" in TEXT and "P0" in TEXT and "P1" in TEXT
    for banned in ("CTOs, CISOs", "单点卡片"):
        assert banned not in TEXT, banned
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/prompts/test_report_executive_directives.py -v`
Expected: FAIL（现 prompt 无速查表约束/P0P1，有 CTOs, CISOs）

- [ ] **Step 3: 实现**

`report-executive.txt` 修改：
1. `<audience>` 段：删 "Technical leadership (CTOs, CISOs, Engineering VPs)" 改为"报告读者：安全负责人与研发负责人——技术准确 + 表达直接"（去掉元话语式身份罗列）
2. objective 摘要指令改为四段结构（总体态势/关键数字——引用速查表/最高危攻击面/修复路线 P0、P1），数字段指令原文包含："从「漏洞速查表」章节读取各类计数与严重度分布；禁止自行清点漏洞卡片；禁止使用『单点卡片』『GN』『合并』等流水线内部术语。"
3. cleanup 列表追加一条："「漏洞速查表」整章（标题到表格结束）禁止删除、改写或重排——它是确定性生成的读者索引。"
4. 保留既有 HARD CONSTRAINT（禁脚本重写）与 LANGUAGE 段不动

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/prompts/test_report_executive_directives.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add prompts/report-executive.txt packages/core/tests/prompts/test_report_executive_directives.py
git commit -m "feat(report): report-executive 摘要四段结构(态势/速查表数字/攻击面/P0P1 修复路线)+速查表保护+内部术语禁令

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: web 前端 severity 读真数据

**Files:**
- Modify: `packages/web/frontend/src/lib/vuln-block.ts`（`inferSeverity` :60 优先解析卡片元信息行；新导出 `parseMetaSeverity`）
- Test: `packages/web/frontend/src/lib/vuln-block.test.ts`（追加）

**Interfaces:**
- Produces: `parseMetaSeverity(block: ParsedVulnBlock): Severity | null`——从卡片 body 首个元信息行匹配 `严重程度：严重|高危|中危|低危` → `critical|high|medium|low`；不匹配返回 `null`
- `inferSeverity(block, topRiskIds)` 行为：先 `parseMetaSeverity`，非 null 直接返回；null 落回现有启发式（旧报告兼容）。**不改 `parseVulnBlock` 的 ID 解析**（`vuln-block.ts:111` 注释警示 topRiskIds 联动断链回归 hr_20260713，本任务不触碰 ID 逻辑）

- [ ] **Step 1: 写失败测试**

```typescript
// packages/web/frontend/src/lib/vuln-block.test.ts 追加
import { inferSeverity, parseMetaSeverity, splitByVulnBlocks } from './vuln-block';

const CARD_MD = [
  '### INJ-VULN-01 注入漏洞：命令注入',
  '严重程度：严重 ｜ CWE-95 ｜ 验证：静态分析 ｜ 置信度：高（双轨确认）',
  '',
  '**漏洞说明**',
  'preTax 直接传入 eval()。',
].join('\n');

describe('parseMetaSeverity', () => {
  it('reads severity from the card meta line', () => {
    const blocks = splitByVulnBlocks(CARD_MD);
    const b = blocks.find((x) => x.id === 'INJ-VULN-01');
    expect(b).toBeDefined();
    expect(parseMetaSeverity(b!)).toBe('critical');
    expect(inferSeverity(b!)).toBe('critical');
  });
  it('falls back to heuristic when meta line absent (legacy reports)', () => {
    const legacy = splitByVulnBlocks('### INJ-VULN-02 Old style\n\nsome body');
    const b = legacy.find((x) => x.id === 'INJ-VULN-02');
    expect(parseMetaSeverity(b!)).toBeNull();
    // 启发式兜底仍工作（不抛错）
    expect(['critical', 'high', 'medium', 'low']).toContain(inferSeverity(b!));
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/vitest run src/lib/vuln-block.test.ts`
Expected: FAIL（`parseMetaSeverity` 未导出）

- [ ] **Step 3: 实现**

```typescript
// vuln-block.ts 追加（Severity 类型沿用文件内既有定义）
const META_SEVERITY_ZH: Record<string, Severity> = {
  '严重': 'critical', '高危': 'high', '中危': 'medium', '低危': 'low',
};

export function parseMetaSeverity(block: ParsedVulnBlock): Severity | null {
  const m = block.body.match(/严重程度[：:]\s*(严重|高危|中危|低危)/);
  return m ? META_SEVERITY_ZH[m[1]] : null;
}

// inferSeverity 函数体开头插入：
//   const meta = parseMetaSeverity(block);
//   if (meta) return meta;
// 其余启发式逻辑原样保留
```

（`block.body` 的实际字段名以 `ParsedVulnBlock` 现有定义为准——若 body 属性名不同，用对应字段；实现时先读该 interface 定义。）

- [ ] **Step 4: 跑测试确认通过（含既有用例回归）**

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/vitest run src/lib/vuln-block.test.ts src/lib/vuln-block.smoke.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/web/frontend/src/lib/vuln-block.ts packages/web/frontend/src/lib/vuln-block.test.ts
git commit -m "feat(report-web): 漏洞卡 severity 优先读卡片元信息行,启发式降为旧报告兜底

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: 全链回归 + 白盒渲染 fixture 端到端

**Files:**
- Create: `packages/core/tests/test_report_readability_e2e.py`
- 无生产代码改动（纯回归 + 断言总装）

**Interfaces:**
- Consumes: Task 1-8 全部产出

- [ ] **Step 1: 写端到端 fixture 测试**

```python
# packages/core/tests/test_report_readability_e2e.py
"""端到端：LLM 1 条 + GN 9 条（同单位笛卡尔积）→ 收敛/合并 → 渲染 → 速查表 →
报告级断言（spec 验收口径）。"""
import pytest
from supernova_core.code_index.dual_track_merger import merge_dual_track_queues
from supernova_core.models.queue_schemas import InjectionVulnerability
from supernova_core.services.findings_renderer import render_vuln_card
from supernova_core.services.report_assembler import render_summary_table

def _llm(): ...   # 复用 test_dual_track_merger.py 的 _llm_inj 构造
def _gn_9(): ...  # 复用 _gn_inj 构造 9 条笛卡尔积

@pytest.mark.asyncio
async def test_e2e_nine_gn_one_llm_become_one_card():
    merged = merge_dual_track_queues([_llm()], _gn_9())
    assert len(merged) == 1
    card = render_vuln_card(merged[0], "injection", snippet=None)
    # 四要素齐 + 内部标签零出现 + 入口 10 行
    for s in ("**漏洞说明**", "**危害**", "**问题代码**", "**修复建议**", "**受影响入口**"):
        assert s in card
    for banned in ("llm-pass-failed", "needs_review", "unparseable-llm"):
        assert banned not in card
    table = render_summary_table({"injection": merged})
    assert table.count("\n| INJ-") == 1  # 速查表按归并单位计数（1 行非 10 行）
```

- [ ] **Step 2: 跑全部本 plan 相关测试**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/models/test_severity_rules.py packages/core/tests/code_index/test_gn_collapse.py packages/core/tests/code_index/test_dual_track_merger.py packages/core/tests/services/test_code_snippet.py packages/core/tests/test_findings_renderer.py packages/core/tests/test_report_assembler.py packages/core/tests/services/test_report_assembler.py packages/core/tests/prompts/test_report_style_includes.py packages/core/tests/prompts/test_report_executive_directives.py packages/core/tests/prompts/test_vuln_prompt_schema_contract.py packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py packages/core/tests/test_report_readability_e2e.py -v`
Expected: PASS 全绿

Run: `cd /root/shannon-py/packages/web/frontend && ./node_modules/.bin/vitest run src/lib/vuln-block.test.ts src/lib/vuln-block.smoke.test.ts && ./node_modules/.bin/tsc --noEmit`
Expected: PASS + 0 类型错误

- [ ] **Step 3: （手动，标注给用户）真机冒烟**

重扫一次 NodeGoat 白盒验证观感（**worker 容器须先 rebuild**：改动涉及 core 包）。检查点：四要素卡片、速查表首章、GN 笛卡尔积消失、中文全文、执行摘要无内部口径。此步不在 CI 内，完成后回报用户。

- [ ] **Step 4: Commit**

```bash
git add packages/core/tests/test_report_readability_e2e.py
git commit -m "test(report): 白盒报告可读性端到端回归(9+1→1 卡/四要素/速查表/内部标签零出现)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Self-Review 记录

- **Spec 覆盖**：§4 schema→T1；§3.2 GN 收敛→T2；§3.3 跨轨 key→T3；§10.4 snippet→T4；§5/§6 卡片与降级→T5；§7 速查表→T6；§8 风格指南+§10.6→T7；§7 摘要→T8；§10.7 前端→T9；§11 回归→T10。§9 内部概念剥离分散于 T5（llm-pass-failed/GN 标题）+T6（Xss/Count）+T8（executive 禁令）；§9 轨道注记移附录（inject_gitnexus_track_status）**未覆盖**——降级为可选后续项：该 activity 注入的是章末状态段，不进漏洞卡片正文，观感影响小，避免本 plan 膨胀（若用户要求，追加 0.5 任务调整 activity 输出位置至文末附录）。
- **占位符扫描**：T6 Step 3 与 T5 Step 3 的实现段以"要点+伪码"给出而非全量代码（模板字符串较长），关键断言已由 Step 1 测试锁定；T9 已注明 `ParsedVulnBlock.body` 字段名以现有定义为准。可接受。
- **类型一致性**：`effective_severity`/`effective_severity_from_str`/`max_severity`（T1 定义，T2/T3/T6 消费）；`collapse_gn_entries`（T2 定义，T3 在 merger 入口消费）；`render_vuln_card(vuln, vuln_class, snippet)`（T5 定义，T10 消费）；`render_summary_table(queues_by_class)`（T6 定义，T10 消费）——签名一致。
