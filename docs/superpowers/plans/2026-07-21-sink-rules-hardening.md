# sink 硬规则增强(receiver_pattern 失配修复 + Java 全类别补齐)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 sink 硬规则 `receiver_pattern` 对 Java 实例变量 receiver 的系统性失配(8 条 Java 规则 0 命中的根因),并补齐 Java 全类别缺失 sink,使关轨模式下 GitNexus 确定性兜底对 Java 真实代码生效。

**Architecture:** callee 独特度三分原则(独特 callee→rp=`.+`+`needs_review` / 通用可配类名→拆语义规则 / 太通用→交探测器);`execute` 双语义(保留 `java-stmt-execute`(sql)+ 加 `java-httpclient-execute`(ssrf),双命中靠 `chain_verdict` 复核否决,DDL 不漏);别语言三处小修(php parser receiver lstrip `$` / `go-db-query` / `ts-child-process-exec`)。改动限 `data/sink_rules.yml` + `parsers/php_parser.py` + 测试,不碰 `detect_sinks` 核心逻辑、不碰 LLM 轨 prompt。

**Tech Stack:** Python 3.12 / pytest / tree-sitter(java/php/go/typescript)/ pydantic。端到端测试用 `tempfile` + `parser.parse_file` + `_src_provider` harness(对齐 `tests/code_index/test_sink_detector.py` 既有风格)。

## Global Constraints

- **铁律(CLAUDE.md §1):确定性产物不喂 LLM 轨 prompt。** 本 plan 只动 GitNexus 轨确定性层(`data/sink_rules.yml` + `parsers/php_parser.py`),**禁碰 `vuln-*.txt`**。
- **测试陷阱(CLAUDE.md §3):全套 pytest 有预存挂起/失败。** 只跑本 plan 改动的测试文件:`uv run pytest packages/core/tests/code_index/test_sink_detector.py packages/core/tests/code_index/test_rule_loader.py -v`,勿广跑全套。
- **守铁律回归**:`packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py` + `packages/whitebox/tests/test_workflow_gitnexus_failfast.py` 必须(Task 6)保持绿。
- **DEFAULT_RULES 全集锚点**:`test_sink_detector.py:218 test_rule_id_set_externalized_stable` 断言 rule_id 全集。**新增规则**(Task 1/2/3/4)必须同步把新 rule_id 追加进该测试的 `expected` set;**改 receiver_pattern**(rule_id 不变)**不影响锚点**。
- **`externally_exploitable`** 不被 verdict 覆写(本 plan 不涉及,守)。
- **commit**:conventional-commit + 中文正文,只 `git add` 该 task 的 src + test 文件。分支 `feat/fork-py`。
- **非目标**:不追求关轨追平原始 TS 全覆盖(业务逻辑缺陷/跨服务二阶结构性覆盖不了,仍需开轨)。本 plan 只把硬规则漏报从「8 条全 0 命中」修到「Java 真实 receiver 能命中」。

## File Structure

| 文件 | 责任 | 改动 |
|---|---|---|
| `data/sink_rules.yml` | sink 硬规则库 | 改 8 条 Java `receiver_pattern` + 加 10 条 Java(Deser 3 / SSRF 3 / SQL 3 / Redirect 1)+ 改 `go-db-query`/`ts-child-process-exec` rp |
| `parsers/php_parser.py` | PHP call 提取 | `member_call`/`scoped_call` receiver `lstrip("$")`(与 name 一致) |
| `tests/code_index/test_sink_detector.py` | sink 检测端到端测试 | 加 Java/别语言规则命中测试类 + 更新 `test_rule_id_set_externalized_stable` 锚点 |
| `tests/code_index/test_rule_loader.py` | 规则 fail-fast 加载测试 | 新规则 fail-fast 回归(已有用例覆盖,Task 6 确认) |

> 全路径前缀:`packages/core/src/shannon_core/code_index/`(src)与 `packages/core/tests/code_index/`(test)。下文路径均省略前缀,执行时补全。

---

## Task 1: Java SQL sink 规则(改 2 条 rp + 加 3 条)

**Files:**
- Modify: `data/sink_rules.yml`(`java-stmt-executequery`、`java-jpa-createnativequery` 改 rp;新增 `java-jpa-createquery`、`java-jdbctemplate-query`、`java-stmt-executeupdate`)
- Test: `tests/code_index/test_sink_detector.py`(新增 `TestJavaSqlSinksHardening` 类 + 更新锚点 set)

**Interfaces:**
- Consumes: `detect_sinks`(`sink_detector.py:134`)、`JavaParser`(`parsers/java_parser.py`)、`_src_provider`(`test_sink_detector.py:7` 既有 helper)、`SinkCategory`/`SlotContext`(`parameter_models`)
- Produces: `DEFAULT_RULES` 含 3 条新 SQL 规则(`java-jpa-createquery`/`java-jdbctemplate-query`/`java-stmt-executeupdate`);2 条旧规则 rp 由 `null`→`.+`、`needs_review_default: true`

- [ ] **Step 1: 写失败测试**

在 `test_sink_detector.py` 末尾追加(`_src_provider` 已在文件顶部定义,直接用):

