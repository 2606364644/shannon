# pre-recon 双轨实现计划（Plan 5）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** pre-recon 双轨——LLM 轨（Phase 1/2 自主探索 sink/entry/模板，不被锚定）+ GitNexus 轨（确定性 entry_points / sinks / **模板转义指令**，补评估文档 §1.4 断路）作下限注入，pre-recon LLM 合并。

**Architecture:** 新写 `template_escape_detector`（扫模板文件 unescaped 指令，补断路）+ `build_pre_recon_gitnexus_track`（读 code_index.json 的 entry_points/sink_call_sites + file_manifest 模板 → 转义检测 → markdown）→ `run_agent` 仅对 PRE_RECON 注入 `prompt_variables={"pre_recon_gitnexus_track": md}` → `pre-recon-code.txt` 消费 `{{PRE_RECON_GITNEXUS_TRACK}}`。模式对齐 Plan 2/6（确定性事实作 LLM 下限，情报阶段在 LLM 端合并）。

**Tech Stack:** Python 3.12, pytest, pytest-asyncio

## Global Constraints

- **仅 PRE_RECON agent 注入**（`run_agent` 通用入口）；非 PRE_RECON 不注入
- code_index.json / file_manifest 缺失时**跳过**（不崩；降级空 track）
- 确定性 entry/sink/模板转义是**下限**（pre-recon LLM 仍须独立探索清单外，符合双轨"下限非上限"）
- 模板转义检测是**字符级正则**（EJS `<%-` / Jinja2 `{{|safe}}` / Mustache `{{{`），比 LLM 更可靠
- pre-recon 情报合并在 **LLM 端**（注入 markdown，pre-recon LLM 合并），非 Plan 3 文件合并（pre-recon 产物是 markdown 不是 queue）——同 Plan 6
- TDD + frequent commits（`feat(code_index):` / `feat(whitebox):` / `feat(prompt):`）
- 复用 `CodeIndex`（Plan 1 加了 parameter_graph）、`SinkCallSite`、`FileManifest.filter_by_type`（models.py:164）

---

### Task 1: `template_escape_detector`（补模板转义断路）

**Files:**
- Create: `packages/core/src/shannon_core/code_index/template_escape_detector.py`
- Test: `packages/core/tests/code_index/test_template_escape_detector.py`（Create）

**Interfaces:**
- Consumes: 模板文件 `Path` 列表（来自 `FileManifest.filter_by_type("template")`）
- Produces: `TemplateEscapeFinding`（file_path/line/directive/escaping/engine）+ `detect_template_escapes(files) -> list[TemplateEscapeFinding]`

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/code_index/test_template_escape_detector.py
from pathlib import Path

from shannon_core.code_index.template_escape_detector import (
    detect_template_escapes,
    detect_template_escape,
)


def test_detects_ejs_unescaped(tmp_path):
    f = tmp_path / "a.ejs"
    f.write_text("<%= safe %>\n<%- unsafe %>\n")
    findings = detect_template_escape(f)
    unescaped = [x for x in findings if x.escaping == "unescaped"]
    assert len(unescaped) == 1
    assert unescaped[0].line == 2
    assert "ejs" in unescaped[0].engine


def test_detects_jinja2_safe(tmp_path):
    f = tmp_path / "b.jinja2"
    f.write_text("{{ x }}\n{{ y | safe }}\n")
    findings = detect_template_escape(f)
    unescaped = [x for x in findings if x.escaping == "unescaped"]
    assert any("jinja" in x.engine for x in unescaped)


def test_detects_mustache_triple(tmp_path):
    f = tmp_path / "c.hbs"
    f.write_text("{{ escaped }}\n{{{ unescaped }}}\n")
    findings = detect_template_escape(f)
    unescaped = [x for x in findings if x.escaping == "unescaped"]
    assert any("mustache" in x.engine or "triple" in x.engine for x in unescaped)


def test_multiple_files(tmp_path):
    (tmp_path / "a.ejs").write_text("<%- x %>")
    (tmp_path / "b.jinja2").write_text("{{ x | safe }}")
    findings = detect_template_escapes([tmp_path / "a.ejs", tmp_path / "b.jinja2"])
    assert len(findings) == 2


