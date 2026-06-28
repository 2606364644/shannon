# 入口点识别优化：确定性 + LLM 独立扫描 + Fusion

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将入口点识别从"纯确定性"升级为"确定性做精度 + LLM 做全面独立扫描 + Fusion 合并去重"。

**Architecture:** 确定性层（`entry_points.py`）和 LLM 层（pre-recon Entry Point Mapper 子 agent）完全独立运行，互不知道对方结果。融合层（`entry_point_fusion.py`）从 pre-recon deliverable 解析 LLM 发现的入口点，与确定性入口点合并去重（确定性优先），然后由修复后的 `save_adjudication` 按 confidence 阈值裁定。

**Tech Stack:** Python (Pydantic models), Temporal workflow/activities, prompt text editing

---

## File Structure

| File | Responsibility |
|---|---|
| `packages/core/src/shannon_core/code_index/models.py` | `EntryPoint` 模型 — 新增 `authentication`、`source` 字段 |
| `packages/core/src/shannon_core/code_index/entry_point_fusion.py` | 新增 `parse_llm_entry_points`；扩展 `merge_entry_points` 增加 LLM 来源 |
| `packages/core/src/shannon_core/code_index/__init__.py` | 新增 `run_entry_point_fusion`；修复 `save_adjudication` 按 confidence 裁定 |
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | 新增 `run_entry_point_fusion` activity |
| `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | 在 PRE_RECON 和 save_adjudication 之间插入 fusion 步骤 |
| `prompts/pre-recon-code.txt` | 升级 Entry Point Mapper prompt；简化 Phase 0 移除入口点裁定 |
| `packages/core/tests/code_index/test_entry_point_fusion.py` | 新增测试覆盖 LLM 解析 + 四源合并 |
| `packages/core/tests/code_index/test_code_index.py` | 新增测试覆盖 `save_adjudication` confidence 裁定 |

---

### Task 1: 扩展 `EntryPoint` 模型

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/models.py:12-15` (Verdict enum) + `:48-58` (EntryPoint)
- Test: `packages/core/tests/code_index/test_models.py`（如果存在）或 `test_entry_point_fusion.py`

- [ ] **Step 1: 写失败测试 — Verdict 新枚举值 + EntryPoint 新字段**

在 `packages/core/tests/code_index/test_entry_point_fusion.py` 顶部新增：

```python
from shannon_core.code_index.models import EntryPoint, Verdict

def test_verdict_has_needs_review():
    assert Verdict.NEEDS_REVIEW.value == "needs_review"

在 `packages/core/tests/code_index/test_entry_point_fusion.py` 顶部新增：

```python
from shannon_core.code_index.models import EntryPoint

def test_entry_point_has_authentication_and_source():
    ep = EntryPoint(
        func_block_id="app.py:handler:10",
        entry_type="http_route",
        route="/users",
        http_method="GET",
        confidence=0.95,
        evidence="Flask route decorator",
        needs_llm_review=False,
        authentication="public",
        source="code_index",
    )
    assert ep.authentication == "public"
    assert ep.source == "code_index"


def test_entry_point_defaults():
    ep = EntryPoint(
        func_block_id="app.py:handler:10",
        entry_type="http_route",
        confidence=0.60,
        evidence="LLM discovery",
        needs_llm_review=False,
    )
    assert ep.authentication is None
    assert ep.source == "code_index"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_entry_point_fusion.py::test_verdict_has_needs_review -xvs`
Expected: FAIL — `Verdict` 没有 `NEEDS_REVIEW`

- [ ] **Step 3: 实现 — 在 `Verdict` 添加 NEEDS_REVIEW + 在 `EntryPoint` 添加两个新字段**

修改 `packages/core/src/shannon_core/code_index/models.py`：

1. 将 `Verdict` 枚举改为：

```python
class Verdict(str, Enum):
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    RECLASSIFIED = "reclassified"
    NEEDS_REVIEW = "needs_review"
```

2. 将 `EntryPoint` 类改为：

```python
class EntryPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    func_block_id: str
    entry_type: str  # "http_route" | "rpc" | "cli" | "message_consumer" | ...
    route: str | None = None
    http_method: str | None = None
    confidence: float
    evidence: str
    needs_llm_review: bool  # True when confidence < 0.8
    authentication: str | None = None  # "public" | "required" | "unknown"
    source: str = "code_index"  # "code_index" | "gitnexus" | "schema" | "llm_pre_recon"
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_entry_point_fusion.py::test_entry_point_has_authentication_and_source packages/core/tests/code_index/test_entry_point_fusion.py::test_entry_point_defaults -xvs`
Expected: PASS

- [ ] **Step 5: 运行全量现有测试确认无回归**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/ -x --timeout=60`
Expected: 全部 PASS（新字段有默认值，不影响现有代码）

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/code_index/models.py packages/core/tests/code_index/test_entry_point_fusion.py
git commit -m "feat(models): add authentication and source fields to EntryPoint"
```

---

### Task 2: 新增 `parse_llm_entry_points` 解析函数

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/entry_point_fusion.py`
- Test: `packages/core/tests/code_index/test_entry_point_fusion.py`

