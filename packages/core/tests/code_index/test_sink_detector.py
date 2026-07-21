"""Tests for sink_detector module and SinkCallSite model."""
from shannon_core.code_index.parameter_models import (
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
        from shannon_core.code_index.sink_detector import SinkRule
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
        from shannon_core.code_index.sink_detector import DEFAULT_RULES, SinkRule
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
        from shannon_core.code_index.sink_detector import DEFAULT_RULES
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
        from shannon_core.code_index.sink_detector import DEFAULT_RULES
        rule = next((r for r in DEFAULT_RULES if r.rule_id == "py-subprocess-popen"), None)
        assert rule is not None
        assert rule.receiver_pattern.match("subprocess")
        assert not rule.receiver_pattern.match("myobj")
        assert rule.category == SinkCategory.COMMAND

    def test_ts_innerhtml_rule_needs_review(self):
        from shannon_core.code_index.sink_detector import DEFAULT_RULES
        # innerHTML assignment handled via assignment-style rule; if present, must be needs_review
        rule = next((r for r in DEFAULT_RULES if r.rule_id == "ts-innerhtml"), None)
        assert rule is not None
        assert rule.needs_review_default is True
        assert rule.category == SinkCategory.XSS

    def test_py_render_template_string_rule_exists(self):
        from shannon_core.code_index.sink_detector import DEFAULT_RULES
        rule = next((r for r in DEFAULT_RULES if r.rule_id == "py-render-template-string"), None)
        assert rule is not None
        assert rule.callee == "render_template_string"
        assert rule.category == SinkCategory.TEMPLATE

    def test_rule_id_unique(self):
        from shannon_core.code_index.sink_detector import DEFAULT_RULES
        ids = [r.rule_id for r in DEFAULT_RULES]
        assert len(ids) == len(set(ids))

    def test_rule_id_set_externalized_stable(self):
        """外部化锚点:DEFAULT_RULES 的 rule_id 全集须等于搬迁前的 56 条(防 YAML 丢/换规则)。

        搬迁自旧硬编码 DEFAULT_RULES tuple;若 YAML 写错(漏条/改 id),此断言 fail。
        比数量断言更强 —— 防止「数量对但换了一批」。
        """
        from shannon_core.code_index.sink_detector import DEFAULT_RULES
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
            "ts-sequelize-query",
            # 补充(vuln-range 三项目反哺):RestTemplate SSRF / vm / Pug / Angular XSS / needle
            "java-resttemplate-exchange", "java-resttemplate-getforobject",
            "ts-pug-compile", "ts-vm-runincontext",
            "ts-bypass-security-trust-html", "ts-needle-get",
            # sink 硬规则增强(Task 3+4):Java 全类别补齐 + execute 双语义
            "java-resttemplate-postforentity", "java-response-sendredirect",
            "java-url-openconnection", "java-httpclient-execute",
        }
        got = {r.rule_id for r in DEFAULT_RULES}
        assert got == expected, f"missing={expected-got} extra={got-expected}"


class TestIsEntryHint:
    def test_function_param_identifier(self):
        from shannon_core.code_index.sink_detector import is_entry_hint
        from shannon_core.code_index.models import FuncBlock
        block = FuncBlock(
            id="app.py:f:1", file_path="app.py", function_name="f",
            start_line=1, end_line=2, source_code="def f(user_id): pass",
            parameters=["user_id"], language="python",
        )
        assert is_entry_hint("user_id", block) is True

    def test_request_attr_python(self):
        from shannon_core.code_index.sink_detector import is_entry_hint
        from shannon_core.code_index.models import FuncBlock
        block = FuncBlock(
            id="app.py:f:1", file_path="app.py", function_name="f",
            start_line=1, end_line=2, source_code="", parameters=[], language="python",
        )
        assert is_entry_hint("request.args.get('id')", block) is True
        assert is_entry_hint("request.form['x']", block) is True
        assert is_entry_hint("request.json", block) is True

    def test_request_attr_express(self):
        from shannon_core.code_index.sink_detector import is_entry_hint
        from shannon_core.code_index.models import FuncBlock
        block = FuncBlock(
            id="app.ts:f:1", file_path="app.ts", function_name="f",
            start_line=1, end_line=2, source_code="", parameters=["req"], language="typescript",
        )
        assert is_entry_hint("req.params.id", block) is True
        assert is_entry_hint("req.body", block) is True
        assert is_entry_hint("req.query.x", block) is True

    def test_literal_not_hint(self):
        from shannon_core.code_index.sink_detector import is_entry_hint
        from shannon_core.code_index.models import FuncBlock
        block = FuncBlock(
            id="app.py:f:1", file_path="app.py", function_name="f",
            start_line=1, end_line=2, source_code="", parameters=[], language="python",
        )
        assert is_entry_hint("'literal string'", block) is False
        assert is_entry_hint("42", block) is False

    def test_local_var_not_hint(self):
        from shannon_core.code_index.sink_detector import is_entry_hint
        from shannon_core.code_index.models import FuncBlock
        block = FuncBlock(
            id="app.py:f:1", file_path="app.py", function_name="f",
            start_line=1, end_line=2, source_code="", parameters=["x"], language="python",
        )
        # 'data' is not a parameter — not a hint
        assert is_entry_hint("data", block) is False