def test_unreadable_file_skipped(tmp_path):
    findings = detect_template_escape(tmp_path / "nonexistent.ejs")
    assert findings == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_template_escape_detector.py -v`
Expected: FAIL — `ModuleNotFoundError: ...template_escape_detector`

- [ ] **Step 3: Implement the detector**

```python
# packages/core/src/shannon_core/code_index/template_escape_detector.py
"""Detect template escaping modes (escaped vs unescaped) in template files.

评估文档 §1.4 断路修复：file_discovery 分类了模板文件（SECURITY_FILE_TYPES），
但转义指令分析未接 sink_detector。本检测器扫模板文件，识别 UNESCAPED 输出指令
（字符级正则，比 LLM 更可靠），作为 pre-recon GitNexus 轨的确定性模板 sink。
"""

import re
from dataclasses import dataclass
from pathlib import Path

# UNESCAPED output directives per template engine (high-risk: raw user data → output)
# Order matters: more specific patterns first.
_UNESCAPED_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"<%-"), "ejs-unescaped"),            # EJS <%- %> (vs escaped <%= %>)
    (re.compile(r"\{\{[^}]*\|\s*safe\s*\}\}"), "jinja2-safe"),  # Jinja2 {{ x|safe }}
    (re.compile(r"\{\{\{"), "mustache-triple"),       # Mustache/Handlebars {{{ }}} (vs {{ }})
]

# ESCAPED (safe) directives — tracked for coverage audit (optional)
_ESCAPED_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"<%="), "ejs-escaped"),
    (re.compile(r"\{\{(?!.*\|safe)(?!\{)"), "jinja2-escaped"),
]


@dataclass(frozen=True)
class TemplateEscapeFinding:
    file_path: str
    line: int
    directive: str
    escaping: str   # "unescaped" | "escaped"
    engine: str     # "ejs-unescaped" | "jinja2-safe" | ...


def detect_template_escape(template_file: Path) -> list[TemplateEscapeFinding]:
    """Scan one template file for escaped/unescaped output directives."""
    findings: list[TemplateEscapeFinding] = []
    try:
        content = template_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return findings
    for i, line in enumerate(content.splitlines(), 1):
        for pat, tag in _UNESCAPED_PATTERNS:
            for m in pat.finditer(line):
                findings.append(TemplateEscapeFinding(
                    file_path=str(template_file), line=i,
                    directive=m.group().strip(), escaping="unescaped", engine=tag,
                ))
        for pat, tag in _ESCAPED_PATTERNS:
            for m in pat.finditer(line):
                findings.append(TemplateEscapeFinding(
                    file_path=str(template_file), line=i,
                    directive=m.group().strip(), escaping="escaped", engine=tag,
                ))
    return findings


def detect_template_escapes(template_files: list[Path]) -> list[TemplateEscapeFinding]:
    """Scan multiple template files; return all escape findings."""
    all_findings: list[TemplateEscapeFinding] = []
    for f in template_files:
        all_findings.extend(detect_template_escape(f))
    return all_findings
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_template_escape_detector.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/code_index/template_escape_detector.py packages/core/tests/code_index/test_template_escape_detector.py
git commit -m "feat(code_index): add template_escape_detector (fix §1.4 escaping analysis gap)"
```

---

### Task 2: `build_pre_recon_gitnexus_track`（确定性 track → markdown）

**Files:**
- Create: `packages/core/src/shannon_core/code_index/pre_recon_gitnexus_track.py`
- Test: `packages/core/tests/code_index/test_pre_recon_gitnexus_track.py`（Create）

**Interfaces:**
- Consumes: `CodeIndex`（entry_points / sink_call_sites / file_manifest）、`template_escape_detector`（Task 1）、`repo_root: Path`、`deliverables: Path`
- Produces: `build_pre_recon_gitnexus_track(repo_root, deliverables) -> str`（markdown）；`render_pre_recon_gitnexus_track(entry_points, sinks, escape_findings) -> str`

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/code_index/test_pre_recon_gitnexus_track.py
from pathlib import Path

from shannon_core.code_index.models import CodeIndex, EntryPoint, FileEntry, FileManifest
from shannon_core.code_index.parameter_models import SinkCallSite
from shannon_core.code_index.pre_recon_gitnexus_track import (
    build_pre_recon_gitnexus_track,
    render_pre_recon_gitnexus_track,
)
from shannon_core.code_index.template_escape_detector import TemplateEscapeFinding


def test_render_lists_entry_sinks_template():
    ep = EntryPoint(func_block_id="app.py:h:1", entry_type="http_route",
                    route="/api/x", http_method="GET", confidence=0.9,
                    evidence="router.get", needs_llm_review=False, authentication="public")
    sink = SinkCallSite(id="s1", caller_id="app.py:h:1", file_path="app.py",
                        line=5, column=8, callee_name="exec",
                        category=None, sink_subtype="cmd", dangerous_slots=[])
    finding = TemplateEscapeFinding(file_path="v.ejs", line=2, directive="<%-",
                                    escaping="unescaped", engine="ejs-unescaped")
    md = render_pre_recon_gitnexus_track([ep], [sink], [finding])
    assert "/api/x" in md
    assert "app.py:5" in md
    assert "v.ejs:2" in md
    assert "unescaped" in md
    assert "下限" in md or "独立" in md  # lower-bound disclaimer


def test_build_degrades_when_no_code_index(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    # no code_index.json
    md = build_pre_recon_gitnexus_track(tmp_path, deliverables)
    assert "降级" in md or "无" in md
```

