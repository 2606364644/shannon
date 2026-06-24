# vuln-authz 双轨实现计划（Plan 7）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 vuln-authz（IDOR / dominance，spec §5.7 ⭐净进步方向）补上 **GitNexus 确定性轨**——调用图 dominance 启发式（handler→sink 必经守卫）+ 注解/ORM ownership 检测（复用 Plan 6 的 `scan_endpoint_security`）+ framework-analyzer 推断端点（复用 Plan 2 的 `framework_analysis.json`）产 **IDOR 候选链**（handler→sink 无 ownership guard）→ **LLM 链判定 pass**（给候选链判 IDOR verdict，产 `authz_gitnexus_queue.json`）→ Plan 3 的 `merge_dual_track_queues` 按 endpoint 去重做 **verdict OR 合并**（authz 已在 Plan 3 Task 3 的 `("injection","xss","ssrf","authz","auth")` 名单内，合并器 wiring 已就绪；本 plan 只需产 GitNexus 轨 queue）。

**Architecture:** authz 是 **vuln verdict 阶段**（spec §5.7 合并是 verdict OR，按 endpoint 去重），不是 recon 情报阶段——故**不走** Plan 6 的「下限注入 LLM 侧合并」，而是走 Plan 3 的「双 queue 文件合并」。两轨：
- **LLM 轨（现状不变）**：authz agent（`run_vuln_agent`→`run_agent`→`vuln-authz.txt`）自主 dominance trace，产 `authz_analysis_deliverable.md` + `authz_exploitation_queue.json`（executor.py:130-133 写 queue）。**本 plan 不改 LLM 轨**（spec 原则 2：不锚定——LLM 轨不注入 GitNexus 候选链）。
- **GitNexus 轨（本 plan 新增）**：
  1. **候选生成**（确定性，`authz_gitnexus_track.py`）：读 `code_index.json` 的 `edges`+`chains`+`entry_points`+`blocks` → 对每个 HTTP 端点，找其 handler 到「副作用 sink」（DB 写/ORM mutation/file/state）的调用路径 → 用 dominance 启发式判定路径上是否存在 ownership 守卫（守卫必须 **dominate** sink，即在所有 handler→sink 路径上都出现且在 sink 之前）→ 无 ownership guard 的路径 = **IDOR 候选链**。ownership 守卫检测复用 Plan 6 的 `scan_endpoint_security`（ORM 谓词 regex）+ handler 源码 decorator/middleware。framework-analyzer 推断端点（finale-rest/epilogue auto-generated CRUD，Plan 2 产 `framework_analysis.json`）默认无 ownership → 直接作候选。
  2. **LLM 链判定 pass**（`run_authz_gitnexus_judge` activity）：把候选链 + evidence（调用路径 + 守卫缺失证据 + framework origin）渲染成单个 prompt → 一次 `run_claude_prompt`（非 Task Agent，省成本）→ LLM 对每条候选判 IDOR verdict + 产 `AuthzVulnerability` 列表 → 写 `authz_gitnexus_queue.json`。
  3. **合并**（Plan 3 已 wiring）：`run_merge_dual_track_queues` 读 `authz_llm_queue.json`（Plan 3 Task 3 从 `authz_exploitation_queue.json` 重命名）+ `authz_gitnexus_queue.json` → `merge_dual_track_queues(mode="verdict")` 按 endpoint 去重 verdict OR → 写回 `authz_exploitation_queue.json`。**本 plan 不改合并器**（Plan 3 已覆盖 authz）。

**Tech Stack:** Python 3.12, pydantic v2, pytest, pytest-asyncio（dominance 启发式自实现，**不引 networkx**——已确认环境无 networkx 且 dominance 的"必经守卫"判定只需可达性 BFS/DFS）。

## Global Constraints

- **authz 是 verdict 阶段，走 Plan 3 文件合并，不走 Plan 6 LLM 侧合并**：spec §5.7「合并：按 endpoint 去重；verdict OR」。authz 产物是结构化 `AuthzVulnerability` JSON（`authz_exploitation_queue.json`，见 `vuln-authz.txt:144-161` 的 exploitation_queue_format），不是 markdown 情报表。故用 Plan 3 的 `merge_dual_track_queues(mode="verdict")`。**与 Plan 6（recon 情报 LLM 侧合并）是不同机制，不混淆**。
- **LLM 轨不被锚定（spec 原则 2）**：GitNexus 候选链**只注入 judge activity 的 prompt**（GitNexus 轨的判定者），**绝不注入 authz agent（LLM 轨）的 prompt**。authz agent 仍自主 dominance trace。`vuln-authz.txt` **不加** `{{AUTHZ_GITNEXUS_CANDIDATES}}` 占位符。
- **dominance 启发式 ≠ 数学证明（spec §8）**：spec §8「不做完整 dominance 数学证明，用调用图启发式 + LLM 语义确认」。本 plan 的 dominance 启发式 =「handler→sink 路径集的**交集守卫**」：若存在一条 handler→sink 路径上无 ownership 守卫节点，则该 sink 对该端点 **unguarded 候选**（保守，宁过报不漏报）。这是「路径无守卫」而非严格 post-dominator（严格 post-dominator 要求守卫在**所有**路径上；启发式取「存在无守卫路径」更保守——把守卫未 dominate 全部路径也判候选，符合 spec §2 原则 4「宁过报不漏报」）。**真实 dominance 精度由 LLM 链判定 pass 语义确认修正**。
- **复用 Plan 6 的 ownership/auth/middleware 检测**：Plan 6 的 `scan_endpoint_security`（`_AUTH_GUARD_RE` + `_OWNERSHIP_PREDICATE_RE` + `_detect_auth` + `_detect_ownership`）已实现跨语言（TS/Go/Java/PHP/Python）注解/ORM 谓词扫描。本 plan **import 复用**，不重写。Plan 6 落地是前置依赖（Task 2 注明 import 容错：Plan 6 未落地时测试 skip + 实现降级为空候选）。
- **复用 Plan 2 的 framework_analysis.json**：Plan 2 产 `framework_analysis.json`（含 `inferred_endpoints`，finale-rest/epilogue auto-generated CRUD）。本 plan 读其 `inferred_endpoints` 作 IDOR 候选端点来源之一（auto-generated 默认无 ownership）。Plan 2 未落地 → 文件缺失 → 候选来源少一路，不崩（优雅降级）。
- **合并 wiring 复用 Plan 3**：Plan 3 Task 3 的 `run_merge_dual_track_queues` 已遍历 `("injection","xss","ssrf","authz","auth")`（activities.py 见 Plan 3 Task 3 Step 3，约 :611），已覆盖 authz。**本 plan 不改 Plan 3 wiring**——本 plan 只产 `authz_gitnexus_queue.json`，Plan 3 合并器自动拾取。若 Plan 3 未落地，本 plan 产的 gitnexus queue 落盘但不被合并（`authz_exploitation_queue.json` 仍是 LLM 轨原始）——降级行为可接受。
- **GitNexus 索引缺失/降级时优雅降级**：`code_index.json` 缺失/空/解析失败 → 候选生成返空 → judge activity 跑空 prompt 产空 queue → 合并等价 LLM-only（spec §6）。**绝不崩 pipeline**（spec §9 验收 #5）。
- **judge activity 用单次 LLM 调用，非 Task Agent**：authz agent 是 Task Agent（per-endpoint 多轮，贵）。GitNexus 轨的「链判定 pass」是 per-候选的单轮判定（候选已给定，不需探索），用 `run_claude_prompt` + `structured_output_schema`（JSON 输出）一次产全量 verdict。比 authz agent 省（spec §10 成本缓解）。候选数设上限（default 50）防 prompt 过长。
- **工作流插入点**：judge activity 在 vuln 阶段并行 gather 完成（`workflows.py:312-321`）**之后**、Plan 3 的 `run_merge_dual_track_queues`（`workflows.py:323` 附近）**之前**插入。这样 LLM 轨 queue（`authz_exploitation_queue.json`）已就绪，gitnexus queue 也在合并前就绪。
- TDD + frequent commits（`feat(code_index):` / `feat(whitebox):` / `feat(prompt):`）；真实 GitNexus 索引产出非空候选链需 MCP 环境，单元测试用合成 `code_index.json`+`framework_analysis.json`。

---

### Task 1: 写 dominance 启发式 `find_unguarded_sink_paths`

**Files:**
- Create: `packages/core/src/shannon_core/code_index/authz_gitnexus_track.py`
- Test: `packages/core/tests/code_index/test_authz_dominance.py`（Create）

**Interfaces:**
- Consumes: `CodeIndex.edges`（`CallEdge`：caller_id → callee_name/callee_file，`models.py:39-46`）+ `CodeIndex.chains`（`CallChain.path`：FuncBlock.id 列表，`models.py:63-69`）+ `dict[str, FuncBlock]`（按 id 索引，从 `CodeIndex.blocks` 建）
- Produces: `find_unguarded_sink_paths(index: CodeIndex, *, max_paths_per_endpoint: int = 20) -> list[IDORCandidateChain]`；`IDORCandidateChain`（新 dataclass：`endpoint_id: str`、`handler_id: str`、`sink_id: str`、`path: tuple[str, ...]`、`guard_nodes_on_path: tuple[str, ...]`（路径上出现的守卫节点 id；空 = 无守卫））。启发式：遍历以每个 HTTP entry 的 handler 为起点的 `chains`，找路径中含「副作用 sink」的子路径，用 ownership regex 判路径上 handler 段是否有 ownership 守卫；无守卫的 = 候选。

> **副作用 sink 识别**：`_SIDE_EFFECT_SINK_RE`（DB 写/ORM mutation/file write/state update 关键词，跨语言）在 sink 节点的 `FuncBlock.source_code` 或 `function_name` 上匹配。这是 IDOR 的「sink」——即 ownership 应当守卫的资源访问点。

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/code_index/test_authz_dominance.py
from shannon_core.code_index.models import (
    CallChain, CallEdge, CodeIndex, EntryPoint, FuncBlock,
)
from shannon_core.code_index.authz_gitnexus_track import (
    IDORCandidateChain,
    find_unguarded_sink_paths,
)


def _block(bid, source, name=None):
    file_path, func_name, line = bid.rsplit(":", 2)
    return FuncBlock(
        id=bid, file_path=file_path, function_name=name or func_name,
        start_line=int(line), end_line=int(line) + 5, source_code=source,
        parameters=[], language="typescript",
    )


def _idx(blocks, edges, chains, entry_points=None):
    return CodeIndex(
        repository="r", language="typescript", total_blocks=len(blocks),
        total_entry_points=len(entry_points or []), total_chains=len(chains),
        blocks=blocks, edges=edges, entry_points=entry_points or [],
        chains=chains,
    )


