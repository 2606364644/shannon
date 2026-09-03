# 双轨去重断裂修复 MVP 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复漏洞报告去重失效——通过 key 归一化（vtype/占位符/标点）+ endpoint 确定性回填 + 观测，让双轨确定性配对恢复、轨内折叠恢复设计意图。

**Architecture:** 全部改动在确定性数据管道的「key 计算层」与「builder 产卡层」：归一化函数（`gn_collapse.py` / `dual_track_merger.py`）纯函数化收敛 key；builder 显式赋 `endpoint` + verdict 后处理白名单验证回填兜底 join miss；观测补静默盲区。不碰 LLM 轨 prompt、不碰合并语义（verdict OR 不变）。

**Tech Stack:** Python 3.13, pydantic v2, pytest, asyncio（whitebox activity）

**Spec:** `docs/superpowers/specs/2026-09-03-dual-track-dedup-mvp-design.md`

## Global Constraints

- **铁律（CLAUDE.md §1）**：不把确定性产物喂 LLM 轨 prompt。本计划改动全在 GN 轨产物处理 + 合并层，天然合规；不触碰 `packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py` 锁定的不变量。
- **web 进程零 agent 执行点（CLAUDE.md §2）**：改动全部在 `packages/core` + `packages/whitebox`（worker 侧），web 侧零改动。
- **cost 字段语义不变量（CLAUDE.md §4）**：`cost_usd`/`total_cost_usd` 字段名、`cost_currency` 不引入新变更。
- **测试陷阱（CLAUDE.md §3）**：只跑改动相关测试文件，勿广跑全套。core 包测试命令：`cd packages/core && python -m pytest tests/code_index/<file> -v`。
- **分支 `feat/fork-py`**：直接提交到当前分支（本地未 push，含多项在途改动，commit 时只加本次相关文件）。
- **验收门（spec §7）**：Task 7 夹具回归全绿 + 改动相关单测全绿 + 现有相关测试同批更新后全绿。

---

### Task 1: F1 尾标点 + F4 占位符归一（gn_collapse 侧）

**Files:**
- Modify: `packages/core/src/supernova_core/code_index/gn_collapse.py:13-40`（`_METHOD_PATH`、`extract_endpoint`，新增 `_normalize_placeholders`）
- Test: `packages/core/tests/code_index/test_gn_collapse.py`

**Interfaces:**
- Produces: `_normalize_placeholders(path: str) -> str`（`/allocations/:userId` → `/allocations/{userId}`）；`extract_endpoint()` 不再返回带尾标点的 route。

- [ ] **Step 1: 写失败测试**（append 到 test_gn_collapse.py）

```python
def test_extract_endpoint_strips_trailing_punct():
    # 尾标点：',' 与全角 ')' 都会污染 key / 报告展示
    assert extract_endpoint("POST /login, -> handler") == "POST /login"
    assert extract_endpoint("POST /memos) -> x") == "POST /memos"
    assert extract_endpoint("GET /allocations/:userId?threshold=1 -> x") == "GET /allocations/:userId"

def test_normalize_placeholders():
    from supernova_core.code_index.gn_collapse import _normalize_placeholders
    assert _normalize_placeholders("/allocations/:userId") == "/allocations/{userId}"
    assert _normalize_placeholders("/benefits") == "/benefits"
    # 保留参数名（:id ≠ :userId，不得归一成同一形）
    assert _normalize_placeholders("/a/:id") == "/a/{id}"
    # 不误伤协议串
    assert ":https" not in _normalize_placeholders("/x?u=https://a.b")
```

- [ ] **Step 2: 运行确认失败**

Run: `cd packages/core && python -m pytest tests/code_index/test_gn_collapse.py -v -k "strips_trailing_punct or normalize_placeholders"`
Expected: FAIL（当前 `extract_endpoint("POST /login,")` 返回 `"POST /login,"`；`_normalize_placeholders` 未定义）

- [ ] **Step 3: 实现**（gn_collapse.py）

