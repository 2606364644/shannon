# 报告渲染崩溃止血(L0.5+L1)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让白盒报告渲染对任意格式的 exploitation queue 文件(裸 list / 坏 JSON / 字段漂移)都不再崩溃,并把确定性的数据格式错误归为 non-retryable,杜绝 Temporal 无意义重试。

**Architecture:** 新增 `VulnerabilityQueue.parse_lenient` 容错解析(返回 `LenientParseResult`,带 warnings,永不抛),替换三处裸 `model_validate_json`(whitebox renderer + blackbox 两处);renderer 加单 class / 单 vuln 隔离,坏数据写可见 warning 而非 none_found;`errors.py` 把 pydantic ValidationError 归为 non-retryable。这是止血层(L1+L0.5),治本层(L2 schema + prompt)见 spec,冒烟后另起计划。

**Tech Stack:** Python 3.13, pydantic 2.13, pytest + pytest-asyncio, temporalio。

**Spec:** `docs/superpowers/specs/2026-06-22-report-render-queue-format-fix.md`

## Global Constraints

- **只跑相关测试子集** —— 全量 pytest 会 hang 于 Temporal/网络(memory:`pytest-whitebox-hang`)。禁止 `pytest` 全跑;每个 task 的命令精确到文件。
- 中文回复用户;代码、标识符、commit message 用英文。
- pydantic 2.13 API:`model_validate(dict)`、`model_validate_json(str)`、`model_dump_json()`。
- 不改:`workspace.py`、`paths.py`、`*/cli/main.py`(已自带宽容读取)。
- 不在本计划范围:L2(`VULN_QUEUE_SCHEMA` + `run_agent` 传 schema + prompt)、L2 测试。

## File Structure

| 文件 | 责任 | 本计划改动 |
|---|---|---|
| `packages/core/src/shannon_core/models/queue_schemas.py` | vuln queue 模型 | 加 `LenientParseResult` + `VulnerabilityQueue.parse_lenient` |
| `packages/core/src/shannon_core/models/errors.py` | Temporal 错误分类 | Level 2 加 pydantic ValidationError → non-retryable |
| `packages/core/src/shannon_core/services/findings_renderer.py` | 渲染 findings.md | line 213-214 替换 + 隔离 + warning |
| `packages/blackbox/src/shannon_blackbox/services/coverage_renderer.py` | blackbox 覆盖缺口渲染 | line 107 替换 |
| `packages/blackbox/src/shannon_blackbox/services/exploitation_checker.py` | blackbox queue 覆盖比对 | line 208 替换 |
| `packages/core/tests/test_queue_schemas.py` | queue 模型测试 | 加 `parse_lenient` 用例 |
| `packages/core/tests/test_error_classification.py` | 错误分类测试 | 加 ValidationError 用例 |
| `packages/core/tests/test_findings_renderer.py` | renderer 测试 | 加 bare_list/隔离/warning 用例 |
| `packages/blackbox/tests/test_coverage_renderer.py` | coverage 测试 | 加 bare_list 用例 |
| `packages/blackbox/tests/test_exploitation_checker.py` | checker 测试 | 加 bare_list 用例 |

**接口契约(跨 task):**
- Task 1 产出 `VulnerabilityQueue.parse_lenient(content: str) -> LenientParseResult`,其中 `LenientParseResult.queue: VulnerabilityQueue`、`LenientParseResult.warnings: list[str]`、`LenientParseResult.original_form: str`。Task 3、4 消费它。

---

## Task 1: `VulnerabilityQueue.parse_lenient` 容错解析

**Files:**
- Modify: `packages/core/src/shannon_core/models/queue_schemas.py`
- Test: `packages/core/tests/test_queue_schemas.py`

**Interfaces:**
- Produces: `LenientParseResult` dataclass;`VulnerabilityQueue.parse_lenient(cls, content: str) -> LenientParseResult`(classmethod,永不抛)。

- [ ] **Step 1: 写失败测试**

追加到 `packages/core/tests/test_queue_schemas.py` 末尾:

