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


def test_extract_json_payload_glm_markdown_with_code_block():
    """GLM 真实形态：Markdown 标题 + 分析 + ```java 代码示例(含{}) + 末尾 ```json。

    旧实现 find('{') 落在 ```java 代码块里，提取出夹 java 代码的畸形子串导致
    json.loads 失败（taint-analysis WARNING 刷屏根因）。增强后从后往前取合法 fence。
    """
    text = (
        "# 污点传播分析\n\n"
        "## 分析过程\n\n"
        "函数数据流：\n\n"
        "```java\nprivate void fetchItems(String ip) {\n    Assert.notNull(ip);\n}\n```\n\n"
        "ip 直达 sink，结果：\n\n"
        '```json\n{"tainted_params":["ip"],"propagation_paths":[]}\n```'
    )
    import json as _json
    payload = _extract_json_payload(text)
    assert payload is not None
    assert _json.loads(payload) == {"tainted_params": ["ip"], "propagation_paths": []}


def test_extract_json_payload_multiple_fences_takes_last_valid():
    """多个 fence 时从后往前取首个合法 JSON（前面 ```java 非 JSON 跳过）。"""
    text = (
        "```python\nnot json here\n```\n"
        '```json\n{"valid": true}\n```'
    )
    import json as _json
    assert _json.loads(_extract_json_payload(text)) == {"valid": True}


def test_extract_json_payload_array_root():
    """JSON array 根（sink/source discovery 返回 array）。

    旧实现 find('{') 漏掉 array，rfind('}') 落到最后一个元素，把 [{...}] 截断成
    单个 object（丢 [...] 包裹），下游 [d for d in data if isinstance(d,dict)] 遍历
    dict 的 keys 返回空 -> 软 sink 召回全丢。增强后正确返回 array。
    """
    import json as _json
    text = "```json\n[{\"sink\":\"exec\",\"line\":10},{\"sink\":\"query\",\"line\":20}]\n```"
    payload = _extract_json_payload(text)
    assert payload is not None
    data = _json.loads(payload)
    assert isinstance(data, list) and len(data) == 2


def test_extract_json_payload_array_root_with_braces_in_prose():
    """array 根 + 前导叙述含 {}（旧实现截断 bug 的危险形态）。"""
    import json as _json
    text = "函数 foo() {} 有问题。\n```json\n[{\"sink\":\"exec\"}]\n```"
    payload = _extract_json_payload(text)
    assert payload is not None
    data = _json.loads(payload)
    assert isinstance(data, list) and data == [{"sink": "exec"}]


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


def test_validate_json_converts_invalid_escape_to_structured_output_parse_error():
    """含 {} 但 JSON 语法坏（非法反斜杠转义，如正则 \\d、Windows 路径 \\U）应转成
    StructuredOutputParseError 走 L1 轻量重输兜底，而非裸 json.JSONDecodeError 冒泡成
    AgentExecutionError retryable（2026-07-22 auth-vuln ``Invalid \\escape`` 失败根因）。

    _extract_json_payload 能提取到 candidate（fence/叙述剥离不关心转义），故失败发生在
    json.loads 这一步——此分支此前未捕获 JSONDecodeError，是 L0 容错的盲区。
    """
    s = RawJsonSchemaOutputSchema({"type": "object"})
    # GLM 在 JSON 字符串值里写了正则元字符 \d + Windows 路径 \U（均为非法 JSON 转义）
    bad = r'{"regex": "\d+", "path": "C:\Users\admin"}'
    with pytest.raises(StructuredOutputParseError):
        s.validate_json(bad)


def test_structured_output_parse_error_not_model_behavior_error():
    """不变量：不继承 ModelBehaviorError，避免被 openai-agents error handler 误吞。"""
    from agents import ModelBehaviorError
    assert not issubclass(StructuredOutputParseError, ModelBehaviorError)
    assert issubclass(StructuredOutputParseError, Exception)
