"""Tests for sink_detector module and SinkCallSite model."""
from supernova_core.code_index.parameter_models import (
    SinkCallSite, DangerousSlot, SlotContext, SinkCategory, SinkType,
)


def _src_provider(src: str):
    """Return a source_provider closure that always returns the same bytes."""
    src_bytes = src.encode("utf-8")
    def _provide(block):
        return src_bytes
    return _provide


class TestSlotContext:
    def test_values(self):
        assert SlotContext.SQL_VALUE == "sql_value"
        assert SlotContext.SQL_IDENTIFIER == "sql_identifier"
        assert SlotContext.CMD_ARGUMENT == "cmd_argument"
        assert SlotContext.FILE_PATH == "file_path"
        assert SlotContext.TEMPLATE_EXPR == "template_expr"
        assert SlotContext.URL == "url"
        assert SlotContext.DESERIALIZE_OBJ == "deserialize"
        assert SlotContext.GENERIC == "generic"


class TestSinkCategory:
    def test_values(self):
        assert SinkCategory.SQL == "sql"
        assert SinkCategory.COMMAND == "command"
        assert SinkCategory.FILE == "file"
        assert SinkCategory.TEMPLATE == "template"
        assert SinkCategory.DESERIALIZATION == "deserialization"
        assert SinkCategory.SSRF == "ssrf"
        assert SinkCategory.XSS == "xss"
        assert SinkCategory.LOG == "log"
        assert SinkCategory.REDIRECT == "redirect"


class TestDangerousSlot:
    def test_basic(self):
        slot = DangerousSlot(
            arg_index=0,
            slot=SlotContext.SQL_VALUE,
            expression="user_sql",
            is_entry_hint=False,
        )
        assert slot.arg_index == 0
        assert slot.slot == SlotContext.SQL_VALUE
        assert slot.expression == "user_sql"
        assert slot.is_entry_hint is False

    def test_variadic_index(self):
        slot = DangerousSlot(
            arg_index=-1,
            slot=SlotContext.CMD_ARGUMENT,
            expression="*args",
            is_entry_hint=False,
        )
        assert slot.arg_index == -1


class TestSinkCallSite:
    def test_basic(self):
        site = SinkCallSite(
            id="app.py:handler:execute:5:8",
            caller_id="app.py:handler:1",
            callee_name="execute",
            callee_receiver="cursor",
            category=SinkCategory.SQL,
            sink_subtype="sql_raw",
            file_path="app.py",
            line=5,
            column=8,
            dangerous_slots=[
                DangerousSlot(
                    arg_index=0,
                    slot=SlotContext.SQL_VALUE,
                    expression="user_sql",
                    is_entry_hint=False,
                ),
            ],
            rule_id="py-db-cursor-execute",
        )
        assert site.id == "app.py:handler:execute:5:8"
        assert site.callee_name == "execute"
        assert site.callee_receiver == "cursor"
        assert site.category == SinkCategory.SQL
        assert site.needs_review is False  # default
        assert len(site.dangerous_slots) == 1

    def test_needs_review_default_false(self):
        site = SinkCallSite(
            id="a:b:c:1:0",
            caller_id="a:b:1",
            callee_name="c",
            callee_receiver=None,
            category=SinkCategory.XSS,
            sink_subtype="xss_dom",
            file_path="a",
            line=1,
            column=0,
            dangerous_slots=[],
            rule_id="ts-innerhtml",
        )
        assert site.needs_review is False

    def test_serialization_roundtrip(self):
        site = SinkCallSite(
            id="a.py:foo:bar:1:0",
            caller_id="a.py:foo:1",
            callee_name="bar",
            callee_receiver=None,
            category=SinkCategory.COMMAND,
            sink_subtype="js_eval",
            file_path="a.py",
            line=1,
            column=0,
            dangerous_slots=[],
            rule_id="ts-eval",
            needs_review=True,
        )
        json_str = site.model_dump_json()
        assert '"js_eval"' in json_str
        assert '"needs_review":true' in json_str
        site2 = SinkCallSite.model_validate_json(json_str)
        assert site2.category == SinkCategory.COMMAND
        assert site2.needs_review is True


class TestSinkTypeCompatibility:
    """Spec B 保留 SinkType 作 risk_scorer 兼容。"""
    def test_sink_type_still_defined(self):
        assert SinkType.SQL_EXECUTION == "sql_execution"
        assert SinkType.COMMAND_EXEC == "command_exec"