```python
from shannon_core.models.queue_schemas import LenientParseResult, VulnerabilityQueue


def test_parse_lenient_standard_object():
    content = VulnerabilityQueue(vulnerabilities=[
        InjectionVulnerability(
            ID="INJ-1", vulnerability_type="SQLi",
            externally_exploitable=True, confidence="high",
        ),
    ]).model_dump_json()
    result = VulnerabilityQueue.parse_lenient(content)
    assert result.original_form == "object"
    assert result.warnings == []
    assert len(result.queue.vulnerabilities) == 1
    assert result.queue.vulnerabilities[0].ID == "INJ-1"


def test_parse_lenient_wraps_bare_list():
    content = json.dumps([
        {"ID": "AUTH-1", "vulnerability_type": "Auth", "externally_exploitable": True, "confidence": "high"},
        {"ID": "AUTH-2", "vulnerability_type": "Auth", "externally_exploitable": True, "confidence": "medium"},
    ])
    result = VulnerabilityQueue.parse_lenient(content)
    assert result.original_form == "bare_list"
    assert any("bare-list" in w for w in result.warnings)
    assert len(result.queue.vulnerabilities) == 2
    assert result.queue.vulnerabilities[0].ID == "AUTH-1"


def test_parse_lenient_invalid_json():
    result = VulnerabilityQueue.parse_lenient("{not valid json")
    assert result.original_form == "invalid_json"
    assert len(result.queue.vulnerabilities) == 0
    assert any("invalid json" in w for w in result.warnings)


def test_parse_lenient_object_without_vulnerabilities_key():
    result = VulnerabilityQueue.parse_lenient(json.dumps({"meta": "no queue here"}))
    assert result.original_form == "object_no_key"
    assert len(result.queue.vulnerabilities) == 0
    assert any("vulnerabilities" in w for w in result.warnings)


def test_parse_lenient_drops_malformed_entries_keeps_good():
    content = json.dumps([
        {"ID": "GOOD-1", "vulnerability_type": "Auth", "externally_exploitable": True, "confidence": "high"},
        {"missing": "required fields"},
        {"ID": "GOOD-2", "vulnerability_type": "Auth", "externally_exploitable": True, "confidence": "low"},
    ])
    result = VulnerabilityQueue.parse_lenient(content)
    ids = [v.ID for v in result.queue.vulnerabilities]
    assert ids == ["GOOD-1", "GOOD-2"]
    assert any("dropped" in w for w in result.warnings)


def test_parse_lenient_vulnerabilities_not_a_list():
    content = json.dumps({"vulnerabilities": "not a list"})
    result = VulnerabilityQueue.parse_lenient(content)
    assert len(result.queue.vulnerabilities) == 0
    assert result.warnings  # some warning surfaced


def test_parse_lenient_returns_lenient_parse_result():
    result = VulnerabilityQueue.parse_lenient("[]")
    assert isinstance(result, LenientParseResult)
    assert hasattr(result, "queue")
    assert hasattr(result, "warnings")
    assert hasattr(result, "original_form")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest packages/core/tests/test_queue_schemas.py -v`
Expected: FAIL —— `LenientParseResult` 和 `parse_lenient` 不存在(ImportError / AttributeError)。

- [ ] **Step 3: 实现 parse_lenient**

修改 `packages/core/src/shannon_core/models/queue_schemas.py`。文件顶部加 `import json` 和 `from dataclasses import dataclass, field`;在 `Vulnerability` Union 定义之后、`VulnerabilityQueue` 之前加 `LenientParseResult`;给 `VulnerabilityQueue` 加 `parse_lenient` classmethod。

完整改后的文件关键部分(替换现有 line 1-63):

