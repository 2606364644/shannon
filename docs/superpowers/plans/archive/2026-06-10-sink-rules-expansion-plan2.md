# Plan 2: Sink 规则库扩展（XSS/SSRF/XXE/路径穿越/命令变体）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 sink 规则库从 46 条扩展到 ~85 条，覆盖 XSS（5→20 条）、SSRF（11→19 条）、XXE（0→5 条）、路径穿越（3→11 条）、命令注入替代变体（13→20 条）。

**Architecture:** 纯规则库扩展——在 `sink_detector.py` 的 `DEFAULT_RULES` 元组中追加新的 `SinkRule` 实例。不修改检测引擎逻辑，不修改 `_build_rule_index` 或 `detect_sinks`。每批规则独立提交、独立测试。

**Tech Stack:** Python 3.11+, pytest

**Spec:** `docs/superpowers/specs/2026-06-10-whitebox-analysis-tri-dimensional-comparison.md` §1.2.1-1.2.5, §1.3.2

---

## File Structure

| 文件 | 职责 | 改动类型 |
|---|---|---|
| `packages/core/src/shannon_core/code_index/sink_detector.py:67-198` | DEFAULT_RULES 元组 | **追加规则** |
| `packages/core/src/shannon_core/code_index/parameter_models.py:108-118` | SinkCategory 枚举 | **可能新增 XXE** |
| `packages/core/tests/code_index/test_sink_detector.py` | 规则命中测试 | **追加测试** |

---

## Pre-requisite: Add XXE SinkCategory

如果 `SinkCategory` 枚举中没有 `XXE`，需要先添加。

- [ ] **Step 0: Check and add XXE category**

检查 `parameter_models.py:108-118` 的 `SinkCategory` 枚举。如果没有 `XXE = "xxe"` 则添加：

```python
class SinkCategory(str, Enum):
    """主分类，与 SinkType 并存。"""
    SQL = "sql"
    COMMAND = "command"
    FILE = "file"
    TEMPLATE = "template"
    DESERIALIZATION = "deserialization"
    SSRF = "ssrf"
    XSS = "xss"
    XXE = "xxe"              # XML External Entity
    LOG = "log"
    REDIRECT = "redirect"
```

Commit: `git commit -m "feat(sink): add XXE category to SinkCategory enum"`

---