```python
class TestJavaSqlSinksHardening:
    """Java SQL sink 规则 receiver_pattern 失配修复(治 0 命中)+ 补 createQuery/JdbcTemplate/executeUpdate。

    根因:_rule_matches 对 rp=null 只匹配裸调用(receiver is None),但 Java method call 恒为
    instance.method(),receiver 非空(stmt/em/jdbcTemplate)→ 8 条 Java 规则全 0 命中。
    改 rp='.+' + needs_review_default=true 后任意 receiver 命中。
    """

    def _java_sites(self, body: str):
        """helper:完整 class 包裹方法体 → JavaParser 切 block → detect_sinks。"""
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.parsers.java_parser import JavaParser
        import tempfile, pathlib
        src = f"class C {{\n  void q(String s) {{\n{body}\n  }}\n}}\n"
        parser = JavaParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "C.java"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        return detect_sinks(blocks, parser, source_provider=_src_provider(src))

    def test_executequery_arbitrary_receiver_hit(self):
        """stmt.executeQuery(sql) receiver=stmt → 命中 java-stmt-executequery(原 rp=null 不命中)。"""
        sites = self._java_sites("    stmt.executeQuery(sql);")
        hit = [s for s in sites if s.rule_id == "java-stmt-executequery"]
        assert hit, "stmt.executeQuery(sql) 应命中 java-stmt-executequery"
        assert hit[0].callee_name == "executeQuery"
        assert hit[0].callee_receiver == "stmt"
        assert hit[0].category == SinkCategory.SQL
        assert hit[0].needs_review is True  # rp=.+ 静态精度不足 → needs_review

    def test_createnativequery_hit(self):
        """em.createNativeQuery(sql) → java-jpa-createnativequery(原 rp=null 不命中)。"""
        sites = self._java_sites("    em.createNativeQuery(sql);")
        hit = [s for s in sites if s.rule_id == "java-jpa-createnativequery"]
        assert hit, "em.createNativeQuery(sql) 应命中 java-jpa-createnativequery"
        assert hit[0].callee_receiver == "em"

    def test_jpa_createquery_new_rule_hit(self):
        """em.createQuery(sql) → java-jpa-createquery(新增规则)。"""
        sites = self._java_sites("    em.createQuery(sql);")
        hit = [s for s in sites if s.rule_id == "java-jpa-createquery"]
        assert hit, "em.createQuery(sql) 应命中新增 java-jpa-createquery"
        assert hit[0].category == SinkCategory.SQL
        assert hit[0].needs_review is True

    def test_jdbctemplate_query_new_rule_hit(self):
        """jdbcTemplate.query(sql) → java-jdbctemplate-query(新增规则)。"""
        sites = self._java_sites("    jdbcTemplate.query(sql);")
        hit = [s for s in sites if s.rule_id == "java-jdbctemplate-query"]
        assert hit, "jdbcTemplate.query(sql) 应命中新增 java-jdbctemplate-query"

    def test_stmt_executeupdate_new_rule_hit(self):
        """stmt.executeUpdate(sql) → java-stmt-executeupdate(新增规则,JDBC DML)。"""
        sites = self._java_sites("    stmt.executeUpdate(sql);")
        hit = [s for s in sites if s.rule_id == "java-stmt-executeupdate"]
        assert hit, "stmt.executeUpdate(sql) 应命中新增 java-stmt-executeupdate"
```

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run pytest packages/core/tests/code_index/test_sink_detector.py::TestJavaSqlSinksHardening -v`
Expected: 5 FAIL —— `executeQuery`/`createNativeQuery` 因 rp=null 不命中(`hit` 为空);`createQuery`/`query`/`executeUpdate` 因规则不存在(`ImportError`/空)。

- [ ] **Step 3: 实现(改 YAML)**

在 `data/sink_rules.yml`:

3a. 改 `java-stmt-executequery`(`:52` 附近)—— `receiver_pattern: null` → `".+"`,加 `needs_review_default: true`:
```yaml
  - rule_id: java-stmt-executequery
    languages: [java]
    callee: executeQuery
    receiver_pattern: ".+"          # 原 null(只匹配裸调用,Java 不存在)→ .+ 任意 receiver
    category: sql
    sink_subtype: sql_raw
    needs_review_default: true      # rp=.+ 静态精度不足,交 chain_verdict LLM 复核
    dangerous_slots: [{arg_index: 0, slot: sql_value}]
```

3b. 改 `java-jpa-createnativequery`(`:149` 附近)—— 只改 `receiver_pattern: null` → `".+"`(它已有 `needs_review_default: true`):
```yaml
  - rule_id: java-jpa-createnativequery
    languages: [java]
    callee: createNativeQuery
    receiver_pattern: ".+"          # 原 null → .+
    category: sql
    sink_subtype: sql_raw
    needs_review_default: true
    dangerous_slots: [{arg_index: 0, slot: sql_value}]
```

3c. 在 Java SQL 规则段(`java-jpa-createnativequery` 之后)新增 3 条:
```yaml
  # Java JPA EntityManager.createQuery(HQL/JPQL)— receiver 实例变量(em/entityManager)
  - rule_id: java-jpa-createquery
    languages: [java]
    callee: createQuery
    receiver_pattern: ".+"
    category: sql
    sink_subtype: sql_raw
    needs_review_default: true
    dangerous_slots: [{arg_index: 0, slot: sql_value}]

  # Spring JdbcTemplate.query(sql)— receiver 实例变量(jdbcTemplate/jt)
  - rule_id: java-jdbctemplate-query
    languages: [java]
    callee: query
    receiver_pattern: ".+"
    category: sql
    sink_subtype: sql_raw
    needs_review_default: true      # query 通用,receiver 任意;needs_review 滤非 SQL 的 .query
    dangerous_slots: [{arg_index: 0, slot: sql_value}]

  # JDBC Statement.executeUpdate(DML: INSERT/UPDATE/DELETE)— executeUpdate 独特,几乎只 JDBC
  - rule_id: java-stmt-executeupdate
    languages: [java]
    callee: executeUpdate
    receiver_pattern: ".+"
    category: sql
    sink_subtype: sql_raw
    needs_review_default: true
    dangerous_slots: [{arg_index: 0, slot: sql_value}]
