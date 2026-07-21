import pytest

from supernova_core.agents.openai_output_schema import (
    RawJsonSchemaOutputSchema,
    StructuredOutputParseError,
    _extract_json_payload,
)


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


def test_extract_json_payload_plain():
    assert _extract_json_payload('{"k": "v"}') == '{"k": "v"}'


def test_extract_json_payload_markdown_fence_with_lang():
    assert _extract_json_payload('```json\n{"k": "v"}\n```') == '{"k": "v"}'


def test_extract_json_payload_markdown_fence_no_lang():
    assert _extract_json_payload('```\n{"k": "v"}\n```') == '{"k": "v"}'


def test_extract_json_payload_leading_prose():
    text = '分析完成，结论如下：\n{"vulnerabilities": []}\n以上。'
    assert _extract_json_payload(text) == '{"vulnerabilities": []}'


def test_extract_json_payload_empty_or_blank():
    assert _extract_json_payload("") is None
    assert _extract_json_payload("   ") is None


def test_extract_json_payload_no_braces():
    assert _extract_json_payload("纯叙述收尾，没有 JSON") is None


def test_validate_json_parses_markdown_fence():
    s = RawJsonSchemaOutputSchema({"type": "object"})
    assert s.validate_json('```json\n{"k": "v"}\n```') == {"k": "v"}


def test_validate_json_parses_leading_prose():
    s = RawJsonSchemaOutputSchema({"type": "object"})
    assert s.validate_json('结论如下：{"k": "v"}') == {"k": "v"}


def test_validate_json_raises_structured_output_parse_error_on_prose():
    s = RawJsonSchemaOutputSchema({"type": "object"})
    with pytest.raises(StructuredOutputParseError):
        s.validate_json("纯叙述收尾，没有 JSON")


def test_structured_output_parse_error_not_model_behavior_error():
    """不变量：不继承 ModelBehaviorError，避免被 openai-agents error handler 误吞。"""
    from agents import ModelBehaviorError
    assert not issubclass(StructuredOutputParseError, ModelBehaviorError)
    assert issubclass(StructuredOutputParseError, Exception)
