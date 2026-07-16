from shannon_core.collectors.base import (
    CollectorBase,
    DuplicateCallError,
    SectionSchema,
)


def _schema(tool="set_alpha", key="alpha"):
    return SectionSchema(
        tool_name=tool,
        section_key=key,
        description="alpha tool",
        json_schema={"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
    )


def test_set_section_stores_payload_keyed_by_section_key():
    c = CollectorBase([_schema()])
    c.set_section("set_alpha", {"x": "v"})
    assert c.get_all() == {"alpha": {"x": "v"}}


def test_set_section_is_write_once_duplicate_raises():
    c = CollectorBase([_schema()])
    c.set_section("set_alpha", {"x": "first"})
    try:
        c.set_section("set_alpha", {"x": "second"})
    except DuplicateCallError:
        pass
    else:
        raise AssertionError("expected DuplicateCallError on second call")
    assert c.get_all() == {"alpha": {"x": "first"}}   # first call wins


def test_skipped_section_omitted_from_get_all():
    c = CollectorBase([_schema(), _schema("set_beta", "beta")])
    c.set_section("set_alpha", {"x": "v"})
    assert c.get_all() == {"alpha": {"x": "v"}}      # beta absent = skipped


def test_get_call_status_reports_called_or_skipped():
    c = CollectorBase([_schema(), _schema("set_beta", "beta")])
    c.set_section("set_alpha", {"x": "v"})
    assert c.get_call_status() == {"set_alpha": "called", "set_beta": "skipped"}


def test_tool_names_and_section_schemas_preserve_declaration_order():
    a, b = _schema(), _schema("set_beta", "beta")
    c = CollectorBase([a, b])
    assert c.tool_names() == ["set_alpha", "set_beta"]
    assert c.section_schemas == [a, b]


def test_set_section_rejects_unknown_tool():
    c = CollectorBase([_schema()])
    try:
        c.set_section("set_nope", {"x": "v"})
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError for unknown tool")


def test_get_all_returns_copy_not_internal_state():
    c = CollectorBase([_schema()])
    c.set_section("set_alpha", {"x": "v"})
    out = c.get_all()
    out["alpha"]["x"] = "mutated"
    assert c.get_all() == {"alpha": {"x": "v"}}   # internal untouched