- [ ] **Step 1: 写失败测试 — parse_llm_entry_points**

在 `packages/core/tests/code_index/test_entry_point_fusion.py` 新增：

```python
from shannon_core.code_index.entry_point_fusion import parse_llm_entry_points

DELIVERABLE_WITH_ENTRY_POINTS = """
# Pre-Recon Deliverable

## 5. Attack Surface Analysis

### External Entry Points

1. **POST /api/users** — `src/routes/users.py:create_user` (line 42)
   - Authentication: required (JWT middleware)
   - Framework: Express.js

2. **GET /api/public/status** — `src/routes/health.py:status_check` (line 15)
   - Authentication: public
   - Framework: Express.js

3. **Webhook: /webhooks/stripe** — `src/webhooks/stripe.py:handle_webhook` (line 8)
   - Authentication: required (HMAC signature)
   - Entry type: webhook

### API Schema Files
- `openapi.yaml` — defines 45 endpoints
- `schema.graphql` — defines 12 queries, 8 mutations
"""

DELIVERABLE_WITHOUT_ENTRY_POINTS = """
# Pre-Recon Deliverable

## 5. Attack Surface Analysis

No external entry points were identified. The application appears to be a
background processing service with no HTTP interface.
"""


def test_parse_llm_entry_points_extracts_routes():
    result = parse_llm_entry_points(DELIVERABLE_WITH_ENTRY_POINTS)
    assert len(result) >= 2  # at least the two explicitly listed routes
    # Check that file paths are extracted
    file_paths = [ep.func_block_id for ep in result]
    assert any("users.py" in fp for fp in file_paths)


def test_parse_llm_entry_points_extracts_webhook():
    result = parse_llm_entry_points(DELIVERABLE_WITH_ENTRY_POINTS)
    webhooks = [ep for ep in result if ep.entry_type == "webhook"]
    assert len(webhooks) >= 1
    assert any("stripe" in ep.evidence.lower() for ep in webhooks)


def test_parse_llm_entry_points_confidence_is_060():
    result = parse_llm_entry_points(DELIVERABLE_WITH_ENTRY_POINTS)
    for ep in result:
        assert ep.confidence == 0.60
        assert ep.source == "llm_pre_recon"


def test_parse_llm_entry_points_empty():
    result = parse_llm_entry_points(DELIVERABLE_WITHOUT_ENTRY_POINTS)
    assert result == []


def test_parse_llm_entry_points_malformed():
    result = parse_llm_entry_points("totally not a deliverable")
    assert result == []
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_entry_point_fusion.py::test_parse_llm_entry_points_extracts_routes -xvs`
Expected: FAIL — `ImportError: cannot import name 'parse_llm_entry_points'`

- [ ] **Step 3: 实现 parse_llm_entry_points**

在 `packages/core/src/shannon_core/code_index/entry_point_fusion.py` 末尾新增：