```python
import json
from dataclasses import dataclass, field
from typing import Union

from pydantic import BaseModel


class BaseVulnerability(BaseModel):
    ID: str
    vulnerability_type: str
    externally_exploitable: bool
    confidence: str
    notes: str | None = None

# ... (InjectionVulnerability / XssVulnerability / AuthVulnerability / SsrfVulnerability / AuthzVulnerability 保持不变) ...

Vulnerability = Union[InjectionVulnerability, XssVulnerability, AuthVulnerability, SsrfVulnerability, AuthzVulnerability, BaseVulnerability]


@dataclass
class LenientParseResult:
    """Result of lenient queue parsing.

    ``queue`` is always a valid (possibly empty) VulnerabilityQueue.
    ``warnings`` is non-empty whenever lenient recovery was applied —
    callers MUST surface these (never silent).
    """
    queue: "VulnerabilityQueue"
    warnings: list[str] = field(default_factory=list)
    original_form: str = "object"  # object | bare_list | object_no_key | invalid_json


class VulnerabilityQueue(BaseModel):
    vulnerabilities: list[Vulnerability] = []

    @classmethod
    def parse_lenient(cls, content: str) -> LenientParseResult:
        """Tolerantly parse a queue file, absorbing legacy/hand-written forms.

        Never raises. Supported forms:
        - {"vulnerabilities": [...]}            -> object (normal)
        - [...]                                  -> bare_list (wrapped)
        - {...} without "vulnerabilities"        -> object_no_key (empty)
        - invalid JSON                           -> invalid_json (empty)
        Per-entry schema failures are dropped (recorded in warnings).
        """
        warnings: list[str] = []

        # --- JSON decode ---
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, ValueError) as exc:
            return LenientParseResult(
                queue=cls(vulnerabilities=[]),
                warnings=[f"invalid json: {exc}"],
                original_form="invalid_json",
            )

        # --- Normalize top-level form into an entries list ---
        if isinstance(data, list):
            warnings.append(f"wrapped bare-list form ({len(data)} entries)")
            original_form = "bare_list"
            entries = data
        elif isinstance(data, dict):
            entries = data.get("vulnerabilities")
            if not isinstance(entries, list):
                actual = type(entries).__name__ if entries is not None else "None"
                warnings.append(f"'vulnerabilities' is {actual}, expected list")
                return LenientParseResult(
                    queue=cls(vulnerabilities=[]),
                    warnings=warnings,
                    original_form="object_no_key",
                )
            original_form = "object"
        else:
            warnings.append(f"top-level JSON is {type(data).__name__}, expected object or array")
            return LenientParseResult(
                queue=cls(vulnerabilities=[]),
                warnings=warnings,
                original_form="invalid_json",
            )

        # --- Validate entries individually, drop malformed ---
        vulns: list[Vulnerability] = []
        dropped = 0
        for entry in entries:
            if not isinstance(entry, dict):
                dropped += 1
                continue
            try:
                vulns.append(Vulnerability.model_validate(entry))
            except Exception:
                dropped += 1
        if dropped:
            warnings.append(f"dropped {dropped} malformed entr{'y' if dropped == 1 else 'ies'}")

        return LenientParseResult(
            queue=cls(vulnerabilities=vulns),
            warnings=warnings,
            original_form=original_form,
        )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest packages/core/tests/test_queue_schemas.py -v`
Expected: PASS(含原有用例 + 7 个新用例全绿)。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/models/queue_schemas.py packages/core/tests/test_queue_schemas.py
git commit -m "feat(queue): add VulnerabilityQueue.parse_lenient for tolerant parsing"
```

---

## Task 2: pydantic ValidationError 归为 non-retryable(L0.5)

**Files:**
- Modify: `packages/core/src/shannon_core/models/errors.py`(Level 2,line 154-156 的 Output validation 段之后)
- Test: `packages/core/tests/test_error_classification.py`

**Interfaces:**
- 无新接口;修改 `classify_error_for_temporal` 返回值。

- [ ] **Step 1: 写失败测试**

追加到 `packages/core/tests/test_error_classification.py`(如文件不存在则新建并遵循现有 import 风格 `from shannon_core.models.errors import classify_error_for_temporal`):

```python
def test_pydantic_validation_error_is_non_retryable():
    """Deterministic data-format errors must not trigger Temporal retries."""
    from pydantic import BaseModel, ValidationError

    class M(BaseModel):
        x: int

    try:
        M.model_validate({"x": "not an int"})
    except ValidationError as exc:
        error_type, retryable = classify_error_for_temporal(exc)
        assert retryable is False
        assert error_type == "OutputValidationError"
        return
    raise AssertionError("ValidationError was not raised")


