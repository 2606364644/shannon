from supernova_core.collectors.base import (
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


def _append_schema(tool="set_eps", key="eps"):
    return SectionSchema(
        tool_name=tool,
        section_key=key,
        description="append tool",
        json_schema={"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
        mode="append",
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


# ── append 语义（mode="append" section 支持多次调累积） ──────────────────────

def test_append_section_accumulates():
    c = CollectorBase([_append_schema()])
    c.append_section("set_eps", {"x": "a"})
    c.append_section("set_eps", {"x": "b"})
    out = c.get_all()
    assert out == {"eps": [{"x": "a"}, {"x": "b"}]}
    assert len(out["eps"]) == 2


def test_append_section_no_duplicate_error():
    # append 工具多次调不抛 DuplicateCallError（与 set_* write-once 相对）
    c = CollectorBase([_append_schema()])
    c.append_section("set_eps", {"x": "a"})
    c.append_section("set_eps", {"x": "b"})  # 不抛
    c.append_section("set_eps", {"x": "c"})  # 不抛
    assert len(c.get_all()["eps"]) == 3


def test_append_section_rejects_set_mode_section():
    # 对 mode="set" 的 section 调 append_section 抛错（误用保护）
    c = CollectorBase([_schema()])  # 默认 mode="set"
    try:
        c.append_section("set_alpha", {"x": "v"})
    except (TypeError, ValueError):
        pass
    else:
        raise AssertionError("expected TypeError/ValueError when append_section on mode=set section")


def test_set_section_rejects_append_mode_section():
    # 对 mode="append" 的 section 调 set_section 抛错（误用保护）
    c = CollectorBase([_append_schema()])
    try:
        c.set_section("set_eps", {"x": "v"})
    except (TypeError, ValueError):
        pass
    else:
        raise AssertionError("expected TypeError/ValueError when set_section on mode=append section")


def test_append_empty_not_in_get_all():
    # mode="append" section 未 append 过 → get_all() 不含该 key（renderer 补 placeholder）
    c = CollectorBase([_append_schema(), _schema()])
    c.set_section("set_alpha", {"x": "v"})
    out = c.get_all()
    assert "eps" not in out
    assert out == {"alpha": {"x": "v"}}


def test_get_call_status_append():
    c = CollectorBase([_append_schema()])
    # 未调 → skipped
    assert c.get_call_status() == {"set_eps": "skipped"}
    c.append_section("set_eps", {"x": "a"})
    # 调过 → called（不论次数）
    assert c.get_call_status() == {"set_eps": "called"}
    c.append_section("set_eps", {"x": "b"})
    assert c.get_call_status() == {"set_eps": "called"}  # 仍 called


def test_get_all_append_returns_deep_copy():
    c = CollectorBase([_append_schema()])
    c.append_section("set_eps", {"x": "a"})
    out = c.get_all()
    out["eps"][0]["x"] = "mutated"
    assert c.get_all()["eps"][0]["x"] == "a"  # internal untouched


def test_set_mode_unchanged_for_existing_collectors():
    # 回归：PreReconCollector 的 section_schemas 全 mode="set"（默认值）
    from supernova_core.collectors.pre_recon import PreReconCollector

    c = PreReconCollector()
    for s in c.section_schemas:
        assert s.mode == "set", f"{s.tool_name} should be mode=set by default"
    # set_section/get_call_status 行为不变
    c.set_section("set_executive_summary", {"text": "hi"})
    assert c.get_all() == {"executive_summary": {"text": "hi"}}
    assert c.get_call_status()["set_executive_summary"] == "called"
    assert c.get_call_status()["set_application_intelligence"] == "skipped"


def test_append_section_unknown_tool_raises_keyerror():
    c = CollectorBase([_append_schema()])
    try:
        c.append_section("set_nope", {"x": "v"})
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError for unknown append tool")