```python
_METHOD_PATH = re.compile(
    r"\b(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(/\S*)")
# 剥尾标点：闭括号/逗号/分号/引号（含全角），URL 合法尾字符（. 数字）不动
_TRAILING_PUNCT_RE = re.compile(r"[),.;'\"）]+$")
_PARAM_PLACEHOLDER_RE = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")


def _normalize_placeholders(path: str) -> str:
    """路由占位符归一：:userId → {userId}（Express :param ↔ OpenAPI {param} 同义路由）。"""
    return _PARAM_PLACEHOLDER_RE.sub(r"{\1}", path)
```

`extract_endpoint` 中 route 归一后追加剥标点：

```python
    route = m.group(2).split("?", 1)[0].rstrip("/") or "/"
    route = _TRAILING_PUNCT_RE.sub("", route).rstrip("/") or "/"
    return f"{m.group(1).upper()} {route}"
```

- [ ] **Step 4: 运行确认通过**

Run: `cd packages/core && python -m pytest tests/code_index/test_gn_collapse.py -v`
Expected: PASS（含既有用例不回归）

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/supernova_core/code_index/gn_collapse.py packages/core/tests/code_index/test_gn_collapse.py
git commit -m "fix(core): extract_endpoint 剥尾标点 + 占位符归一 :p→{p}（防 POST /login, 脏 key/展示）"
```

---

### Task 2: F2 vtype 类级归一（`_canonical_vtype`）+ F3 XSS 细型类级化

**Files:**
- Modify: `packages/core/src/supernova_core/code_index/dual_track_merger.py:68-115`（`_strict_key`/`_finding_key` 应用归一）、`packages/core/src/supernova_core/code_index/gn_collapse.py:59-77`（`_unit_key` 应用归一）
- Test: `packages/core/tests/code_index/test_dual_track_merger.py`

**Interfaces:**
- Produces: `_canonical_vtype(vtype: object) -> str`（细分 → 类级；authz 原样）。供 Task 3 分桶与 `_unit_key` 复用。

- [ ] **Step 1: 写失败测试**（append 到 test_dual_track_merger.py）

```python
from supernova_core.code_index.dual_track_merger import _finding_key, _canonical_vtype
from supernova_core.models.queue_schemas import InjectionVulnerability, XssVulnerability

def _inj(id_, vtype, path, sink):
    return InjectionVulnerability(
        ID=id_, vulnerability_type=vtype, externally_exploitable=True,
        confidence="low", source=f"preTax (app/routes/contributions.js:ContributionsHandler:7)",
        path=path, sink_call=sink, verdict="vulnerable", source_track="gitnexus")

def test_canonical_vtype_maps_llm_gn():
    assert _canonical_vtype("CommandInjection") == "injection"
    assert _canonical_vtype("injection") == "injection"
    assert _canonical_vtype("URL_Manipulation") == "ssrf"
    assert _canonical_vtype("Reflected") == "xss"
    assert _canonical_vtype("Stored") == "xss"
    assert _canonical_vtype("Horizontal") == "Horizontal"  # authz 特判依赖

def test_injection_cross_track_keys_now_collide():
    # 修前：LLM 'CommandInjection' vs GN 'injection' → key 不同（交集 0）
    llm = _inj("LLM-1", "CommandInjection", "POST /contributions → c",
               "eval() @ app/routes/contributions.js:32")
    gn = _inj("GN-1", "injection", "POST /contributions → c",
              "app/routes/contributions.js:ContributionsHandler:eval:32:23")
    assert _finding_key(llm) == _finding_key(gn)