```

3d. 更新锚点:`test_sink_detector.py:225` 的 `expected` set,在 Java 段追加 3 个新 id:
```python
            "java-httpclient-send",
            "java-jpa-createquery",          # 新增(Task 1)
            "java-jpa-createnativequery", "java-objectinput-readobject",
            "java-jdbctemplate-query",       # 新增(Task 1)
            "java-runtime-exec", "java-stmt-execute", "java-stmt-executequery",
            "java-stmt-executeupdate",       # 新增(Task 1)
```
(按字母序插入,保持 set 可读;顺序不影响 set 相等。)

- [ ] **Step 4: 跑测试验证通过 + 锚点回归**

Run: `uv run pytest packages/core/tests/code_index/test_sink_detector.py::TestJavaSqlSinksHardening packages/core/tests/code_index/test_sink_detector.py::TestSinkRuleLibrary::test_rule_id_set_externalized_stable -v`
Expected: 5 新测试 PASS + 锚点 PASS。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/data/sink_rules.yml packages/core/tests/code_index/test_sink_detector.py
git commit -m "feat(code_index): Java SQL sink 规则 receiver_pattern 失配修复 + 补 createQuery/JdbcTemplate/executeUpdate

治 Java 硬规则 0 命中:_rule_matches 对 rp=null 只匹配裸调用,但 Java method call 恒有
receiver(stmt/em)→ 失配。executeQuery/createNativeQuery rp null→.+ + needs_review;
新增 createQuery/jdbctemplate-query/stmt-executeupdate(SQL 全类别)。spec §4.1/4.2。"
```

---

## Task 2: Java Deserialization 规则(改 readObject + 加 fastjson/Jackson) — 含 INJ-01 复现

**Files:**
- Modify: `data/sink_rules.yml`(`java-objectinput-readobject` 改 rp;新增 `java-fastjson-parseobject`、`java-fastjson-parsearray`、`java-jackson-readvalue`)
- Test: `tests/code_index/test_sink_detector.py`(新增 `TestJavaDeserSinksHardening` + 锚点更新)

**Interfaces:**
- Consumes: 同 Task 1(`detect_sinks`/`JavaParser`/`_src_provider`)
- Produces: 3 条新 deser 规则;`java-objectinput-readobject` rp `null`→`.+`

- [ ] **Step 1: 写失败测试**

追加到 `test_sink_detector.py`:

```python
class TestJavaDeserSinksHardening:
    """Java deser sink:fastjson/Jackson 补召回 + readObject rp 失配修复。

    复现原始版 INJ-01:ClusterConfigController.apiModifyClusterConfig 的
    JSON.parseObject(payload)(fastjson autotype,RCE 级)—— 重构版硬规则 0 命中根因之一。
    """

    def _java_sites(self, body: str):
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.parsers.java_parser import JavaParser
        import tempfile, pathlib
        src = f"class C {{\n  void q(String p) {{\n{body}\n  }}\n}}\n"
        parser = JavaParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "C.java"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        return detect_sinks(blocks, parser, source_provider=_src_provider(src))

    def test_fastjson_parseobject_inj01_repro(self):
        """JSON.parseObject(payload) → java-fastjson-parseobject(复现原始版 INJ-01)。"""
        sites = self._java_sites("    JSON.parseObject(payload);")
        hit = [s for s in sites if s.rule_id == "java-fastjson-parseobject"]
        assert hit, "JSON.parseObject(payload) 应命中 java-fastjson-parseobject"
        assert hit[0].callee_name == "parseObject"
        assert hit[0].callee_receiver == "JSON"  # 静态调用,object 字段 = JSON
        assert hit[0].category == SinkCategory.DESERIALIZATION
        assert hit[0].needs_review is True

    def test_fastjson_parsearray_hit(self):
        """JSON.parseArray(payload) → java-fastjson-parsearray。"""
        sites = self._java_sites("    JSON.parseArray(payload);")
        hit = [s for s in sites if s.rule_id == "java-fastjson-parsearray"]
        assert hit

    def test_jackson_readvalue_hit(self):
        """objectMapper.readValue(payload, Class) → java-jackson-readvalue。"""
        sites = self._java_sites("    objectMapper.readValue(payload, Object.class);")
        hit = [s for s in sites if s.rule_id == "java-jackson-readvalue"]
        assert hit
        assert hit[0].category == SinkCategory.DESERIALIZATION

    def test_readobject_arbitrary_receiver_hit(self):
        """ois.readObject(payload) → java-objectinput-readobject(原 rp=null 不命中)。"""
        sites = self._java_sites("    ois.readObject(payload);")
        hit = [s for s in sites if s.rule_id == "java-objectinput-readobject"]
        assert hit, "ois.readObject(payload) 应命中 java-objectinput-readobject(receiver=ois)"
        assert hit[0].callee_receiver == "ois"
```

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run pytest packages/core/tests/code_index/test_sink_detector.py::TestJavaDeserSinksHardening -v`
Expected: 4 FAIL —— `parseObject`/`parseArray`/`readValue` 规则不存在;`readObject` rp=null 不命中。

- [ ] **Step 3: 实现(改 YAML)**

3a. 改 `java-objectinput-readobject`(`:323` 附近)—— `receiver_pattern: null` → `".+"`(已有 `needs_review_default: true`):
```yaml
  - rule_id: java-objectinput-readobject
    languages: [java]
    callee: readObject
    receiver_pattern: ".+"          # 原 null → .+
    category: deserialization
    sink_subtype: deser_java
    needs_review_default: true
    dangerous_slots: [{arg_index: 0, slot: deserialize}]
