"""llm_json 单测：repair_json_arguments（tool_call arguments 修复）+ repair_truncated_json（流截断修复）。"""
import json

import pytest

from supernova_core.agents.llm_json import repair_json_arguments, repair_truncated_json


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


# ── repair_truncated_json（spec 2026-08-19 §3.1：网关流中断兜底）─────────────


def _queue(n: int) -> str:
    """构造 n 条 findings 的 exploitation queue JSON（模拟 vuln agent 最终消息）。"""
    vulns = [
        {"ID": f"AUTH-VULN-{i:02d}", "title": f"t{i}",
         "notes": f"notes {i} with }} brace and \" quote"}
        for i in range(1, n + 1)
    ]
    return json.dumps({"vulnerabilities": vulns}, ensure_ascii=False)


def test_truncated_mid_12th_element_recovers_11():
    """截断在第 12 条元素内部（ID 字符串中途）→ 救回前 11 条完整元素。"""
    full = _queue(12)
    truncated = full[: full.index('"AUTH-VULN-12"') + 5]  # 第 12 条 ID 字符串中途
    with pytest.raises(json.JSONDecodeError):
        json.loads(truncated)  # 前置：截断串本身必须是坏 JSON（否则用例失真）
    repaired = repair_truncated_json(truncated)
    assert repaired is not None
    data = json.loads(repaired)
    assert len(data["vulnerabilities"]) == 11
    assert data["vulnerabilities"][-1]["ID"] == "AUTH-VULN-11"


def test_truncated_inside_string_literal_drops_partial_element():
    """截断在第 12 条 notes 字符串中间 → 残缺元素丢弃，救回 11 条。"""
    full = _queue(12)
    truncated = full[: full.index("notes 12") + 4]  # notes 值字符串中途
    repaired = repair_truncated_json(truncated)
    assert repaired is not None
    data = json.loads(repaired)
    assert len(data["vulnerabilities"]) == 11
    assert data["vulnerabilities"][-1]["ID"] == "AUTH-VULN-11"


def test_truncated_object_root_closing_brace_only():
    """object 根只缺闭合括号（尾部值完整，如 `{"a": 1, "b": 2`）→ 补全返回。"""
    assert repair_truncated_json('{"count": 12') == '{"count": 12}'


def test_truncated_array_root_recovers_elements():
    """array 根（`[...]`）尾部截断 → 丢残缺元素、补 `]`。"""
    full = json.dumps([{"ID": "A-1"}, {"ID": "A-2"}, {"ID": "A-3"}])
    truncated = full[: full.index('"A-3"')]
    repaired = repair_truncated_json(truncated)
    assert repaired is not None
    assert json.loads(repaired) == [{"ID": "A-1"}, {"ID": "A-2"}]


def test_truncated_before_first_element_returns_none():
    """截断在首个完整元素之前（无元素可救）→ None（走 validator 防线重试）。"""
    truncated = '{"vulnerabilities": [{"ID": "AU'
    assert repair_truncated_json(truncated) is None


def test_complete_json_returns_none():
    """完整 JSON 不归本函数管 → None（调用方已 loads 成功）。"""
    assert repair_truncated_json(_queue(12)) is None
    assert repair_truncated_json('{"a": 1}') is None


def test_garbage_and_empty_return_none():
    """纯文本 / 空串 / None → None。"""
    assert repair_truncated_json("not json at all") is None
    assert repair_truncated_json("") is None
    assert repair_truncated_json(None) is None


def test_truncated_after_nested_container_drop_partial_element():
    """截断在元素内嵌套容器闭合后（元素 `}` 未到）→ 部分元素丢弃，只救完整元素。

    final review Important #1：该形态的嵌套 pop 点不是元素完整边界，
    补全后会救出缺字段的部分元素（如缺 notes），违反 spec「元素内部
    截断连同残缺元素丢弃」。修复后只认元素完整/根层边界 candidate。
    """
    full = json.dumps({"vulnerabilities": [
        {"ID": "V-01", "title": "t1", "notes": "n1"},
        {"ID": "V-02", "title": "t2", "notes": "n2"},
        {"ID": "V-03", "title": "t3", "notes": "n3",
         "evidence": {"steps": ["a", "b"]}},
    ]}, ensure_ascii=False)
    # 截断在 V-03 的 evidence 嵌套 object 闭合后、V-03 自身 `}` 前
    truncated = full[: full.index('"evidence": {"steps": ["a", "b"]}') + len('"evidence": {"steps": ["a", "b"]}') - 1]
    with pytest.raises(json.JSONDecodeError):
        json.loads(truncated)  # 前置：截断串是坏 JSON
    repaired = repair_truncated_json(truncated)
    assert repaired is not None
    data = json.loads(repaired)
    assert len(data["vulnerabilities"]) == 2  # 部分的 V-03 被丢弃
    assert data["vulnerabilities"][-1]["ID"] == "V-02"


def test_truncated_unclosed_json_fence_recovered():
    """未闭合 ```json fence 内的截断 payload（_extract_json_payload obj_sub 半截
    汇入链路，spec §6「围栏内提取后半截」）→ 同样救回完整元素。"""
    full = _queue(3)
    fenced = "Here is the queue:\n```json\n" + full
    truncated = fenced[: len("Here is the queue:\n```json\n") + full.index('"AUTH-VULN-03"') + 5]
    repaired = repair_truncated_json(truncated)
    assert repaired is not None
    data = json.loads(repaired)
    assert len(data["vulnerabilities"]) == 2
    assert data["vulnerabilities"][-1]["ID"] == "AUTH-VULN-02"
