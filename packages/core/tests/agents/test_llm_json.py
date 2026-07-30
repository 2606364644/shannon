"""repair_json_arguments 单测：把可能非法的 tool_call arguments 串修成合法 JSON。

防线1（bridge._on_invoke_set）与防线2（providers_openai 发包前清洗 messages）共用。
"""
from supernova_core.agents.llm_json import repair_json_arguments


def test_valid_json_passthrough():
    """合法 JSON 原样直通。"""
    assert repair_json_arguments('{"a": 1}') == '{"a": 1}'


def test_valid_array_passthrough():
    """array 根也是合法 JSON，直通。"""
    assert repair_json_arguments('[1, 2]') == '[1, 2]'


def test_markdown_fenced_repaired():
    """markdown 围栏包裹的 JSON 被抠出修复。"""
    assert repair_json_arguments('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_trailing_garbage_repaired():
    """JSON 后跟尾随文本：首{末}子串能 loads 即返回该子串。"""
    assert repair_json_arguments('{"a": 1} trailing text') == '{"a": 1}'


def test_truncated_returns_none():
    """截断（缺右括号）无法修复 → None。"""
    assert repair_json_arguments('{"architecture":') is None


def test_garbage_returns_none():
    """纯文本（全无 {/[）→ None。"""
    assert repair_json_arguments('not json at all') is None


def test_empty_string_returns_none():
    assert repair_json_arguments('') is None


def test_whitespace_only_returns_none():
    assert repair_json_arguments('   ') is None


def test_none_returns_none():
    assert repair_json_arguments(None) is None  # type: ignore[arg-type]