```

3b. 在 Deserialization 段(`java-objectinput-readobject` 之后)新增 3 条:
```yaml
  # fastjson JSON.parseObject / parseArray(autotype RCE 风险;receiver=JSON 静态调用或实例)
  - rule_id: java-fastjson-parseobject
    languages: [java]
    callee: parseObject
    receiver_pattern: ".+"
    category: deserialization
    sink_subtype: deser_fastjson
    needs_review_default: true
    dangerous_slots: [{arg_index: 0, slot: deserialize}]

  - rule_id: java-fastjson-parsearray
    languages: [java]
    callee: parseArray
    receiver_pattern: ".+"
    category: deserialization
    sink_subtype: deser_fastjson
    needs_review_default: true
    dangerous_slots: [{arg_index: 0, slot: deserialize}]

  # Jackson ObjectMapper.readValue(enableDefaultTyping RCE 风险)
  - rule_id: java-jackson-readvalue
    languages: [java]
    callee: readValue
    receiver_pattern: ".+"
    category: deserialization
    sink_subtype: deser_jackson
    needs_review_default: true
    dangerous_slots: [{arg_index: 0, slot: deserialize}]
```

> 注:`sink_subtype` 是自由字符串(`parameter_models.py:159` `sink_subtype: str`;`_build_sink_rules` 只对 `category`/`slot` fail-fast,**不校验 subtype**)。`deser_fastjson`/`deser_jackson` 直接用,无需补枚举。

3c. 更新锚点 `expected` set,追加 3 个新 id:
```python
            "java-fastjson-parsearray",     # 新增(Task 2)
            "java-fastjson-parseobject",     # 新增(Task 2)
            "java-httpclient-send",
            "java-jackson-readvalue",        # 新增(Task 2)
            "java-jpa-createquery",
```

- [ ] **Step 4: 跑测试验证通过 + 锚点 + fail-fast 回归**

Run: `uv run pytest packages/core/tests/code_index/test_sink_detector.py::TestJavaDeserSinksHardening packages/core/tests/code_index/test_sink_detector.py::TestSinkRuleLibrary::test_rule_id_set_externalized_stable packages/core/tests/code_index/test_rule_loader.py -v`
Expected: 4 新测试 + 锚点 + fail-fast 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/data/sink_rules.yml packages/core/tests/code_index/test_sink_detector.py
# 若补了 sink_subtype 枚举,一并 add parameter_models.py
git commit -m "feat(code_index): Java deser sink 补 fastjson/Jackson + readObject rp 修复(复现 INJ-01)

新增 fastjson parseObject/parseArray(autotype)+ Jackson readValue;readObject rp
null→.+。JSON.parseObject(payload) 命中闭环原始版 INJ-01(ClusterConfigController)。
spec §4.2 Deserialization。"
```

---

## Task 3: Java Command/SSRF/Redirect 规则(改 4 条 rp + 加 3 条)

**Files:**
- Modify: `data/sink_rules.yml`(`java-runtime-exec`/`java-httpclient-send`/`java-resttemplate-getforobject`/`java-resttemplate-exchange` 改 rp;新增 `java-resttemplate-postforentity`、`java-url-openconnection`、`java-response-sendredirect`)
- Test: `tests/code_index/test_sink_detector.py`(新增 `TestJavaSsrfCmdRedirectSinks` + 锚点更新)

**Interfaces:**
- Consumes: 同 Task 1
- Produces: 3 条新规则;4 条旧规则 rp `^(类型名)$`→`.+`

- [ ] **Step 1: 写失败测试**

追加到 `test_sink_detector.py`:

