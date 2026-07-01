"""规则 YAML 加载器 + 各 detector _build_* 的 fail-fast / 解析测试。

锁外部化不变量:YAML 是可信内部数据,未知枚举值必须 fail-fast(ValueError),
不回落(对比 sink_discovery_llm._to_category 的 LLM 输出容错)。反斜杠转义等价
锁 source_rules.yml 双引号串的反斜杠双写不被写错。
"""
import pytest

from shannon_core.code_index._rule_loader import DATA_DIR, load_yaml
from shannon_core.code_index import sink_detector, source_detector, sink_discovery_llm
from shannon_core.code_index.parameter_models import SlotContext


def test_data_dir_has_three_rule_files():
    assert (DATA_DIR / "sink_rules.yml").exists()
    assert (DATA_DIR / "source_rules.yml").exists()
    assert (DATA_DIR / "sink_candidates.yml").exists()


def test_load_yaml_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        load_yaml(DATA_DIR / "nonexistent.yml")


def test_build_sink_rules_unknown_category_raises():
    with pytest.raises(ValueError):
        sink_detector._build_sink_rules({"rules": [{
            "rule_id": "x", "languages": ["python"], "callee": "f",
            "receiver_pattern": None, "category": "not-a-category",
            "sink_subtype": "s", "dangerous_slots": [{"arg_index": 0, "slot": "sql_value"}],
        }]})


def test_build_sink_rules_unknown_slot_raises():
    with pytest.raises(ValueError):
        sink_detector._build_sink_rules({"rules": [{
            "rule_id": "x", "languages": ["python"], "callee": "f",
            "receiver_pattern": None, "category": "sql",
            "sink_subtype": "s", "dangerous_slots": [{"arg_index": 0, "slot": "not-a-slot"}],
        }]})


def test_build_sink_rules_null_receiver_and_slots():
    rules = sink_detector._build_sink_rules({"rules": [{
        "rule_id": "x", "languages": ["python"], "callee": "f",
        "receiver_pattern": None, "category": "sql",
        "sink_subtype": "s", "dangerous_slots": [{"arg_index": 0, "slot": "sql_value"}],
    }]})
    assert rules[0].receiver_pattern is None
    assert rules[0].dangerous_slots == ((0, SlotContext.SQL_VALUE),)
    assert rules[0].needs_review_default is False


def test_build_sink_rules_needs_review_default_true():
    rules = sink_detector._build_sink_rules({"rules": [{
        "rule_id": "x", "languages": ["python"], "callee": "f",
        "receiver_pattern": None, "category": "sql",
        "sink_subtype": "s", "needs_review_default": True,
        "dangerous_slots": [{"arg_index": 0, "slot": "sql_value"}],
    }]})
    assert rules[0].needs_review_default is True


def test_build_source_rules_unknown_source_type_raises():
    with pytest.raises(ValueError):
        source_detector._build_source_rules({"rules": [{
            "rule_id": "x", "languages": ["python"],
            "pattern": "x", "source_type": "not-a-source",
        }]})


def test_source_pattern_backslash_equivalence():
    """YAML 双引号串反斜杠解析 —— 验证 source pattern 功能等价(能匹配、捕获对)。

    原始 Python raw string 里 ['\"] 的反斜杠对 regex 是冗余的('"' 在字符集内无需转义);
    YAML 双引号串把 \" 解析为 ",自然得到更干净的 ['"]。两者 regex 等价。本测试锁功能
    等价(匹配行为 + group(1) 捕获),而非字符串完全相同 —— 防反斜杠写错导致 regex 坏。
    """
    rules = {r.rule_id: r for r in source_detector.DEFAULT_SOURCE_RULES}
    php = rules["php-get"].pattern
    # 单引号和双引号两种取用形式都必须匹配(证明 ['"] 字符集正确,反斜杠未误丢语义字符)
    assert php.search("$id = $_GET['id'];")
    assert php.search('$id = $_GET["id"];')
    assert php.search("$id = $_GET['userId'];").group(1) == "userId"
    # 关键转义(\. \w \$)regex 必需 —— YAML 双引号里双写不能丢,否则匹配错位
    gin = rules["go-gin-query"].pattern
    assert gin.search('c.Query("threshold")').group(1) == "threshold"


def test_build_sink_candidates_receivers_any_absent_is_none():
    groups = sink_discovery_llm._build_sink_candidates({"candidates": [
        {"languages": ["go"], "callees": ["Raw"]},                       # 无 receivers_any → None
        {"languages": ["typescript"], "callees": ["raw"], "receivers_any": ["knex", "db"]},
    ]})
    assert groups[0].receivers_any is None
    assert groups[1].receivers_any == ("knex", "db")