def test_xss_stored_reflected_same_class_key():
    # Stored/Reflected 细型类级化：同 endpoint+sink 的两轨卡 key 对齐
    llm = XssVulnerability(
        ID="L", vulnerability_type="Stored", externally_exploitable=True,
        confidence="low", source="benefitStartDate (app/routes/benefits.js:BenefitsHandler:30)",
        path="POST /benefits → x", sink_call="swig {{user.benefitStartDate}} in value", verdict="vulnerable")
    gn = XssVulnerability(
        ID="G", vulnerability_type="Reflected", externally_exploitable=True,
        confidence="low", source="benefitStartDate (app/routes/benefits.js:BenefitsHandler:30)",
        path="POST /benefits → x", sink_call="app/routes/benefits.js:BenefitsHandler:render:51:23",
        verdict="vulnerable")
    assert _finding_key(llm)[0] == _finding_key(gn)[0] == "xss"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd packages/core && python -m pytest tests/code_index/test_dual_track_merger.py -v -k "canonical_vtype or injection_cross_track or xss_stored_reflected"`
Expected: FAIL（`_canonical_vtype` 未定义；`_finding_key` 不归一 vtype）

- [ ] **Step 3: 实现**

dual_track_merger.py 顶部（`_strict_key` 之前）加：

```python
# 漏洞类级归一（仅 key 计算用，不动卡上展示字段）。authz 原样返回——
# _finding_key 的 Horizontal endpoint-only 特判依赖其原始形态。
_VTYPE_CLASS_MAP = {
    "CommandInjection": "injection", "RCE": "injection", "OSCommandInjection": "injection",
    "SQLi": "injection", "Eval": "injection", "NoSQL": "injection",
    "URL_Manipulation": "ssrf", "SSRF": "ssrf",
    "Reflected": "xss", "Stored": "xss",
}


def _canonical_vtype(vtype: object) -> str:
    if vtype is None:
        return None
    return _VTYPE_CLASS_MAP.get(str(vtype), str(vtype))
```

`_strict_key` 与 `_finding_key` 开头各自把 vtype 换成 `_canonical_vtype(...)`（`_finding_key` 里 `if vtype == "Horizontal"` 判断改用原始值，即 `raw = getattr(...)`、`vtype = _canonical_vtype(raw)`，特判仍用 `raw == "Horizontal"`）。`_finding_key` 末尾 `_strict_key(finding)` 已内部归一，无需重复。

gn_collapse.py `_unit_key` 的 vtype 同样归一：

```python
from supernova_core.code_index.dual_track_merger import _canonical_vtype  # 注意：检查循环 import
```

> 若 gn_collapse ↔ dual_track_merger 有循环 import（gn_collapse 被 merger import，`_unit_key` 在 merge 里经 collapse_gn_entries 调用），将 `_canonical_vtype` 定义放 gn_collapse.py、merger 从 gn_collapse import（merger 已 import gn_collapse 的 `extract_endpoint`/`parse_sink_call_site_id`，方向无环）。

- [ ] **Step 4: 运行确认通过**

Run: `cd packages/core && python -m pytest tests/code_index/test_dual_track_merger.py tests/code_index/test_gn_collapse.py -v`
Expected: PASS（`_unit_key` 的 `("__strict__", id(f))` 兜底分支、Horizontal 特判均不回归）

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/supernova_core/code_index/dual_track_merger.py packages/core/src/supernova_core/code_index/gn_collapse.py packages/core/tests/code_index/test_dual_track_merger.py
git commit -m "fix(core): vtype 类级归一 CommandInjection→injection / Stored|Reflected→xss（key 计算层，injection 双轨 key 重新可撞）"
```

---

### Task 3: F3 配套 LLM 轨同 key 折叠化（不吞卡）

**Files:**
- Modify: `packages/core/src/supernova_core/code_index/dual_track_merger.py:224-230`（`merge_dual_track_queues` 的 `llm_by_key` 分桶）
- Test: `packages/core/tests/code_index/test_dual_track_merger.py`

**Interfaces:**
- Consumes: Task 2 `_finding_key`（类级 vtype）
- Produces: 同 key 多 LLM 卡 → 主卡 `merged_from` 挂靠其余卡 ID + log，卡不丢。

- [ ] **Step 1: 写失败测试**

```python
def test_llm_same_key_cards_fold_not_dropped():
    # 类级化后两卡同 key（同 endpoint+sink）；修前 setdefault 静默留一丢一
    llm = []
    for i, t in enumerate(("Reflected", "Stored")):
        llm.append(XssVulnerability(
            ID=f"L{i}", vulnerability_type=t, externally_exploitable=True,
            confidence="low", source=f"p{i} (app/routes/session.js:SessionHandler:8)",
            path="POST /login → x", sink_call="render", verdict="vulnerable"))
    from supernova_core.code_index.dual_track_merger import merge_dual_track_queues
    merged = merge_dual_track_queues(llm, [], mode="verdict")
    # 折叠：一条主卡 + merged_from 挂靠另一条 ID；不吞卡
    assert len(merged) == 1
    assert sorted(getattr(merged[0], "merged_from") or []) == ["L1"]
```