```python
class TestJavaSsrfCmdRedirectSinks:
    """Java SSRF/Command/Redirect 规则:rp ^(类型名)\$ 失配修复(Java receiver 是实例变量/整链)+ 补全。"""

    def _java_sites(self, body: str):
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.parsers.java_parser import JavaParser
        import tempfile, pathlib
        src = f"class C {{\n  void q() {{\n{body}\n  }}\n}}\n"
        parser = JavaParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "C.java"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        return detect_sinks(blocks, parser, source_provider=_src_provider(src))

    def test_runtime_exec_chain_receiver_hit(self):
        """Runtime.getRuntime().exec(cmd) receiver=整链 'Runtime.getRuntime()' → java-runtime-exec。
        原 rp ^(Runtime|getRuntime)\$ 不匹配整链;改 .+ 后命中。"""
        sites = self._java_sites("    Runtime.getRuntime().exec(cmd);")
        hit = [s for s in sites if s.rule_id == "java-runtime-exec"]
        assert hit, "Runtime.getRuntime().exec(cmd) 应命中 java-runtime-exec(整链 receiver)"
        assert hit[0].category == SinkCategory.COMMAND

    def test_resttemplate_getforobject_hit(self):
        """restTemplate.getForObject(url) → java-resttemplate-getforobject(原 rp 不匹配实例变量)。"""
        sites = self._java_sites("    restTemplate.getForObject(url);")
        hit = [s for s in sites if s.rule_id == "java-resttemplate-getforobject"]
        assert hit
        assert hit[0].category == SinkCategory.SSRF

    def test_resttemplate_exchange_hit(self):
        """restTemplate.exchange(url) → java-resttemplate-exchange。"""
        sites = self._java_sites("    restTemplate.exchange(url);")
        hit = [s for s in sites if s.rule_id == "java-resttemplate-exchange"]
        assert hit

    def test_resttemplate_postforentity_new_rule_hit(self):
        """restTemplate.postForEntity(url, body) → java-resttemplate-postforentity(新增)。"""
        sites = self._java_sites("    restTemplate.postForEntity(url, body);")
        hit = [s for s in sites if s.rule_id == "java-resttemplate-postforentity"]
        assert hit
        assert hit[0].category == SinkCategory.SSRF

    def test_url_openconnection_new_rule_hit(self):
        """new URL(x).openConnection() → java-url-openconnection(新增)。"""
        sites = self._java_sites("    new URL(x).openConnection();")
        hit = [s for s in sites if s.rule_id == "java-url-openconnection"]
        assert hit
        assert hit[0].category == SinkCategory.SSRF

    def test_response_sendredirect_new_rule_hit(self):
        """response.sendRedirect(url) → java-response-sendredirect(新增)。"""
        sites = self._java_sites("    response.sendRedirect(url);")
        hit = [s for s in sites if s.rule_id == "java-response-sendredirect"]
        assert hit
        assert hit[0].category == SinkCategory.REDIRECT
```

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run pytest packages/core/tests/code_index/test_sink_detector.py::TestJavaSsrfCmdRedirectSinks -v`
Expected: 6 FAIL —— 4 条改 rp 规则因 `^(类型名)$` 不匹配实例变量/整链不命中;3 条新规则不存在。

- [ ] **Step 3: 实现(改 YAML)**

3a. 改 4 条 rp(`java-runtime-exec` `:249`、`java-httpclient-send` `:398`、`java-resttemplate-getforobject` `:507`、`java-resttemplate-exchange` `:516`)—— `receiver_pattern: "^(...)$"` → `".+"`(均已有 `needs_review_default: true`)。例:
```yaml
  - rule_id: java-runtime-exec
    languages: [java]
    callee: exec
    receiver_pattern: ".+"          # 原 ^(Runtime|getRuntime)$ 不匹配整链 Runtime.getRuntime()
    category: command
    sink_subtype: command_exec
    needs_review_default: true
    dangerous_slots: [{arg_index: 0, slot: cmd_argument}]
```
（`java-httpclient-send`/`java-resttemplate-getforobject`/`java-resttemplate-exchange` 同样只把 `receiver_pattern` 行改成 `".+"`,其余字段不动。）

3b. 在 SSRF 段新增 2 条 + Redirect 段新增 1 条:
```yaml
  # Spring RestTemplate.postForEntity(SSRF)
  - rule_id: java-resttemplate-postforentity
    languages: [java]
    callee: postForEntity
    receiver_pattern: ".+"
    category: ssrf
    sink_subtype: ssrf_resttemplate
    needs_review_default: true
    dangerous_slots: [{arg_index: 0, slot: url}]

  # java.net.URL.openConnection(SSRF)
  - rule_id: java-url-openconnection
    languages: [java]
    callee: openConnection
    receiver_pattern: ".+"
    category: ssrf
    sink_subtype: ssrf_java_url
    needs_review_default: true
    dangerous_slots: [{arg_index: 0, slot: url}]
```
```yaml
  # HttpServletResponse.sendRedirect(open redirect)
  - rule_id: java-response-sendredirect
    languages: [java]
    callee: sendRedirect
    receiver_pattern: ".+"
    category: redirect
    sink_subtype: open_redirect
    needs_review_default: true
    dangerous_slots: [{arg_index: 0, slot: url}]
```

> 注:`sink_subtype` 自由字符串不校验(见 Task 2 注);`ssrf_java_url` 直接用,`open_redirect`/`ssrf_resttemplate` 既有。

3c. 更新锚点 `expected` set,追加 3 个新 id:
```python
            "java-resttemplate-exchange", "java-resttemplate-getforobject",
            "java-resttemplate-postforentity",  # 新增(Task 3)
            "java-response-sendredirect",         # 新增(Task 3)
            ...
            "java-url-openconnection",            # 新增(Task 3)
```

- [ ] **Step 4: 跑测试验证通过 + 锚点**

Run: `uv run pytest packages/core/tests/code_index/test_sink_detector.py::TestJavaSsrfCmdRedirectSinks packages/core/tests/code_index/test_sink_detector.py::TestSinkRuleLibrary::test_rule_id_set_externalized_stable packages/core/tests/code_index/test_rule_loader.py -v`
Expected: 6 新测试 + 锚点 + fail-fast 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/data/sink_rules.yml packages/core/tests/code_index/test_sink_detector.py
git commit -m "feat(code_index): Java SSRF/Command/Redirect 规则 rp 失配修复 + 补 postForEntity/openConnection/sendRedirect

runtime-exec/httpclient-send/resttemplate-* rp ^(类型名)\$→.+ (Java receiver 是实例变量/
整链);新增 postForEntity/url-openconnection/response-sendredirect。spec §4.1/4.2。"
```

---

## Task 4: execute 双语义(java-stmt-execute 保留改 rp + java-httpclient-execute 新增 + 双命中/DDL 测试)

**Files:**
- Modify: `data/sink_rules.yml`(`java-stmt-execute` 改 rp 保留;新增 `java-httpclient-execute`)
- Test: `tests/code_index/test_sink_detector.py`(新增 `TestExecuteDualSemantics` + 锚点更新)

**Interfaces:**
- Consumes: 同 Task 1
- Produces: `java-stmt-execute` rp `null`→`.+`(category=sql 保留);新 `java-httpclient-execute`(execute, ssrf)