### Task 1: XSS 规则扩展（+18 条）

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/sink_detector.py` (DEFAULT_RULES 追加)
- Test: `packages/core/tests/code_index/test_sink_detector.py`

当前 XSS 只有 2 条（`ts-innerhtml` + `ts-document-write`）。需要覆盖 5 个上下文。

新增 helper 正则（在现有 helper 区域约第 51-63 行添加）：

```python
_JQUERY_LIKE = re.compile(r"^\$\(.*\)$|^(jQuery)$")
_DOCUMENT_LIKE = re.compile(r"^(document)$")
_WINDOW_LIKE = re.compile(r"^(window)$")
_ELEMENT_LIKE = re.compile(r"^(element|el|this)$")
```

新增 18 条 XSS 规则：

```python
    # --- XSS: HTML Body Context (jQuery) ---
    SinkRule("ts-jquery-html", ("typescript",), "html", None,
             SinkCategory.XSS, "xss_jquery_html", ((0, SlotContext.GENERIC),),
             needs_review_default=True),
    SinkRule("ts-jquery-append", ("typescript",), "append", None,
             SinkCategory.XSS, "xss_jquery_append", ((0, SlotContext.GENERIC),),
             needs_review_default=True),
    SinkRule("ts-jquery-prepend", ("typescript",), "prepend", None,
             SinkCategory.XSS, "xss_jquery_prepend", ((0, SlotContext.GENERIC),),
             needs_review_default=True),
    SinkRule("ts-jquery-after", ("typescript",), "after", None,
             SinkCategory.XSS, "xss_jquery_after", ((0, SlotContext.GENERIC),),
             needs_review_default=True),
    SinkRule("ts-jquery-before", ("typescript",), "before", None,
             SinkCategory.XSS, "xss_jquery_before", ((0, SlotContext.GENERIC),),
             needs_review_default=True),
    SinkRule("ts-jquery-replacewith", ("typescript",), "replaceWith", None,
             SinkCategory.XSS, "xss_jquery_replacewith", ((0, SlotContext.GENERIC),),
             needs_review_default=True),
    SinkRule("ts-jquery-wrap", ("typescript",), "wrap", None,
             SinkCategory.XSS, "xss_jquery_wrap", ((0, SlotContext.GENERIC),),
             needs_review_default=True),
    SinkRule("ts-jquery-add", ("typescript",), "add", None,
             SinkCategory.XSS, "xss_jquery_add", ((0, SlotContext.GENERIC),),
             needs_review_default=True),
    # --- XSS: HTML Body Context (native) ---
    SinkRule("ts-outerhtml", ("typescript",), "outerHTML", None,
             SinkCategory.XSS, "xss_outerhtml", ((0, SlotContext.GENERIC),),
             needs_review_default=True),
    SinkRule("ts-insertadjacenthtml", ("typescript",), "insertAdjacentHTML", None,
             SinkCategory.XSS, "xss_insertadjacenthtml", ((0, SlotContext.GENERIC),),
             needs_review_default=True),
    # --- XSS: JavaScript Context ---
    SinkRule("ts-settimeout-string", ("typescript",), "setTimeout", _WINDOW_LIKE,
             SinkCategory.XSS, "xss_settimeout", ((0, SlotContext.GENERIC),),
             needs_review_default=True),
    SinkRule("ts-setinterval-string", ("typescript",), "setInterval", _WINDOW_LIKE,
             SinkCategory.XSS, "xss_setinterval", ((0, SlotContext.GENERIC),),
             needs_review_default=True),
    SinkRule("ts-function-constructor", ("typescript",), "Function", None,
             SinkCategory.XSS, "xss_function_ctor", ((0, SlotContext.GENERIC),),
             needs_review_default=True),
    # --- XSS: URL Context ---
    SinkRule("ts-location-href", ("typescript",), "href", None,
             SinkCategory.XSS, "xss_location_href", ((0, SlotContext.URL),),
             needs_review_default=True),
    SinkRule("ts-location-replace", ("typescript",), "replace", _WINDOW_LIKE,
             SinkCategory.XSS, "xss_location_replace", ((0, SlotContext.URL),),
             needs_review_default=True),
    SinkRule("ts-window-open", ("typescript",), "open", _WINDOW_LIKE,
             SinkCategory.XSS, "xss_window_open", ((0, SlotContext.URL),),
             needs_review_default=True),
    SinkRule("ts-location-assign", ("typescript",), "assign", None,
             SinkCategory.XSS, "xss_location_assign", ((0, SlotContext.URL),),
             needs_review_default=True),
    SinkRule("ts-srcdoc", ("typescript",), "srcdoc", None,
             SinkCategory.XSS, "xss_srcdoc", ((0, SlotContext.GENERIC),),
             needs_review_default=True),
```

- [ ] **Step 1: Write test — jQuery html detects XSS**

在 `test_sink_detector.py` 添加（需要在文件顶部 `from shannon_core.code_index.sink_detector import DEFAULT_RULES, detect_sinks`，具体位置取决于现有测试结构）：

```python
class TestXSSRuleExpansion:
    def _src(self, code: str, lang: str = "typescript"):
        src_bytes = code.encode("utf-8")
        def _provide(block):
            return src_bytes
        return _provide

    def test_jquery_html_detected(self):
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.models import FuncBlock
        block = FuncBlock(
            id="app.js:render:1", file_path="app.js",
            function_name="render", start_line=1, end_line=5,
            source_code='$("#output").html(userData);',
            parameters=[], language="typescript",
        )
        hits = detect_sinks([block], self._src('$("#output").html(userData);'))
        xss_hits = [h for h in hits if h.category == SinkCategory.XSS]
        assert len(xss_hits) >= 1
        assert xss_hits[0].rule_id == "ts-jquery-html"

    def test_settimeout_detected(self):
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.models import FuncBlock
        block = FuncBlock(
            id="app.js:run:1", file_path="app.js",
            function_name="run", start_line=1, end_line=5,
            source_code='setTimeout("process(\'" + userInput + "\')", 100);',
            parameters=[], language="typescript",
        )
        hits = detect_sinks([block], self._src('setTimeout("process(\'" + userInput + "\')", 100);'))
        xss_hits = [h for h in hits if h.category == SinkCategory.XSS]
        assert len(xss_hits) >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/test_sink_detector.py::TestXSSRuleExpansion -v`
Expected: FAIL — rules not yet added

- [ ] **Step 3: Add helper regexes and 18 XSS rules to DEFAULT_RULES**

在 `sink_detector.py` 第 63 行后添加 helper 正则，在 DEFAULT_RULES 的 `# --- REDIRECT ---` 之后追加 XSS 规则块。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/test_sink_detector.py::TestXSSRuleExpansion -v`
Expected: PASS