> 注：`SinkCallSite` / `SinkCategory` 的必填字段以 `parameter_models.py` 实际定义为准（先 `grep "class SinkCallSite\|class SinkCategory" packages/core/src/shannon_core/code_index/parameter_models.py` 确认字段名，调整 test fixture）。`EntryPoint` 字段见 `models.py:49-60`。

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_pre_recon_gitnexus_track.py -v`
Expected: FAIL — `ModuleNotFoundError: ...pre_recon_gitnexus_track`

- [ ] **Step 3: Implement build + render**

```python
# packages/core/src/shannon_core/code_index/pre_recon_gitnexus_track.py
"""Build the deterministic pre-recon GitNexus track: entry points + sinks +
template escaping, rendered as markdown for injection into pre-recon LLM
via {{PRE_RECON_GITNEXUS_TRACK}}.

确定性事实作为 pre-recon LLM 的下限（dual-track: lower bound, not ceiling）。
pre-recon LLM 仍须独立探索清单外的 sink/entry/模板。
"""

from pathlib import Path

from shannon_core.code_index.models import CodeIndex, EntryPoint
from shannon_core.code_index.parameter_models import SinkCallSite
from shannon_core.code_index.template_escape_detector import (
    TemplateEscapeFinding,
    detect_template_escapes,
)

_ENTRY_CAP = 80
_SINK_CAP = 150
_TEMPLATE_CAP = 150


def render_pre_recon_gitnexus_track(
    entry_points: list[EntryPoint],
    sinks: list[SinkCallSite],
    escape_findings: list[TemplateEscapeFinding],
) -> str:
    """Render the deterministic track as markdown."""
    lines = [
        "## Pre-Recon GitNexus Track（确定性：entry points / sinks / 模板转义）",
        "",
        f"### Entry Points（{len(entry_points)}）",
    ]
    for ep in entry_points[:_ENTRY_CAP]:
        route = ep.route or ep.func_block_id
        lines.append(f"- `{ep.http_method or '?'} {route}` · auth={ep.authentication or '?'}")
    if len(entry_points) > _ENTRY_CAP:
        lines.append(f"- ...（+{len(entry_points) - _ENTRY_CAP} more，见 code_index.json）")

    lines.extend(["", f"### Sinks（{len(sinks)}）"])
    for s in sinks[:_SINK_CAP]:
        cat = s.category.value if s.category else "?"
        lines.append(f"- `{s.id}` ({s.file_path}:{s.line}) {cat}/{s.sink_subtype} @ `{s.callee_name}`")

    unescaped = [f for f in escape_findings if f.escaping == "unescaped"]
    lines.extend(["", f"### 模板转义（unescaped = 高危，{len(unescaped)}）"])
    for f in unescaped[:_TEMPLATE_CAP]:
        lines.append(f"- `{f.file_path}:{f.line}` {f.engine}: `{f.directive}` ⚠️ unescaped")

    lines.extend([
        "",
        "⚠️ **下限非上限**：以上 entry/sink/模板转义为确定性检测，pre-recon 必须"
        "覆盖这些；**仍须独立探索清单外的 sink/entry/模板**（确定性未列出 ≠ 安全）。",
    ])
    return "\n".join(lines)