**编排不变量:** `execute` 同名横跨 SQL(`Statement.execute`)与 SSRF(`httpClient.execute`),保留两条规则、靠 `chain_verdict` 复核否决误判(本 task 只验证产正确的 SinkCallSite,不改 `chain_verdict`)。

- [ ] **Step 1: 写失败测试**

追加到 `test_sink_detector.py`:

```python
class TestExecuteDualSemantics:
    """execute 双语义:Statement.execute(SQL)+ httpClient.execute(SSRF),callee 同名无法消歧。

    保留 java-stmt-execute(sql)+ 加 java-httpclient-execute(ssrf),双命中靠 chain_verdict
    复核否决 SQL 那条(下游职责,本测试只验证 detect_sinks 产两个 SinkCallSite)。DDL 不漏。
    """

    def _java_sites(self, body: str):
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.parsers.java_parser import JavaParser
        import tempfile, pathlib
        src = f"class C {{\n  void q() {{\n{body}\n  }}\n}}\n"
        parser = JavaParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "C.java"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        return detect_sinks(blocks, parser, source_provider=_src_provider(src))

    def test_statement_execute_ddl_not_missed(self):
        """stmt.execute('DROP TABLE') → java-stmt-execute(SQL)命中。DDL 场景只有 execute 覆盖,不能漏。"""
        sites = self._java_sites('    stmt.execute("DROP TABLE x");')
        sql = [s for s in sites if s.rule_id == "java-stmt-execute"]
        assert sql, "stmt.execute(DDL) 应命中 java-stmt-execute(不漏 DDL)"
        assert sql[0].category == SinkCategory.SQL

    def test_httpclient_execute_dual_hit(self):
        """httpClient.execute(request) → java-httpclient-execute(ssrf) + java-stmt-execute(sql)双命中。
        callee=execute 同名;两条规则都 rp='.+' → 各产一个 SinkCallSite。chain_verdict 后续否决 SQL 那条。"""
        sites = self._java_sites("    httpClient.execute(request);")
        rule_ids = {s.rule_id for s in sites}
        assert "java-httpclient-execute" in rule_ids, "httpClient.execute 应命中 java-httpclient-exchange(ssrf)"
        assert "java-stmt-execute" in rule_ids, "httpClient.execute 也命中 java-stmt-execute(sql,待 chain_verdict 否决)"
        ssrf = [s for s in sites if s.rule_id == "java-httpclient-execute"]
        assert ssrf[0].category == SinkCategory.SSRF
        assert ssrf[0].callee_receiver == "httpClient"
```

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run pytest packages/core/tests/code_index/test_sink_detector.py::TestExecuteDualSemantics -v`
Expected: 2 FAIL —— `stmt.execute` 因 `java-stmt-execute` rp=null 不命中;`httpClient.execute` 因 `java-httpclient-execute` 不存在。

- [ ] **Step 3: 实现(改 YAML)**

3a. 改 `java-stmt-execute`(`:61` 附近)—— `receiver_pattern: null` → `".+"`,加 `needs_review_default: true`(category=sql 保留,**不删**):
```yaml
  - rule_id: java-stmt-execute
    languages: [java]
    callee: execute
    receiver_pattern: ".+"          # 原 null(只匹配裸调用)→ .+ ;execute 横跨 SQL+SSRF,
    category: sql                   #   保留 sql 语义,SSRF 由 java-httpclient-execute 覆盖,
    sink_subtype: sql_raw           #   双命中靠 chain_verdict 复核否决(不漏 DDL)
    needs_review_default: true
    dangerous_slots: [{arg_index: 0, slot: sql_value}]
```

3b. 在 SSRF 段新增 `java-httpclient-execute`(覆盖 `httpClient.execute`,接住 execute 的 SSRF 语义):
```yaml
  # Apache HttpClient.execute(SSRF)— execute 同名横跨 SQL(java-stmt-execute),
  # 本规则标 ssrf 语义;双命中靠 chain_verdict 复核(receiver=httpClient + HttpGet 参数)否决 SQL 那条。
  - rule_id: java-httpclient-execute
    languages: [java]
    callee: execute
    receiver_pattern: ".+"
    category: ssrf
    sink_subtype: ssrf_java_http
    needs_review_default: true
    dangerous_slots: [{arg_index: 0, slot: url}]
```

3c. 更新锚点 `expected` set,追加 1 个新 id(`java-stmt-execute` 已在锚点,改 rp 不影响):
```python
            "java-httpclient-execute",      # 新增(Task 4)
            "java-httpclient-send",
```

- [ ] **Step 4: 跑测试验证通过 + 锚点**

Run: `uv run pytest packages/core/tests/code_index/test_sink_detector.py::TestExecuteDualSemantics packages/core/tests/code_index/test_sink_detector.py::TestSinkRuleLibrary::test_rule_id_set_externalized_stable -v`
Expected: 2 新测试 + 锚点 PASS。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/data/sink_rules.yml packages/core/tests/code_index/test_sink_detector.py
git commit -m "feat(code_index): execute 双语义规则(保留 stmt-execute SQL + 加 httpclient-execute SSRF)

execute 同名横跨 SQL(Statement.execute)/SSRF(httpClient.execute);保留 java-stmt-execute
(sql, rp .+ )不漏 DDL + 新增 java-httpclient-execute(ssrf)。双命中靠 chain_verdict 复核
否决 SQL 那条。spec §4.3 关键决策。"
```

---

## Task 5: 别语言(php parser receiver lstrip `$` + go-db-query + ts-child-process-exec)