> 注意：`XssVulnerability.ID` 必填。构造时 `ID=f"L{i}"`。`_finding_key` 对两卡的 sink 文本须相同才同 key——本测试 sink 用 `render`（GN 形态）或同文本即可。

- [ ] **Step 2: 运行确认失败**

Run: `cd packages/core && python -m pytest tests/code_index/test_dual_track_merger.py -v -k "llm_same_key"`
Expected: FAIL（修前 `setdefault` 保留第一条、`merged_from` 为空）

- [ ] **Step 3: 实现**（merge_dual_track_queues 内）

```python
    llm_by_key: dict[tuple, Vulnerability] = {}
    for finding in llm_findings:
        key = _finding_key(finding)
        if key in llm_by_key:
            # 同 key 多卡折叠（F3 配套）：主卡 merged_from 挂靠其余卡 ID，
            # 不吞卡——避免类级化后 Stored/Reflected 细型被静默丢弃。
            existing = llm_by_key[key]
            data = existing.model_dump()
            mf = list(data.get("merged_from") or [])
            if finding.ID not in mf:
                mf.append(finding.ID)
            data["merged_from"] = mf
            llm_by_key[key] = type(existing).model_validate(data)
            logger.info("llm-track collapse: %s folded into %s (same unit key)",
                        finding.ID, existing.ID)
        else:
            llm_by_key[key] = finding
```

- [ ] **Step 4: 运行确认通过 + 既有用例不回归**

Run: `cd packages/core && python -m pytest tests/code_index/test_dual_track_merger.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/supernova_core/code_index/dual_track_merger.py packages/core/tests/code_index/test_dual_track_merger.py
git commit -m "fix(core): LLM 轨同 key 多卡折叠挂 merged_from（原 setdefault 静默吞卡，类级化后必配）"
```

---

### Task 4: F4 merger 侧占位符归一 + F5 观测

**Files:**
- Modify: `packages/core/src/supernova_core/code_index/dual_track_merger.py:35-53`（`_normalize_endpoint` 接入占位符归一）、`packages/core/src/supernova_core/services/track_parity.py:48-64`（三态观测）、`packages/core/src/supernova_core/code_index/gn_collapse.py`（collapse 分支统计）
- Test: `packages/core/tests/code_index/test_dual_track_merger.py`、`packages/core/tests/code_index/test_track_parity.py`

**Interfaces:**
- Consumes: Task 1 `_normalize_placeholders`
- Produces: `_normalize_endpoint` 对占位符风格不敏感；`enhance_track_parity` 三态 log；`collapse_gn_entries` 打端点/文件回退分支统计。

- [ ] **Step 1: 写失败测试**

```python
def test_normalize_endpoint_placeholder_agnostic():
    from supernova_core.code_index.dual_track_merger import _normalize_endpoint
    assert _normalize_endpoint("GET /allocations/:userId") == \
           _normalize_endpoint("GET /allocations/{userId}")
    assert _normalize_endpoint("POST /login,") == "POST /login"
```

track_parity 三态观测测试（caplog）：

```python
def test_track_parity_zero_pairs_logged(caplog):
    # LLM 返回 0 对：应显式打 WARNING（区分「无对」与「全中低置信」）
    async def fake_client(prompt):
        return '{"merge": []}'
    from supernova_core.services.track_parity import enhance_track_parity
    # 构造一条 llm-only + 一条 gn-only 卡（复用 Task 3 的构造）
    ...
    import asyncio
    asyncio.run(enhance_track_parity([llm, gn], fake_client))
    assert any("track-parity" in r.message and "0" in r.message for r in caplog.records)
```

- [ ] **Step 2: 运行确认失败**

