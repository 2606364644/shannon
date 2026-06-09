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