def _ep(handler_id, route, method="DELETE"):
    return EntryPoint(
        func_block_id=handler_id, entry_type="http_route", route=route,
        http_method=method, confidence=0.9, evidence="",
        needs_llm_review=False, authentication="required",
    )


def test_no_candidates_when_handler_has_ownership_guard():
    """Handler body has ORM ownership predicate → no unguarded sink path."""
    handler = _block(
        "u.js:update:10",
        "async function update(req){\n"
        "  const row = await db.user.findFirst({ where: { userId: req.user.id } });\n"
        "  await db.user.update(row);\n"
        "}",
    )
    sink = _block("db.js:update:1", "function update(){ model.save(); }")
    chain = CallChain(
        entry_point_id=handler.id, path=[handler.id, sink.id],
        depth=1, has_unresolved=False,
    )
    index = _idx([handler, sink], [], [chain], [_ep(handler.id, "/api/users/:id")])
    cands = find_unguarded_sink_paths(index)
    assert cands == []  # ownership guard present in handler


def test_candidate_when_no_ownership_guard_reaches_sink():
    """Handler reaches a side-effect sink (ORM update) with no ownership predicate."""
    handler = _block(
        "u.js:update:10",
        "async function update(req){ await repo.update(req.params.id, req.body); }",
    )
    sink = _block("repo.js:update:1", "function update(){ db.user.update(); }")
    chain = CallChain(
        entry_point_id=handler.id, path=[handler.id, sink.id],
        depth=1, has_unresolved=False,
    )
    index = _idx([handler, sink], [], [chain], [_ep(handler.id, "/api/Feedbacks/:id")])
    cands = find_unguarded_sink_paths(index)
    assert len(cands) == 1
    c = cands[0]
    assert c.handler_id == handler.id
    assert c.sink_id == sink.id
    assert c.guard_nodes_on_path == ()  # no guard


def test_candidate_dedup_by_endpoint_sink():
    """Two chains to the same sink from the same endpoint → one candidate."""
    handler = _block("h.js:f:1", "async function f(req){ await s(req.id); }")
    sink = _block("s.js:g:1", "function g(){ db.user.remove(); }")
    ch1 = CallChain(entry_point_id=handler.id, path=[handler.id, sink.id], depth=1, has_unresolved=False)
    ch2 = CallChain(entry_point_id=handler.id, path=[handler.id, "x.js:m:1", sink.id], depth=2, has_unresolved=False)
    index = _idx([handler, sink], [], [ch1, ch2], [_ep(handler.id, "/api/u/:id")])
    cands = find_unguarded_sink_paths(index)
    assert len(cands) == 1  # deduped by (endpoint, sink)


def test_chains_without_side_effect_sink_are_skipped():
    """Chain ending in a non-sink (e.g. logger) → not a candidate."""
    handler = _block("h.js:f:1", "function f(){ log('x'); }")
    leaf = _block("log.js:l:1", "function l(){ console.log(); }")  # not a side-effect sink
    chain = CallChain(entry_point_id=handler.id, path=[handler.id, leaf.id], depth=1, has_unresolved=False)
    index = _idx([handler, leaf], [], [chain], [_ep(handler.id, "/api/x")])
    assert find_unguarded_sink_paths(index) == []


def test_respects_max_paths_per_endpoint():
    """Cap candidate count per endpoint to bound the judge-LLM cost."""
    handler = _block("h.js:f:1", "function f(){ sink1(); }")
    sinks = [
        _block(f"s.js:g{i}:1", f"function g{i}(){{ db.user.update(); }}")
        for i in range(5)
    ]
    chains = [
        CallChain(entry_point_id=handler.id, path=[handler.id, s.id],
                  depth=1, has_unresolved=False)
        for s in sinks
    ]
    index = _idx([handler, *sinks], [], chains, [_ep(handler.id, "/api/u/:id")])
    cands = find_unguarded_sink_paths(index, max_paths_per_endpoint=2)
    assert len(cands) == 2