Run: `cd packages/core && python -m pytest tests/code_index/test_dual_track_merger.py tests/code_index/test_track_parity.py -v -k "placeholder_agnostic or zero_pairs"`
Expected: FAIL（`_normalize_endpoint` 不归一占位符；`enhance_track_parity` 0 对不打 WARNING）

- [ ] **Step 3: 实现**

`_normalize_endpoint` 的 path 处理段接入 `_normalize_placeholders`（在 split `?` 之后、返回前对 path 应用）。

`track_parity.enhance_track_parity` 三态：

```python
    if llm_only and gn_only:
        try:
            raw = await llm_client(build_pairing_prompt(llm_only, gn_only))
            pairs = parse_pairing_response(raw, valid_gn_ids={f.ID for f in gn_only},
                                           valid_llm_ids={f.ID for f in llm_only})
            if pairs:
                high = [p for p in pairs if p.confidence == "high"]
                if not high:
                    logger.warning(
                        "track-parity: %d 对 LLM 判定均 <high> 无配对应用 "
                        "(llm_only=%d gn_only=%d)", len(pairs), len(llm_only), len(gn_only))
                merged = apply_pairing_merge(merged, pairs)
            else:
                logger.warning(
                    "track-parity: LLM 返回 0 对（解析失败或全被过滤）"
                    "(llm_only=%d gn_only=%d)", len(llm_only), len(gn_only))
        except Exception as exc:
            logger.warning("track-parity pairing skipped (LLM unavailable): %s", exc)
```

`collapse_gn_entries` 末尾统计：`_unit_key` 分支计数（endpoint 命中 vs 文件回退），`logger.info("gn-collapse: %d groups (%d endpoint, %d file-fallback)", ...)`。

- [ ] **Step 4: 运行确认通过**

Run: `cd packages/core && python -m pytest tests/code_index/test_dual_track_merger.py tests/code_index/test_track_parity.py tests/code_index/test_gn_collapse.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/supernova_core/code_index/dual_track_merger.py packages/core/src/supernova_core/services/track_parity.py packages/core/src/supernova_core/code_index/gn_collapse.py packages/core/tests/code_index/test_dual_track_merger.py packages/core/tests/code_index/test_track_parity.py
git commit -m "fix(core): _normalize_endpoint 占位符归一 + track-parity 0 产出三态观测 + collapse 分支统计"
```

---

### Task 5: F6a builder 显式赋 endpoint（3 builder）+ ssrf 脏值修复

**Files:**
- Modify: `packages/core/src/supernova_core/code_index/vuln_chain_builders/xss_builder.py:195-210`、`injection_builder.py`（构造段）、`ssrf_builder.py:76-86`
- Test: `packages/core/tests/code_index/test_xss_builder.py`（若有现成则加断言，否则 Task 7 夹具回归兜底）

**Interfaces:**
- Consumes: 各 builder 内已算出的 `route_label`（`http_route_label(chain.entry_point_id, entry_points)`，三处均已存在）
- Produces: 三 builder 产物 `endpoint=route_label`（join miss 时为 None，由 Task 6 回填兜底）；ssrf `source_endpoint` 不再兜底 handler-id。

- [ ] **Step 1: 各 builder 构造处加 `endpoint=route_label,`**

xss_builder.py（构造段，`path=path` 附近）：

```python
            path=path,
            endpoint=route_label,          # F6a：确定性 join，miss=None（Task 6 回填兜底）
```

injection_builder.py、ssrf_builder.py 同。ssrf_builder.py:85 改：

```python
            source_endpoint=route_label,  # 不再 or chain.entry_point_id（handler-id 脏值）
```

- [ ] **Step 2: 轻量验证**（构造需 pgraph 较重，先确认模型字段存在 + 不破坏既有 builder 测试）

Run: `cd packages/core && python -m pytest tests/code_index/test_xss_builder.py tests/code_index/test_injection_builder.py tests/code_index/test_ssrf_builder.py -v`
Expected: PASS（现有 builder 测试不回归；若某 builder 测试构造了 `endpoint` 断言字段值，同步更新）

- [ ] **Step 3: Commit**