def test_input_should_be_text_is_non_retryable():
    """Raw pydantic error string surfaces non-retryable even without the exception type."""

    class FakeError(Exception):
        pass

    err = FakeError("1 validation error for VulnerabilityQueue\n  Input should be an object")
    error_type, retryable = classify_error_for_temporal(err)
    assert retryable is False
    assert error_type == "OutputValidationError"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest packages/core/tests/test_error_classification.py -v -k "validation_error or input_should_be"`
Expected: FAIL —— 当前 default 返回 `("TransientError", True)`,`retryable is True`。

- [ ] **Step 3: 实现**

修改 `packages/core/src/shannon_core/models/errors.py`,在 line 156(`# Output validation` 段的 if 之后)、line 158(`# Invalid request` 之前)插入:

```python
    # Pydantic / data-format validation — deterministic, retrying won't change input
    if "validation error" in text or "input should be" in text:
        return ("OutputValidationError", False)
```

(放在 Output validation 段之后,语义聚集;"validation error" 不含 "output validation",与 line 155 不冲突。)

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest packages/core/tests/test_error_classification.py -v`
Expected: PASS(原有用例 + 2 个新用例全绿)。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/models/errors.py packages/core/tests/test_error_classification.py
git commit -m "fix(errors): classify pydantic ValidationError as non-retryable"
```

---

## Task 3: findings_renderer 容错 + 隔离 + warning(L1 主修)

**Files:**
- Modify: `packages/core/src/shannon_core/services/findings_renderer.py`(顶部加 logger;`render_findings_from_queues` line 200-227 重写)
- Test: `packages/core/tests/test_findings_renderer.py`

**Interfaces:**
- Consumes: `VulnerabilityQueue.parse_lenient`(Task 1)。
- 不改 `render_*_entry` / `filter_vulnerabilities` / `CLASS_CONFIG` 签名。

- [ ] **Step 1: 写失败测试**

追加到 `packages/core/tests/test_findings_renderer.py`(import 区已有 `VulnerabilityQueue`,复用):

```python
@pytest.mark.asyncio
async def test_render_recovers_bare_list_queue(tmp_path):
    """NodeGoat regression: bare-list queue renders instead of crashing."""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    bare_list = json.dumps([
        {"ID": "AUTH-1", "vulnerability_type": "Auth",
         "externally_exploitable": True, "confidence": "high",
         "source_endpoint": "POST /login"},
    ])
    (deliverables / "auth_exploitation_queue.json").write_text(bare_list)

    await FindingsRenderer.render_findings_from_queues(deliverables)

    findings = (deliverables / "auth_findings.md").read_text()
    assert "### AUTH-1" in findings
    assert "**Source Endpoint:** POST /login" in findings
    assert "auto-recovered" in findings.lower() or "bare-list" in findings.lower()


@pytest.mark.asyncio
async def test_render_isolates_bad_class(tmp_path):
    """A bad queue in one class must not block rendering of another."""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "auth_exploitation_queue.json").write_text("{not valid json")
    good = VulnerabilityQueue(vulnerabilities=[
        InjectionVulnerability(
            ID="INJ-1", vulnerability_type="SQLi",
            externally_exploitable=True, confidence="high",
            sink_call="db.execute",
        ),
    ])
    (deliverables / "injection_exploitation_queue.json").write_text(good.model_dump_json())

    await FindingsRenderer.render_findings_from_queues(deliverables)

    inj = (deliverables / "injection_findings.md").read_text()
    assert "### INJ-1" in inj
    auth = (deliverables / "auth_findings.md").read_text()
    assert "## Authentication Vulnerabilities" in auth
    assert "No authentication vulnerabilities found." not in auth  # not none_found
    assert "auth_exploitation_queue.json" in auth  # surfaces the bad file


@pytest.mark.asyncio
async def test_render_entry_isolation(tmp_path):
    """A single vuln that fails to render must not abort the whole class.

    Simulated by injecting a vuln whose render touches an attribute the entry
    lacks — covered indirectly via malformed entries being dropped by
    parse_lenient and good entries still rendering.
    """
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    content = json.dumps([
        {"ID": "AUTH-1", "vulnerability_type": "Auth",
         "externally_exploitable": True, "confidence": "high",
         "source_endpoint": "POST /login"},
        {"no_required_fields": True},  # dropped by parse_lenient
        {"ID": "AUTH-2", "vulnerability_type": "Auth",
         "externally_exploitable": True, "confidence": "medium"},
    ])
    (deliverables / "auth_exploitation_queue.json").write_text(content)

    await FindingsRenderer.render_findings_from_queues(deliverables)

    findings = (deliverables / "auth_findings.md").read_text()
    assert "### AUTH-1" in findings
    assert "### AUTH-2" in findings


@pytest.mark.asyncio
async def test_render_standard_empty_queue_still_none_found(tmp_path):
    """Regression guard: a well-formed empty queue still reads 'none found'."""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "xss_exploitation_queue.json").write_text(
        VulnerabilityQueue(vulnerabilities=[]).model_dump_json()
    )

    await FindingsRenderer.render_findings_from_queues(deliverables)

    findings = (deliverables / "xss_findings.md").read_text()
    assert "No XSS vulnerabilities found." in findings
    assert "auto-recovered" not in findings.lower()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest packages/core/tests/test_findings_renderer.py -v -k "recovers_bare_list or isolates_bad or entry_isolation or standard_empty"`