class TestSinkRuleLibrary:
    def test_sink_rule_dataclass(self):
        from supernova_core.code_index.sink_detector import SinkRule
        import re
        rule = SinkRule(
            rule_id="py-db-cursor-execute",
            languages=("python",),
            callee="execute",
            receiver_pattern=re.compile(r"^(cursor|cnx|conn|db)$"),
            category=SinkCategory.SQL,
            sink_subtype="sql_raw",
            dangerous_slots=((0, SlotContext.SQL_VALUE),),
        )
        assert rule.rule_id == "py-db-cursor-execute"
        assert rule.languages == ("python",)
        assert rule.dangerous_slots == ((0, SlotContext.SQL_VALUE),)
        assert rule.needs_review_default is False  # default

    def test_default_rule_library_loaded(self):
        """起始规则库至少覆盖 5 语言 x 8 类 sink."""
        from supernova_core.code_index.sink_detector import DEFAULT_RULES, SinkRule
        assert len(DEFAULT_RULES) >= 40
        # Verify language coverage
        langs = {lang for r in DEFAULT_RULES for lang in r.languages}
        assert "python" in langs
        assert "typescript" in langs
        assert "go" in langs
        assert "java" in langs
        assert "php" in langs
        # Verify category coverage (all 8 categories)
        cats = {r.category for r in DEFAULT_RULES}
        assert SinkCategory.SQL in cats
        assert SinkCategory.COMMAND in cats
        assert SinkCategory.DESERIALIZATION in cats
        assert SinkCategory.SSRF in cats
        assert SinkCategory.XSS in cats
        assert SinkCategory.TEMPLATE in cats
        assert SinkCategory.FILE in cats
        assert SinkCategory.REDIRECT in cats

    def test_py_db_cursor_execute_rule_exists(self):
        from supernova_core.code_index.sink_detector import DEFAULT_RULES
        rule = next((r for r in DEFAULT_RULES if r.rule_id == "py-db-cursor-execute"), None)
        assert rule is not None
        assert rule.callee == "execute"
        assert rule.receiver_pattern.match("cursor")
        assert rule.receiver_pattern.match("cnx")
        assert rule.receiver_pattern.match("conn")
        assert rule.receiver_pattern.match("db")
        assert not rule.receiver_pattern.match("users")  # `.query()` of a model
        assert rule.category == SinkCategory.SQL

    def test_py_subprocess_receiver(self):
        from supernova_core.code_index.sink_detector import DEFAULT_RULES
        rule = next((r for r in DEFAULT_RULES if r.rule_id == "py-subprocess-popen"), None)
        assert rule is not None
        assert rule.receiver_pattern.match("subprocess")
        assert not rule.receiver_pattern.match("myobj")
        assert rule.category == SinkCategory.COMMAND

    def test_ts_innerhtml_rule_needs_review(self):
        from supernova_core.code_index.sink_detector import DEFAULT_RULES
        # innerHTML assignment handled via assignment-style rule; if present, must be needs_review
        rule = next((r for r in DEFAULT_RULES if r.rule_id == "ts-innerhtml"), None)
        assert rule is not None
        assert rule.needs_review_default is True
        assert rule.category == SinkCategory.XSS

    def test_py_render_template_string_rule_exists(self):
        from supernova_core.code_index.sink_detector import DEFAULT_RULES
        rule = next((r for r in DEFAULT_RULES if r.rule_id == "py-render-template-string"), None)
        assert rule is not None
        assert rule.callee == "render_template_string"
        assert rule.category == SinkCategory.TEMPLATE

    def test_rule_id_unique(self):
        from supernova_core.code_index.sink_detector import DEFAULT_RULES
        ids = [r.rule_id for r in DEFAULT_RULES]
        assert len(ids) == len(set(ids))

    def test_rule_id_set_externalized_stable(self):
        """外部化锚点:DEFAULT_RULES 的 rule_id 全集须等于搬迁前的 56 条(防 YAML 丢/换规则)。

        搬迁自旧硬编码 DEFAULT_RULES tuple;若 YAML 写错(漏条/改 id),此断言 fail。
        比数量断言更强 —— 防止「数量对但换了一批」。
        """
        from supernova_core.code_index.sink_detector import DEFAULT_RULES
        expected = {
            "go-db-query", "go-exec-command", "go-gorm-exec", "go-gorm-raw",
            "go-http-get", "go-http-post",
            "java-fastjson-parsearray",         # 新增(Task 2)
            "java-fastjson-parseobject",        # 新增(Task 2)
            "java-httpclient-send",
            "java-jackson-readvalue",           # 新增(Task 2)
            "java-jdbctemplate-query",          # 新增(Task 1)
            "java-jpa-createquery", "java-jpa-createnativequery",
            "java-objectinput-readobject",
            "java-runtime-exec", "java-stmt-execute", "java-stmt-executequery",
            "java-stmt-executeupdate",          # 新增(Task 1)
            "php-curl-exec", "php-db-raw", "php-db-select-static",
            "php-file-get-contents", "php-file-put-contents", "php-include",
            "php-laravel-whereraw", "php-mysqli-query", "php-passthru",
            "php-proc-exec", "php-require", "php-shell-exec", "php-system",
            "php-unserialize", "py-db-cursor-execute", "py-db-cursor-executemany",
            "py-django-raw", "py-flask-redirect", "py-jinja-template-render",
            "py-os-popen", "py-os-system", "py-pickle-load", "py-pickle-loads",
            "py-render-template-string", "py-requests-get", "py-requests-post",
            "py-requests-put", "py-sqlalchemy-text", "py-subprocess-call",
            "py-subprocess-checkoutput", "py-subprocess-popen", "py-subprocess-run",
            "py-urllib-urlopen", "py-yaml-load", "ts-axios-get",
            "ts-child-process-exec", "ts-db-query", "ts-document-write", "ts-eval",
            "ts-fetch", "ts-innerhtml", "ts-knex-raw", "ts-orm-model-query",
            "ts-res-redirect",
            # spec 2026-08-21 修复点 D: 服务端模板渲染 XSS(Express res.render)
            "ts-res-render",
            "ts-sequelize-query",
            # 补充(vuln-range 三项目反哺):RestTemplate SSRF / vm / Pug / Angular XSS / needle / marked
            "java-resttemplate-exchange", "java-resttemplate-getforobject",
            "ts-pug-compile", "ts-vm-runincontext",
            "ts-bypass-security-trust-html", "ts-needle-get",
            "ts-marked-render",
            # sink 硬规则增强(Task 3+4):Java 全类别补齐 + execute 双语义
            "java-resttemplate-postforentity", "java-response-sendredirect",
            "java-url-openconnection", "java-httpclient-execute",
            # deepsec 吸收 §1.1 RCE(deepsec matchers/rce.ts)
            "ts-child-process-execsync", "ts-child-process-spawn",
            "ts-child-process-spawn-qualified", "ts-child-process-spawnsync",
            "ts-child-process-spawnsync-qualified", "ts-vm-runinnewcontext",
            "ts-vm-runinthiscontext",
            # deepsec 吸收 §1.2 SSRF(deepsec matchers/ssrf.ts)扩 axios 全方法 + http/undici/got
            "ts-axios-post", "ts-axios-put", "ts-axios-delete", "ts-axios-patch",
            "ts-axios-request", "ts-http-request", "ts-http-get",
            "ts-undici-request", "ts-got-get", "ts-got-post",
            # deepsec 吸收 §1.3 raw SQL(deepsec matchers/{js,py,go,jvm}-sql-raw.ts)
            "ts-sequelize-literal", "ts-sequelize-fn", "ts-knex-whereraw",
            "ts-knex-orderbyraw", "ts-knex-havingraw", "ts-postgresjs-raw",
            "ts-postgresjs-unsafe", "ts-better-sqlite3-prepare", "ts-better-sqlite3-exec",
            "ts-prisma-queryraw", "ts-prisma-executeraw",
            "py-django-extra", "py-asyncpg-fetch",
            "go-db-queryrow", "go-db-querycontext", "go-db-queryrowcontext",
            "go-db-execcontext", "go-sqlx-get", "go-sqlx-select",
            "java-conn-preparestatement", "java-jdbctemplate-update",
            "java-jdbctemplate-queryforobject", "java-jdbctemplate-queryforlist",
            "java-jdbctemplate-batchupdate",
            # deepsec 吸收 §1.4 PHP open redirect 收尾
            "php-redirect",
        }
        got = {r.rule_id for r in DEFAULT_RULES}
        assert got == expected, f"missing={expected-got} extra={got-expected}"


class TestIsEntryHint:
    def test_function_param_identifier(self):
        from supernova_core.code_index.sink_detector import is_entry_hint
        from supernova_core.code_index.models import FuncBlock
        block = FuncBlock(
            id="app.py:f:1", file_path="app.py", function_name="f",
            start_line=1, end_line=2, source_code="def f(user_id): pass",
            parameters=["user_id"], language="python",
        )
        assert is_entry_hint("user_id", block) is True

    def test_request_attr_python(self):
        from supernova_core.code_index.sink_detector import is_entry_hint
        from supernova_core.code_index.models import FuncBlock
        block = FuncBlock(
            id="app.py:f:1", file_path="app.py", function_name="f",
            start_line=1, end_line=2, source_code="", parameters=[], language="python",
        )
        assert is_entry_hint("request.args.get('id')", block) is True
        assert is_entry_hint("request.form['x']", block) is True
        assert is_entry_hint("request.json", block) is True

    def test_request_attr_express(self):
        from supernova_core.code_index.sink_detector import is_entry_hint
        from supernova_core.code_index.models import FuncBlock
        block = FuncBlock(
            id="app.ts:f:1", file_path="app.ts", function_name="f",
            start_line=1, end_line=2, source_code="", parameters=["req"], language="typescript",
        )
        assert is_entry_hint("req.params.id", block) is True
        assert is_entry_hint("req.body", block) is True
        assert is_entry_hint("req.query.x", block) is True

    def test_literal_not_hint(self):
        from supernova_core.code_index.sink_detector import is_entry_hint
        from supernova_core.code_index.models import FuncBlock
        block = FuncBlock(
            id="app.py:f:1", file_path="app.py", function_name="f",
            start_line=1, end_line=2, source_code="", parameters=[], language="python",
        )
        assert is_entry_hint("'literal string'", block) is False
        assert is_entry_hint("42", block) is False

    def test_local_var_not_hint(self):
        from supernova_core.code_index.sink_detector import is_entry_hint
        from supernova_core.code_index.models import FuncBlock
        block = FuncBlock(
            id="app.py:f:1", file_path="app.py", function_name="f",
            start_line=1, end_line=2, source_code="", parameters=["x"], language="python",
        )
        # 'data' is not a parameter — not a hint
        assert is_entry_hint("data", block) is False


