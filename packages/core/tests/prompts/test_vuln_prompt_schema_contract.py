"""vuln prompt 字段表 ↔ collector submit_finding schema 一致性锁定。

根因背景（2026-08-20 follow-up）：移植时把 TS 原版 injectionFields（sink_call 族）
与 xssFields（sink_function 族）两套 schema 合并成 XSS 风格一套，而 prompt 保留了
TS 原版 injection 字段表——collector schema 作为 submit_finding 工具契约与 prompt
字段表给模型矛盾指令，authentication_required/accessible_routes 更被 pydantic
静默丢弃。本测试把「prompt 教的每个字段必须在 collector schema 声明」锁进 CI：
两处任何一侧漂移即红，防止双 SSOT 再分叉。
"""
import re
from pathlib import Path

import pytest

from supernova_core.collectors.vuln import make_vuln_sections

PROMPTS_DIR = Path(__file__).resolve().parents[4] / "prompts"

VULN_CLASSES = ["injection", "xss", "auth", "ssrf", "authz"]

# 行首缩进 + "field": 的 JSON-ish 键行（字段表格式；值内引号不在行首，不误匹配）
_FIELD_KEY_RE = re.compile(r'^\s*"([A-Za-z_]+)":', re.MULTILINE)


def _prompt_finding_fields(vuln_class: str) -> set[str]:
    text = (PROMPTS_DIR / f"vuln-{vuln_class}.txt").read_text(encoding="utf-8")
    block = re.search(
        r"<finding_submission>(.*?)</finding_submission>", text, re.DOTALL)
    assert block, f"vuln-{vuln_class}.txt missing <finding_submission> block"
    fields = set(_FIELD_KEY_RE.findall(block.group(1)))
    assert fields, f"vuln-{vuln_class}.txt finding_submission has no fields"
    return fields


def _schema_finding_fields(vuln_class: str) -> set[str]:
    submit = make_vuln_sections(vuln_class)[0]
    assert submit.tool_name == "submit_finding"
    return set(submit.json_schema["properties"])


@pytest.mark.parametrize("vuln_class", VULN_CLASSES)
def test_prompt_fields_declared_in_collector_schema(vuln_class: str):
    """prompt 字段表教的每个字段必须在 submit_finding schema 声明。

    prompt 是字段契约的权威（TS 原版 ∪ 移植增强，模型行为主驱动）；
    schema 缺字段 = 模型收到矛盾契约（prompt 说交 sink_call、工具 schema
    只认 sink_function），漂移即红。
    """
    undeclared = _prompt_finding_fields(vuln_class) - _schema_finding_fields(vuln_class)
    assert not undeclared, (
        f"vuln-{vuln_class}.txt teaches fields the submit_finding schema "
        f"does not declare: {sorted(undeclared)}"
    )


def test_injection_schema_keeps_ts_native_field_family():
    """injection 拥有 sink_call 族字段（TS 原版 injectionFields），与 xss
    （sink_function 族）是两套契约——防再被合并成 XSS 风格单套。"""
    inj = _schema_finding_fields("injection")
    xss = _schema_finding_fields("xss")
    assert {"sink_call", "slot_type", "sanitization_observed",
            "concat_occurrences"} <= inj
    assert "sink_function" in xss and "sink_call" not in xss
    assert "sink_call" in inj and "sink_function" not in inj