Expected: FAIL —— `recovers_bare_list` 与 `isolates_bad` 在 `model_validate_json` 处抛 ValidationError。

- [ ] **Step 3: 实现**

修改 `packages/core/src/shannon_core/services/findings_renderer.py`。

顶部 import 区(line 1-15 附近)加 logging:

```python
import logging
```

并在 import 之后、`SEVERITY_ORDER` 之前加:

```python
logger = logging.getLogger(__name__)
```

完整重写 `render_findings_from_queues`(替换 line 199-227 的方法体,保留 `class FindingsRenderer:` 与签名):

```python
class FindingsRenderer:
    @staticmethod
    async def render_findings_from_queues(
        deliverables_path: Path,
        report_config: ReportConfig | None = None,
    ) -> None:
        config = report_config or ReportConfig()
        for _vuln_class, class_cfg in CLASS_CONFIG.items():
            findings_path = deliverables_path / class_cfg.findings_file
            if await async_path_exists(findings_path):
                continue
            queue_path = deliverables_path / class_cfg.queue_file
            if not await async_path_exists(queue_path):
                continue

            try:
                content = await async_read_file(queue_path)
                parsed = VulnerabilityQueue.parse_lenient(content)
            except Exception as exc:  # noqa: BLE001 — isolate this class
                logger.warning("queue %s unreadable: %s", class_cfg.queue_file, exc)
                await async_write_file(findings_path, "\n".join([
                    f"## {class_cfg.heading}", "",
                    f"> ⚠️ {class_cfg.heading} queue unreadable; findings unavailable for this class. See logs.",
                    "", DISCLAIMER, "",
                ]))
                continue

            if parsed.warnings:
                logger.warning(
                    "queue %s parsed leniently: %s", class_cfg.queue_file, parsed.warnings
                )
            queue = parsed.queue
            filtered = filter_vulnerabilities(queue, config)

            sections: list[str] = [f"## {class_cfg.heading}", ""]
            if parsed.warnings:
                sections.append(
                    "> ⚠️ Queue auto-recovered ("
                    + "; ".join(parsed.warnings)
                    + f"). Raw queue preserved at `{class_cfg.queue_file}`; verify data integrity."
                )
                sections.append("")

            if not filtered:
                if parsed.original_form == "object" and not parsed.warnings:
                    sections.append(class_cfg.none_found_label)
                else:
                    reason = "; ".join(parsed.warnings) or "no parseable entries"
                    sections.append(
                        f"> ⚠️ No renderable entries in `{class_cfg.queue_file}` ({reason})."
                    )
            else:
                for vuln in filtered:
                    try:
                        sections.append(class_cfg.render_entry(vuln))
                    except Exception as exc:  # noqa: BLE001 — isolate single entry
                        logger.warning(
                            "render entry %s failed: %s",
                            getattr(vuln, "ID", "?"), exc,
                        )
                        sections.append(
                            f"### {getattr(vuln, 'ID', 'UNKNOWN')} — render error\n"
                        )

            sections.append("")
            sections.append(DISCLAIMER)
            sections.append("")
            await async_write_file(findings_path, "\n".join(sections))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest packages/core/tests/test_findings_renderer.py -v`