class TestDetectSinksPython:
    def test_python_cursor_execute_hit(self):
        from supernova_core.code_index.sink_detector import detect_sinks
        from supernova_core.code_index.parsers.python_parser import PythonParser
        # Build a block with a known cursor.execute call
        src = (
            "def f(user_sql):\n"
            "    cursor.execute(user_sql)\n"
        )
        parser = PythonParser()
        # parse_file needs a real path; tmp_path provides one
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "app.py"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        # Use source_provider to feed bytes back in
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        assert len(sites) == 1
        site = sites[0]
        assert site.callee_name == "execute"
        assert site.callee_receiver == "cursor"
        assert site.category == SinkCategory.SQL
        assert site.rule_id == "py-db-cursor-execute"
        assert len(site.dangerous_slots) == 1
        assert site.dangerous_slots[0].arg_index == 0
        assert site.dangerous_slots[0].slot == SlotContext.SQL_VALUE
        assert site.dangerous_slots[0].expression == "user_sql"
        assert site.dangerous_slots[0].is_entry_hint is True
        assert site.needs_review is False

    def test_python_os_system_hit(self):
        from supernova_core.code_index.sink_detector import detect_sinks
        from supernova_core.code_index.parsers.python_parser import PythonParser
        import tempfile, pathlib
        src = (
            "import os\n"
            "def f(cmd):\n"
            "    os.system(cmd)\n"
        )
        parser = PythonParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "app.py"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        rules = [s.rule_id for s in sites]
        assert "py-os-system" in rules

    def test_python_subprocess_run_hit(self):
        from supernova_core.code_index.sink_detector import detect_sinks
        from supernova_core.code_index.parsers.python_parser import PythonParser
        import tempfile, pathlib
        src = (
            "import subprocess\n"
            "def f(cmd):\n"
            "    subprocess.run(['ls', cmd])\n"
        )
        parser = PythonParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "app.py"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        rules = [s.rule_id for s in sites]
        assert "py-subprocess-run" in rules

    def test_python_pickle_loads_hit(self):
        from supernova_core.code_index.sink_detector import detect_sinks
        from supernova_core.code_index.parsers.python_parser import PythonParser
        import tempfile, pathlib
        src = (
            "import pickle\n"
            "def f(blob):\n"
            "    pickle.loads(blob)\n"
        )
        parser = PythonParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "app.py"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        rules = [s.rule_id for s in sites]
        assert "py-pickle-loads" in rules

    def test_python_render_template_string_hit(self):
        from supernova_core.code_index.sink_detector import detect_sinks
        from supernova_core.code_index.parsers.python_parser import PythonParser
        import tempfile, pathlib
        src = (
            "from flask import render_template_string\n"
            "def f(template_str):\n"
            "    return render_template_string(template_str)\n"
        )
        parser = PythonParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "app.py"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        rules = [s.rule_id for s in sites]
        assert "py-render-template-string" in rules

    def test_python_requests_get_hit(self):
        from supernova_core.code_index.sink_detector import detect_sinks
        from supernova_core.code_index.parsers.python_parser import PythonParser
        import tempfile, pathlib
        src = (
            "import requests\n"
            "def f(url):\n"
            "    requests.get(url)\n"
        )
        parser = PythonParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "app.py"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        rules = [s.rule_id for s in sites]
        assert "py-requests-get" in rules
        ssrf_site = next(s for s in sites if s.rule_id == "py-requests-get")
        assert ssrf_site.category == SinkCategory.SSRF
        assert ssrf_site.dangerous_slots[0].slot == SlotContext.URL

    def test_no_false_positive_model_query(self):
        """.query() on non-DB receiver (User.query) must NOT hit SQL rule
        (no receiver pattern match for 'User')."""
        from supernova_core.code_index.sink_detector import detect_sinks
        from supernova_core.code_index.parsers.python_parser import PythonParser
        import tempfile, pathlib
        src = (
            "def f():\n"
            "    return User.query.all()\n"
        )
        parser = PythonParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "app.py"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        sql_sites = [s for s in sites if s.category == SinkCategory.SQL]
        assert len(sql_sites) == 0

    def test_id_format(self):
        """SinkCallSite.id follows '{file}:{caller_func}:{callee}:{line}:{col}'."""
        from supernova_core.code_index.sink_detector import detect_sinks
        from supernova_core.code_index.parsers.python_parser import PythonParser
        import tempfile, pathlib
        src = (
            "def f(user_sql):\n"
            "    cursor.execute(user_sql)\n"
        )
        parser = PythonParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "app.py"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        assert len(sites) == 1
        # cursor.execute is on line 2, at column 4 (4-space indent)
        assert sites[0].id == "app.py:f:execute:2:4"

    def test_caller_id_links_back(self):
        from supernova_core.code_index.sink_detector import detect_sinks
        from supernova_core.code_index.parsers.python_parser import PythonParser
        import tempfile, pathlib
        src = (
            "def f(user_sql):\n"
            "    cursor.execute(user_sql)\n"
        )
        parser = PythonParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "app.py"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        assert sites[0].caller_id == "app.py:f:1"

    def test_multiple_rules_same_callee_emit_multiple_sites(self):
        from supernova_core.code_index.sink_detector import detect_sinks, SinkRule
        import re
        from supernova_core.code_index.parsers.python_parser import PythonParser
        import tempfile, pathlib
        # Two rules, same callee 'f', both receiver_patterns match 'obj'.
        rule_a = SinkRule("test-multi-a", ("python",), "f", re.compile(r"^obj$"),
                          SinkCategory.SQL, "sql", ((0, SlotContext.SQL_VALUE),))
        rule_b = SinkRule("test-multi-b", ("python",), "f", re.compile(r"^obj$"),
                          SinkCategory.COMMAND, "cmd", ((0, SlotContext.CMD_ARGUMENT),))
        src = "def f(q):\n    obj.f(q)\n"
        parser = PythonParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "app.py"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src),
                             rules=(rule_a, rule_b))
        assert len(sites) == 2
        assert {s.rule_id for s in sites} == {"test-multi-a", "test-multi-b"}
        assert {s.category for s in sites} == {SinkCategory.SQL, SinkCategory.COMMAND}


class TestDangerousSlotsInternals:
    def test_variadic_slot_minus_one(self):
        from supernova_core.code_index.sink_detector import _build_dangerous_slots, SinkRule
        import re
        from supernova_core.code_index.models import FuncBlock
        rule = SinkRule(
            rule_id="test-variadic",
            languages=("python",),
            callee="f",
            receiver_pattern=None,
            category=SinkCategory.COMMAND,
            sink_subtype="cmd",
            dangerous_slots=((-1, SlotContext.CMD_ARGUMENT),),
        )
        block = FuncBlock(
            id="a.py:f:1", file_path="a.py", function_name="f",
            start_line=1, end_line=2, source_code="",
            parameters=["x"], language="python",
        )
        slots = _build_dangerous_slots(rule, ["x", "y"], block)
        assert len(slots) == 1
        assert slots[0].arg_index == -1
        assert slots[0].slot == SlotContext.CMD_ARGUMENT
        assert slots[0].expression == "x,y"          # args joined
        assert slots[0].is_entry_hint is True         # 'x' is a param → any() True

    def test_normal_index_and_out_of_range(self):
        from supernova_core.code_index.sink_detector import _build_dangerous_slots, SinkRule
        from supernova_core.code_index.models import FuncBlock
        rule = SinkRule(
            rule_id="test-normal",
            languages=("python",),
            callee="f",
            receiver_pattern=None,
            category=SinkCategory.SQL,
            sink_subtype="sql",
            dangerous_slots=((0, SlotContext.SQL_VALUE), (5, SlotContext.SQL_VALUE)),
        )
        block = FuncBlock(
            id="a.py:f:1", file_path="a.py", function_name="f",
            start_line=1, end_line=2, source_code="",
            parameters=[], language="python",
        )
        slots = _build_dangerous_slots(rule, ["only_arg"], block)
        # index 0 present, index 5 out of range → skipped, no crash
        assert len(slots) == 1
        assert slots[0].arg_index == 0
        assert slots[0].expression == "only_arg"