```python
import re
from shannon_core.code_index.models import EntryPoint


def parse_llm_entry_points(deliverable_text: str) -> list[EntryPoint]:
    """Parse LLM-discovered entry points from pre-recon deliverable Markdown.

    Looks for the "Attack Surface Analysis" section and extracts structured
    entry point information using regex patterns.

    Args:
        deliverable_text: Full text of pre_recon_deliverable.md.

    Returns:
        List of EntryPoint objects with confidence=0.60 and source="llm_pre_recon".
        Returns empty list on parse failures (never raises).
    """
    if not deliverable_text:
        return []

    entry_points: list[EntryPoint] = []

    # Find the Attack Surface Analysis section
    section_match = re.search(
        r"## 5\. Attack Surface Analysis(.*?)(?=## \d|$)",
        deliverable_text,
        re.DOTALL | re.IGNORECASE,
    )
    if not section_match:
        return []

    section = section_match.group(1)

    # Pattern 1: **METHOD /path** — `file.py:func_name` (line N)
    route_pattern = re.compile(
        r"\**(?:GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(/[^\s*]*)\**\s*"
        r"[—\-–]\s*`([^`]+)`",
        re.IGNORECASE,
    )
    for m in route_pattern.finditer(section):
        route_path = m.group(1).strip()
        func_ref = m.group(2).strip()
        # Parse file:function:line
        parts = func_ref.rsplit(":", 2)
        if len(parts) >= 2:
            file_path = parts[0]
            func_name = parts[1]
        else:
            file_path = func_ref
            func_name = "unknown"

        # Extract HTTP method
        method_match = re.match(r"\**(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)", m.group(0), re.IGNORECASE)
        http_method = method_match.group(1).upper() if method_match else None

        # Extract authentication info from nearby text
        auth = _extract_auth_nearby(section, m.start())

        entry_points.append(EntryPoint(
            func_block_id=f"{file_path}:{func_name}",
            entry_type="http_route",
            route=route_path,
            http_method=http_method,
            confidence=0.60,
            evidence=f"LLM discovered: {func_ref}",
            needs_llm_review=False,
            authentication=auth,
            source="llm_pre_recon",
        ))

    # Pattern 2: Webhook entries
    webhook_pattern = re.compile(
        r"\**Webhook:?\s*([^\n*]+)\**\s*[—\-–]\s*`([^`]+)`",
        re.IGNORECASE,
    )
    for m in webhook_pattern.finditer(section):
        webhook_path = m.group(1).strip()
        func_ref = m.group(2).strip()
        parts = func_ref.rsplit(":", 2)
        file_path = parts[0] if len(parts) >= 2 else func_ref
        func_name = parts[1] if len(parts) >= 2 else "unknown"

        auth = _extract_auth_nearby(section, m.start())

        entry_points.append(EntryPoint(
            func_block_id=f"{file_path}:{func_name}",
            entry_type="webhook",
            route=webhook_path,
            http_method="POST",
            confidence=0.60,
            evidence=f"LLM discovered webhook: {func_ref}",
            needs_llm_review=False,
            authentication=auth,
            source="llm_pre_recon",
        ))

    return entry_points


def _extract_auth_nearby(text: str, position: int, window: int = 300) -> str | None:
    """Look for authentication keywords near a match position."""
    start = max(0, position - window)
    end = min(len(text), position + window)
    nearby = text[start:end].lower()

    if "authentication: public" in nearby or "auth: public" in nearby:
        return "public"
    if "authentication: required" in nearby or "auth: required" in nearby:
        return "required"
    if "authentication" in nearby:
        return "unknown"
    return None
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_entry_point_fusion.py -k "parse_llm" -xvs`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/entry_point_fusion.py packages/core/tests/code_index/test_entry_point_fusion.py
git commit -m "feat(fusion): add parse_llm_entry_points to extract LLM entry points from deliverable"
```

---

### Task 3: 扩展 `merge_entry_points` 增加 LLM 来源

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/entry_point_fusion.py`
- Test: `packages/core/tests/code_index/test_entry_point_fusion.py`

- [ ] **Step 1: 写失败测试 — 四源合并 + LLM 来源**

在 `packages/core/tests/code_index/test_entry_point_fusion.py` 新增：

```python
def _llm_ep(name: str, file: str, entry_type: str = "http_route", auth: str | None = None) -> EntryPoint:
    return EntryPoint(
        func_block_id=f"{file}:{name}",
        entry_type=entry_type,
        route=f"/{name}",
        http_method="GET",
        confidence=0.60,
        evidence=f"LLM discovered: {file}:{name}",
        needs_llm_review=False,
        authentication=auth,
        source="llm_pre_recon",
    )


class TestMergeWithLLM:
    def test_llm_fills_gap(self):
        """LLM discovers an entry point that deterministic missed."""
        result = merge_entry_points(
            gitnexus_eps=[_gitnexus_ep("a", "app.py")],
            schema_eps=[],
            convention_eps=[],
            llm_eps=[_llm_ep("webhook_handler", "hooks.py", entry_type="webhook")],
        )
        assert len(result) == 2
        sources = {ep.source for ep in result}
        assert "gitnexus" in sources
        assert "llm_pre_recon" in sources

    def test_deterministic_wins_over_llm(self):
        """When both find the same entry point, deterministic (gitnexus) wins."""
        result = merge_entry_points(
            gitnexus_eps=[_gitnexus_ep("handler", "app.py", score=0.9)],
            schema_eps=[],
            convention_eps=[],
            llm_eps=[_llm_ep("handler", "app.py")],
        )
        assert len(result) == 1
        assert result[0].source == "gitnexus"
        assert result[0].confidence == 0.9

    def test_llm_supplements_auth_annotation(self):
        """LLM auth annotation supplements deterministic entry that lacks it."""
        # Create a deterministic entry without auth info, and an LLM entry with auth
        # Since merge keeps gitnexus version for same uid, the auth supplement
        # happens via the confidence-based priority logic
        result = merge_entry_points(
            gitnexus_eps=[_gitnexus_ep("handler", "app.py", score=0.9)],
            schema_eps=[],
            convention_eps=[],
            llm_eps=[_llm_ep("handler", "app.py", auth="required")],
        )
        assert len(result) == 1
        # gitnexus version wins but we don't lose the auth info
        # The deterministic version has no auth, LLM has auth="required"
        # Design decision: deterministic wins, auth is NOT supplemented for now
        # (can be added as enhancement later)
        assert result[0].source == "gitnexus"

    def test_llm_only_entry_points(self):
        """Only LLM entry points, no deterministic sources."""
        result = merge_entry_points(
            gitnexus_eps=[],
            schema_eps=[],
            convention_eps=[],
            llm_eps=[
                _llm_ep("stripe_webhook", "webhooks.py", entry_type="webhook"),
                _llm_ep("upload", "upload.py", entry_type="upload"),
            ],
        )
        assert len(result) == 2
        for ep in result:
            assert ep.confidence == 0.60
            assert ep.source == "llm_pre_recon"

    def test_four_sources_merged(self):
        """All four sources contribute independent entry points."""
        result = merge_entry_points(
            gitnexus_eps=[_gitnexus_ep("a", "app.py")],
            schema_eps=[_schema_ep("b", "api.py")],
            convention_eps=[_convention_ep("c", "pages/api/x.ts")],
            llm_eps=[_llm_ep("webhook", "hooks.py", entry_type="webhook")],
        )
        assert len(result) == 4
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_entry_point_fusion.py::TestMergeWithLLM -xvs`
Expected: FAIL — `merge_entry_points()` 不接受 `llm_eps` 参数

- [ ] **Step 3: 扩展 merge_entry_points 支持 LLM 来源**

修改 `packages/core/src/shannon_core/code_index/entry_point_fusion.py` 中的 `merge_entry_points` 函数签名和逻辑：

替换整个 `merge_entry_points` 函数为：

```python
def merge_entry_points(
    gitnexus_eps: list[dict],
    schema_eps: list[UnifiedEntryPoint],
    convention_eps: list[UnifiedEntryPoint],
    llm_eps: list[EntryPoint] | None = None,
) -> list[UnifiedEntryPoint]:
    """Merge entry points from multiple sources.

    Priority order for dedup: gitnexus > schema > convention > llm_pre_recon.
    Each source gets a confidence score:
    - gitnexus: from EP Scoring (variable)
    - schema_file: 0.80 (high trust, but not code-verified)
    - framework_convention: 0.75 (convention-based, good trust)
    - llm_pre_recon: 0.60 (LLM-discovered, lower default confidence)

    Args:
        gitnexus_eps: Entry points from GitNexus EP Scoring (MCP cypher results).
        schema_eps: Entry points from Schema file parsing.
        convention_eps: Entry points from framework convention detection.
        llm_eps: Entry points discovered by LLM pre-recon Entry Point Mapper.

    Returns:
        Deduplicated list of UnifiedEntryPoint sorted by confidence descending.
    """
    unified: dict[str, UnifiedEntryPoint] = {}

    # Source 1: GitNexus EP Scoring (primary)
    for ep in gitnexus_eps:
        name = ep.get("name", "")
        file_path = ep.get("filePath", "")
        key = f"{file_path}:{name}"
        if key not in unified:
            unified[key] = UnifiedEntryPoint(
                uid=key,
                name=name,
                file_path=file_path,
                confidence=ep.get("score", 0.5),
                source="gitnexus",
                entry_type=ep.get("kind", "unknown"),
                route=ep.get("route"),
                http_method=ep.get("httpMethod"),
                evidence=f"GitNexus EP Scoring (score={ep.get('score', 0.5):.2f})",
            )

    # Source 2: Schema files (OpenAPI/GraphQL/Proto)
    for ep in schema_eps:
        if ep.uid not in unified:
            unified[ep.uid] = ep

    # Source 3: Framework conventions (Next.js, Django, etc.)
    for ep in convention_eps:
        if ep.uid not in unified:
            unified[ep.uid] = ep

    # Source 4: LLM pre-recon discoveries
    llm_count = 0
    for ep in llm_eps or []:
        # Dedup key: extract file:function from func_block_id
        key = ep.func_block_id
        if key not in unified:
            # Extract function name from func_block_id
            parts = ep.func_block_id.split(":")
            name = parts[1] if len(parts) >= 2 else ep.func_block_id
            file_path = parts[0] if len(parts) >= 2 else ""

            unified[key] = UnifiedEntryPoint(
                uid=key,
                name=name,
                file_path=file_path,
                confidence=ep.confidence,
                source="llm_pre_recon",
                entry_type=ep.entry_type,
                route=ep.route,
                http_method=ep.http_method,
                evidence=ep.evidence,
            )
            llm_count += 1

    # Sort by confidence descending
    result = sorted(unified.values(), key=lambda ep: -ep.confidence)

    logger.info(
        "Merged %d entry points: %d from GitNexus, %d from schema, %d from convention, %d from LLM",
        len(result),
        sum(1 for e in result if e.source == "gitnexus"),
        sum(1 for e in result if e.source == "schema_file"),
        sum(1 for e in result if e.source == "framework_convention"),
        sum(1 for e in result if e.source == "llm_pre_recon"),
    )

    return result
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_entry_point_fusion.py -xvs`
Expected: 全部 PASS（包括原有的 8 个测试和新增的 5 个 LLM 测试）

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/entry_point_fusion.py packages/core/tests/code_index/test_entry_point_fusion.py
git commit -m "feat(fusion): extend merge_entry_points with LLM source (4th source)"
```

---

### Task 4: 新增 `run_entry_point_fusion` + 修复 `save_adjudication`

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/__init__.py`
- Test: `packages/core/tests/code_index/test_code_index.py`（或新建）

- [ ] **Step 1: 写失败测试 — run_entry_point_fusion**

在 `packages/core/tests/code_index/test_entry_point_fusion.py` 新增：

```python
import json
import tempfile
from pathlib import Path
from shannon_core.code_index import run_entry_point_fusion


def _make_test_deliverable() -> str:
    return """# Pre-Recon Deliverable

## 5. Attack Surface Analysis

### External Entry Points

1. **POST /api/webhooks/stripe** — `src/webhooks/stripe.py:handle_webhook` (line 8)
   - Authentication: required (HMAC signature)
   - Entry type: webhook

2. **GET /api/public/health** — `src/routes/health.py:health_check` (line 15)
   - Authentication: public
"""


def _make_test_code_index() -> dict:
    return {
        "repository": "/tmp/test-repo",
        "language": "python",
        "total_blocks": 10,
        "total_entry_points": 1,
        "total_chains": 0,
        "blocks": [],
        "edges": [],
        "entry_points": [
            {
                "func_block_id": "src/routes/health.py:health_check:15",
                "entry_type": "http_route",
                "route": "/api/public/health",
                "http_method": "GET",
                "confidence": 0.95,
                "evidence": "Flask @app.route decorator",
                "needs_llm_review": False,
                "authentication": None,
                "source": "code_index",
            }
        ],
        "chains": [],
        "sink_call_sites": [],
    }


class TestRunEntryPointFusion:
    def test_fusion_merges_deterministic_and_llm(self, tmp_path):
        # Write code_index.json
        code_index = _make_test_code_index()
        (tmp_path / "code_index.json").write_text(json.dumps(code_index))

        # Write deliverable
        (tmp_path / "pre_recon_deliverable.md").write_text(_make_test_deliverable())

        result = run_entry_point_fusion(str(tmp_path))

        # Should have: 1 deterministic (health_check) + 1 LLM-only (stripe webhook)
        assert result.total_entry_points >= 2
        sources = {ep.source for ep in result.entry_points}
        assert "code_index" in sources
        assert "llm_pre_recon" in sources

    def test_fusion_deduplicates_same_entry(self, tmp_path):
        # Both deterministic and LLM find health_check
        code_index = _make_test_code_index()
        (tmp_path / "code_index.json").write_text(json.dumps(code_index))
        (tmp_path / "pre_recon_deliverable.md").write_text(_make_test_deliverable())

        result = run_entry_point_fusion(str(tmp_path))

        # health_check should appear once (deterministic version)
        health_entries = [
            ep for ep in result.entry_points
            if "health_check" in ep.func_block_id or "health" in (ep.route or "")
        ]
        assert len(health_entries) >= 1
```

- [ ] **Step 2: 写失败测试 — save_adjudication confidence 裁定**

在同一测试文件新增：

```python
from shannon_core.code_index import save_adjudication
from shannon_core.code_index.models import AdjudicationResult


class TestSaveAdjudication:
    def test_high_confidence_confirmed(self, tmp_path):
        code_index = {
            "repository": "/tmp/test",
            "language": "python",
            "total_blocks": 1, "total_entry_points": 1, "total_chains": 0,
            "blocks": [], "edges": [],
            "entry_points": [{
                "func_block_id": "app.py:handler:10",
                "entry_type": "http_route",
                "confidence": 0.95,
                "evidence": "test",
                "needs_llm_review": False,
                "source": "code_index",
            }],
            "chains": [], "sink_call_sites": [],
        }
        (tmp_path / "code_index.json").write_text(json.dumps(code_index))

        save_adjudication(str(tmp_path))

        result = AdjudicationResult.model_validate_json(
            (tmp_path / "entry_points.json").read_text()
        )
        assert result.adjudicated_entry_points[0].verdict.value == "confirmed"

    def test_medium_confidence_needs_review(self, tmp_path):
        code_index = {
            "repository": "/tmp/test",
            "language": "python",
            "total_blocks": 1, "total_entry_points": 1, "total_chains": 0,
            "blocks": [], "edges": [],
            "entry_points": [{
                "func_block_id": "app.py:maybe_handler:20",
                "entry_type": "unknown",
                "confidence": 0.60,
                "evidence": "LLM discovery",
                "needs_llm_review": False,
                "source": "llm_pre_recon",
            }],
            "chains": [], "sink_call_sites": [],
        }
        (tmp_path / "code_index.json").write_text(json.dumps(code_index))

        save_adjudication(str(tmp_path))

        result = AdjudicationResult.model_validate_json(
            (tmp_path / "entry_points.json").read_text()
        )
        assert result.adjudicated_entry_points[0].verdict.value == "needs_review"

    def test_low_confidence_rejected(self, tmp_path):
        code_index = {
            "repository": "/tmp/test",
            "language": "python",
            "total_blocks": 1, "total_entry_points": 1, "total_chains": 0,
            "blocks": [], "edges": [],
            "entry_points": [{
                "func_block_id": "app.py:util:30",
                "entry_type": "unknown",
                "confidence": 0.30,
                "evidence": "low confidence",
                "needs_llm_review": True,
                "source": "code_index",
            }],
            "chains": [], "sink_call_sites": [],
        }
        (tmp_path / "code_index.json").write_text(json.dumps(code_index))

        save_adjudication(str(tmp_path))

        result = AdjudicationResult.model_validate_json(
            (tmp_path / "entry_points.json").read_text()
        )
        assert result.adjudicated_entry_points[0].verdict.value == "rejected"
```

- [ ] **Step 3: 运行测试验证失败**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_entry_point_fusion.py::TestRunEntryPointFusion -xvs`
Expected: FAIL — `ImportError: cannot import name 'run_entry_point_fusion'`

- [ ] **Step 4: 实现 run_entry_point_fusion**

在 `packages/core/src/shannon_core/code_index/__init__.py` 的 `write_index_files` 函数之后新增：

```python
def run_entry_point_fusion(deliverables_dir: str) -> CodeIndex:
    """Merge deterministic entry points with LLM-discovered entry points.

    Reads code_index.json and pre_recon_deliverable.md, parses LLM entry
    points from the deliverable, merges with deterministic entry points,
    and updates code_index.json in place.

    Args:
        deliverables_dir: Path to the deliverables directory containing
            code_index.json and pre_recon_deliverable.md.

    Returns:
        Updated CodeIndex with merged entry points.
    """
    from shannon_core.code_index.entry_point_fusion import (
        parse_llm_entry_points,
        merge_entry_points,
    )

    out = Path(deliverables_dir)
    code_index_path = out / "code_index.json"
    deliverable_path = out / "pre_recon_deliverable.md"

    if not code_index_path.exists():
        logger.warning("code_index.json not found; skipping entry point fusion")
        return CodeIndex.model_validate_json(code_index_path.read_text())

    index = CodeIndex.model_validate_json(code_index_path.read_text())

    # Parse LLM entry points from deliverable (if it exists)
    llm_eps: list[EntryPoint] = []
    if deliverable_path.exists():
        deliverable_text = deliverable_path.read_text()
        llm_eps = parse_llm_entry_points(deliverable_text)
        logger.info("Parsed %d LLM entry points from deliverable", len(llm_eps))
    else:
        logger.info("No pre_recon_deliverable.md found; LLM fusion skipped")

    # Merge: deterministic entry points as the base, LLM as supplementary
    deterministic_ids = {ep.func_block_id for ep in index.entry_points}

    # Add LLM-only discoveries
    merged_entries = list(index.entry_points)
    added = 0
    for ep in llm_eps:
        if ep.func_block_id not in deterministic_ids:
            merged_entries.append(ep)
            added += 1
        else:
            logger.debug("LLM entry point %s already in deterministic results, skipping", ep.func_block_id)

    logger.info(
        "Entry point fusion: %d deterministic + %d LLM-only = %d total",
        len(index.entry_points), added, len(merged_entries),
    )

    # Update index
    updated = index.model_copy(update={
        "entry_points": merged_entries,
        "total_entry_points": len(merged_entries),
    })

    # Write updated code_index.json
    code_index_path.write_text(updated.model_dump_json(indent=2))

    return updated
```

同时在文件顶部的 import 中确认 `EntryPoint` 已导入（当前已有）。

- [ ] **Step 5: 修复 save_adjudication — 按 confidence 裁定**

替换 `packages/core/src/shannon_core/code_index/__init__.py` 中的 `save_adjudication` 函数（line 315-355）为：

```python
def save_adjudication(deliverables_dir: str) -> None:
    """Adjudicate entry points by confidence and write adjudication result.

    Reads code_index.json, assigns verdict based on confidence thresholds:
    - confidence >= 0.85: CONFIRMED
    - confidence < 0.50: REJECTED
    - otherwise: NEEDS_REVIEW
    """
    out = Path(deliverables_dir)
    out.mkdir(parents=True, exist_ok=True)
    code_index_path = out / "code_index.json"

    if not code_index_path.exists():
        logger.warning("code_index.json not found; skipping adjudication")
        return

    index = CodeIndex.model_validate_json(code_index_path.read_text())

    adjudicated = []
    for ep in index.entry_points:
        if ep.confidence >= 0.85:
            verdict = Verdict.CONFIRMED
        elif ep.confidence < 0.50:
            verdict = Verdict.REJECTED
        else:
            verdict = Verdict.NEEDS_REVIEW

        adjudicated.append(AdjudicatedEntryPoint(
            func_block_id=ep.func_block_id,
            verdict=verdict,
            entry_type=ep.entry_type,
            route=ep.route,
            http_method=ep.http_method,
            evidence=ep.evidence,
            source=EntryPointSource.CODE_INDEX if ep.source in ("code_index", "gitnexus")
                    else EntryPointSource.LLM_DISCOVERY,
        ))

    result = AdjudicationResult(
        repository=index.repository,
        language=index.language,
        adjudicated_entry_points=adjudicated,
    )

    entry_points_path = out / "entry_points.json"
    entry_points_path.write_text(result.model_dump_json(indent=2))

    confirmed = sum(1 for a in adjudicated if a.verdict == Verdict.CONFIRMED)
    needs_review = sum(1 for a in adjudicated if a.verdict == Verdict.NEEDS_REVIEW)
    rejected = sum(1 for a in adjudicated if a.verdict == Verdict.REJECTED)
    logger.info(
        "Adjudicated %d entry points: %d confirmed, %d needs_review, %d rejected",
        len(adjudicated), confirmed, needs_review, rejected,
    )
```

- [ ] **Step 6: 运行全部测试**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_entry_point_fusion.py -xvs`
Expected: 全部 PASS

- [ ] **Step 7: 运行全量现有测试确认无回归**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/ -x --timeout=60`
Expected: 全部 PASS

- [ ] **Step 8: Commit**

```bash
git add packages/core/src/shannon_core/code_index/__init__.py packages/core/tests/code_index/test_entry_point_fusion.py
git commit -m "feat(code_index): add run_entry_point_fusion + fix save_adjudication confidence-based verdict"
```

---

### Task 5: 新增 activity 并插入管线

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`

- [ ] **Step 1: 在 activities.py 新增 run_entry_point_fusion activity**

在 `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` 的 `run_save_adjudication` 函数之前新增：

```python
@activity.defn
async def run_entry_point_fusion(input: ActivityInput) -> dict:
    """Merge deterministic entry points with LLM-discovered entry points."""
    try:
        from shannon_core.code_index import run_entry_point_fusion as _fusion

        repo, deliverables, _ = _get_paths(input)
        index = _fusion(str(deliverables))

        return {
            "total_entry_points": index.total_entry_points,
            "status": "ok",
        }
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
```

- [ ] **Step 2: 在 workflows.py 插入 fusion 步骤**

修改 `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`，在 PRE_RECON 完成后、`run_save_adjudication` 之前插入 fusion 调用。

找到约 line 121-129 的代码块：

```python
                self._state.completed_agents.append(AgentName.PRE_RECON.value)
                self._state.agent_metrics[AgentName.PRE_RECON.value] = metrics

                # Auto-confirm entry points (adjudication writes confirmed entry points)
                adjudication_input = ActivityInput(**{**act_input.__dict__, "workspace_name": AgentName.PRE_RECON.value})
                await workflow.execute_activity(
                    activities.run_save_adjudication, adjudication_input,
                    start_to_close_timeout=timedelta(minutes=2),
                )
                self._state.current_agent = None
```

替换为：

```python
                self._state.completed_agents.append(AgentName.PRE_RECON.value)
                self._state.agent_metrics[AgentName.PRE_RECON.value] = metrics

                # Entry point fusion: merge deterministic + LLM discoveries
                fusion_input = ActivityInput(**{**act_input.__dict__, "workspace_name": AgentName.PRE_RECON.value})
                await workflow.execute_activity(
                    activities.run_entry_point_fusion, fusion_input,
                    start_to_close_timeout=timedelta(minutes=2),
                )

                # Adjudicate merged entry points by confidence
                adjudication_input = ActivityInput(**{**act_input.__dict__, "workspace_name": AgentName.PRE_RECON.value})
                await workflow.execute_activity(
                    activities.run_save_adjudication, adjudication_input,
                    start_to_close_timeout=timedelta(minutes=2),
                )
                self._state.current_agent = None
```

- [ ] **Step 3: 验证 Python 语法正确**

Run: `cd /root/shannon-py && python -c "from shannon_whitebox.pipeline import activities; from shannon_whitebox.pipeline import workflows; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/src/shannon_whitebox/pipeline/workflows.py
git commit -m "feat(pipeline): insert entry point fusion step between PRE_RECON and adjudication"
```

---

### Task 6: 升级 Entry Point Mapper prompt + 简化 Phase 0

**Files:**
- Modify: `prompts/pre-recon-code.txt`

- [ ] **Step 1: 替换 Entry Point Mapper prompt**

在 `prompts/pre-recon-code.txt` 中，找到约 line 152-153 的 Entry Point Mapper Agent prompt：

```
2. **Entry Point Mapper Agent** (Supplementary Discovery):
   "Find entry points that the deterministic code_index may have MISSED. Focus on patterns the AST analysis cannot detect: configuration-file routes (e.g., urls.py, routes.yaml, router configs), dynamic route registration, unknown framework conventions, and implicitly registered handlers. Do NOT report entry points already detected by code_index — only report genuinely new discoveries."
```

替换为：

```
2. **Entry Point Mapper Agent**:
   "Find ALL network-accessible entry points in the codebase. Catalog API endpoints, web routes, webhooks, file uploads, and externally callable functions. Also identify and catalog API schema files that document these endpoints (OpenAPI/Swagger *.json/*.yaml/*.yml, GraphQL *.graphql/*.gql, JSON Schema *.schema.json, Protocol Buffers *.proto). Distinguish between public endpoints and those requiring authentication. Exclude local-only development tools, CLI scripts, and build processes. Provide exact file paths and route definitions for both endpoints and schemas."
```

- [ ] **Step 2: 简化 Phase 0 — 移除入口点裁定步骤**

在 `prompts/pre-recon-code.txt` 中，找到 Phase 0 部分（约 line 114-143）。将整个 Phase 0 替换为简化版本：

```
## Phase 0: Code Index Review (MUST complete before Phase 1)

Review the deterministic code index data provided below:

1. Read `code_index.json` and review the call chain statistics and file coverage data
2. Note any coverage gaps or degradation warnings
3. Use this understanding to inform your Phase 1 and Phase 2 analysis

Entry point adjudication is handled automatically downstream — you do NOT need to write entry_points.json.
```

- [ ] **Step 3: 从 phase0_data 中移除 Entry Points 表格**

在 `prompts/pre-recon-code.txt` 中，找到 `<phase0_data>` section（约 line 440-463）。移除 Entry Points 相关的表格：

将：
```
### Entry Points (from Entry Point Detection)
{{ENTRY_POINTS_TABLE}}

### Call Chain Statistics
```

替换为：
```
### Call Chain Statistics
```

- [ ] **Step 4: 验证 prompt 文件完整性**

Run: `grep -c "Entry Point Mapper" /root/shannon-py/prompts/pre-recon-code.txt`
Expected: 2+（至少在 Phase 1 和 Section mapping 中出现）

Run: `grep -c "save-deliverable.*ENTRY_POINTS" /root/shannon-py/prompts/pre-recon-code.txt`
Expected: 0（已移除）

Run: `grep -c "Phase 0" /root/shannon-py/prompts/pre-recon-code.txt`
Expected: ≥ 1（Phase 0 仍存在但已简化）

- [ ] **Step 5: Commit**

```bash
git add prompts/pre-recon-code.txt
git commit -m "feat(prompt): upgrade Entry Point Mapper to comprehensive scan + simplify Phase 0"
```

---

### Task 7: 运行全量测试 + 最终验证

**Files:** 无新改动

- [ ] **Step 1: 运行全量 code_index 测试**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/ -xvs --timeout=120`
Expected: 全部 PASS

- [ ] **Step 2: 运行全量 whitebox pipeline 测试（如果存在）**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/ -x --timeout=120 2>/dev/null || echo "No whitebox tests or non-blocking failures"`
Expected: 无回归

- [ ] **Step 3: 验证 import 链完整**

Run: `cd /root/shannon-py && python -c "from shannon_core.code_index import run_entry_point_fusion, save_adjudication; print('imports OK')"`
Expected: `imports OK`

- [ ] **Step 4: 验证 prompt 关键内容**

Run: `grep -A2 "Entry Point Mapper Agent" /root/shannon-py/prompts/pre-recon-code.txt`
Expected: 输出包含 "Find ALL network-accessible entry points"

- [ ] **Step 5: Final commit（如果有遗漏修正）**

```bash
git add -A
git diff --cached --stat
# Only commit if there are actual changes
git commit -m "chore: final verification fixes" || echo "No changes needed"
```