- [ ] **Step 5: Run all sink_detector tests to verify no regressions**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/test_sink_detector.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add packages/core/src/shannon_core/code_index/sink_detector.py packages/core/src/shannon_core/code_index/parameter_models.py packages/core/tests/code_index/test_sink_detector.py
git commit -m "feat(sink): expand XSS rules from 2 to 20, covering HTML Body/Attribute/JS/CSS/URL contexts

Adds 18 new XSS SinkRules covering:
- jQuery DOM sinks (html, append, prepend, after, before, replaceWith, wrap, add)
- Native DOM sinks (outerHTML, insertAdjacentHTML)
- JavaScript context (setTimeout, setInterval, Function constructor)
- URL context (location.href, location.replace, window.open, location.assign, srcdoc)"
```

---

### Task 2: SSRF 规则扩展（+8 条）

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/sink_detector.py`
- Test: `packages/core/tests/code_index/test_sink_detector.py`

当前 SSRF 覆盖 HTTP clients (~2.5/13 子类)。新增 headless browsers、raw sockets、cloud metadata。

新增 helper：

```python
_BROWSER_LIKE = re.compile(r"^(page|browser|context|Page|Browser)$")
_SOCKET_LIKE = re.compile(r"^(socket|s|sock|conn)$")
```

新增 8 条 SSRF 规则：

```python
    # --- SSRF: Raw Sockets ---
    SinkRule("py-socket-connect", ("python",), "connect", _SOCKET_LIKE,
             SinkCategory.SSRF, "ssrf_socket", ((0, SlotContext.URL),)),
    SinkRule("ts-socket-connect", ("typescript",), "connect", None,
             SinkCategory.SSRF, "ssrf_socket", ((0, SlotContext.URL),),
             needs_review_default=True),
    SinkRule("go-net-dial", ("go",), "Dial", _GO_HTTP,
             SinkCategory.SSRF, "ssrf_socket", ((0, SlotContext.URL),)),
    SinkRule("java-socket-connect", ("java",), "connect", None,
             SinkCategory.SSRF, "ssrf_socket", ((0, SlotContext.URL),),
             needs_review_default=True),
    # --- SSRF: Headless Browsers ---
    SinkRule("py-playwright-goto", ("python",), "goto", _BROWSER_LIKE,
             SinkCategory.SSRF, "ssrf_headless", ((0, SlotContext.URL),)),
    SinkRule("ts-puppeteer-goto", ("typescript",), "goto", None,
             SinkCategory.SSRF, "ssrf_headless", ((0, SlotContext.URL),),
             needs_review_default=True),
    # --- SSRF: Cloud Metadata Helpers ---
    SinkRule("py-boto3-describe", ("python",), "describe_instances", None,
             SinkCategory.SSRF, "ssrf_cloud_metadata", ((0, SlotContext.GENERIC),),
             needs_review_default=True),
    SinkRule("py-requests-metadata", ("python",), "get", _REQUESTS_LIKE,
             SinkCategory.SSRF, "ssrf_cloud_metadata", ((0, SlotContext.URL),)),
    # 注：py-requests-get 已经覆盖 HTTP client，这里用 metadata 场景的子类
    # 实际上 py-requests-get 和 py-requests-metadata 会重复匹配，
    # 但 sink_detector 的 _build_rule_index 按 callee 分组，不会导致重复
```