**Files:**
- Modify: `parsers/php_parser.py`(`destructure_call` 的 `member_call`/`scoped_call` receiver lstrip `$`)
- Modify: `data/sink_rules.yml`(`go-db-query` rp null→`.+`;`ts-child-process-exec` rp null→`^(child_process|cp)$`)
- Test: `tests/code_index/test_sink_detector.py`(新增 `TestOtherLangsReceiverFix`)

**Interfaces:**
- Consumes: `PhpParser`/`GoParser`/`TypeScriptParser` + `detect_sinks`
- Produces: php member/scoped call receiver 与 name 一致去 `$`;`go-db-query`/`ts-child-process-exec` 命中真实 receiver

- [ ] **Step 1: 写失败测试**

追加到 `test_sink_detector.py`:

```python
class TestOtherLangsReceiverFix:
    """别语言 receiver 失配小修:php \$ 前缀 / go-db-query / ts-child-process-exec。"""

    def test_php_mysqli_query_dollar_receiver_hit(self):
        """$mysqli->query($sql) receiver='$mysqli' → php_parser lstrip \$ → 'mysqli' → 命中 php-mysqli-query。"""
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.parsers.php_parser import PhpParser
        import tempfile, pathlib
        src = "<?php\nfunction f($sql) {\n  $mysqli->query($sql);\n}\n"
        parser = PhpParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "a.php"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        hit = [s for s in sites if s.rule_id == "php-mysqli-query"]
        assert hit, "$mysqli->query($sql) 应命中 php-mysqli-query(receiver lstrip $ → mysqli)"
        assert hit[0].callee_receiver == "mysqli"

    def test_go_db_query_receiver_hit(self):
        """db.Query(sql) → go-db-query(原 rp=null 不命中;改 .+ 后命中)。"""
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.parsers.go_parser import GoParser
        import tempfile, pathlib
        src = "package main\nfunc q(db DB, sql string) {\n  db.Query(sql)\n}\n"
        parser = GoParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "a.go"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        hit = [s for s in sites if s.rule_id == "go-db-query"]
        assert hit, "db.Query(sql) 应命中 go-db-query(rp .+ 后)"

    def test_ts_child_process_exec_receiver_hit(self):
        """child_process.exec(cmd) → ts-child-process-exec(原 rp=null 不命中;改 ^(child_process|cp)$ 后)。"""
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.parsers.typescript_parser import TypeScriptParser
        import tempfile, pathlib
        src = "import * as cp from 'child_process';\nfunction f(cmd: string) {\n  cp.exec(cmd);\n}\n"
        parser = TypeScriptParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "a.ts"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        hit = [s for s in sites if s.rule_id == "ts-child-process-exec"]
        assert hit, "cp.exec(cmd) 应命中 ts-child-process-exec(rp ^(child_process|cp)$)"
```

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run pytest packages/core/tests/code_index/test_sink_detector.py::TestOtherLangsReceiverFix -v`
Expected: 3 FAIL —— php receiver 带 `$` 不匹配;`go-db-query`/`ts-child-process-exec` rp=null 不命中。

- [ ] **Step 3: 实现**

3a. `parsers/php_parser.py` 的 `destructure_call`(`member_call_expression` 与 `scoped_call_expression` 分支)—— receiver 也 `lstrip("$")`(与 name 一致):
```python
        if node.type == "member_call_expression":
            name_node = node.child_by_field_name("name")
            obj = node.child_by_field_name("object")
            callee = name_node.text.decode("utf-8").lstrip("$") if name_node else ""
            receiver = obj.text.decode("utf-8").lstrip("$") if obj else None   # 与 name 一致去 $
            return (callee, receiver)
        if node.type == "scoped_call_expression":
            name_node = node.child_by_field_name("name")
            scope = node.child_by_field_name("scope")
            callee = name_node.text.decode("utf-8").lstrip("$") if name_node else ""
            receiver = scope.text.decode("utf-8").lstrip("$") if scope else None   # 与 name 一致去 $
```
（`function_call_expression` 分支 receiver 恒 None,不动。）

3b. `data/sink_rules.yml` 改 `go-db-query`(`:43` 附近)—— `receiver_pattern: null` → `".+"` + `needs_review_default: true`:
```yaml
  - rule_id: go-db-query
    languages: [go]
    callee: Query
    receiver_pattern: ".+"          # 原 null(只匹配裸 Query)→ .+ 覆盖 db.Query
    category: sql
    sink_subtype: sql_raw
    needs_review_default: true
    dangerous_slots: [{arg_index: 0, slot: sql_value}]
```

3c. 改 `ts-child-process-exec`(`:232` 附近)—— `receiver_pattern: null` → `"^(child_process|cp)$"`:
```yaml
  - rule_id: ts-child-process-exec
    languages: [typescript]
    callee: exec
    receiver_pattern: "^(child_process|cp)$"   # 原 null;惯例 import 名 child_process/cp
    category: command
    sink_subtype: command_exec
    needs_review_default: true
    dangerous_slots: [{arg_index: 0, slot: cmd_argument}]
```

（Task 5 不新增规则,锚点 set 无需改。）

- [ ] **Step 4: 跑测试验证通过 + php 回归**

Run: `uv run pytest packages/core/tests/code_index/test_sink_detector.py::TestOtherLangsReceiverFix packages/core/tests/code_index/test_sink_detector.py -v -k "php or Php or go_db or child_process"`
Expected: 3 新测试 PASS;既有 php 测试不破(receiver 去 `$` 对 `^(mysqli|pdo|db|DB)$` 是修正,无规则依赖带 `$`)。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/parsers/php_parser.py packages/core/src/shannon_core/code_index/data/sink_rules.yml packages/core/tests/code_index/test_sink_detector.py
git commit -m "fix(code_index): 别语言 receiver 失配小修(php lstrip \$ / go-db-query / ts-child-process-exec)

php_parser member/scoped call receiver 与 name 一致 lstrip \$(\$mysqli→mysqli 命中);
go-db-query rp null→.+;ts-child-process-exec rp null→^(child_process|cp)\$。spec §5。"
```