class TestDetectSinksCrossLanguage:
    def test_ts_eval_hit(self):
        from supernova_core.code_index.sink_detector import detect_sinks
        from supernova_core.code_index.parsers.typescript_parser import TypeScriptParser
        import tempfile, pathlib
        src = (
            "function f(code: string) {\n"
            "    return eval(code);\n"
            "}\n"
        )
        parser = TypeScriptParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "app.ts"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        rules = [s.rule_id for s in sites]
        assert "ts-eval" in rules

    def test_go_exec_command_hit(self):
        from supernova_core.code_index.sink_detector import detect_sinks
        from supernova_core.code_index.parsers.go_parser import GoParser
        import tempfile, pathlib
        src = (
            "package main\n"
            "import \"os/exec\"\n"
            "func f(cmd string) {\n"
            "    exec.Command(\"sh\", \"-c\", cmd)\n"
            "}\n"
        )
        parser = GoParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "app.go"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        rules = [s.rule_id for s in sites]
        assert "go-exec-command" in rules

    def test_php_unserialize_hit(self):
        from supernova_core.code_index.sink_detector import detect_sinks
        from supernova_core.code_index.parsers.php_parser import PhpParser
        import tempfile, pathlib
        src = (
            "<?php\n"
            "function f($data) {\n"
            "    return unserialize($data);\n"
            "}\n"
        )
        parser = PhpParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "app.php"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        rules = [s.rule_id for s in sites]
        assert "php-unserialize" in rules


# ===== Task 1 (spec 改动 1.2 A + D): ORM Raw sink rules + whitelist guard =====

class TestOrmRawRules:
    """Spec 改动 1.2 A — 9 ORM Raw / string-built SQL sink rules."""

    def test_orm_raw_rules_present(self):
        from supernova_core.code_index.sink_detector import DEFAULT_RULES
        ids = {r.rule_id for r in DEFAULT_RULES}
        for rid in ("py-django-raw", "py-sqlalchemy-text", "ts-knex-raw",
                    "ts-sequelize-query", "go-gorm-raw", "go-gorm-exec",
                    "java-jpa-createnativequery", "php-laravel-whereraw", "php-db-raw"):
            assert rid in ids, f"missing ORM Raw rule {rid}"

    def test_go_gorm_raw_detects_string_built_query(self):
        # Inline Go source with a string-built db.Raw(...) call + real detect_sinks.
        # Uses the same GoParser/parse_file/tempfile harness pattern as the
        # existing test_go_exec_command_hit above.
        from supernova_core.code_index.sink_detector import detect_sinks
        from supernova_core.code_index.parsers.go_parser import GoParser
        import tempfile, pathlib
        src = (
            "package main\n"
            "func h(name string) {\n"
            '    db.Raw("SELECT * FROM u WHERE n = \'" + name + "\'")\n'
            "}\n"
        )
        parser = GoParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "app.go"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        raw = [s for s in sites if s.rule_id == "go-gorm-raw"]
        assert raw, "go-gorm-raw should fire on db.Raw(...) with concatenation"
        assert raw[0].category == SinkCategory.SQL

    def test_orm_raw_rule_fields(self):
        """Spot-check field values for a couple of the new rules."""
        from supernova_core.code_index.sink_detector import DEFAULT_RULES
        def _rule(rule_id: str):
            return next(r for r in DEFAULT_RULES if r.rule_id == rule_id)

        # py-django-raw: receiver 'objects', sql_raw, SQL_VALUE slot 0
        r = _rule("py-django-raw")
        assert r.callee == "raw"
        assert r.receiver_pattern.match("objects")
        assert not r.receiver_pattern.match("User")
        assert r.category == SinkCategory.SQL
        assert r.sink_subtype == "sql_raw"
        assert r.dangerous_slots == ((0, SlotContext.SQL_VALUE),)
        assert r.needs_review_default is False

        # py-sqlalchemy-text: bare callee, needs_review_default=True
        r = _rule("py-sqlalchemy-text")
        assert r.callee == "text"
        assert r.receiver_pattern is None
        assert r.needs_review_default is True

        # java-jpa-createnativequery: rp=.+ (Task 1 receiver_pattern 失配修复,原 null), needs_review_default=True
        r = _rule("java-jpa-createnativequery")
        assert r.callee == "createNativeQuery"
        assert r.receiver_pattern.match("em")   # Task 1: rp null→.+ 任意 receiver 命中
        assert r.needs_review_default is True


class TestSqlCommandWhitelistGuard:
    """Spec 改动 1.2 D — guard: SQL/COMMAND issue_types must stay in whitelist."""

    def test_sql_command_categories_in_whitelist(self):
        from supernova_core.code_index.finding_models import VALID_INJECTION_CATEGORIES
        # New ORM Raw / command rules produce SQL/COMMAND findings; their
        # issue_types must be accepted by VALID_INJECTION_CATEGORIES.
        assert "sql_injection" in VALID_INJECTION_CATEGORIES
        assert "command_injection" in VALID_INJECTION_CATEGORIES


# ===== Task 2 (spec 改动 1.2 B): dynamic-identifier arg-shape detection =====

class TestSqlArgShapeIdentifier:
    """Spec 改动 1.2 B — SQL sink's string-built arg → SQL_IDENTIFIER + needs_review.

    Uses the same inline PythonParser + tempfile + _src_provider harness as the
    existing TestDetectSinksPython cases (the brief's _py_blocks/_py_parser
    helpers are not defined in this module).
    """

    def test_py_sql_fstring_arg_marked_identifier(self):
        # f-string arg into SQL sink → SQL_IDENTIFIER slot + needs_review True.
        # The receiver must match _DB_CURSOR (cursor|cnx|conn|db|database),
        # so we name the cursor variable "cursor" (matches existing
        # test_python_cursor_execute_hit convention).
        from supernova_core.code_index.sink_detector import detect_sinks
        from supernova_core.code_index.parsers.python_parser import PythonParser
        import tempfile, pathlib
        src = (
            "def f(tn):\n"
            '    cursor.execute(f"SELECT * FROM {tn}")\n'
        )
        parser = PythonParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "app.py"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        ex = [s for s in sites if s.rule_id == "py-db-cursor-execute"]
        assert ex, "cursor.execute should still fire on f-string arg"
        ident_slots = [d for d in ex[0].dangerous_slots
                       if d.slot == SlotContext.SQL_IDENTIFIER]
        assert ident_slots, "f-string arg into SQL sink should be marked SQL_IDENTIFIER"
        assert ex[0].needs_review is True

    def test_py_sql_bound_arg_stays_value(self):
        # bound ?-placeholder arg stays SQL_VALUE, no SQL_IDENTIFIER slot,
        # and needs_review stays at its rule default (False for cursor.execute).
        from supernova_core.code_index.sink_detector import detect_sinks
        from supernova_core.code_index.parsers.python_parser import PythonParser
        import tempfile, pathlib
        src = (
            "def f(name):\n"
            '    cursor.execute("SELECT * FROM u WHERE n = ?", (name,))\n'
        )
        parser = PythonParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "app.py"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        ex = [s for s in sites if s.rule_id == "py-db-cursor-execute"]
        assert ex, "cursor.execute should fire on bound-arg query"
        val_slots = [d for d in ex[0].dangerous_slots
                     if d.slot == SlotContext.SQL_VALUE]
        ident_slots = [d for d in ex[0].dangerous_slots
                       if d.slot == SlotContext.SQL_IDENTIFIER]
        assert val_slots and not ident_slots, \
            "bound ?-placeholder arg must stay SQL_VALUE"
        assert ex[0].needs_review is False


