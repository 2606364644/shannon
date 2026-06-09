"""Tests for sink_detector module and SinkCallSite model."""
from shannon_core.code_index.parameter_models import (
    SinkCallSite, DangerousSlot, SlotContext, SinkCategory, SinkType,
)


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