class TestDetectSinksPython:
    def test_python_cursor_execute_hit(self):
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.parsers.python_parser import PythonParser
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
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.parsers.python_parser import PythonParser
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
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.parsers.python_parser import PythonParser
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
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.parsers.python_parser import PythonParser
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
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.parsers.python_parser import PythonParser
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
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.parsers.python_parser import PythonParser
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
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.parsers.python_parser import PythonParser
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
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.parsers.python_parser import PythonParser
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
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.parsers.python_parser import PythonParser
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
        from shannon_core.code_index.sink_detector import detect_sinks, SinkRule
        import re
        from shannon_core.code_index.parsers.python_parser import PythonParser
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
        from shannon_core.code_index.sink_detector import _build_dangerous_slots, SinkRule
        import re
        from shannon_core.code_index.models import FuncBlock
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
        from shannon_core.code_index.sink_detector import _build_dangerous_slots, SinkRule
        from shannon_core.code_index.models import FuncBlock
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
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.parsers.typescript_parser import TypeScriptParser
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
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.parsers.go_parser import GoParser
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
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.parsers.php_parser import PhpParser
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
        from shannon_core.code_index.sink_detector import DEFAULT_RULES
        ids = {r.rule_id for r in DEFAULT_RULES}
        for rid in ("py-django-raw", "py-sqlalchemy-text", "ts-knex-raw",
                    "ts-sequelize-query", "go-gorm-raw", "go-gorm-exec",
                    "java-jpa-createnativequery", "php-laravel-whereraw", "php-db-raw"):
            assert rid in ids, f"missing ORM Raw rule {rid}"

    def test_go_gorm_raw_detects_string_built_query(self):
        # Inline Go source with a string-built db.Raw(...) call + real detect_sinks.
        # Uses the same GoParser/parse_file/tempfile harness pattern as the
        # existing test_go_exec_command_hit above.
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.parsers.go_parser import GoParser
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
        from shannon_core.code_index.sink_detector import DEFAULT_RULES
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

        # java-jpa-createnativequery: bare callee, needs_review_default=True
        r = _rule("java-jpa-createnativequery")
        assert r.callee == "createNativeQuery"
        assert r.receiver_pattern is None
        assert r.needs_review_default is True


class TestSqlCommandWhitelistGuard:
    """Spec 改动 1.2 D — guard: SQL/COMMAND issue_types must stay in whitelist."""

    def test_sql_command_categories_in_whitelist(self):
        from shannon_core.code_index.finding_models import VALID_INJECTION_CATEGORIES
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
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.parsers.python_parser import PythonParser
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
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.parsers.python_parser import PythonParser
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
        from shannon_core.code_index.sink_detector import DEFAULT_RULES
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
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.parsers.typescript_parser import TypeScriptParser
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
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.parsers.typescript_parser import TypeScriptParser
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


class TestJavaSsrfCmdRedirectSinks:
    """Java SSRF/Command/Redirect 规则:rp ^(类型名)$ 失配修复(Java receiver 是实例变量/整链)+ 补全。"""

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
        assert "java-httpclient-execute" in rule_ids, "httpClient.execute 应命中 java-httpclient-execute(ssrf)"
        assert "java-stmt-execute" in rule_ids, "httpClient.execute 也命中 java-stmt-execute(sql,待 chain_verdict 否决)"
        ssrf = [s for s in sites if s.rule_id == "java-httpclient-execute"]
        assert ssrf[0].category == SinkCategory.SSRF
        assert ssrf[0].callee_receiver == "httpClient"