# ===== Koa+Sequelize 治本(改动2): ts-orm-model-query 覆盖模型/实例 .query =====

class TestOrmModelQuery:
    """改动2 — ts-orm-model-query:Sequelize 模型/实例的 .query(trip: Trip.query /
    dbConfig.trip.query)。原 ts-sequelize-query(receiver ^sequelize$) + ts-db-query(裸调用)
    全漏 → receiver_pattern ".+" = 任意非空 receiver 兜底,needs_review_default=True
    交 Spec C LLM 复核滤非 SQL 的 .query(静态精度不足)。"""

    def test_ts_orm_model_query_rule_present(self):
        from supernova_core.code_index.sink_detector import DEFAULT_RULES
        rule = next((r for r in DEFAULT_RULES if r.rule_id == "ts-orm-model-query"), None)
        assert rule is not None
        assert rule.callee == "query"
        assert rule.receiver_pattern.match("Trip")
        assert rule.receiver_pattern.match("dbConfig.trip")
        assert rule.receiver_pattern.match("sequelize")
        assert not rule.receiver_pattern.match("")  # 空不匹配(裸调用归 ts-db-query)
        assert rule.category == SinkCategory.SQL
        assert rule.needs_review_default is True

    def test_trip_query_hit(self):
        """Trip.query(sql) receiver=Trip → 命中 ts-orm-model-query, needs_review=True。"""
        from supernova_core.code_index.sink_detector import detect_sinks
        from supernova_core.code_index.parsers.typescript_parser import TypeScriptParser
        import tempfile, pathlib
        src = (
            "function f(sql: string) {\n"
            "    return Trip.query(sql);\n"
            "}\n"
        )
        parser = TypeScriptParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "svc.ts"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        orm = [s for s in sites if s.rule_id == "ts-orm-model-query"]
        assert orm, "Trip.query(sql) 应命中 ts-orm-model-query"
        assert orm[0].callee_name == "query"
        assert orm[0].callee_receiver == "Trip"
        assert orm[0].category == SinkCategory.SQL
        assert orm[0].needs_review is True

    def test_dbconfig_trip_query_chain_receiver_hit(self):
        """dbConfig.trip.query(sql) receiver=整链 dbConfig.trip(typescript_parser
        member_expression object=整链)→ .+ 匹配(改动2 关键:整链 receiver 覆盖,
        非首段 dbConfig)。"""
        from supernova_core.code_index.sink_detector import detect_sinks
        from supernova_core.code_index.parsers.typescript_parser import TypeScriptParser
        import tempfile, pathlib
        src = (
            "function f(sql: string) {\n"
            "    return dbConfig.trip.query(sql);\n"
            "}\n"
        )
        parser = TypeScriptParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "svc.ts"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        orm = [s for s in sites if s.rule_id == "ts-orm-model-query"]
        assert orm, "dbConfig.trip.query(sql) 应命中 ts-orm-model-query"
        assert orm[0].callee_receiver == "dbConfig.trip"  # 整链(非首段)
        assert orm[0].needs_review is True


class TestJavaSqlSinksHardening:
    """Java SQL sink 规则 receiver_pattern 失配修复(治 0 命中)+ 补 createQuery/JdbcTemplate/executeUpdate。

    根因:_rule_matches 对 rp=null 只匹配裸调用(receiver is None),但 Java method call 恒为
    instance.method(),receiver 非空(stmt/em/jdbcTemplate)→ 8 条 Java 规则全 0 命中。
    改 rp='.+' + needs_review_default=true 后任意 receiver 命中。
    """

    def _java_sites(self, body: str):
        """helper:完整 class 包裹方法体 → JavaParser 切 block → detect_sinks。"""
        from supernova_core.code_index.sink_detector import detect_sinks
        from supernova_core.code_index.parsers.java_parser import JavaParser
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


class TestJavaDeserSinksHardening:
    """Java deser sink:fastjson/Jackson 补召回 + readObject rp 失配修复。

    复现原始版 INJ-01:ClusterConfigController.apiModifyClusterConfig 的
    JSON.parseObject(payload)(fastjson autotype,RCE 级)—— 重构版硬规则 0 命中根因之一。
    """

    def _java_sites(self, body: str):
        from supernova_core.code_index.sink_detector import detect_sinks
        from supernova_core.code_index.parsers.java_parser import JavaParser
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


