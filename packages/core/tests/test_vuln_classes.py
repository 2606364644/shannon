"""vuln 类选择纯函数：优先级链 CLI > env > YAML > 默认，集中可测、黑白盒可复用。"""
import pytest

from supernova_core.config.vuln_selection import (
    InvalidVulnClass,
    resolve_vuln_classes,
    select_vuln_classes,
)
from supernova_core.models.config import ALL_VULN_CLASSES


class TestResolveVulnClasses:
    """合并字符串来源（CLI/env）：CLI 优先，都空返回 None。"""

    def test_cli_takes_precedence_over_env(self):
        assert resolve_vuln_classes("injection", "xss") == ["injection"]

    def test_env_used_when_cli_absent(self):
        assert resolve_vuln_classes(None, "xss,auth") == ["xss", "auth"]

    def test_cli_empty_string_falls_through_to_env(self):
        assert resolve_vuln_classes("", "xss") == ["xss"]

    def test_both_none_returns_none(self):
        assert resolve_vuln_classes(None, None) is None

    def test_both_empty_returns_none(self):
        assert resolve_vuln_classes("", "") is None

    def test_comma_split_and_trim(self):
        assert resolve_vuln_classes(" injection , xss ", None) == ["injection", "xss"]

    def test_empty_tokens_dropped(self):
        assert resolve_vuln_classes("injection,,xss,", None) == ["injection", "xss"]

    def test_duplicates_deduped_order_preserved(self):
        assert resolve_vuln_classes("xss,injection,xss", None) == ["xss", "injection"]

    def test_invalid_class_raises_with_legal_list(self):
        with pytest.raises(InvalidVulnClass) as exc:
            resolve_vuln_classes("injection,foo", None)
        msg = str(exc.value)
        assert "foo" in msg
        for legal in ALL_VULN_CLASSES:
            assert legal in msg

    def test_invalid_in_env_raises(self):
        with pytest.raises(InvalidVulnClass):
            resolve_vuln_classes(None, "nope")


class TestSelectVulnClasses:
    """合并 list 来源：override（CLI/env 已解析）> YAML > 默认全跑。"""

    def test_override_takes_precedence(self):
        assert select_vuln_classes(["injection"], ["xss"]) == ["injection"]

    def test_empty_override_falls_through_to_yaml(self):
        # 空列表 falsy，视同未指定
        assert select_vuln_classes([], ["xss"]) == ["xss"]

    def test_none_override_uses_yaml(self):
        assert select_vuln_classes(None, ["xss", "auth"]) == ["xss", "auth"]

    def test_both_none_uses_default(self):
        assert select_vuln_classes(None, None) == list(ALL_VULN_CLASSES)

    def test_default_covers_all_five_classes(self):
        result = select_vuln_classes(None, None)
        assert set(result) == {"injection", "xss", "auth", "authz", "ssrf"}

    def test_default_not_mutated(self):
        before = list(ALL_VULN_CLASSES)
        select_vuln_classes(None, None)
        select_vuln_classes(["xss"], None)
        assert ALL_VULN_CLASSES == before

    def test_returns_copy_not_alias(self):
        yaml_vuln = ["xss"]
        result = select_vuln_classes(None, yaml_vuln)
        result.append("injection")
        assert yaml_vuln == ["xss"]