---

## Task 6: 守铁律回归 + 全锚点确认 + 真机验证(后置)

**Files:**
- 无源码改动(回归 + 记录真机步骤)

- [ ] **Step 1: 守铁律回归**

Run:
```
uv run pytest packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py -v
uv run pytest packages/whitebox/tests/test_workflow_gitnexus_failfast.py -v
```
Expected: 全 PASS。铁律(确定性产物不喂 LLM 轨)不破。

- [ ] **Step 2: sink_detector 全量回归 + 锚点最终确认**

Run:
```
uv run pytest packages/core/tests/code_index/test_sink_detector.py packages/core/tests/code_index/test_rule_loader.py -v
```
Expected: 全 PASS。`test_rule_id_set_externalized_stable` 的 `expected` set 含 Task 1-4 新增的 10 条(共原 56 + 补充 6 + 新 10 = 72 条,以实际为准)。

- [ ] **Step 3: 真机验证(关轨重扫 sentinel_dashboard,后置——依赖 sink 探测器落地)**

```bash
# 1. 关轨重扫(改后)
SHANNON_LLM_TRACK_ENABLED=0 uv run shannon-whitebox start --repo /root/shannon-py/repos/frontend/sentinel_dashboard

# 2. 验收点:code_index.json 的 sink_call_sites 改前(3 个空壳 llm-sink-hunter,callee 空)
#    → 改后(含 java-fastjson-parseobject / java-httpclient-execute 等硬规则 sink,
#      callee_name/dangerous_slots 非空);injection_gitnexus_queue.json 从不存在 → 非空
ls -la workspaces/sentinel_dashboard_*/deliverables/whitebox/code_index.json
python3 -c "import json,glob; d=json.load(open(sorted(glob.glob('workspaces/sentinel_dashboard_*'))[-1]+'/deliverables/whitebox/code_index.json')); from collections import Counter; print(Counter(s['rule_id'] for s in d['sink_call_sites']))"
```
Expected: `sink_call_sites` 的 rule_id 分布含 `java-fastjson-parseobject`/`java-httpclient-execute` 等硬规则 sink(改前只有 `llm-sink-hunter`)。

- [ ] **Step 4: 验收记录**

在 `docs/superpowers/specs/2026-07-21-sink-rules-hardening-design.md` §9 后补真机结果(改前/改后 sink_call_sites rule_id 分布对比、injection_gitnexus_queue 非空)。

- [ ] **Step 5: Commit(若有文档更新)**

```bash
git add docs/superpowers/specs/2026-07-21-sink-rules-hardening-design.md
git commit -m "docs(spec): sink 硬规则增强真机验收结果"
```

---

## Self-Review

**1. Spec coverage:**
- §4.1 改 8 条 Java rp → Task 1(executequery/createnativequery)+ Task 2(readobject)+ Task 3(runtime-exec/httpclient-send/resttemplate-* )+ Task 4(java-stmt-execute)✓ 全覆盖
- §4.2 加 10 条 Java → Task 1(3 SQL)+ Task 2(3 Deser)+ Task 3(3 SSRF/Redirect)+ Task 4(1 httpclient-execute)= 10 ✓
- §4.3 execute 双语义 → Task 4 ✓
- §4.4 不加(YAGNI)→ Global Constraints 非目标 ✓
- §5 别语言 → Task 5 ✓
- §6 测试策略 → 各 task Step 1 + Task 6 回归 ✓
- §7 scope 边界 → Global Constraints ✓
- 锚点更新 → Task 1/2/3/4 各自 Step 3d/3c ✓

**2. Placeholder scan:**
- Task 2/3 的 `sink_subtype`(`deser_fastjson`/`deser_jackson`/`ssrf_java_url`)已确认自由字符串不校验(`parameter_models.py:159`,`_build_sink_rules` 不校验 subtype),直接用新值,无 fail-fast 风险。
- Task 6 Step 2 锚点数「72 条,以实际为准」——因补充段已有 6 条 + 本 plan 10 条,精确数需跑后确认;非占位符(测试本身是断言)。

**3. Type consistency:**
- `_java_sites`/`_src_provider` helper 在各 Test 类一致;`SinkCategory.SQL`/`DESERIALIZATION`/`SSRF`/`COMMAND`/`REDIRECT` 枚举值与 `parameter_models.py:TestSinkCategory`(test_sink_detector.py:28)一致。
- 新 rule_id 命名一致(`java-<lib>-<method>`),与既有 `java-resttemplate-getforobject` 风格对齐。
- receiver lstrip `$` 对 php `member_call`/`scoped_call` 两分支一致。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-21-sink-rules-hardening.md`.

**依赖图:** Task 1/2/3/4/5 相互独立(改不同规则/parser),可并行;Task 6 最后(回归 + 真机)。
**锚点耦合:** Task 1/2/3/4 各自更新 `test_rule_id_set_externalized_stable` 的 `expected` set(追加本 task 新 id);若并行执行需合并锚点更新(建议串行 1→2→3→4 避免锚点 set 冲突)。

**两种执行方式:**

**1. Subagent-Driven(推荐)** — 每个 task 派新 subagent,task 间 review,快迭代。适合本 plan(task 独立性强)。

**2. Inline Execution** — 本会话内用 executing-plans 批量执行 + 检查点 review。

**选哪种?**