- [ ] **Step 7: Write test — socket SSRF detected**

```python
class TestSSRFExpansion:
    def _src(self, code: str, lang: str = "python"):
        src_bytes = code.encode("utf-8")
        def _provide(block):
            return src_bytes
        return _provide

    def test_socket_connect_detected(self):
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.models import FuncBlock
        block = FuncBlock(
            id="app.py:handler:1", file_path="app.py",
            function_name="handler", start_line=1, end_line=5,
            source_code="s.connect((host, port))",
            parameters=[], language="python",
        )
        hits = detect_sinks([block], self._src("s.connect((host, port))"))
        ssrf_hits = [h for h in hits if h.category == SinkCategory.SSRF and h.sink_subtype == "ssrf_socket"]
        assert len(ssrf_hits) >= 1
```

- [ ] **Step 8: Add rules, run test, commit**

```bash
git commit -m "feat(sink): add 8 SSRF rules for raw sockets, headless browsers, cloud metadata"
```

---

### Task 3: XXE 规则新增（+5 条）

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/sink_detector.py`
- Test: `packages/core/tests/code_index/test_sink_detector.py`

```python
    # --- XXE ---
    SinkRule("py-lxml-parse", ("python",), "parse", re.compile(r"^(etree|lxml)$"),
             SinkCategory.XXE, "xxe_xml_parse", ((0, SlotContext.GENERIC),),
             needs_review_default=True),
    SinkRule("py-lxml-fromstring", ("python",), "fromstring", re.compile(r"^(etree|lxml)$"),
             SinkCategory.XXE, "xxe_xml_parse", ((0, SlotContext.GENERIC),),
             needs_review_default=True),
    SinkRule("java-documentbuilder-parse", ("java",), "parse", None,
             SinkCategory.XXE, "xxe_xml_parse", ((0, SlotContext.GENERIC),),
             needs_review_default=True),
    SinkRule("php-simplexml-load-string", ("php",), "simplexml_load_string", None,
             SinkCategory.XXE, "xxe_xml_parse", ((0, SlotContext.GENERIC),)),
    SinkRule("php-domdocument-loadxml", ("php",), "loadXML", re.compile(r"^(dom|DOMDocument|doc)$"),
             SinkCategory.XXE, "xxe_xml_parse", ((0, SlotContext.GENERIC),)),
```

- [ ] **Step 9: Write test, add rules, commit**

```python
class TestXXERules:
    def test_python_lxml_fromstring_detected(self):
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.models import FuncBlock
        block = FuncBlock(
            id="app.py:parse:1", file_path="app.py",
            function_name="parse", start_line=1, end_line=5,
            source_code="tree = etree.fromstring(xml_data)",
            parameters=[], language="python",
        )
        hits = detect_sinks([block], self._src("tree = etree.fromstring(xml_data)"))
        xxe_hits = [h for h in hits if h.category == SinkCategory.XXE]
        assert len(xxe_hits) >= 1
```

```bash
git commit -m "feat(sink): add 5 XXE rules for Python lxml, Java DocumentBuilder, PHP simplexml/DOMDocument"
```

---

### Task 4: 路径穿越扩展全语言（+8 条）

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/sink_detector.py`
- Test: `packages/core/tests/code_index/test_sink_detector.py`