class TestJavaSsrfCmdRedirectSinks:
    """Java SSRF/Command/Redirect 规则:rp ^(类型名)$ 失配修复(Java receiver 是实例变量/整链)+ 补全。"""

    def _java_sites(self, body: str):
        from supernova_core.code_index.sink_detector import detect_sinks
        from supernova_core.code_index.parsers.java_parser import JavaParser
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
        原 rp ^(Runtime|getRuntime)$ 不匹配整链;改 .+ 后命中。"""
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


class TestExecuteDualSemantics:
    """execute 双语义:Statement.execute(SQL)+ httpClient.execute(SSRF),callee 同名无法消歧。

    保留 java-stmt-execute(sql)+ 加 java-httpclient-execute(ssrf),双命中靠 chain_verdict
    复核否决 SQL 那条(下游职责,本测试只验证 detect_sinks 产两个 SinkCallSite)。DDL 不漏。
    """

    def _java_sites(self, body: str):
        from supernova_core.code_index.sink_detector import detect_sinks
        from supernova_core.code_index.parsers.java_parser import JavaParser
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
        assert "java-httpclient-execute" in rule_ids, "httpClient.execute 应命中 java-httpclient-execute(ssrf)"
        assert "java-stmt-execute" in rule_ids, "httpClient.execute 也命中 java-stmt-execute(sql,待 chain_verdict 否决)"
        ssrf = [s for s in sites if s.rule_id == "java-httpclient-execute"]
        assert ssrf[0].category == SinkCategory.SSRF
        assert ssrf[0].callee_receiver == "httpClient"


class TestOtherLangsReceiverFix:
    """别语言 receiver 失配小修:php $ 前缀 / go-db-query / ts-child-process-exec。"""

    def test_php_mysqli_query_dollar_receiver_hit(self):
        """$mysqli->query($sql) receiver='$mysqli' → php_parser lstrip $ → 'mysqli' → 命中 php-mysqli-query。"""
        from supernova_core.code_index.sink_detector import detect_sinks
        from supernova_core.code_index.parsers.php_parser import PhpParser
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
        from supernova_core.code_index.sink_detector import detect_sinks
        from supernova_core.code_index.parsers.go_parser import GoParser
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
        from supernova_core.code_index.sink_detector import detect_sinks
        from supernova_core.code_index.parsers.typescript_parser import TypeScriptParser
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


# ===== §0 死规则修复(deepsec 吸收 M1): ts-res-redirect + php-laravel-whereraw =====

class TestDeadRuleFixRedirectAndWhereRaw:
    """死规则修复:原 receiver_pattern: null 只匹配裸调用,但真实写法恒带 receiver
    → 生产中永不命中。

    - ts-res-redirect: TS/JS 现实中 res.redirect(u) 100% 带 receiver;原 rp=null 命不中。
      修复 rp null→^(res|response|ctx)$。连带解锁 ssrf_builder.py REDIRECT 过滤空转。
    - php-laravel-whereraw: Laravel 真实写法 $q->whereRaw()/DB::whereRaw() 带 receiver;
      原 rp=null 命不中(裸 whereRaw() 极罕见)。修复 rp null→.+ (对齐 java-stmt-execute 惯例)。
    """

    def test_ts_res_redirect_qualified_hit(self):
        """res.redirect(url) receiver=res → 命中 ts-res-redirect(原 rp=null 不命中)。"""
        from supernova_core.code_index.sink_detector import detect_sinks
        from supernova_core.code_index.parsers.typescript_parser import TypeScriptParser
        import tempfile, pathlib
        src = (
            "function f(url: string, res: any) {\n"
            "  res.redirect(url);\n"
            "}\n"
        )
        parser = TypeScriptParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "app.ts"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        hit = [s for s in sites if s.rule_id == "ts-res-redirect"]
        assert hit, "res.redirect(url) 应命中 ts-res-redirect(rp ^(res|response|ctx)$)"
        assert hit[0].callee_name == "redirect"
        assert hit[0].callee_receiver == "res"
        assert hit[0].category == SinkCategory.REDIRECT

    def test_ts_response_redirect_hit(self):
        """response.redirect(url) receiver=response → 命中(收窄集合内)。"""
        from supernova_core.code_index.sink_detector import detect_sinks
        from supernova_core.code_index.parsers.typescript_parser import TypeScriptParser
        import tempfile, pathlib
        src = "function f(url: string, response: any) {\n  response.redirect(url);\n}\n"
        parser = TypeScriptParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "a.ts"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        assert any(s.rule_id == "ts-res-redirect" for s in sites)

    def test_ts_res_redirect_bare_call_no_longer_hit(self):
        """裸 redirect(url)(TS 现实中不存在此写法)修复后不再命中 —— rp 收窄语义锁定。

        原死规则靠裸调用命中(只在测试里 exists);修复后裸调用不命中,正合语义:
        open redirect 的危险写法是 res.redirect,不是 bare redirect。
        """
        from supernova_core.code_index.sink_detector import detect_sinks
        from supernova_core.code_index.parsers.typescript_parser import TypeScriptParser
        import tempfile, pathlib
        src = "function f(url: string) {\n  return redirect(url);\n}\n"
        parser = TypeScriptParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "a.ts"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        assert not any(s.rule_id == "ts-res-redirect" for s in sites), \
            "裸 redirect(url) 不应再命中 ts-res-redirect(rp 收窄后)"

    def test_ts_res_redirect_non_matching_receiver_misses(self):
        """someObj.redirect(url) receiver 不在收窄集合 → 不命中(防误报泛化)。"""
        from supernova_core.code_index.sink_detector import detect_sinks
        from supernova_core.code_index.parsers.typescript_parser import TypeScriptParser
        import tempfile, pathlib
        src = "function f(url: string) {\n  foo.redirect(url);\n}\n"
        parser = TypeScriptParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "a.ts"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        assert not any(s.rule_id == "ts-res-redirect" for s in sites)

    def test_php_laravel_whereraw_chain_receiver_hit(self):
        """$query->whereRaw(sql) receiver=query(php_parser lstrip $)→ 命中(原 rp=null 不命中)。"""
        from supernova_core.code_index.sink_detector import detect_sinks
        from supernova_core.code_index.parsers.php_parser import PhpParser
        import tempfile, pathlib
        src = (
            "<?php\n"
            "function f($sql) {\n"
            "  $query->whereRaw($sql);\n"
            "}\n"
        )
        parser = PhpParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "app.php"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        hit = [s for s in sites if s.rule_id == "php-laravel-whereraw"]
        assert hit, "$query->whereRaw($sql) 应命中 php-laravel-whereraw(rp .+ 后)"
        assert hit[0].callee_name == "whereRaw"
        assert hit[0].callee_receiver == "query"   # lstrip $
        assert hit[0].category == SinkCategory.SQL

    def test_php_laravel_whereraw_static_db_hit(self):
        """DB::whereRaw(sql) scoped_call receiver=DB → 命中(.+ 覆盖静态调用形态)。"""
        from supernova_core.code_index.sink_detector import detect_sinks
        from supernova_core.code_index.parsers.php_parser import PhpParser
        import tempfile, pathlib
        src = (
            "<?php\n"
            "function f($sql) {\n"
            "  DB::whereRaw($sql);\n"
            "}\n"
        )
        parser = PhpParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "app.php"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        hit = [s for s in sites if s.rule_id == "php-laravel-whereraw"]
        assert hit, "DB::whereRaw($sql) 应命中 php-laravel-whereraw(scoped_call receiver=DB)"
        assert hit[0].callee_receiver == "DB"


# ===== §1.1 RCE(deepsec 吸收 M2a): TS/JS command exec 扩 execSync/spawn/spawnSync/vm =====

class TestDeepsecRceSinks:
    """deepsec matchers/rce.ts 吸收:execSync / spawn / spawnSync / vm.runInNew|ThisContext。

    探针已验证 iter_calls 抽取正常(receiver 形态正确);new Function() 是 new_expression
    抽不出 callee → 不上规则(归 §4.2)。
    """

    def _ts_sites(self, src: str):
        from supernova_core.code_index.sink_detector import detect_sinks
        from supernova_core.code_index.parsers.typescript_parser import TypeScriptParser
        import tempfile, pathlib
        parser = TypeScriptParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "a.ts"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        return detect_sinks(blocks, parser, source_provider=_src_provider(src))

    def test_execsync_bare_hit(self):
        sites = self._ts_sites("function f(cmd: string){ return execSync(cmd); }\n")
        hit = [s for s in sites if s.rule_id == "ts-child-process-execsync"]
        assert hit, "execSync(cmd) 裸调用应命中 ts-child-process-execsync"
        assert hit[0].category == SinkCategory.COMMAND
        assert hit[0].callee_receiver is None

    def test_spawn_bare_hit(self):
        sites = self._ts_sites("function f(cmd: string){ return spawn(cmd); }\n")
        assert any(s.rule_id == "ts-child-process-spawn" for s in sites)

    def test_spawn_qualified_hit(self):
        sites = self._ts_sites(
            "import * as cp from 'child_process';\n"
            "function f(cmd: string){ return cp.spawn('sh', ['-c', cmd]); }\n")
        hit = [s for s in sites if s.rule_id == "ts-child-process-spawn-qualified"]
        assert hit, "cp.spawn(...) 应命中 ts-child-process-spawn-qualified"
        assert hit[0].callee_receiver == "cp"

    def test_spawnsync_bare_hit(self):
        sites = self._ts_sites("function f(cmd: string){ return spawnSync(cmd); }\n")
        assert any(s.rule_id == "ts-child-process-spawnsync" for s in sites)

    def test_spawnsync_qualified_hit(self):
        sites = self._ts_sites(
            "import * as cp from 'child_process';\n"
            "function f(cmd: string){ return cp.spawnSync('git', ['status']); }\n")
        assert any(s.rule_id == "ts-child-process-spawnsync-qualified" for s in sites)

    def test_vm_runinnewcontext_hit(self):
        """vm.runInNewContext(code, ctx) → ts-vm-runinnewcontext(既有 runIncontext 是不同 callee)。"""
        sites = self._ts_sites(
            "import * as vm from 'vm';\n"
            "function f(code: string){ return vm.runInNewContext(code, {}); }\n")
        hit = [s for s in sites if s.rule_id == "ts-vm-runinnewcontext"]
        assert hit, "vm.runInNewContext(code) 应命中 ts-vm-runinnewcontext"
        assert hit[0].callee_receiver == "vm"
        assert hit[0].category == SinkCategory.COMMAND

    def test_vm_runinthiscontext_hit(self):
        sites = self._ts_sites(
            "import * as vm from 'vm';\n"
            "function f(snippet: string){ return vm.runInThisContext(snippet); }\n")
        assert any(s.rule_id == "ts-vm-runinthiscontext" for s in sites)

    def test_vm_runincontext_distinct_from_new(self):
        """既有 ts-vm-runincontext(runInContext)与新增 runInNewContext 各自精确匹配,不串。"""
        sites = self._ts_sites(
            "import * as vm from 'vm';\n"
            "function f(c: string){ vm.runInContext(c); vm.runInNewContext(c, {}); }\n")
        ids = {s.rule_id for s in sites if s.callee_name.startswith("runIn")}
        assert "ts-vm-runincontext" in ids
        assert "ts-vm-runinnewcontext" in ids

    def test_new_function_not_extracted(self):
        """new Function(...) 是 new_expression,iter_calls 抽不出 callee → 不命中任何规则。

        归 §4.2 detector 能力项;此测试锁定现状,防误以为已覆盖。
        """
        sites = self._ts_sites(
            "function f(b: string){ return new Function('return ' + b)(); }\n")
        assert not [s for s in sites if s.callee_name == "Function"], \
            "new Function() 不应被 iter_calls 抽出(归 §4.2 new_expression 支持)"


# ===== §1.2 SSRF(deepsec 吸收 M2a): axios 全方法 + Node http/undici/got =====

class TestDeepsecSsrfSinks:
    """deepsec matchers/ssrf.ts 吸收:扩 axios 全方法 + Node 原生 http.request/get + undici/got。"""

    def _ts_sites(self, src: str):
        from supernova_core.code_index.sink_detector import detect_sinks
        from supernova_core.code_index.parsers.typescript_parser import TypeScriptParser
        import tempfile, pathlib
        parser = TypeScriptParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "a.ts"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        return detect_sinks(blocks, parser, source_provider=_src_provider(src))

    def test_axios_post_put_delete_patch_hit(self):
        src = (
            "function f(u: string){ axios.post(u); axios.put(u); "
            "axios.delete(u); axios.patch(u); }\n"
        )
        sites = self._ts_sites(src)
        ids = {s.rule_id for s in sites}
        assert "ts-axios-post" in ids
        assert "ts-axios-put" in ids
        assert "ts-axios-delete" in ids
        assert "ts-axios-patch" in ids

    def test_axios_request_hit(self):
        sites = self._ts_sites("function f(u: string){ axios.request({url: u}); }\n")
        hit = [s for s in sites if s.rule_id == "ts-axios-request"]
        assert hit, "axios.request(config) 应命中 ts-axios-request"
        assert hit[0].needs_review is True  # request 词泛,标 review

    def test_http_request_hit(self):
        """http.request(url) Node 原生 → ts-http-request。"""
        sites = self._ts_sites(
            "import * as http from 'http';\n"
            "function f(u: string){ http.request(u); }\n")
        hit = [s for s in sites if s.rule_id == "ts-http-request"]
        assert hit, "http.request(url) 应命中 ts-http-request"
        assert hit[0].callee_receiver == "http"
        assert hit[0].category == SinkCategory.SSRF

    def test_https_request_hit(self):
        sites = self._ts_sites(
            "import * as https from 'https';\n"
            "function f(u: string){ https.request(u); }\n")
        assert any(s.rule_id == "ts-http-request" for s in sites)

    def test_http_get_hit(self):
        sites = self._ts_sites(
            "import * as http from 'http';\n"
            "function f(u: string){ http.get(u); }\n")
        hit = [s for s in sites if s.rule_id == "ts-http-get"]
        assert hit, "http.get(url) 应命中 ts-http-get"
        assert hit[0].needs_review is True  # get 词极泛,标 review

    def test_undici_request_hit(self):
        sites = self._ts_sites(
            "import * as undici from 'undici';\n"
            "function f(u: string){ undici.request(u); }\n")
        assert any(s.rule_id == "ts-undici-request" for s in sites)

    def test_got_get_post_hit(self):
        sites = self._ts_sites(
            "import * as got from 'got';\n"
            "function f(u: string){ got.get(u); got.post(u); }\n")
        ids = {s.rule_id for s in sites}
        assert "ts-got-get" in ids
        assert "ts-got-post" in ids

    def test_axios_get_unaffected_by_new_rules(self):
        """既有 ts-axios-get 不受新增 post/put 规则影响(get 仍命中 ts-axios-get)。"""
        sites = self._ts_sites("function f(u: string){ axios.get(u); }\n")
        assert any(s.rule_id == "ts-axios-get" for s in sites)

    def test_non_axios_get_not_hit_ssrf(self):
        """foo.get(u)(receiver 非 axios/http/https/got)→ 不命中 SSRF(防误报泛化)。"""
        sites = self._ts_sites("function f(u: string){ foo.get(u); }\n")
        ssrf = [s for s in sites if s.category == SinkCategory.SSRF]
        assert not ssrf, "foo.get() 不应命中任何 SSRF 规则"


# ===== §1.3-1.4 raw SQL + redirect(deepsec 吸收 M2b)=====

class TestDeepsecRawSqlTs:
    """deepsec matchers/js-sql-raw.ts 吸收:Sequelize.literal/fn、knex.*Raw、postgres.js、
    better-sqlite3、Prisma $queryRaw/$executeRaw。"""

    def _ts_sites(self, src: str):
        from supernova_core.code_index.sink_detector import detect_sinks
        from supernova_core.code_index.parsers.typescript_parser import TypeScriptParser
        import tempfile, pathlib
        parser = TypeScriptParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "a.ts"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        return detect_sinks(blocks, parser, source_provider=_src_provider(src))

    def test_sequelize_literal_both_cases(self):
        """Sequelize.literal(类名)/sequelize.literal(实例)双形态都命中。"""
        sites = self._ts_sites(
            "function f(sql: string){ Sequelize.literal(sql); sequelize.literal(sql); }\n")
        lit = [s for s in sites if s.rule_id == "ts-sequelize-literal"]
        assert len(lit) == 2, "Sequelize.literal + sequelize.literal 都应命中"
        assert all(s.category == SinkCategory.SQL for s in lit)

    def test_sequelize_fn_hit(self):
        sites = self._ts_sites("function f(cmd: string){ Sequelize.fn(cmd, 1); }\n")
        assert any(s.rule_id == "ts-sequelize-fn" for s in sites)

    def test_knex_whereraw_orderbyraw_havingraw_hit(self):
        src = (
            "function f(sql: string){ knex.whereRaw(sql); "
            "knex.orderByRaw(sql); knex.havingRaw(sql); }\n"
        )
        sites = self._ts_sites(src)
        ids = {s.rule_id for s in sites}
        assert "ts-knex-whereraw" in ids
        assert "ts-knex-orderbyraw" in ids
        assert "ts-knex-havingraw" in ids

    def test_postgresjs_raw_unsafe_hit(self):
        sites = self._ts_sites(
            "import * as sql from 'postgres';\n"
            "function f(s: string){ sql.raw(s); sql.unsafe(s); }\n")
        ids = {s.rule_id for s in sites}
        assert "ts-postgresjs-raw" in ids
        assert "ts-postgresjs-unsafe" in ids

    def test_better_sqlite3_prepare_exec_hit(self):
        sites = self._ts_sites(
            "function f(s: string){ db.prepare(s); db.exec(s); }\n")
        ids = {s.rule_id for s in sites}
        assert "ts-better-sqlite3-prepare" in ids
        assert "ts-better-sqlite3-exec" in ids

    def test_prisma_queryraw_executeraw_hit(self):
        """prisma.$queryRaw(sql) call 形态命中;tagged template 形态归 §4.3(此处只测 call)。"""
        sites = self._ts_sites(
            "function f(s: string){ prisma.$queryRaw(s); prisma.$executeRaw(s); }\n")
        ids = {s.rule_id for s in sites}
        assert "ts-prisma-queryraw" in ids, "$queryRaw call 形态应命中"
        assert "ts-prisma-executeraw" in ids

    def test_knex_raw_unchanged(self):
        """既有 ts-knex-raw(raw@knex)不受新增 whereRaw 等影响。"""
        sites = self._ts_sites("function f(sql: string){ knex.raw(sql); }\n")
        assert any(s.rule_id == "ts-knex-raw" for s in sites)


class TestDeepsecRawSqlGo:
    """deepsec matchers/go-sql-raw.ts 吸收:QueryRow/Query*Context/ExecContext/sqlx Get/Select。"""

    def _go_sites(self, src: str):
        from supernova_core.code_index.sink_detector import detect_sinks
        from supernova_core.code_index.parsers.go_parser import GoParser
        import tempfile, pathlib
        parser = GoParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "a.go"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        return detect_sinks(blocks, parser, source_provider=_src_provider(src))

    def test_queryrow_hit(self):
        sites = self._go_sites("package main\nfunc f(db DB, sql string){ db.QueryRow(sql) }\n")
        assert any(s.rule_id == "go-db-queryrow" for s in sites)

    def test_querycontext_arg1_hit(self):
        """QueryContext(ctx, sql) 危险参数在 arg1(非 arg0)。"""
        sites = self._go_sites(
            "package main\nfunc f(db DB, ctx Ctx, sql string){ db.QueryContext(ctx, sql) }\n")
        hit = [s for s in sites if s.rule_id == "go-db-querycontext"]
        assert hit, "db.QueryContext(ctx, sql) 应命中 go-db-querycontext"
        assert hit[0].dangerous_slots[0].arg_index == 1

    def test_queryrowcontext_execcontext_hit(self):
        sites = self._go_sites(
            "package main\nfunc f(db DB, ctx Ctx, sql string){ "
            "db.QueryRowContext(ctx, sql); db.ExecContext(ctx, sql) }\n")
        ids = {s.rule_id for s in sites}
        assert "go-db-queryrowcontext" in ids
        assert "go-db-execcontext" in ids

    def test_sqlx_get_select_hit(self):
        sites = self._go_sites(
            "package main\nfunc f(db DB, sql string){ db.Get(&u, sql); db.Select(&us, sql) }\n")
        ids = {s.rule_id for s in sites}
        assert "go-sqlx-get" in ids
        assert "go-sqlx-select" in ids


class TestDeepsecRawSqlJava:
    """deepsec matchers/jvm-sql-raw.ts 吸收:prepareStatement/JdbcTemplate update/queryFor*/batchUpdate。"""

    def _java_sites(self, body: str):
        from supernova_core.code_index.sink_detector import detect_sinks
        from supernova_core.code_index.parsers.java_parser import JavaParser
        import tempfile, pathlib
        src = f"class C {{\n  void q(String s) {{\n{body}\n  }}\n}}\n"
        parser = JavaParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "C.java"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        return detect_sinks(blocks, parser, source_provider=_src_provider(src))

    def test_preparestatement_hit(self):
        sites = self._java_sites("    conn.prepareStatement(sql);")
        hit = [s for s in sites if s.rule_id == "java-conn-preparestatement"]
        assert hit, "conn.prepareStatement(sql) 应命中 java-conn-preparestatement"
        assert hit[0].callee_receiver == "conn"

    def test_jdbctemplate_update_queryforobject_queryforlist_batchupdate_hit(self):
        sites = self._java_sites(
            "    jdbcTemplate.update(sql);\n"
            "    jdbcTemplate.queryForObject(sql, String.class);\n"
            "    jdbcTemplate.queryForList(sql);\n"
            "    jdbcTemplate.batchUpdate(sql);")
        ids = {s.rule_id for s in sites}
        assert "java-jdbctemplate-update" in ids
        assert "java-jdbctemplate-queryforobject" in ids
        assert "java-jdbctemplate-queryforlist" in ids
        assert "java-jdbctemplate-batchupdate" in ids

    def test_jdbctemplate_query_unchanged(self):
        """既有 java-jdbctemplate-query 不受新增 update/queryFor* 影响。"""
        sites = self._java_sites("    jdbcTemplate.query(sql);")
        assert any(s.rule_id == "java-jdbctemplate-query" for s in sites)


class TestDeepsecRawSqlPy:
    """deepsec matchers/py-sql-raw.ts 吸收:django extra、asyncpg fetch、
    + 改 py-db-cursor-execute 扩 receiver 加 session(SQLAlchemy session.execute)。"""

    def _py_sites(self, src: str):
        from supernova_core.code_index.sink_detector import detect_sinks
        from supernova_core.code_index.parsers.python_parser import PythonParser
        import tempfile, pathlib
        parser = PythonParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "a.py"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        return detect_sinks(blocks, parser, source_provider=_src_provider(src))

    def test_session_execute_now_hits_cursor_rule(self):
        """session.execute(sql)(SQLAlchemy)→ 改后的 py-db-cursor-execute 命中(原 receiver 无 session 不命中)。"""
        sites = self._py_sites("def f(session, sql):\n    session.execute(sql)\n")
        hit = [s for s in sites if s.rule_id == "py-db-cursor-execute"]
        assert hit, "session.execute(sql) 应命中 py-db-cursor-execute(扩 receiver 加 session)"
        assert hit[0].callee_receiver == "session"

    def test_django_extra_hit(self):
        """User.objects.extra(where=...) → py-django-extra(receiver 整链 User.objects)。"""
        sites = self._py_sites(
            'def f():\n    return User.objects.extra(where=["x"])\n')
        hit = [s for s in sites if s.rule_id == "py-django-extra"]
        assert hit, "User.objects.extra(...) 应命中 py-django-extra"
        assert hit[0].callee_receiver == "User.objects"

    def test_asyncpg_fetch_hit(self):
        sites = self._py_sites("def f(conn, sql):\n    conn.fetch(sql)\n")
        hit = [s for s in sites if s.rule_id == "py-asyncpg-fetch"]
        assert hit, "conn.fetch(sql) 应命中 py-asyncpg-fetch"
        assert hit[0].callee_receiver == "conn"

    def test_cursor_execute_still_works(self):
        """既有 cursor.execute(sql) 命中不变(回归)。"""
        sites = self._py_sites("def f(sql):\n    cursor.execute(sql)\n")
        assert any(s.rule_id == "py-db-cursor-execute" for s in sites)


class TestDeepsecPhpRedirect:
    """§1.4 PHP open redirect 收尾:$response->redirect(u) method 形态确定性命中。"""

    def _php_sites(self, src: str):
        from supernova_core.code_index.sink_detector import detect_sinks
        from supernova_core.code_index.parsers.php_parser import PhpParser
        import tempfile, pathlib
        parser = PhpParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "a.php"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        return detect_sinks(blocks, parser, source_provider=_src_provider(src))

    def test_response_redirect_hit(self):
        sites = self._php_sites("<?php\nfunction f($u){ $response->redirect($u); }\n")
        hit = [s for s in sites if s.rule_id == "php-redirect"]
        assert hit, "$response->redirect($u) 应命中 php-redirect"
        assert hit[0].callee_receiver == "response"
        assert hit[0].category == SinkCategory.REDIRECT

    def test_bare_redirect_not_hit_php_redirect_rule(self):
        """bare redirect($u)(helper)不命中 php-redirect 规则(rp 收窄),归候选表兜底。"""
        sites = self._php_sites("<?php\nfunction f($u){ return redirect($u); }\n")
        assert not any(s.rule_id == "php-redirect" for s in sites)