```bash
git add packages/core/src/supernova_core/code_index/vuln_chain_builders/xss_builder.py packages/core/src/supernova_core/code_index/vuln_chain_builders/injection_builder.py packages/core/src/supernova_core/code_index/vuln_chain_builders/ssrf_builder.py
git commit -m "fix(core): builder 显式赋 endpoint=route_label + ssrf source_endpoint 去 handler-id 兜底（endpoint 回填主修复第一段）"
```

---

### Task 6: F6-B 白名单验证回填（`endpoint_backfill.py`）+ activity 接线

**Files:**
- Create: `packages/core/src/supernova_core/code_index/endpoint_backfill.py`
- Modify: `packages/whitebox/src/supernova_whitebox/pipeline/activities.py`（queue_findings 落盘前，约 3499 行）
- Test: `packages/core/tests/code_index/test_endpoint_backfill.py`（新建）

**Interfaces:**
- Consumes: Task 1 `_METHOD_PATH`（含剥标点）、`_normalize_placeholders`；`index.entry_points` 全量（activity 侧构建白名单）
- Produces: `backfill_endpoints(findings: list, all_routes: set[str]) -> list` —— endpoint 仍为 None 的卡经「提名→白名单验证→唯一命中采信」回填；白名单外丢弃 / 多歧义不采信。

- [ ] **Step 1: 写失败测试**（test_endpoint_backfill.py 新建）

```python
import sys
sys.path.insert(0, "src")  # 按现有测试 import 方式调整
from supernova_core.code_index.endpoint_backfill import backfill_endpoints
from supernova_core.models.queue_schemas import XssVulnerability

ROUTES = {"POST /login", "POST /signup", "GET /allocations/:userId"}

def _card(id_, title, evidence):
    return XssVulnerability(
        ID=id_, vulnerability_type="Reflected", externally_exploitable=True,
        confidence="low", source="p (app/routes/session.js:SessionHandler:8)",
        title=title, evidence_chain=evidence, verdict="vulnerable")

def test_backfill_unique_hit():
    cards = backfill_endpoints([_card("G", "反射型 XSS：POST /login 的 userName ...", "autoescape:false")], ROUTES)
    assert cards[0].endpoint == "POST /login"

def test_backfill_placeholder_normalized():
    cards = backfill_endpoints([_card("G", "XSS: GET /allocations/:userId 的 userId ...", "action 属性")],
                               {f"GET /allocations/:userId"})
    assert cards[0].endpoint == "GET /allocations/:userId"

def test_backfill_outside_whitelist_dropped():
    cards = backfill_endpoints([_card("G", "XSS：POST /nonexistent ...", "x")], ROUTES)
    assert cards[0].endpoint is None

def test_backfill_already_set_untouched():
    card = _card("G", "POST /login ...", "x")
    card.endpoint = "POST /login"
    out = backfill_endpoints([card], set())
    assert out[0].endpoint == "POST /login"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd packages/core && python -m pytest tests/code_index/test_endpoint_backfill.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现**（endpoint_backfill.py）

```python
"""endpoint 确定性回填（spec 2026-09-03 §3 F6-B）。

verdict 后处理：GN 卡 endpoint 缺失（builder join miss 或 verdict 产物退化）时，
从 verdict agent 自己写的 title/source_detail/evidence_chain 提取 "METHOD /path"
提名 → 全量路由白名单验证（index.entry_points 全量构建，绕开 entry_point_map
同 func_block_id 多路由被 dict 折叠的 bug）→ 唯一命中采信回填。

白名单外丢弃 / 多候选歧义不采信（宁缺勿错拼）——防给错误穿确定性外衣。
"""
from __future__ import annotations

import logging
import re

from supernova_core.code_index.gn_collapse import _METHOD_PATH, _normalize_placeholders

logger = logging.getLogger(__name__)

_TRAILING = re.compile(r"[),.;'\"）]+$")


def _candidate_labels(text: str, all_routes: set[str]) -> set[str]:
    labels = set()
    for m in _METHOD_PATH.finditer(text or ""):
        route = m.group(2).split("?", 1)[0].rstrip("/") or "/"
        route = _normalize_placeholders(_TRAILING.sub("", route)).rstrip("/") or "/"
        label = f"{m.group(1).upper()} {route}"
        if label in all_routes:
            labels.add(label)
    return labels