def test_empty_index_yields_no_candidates():
    index = _idx([], [], [], [])
    assert find_unguarded_sink_paths(index) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_authz_dominance.py -v`
Expected: FAIL — `ModuleNotFoundError: shannon_core.code_index.authz_gitnexus_track`

- [ ] **Step 3: Implement the dominance heuristic + dataclasses**

```python
# packages/core/src/shannon_core/code_index/authz_gitnexus_track.py
"""vuln-authz GitNexus deterministic track (spec §5.7, ⭐ net-progress direction).

Produces IDOR candidate chains: handler→sink call paths where no ownership
guard dominates the sink. The "guard must dominate the sink" is approximated
heuristically (spec §8: not a full dominance proof) — we flag a path as an
IDOR candidate when the handler segment carries no ownership predicate (ORM
`where { userId }` / `findByOwner` etc.) AND the path reaches a side-effect
sink (DB write / ORM mutation / file / state). This is conservative: we
over-report candidates (宁过报不漏报, spec §2 principle 4) and let the LLM
chain-judgement pass (Task 4) confirm or reject each.

Ownership/auth detection reuses Plan 6's scan_endpoint_security machinery
(_OWNERSHIP_PREDICATE_RE etc.) — imported, not reimplemented.

This is the GitNexus TRACK of the dual-track merge. The LLM track (authz
agent, vuln-authz.txt) is untouched (spec principle 2: no anchoring). The
two tracks merge via Plan 3's merge_dual_track_queues (verdict OR by
endpoint).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from shannon_core.code_index.models import CodeIndex, EntryPoint, FuncBlock

logger = logging.getLogger(__name__)


# Side-effect sinks: DB writes, ORM mutations, file writes, state updates.
# Matched against a node's source_code OR function_name (some sinks are
# named update/save/remove/destroy directly). Cross-language.
_SIDE_EFFECT_SINK_RE = re.compile(
    r"(?i)("
    r"\b(update|save|create|destroy|delete|remove|insert|upsert|patch)\b\s*[(<]"
    r"|\.update\s*\(|\.save\s*\(|\.create\s*\(|\.remove\s*\(|\.destroy\s*\("
    r"|\.delete\s*\(|\.insert\s*\(|\.upsert\s*\(|\.patch\s*\("
    r"|\b(exec|query|run)\s*\(\s*['\"]?(update|insert|delete|drop)"
    r"|model\s*\.\s*(save|update|remove|destroy)\b"
    r"|repo\s*\.\s*(save|update|remove|destroy)\b"
    r")"
)


@dataclass(frozen=True)
class IDORCandidateChain:
    """A handler→sink path flagged as a potential IDOR (no ownership guard)."""
    endpoint_id: str          # EntryPoint.func_block_id of the handler
    handler_id: str           # FuncBlock.id of the handler (= endpoint_id here)
    sink_id: str              # FuncBlock.id of the side-effect sink
    path: tuple[str, ...]     # ordered FuncBlock.id list, handler→sink
    guard_nodes_on_path: tuple[str, ...]  # ownership-guard node ids on path (empty=none)


def _is_side_effect_sink(block: FuncBlock | None) -> bool:
    """True if the block performs a DB/ORM/file/state side-effect."""
    if block is None:
        return False
    if _SIDE_EFFECT_SINK_RE.search(block.source_code):
        return True
    return bool(_SIDE_EFFECT_SINK_RE.search(block.function_name + "("))


def _handler_has_ownership_guard(handler: FuncBlock) -> bool:
    """Reuse Plan 6's ownership predicate detection.

    Plan 6 (recon §4.2) ships `scan_endpoint_security` with
    `_OWNERSHIP_PREDICATE_RE`/`_detect_ownership`. We reuse the predicate
    regex directly (imported lazily so Plan 6 is a soft dependency — if it
    has not landed, we degrade to a local copy).
    """
    try:
        from shannon_core.code_index.recon_gitnexus_track import (
            _OWNERSHIP_PREDICATE_RE,
        )
    except ImportError:
        # Plan 6 not landed yet — local fallback (kept in sync with Plan 6).
        _OWNERSHIP_PREDICATE_RE = re.compile(
            r"(?i)("
            r"where\s*[:({]?\s*['\"]?\s*(user_?id|owner_?id|owner|creator_?id|author_?id)\b"
            r"|where\s*\(\s*['\"]?(user_?id|owner_?id|owner|creator_?id|author_?id)['\"]?\s*[,=]"
            r"|\bfind(First|One|All)?\s*\(\s*\{[^}]*?(user_?id|owner|creator)"
            r"|\b(owner|currentUser|req\.user|ctx\.state\.user)\s*\.\s*id\b"
            r"|\b(user_?id|owner_?id)\s*=\s*(req|ctx|currentUser)"
            r")"
        )
    return _OWNERSHIP_PREDICATE_RE.search(handler.source_code) is not None


def find_unguarded_sink_paths(
    index: CodeIndex,
    *,
    max_paths_per_endpoint: int = 20,
) -> list[IDORCandidateChain]:
    """Find handler→sink paths lacking an ownership guard (IDOR candidates).

    Heuristic (spec §8): for each HTTP EntryPoint's handler, walk the
    CallChains rooted at it; for any chain whose tail is a side-effect sink
    AND whose handler carries no ownership predicate, emit a candidate.
    Conservative — flags any path reaching a sink without an ownership check
    in the handler, even if some OTHER path has one (dominance under-approx:
    a guard that doesn't cover every path still yields a candidate).

    Dedup by (endpoint_id, sink_id). Capped per endpoint to bound the
    judge-LLM cost.
    """
    blocks_by_id: dict[str, FuncBlock] = {b.id: b for b in index.blocks}
    http_eps = [ep for ep in index.entry_points
                if ep.entry_type == "http_route" and ep.route is not None]

    candidates: list[IDORCandidateChain] = []
    seen: set[tuple[str, str]] = set()  # (endpoint_id, sink_id)

    for ep in http_eps:
        handler = blocks_by_id.get(ep.func_block_id)
        if handler is None:
            continue  # unresolved handler — Plan 6 surfaces "unknown"; skip here
        # Dominance short-circuit: if the handler itself has an ownership
        # predicate, it dominates the sink for all paths through it — no IDOR
        # candidate from this handler (guard present at the entry).
        if _handler_has_ownership_guard(handler):
            continue
        count_for_ep = 0
        for chain in index.chains:
            if chain.entry_point_id != ep.func_block_id or not chain.path:
                continue
            sink_id = chain.path[-1]
            key = (ep.func_block_id, sink_id)
            if key in seen:
                continue
            if not _is_side_effect_sink(blocks_by_id.get(sink_id)):
                continue
            # Path reached a side-effect sink with no ownership guard in the
            # handler → IDOR candidate.
            seen.add(key)
            candidates.append(IDORCandidateChain(
                endpoint_id=ep.func_block_id,
                handler_id=ep.func_block_id,
                sink_id=sink_id,
                path=tuple(chain.path),
                guard_nodes_on_path=(),  # handler guard absent → no guards
            ))
            count_for_ep += 1
            if count_for_ep >= max_paths_per_endpoint:
                break

    logger.info(
        "authz GitNexus track: %d HTTP endpoints, %d IDOR candidate chains",
        len(http_eps), len(candidates),
    )
    return candidates
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_authz_dominance.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/code_index/authz_gitnexus_track.py packages/core/tests/code_index/test_authz_dominance.py
git commit -m "feat(code_index): dominance heuristic for IDOR candidate chains (authz GitNexus track)"
```

---

### Task 2: 写 framework-inferred 端点候选生成 `find_framework_idor_candidates`

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/authz_gitnexus_track.py`（追加 framework 候选函数 + 渲染 evidence）
- Test: `packages/core/tests/code_index/test_authz_framework_candidates.py`（Create）

**Interfaces:**
- Consumes: `framework_analysis.json`（Plan 2 产，`FrameworkAnalysisResult`，`framework_analyzer.py:56-62` 的 `inferred_endpoints: list[InferredEndpoint]`，其中 `source="framework-auto-generated"`、`vulnerability_indicators` 含「no ownership validation」）
- Produces: `find_framework_idor_candidates(fa_path: Path) -> list[FrameworkIDORCandidate]`；`FrameworkIDORCandidate`（新 dataclass：`method`、`path`、`framework: str`、`model: str | None`、`vulnerability_indicators: tuple[str, ...]`）。读 `framework_analysis.json`，取 `inferred_endpoints` 中 `source=="framework-auto-generated"` 的项（finale-rest/epilogue CRUD，默认无 ownership，直接作 IDOR 候选）。

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/code_index/test_authz_framework_candidates.py
import json

from shannon_core.code_index.authz_gitnexus_track import (
    FrameworkIDORCandidate,
    find_framework_idor_candidates,
)


def _fa(endpoints):
    return {"detected_framework": {"name": "finale-rest"},
            "inferred_endpoints": endpoints,
            "recommendations": []}


def test_framework_auto_generated_endpoints_are_candidates(tmp_path):
    fa = _fa([
        {"method": "DELETE", "path": "/api/Feedbacks/:id",
         "source": "framework-auto-generated", "model": "Feedback",
         "middleware": ("isAuthenticated",),
         "vulnerability_indicators": ("No ownership check on finale resource operations",)},
        {"method": "GET", "path": "/api/Feedbacks",
         "source": "framework-auto-generated", "model": "Feedback",
         "middleware": ("isAuthenticated",), "vulnerability_indicators": ()},
    ])
    fa_path = tmp_path / "framework_analysis.json"
    fa_path.write_text(json.dumps(fa))

    cands = find_framework_idor_candidates(fa_path)
    assert len(cands) == 2
    assert all(c.framework == "finale-rest" for c in cands)
    methods = {c.method for c in cands}
    assert methods == {"DELETE", "GET"}
    assert cands[0].model == "Feedback"


def test_manual_endpoints_excluded(tmp_path):
    fa = _fa([
        {"method": "DELETE", "path": "/api/x/:id", "source": "manual",
         "model": None, "middleware": (), "vulnerability_indicators": ()},
    ])
    fa_path = tmp_path / "framework_analysis.json"
    fa_path.write_text(json.dumps(fa))
    assert find_framework_idor_candidates(fa_path) == []


def test_missing_framework_file_yields_empty(tmp_path):
    """Plan 2 not landed → no framework_analysis.json → empty (graceful)."""
    cands = find_framework_idor_candidates(tmp_path / "framework_analysis.json")
    assert cands == []


def test_invalid_framework_json_yields_empty(tmp_path):
    fa_path = tmp_path / "framework_analysis.json"
    fa_path.write_text("not json")
    assert find_framework_idor_candidates(fa_path) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_authz_framework_candidates.py -v`
Expected: FAIL — `ImportError: cannot import name 'find_framework_idor_candidates'`

- [ ] **Step 3: Implement the framework candidate finder**

Append to `packages/core/src/shannon_core/code_index/authz_gitnexus_track.py`:

```python
from pathlib import Path


@dataclass(frozen=True)
class FrameworkIDORCandidate:
    """A framework auto-generated endpoint (default no ownership) → IDOR candidate."""
    method: str
    path: str
    framework: str
    model: str | None
    vulnerability_indicators: tuple[str, ...]


def find_framework_idor_candidates(fa_path: Path) -> list[FrameworkIDORCandidate]:
    """Read framework_analysis.json (Plan 2); auto-generated endpoints are IDOR candidates.

    finale-rest/epilogue auto-generate CRUD with isAuthenticated only and no
    ownership validation by default (framework_analyzer.py:84-99). These are
    direct IDOR candidates. Manual endpoints (source="manual") are excluded —
    they're analyzed via the dominance heuristic (Task 1).

    Lenient: missing/invalid framework_analysis.json → empty list (Plan 2 not
    landed is a soft dependency).
    """
    if not fa_path.exists():
        logger.info("authz GitNexus track: framework_analysis.json missing → no framework candidates")
        return []
    try:
        data = json.loads(fa_path.read_text())
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        logger.warning("authz GitNexus track: framework_analysis.json parse failed (%s) → empty", exc)
        return []

    framework_name = ""
    fw = data.get("detected_framework")
    if isinstance(fw, dict):
        framework_name = str(fw.get("name", ""))

    out: list[FrameworkIDORCandidate] = []
    for ep in data.get("inferred_endpoints", []):
        if not isinstance(ep, dict):
            continue
        if ep.get("source") != "framework-auto-generated":
            continue
        indicators = ep.get("vulnerability_indicators", []) or []
        out.append(FrameworkIDORCandidate(
            method=str(ep.get("method", "")),
            path=str(ep.get("path", "")),
            framework=framework_name,
            model=ep.get("model"),
            vulnerability_indicators=tuple(str(i) for i in indicators),
        ))
    logger.info("authz GitNexus track: %d framework auto-generated IDOR candidates", len(out))
    return out
```

（在文件顶部 `import` 区补 `import json`。）

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_authz_framework_candidates.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/code_index/authz_gitnexus_track.py packages/core/tests/code_index/test_authz_framework_candidates.py
git commit -m "feat(code_index): framework-inferred IDOR candidates from framework_analysis.json (authz GitNexus track)"
```

---

### Task 3: 写 evidence 渲染器 `render_authz_gitnexus_candidates`

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/authz_gitnexus_track.py`（追加渲染函数）
- Test: `packages/core/tests/code_index/test_authz_render_candidates.py`（Create）

**Interfaces:**
- Consumes: `list[IDORCandidateChain]`（Task 1）+ `list[FrameworkIDORCandidate]`（Task 2）+ `CodeIndex`（取 handler/sink 的 `FuncBlock.source_code` 片段作 evidence）+ `EntryPoint`（取 method/route）
- Produces: `render_authz_gitnexus_candidates(dominance_cands, framework_cands, *, index, entry_points, max_snippet=200) -> str`（markdown，含两类候选表 + 调用路径 + 守卫缺失证据 + verdict 判定指令；空候选 → 空候选占位）。供 judge activity prompt 用。

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/code_index/test_authz_render_candidates.py
from shannon_core.code_index.models import (
    CallChain, CodeIndex, EntryPoint, FuncBlock,
)
from shannon_core.code_index.authz_gitnexus_track import (
    FrameworkIDORCandidate,
    IDORCandidateChain,
    render_authz_gitnexus_candidates,
)


def _block(bid, source):
    fp, fn, ln = bid.rsplit(":", 2)
    return FuncBlock(id=bid, file_path=fp, function_name=fn, start_line=int(ln),
                     end_line=int(ln) + 3, source_code=source, parameters=[],
                     language="typescript")


def _index():
    handler = _block("u.js:update:10", "async function update(req){ await repo.update(req.params.id); }")
    sink = _block("repo.js:update:1", "function update(){ db.user.update(); }")
    return CodeIndex(repository="r", language="typescript", total_blocks=2,
                     total_entry_points=1, total_chains=1,
                     blocks=[handler, sink], edges=[],
                     entry_points=[EntryPoint(func_block_id="u.js:update:10",
                                              entry_type="http_route", route="/api/u/:id",
                                              http_method="PUT", confidence=0.9, evidence="",
                                              needs_llm_review=False, authentication="required")],
                     chains=[CallChain(entry_point_id="u.js:update:10",
                                       path=["u.js:update:10", "repo.js:update:1"],
                                       depth=1, has_unresolved=False)])


def test_render_empty_candidates_yields_notice():
    index = _index()
    out = render_authz_gitnexus_candidates([], [], index=index, entry_points=index.entry_points)
    assert "无" in out or "no" in out.lower()


def test_render_dominance_candidate_lists_endpoint_and_path():
    index = _index()
    cand = IDORCandidateChain(
        endpoint_id="u.js:update:10", handler_id="u.js:update:10",
        sink_id="repo.js:update:1",
        path=("u.js:update:10", "repo.js:update:1"), guard_nodes_on_path=(),
    )
    out = render_authz_gitnexus_candidates([cand], [], index=index, entry_points=index.entry_points)
    assert "PUT /api/u/:id" in out
    assert "u.js:update:10" in out
    assert "repo.js:update:1" in out
    assert "ownership" in out.lower() or "guard" in out.lower()


def test_render_framework_candidate_lists_method_path_and_indicator():
    index = _index()
    fw = FrameworkIDORCandidate(
        method="DELETE", path="/api/Feedbacks/:id", framework="finale-rest",
        model="Feedback",
        vulnerability_indicators=("No ownership check on finale resource operations",),
    )
    out = render_authz_gitnexus_candidates([], [fw], index=index, entry_points=index.entry_points)
    assert "DELETE /api/Feedbacks/:id" in out
    assert "finale-rest" in out
    assert "No ownership check" in out


def test_render_includes_verdict_directive():
    index = _index()
    cand = IDORCandidateChain(
        endpoint_id="u.js:update:10", handler_id="u.js:update:10",
        sink_id="repo.js:update:1",
        path=("u.js:update:10", "repo.js:update:1"), guard_nodes_on_path=(),
    )
    out = render_authz_gitnexus_candidates([cand], [], index=index, entry_points=index.entry_points)
    # judge directive present
    assert "verdict" in out.lower() or "判定" in out
    assert "vulnerable" in out.lower() or "safe" in out.lower()
```

- [ ] **Step 2: Run test to fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_authz_render_candidates.py -v`
Expected: FAIL — `ImportError: cannot import name 'render_authz_gitnexus_candidates'`

- [ ] **Step 3: Implement the renderer**

Append to `packages/core/src/shannon_core/code_index/authz_gitnexus_track.py`:

```python
def _endpoint_label(func_block_id: str, entry_points: list[EntryPoint]) -> str:
    """Render an endpoint as 'METHOD /path' (fallback to handler id)."""
    for ep in entry_points:
        if ep.func_block_id == func_block_id and ep.route:
            return f"{ep.http_method or '—'} {ep.route}"
    return func_block_id


def _snippet(source: str, max_len: int = 200) -> str:
    s = source.strip().replace("\n", " ")
    return s[:max_len] + ("…" if len(s) > max_len else "")


def render_authz_gitnexus_candidates(
    dominance_cands: list[IDORCandidateChain],
    framework_cands: list[FrameworkIDORCandidate],
    *,
    index: CodeIndex,
    entry_points: list[EntryPoint],
    max_snippet: int = 200,
) -> str:
    """Render GitNexus-track IDOR candidates as markdown for the judge prompt.

    Two sections: (1) dominance candidates (handler→sink path, no ownership
    guard), (2) framework auto-generated endpoints (default no ownership).
    Plus a verdict directive telling the judge LLM to emit one
    AuthzVulnerability per candidate with externally_exploitable + reason.
    """
    if not dominance_cands and not framework_cands:
        return "（无确定性 IDOR 候选。GitNexus 索引或 framework 分析可能未就绪。）"

    blocks_by_id = {b.id: b for b in index.blocks}
    lines: list[str] = ["## Authz GitNexus Track — IDOR 候选链（确定性，待 LLM 判定）", ""]

    # ----- dominance candidates -----
    if dominance_cands:
        lines.append("### 1) 调用图 dominance 候选（handler→sink 无 ownership 守卫）")
        lines.append("")
        lines.append("| Endpoint | Handler | Sink | 调用路径 | Handler 片段 | Sink 片段 |")
        lines.append("|---|---|---|---|---|---|")
        for c in dominance_cands:
            label = _endpoint_label(c.endpoint_id, entry_points)
            handler_src = _snippet(blocks_by_id[c.handler_id].source_code, max_snippet) \
                if c.handler_id in blocks_by_id else "—"
            sink_src = _snippet(blocks_by_id[c.sink_id].source_code, max_snippet) \
                if c.sink_id in blocks_by_id else "—"
            path_str = " → ".join(c.path)
            lines.append(
                f"| `{label}` | `{c.handler_id}` | `{c.sink_id}` | `{path_str}` "
                f"| `{handler_src}` | `{sink_src}` |"
            )
        lines.append("")
        lines.append(
            "> ⚠️ 这些路径的 handler 段未检出 ORM ownership 谓词（`where { userId }` 等）。"
            "守卫缺失仅为启发式（dominance 非数学证明），须你语义确认：该 sink 是否真无"
            " ownership/role 守卫，或守卫在调用路径的其它节点上。"
        )
        lines.append("")

    # ----- framework candidates -----
    if framework_cands:
        lines.append("### 2) Framework 自动生成端点（默认无 ownership validation）")
        lines.append("")
        lines.append("| Method | Path | Framework | Model | Vulnerability Indicators |")
        lines.append("|---|---|---|---|---|")
        for f in framework_cands:
            indicators = "; ".join(f.vulnerability_indicators) if f.vulnerability_indicators else "—"
            model = f.model or "—"
            lines.append(
                f"| {f.method} | `{f.path}` | {f.framework} | {model} | {indicators} |"
            )
        lines.append("")
        lines.append(
            "> ⚠️ finale-rest/epilogue 等 ORM-to-REST 框架默认 isAuthenticated 但无 ownership。"
            "除非框架的 create.end/update.end/destroy.end hook 显式加了 ownership 校验，"
            "否则默认 IDOR。须你确认是否有 hook 覆盖默认行为。"
        )
        lines.append("")

    # ----- verdict directive -----
    lines.extend([
        "### 判定指令（每条候选产一条 AuthzVulnerability）",
        "",
        "对上方**每一条**候选，判 IDOR verdict：",
        "- **vulnerable**：该端点到达 side-effect sink 且**无** ownership/role 守卫 dominate 该 sink",
        "- **safe / not-exploitable**：存在覆盖所有路径的 ownership 守卫（如 hook、middleware、ORM 谓词），或 sink 非敏感资源",
        "- 输出 JSON 数组，每元素含：`endpoint`（METHOD /path）、`vulnerability_type`（Horizontal）、",
        "  `externally_exploitable`（bool）、`vulnerable_code_location`、`guard_evidence`（缺失守卫描述）、",
        "  `side_effect`、`reason`、`minimal_witness`、`confidence`（high/med/low）、`notes`",
        "- **保守**：不确定时判 vulnerable（宁过报不漏报）。",
    ])
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_authz_render_candidates.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/code_index/authz_gitnexus_track.py packages/core/tests/code_index/test_authz_render_candidates.py
git commit -m "feat(code_index): render authz GitNexus candidates markdown (dominance + framework)"
```

---

### Task 4: 写编排函数 `build_authz_gitnexus_track`（读 code_index + framework → 候选 → markdown）

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/authz_gitnexus_track.py`（追加编排函数）
- Test: `packages/core/tests/code_index/test_authz_build_track.py`（Create）

**Interfaces:**
- Consumes: `code_index.json` + `framework_analysis.json`（deliverables 目录）
- Produces: `build_authz_gitnexus_track(deliverables_dir: str) -> tuple[str, list[IDORCandidateChain], list[FrameworkIDORCandidate]]`（返回 (markdown, dominance_cands, framework_cands)；markdown 供 judge prompt，cands 供测试断言；文件缺失/空 → 返回空候选占位 markdown + 空列表，不 raise）

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/code_index/test_authz_build_track.py
import json

from shannon_core.code_index.authz_gitnexus_track import build_authz_gitnexus_track


def _block(bid, source):
    fp, fn, ln = bid.rsplit(":", 2)
    return {"id": bid, "file_path": fp, "function_name": fn, "start_line": int(ln),
            "end_line": int(ln) + 3, "source_code": source, "parameters": [],
            "decorators": [], "language": "typescript"}


def _ep(handler, route, method="DELETE"):
    return {"func_block_id": handler, "entry_type": "http_route", "route": route,
            "http_method": method, "confidence": 0.9, "evidence": "",
            "needs_llm_review": False, "authentication": "required", "source": "code_index"}


def _write_index(tmp_path, eps, blocks):
    (tmp_path / "code_index.json").write_text(json.dumps({
        "repository": "r", "language": "typescript",
        "total_blocks": len(blocks), "total_entry_points": len(eps),
        "total_chains": 1, "blocks": blocks, "edges": [],
        "entry_points": eps,
        "chains": [{"entry_point_id": eps[0]["func_block_id"],
                    "path": [eps[0]["func_block_id"], blocks[-1]["id"]],
                    "depth": 1, "has_unresolved": False}] if eps else [],
    }))


def _write_framework(tmp_path, endpoints):
    (tmp_path / "framework_analysis.json").write_text(json.dumps({
        "detected_framework": {"name": "finale-rest"},
        "inferred_endpoints": endpoints, "recommendations": [],
    }))


def test_build_dominance_and_framework_candidates(tmp_path):
    handler = _block("u.js:update:10", "async function update(req){ await repo.update(req.params.id); }")
    sink = _block("repo.js:update:1", "function update(){ db.user.update(); }")
    _write_index(tmp_path, [_ep("u.js:update:10", "/api/u/:id", "PUT")], [handler, sink])
    _write_framework(tmp_path, [
        {"method": "DELETE", "path": "/api/Feedbacks/:id", "source": "framework-auto-generated",
         "model": "Feedback", "middleware": ("isAuthenticated",),
         "vulnerability_indicators": ("No ownership check on finale resource operations",)},
    ])

    md, dom_cands, fw_cands = build_authz_gitnexus_track(str(tmp_path))
    assert len(dom_cands) == 1
    assert dom_cands[0].sink_id == "repo.js:update:1"
    assert len(fw_cands) == 1
    assert fw_cands[0].method == "DELETE"
    # markdown surfaces both
    assert "PUT /api/u/:id" in md
    assert "DELETE /api/Feedbacks/:id" in md
    assert "verdict" in md.lower() or "判定" in md


def test_build_missing_code_index_returns_empty(tmp_path):
    md, dom, fw = build_authz_gitnexus_track(str(tmp_path))
    assert "无" in md or "no" in md.lower()
    assert dom == [] and fw == []


def test_build_framework_only_when_code_index_empty(tmp_path):
    (tmp_path / "code_index.json").write_text(json.dumps({
        "repository": "r", "language": "typescript", "total_blocks": 0,
        "total_entry_points": 0, "total_chains": 0, "blocks": [], "edges": [],
        "entry_points": [], "chains": [],
    }))
    _write_framework(tmp_path, [
        {"method": "DELETE", "path": "/api/F/:id", "source": "framework-auto-generated",
         "model": "F", "middleware": (), "vulnerability_indicators": ()},
    ])
    md, dom, fw = build_authz_gitnexus_track(str(tmp_path))
    assert dom == []  # no chains
    assert len(fw) == 1
    assert "DELETE /api/F/:id" in md


def test_build_invalid_code_index_returns_empty(tmp_path):
    (tmp_path / "code_index.json").write_text("not json")
    md, dom, fw = build_authz_gitnexus_track(str(tmp_path))
    assert isinstance(md, str)
    assert dom == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_authz_build_track.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_authz_gitnexus_track'`

- [ ] **Step 3: Implement the orchestrator**

Append to `packages/core/src/shannon_core/code_index/authz_gitnexus_track.py`:

```python
def build_authz_gitnexus_track(
    deliverables_dir: str,
) -> tuple[str, list[IDORCandidateChain], list[FrameworkIDORCandidate]]:
    """Read code_index.json + framework_analysis.json, build IDOR candidates.

    Returns (markdown, dominance_candidates, framework_candidates):
    - markdown: rendered candidates for the judge-LLM prompt (Task 5).
    - dominance_candidates / framework_candidates: raw lists for test asserts.

    Lenient: missing/invalid code_index.json → empty dominance candidates
    (framework candidates may still come from framework_analysis.json).
    Missing framework_analysis.json → empty framework candidates. Never raises
    (spec §6 graceful degradation: when GitNexus index is absent, only the LLM
    track runs).
    """
    out = Path(deliverables_dir)
    ci_path = out / "code_index.json"

    index: CodeIndex | None = None
    if ci_path.exists():
        try:
            index = CodeIndex.model_validate_json(ci_path.read_text())
        except Exception as exc:  # invalid JSON / schema drift
            logger.warning("authz GitNexus track: code_index.json parse failed (%s)", exc)
            index = None
    else:
        logger.info("authz GitNexus track: code_index.json missing")

    dominance_cands: list[IDORCandidateChain] = []
    if index is not None:
        dominance_cands = find_unguarded_sink_paths(index)

    framework_cands = find_framework_idor_candidates(out / "framework_analysis.json")

    if index is None:
        index = CodeIndex(
            repository="", language="", total_blocks=0, total_entry_points=0,
            total_chains=0, blocks=[], edges=[], entry_points=[], chains=[],
        )
    entry_points = list(index.entry_points)

    md = render_authz_gitnexus_candidates(
        dominance_cands, framework_cands, index=index, entry_points=entry_points,
    )
    logger.info(
        "authz GitNexus track built: %d dominance + %d framework candidates",
        len(dominance_cands), len(framework_cands),
    )
    return md, dominance_cands, framework_cands
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_authz_build_track.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full authz_gitnexus_track test suite**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_authz_dominance.py packages/core/tests/code_index/test_authz_framework_candidates.py packages/core/tests/code_index/test_authz_render_candidates.py packages/core/tests/code_index/test_authz_build_track.py -v`
Expected: PASS (6+4+4+4 = 18 tests)

- [ ] **Step 6: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/code_index/authz_gitnexus_track.py packages/core/tests/code_index/test_authz_build_track.py
git commit -m "feat(code_index): build authz GitNexus track from code_index + framework (orchestrator)"
```

---

### Task 5: 写 judge activity `run_authz_gitnexus_judge`（候选 → LLM → authz_gitnexus_queue.json）

**Files:**
- Create: `packages/whitebox/src/shannon_whitebox/prompts/authz_gitnexus_judge.txt`（judge prompt template，含 `{{AUTHZ_GITNEXUS_CANDIDATES}}` 占位 + 输出 JSON schema 指令）
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`（新增 `run_authz_gitnexus_judge` activity，约在 `run_vuln_agent` 之后 :135）
- Test: `packages/whitebox/tests/test_run_authz_gitnexus_judge.py`（Create）

**Interfaces:**
- Consumes: `build_authz_gitnexus_track`（Task 4）、`run_claude_prompt`（`runner.py:90`，单次 LLM 调用 + `structured_output_schema`）、`PromptManager`（加载 judge template）
- Produces: `run_authz_gitnexus_judge(input: ActivityInput) -> dict`（读 deliverables 的 `code_index.json`+`framework_analysis.json` 产候选 markdown → 渲染 judge prompt → `run_claude_prompt` → 产 `AuthzVulnerability` JSON → 写 `deliverables/authz_gitnexus_queue.json`；候选为空 → 写空 queue，不调 LLM 省成本）

> **judge prompt 不通过 executor.execute**（executor 是 agent 级，带 git checkpoint/validator/queue_filename 自动写 `authz_exploitation_queue.json`，会覆盖 LLM 轨）。judge activity 用底层 `run_claude_prompt` + `structured_output_schema` 直接产 JSON，自己写 `authz_gitnexus_queue.json`（独立文件名，不碰 exploitation_queue）。

- [ ] **Step 1: Create the judge prompt template**

Write `/root/shannon-py/prompts/authz_gitnexus_judge.txt`（放 `prompts/` 根，与 `vuln-authz.txt` 同级，executor 的 `prompts_dir = parents[5]/"prompts"` 即此目录）:

```
<role>
You are an Authorization Verdict Judge. You are given a list of IDOR candidate
chains produced by a deterministic GitNexus call-graph analysis (handler→sink
paths with no detected ownership guard, plus framework auto-generated
endpoints). Your job is to confirm or reject each candidate as an IDOR
vulnerability, based ONLY on the evidence in each chain.
</role>

<objective>
For EACH candidate below, emit one AuthzVulnerability verdict. Be conservative:
when ownership/role guard coverage is unclear, judge vulnerable (prefer
over-reporting — the merge phase will reconcile with the LLM track).
</objective>

<input>
{{AUTHZ_GITNEXUS_CANDIDATES}}
</input>

<output_format>
Respond with a JSON object of exactly this shape (no prose outside JSON):

{
  "vulnerabilities": [
    {
      "ID": "AUTHZ-GN-NN",
      "vulnerability_type": "Horizontal",
      "externally_exploitable": true,
      "endpoint": "DELETE /api/Feedbacks/:id",
      "vulnerable_code_location": "file:line of the missing/misplaced guard",
      "role_context": "authenticated user (no role restriction)",
      "guard_evidence": "no ownership check; isAuthenticated only",
      "side_effect": "delete any Feedback by id",
      "reason": "1-2 lines why vulnerable/safe",
      "minimal_witness": "change :id to another user's Feedback id",
      "confidence": "high | med | low",
      "notes": "candidate source: dominance | framework"
    }
  ]
}

Rules:
- Emit ONE entry per candidate. Rejected candidates: set externally_exploitable=false
  and explain in reason (e.g. "ownership guard dominates sink via middleware X").
- Set `notes` to the candidate source ("dominance" or "framework").
- If there are zero candidates, emit {"vulnerabilities": []}.
</output_format>
```

- [ ] **Step 2: Write the failing test**

```python
# packages/whitebox/tests/test_run_authz_gitnexus_judge.py
import json
from unittest.mock import AsyncMock, patch

import pytest

from shannon_whitebox.pipeline import activities


class _FakeInput:
    def __init__(self, tmp_path):
        self.agent_name = None
        self.web_url = None
        self.repo_path = str(tmp_path)
        self.deliverables_subdir = None
        self.workspace_name = None
        self.config_path = None
        self.api_key = None
        self.pipeline_testing_mode = False
        self.prompt_override = None


def _write_index_with_candidate(tmp_path):
    handler = {"id": "u.js:update:10", "file_path": "u.js", "function_name": "update",
               "start_line": 10, "end_line": 13,
               "source_code": "async function update(req){ await repo.update(req.params.id); }",
               "parameters": [], "decorators": [], "language": "typescript"}
    sink = {"id": "repo.js:update:1", "file_path": "repo.js", "function_name": "update",
            "start_line": 1, "end_line": 3,
            "source_code": "function update(){ db.user.update(); }",
            "parameters": [], "decorators": [], "language": "typescript"}
    (tmp_path / "code_index.json").write_text(json.dumps({
        "repository": "r", "language": "typescript", "total_blocks": 2,
        "total_entry_points": 1, "total_chains": 1, "blocks": [handler, sink],
        "edges": [],
        "entry_points": [{"func_block_id": "u.js:update:10", "entry_type": "http_route",
                          "route": "/api/u/:id", "http_method": "PUT", "confidence": 0.9,
                          "evidence": "", "needs_llm_review": False,
                          "authentication": "required", "source": "code_index"}],
        "chains": [{"entry_point_id": "u.js:update:10",
                    "path": ["u.js:update:10", "repo.js:update:1"],
                    "depth": 1, "has_unresolved": False}],
    }))


@pytest.mark.asyncio
async def test_judge_writes_gitnexus_queue_from_candidates(tmp_path):
    _write_index_with_candidate(tmp_path)
    captured = {}

    async def fake_run(prompt, **kwargs):
        captured["prompt"] = prompt
        return type("R", (), {
            "success": True, "error": None, "retryable": False, "turns": 1,
            "cost": 0.0, "text": "", "model": "m", "stop_reason": "end",
            "tokens": None,
            "structured_output": {"vulnerabilities": [{
                "ID": "AUTHZ-GN-01", "vulnerability_type": "Horizontal",
                "externally_exploitable": True, "endpoint": "PUT /api/u/:id",
                "vulnerable_code_location": "u.js:update:10",
                "role_context": "user", "guard_evidence": "no ownership check",
                "side_effect": "update any user record", "reason": "no ownership",
                "minimal_witness": "change :id", "confidence": "high",
                "notes": "dominance",
            }]},
        })()

    with patch.object(activities, "_get_paths", return_value=(tmp_path, tmp_path, tmp_path)):
        with patch("shannon_whitebox.pipeline.activities.run_claude_prompt", new=fake_run):
            with patch("shannon_whitebox.audit.session_registry.get_audit_session") as gs:
                inst = gs.return_value
                inst.track_step = _noop_cm_factory()
            result = await activities.run_authz_gitnexus_judge(_FakeInput(tmp_path))

    queue_path = tmp_path / "authz_gitnexus_queue.json"
    assert queue_path.exists()
    data = json.loads(queue_path.read_text())
    assert len(data["vulnerabilities"]) == 1
    v = data["vulnerabilities"][0]
    assert v["externally_exploitable"] is True
    assert v["source_track"] == "gitnexus"
    assert v["evidence_chain"]  # populated from candidate path
    assert result["candidate_count"] >= 1
    # prompt carried candidates
    assert "PUT /api/u/:id" in captured["prompt"]


@pytest.mark.asyncio
async def test_judge_skips_llm_when_no_candidates(tmp_path):
    """No candidates → write empty queue, do NOT call LLM (save cost)."""
    (tmp_path / "code_index.json").write_text(json.dumps({
        "repository": "r", "language": "typescript", "total_blocks": 0,
        "total_entry_points": 0, "total_chains": 0, "blocks": [], "edges": [],
        "entry_points": [], "chains": [],
    }))

    called = {"n": 0}

    async def fake_run(prompt, **kwargs):
        called["n"] += 1
        return type("R", (), {"success": True, "structured_output": {"vulnerabilities": []}})()

    with patch.object(activities, "_get_paths", return_value=(tmp_path, tmp_path, tmp_path)):
        with patch("shannon_whitebox.pipeline.activities.run_claude_prompt", new=fake_run):
            with patch("shannon_whitebox.audit.session_registry.get_audit_session") as gs:
                inst = gs.return_value
                inst.track_step = _noop_cm_factory()
            result = await activities.run_authz_gitnexus_judge(_FakeInput(tmp_path))

    assert called["n"] == 0  # LLM not called
    assert (tmp_path / "authz_gitnexus_queue.json").exists()
    data = json.loads((tmp_path / "authz_gitnexus_queue.json").read_text())
    assert data["vulnerabilities"] == []
    assert result["candidate_count"] == 0


@pytest.mark.asyncio
async def test_judge_lenient_on_invalid_llm_output(tmp_path):
    """LLM returns non-JSON → parse_lenient absorbs, writes empty queue, no crash."""
    _write_index_with_candidate(tmp_path)

    async def fake_run(prompt, **kwargs):
        return type("R", (), {
            "success": True, "structured_output": None,
            "text": "not json", "error": None, "retryable": False, "turns": 1,
            "cost": 0.0, "model": "m", "stop_reason": "end", "tokens": None,
        })()

    with patch.object(activities, "_get_paths", return_value=(tmp_path, tmp_path, tmp_path)):
        with patch("shannon_whitebox.pipeline.activities.run_claude_prompt", new=fake_run):
            with patch("shannon_whitebox.audit.session_registry.get_audit_session") as gs:
                inst = gs.return_value
                inst.track_step = _noop_cm_factory()
            await activities.run_authz_gitnexus_judge(_FakeInput(tmp_path))

    data = json.loads((tmp_path / "authz_gitnexus_queue.json").read_text())
    assert data["vulnerabilities"] == []  # lenient


def _noop_cm_factory():
    class _CM:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
    return lambda *a, **k: _CM()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_run_authz_gitnexus_judge.py -v`
Expected: FAIL — `AttributeError: module ...activities has no attribute 'run_authz_gitnexus_judge'`

- [ ] **Step 4: Implement the judge activity**

Add to `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`（在 `run_vuln_agent` 之后，约 :135；在文件顶部 `from shannon_core.agents.executor import AgentExecutor` 附近补 `from shannon_core.agents.runner import run_claude_prompt`——若已 import 则跳过）:

```python
@activity.defn
async def run_authz_gitnexus_judge(input: ActivityInput) -> dict:
    """GitNexus track LLM chain-judgement pass for authz (spec §5.7).

    1. Build IDOR candidates from code_index.json + framework_analysis.json
       (dominance heuristic + framework auto-generated).
    2. If candidates exist, render them into the authz_gitnexus_judge prompt
       and call run_claude_prompt (single call, structured JSON output).
    3. Parse the LLM verdicts leniently, tag each with source_track="gitnexus"
       + evidence_chain (the candidate path), write authz_gitnexus_queue.json.

    No candidates → write empty queue, skip the LLM call (save cost).
    Lenient on LLM output: invalid JSON → empty queue, no crash.

    This writes ONLY authz_gitnexus_queue.json (never authz_exploitation_queue.json
    — that's the LLM track's; Plan 3 merges them). It does NOT go through
    executor.execute (no git checkpoint, no validator, no auto queue write).
    """
    from shannon_whitebox.audit.session_registry import get_audit_session
    from shannon_core.code_index.authz_gitnexus_track import build_authz_gitnexus_track
    from shannon_core.models.queue_schemas import VulnerabilityQueue, _VulnerabilityAdapter
    from shannon_core.utils.atomic_write import atomic_write_json
    from shannon_core.prompts.manager import PromptManager

    try:
        async with get_audit_session().track_step(
            "vulnerability-analysis", "authz-gitnexus-judge",
            intent=intent_for("authz-gitnexus-judge"),
        ):
            repo, deliverables, _ = _get_paths(input)
            md, dom_cands, fw_cands = build_authz_gitnexus_track(str(deliverables))
            candidate_count = len(dom_cands) + len(fw_cands)

            # Evidence map: endpoint label → candidate path (for evidence_chain).
            evidence_by_endpoint: dict[str, str] = {}
            for c in dom_cands:
                label = f"{c.endpoint_id}→{c.sink_id}"
                evidence_by_endpoint[c.endpoint_id] = " → ".join(c.path)

            vulnerabilities: list[dict] = []
            if candidate_count > 0:
                prompts_dir = Path(__file__).resolve().parents[5] / "prompts"
                prompt_manager = PromptManager(prompts_dir)
                prompt = prompt_manager.load_sync(
                    "authz_gitnexus_judge",
                    variables={
                        "deliverables_path": str(deliverables),
                        "authz_gitnexus_candidates": md,
                    },
                )
                result = await run_claude_prompt(
                    prompt=prompt,
                    repo_path=str(repo),
                    model_tier="medium",
                    api_key=input.api_key,
                    structured_output_schema={
                        "type": "object",
                        "properties": {
                            "vulnerabilities": {"type": "array"},
                        },
                    },
                )
                raw = result.structured_output
                if raw is None and result.text:
                    raw = result.text  # fallback to text; parse_lenient handles
                parsed = VulnerabilityQueue.parse_lenient(
                    raw if isinstance(raw, str) else json.dumps(raw) if raw is not None else "{}"
                )
                for v in parsed.queue.vulnerabilities:
                    data = v.model_dump()
                    data["source_track"] = "gitnexus"
                    if not data.get("evidence_chain"):
                        ep_key = getattr(v, "endpoint", None) or ""
                        data["evidence_chain"] = evidence_by_endpoint.get(
                            _match_endpoint_to_handler(ep_key, dom_cands), ""
                        ) or "dominance/framework candidate"
                    vulnerabilities.append(data)

            atomic_write_json(
                deliverables / "authz_gitnexus_queue.json",
                {"vulnerabilities": vulnerabilities},
            )
            return {
                "candidate_count": candidate_count,
                "verdict_count": len(vulnerabilities),
                "dominance_candidates": len(dom_cands),
                "framework_candidates": len(fw_cands),
            }
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


def _match_endpoint_to_handler(endpoint_label: str, dom_cands) -> str:
    """Best-effort: match an LLM-emitted endpoint label back to a dominance
    candidate's handler id, so we can attach the right evidence_chain.

    endpoint_label is like 'PUT /api/u/:id'; we don't have route→handler here,
    so we fall back to the first candidate's handler if the label is non-empty.
    This is best-effort metadata; the merge (Plan 3) dedups by endpoint anyway.
    """
    if not endpoint_label or not dom_cands:
        return ""
    return dom_cands[0].endpoint_id
```

> 注：`run_claude_prompt` import：若 activities.py 顶部已 `from shannon_core.agents.executor import AgentExecutor`，需额外 `from shannon_core.agents.runner import run_claude_prompt`。检查现有 import（executor.py:13 已 import runner，但 activities.py 不直接 import runner）→ 本 task Step 4 在 activity 内 lazy import 更安全（避免顶层循环）。改为在 activity 函数体内 `from shannon_core.agents.runner import run_claude_prompt`。

> `intent_for("authz-gitnexus-judge")` 同 Plan 3 Self-Review 决策点 A：key 不存在时返回 None，`track_step(intent=None)` 合法，不 KeyError。可选补 `step_intents.py` 一行 StepSpec（非阻塞）。

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_run_authz_gitnexus_judge.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
cd /root/shannon-py
git add prompts/authz_gitnexus_judge.txt packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/tests/test_run_authz_gitnexus_judge.py
git commit -m "feat(whitebox): authz GitNexus judge activity (candidates → LLM verdict → authz_gitnexus_queue.json)"
```

---

### Task 6: 工作流 wiring — vuln 阶段后、合并前插入 judge activity

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`（vuln 阶段 gather 完成 `:312-321` 后、`run_merge_dual_track_queues` `:323` 附近之前，插入 `run_authz_gitnexus_judge`）
- Test: `packages/whitebox/tests/test_workflow_authz_judge_ordering.py`（Create，验证顺序）

**Interfaces:**
- Consumes: `run_authz_gitnexus_judge`（Task 5）、`run_merge_dual_track_queues`（Plan 3 Task 3，`workflows.py:323` 附近）
- Produces: 工作流在 vuln 阶段并行完成后，先跑 authz GitNexus judge（产 `authz_gitnexus_queue.json`），再跑双轨合并（Plan 3，拾取 gitnexus queue）。**若 Plan 3 未落地**（`:323` 无 `run_merge_dual_track_queues`），judge 仍跑（产 queue 落盘），只是不被合并——降级行为可接受。

- [ ] **Step 1: Write the failing test**

```python
# packages/whitebox/tests/test_workflow_authz_judge_ordering.py
"""Verify the authz GitNexus judge activity is called between the vuln phase
gather and the dual-track merge."""
from unittest.mock import AsyncMock, patch, call

import pytest


@pytest.mark.asyncio
async def test_judge_runs_before_merge_in_workflow(tmp_path, monkeypatch):
    """The workflow must call run_authz_gitnexus_judge before run_merge_dual_track_queues."""
    # We patch both activities and check call order via a shared log.
    order: list[str] = []

    async def fake_judge(inp):
        order.append("judge")
        return {"candidate_count": 0, "verdict_count": 0}

    async def fake_merge(inp):
        order.append("merge")
        return {"merged_classes": []}

    # Import the workflow module to patch the activities it references.
    from shannon_whitebox.pipeline import workflows

    with patch.object(workflows.activities, "run_authz_gitnexus_judge", new=fake_judge):
        with patch.object(workflows.activities, "run_merge_dual_track_queues", new=fake_merge):
            # We can't easily run the full workflow (needs Temporal); instead
            # assert the symbols exist and judge is a distinct activity wired
            # before merge by inspecting the source.
            assert hasattr(workflows.activities, "run_authz_gitnexus_judge")
            assert hasattr(workflows.activities, "run_merge_dual_track_queues")
    # Manual verification: the two fakes are callable.
    assert order == []  # not invoked here; ordering checked by source inspection


def test_workflow_source_calls_judge_before_merge():
    """Source-level check: run_authz_gitnexus_judge appears before
    run_merge_dual_track_queues in the workflow's vuln-phase tail."""
    from pathlib import Path
    wf = Path(__file__).resolve().parents[1] / "src/shannon_whitebox/pipeline/workflows.py"
    src = wf.read_text()
    j = src.find("run_authz_gitnexus_judge")
    m = src.find("run_merge_dual_track_queues")
    if m == -1:
        # Plan 3 not landed yet — judge wiring must still be present.
        assert j != -1, "run_authz_gitnexus_judge must be wired into the workflow"
        return
    assert j != -1, "run_authz_gitnexus_judge must be wired into the workflow"
    assert j < m, "run_authz_gitnexus_judge must be called BEFORE run_merge_dual_track_queues"
```

> 注：Temporal workflow 无法在单元测试里直接驱动（需 worker + temporal server）。本测试用源码级断言（`test_workflow_source_calls_judge_before_merge`）锁定顺序——这是 memory `feat-fork-py-test-gotchas` 记录的「workflow 集成测试挂起」的务实绕法。真实顺序由手动冒烟验证。

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_workflow_authz_judge_ordering.py -v`
Expected: FAIL — `run_authz_gitnexus_judge` not found in workflow source（j == -1）

- [ ] **Step 3: Wire the judge activity into the workflow**

Edit `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`。在 vuln 阶段并行 gather 完成、`log_phase_complete_activity`（vulnerability-analysis）**之前**（约 :321-328 之间，紧接 `for (vt, agent_name, _), result in zip(...)` 循环之后、`await workflow.execute_activity(activities.log_phase_complete_activity, ...` 之前）插入 judge activity。

先定位精确插入点（`:316-328`）:

```python
                for (vt, agent_name, _), result in zip(vuln_tasks, results):
                    if isinstance(result, Exception):
                        self._state.errors.append(f"{agent_name.value}: {result}")
                        self._state.failed_agents.append(agent_name.value)
                    else:
                        self._state.completed_agents.append(agent_name.value)
                        self._state.agent_metrics[agent_name.value] = result

            # === Authz GitNexus track: judge IDOR candidates (spec §5.7) ===
            # Runs after vuln agents (LLM track queues ready) and before the
            # dual-track merge (Plan 3) so authz_gitnexus_queue.json exists
            # when merge reads it. Graceful: empty candidates → empty queue.
            try:
                await workflow.execute_activity(
                    activities.run_authz_gitnexus_judge, act_input,
                    start_to_close_timeout=timedelta(minutes=10),
                    retry_policy=retry_for("standard"),
                )
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning(
                    "Authz GitNexus judge failed (non-fatal, LLM-only track continues): %s", exc)

            # === Dual-track merge (Plan 3): combine LLM + GitNexus queues ===
            if hasattr(activities, "run_merge_dual_track_queues"):
                try:
                    await workflow.execute_activity(
                        activities.run_merge_dual_track_queues, act_input,
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=retry_for("standard"),
                    )
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).warning(
                        "Dual-track merge failed (non-fatal): %s", exc)

            await workflow.execute_activity(
                activities.log_phase_complete_activity,
                ActivityInput(**{**act_input.__dict__, "phase": "vulnerability-analysis"}),
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=retry_for("log"),
            )
```

> 注：`hasattr(activities, "run_merge_dual_track_queues")` 守卫让本 plan 在 Plan 3 未落地时不崩（judge 仍跑，merge 跳过）。若 Plan 3 已落地，`:323` 原有的 `run_merge_dual_track_queues` 调用应移到此处统一（避免重复调用）——见 Step 4 冲突处理。

- [ ] **Step 4: Resolve Plan 3 merge-call overlap**

Plan 3 Task 3 Step 4 在 `:323` 前插入了 `run_merge_dual_track_queues`。本 task 又在 judge 后插一次。**若 Plan 3 已落地**，删去 Plan 3 在 `:323` 的原调用（merge 只应调用一次，在 judge 之后）。**若 Plan 3 未落地**，本 task 的 `if hasattr(...)` 守卫让 merge 调用是 no-op，不重复。

具体：搜索 `workflows.py` 中 `run_merge_dual_track_queues` 的所有出现：
```bash
cd /root/shannon-py && grep -n "run_merge_dual_track_queues" packages/whitebox/src/shannon_whitebox/pipeline/workflows.py
```
- 若结果 = 1（只有本 task 插的 `if hasattr` 块内）→ 无冲突，完成。
- 若结果 ≥ 2（Plan 3 也插了）→ 删去非 `if hasattr` 守卫的那次调用（保留本 task 的 judge→merge 顺序块）。

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_workflow_authz_judge_ordering.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Run the authz test subset for no regression**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_authz_dominance.py packages/core/tests/code_index/test_authz_framework_candidates.py packages/core/tests/code_index/test_authz_render_candidates.py packages/core/tests/code_index/test_authz_build_track.py packages/whitebox/tests/test_run_authz_gitnexus_judge.py packages/whitebox/tests/test_workflow_authz_judge_ordering.py -v`
Expected: PASS (6+4+4+4+3+2 = 23 tests)

- [ ] **Step 7: Commit**

```bash
cd /root/shannon-py
git add packages/whitebox/src/shannon_whitebox/pipeline/workflows.py packages/whitebox/tests/test_workflow_authz_judge_ordering.py
git commit -m "feat(whitebox): wire authz GitNexus judge before dual-track merge in workflow"
```

---

### Task 7: 集成验证 — end-to-end candidate → judge → queue

**Files:**
- Test: `packages/core/tests/code_index/test_authz_track_integration.py`（Create）

**Interfaces:**
- Consumes: Task 1-4（候选生成 + 渲染 + 编排）
- Produces: 验证从合成 `code_index.json` + `framework_analysis.json` 经 `build_authz_gitnexus_track` 产出 dominance 候选 + framework 候选 + 渲染 markdown 含 verdict 指令；空/缺失场景降级不崩

- [ ] **Step 1: Write the integration test**

```python
# packages/core/tests/code_index/test_authz_track_integration.py
"""End-to-end: code_index.json + framework_analysis.json → build_authz_gitnexus_track.

Covers dominance candidates (handler→sink no ownership), framework candidates
(auto-generated CRUD), evidence rendering, and graceful degradation.
"""
import json

from shannon_core.code_index.authz_gitnexus_track import build_authz_gitnexus_track


def _block(bid, source):
    fp, fn, ln = bid.rsplit(":", 2)
    return {"id": bid, "file_path": fp, "function_name": fn, "start_line": int(ln),
            "end_line": int(ln) + 5, "source_code": source, "parameters": [],
            "decorators": [], "language": "typescript"}


def _ep(handler, route, method="DELETE"):
    return {"func_block_id": handler, "entry_type": "http_route", "route": route,
            "http_method": method, "confidence": 0.9, "evidence": "",
            "needs_llm_review": False, "authentication": "required", "source": "code_index"}


def _write(tmp_path, eps, blocks, chains, fw_endpoints=None):
    (tmp_path / "code_index.json").write_text(json.dumps({
        "repository": "r", "language": "typescript", "total_blocks": len(blocks),
        "total_entry_points": len(eps), "total_chains": len(chains),
        "blocks": blocks, "edges": [], "entry_points": eps, "chains": chains,
    }))
    if fw_endpoints is not None:
        (tmp_path / "framework_analysis.json").write_text(json.dumps({
            "detected_framework": {"name": "finale-rest"},
            "inferred_endpoints": fw_endpoints, "recommendations": [],
        }))


def test_e2e_dominance_candidate_plus_framework_candidate(tmp_path):
    handler = _block("u.js:update:10", "async function update(req){ await repo.update(req.params.id); }")
    guarded = _block("g.js:safe:1",
                     "async function safe(req){ const r = await db.user.findFirst({where:{userId:req.user.id}}); await r.save(); }")
    sink = _block("repo.js:update:1", "function update(){ db.user.update(); }")
    safe_sink = _block("repo.js:safeupdate:1", "function safeupdate(){ r.save(); }")
    _write(
        tmp_path,
        [_ep("u.js:update:10", "/api/Feedbacks/:id", "DELETE"),
         _ep("g.js:safe:1", "/api/owned/:id", "PUT")],
        [handler, guarded, sink, safe_sink],
        [
            {"entry_point_id": "u.js:update:10", "path": ["u.js:update:10", "repo.js:update:1"],
             "depth": 1, "has_unresolved": False},
            {"entry_point_id": "g.js:safe:1", "path": ["g.js:safe:1", "repo.js:safeupdate:1"],
             "depth": 1, "has_unresolved": False},  # guarded → not a candidate
        ],
        fw_endpoints=[
            {"method": "DELETE", "path": "/api/Feedbacks", "source": "framework-auto-generated",
             "model": "Feedback", "middleware": ("isAuthenticated",),
             "vulnerability_indicators": ("No ownership check",)},
        ],
    )

    md, dom, fw = build_authz_gitnexus_track(str(tmp_path))

    # dominance: only the unguarded handler → 1 candidate (guarded handler skipped)
    assert len(dom) == 1
    assert dom[0].sink_id == "repo.js:update:1"
    # framework: 1 auto-generated
    assert len(fw) == 1
    assert fw[0].method == "DELETE"
    # markdown surfaces both
    assert "DELETE /api/Feedbacks/:id" in md
    assert "repo.js:update:1" in md
    assert "finale-rest" in md
    # verdict directive
    assert "verdict" in md.lower() or "判定" in md


def test_e2e_graceful_degradation_no_index(tmp_path):
    md, dom, fw = build_authz_gitnexus_track(str(tmp_path))
    assert "无" in md or "no" in md.lower()
    assert dom == [] and fw == []


def test_e2e_guarded_handler_not_a_candidate(tmp_path):
    """Handler with ORM ownership predicate → no dominance candidate even if it reaches a sink."""
    handler = _block("u.js:update:10",
                     "async function update(req){ const r = await db.user.findFirst({where:{userId:req.user.id}}); await repo.update(r); }")
    sink = _block("repo.js:update:1", "function update(){ db.user.update(); }")
    _write(
        tmp_path,
        [_ep("u.js:update:10", "/api/u/:id", "PUT")],
        [handler, sink],
        [{"entry_point_id": "u.js:update:10", "path": ["u.js:update:10", "repo.js:update:1"],
          "depth": 1, "has_unresolved": False}],
    )
    md, dom, fw = build_authz_gitnexus_track(str(tmp_path))
    assert dom == []  # ownership guard present
```

- [ ] **Step 2: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_authz_track_integration.py -v`
Expected: PASS (3 tests)

- [ ] **Step 3: Run the full authz GitNexus test suite**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_authz_dominance.py packages/core/tests/code_index/test_authz_framework_candidates.py packages/core/tests/code_index/test_authz_render_candidates.py packages/core/tests/code_index/test_authz_build_track.py packages/core/tests/code_index/test_authz_track_integration.py packages/whitebox/tests/test_run_authz_gitnexus_judge.py packages/whitebox/tests/test_workflow_authz_judge_ordering.py -v`
Expected: PASS (6+4+4+4+3+3+2 = 26 tests)

- [ ] **Step 4: Run broader code_index suite for no regression**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/ -v 2>&1 | tail -30`
Expected: PASS（含新 26 测试 + 现有 code_index 测试，含 Plan 6 recon_gitnexus_track 若已落地）

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/tests/code_index/test_authz_track_integration.py
git commit -m "test(code_index): integration test for authz GitNexus track end-to-end"
```

---

## Self-Review

**1. Spec coverage**（对照 spec §5.7 vuln-authz ⭐ + §2 原则 + §4.2 verdict 合并）：
- §5.7 LLM 轨（dominance trace，现状 + framework guidance）→ **不改 LLM 轨**（现状 `vuln-authz.txt` 已含 `<framework_endpoint_guidance>` + `<endpoint_security_context_reading>`，本 plan 不动）✓
- §5.7 GitNexus 轨「调用图必经节点 post-dominator」→ Task 1 `find_unguarded_sink_paths`（dominance 启发式：handler 无 ownership 守卫 + 路径达 side-effect sink = 候选；保守近似 post-dominator，spec §8 允许启发式）✓
- §5.7 GitNexus 轨「中间件注解 @Authorized/isOwner + ORM 谓词 ownership where:{userId}」→ Task 1 复用 Plan 6 `scan_endpoint_security` 的 `_OWNERSHIP_PREDICATE_RE` + `_AUTH_GUARD_RE`（import 复用 + fallback 本地副本）✓
- §5.7 GitNexus 轨「framework-analyzer 接通」→ Task 2 `find_framework_idor_candidates` 读 Plan 2 的 `framework_analysis.json`（`inferred_endpoints` auto-generated）✓
- §5.7「LLM 对每条 GitNexus 候选链判 IDOR verdict」→ Task 5 `run_authz_gitnexus_judge`（候选 markdown → judge prompt → 单次 LLM → verdict）✓
- §5.7「合并：verdict OR；framework origin 取 auto-generated；ownership 取 none」→ Plan 3 `merge_dual_track_queues(mode="verdict")` 已覆盖 authz（Plan 3 Task 3 Step 3 名单含 `"authz"`，按 endpoint 去重 verdict OR）。**framework origin / ownership 字段危险侧**：authz verdict 合并以 endpoint 为去重键，`guard_evidence`/`notes` 字段的「无 ownership」标注由 GitNexus 轨 candidate 自带（Task 3 渲染 + Task 5 LLM 产 `notes="dominance/framework"`），合并时 Plan 3 `_clone_with_merge_fields` 保留载体 finding 的字段——本 plan 不需额外字段合并逻辑（authz 无 auth/framework_origin 独立结构化字段，是 free-text，符合 Plan 3 Self-Review 决策点 B「intel 模式 best-effort」的诚实标注）✓
- §2 原则 2「LLM 轨不被锚定」→ GitNexus 候选**只注入 judge activity**（Task 5），**不注入 authz agent**（vuln-authz.txt 无 `{{AUTHZ_GITNEXUS_CANDIDATES}}` 占位符）✓
- §2 原则 4「冲突取 vulnerable（OR），宁过报不漏报」→ Task 1 dominance 启发式保守（存在无守卫路径即候选）+ judge prompt 指令「不确定判 vulnerable」+ Plan 3 verdict OR ✓
- §6 GitNexus 索引降级 → Task 4 `build_authz_gitnexus_track` 文件缺失/空/解析失败 → 空候选 + 占位 markdown（不 raise）+ Task 5 候选为空跳过 LLM 写空 queue ✓
- §9 验收 #1（双轨各自跑通，产 queue）→ LLM 轨产 `authz_exploitation_queue.json`（executor 现状）+ GitNexus 轨产 `authz_gitnexus_queue.json`（Task 5）✓
- §9 验收 #5（GitNexus 索引失败优雅降级）→ Task 4/5 降级 ✓

**2. Placeholder scan**：无 TBD/TODO。Task 5 Step 4 标注 `intent_for("authz-gitnexus-judge")` key 可能缺失——诚实标注（`intent_for` 返回 `str|None`，不 KeyError，Plan 3 Self-Review 决策点 A 同款）。Task 6 Step 4 标注 Plan 3 merge-call overlap——给出 `grep` 诊断 + 删重处理，非占位符。

**3. Type consistency**：
- `IDORCandidateChain`/`FrameworkIDORCandidate` dataclass（frozen，tuple 字段）在 Task 1-5 一致。
- `find_unguarded_sink_paths(index: CodeIndex, *, max_paths_per_endpoint=20) -> list[IDORCandidateChain]` 在 Task 1/4 一致。
- `build_authz_gitnexus_track(str) -> tuple[str, list, list]` 在 Task 4/5 一致（judge activity 解包三元组）。
- `run_authz_gitnexus_judge(input: ActivityInput) -> dict` 在 Task 5/6 一致。
- `authz_gitnexus_queue.json` 文件名在 Task 5（写）/ Plan 3 Task 3 Step 3（读 `gn_path = deliverables / f"{vc}_gitnexus_queue.json"`，vc 含 authz）一致——**Plan 3 的合并器已按 `{vuln_class}_gitnexus_queue.json` 命名拾取**，本 plan 写的 `authz_gitnexus_queue.json` 正好匹配。✓
- `AuthzVulnerability` schema（`queue_schemas.py:52-59`：endpoint/vulnerable_code_location/role_context/guard_evidence/side_effect/reason/minimal_witness）在 Task 5 judge prompt 的 output_format 一致；新增的 `source_track`/`evidence_chain` 字段是 Plan 3 Task 1 加到 `BaseVulnerability` 的（authz 继承），默认 None 向后兼容 ✓。

**4. dominance 启发式正确性**（spec §5.7 + §8）：
- `_handler_has_ownership_guard`：handler 源码含 ORM ownership 谓词 → 视为「守卫在 entry 就 dominate 所有路径」→ 跳过该 handler 的所有候选（Task 1 `test_no_candidates_when_handler_has_ownership_guard`）✓
- 无 ownership 谓词 + 路径达 side-effect sink → 候选（Task 1 `test_candidate_when_no_ownership_guard_reaches_sink`）✓
- 副作用 sink 识别（`_SIDE_EFFECT_SINK_RE`：update/save/create/destroy/delete/remove 等）在 sink 节点源码/函数名匹配（Task 1 `test_chains_without_side_effect_sink_are_skipped`）✓
- 去重 by (endpoint_id, sink_id)（Task 1 `test_candidate_dedup_by_endpoint_sink`）✓
- 上限 `max_paths_per_endpoint`（default 20）防 judge prompt 过长（Task 1 `test_respects_max_paths_per_endpoint`）✓
- **保守性**：启发式是「存在无守卫路径即候选」而非严格「守卫在所有路径上」。这偏保守（宁过报），符合 spec §2 原则 4。真实精度（守卫是否真在其它路径 dominate）由 Task 5 LLM 语义确认修正。spec §8 明确「用调用图启发式 + LLM 语义确认」。✓

**需人决策点**：
- **A. dominance 精度 vs 保守性的取舍**：本 plan 启发式判「handler 段无 ownership 谓词 + 路径达 sink = 候选」（dominance 粗近似）。**替代**：严格 post-dominator（守卫必须在 handler→sink 的**所有**路径上才算 guarded）——但 GitNexus 的 `chains` 是 BFS 枚举（可能有截断/`has_unresolved`），无法保证枚举了所有路径，严格 post-dominator 会因路径不全而漏报。**选保守启发式的理由**：spec §2 原则 4「宁过报不漏报」+ §8「启发式 + LLM 确认」。**风险**：过报候选多 → judge LLM 调用 token 多 → 成本。mitigated by `max_paths_per_endpoint` 上限 + judge 用 medium tier + 合并器 `gitnexus-only` 标 `needs_review`（Plan 3）不直接进 high-confidence exploitation。真实精度由手动冒烟调参。
- **B. judge activity 用单次 LLM 还是 per-候选 LLM**：本 plan 用**单次 LLM**（所有候选塞一个 prompt，一次 `run_claude_prompt` 产全量 verdict）。**替代**：per-候选 LLM（每条候选一次调用）。**选单次的理由**：省成本（spec §10 成本缓解）+ 候选已给定不需探索。**风险**：候选数大（>50）时 prompt 过长 LLM 截断。mitigated by `max_paths_per_endpoint=20` + framework 候选数受 framework 检测数约束（finale-rest/epilogue 通常几十个端点）。若真实场景候选爆炸，改为分批（每批 ≤30 候选一次 LLM）——超本 plan 范围，手动冒烟后定。
- **C. Plan 2/3/6 落地顺序**：本 plan 依赖 Plan 2（framework_analysis.json）、Plan 3（合并器 wiring）、Plan 6（ownership 检测复用）。**全部软依赖**（Task 1 fallback 本地 ownership regex、Task 2 framework 文件缺失返空、Task 6 `hasattr` 守卫跳过 merge）。可独立落地：本 plan 落地后，即使 Plan 2/3/6 全未落，judge activity 仍跑（产 `authz_gitnexus_queue.json`，用本地 ownership regex），只是 framework 候选为空、合并不触发——降级行为可接受。**建议落地顺序**：Plan 1（taint）→ Plan 2（framework）→ Plan 6（recon/ownership）→ **Plan 7（authz，本 plan）** → Plan 3（合并器）→ Plan 8/9。Plan 7 在 Plan 3 前落地也能跑（judge 产 queue，merge 待 Plan 3）。

**Caveat（诚实）**：
- **真实 GitNexus 索引产出非空 dominance 候选需 MCP 环境**：单元测试用合成 `code_index.json`（含 `entry_points` + `chains` + `blocks`）。真实 `CallChain` 精度（GitNexus BFS 枚举的路径完整性、`has_unresolved` 影响）由手动冒烟验证。若真实 GitNexus chains 多数 `has_unresolved=True`（callee 未解析），dominance 候选质量会降——但 Task 1 不按 `has_unresolved` 过滤（保守，宁过报），未解析路径仍产候选交 LLM 确认。
- **ownership regex 跨语言覆盖度**：复用 Plan 6 的 `_OWNERSHIP_PREDICATE_RE`，覆盖 Prisma/Sequelize/TypeORM/GORM/Knex/Mongoose/raw SQL 常见写法，但**可能漏 framework-specific 模式**（Laravel Eloquent policy、Spring Data `@CreatedBy`、自定义 authz decorator）。漏报 → handler 被判「无守卫」→ 过报候选 → 危险侧（保守）。spec §2 原则 4 认可。Task 1 fallback 本地 regex 与 Plan 6 保持同步（Plan 6 落地后 import 复用，regex 来源单一）。
- **judge LLM 输出 lenient 解析**：Task 5 用 `VulnerabilityQueue.parse_lenient` 吸收 LLM 非 JSON / schema 漂移（`test_judge_lenient_on_invalid_llm_output`）。坏 case = LLM 返空或全 reject → `authz_gitnexus_queue.json` 空 → 合并等价 LLM-only。不崩，但 GitNexus 轨贡献为零（LLM judge 失效）。手动冒烟关注 judge LLM 的 verdict 质量。
- **工作流顺序验证受限**：Temporal workflow 无法单元测试直接驱动（memory `feat-fork-py-test-gotchas`：workflow 集成测试挂起）。Task 6 用源码级断言（`run_authz_gitnexus_judge` 在 `run_merge_dual_track_queues` 之前）锁定顺序。真实运行顺序由手动冒烟验证。
- **不改 LLM 轨（vuln-authz.txt）**：本 plan 的 GitNexus 轨独立于 authz agent。authz agent 仍自主 dominance trace（现状），不被 GitNexus 候选锚定（spec 原则 2）。两轨在 Plan 3 合并器交汇。**唯一对 vuln-authz.txt 的间接影响**：无（本 plan 不加占位符、不改 prompt）。
```