Expected: PASS(原有 11 用例 + 4 新用例全绿;`test_render_findings_empty_queue` 仍绿)。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/services/findings_renderer.py packages/core/tests/test_findings_renderer.py
git commit -m "fix(renderer): lenient queue parse + per-class/entry isolation + warnings"
```

---

## Task 4: blackbox 两处裸解析改用 parse_lenient

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/services/exploitation_checker.py:208`
- Modify: `packages/blackbox/src/shannon_blackbox/services/coverage_renderer.py:107`
- Test: `packages/blackbox/tests/test_exploitation_checker.py`、`packages/blackbox/tests/test_coverage_renderer.py`

**Interfaces:**
- Consumes: `VulnerabilityQueue.parse_lenient`(Task 1)。
- 两处替换点签名不变(仍得到 queue / queue_ids),仅从裸 `model_validate_json` 换成 `parse_lenient`。

- [ ] **Step 1: 写失败测试**

追加到 `packages/blackbox/tests/test_exploitation_checker.py` 末尾(该文件已 `import json` 并 `from shannon_blackbox.services.exploitation_checker import ExploitationChecker`;`check_coverage` 签名为 `(queue_path: Path, evidence_path: Path, vuln_class: str) -> CoverageResult | None`,evidence 中 `### ID:` 行被 `extract_covered_ids` 视为已覆盖):

```python
@pytest.mark.asyncio
async def test_check_coverage_tolerates_bare_list_queue(tmp_path):
    """Bare-list queue must not crash check_coverage (recovered via parse_lenient)."""
    queue_path = tmp_path / "auth_exploitation_queue.json"
    queue_path.write_text(json.dumps([
        {"ID": "AUTH-VULN-01", "vulnerability_type": "t",
         "externally_exploitable": True, "confidence": "high"},
    ]))
    evidence_path = tmp_path / "auth_exploitation_evidence.md"
    evidence_path.write_text("## Successfully Exploited\n### AUTH-VULN-01: a\n")

    result = await ExploitationChecker.check_coverage(queue_path, evidence_path, "auth")
    assert result is not None
    assert "AUTH-VULN-01" in result.covered_ids
```

追加到 `packages/blackbox/tests/test_coverage_renderer.py` 末尾(该文件已有 helper `_write_queue(tmp_path, vuln_class, ids)` 写标准 object 格式,以及 `from shannon_blackbox.services.coverage_renderer import close_coverage_gaps`;unverified section heading 为 `## Unverified Findings (Not Dynamically Exploited)`):

```python
def _write_bare_list_queue(tmp_path, vuln_class, ids):
    (tmp_path / f"{vuln_class}_exploitation_queue.json").write_text(json.dumps([
        {"ID": i, "vulnerability_type": "t", "externally_exploitable": True, "confidence": "high"}
        for i in ids
    ]))


@pytest.mark.asyncio
async def test_close_coverage_gaps_tolerates_bare_list_queue(tmp_path):
    """Bare-list queue must not crash close_coverage_gaps (recovered via parse_lenient)."""
    _write_bare_list_queue(tmp_path, "auth", ["AUTH-VULN-01", "AUTH-VULN-02"])
    (tmp_path / "auth_exploitation_evidence.md").write_text(
        "# Ev\n## Successfully Exploited\n### AUTH-VULN-01: a\n"
    )

    results = await close_coverage_gaps(tmp_path, ["auth"])

    assert len(results) == 1
    assert results[0].uncovered_ids == frozenset({"AUTH-VULN-02"})
    ev = (tmp_path / "auth_exploitation_evidence.md").read_text()
    assert "### AUTH-VULN-02" in ev
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest packages/blackbox/tests/test_exploitation_checker.py packages/blackbox/tests/test_coverage_renderer.py -v -k "bare_list"`
Expected: FAIL —— 两处 `model_validate_json` 对裸 list 抛 ValidationError。

- [ ] **Step 3: 实现 exploitation_checker.py**

修改 `packages/blackbox/src/shannon_blackbox/services/exploitation_checker.py` line 208:

```python
        queue = VulnerabilityQueue.parse_lenient(await async_read_file(queue_path)).queue
        queue_ids = {v.ID for v in queue.vulnerabilities}
```

(把 `VulnerabilityQueue.model_validate_json(...)` 整行替换为 `parse_lenient(...).queue`。)

- [ ] **Step 4: 实现 coverage_renderer.py**