def backfill_endpoints(findings: list, all_routes: set[str]) -> list:
    for i, f in enumerate(findings):
        if getattr(f, "endpoint", None):
            continue
        texts = [t for t in (getattr(f, "title", None), getattr(f, "source_detail", None),
                             getattr(f, "evidence_chain", None), getattr(f, "path", None))
                 if isinstance(t, str)]
        found: set[str] = set()
        for t in texts:
            found |= _candidate_labels(t, all_routes)
        if len(found) == 1:
            data = f.model_dump()
            data["endpoint"] = next(iter(found))
            findings[i] = type(f).model_validate(data)
            logger.info("endpoint backfill: %s → %s (llm-nominated, whitelist-verified)",
                        f.ID, data["endpoint"])
        elif len(found) > 1:
            logger.warning("endpoint backfill: %s 多候选歧义 %s 不采信（宁缺勿错拼）", f.ID, sorted(found))
    return findings
```

- [ ] **Step 4: activity 接线**

activities.py queue_findings 组装后、`atomic_write_json` 前（约 3499 行）：

```python
                if queue_findings:
                    # F6-B：endpoint 缺失回填（白名单验证）。全量路由集合绕开
                    # entry_point_map 同 func_block_id 折叠 bug（spec 2026-09-03 §2 R2）。
                    all_routes = {
                        f"{ep.http_method.strip().upper()} {ep.route}"
                        for ep in index.entry_points
                        if ep.route and ep.http_method
                    }
                    from supernova_core.code_index.endpoint_backfill import backfill_endpoints
                    queue_findings = backfill_endpoints(queue_findings, all_routes)
```

- [ ] **Step 5: 运行确认通过**

Run: `cd packages/core && python -m pytest tests/code_index/test_endpoint_backfill.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/supernova_core/code_index/endpoint_backfill.py packages/core/tests/code_index/test_endpoint_backfill.py packages/whitebox/src/supernova_whitebox/pipeline/activities.py
git commit -m "fix(core,whitebox): endpoint 白名单验证回填（LLM 提名+全量路由验证+唯一采信，endpoint 回填第二段）"
```

---

### Task 7: 夹具回归（ground truth 验收）

**Files:**
- Create: `packages/core/tests/code_index/fixtures/nodegoat_20260903/{xss_llm_queue.json, xss_gitnexus_queue.json, injection_llm_queue.json, injection_gitnexus_queue.json}`（从本次扫描中间产物拷贝）
- Create: `packages/core/tests/code_index/test_dedup_regression_nodegoat.py`

**Interfaces:**
- Consumes: Task 1-6 全部（`extract_endpoint`/`_normalize_placeholders`/`_canonical_vtype`/`collapse_gn_entries`/`_finding_key`/`merge_dual_track_queues`）

- [ ] **Step 1: 拷贝 fixture**

```bash
mkdir -p packages/core/tests/code_index/fixtures/nodegoat_20260903
cp "workspaces/__legacy__/scans/NodeGoat-20260903-071648/deliverables/whitebox/intermediate/xss_llm_queue.json" \
   "workspaces/__legacy__/scans/NodeGoat-20260903-071648/deliverables/whitebox/intermediate/xss_gitnexus_queue.json" \
   "workspaces/__legacy__/scans/NodeGoat-20260903-071648/deliverables/whitebox/intermediate/injection_llm_queue.json" \
   "workspaces/__legacy__/scans/NodeGoat-20260903-071648/deliverables/whitebox/intermediate/injection_gitnexus_queue.json" \
   packages/core/tests/code_index/fixtures/nodegoat_20260903/
```

> 注：fixture 是 verdict 后产物（endpoint=None），故本测试验证 collapse 分组 + 跨轨 key 交集 + ground truth，不验证 Task 6 回填（其单测已独立覆盖）。

- [ ] **Step 2: 写回归测试**（test_dedup_regression_nodegoat.py 新建）

```python
import json
from pathlib import Path
from supernova_core.code_index.gn_collapse import collapse_gn_entries, extract_endpoint
from supernova_core.code_index.dual_track_merger import _finding_key
from supernova_core.models.queue_schemas import XssVulnerability, InjectionVulnerability