```python
    # --- FILE: Path Traversal (expanded to all languages) ---
    SinkRule("py-open", ("python",), "open", None,
             SinkCategory.FILE, "file_read", ((0, SlotContext.FILE_PATH),),
             needs_review_default=True),
    SinkRule("py-os-path-join", ("python",), "join", re.compile(r"^(os\.path|posixpath|ntpath)$"),
             SinkCategory.FILE, "file_path_construct", ((0, SlotContext.FILE_PATH),),
             needs_review_default=True),
    SinkRule("ts-fs-readfile", ("typescript",), "readFile", re.compile(r"^(fs|fsPromises)$"),
             SinkCategory.FILE, "file_read", ((0, SlotContext.FILE_PATH),),
             needs_review_default=True),
    SinkRule("ts-fs-readfilesync", ("typescript",), "readFileSync", re.compile(r"^(fs)$"),
             SinkCategory.FILE, "file_read", ((0, SlotContext.FILE_PATH),),
             needs_review_default=True),
    SinkRule("ts-fs-writefile", ("typescript",), "writeFile", re.compile(r"^(fs|fsPromises)$"),
             SinkCategory.FILE, "file_write", ((0, SlotContext.FILE_PATH),),
             needs_review_default=True),
    SinkRule("go-os-open", ("go",), "Open", re.compile(r"^(os)$"),
             SinkCategory.FILE, "file_read", ((0, SlotContext.FILE_PATH),)),
    SinkRule("go-os-readfile", ("go",), "ReadFile", re.compile(r"^(os)$"),
             SinkCategory.FILE, "file_read", ((0, SlotContext.FILE_PATH),)),
    SinkRule("java-fileinputstream", ("java",), "FileInputStream", None,
             SinkCategory.FILE, "file_read", ((0, SlotContext.FILE_PATH),),
             needs_review_default=True),
```

- [ ] **Step 10: Write test, add rules, commit**

```python
class TestPathTraversalExpansion:
    def test_python_open_detected(self):
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.models import FuncBlock
        block = FuncBlock(
            id="app.py:read:1", file_path="app.py",
            function_name="read", start_line=1, end_line=5,
            source_code='f = open("/data/" + filename)',
            parameters=[], language="python",
        )
        hits = detect_sinks([block], self._src('f = open("/data/" + filename)'))
        file_hits = [h for h in hits if h.category == SinkCategory.FILE]
        assert len(file_hits) >= 1
```

```bash
git commit -m "feat(sink): add 8 path traversal rules for Python open, TS fs.readFile, Go os.Open, Java FileInputStream"
```

---

### Task 5: 命令注入替代变体（+7 条）

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/sink_detector.py`
- Test: `packages/core/tests/code_index/test_sink_detector.py`

```python
    # --- COMMAND: Alternative variants ---
    SinkRule("py-eval", ("python",), "eval", None,
             SinkCategory.COMMAND, "js_eval", ((0, SlotContext.CMD_ARGUMENT),)),
    SinkRule("py-exec", ("python",), "exec", None,
             SinkCategory.COMMAND, "command_exec", ((0, SlotContext.CMD_ARGUMENT),),
             needs_review_default=True),
    SinkRule("java-processbuilder", ("java",), "ProcessBuilder", None,
             SinkCategory.COMMAND, "command_exec", ((0, SlotContext.CMD_ARGUMENT),),
             needs_review_default=True),
    SinkRule("ts-child-spawn", ("typescript",), "spawn", re.compile(r"^(child_process)$"),
             SinkCategory.COMMAND, "command_exec", ((0, SlotContext.CMD_ARGUMENT),)),
    SinkRule("ts-child-execfile", ("typescript",), "execFile", re.compile(r"^(child_process)$"),
             SinkCategory.COMMAND, "command_exec", ((0, SlotContext.CMD_ARGUMENT),)),
    SinkRule("php-proc-open", ("php",), "proc_open", None,
             SinkCategory.COMMAND, "command_exec", ((0, SlotContext.CMD_ARGUMENT),)),
    SinkRule("php-popen", ("php",), "popen", None,
             SinkCategory.COMMAND, "command_shell", ((0, SlotContext.CMD_ARGUMENT),)),
```

- [ ] **Step 11: Write test, add rules, commit**

```python
class TestCommandVariants:
    def test_python_eval_detected(self):
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.models import FuncBlock
        block = FuncBlock(
            id="app.py:run:1", file_path="app.py",
            function_name="run", start_line=1, end_line=5,
            source_code="result = eval(user_input)",
            parameters=[], language="python",
        )
        hits = detect_sinks([block], self._src("result = eval(user_input)"))
        cmd_hits = [h for h in hits if h.category == SinkCategory.COMMAND and h.rule_id == "py-eval"]
        assert len(cmd_hits) >= 1