def build_pre_recon_gitnexus_track(repo_root: Path, deliverables: Path) -> str:
    """Read code_index.json + file_manifest templates → deterministic track markdown."""
    ci_path = deliverables / "code_index.json"
    if not ci_path.exists():
        return "（无确定性 code_index.json，pre-recon GitNexus 轨降级为空。LLM 照常自主探索。）"

    index = CodeIndex.model_validate_json(ci_path.read_text())

    # Template files from file_manifest (or skip if absent)
    template_files: list[Path] = []
    if index.file_manifest:
        for entry in index.file_manifest.filter_by_type("template"):
            p = Path(repo_root) / entry.file_path
            if p.exists():
                template_files.append(p)
    escape_findings = detect_template_escapes(template_files)

    return render_pre_recon_gitnexus_track(
        index.entry_points, index.sink_call_sites, escape_findings
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_pre_recon_gitnexus_track.py -v`
Expected: PASS (2 tests)（若 `SinkCallSite` fixture 字段不符，按实际定义修正后 PASS）

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/code_index/pre_recon_gitnexus_track.py packages/core/tests/code_index/test_pre_recon_gitnexus_track.py
git commit -m "feat(code_index): build pre-recon deterministic track (entry/sinks/template escape)"
```

---

### Task 3: `run_agent` 对 PRE_RECON 注入 + prompt 占位符

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`（`run_agent`，PRE_RECON 注入；对齐 Plan 2 的 RECON 注入）
- Modify: `/root/shannon-py/prompts/pre-recon-code.txt`（Phase 0 区，约 :110，加 `{{PRE_RECON_GITNEXUS_TRACK}}` 占位符）
- Test: `packages/whitebox/tests/test_run_agent_pre_recon_injection.py`（Create）

**Interfaces:**
- Consumes: `build_pre_recon_gitnexus_track`（Task 2）、`executor.execute` 的 `prompt_variables` 通道
- Produces: PRE_RECON agent 跑时收到 `{{PRE_RECON_GITNEXUS_TRACK}}` 填充；非 PRE_RECON 不受影响

- [ ] **Step 1: Write the failing test**

```python
# packages/whitebox/tests/test_run_agent_pre_recon_injection.py
import json
from unittest.mock import patch

import pytest

from shannon_whitebox.pipeline import activities


@pytest.mark.asyncio
async def test_pre_recon_agent_gets_gitnexus_track(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    # Minimal code_index.json with one entry point
    (deliverables / "code_index.json").write_text(json.dumps({
        "repository": "r", "language": "python", "total_blocks": 0,
        "total_entry_points": 1, "total_chains": 0,
        "blocks": [], "edges": [], "chains": [],
        "entry_points": [{"func_block_id": "app.py:h:1", "entry_type": "http_route",
                          "route": "/api/x", "http_method": "GET", "confidence": 0.9,
                          "evidence": "router.get", "needs_llm_review": False,
                          "authentication": "public"}],
        "sink_call_sites": [],
    }))

    captured = {}

    class FakeInput:
        agent_name = "pre-recon"
        web_url = None
        repo_path = str(tmp_path)
        config_path = None
        api_key = None
        pipeline_testing_mode = False
        prompt_override = None

    async def fake_execute(**kwargs):
        captured.update(kwargs)
        return type("M", (), {"to_dict": lambda self: {}})()

    with patch.object(activities, "_get_paths", return_value=(tmp_path, deliverables, tmp_path)):
        with patch.object(activities, "executor") as mock_exec:
            mock_exec.execute = fake_execute
            await activities.run_agent(FakeInput())

    pv = captured.get("prompt_variables") or {}
    track = pv.get("pre_recon_gitnexus_track", "")
    assert "/api/x" in track
    assert "下限" in track or "独立" in track


@pytest.mark.asyncio
async def test_non_pre_recon_not_injected(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "code_index.json").write_text("{}")

    captured = {}

    class FakeInput:
        agent_name = "injection"  # non-pre-recon
        web_url = None
        repo_path = str(tmp_path)
        config_path = None
        api_key = None
        pipeline_testing_mode = False
        prompt_override = None

    async def fake_execute(**kwargs):
        captured.update(kwargs)
        return type("M", (), {"to_dict": lambda self: {}})()

    with patch.object(activities, "_get_paths", return_value=(tmp_path, deliverables, tmp_path)):
        with patch.object(activities, "executor") as mock_exec:
            mock_exec.execute = fake_execute
            await activities.run_agent(FakeInput())

    pv = captured.get("prompt_variables")
    assert pv is None or "pre_recon_gitnexus_track" not in (pv or {})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_run_agent_pre_recon_injection.py -v`
Expected: FAIL — `prompt_variables` not passed or missing key

- [ ] **Step 3: Inject for PRE_RECON in `run_agent`**

Edit `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` 的 `run_agent`。**与 Plan 2 的 RECON 注入并存**（同一 `prompt_variables` dict）。在 `executor.execute(...)` 调用前：

```python
        repo, deliverables, _ = _get_paths(input)

        # Dual-track lower bound injection.
        prompt_variables = None

        # Plan 2: framework endpoints for recon §4.2
        if input.agent_name == "recon":
            fa_path = deliverables / "framework_analysis.json"
            if fa_path.exists():
                import json
                from shannon_core.services.framework_endpoint_renderer import render_framework_endpoints
                data = json.loads(fa_path.read_text())
                endpoints = [_to_endpoint(ep) for ep in data.get("inferred_endpoints", [])]
                prompt_variables = {"framework_endpoints_summary": render_framework_endpoints(endpoints)}

        # Plan 5: deterministic track for pre-recon (entry/sinks/template escape)
        if input.agent_name == "pre-recon":
            from pathlib import Path as _Path
            from shannon_core.code_index.pre_recon_gitnexus_track import build_pre_recon_gitnexus_track
            track = build_pre_recon_gitnexus_track(_Path(str(repo)), deliverables)
            prompt_variables = {"pre_recon_gitnexus_track": track}

        metrics = await executor.execute(
            agent_name=input.agent_name,
            repo_path=str(repo),
            web_url=input.web_url,
            deliverables_path=str(deliverables),
            config_path=input.config_path,
            api_key=input.api_key,
            pipeline_testing=input.pipeline_testing_mode,
            prompt_override=input.prompt_override,
            prompt_variables=prompt_variables,
            tool_audit_logger=tool_audit_logger,
        )
```

> 注：若 Plan 2 的 RECON 注入已落地（`prompt_variables` 已构造），把 PRE_RECON 分支并到同一 `prompt_variables` 变量（两个 `if` 互斥——agent_name 不会同时是 recon 和 pre-recon）。`executor.execute` 的 `prompt_variables` 已由 executor.py:85-86 支持。

- [ ] **Step 4: Add placeholder to pre-recon-code.txt**

Edit `/root/shannon-py/prompts/pre-recon-code.txt`。在 `Phase 0: Code Index Review`（约 :110）之后、Phase 1 之前，插入：

```markdown
<pre_recon_gitnexus_track>
{{PRE_RECON_GITNEXUS_TRACK}}

**填充规则**：上表为 GitNexus + AST 确定性检测的 entry points / sinks / 模板转义。
- 这些是**确定性下限**：你的 Sink Hunter / Entry Point Mapper 必须**覆盖**这些（逐条确认 network-reachable / render context）。
- **下限非上限**：确定性未列出的 sink/entry/模板不代表不存在——仍须独立探索（glob 模板、grep 危险 API、变体目录），尤其未覆盖语言/动态调用。
- 模板转义段的 unescaped 项是**高危**，优先分析。
</pre_recon_gitnexus_track>
```

- [ ] **Step 5: Run test + verify placeholder resolves**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_run_agent_pre_recon_injection.py -v`
Expected: PASS (2 tests)

Run: `cd /root/shannon-py && python -c "
from shannon_core.prompts.manager import PromptManager
pm = PromptManager()
tpl = open('prompts/pre-recon-code.txt').read()
out = pm._interpolate(tpl, {'pre_recon_gitnexus_track': 'TEST_TRACK', 'deliverables_path': '/tmp'}, None, 'pre-recon')
assert 'TEST_TRACK' in out
assert '{{PRE_RECON_GITNEXUS_TRACK}}' not in out
print('OK: placeholder resolves')
"`
Expected: `OK: placeholder resolves`

- [ ] **Step 6: Commit**

```bash
cd /root/shannon-py
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/tests/test_run_agent_pre_recon_injection.py prompts/pre-recon-code.txt
git commit -m "feat(whitebox): inject pre-recon deterministic track (dual-track lower bound)"
```

---

### Task 4: 集成验证（落盘闭环）

**Files:**
- Test: `packages/core/tests/code_index/test_pre_recon_track_integration.py`（Create）

**Interfaces:**
- Consumes: Task 1-3
- Produces: 验证 build 读 code_index.json + 模板文件 → markdown 含 entry/sink/转义；占位符替换闭环

- [ ] **Step 1: Write the integration test**

```python
# packages/core/tests/code_index/test_pre_recon_track_integration.py
import json
from pathlib import Path

from shannon_core.code_index.pre_recon_gitnexus_track import build_pre_recon_gitnexus_track


def test_build_from_real_code_index_and_templates(tmp_path):
    """闭环：code_index.json + 模板文件 → markdown 含 entry/sink/转义。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()

    # 模板文件（unescaped）
    (repo / "v.ejs").write_text("<%- user.name %>")

    # code_index.json with entry + sink + template file_manifest
    (deliverables / "code_index.json").write_text(json.dumps({
        "repository": "r", "language": "javascript", "total_blocks": 1,
        "total_entry_points": 1, "total_chains": 0,
        "blocks": [], "edges": [], "chains": [],
        "entry_points": [{"func_block_id": "app.js:h:1", "entry_type": "http_route",
                          "route": "/render", "http_method": "GET", "confidence": 0.9,
                          "evidence": "app.get", "needs_llm_review": False, "authentication": "public"}],
        "sink_call_sites": [],
        "file_manifest": {"entries": [{"file_path": "v.ejs", "file_type": "template", "size_bytes": 20}]},
    }))

    md = build_pre_recon_gitnexus_track(repo, deliverables)
    assert "/render" in md                       # entry point
    assert "v.ejs" in md and "unescaped" in md    # template escape detected
    assert "下限" in md or "独立" in md            # disclaimer
```

- [ ] **Step 2: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_pre_recon_track_integration.py -v`
Expected: PASS

- [ ] **Step 3: Run full code_index test suite (no regression)**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/ -v`
Expected: PASS（含 Task 1/2 + 现有测试）

- [ ] **Step 4: Commit**

```bash
cd /root/shannon-py
git add packages/core/tests/code_index/test_pre_recon_track_integration.py
git commit -m "test(code_index): integration test for pre-recon track build"
```

> **手动冒烟（本 plan 外）**：跑一次白盒扫描，确认 pre-recon prompt 实际收到渲染的 track（entry/sink/模板转义），且 pre-recon LLM 据此填充 deliverable 同时独立探索清单外。

---

## Self-Review

**1. Spec coverage**（对照 spec §5.1 pre-recon）：
- LLM 轨 Phase 1/2 不变 + 不被锚定（prompt 下限声明）→ Task 3 ✓
- GitNexus 轨 entry_points（code_index）→ Task 2 ✓
- GitNexus 轨 sinks（sink_detector 产物 sink_call_sites）→ Task 2 ✓
- GitNexus 轨模板转义（补 §1.4 断路）→ Task 1（template_escape_detector）✓
- 情报合并（LLM 端，markdown 注入）→ Task 2/3 ✓（对齐 Plan 6 模式）
- 下限非上限 → Task 2/3 disclaimer ✓

**2. Placeholder scan**：无 TBD；Task 2 test fixture 注明 `SinkCallSite` 字段以实际定义为准（先 grep 确认）——诚实标注，非占位符。

**3. Type consistency**：`TemplateEscapeFinding` 字段（file_path/line/directive/escaping/engine）在 detector/render/test 一致；`build_pre_recon_gitnexus_track(repo_root, deliverables) -> str` 签名一致；`prompt_variables={"pre_recon_gitnexus_track": ...}` 键名与 `{{PRE_RECON_GITNEXUS_TRACK}}`（manager L154-157 upper-case）一致；与 Plan 2 的 `framework_endpoints_summary` 并存于 `run_agent`（互斥 if 分支）。

**已知缺口（诚实）**：
- Task 3 prompt 占位符填充靠手动冒烟验证（Step footer），单元测只验证 `_interpolate` 替换。
- `SinkCallSite` test fixture 字段需按 `parameter_models.py` 实际定义调整（Task 2 已注明）。
- 真实 code_index.json（FULL degradation）+ 真实模板文件需 GitNexus 环境，单元测用合成 JSON。
- pre-recon 情报合并在 LLM 端（非 Plan 3 文件合并），因为 pre-recon 产物是 markdown 不是 queue——同 Plan 6 recon 的设计取舍。
