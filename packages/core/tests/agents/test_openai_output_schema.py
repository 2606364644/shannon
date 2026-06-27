from shannon_core.agents.openai_output_schema import RawJsonSchemaOutputSchema


def test_is_plain_text_false_when_schema_given():
    schema = {"type": "object", "properties": {"k": {"type": "string"}}}
    s = RawJsonSchemaOutputSchema(schema)
    assert s.is_plain_text() is False


def test_json_schema_returns_raw_schema_unchanged():
    """B2: 直接持有 Claude 风格 JSON Schema，不 round-trip Pydantic。"""
    schema = {"type": "object", "properties": {"verdict": {"type": "string"}}, "required": ["verdict"]}
    s = RawJsonSchemaOutputSchema(schema)
    assert s.json_schema() == schema


def test_is_strict_json_schema_false_for_glm_compat():
    """GLM 第三方 endpoint 用 non-strict，避免 strict 模式拒收。"""
    s = RawJsonSchemaOutputSchema({"type": "object"})
    assert s.is_strict_json_schema() is False


def test_validate_json_parses_valid_json():
    s = RawJsonSchemaOutputSchema({"type": "object"})
    assert s.validate_json('{"k": "v"}') == {"k": "v"}


def test_validate_json_raises_on_invalid():
    import pytest
    s = RawJsonSchemaOutputSchema({"type": "object"})
    with pytest.raises(Exception):
        s.validate_json("not json")


def test_name_is_stable():
    s = RawJsonSchemaOutputSchema({"type": "object"})
    assert isinstance(s.name(), str)
    assert len(s.name()) > 0