修改 `packages/blackbox/src/shannon_blackbox/services/coverage_renderer.py` line 107:

```python
        queue = VulnerabilityQueue.parse_lenient(await async_read_file(queue_path)).queue
        section = render_unverified_section(result, queue)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest packages/blackbox/tests/test_exploitation_checker.py packages/blackbox/tests/test_coverage_renderer.py -v`
Expected: PASS(原有用例 + 2 新用例全绿)。

- [ ] **Step 6: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/services/exploitation_checker.py \
        packages/blackbox/src/shannon_blackbox/services/coverage_renderer.py \
        packages/blackbox/tests/test_exploitation_checker.py \
        packages/blackbox/tests/test_coverage_renderer.py
git commit -m "fix(blackbox): lenient queue parse in check_coverage and close_coverage_gaps"
```

---

## Task 5: 端到端验证(NodeGoat 坏 queue 重跑 render)

**Files:** 无改动;仅验证。

- [ ] **Step 1: 跑相关测试子集全绿**

Run:
```bash
python -m pytest packages/core/tests/test_queue_schemas.py \
  packages/core/tests/test_error_classification.py \
  packages/core/tests/test_findings_renderer.py \
  packages/blackbox/tests/test_exploitation_checker.py \
  packages/blackbox/tests/test_coverage_renderer.py -v
```
Expected: 全 PASS。

- [ ] **Step 2: 对 NodeGoat 坏 queue 验证恢复渲染**

NodeGoat 的坏 queue 仍在 `workspaces/NodeGoat_shannon-1782066250383/deliverables/auth_exploitation_queue.json`(裸 list)。用一个一次性脚本对该目录调 `render_findings_from_queues`,确认:

```bash
python -c "
import asyncio
from pathlib import Path
from shannon_core.services.findings_renderer import FindingsRenderer
d = Path('workspaces/NodeGoat_shannon-1782066250383/deliverables')
# remove any pre-existing auth_findings.md so it re-renders
p = d / 'auth_findings.md'
if p.exists(): p.unlink()
asyncio.run(FindingsRenderer.render_findings_from_queues(d))
print((d / 'auth_findings.md').read_text()[:600])
"
```
Expected: 不抛异常;输出含 `## Authentication Vulnerabilities`、`### AUTH-VULN-01`、`auto-recovered`/`bare-list` warning。

- [ ] **Step 3: 确认 renderer 不再因坏 queue 让 workflow 崩**

逻辑验证(读代码确认):`render_findings` activity(whitebox `activities.py:462`)的 except 不会再见到 `ValidationError`(renderer 内部已消化),即便逃逸,Task 2 也把它归为 non-retryable,Temporal 不再重试 4 次。

- [ ] **Step 4: 标记止血完成**

在 commit message 或 PR 描述记录:NodeGoat 式崩溃已止血,阶段 2(L2 schema + prompt)见 spec,待冒烟后另起计划。

---

## Self-Review 记录

- **Spec coverage**:
  - L0.5(errors.py)→ Task 2 ✓
  - L1 parse_lenient + LenientParseResult → Task 1 ✓
  - L1 findings_renderer(替换 + 单 class/单 vuln 隔离 + warning 文案、坏 class 不写 none_found)→ Task 3 ✓
  - L1 blackbox 两处(coverage_renderer:107、exploitation_checker:208)→ Task 4 ✓
  - L3 测试(queue_schemas / error_classification / findings_renderer / blackbox 两处)→ Task 1-4 各自 TDD ✓
  - 阶段 2(L2 + prompt)明确不在本计划(spec 已注明)✓
- **Type consistency**:`LenientParseResult.queue/.warnings/.original_form` 在 Task 1 定义,Task 3/4 消费,字段名一致;`parse_lenient` 签名一致。
- **Placeholder**:无 TBD/TODO;每步含完整测试代码与实现代码。Task 4 Step 1 的 blackbox 测试已对齐现有 fixture(`_write_queue` 的 `{ID, vulnerability_type, externally_exploitable, confidence}` 字段集、`close_coverage_gaps(tmp_path, [class])` 签名、`## Unverified Findings` heading、`extract_covered_ids` 解析 `### ID:` 的 evidence 格式)。
