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

from supernova_core.collectors.vuln import _FINDING_SCHEMAS, _finding_props, make_vuln_sections
from supernova_core.models.queue_schemas import (
    AuthVulnerability,
    AuthzVulnerability,
    BaseVulnerability,
    InjectionVulnerability,
    SsrfVulnerability,
    XssVulnerability,
)

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


# 报告可读性改造（spec 2026-08-25 Task 7）：报告卡片字段双向锁定——
# prompt 字段表所教 + collector schema 声明，两侧任一漂移即红。
# cvss/owasp_category 是终审遗留 F5 复活（2026-08-25：BaseVulnerability 有、
# 渲染层也渲染，但工具契约从不教 ⇒ 死字段），一并纳入锁定。
_REPORT_CARD_FIELDS = ["severity", "impact", "remediation", "cwe_id", "cvss",
                       "owasp_category"]


@pytest.mark.parametrize("vuln_class", VULN_CLASSES)
def test_report_card_fields_in_prompt_and_schema(vuln_class):
    """报告卡片字段（severity/impact/remediation/cwe_id/cvss/owasp_category）
    必须同时出现在 prompt 字段表与 submit_finding schema（optional 字段，
    不动 _FINDING_BASE_REQUIRED）。

    反向锁定的意义：只进 schema 不进 prompt ⇒ 模型不知道要交（字段永远空）；
    只进 prompt 不进 schema ⇒ 上面的方向一测试会红（同 2026-08-20
    authentication_required 被静默丢弃的教训）。"""
    missing_prompt = [
        f for f in _REPORT_CARD_FIELDS
        if f not in _prompt_finding_fields(vuln_class)
    ]
    assert not missing_prompt, (
        f"vuln-{vuln_class}.txt 字段表缺报告卡片字段（模型不会被教到）: "
        f"{missing_prompt}"
    )
    missing_schema = [
        f for f in _REPORT_CARD_FIELDS
        if f not in _schema_finding_fields(vuln_class)
    ]
    assert not missing_schema, (
        f"submit_finding schema 缺报告卡片字段（collector 会静默丢弃）: "
        f"{missing_schema}"
    )


# 双轨 PoC 前置条件信号（2026-08-27）：authentication_required 派生 PoC 的
# preconditions/Authorization 头，auth 9/9、authz 6/6、ssrf 2/2 卡曾整类缺失
# ——prompt 不教 + schema 不声明，模型无从交出。全类双侧锁定。
@pytest.mark.parametrize("vuln_class", VULN_CLASSES)
def test_authentication_required_in_prompt_and_schema(vuln_class):
    """authentication_required 双侧锁定（PoC preconditions 的唯一信号源）。"""
    assert "authentication_required" in _prompt_finding_fields(vuln_class), (
        f"vuln-{vuln_class}.txt 字段表缺 authentication_required"
        f"（模型不会被教到，PoC preconditions 整类为空）"
    )
    assert "authentication_required" in _schema_finding_fields(vuln_class), (
        f"submit_finding schema 缺 authentication_required"
        f"（collector 会静默丢弃）"
    )


# 各 vuln class 落盘解析用的 pydantic 子类（queue_schemas._CLASS_ADAPTERS 同款
# 映射；子类 model_fields 含继承自 BaseVulnerability 的字段）。
_CLASS_MODELS = {
    "injection": InjectionVulnerability,
    "xss": XssVulnerability,
    "auth": AuthVulnerability,
    "ssrf": SsrfVulnerability,
    "authz": AuthzVulnerability,
}


def test_schema_fields_land_in_pydantic_models():
    """工具 schema 声明的每个 finding 字段必须能落进 pydantic 落盘模型。

    2026-08-20 教训（authentication_required 被静默丢弃）：L0 工具 schema 教了、
    落盘 pydantic 模型没字段 ⇒ model_validate 静默丢弃、字段永远空。双层锁定：
    1. 共通 props（_finding_props 基线块，含 cvss/owasp_category）⊆
       BaseVulnerability.model_fields——报告卡片字段须进基类，全 class 统一落盘；
    2. 各 class schema 全量 properties ⊆ 对应子类 model_fields——injection 的
       sink_call 族等 class 特有字段按设计落在子类（不在基类），由子类兜住。
    """
    dropped_base = set(_finding_props({})) - set(BaseVulnerability.model_fields)
    assert not dropped_base, (
        f"_finding_props 共通字段未进 BaseVulnerability（pydantic 会静默丢弃）: "
        f"{sorted(dropped_base)}"
    )
    for vuln_class, schema in _FINDING_SCHEMAS.items():
        model = _CLASS_MODELS[vuln_class]
        dropped = set(schema["properties"]) - set(model.model_fields)
        assert not dropped, (
            f"{vuln_class} submit_finding schema 字段未进落盘模型 "
            f"{model.__name__}（pydantic 会静默丢弃）: {sorted(dropped)}"
        )


# spec 2026-08-26 §5：taint 三类（inj/xss/ssrf）补接口/参数列表字段——受影响入口
# 节的结构化数据源（接口列表行 + 外部参数）。双侧锁定同 _REPORT_CARD_FIELDS 口径。
_TAINT_ENDPOINT_FIELDS = ["endpoints", "affected_parameters"]
_TAINT_CLASSES = ["injection", "xss", "ssrf"]


@pytest.mark.parametrize("vuln_class", _TAINT_CLASSES)
def test_taint_endpoint_fields_in_prompt_and_schema(vuln_class):
    """endpoints/affected_parameters 必须同时出现在 taint 类 prompt 字段表与
    submit_finding schema——只进 schema 不进 prompt ⇒ 模型不知道要交（字段
    永远空，LLM taint 卡受影响入口节全缺的老问题）；只进 prompt 不进 schema
    ⇒ 方向一测试红（collector 静默丢弃）。"""
    for f in _TAINT_ENDPOINT_FIELDS:
        assert f in _prompt_finding_fields(vuln_class), (
            f"vuln-{vuln_class}.txt 字段表缺 {f}（模型不会被教到）")
        assert f in _schema_finding_fields(vuln_class), (
            f"submit_finding schema 缺 {f}（collector 会静默丢弃）")


def test_taint_endpoint_fields_land_in_base_model():
    """endpoints/affected_parameters 声明在 BaseVulnerability（渲染层 getattr
    读取），全 class 落盘统一。"""
    for f in _TAINT_ENDPOINT_FIELDS:
        assert f in BaseVulnerability.model_fields