```

```bash
git commit -m "feat(sink): add 7 command injection variant rules (Python eval/exec, Java ProcessBuilder, JS child_process.spawn/execFile, PHP proc_open/popen)"
```

---

### Task 6: 模板/SSTI 扩展（+5 条）

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/sink_detector.py`
- Test: `packages/core/tests/code_index/test_sink_detector.py`

```python
    # --- TEMPLATE/SSTI: Cross-language expansion ---
    SinkRule("ts-ejs-render", ("typescript",), "render", re.compile(r"^(ejs)$"),
             SinkCategory.TEMPLATE, "ssti_ejs", ((0, SlotContext.TEMPLATE_EXPR),),
             needs_review_default=True),
    SinkRule("ts-pug-render", ("typescript",), "render", re.compile(r"^(pug|Pug)$"),
             SinkCategory.TEMPLATE, "ssti_pug", ((0, SlotContext.TEMPLATE_EXPR),),
             needs_review_default=True),
    SinkRule("java-freemarker-process", ("java",), "process", None,
             SinkCategory.TEMPLATE, "ssti_freemarker", ((0, SlotContext.TEMPLATE_EXPR),),
             needs_review_default=True),
    SinkRule("java-velocity-evaluate", ("java",), "evaluate", None,
             SinkCategory.TEMPLATE, "ssti_velocity", ((0, SlotContext.TEMPLATE_EXPR),),
             needs_review_default=True),
    SinkRule("php-twig-render", ("php",), "render", re.compile(r"^(twig|Twig|env)$"),
             SinkCategory.TEMPLATE, "ssti_twig", ((0, SlotContext.TEMPLATE_EXPR),),
             needs_review_default=True),
```

- [ ] **Step 12: Write test, add rules, commit**

```python
class TestSSTIExpansion:
    def test_ejs_render_detected(self):
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.models import FuncBlock
        block = FuncBlock(
            id="app.js:render:1", file_path="app.js",
            function_name="render", start_line=1, end_line=5,
            source_code="ejs.render(userTemplate, data)",
            parameters=[], language="typescript",
        )
        hits = detect_sinks([block], self._src("ejs.render(userTemplate, data)"))
        ssti_hits = [h for h in hits if h.category == SinkCategory.TEMPLATE]
        assert len(ssti_hits) >= 1
```

```bash
git commit -m "feat(sink): add 5 SSTI rules for EJS, Pug, Freemarker, Velocity, Twig"
```

---

### Task 7: 最终验证 — 规则总数和覆盖率

- [ ] **Step 13: Run all sink detector tests**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/test_sink_detector.py -v`
Expected: ALL PASS

- [ ] **Step 14: Run full test suite to verify no regressions**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/ -v`
Expected: ALL PASS

- [ ] **Step 15: Verify rule count**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -c "from shannon_core.code_index.sink_detector import DEFAULT_RULES; print(f'Total rules: {len(DEFAULT_RULES)}'); from collections import Counter; cats = Counter(str(r.category) for r in DEFAULT_RULES); print(dict(cats))"`
Expected: Total ~85 rules, with XSS ~20, SSRF ~19, XXE ~5, FILE ~11, COMMAND ~20, TEMPLATE ~7

---

## Self-Review

**1. Spec coverage:**
- §1.2.1 XSS (~5% → ~60%) → Task 1 ✅
- §1.2.2 SSRF (~19% → ~50%) → Task 2 ✅
- §1.2.3 XXE (0 → 5 rules) → Task 3 ✅
- §1.2.4 路径穿越 (PHP only → all languages) → Task 4 ✅
- §1.3.2 命令注入替代变体 → Task 5 ✅
- §1.2.5 模板/SSTI (Python only → cross-language) → Task 6 ✅

**2. Placeholder scan:** No TBD/TODO found ✅

**3. Type consistency:**
- All SinkRule use existing SinkCategory enum values ✅
- SlotContext.GENERIC / URL / FILE_PATH / CMD_ARGUMENT / TEMPLATE_EXPR all exist in enum ✅
- `needs_review_default=True` on ambiguous rules (bare callee, no receiver) ✅