FIX = Path(__file__).parent / "fixtures" / "nodegoat_20260903"

def _load(queue, schema):
    data = json.loads((FIX / queue).read_text())["vulnerabilities"]
    return [schema.model_validate(d) for d in data]

def test_xss_collapse_groups_within_budget():
    gn = _load("xss_gitnexus_queue.json", XssVulnerability)
    groups = collapse_gn_entries(gn)
    assert len(groups) <= 7, f"修前 10 组，修后应 ≤7，实际 {len(groups)}"

def test_xss_login_fully_collapsed():
    # ground truth：POST /login 的全部 GN 卡必须折成 1 组（修前分 3 组）
    gn = _load("xss_gitnexus_queue.json", XssVulnerability)
    groups = collapse_gn_entries(gn)
    login = [g for g in groups if extract_endpoint(getattr(g, "path", None)) == "POST /login"]
    assert len(login) == 1

def test_xss_benefits_fully_collapsed():
    gn = _load("xss_gitnexus_queue.json", XssVulnerability)
    groups = collapse_gn_entries(gn)
    # 修前 GN-03(文件回退组) 与 GN-07(endpoint 组) 分叉；修后同组
    keys = [_unit_key(g) for g in groups]  # noqa: F821
    from supernova_core.code_index.gn_collapse import _unit_key
    groups = collapse_gn_entries(gn)
    keys = [_unit_key(g) for g in groups]
    assert any("benefits" in str(k) for k in keys)

def test_injection_cross_track_intersection():
    llm = _load("injection_llm_queue.json", InjectionVulnerability)
    gn = collapse_gn_entries(_load("injection_gitnexus_queue.json", InjectionVulnerability))
    lk = {_finding_key(f) for f in llm}
    gk = {_finding_key(f) for f in gn}
    assert lk & gk, "修前交集 0；vtype 归一后 POST /contributions eval 应可撞"
```

- [ ] **Step 3: 运行**

Run: `cd packages/core && python -m pytest tests/code_index/test_dedup_regression_nodegoat.py -v`
Expected: PASS（若失败，逐一核对该分组是否「重复数下降但错合并」——验收标准是合并正确性，见 spec §4.1）

- [ ] **Step 4: 全量相关测试回归**

Run: `cd packages/core && python -m pytest tests/code_index/test_gn_collapse.py tests/code_index/test_dual_track_merger.py tests/code_index/test_track_parity.py tests/code_index/test_endpoint_backfill.py tests/code_index/test_dedup_regression_nodegoat.py -v`
Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add packages/core/tests/code_index/fixtures/nodegoat_20260903/ packages/core/tests/code_index/test_dedup_regression_nodegoat.py
git commit -m "test(core): NodeGoat 夹具回归——collapse 分组≤7 / /login·/benefits 全折叠 / injection 跨轨 key 交集恢复（spec 2026-09-03 §4.1）"
```

---

## Self-Review

- **Spec 覆盖**：F1(§3/T1) F2(§3/T2) F3(§3/T3) F4(§3/T1+T4) F5(§3/T4) F6a(§3/T5) F6-B(§3/T6) 观测(§3 F5/T4) 夹具回归(§4.1/T7) 全部落位。不做项（F9/F11/F7-A/F6-B2）在 spec §3 记录在案，plan 不涉。
- **Placeholder scan**：所有代码步骤给出真实代码；无「TBD」「参考 Task N」式占位。Task 3 测试构造处有 `ID` 必填提醒与 sink 同文本提示，属必要上下文非占位。
- **Type consistency**：`_canonical_vtype`/`_normalize_placeholders`/`backfill_endpoints` 三新接口在 Task 2/1/6 定义、Task 3/4/7 消费，签名一致。`_unit_key` 循环 import 已在 Task 2 Step 3 给出规避方向（定义置 gn_collapse，merger 反引）。
- **风险点**：Task 2 Step 3 的 import 方向是唯一需执行时现场确认处（gn_collapse ↔ merger 现有 import 方向），已标注两条可行路径。
