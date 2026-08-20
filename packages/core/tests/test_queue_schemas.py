import json
from supernova_core.models.queue_schemas import (
    BaseVulnerability, InjectionVulnerability, XssVulnerability,
    AuthVulnerability, SsrfVulnerability, AuthzVulnerability,
    LenientParseResult, VulnerabilityQueue,
)

def test_base_vulnerability_required_fields():
    v = BaseVulnerability(
        ID="INJECTION-VULN-001",
        vulnerability_type="SQL Injection",
        externally_exploitable=True,
        confidence="high",
    )
    assert v.ID == "INJECTION-VULN-001"
    assert v.notes is None

def test_injection_vulnerability():
    v = InjectionVulnerability(
        ID="INJECTION-VULN-001",
        vulnerability_type="SQL Injection",
        externally_exploitable=True,
        confidence="high",
        source="user input",
        path="/api/users",
        sink_call="sqlite3.execute",
        mismatch_reason="No parameterized query",
    )
    assert v.sink_call == "sqlite3.execute"

def test_xss_vulnerability():
    v = XssVulnerability(
        ID="XSS-VULN-001",
        vulnerability_type="Reflected XSS",
        externally_exploitable=True,
        confidence="medium",
        sink_function="innerHTML",
        path="/search",
    )
    assert v.sink_function == "innerHTML"

def test_auth_vulnerability():
    v = AuthVulnerability(
        ID="AUTH-VULN-001",
        vulnerability_type="Broken Authentication",
        externally_exploitable=True,
        confidence="high",
        source_endpoint="/api/login",
        missing_defense="No rate limiting",
        exploitation_hypothesis="Brute force possible",
    )
    assert v.missing_defense == "No rate limiting"

def test_ssrf_vulnerability():
    v = SsrfVulnerability(
        ID="SSRF-VULN-001",
        vulnerability_type="SSRF",
        externally_exploitable=True,
        confidence="high",
        vulnerable_parameter="url",
    )
    assert v.vulnerable_parameter == "url"

def test_authz_vulnerability():
    v = AuthzVulnerability(
        ID="AUTHZ-VULN-001",
        vulnerability_type="IDOR",
        externally_exploitable=True,
        confidence="high",
        endpoint="/api/users/{id}",
        guard_evidence="No ownership check",
        side_effect="Access other users' data",
    )
    assert v.guard_evidence == "No ownership check"

def test_vulnerability_queue():
    queue = VulnerabilityQueue(vulnerabilities=[])
    assert len(queue.vulnerabilities) == 0

def test_vulnerability_queue_json_roundtrip():
    v = InjectionVulnerability(
        ID="INJECTION-VULN-001",
        vulnerability_type="SQL Injection",
        externally_exploitable=True,
        confidence="high",
        sink_call="execute",
    )
    queue = VulnerabilityQueue(vulnerabilities=[v])
    json_str = queue.model_dump_json(indent=2)
    parsed = json.loads(json_str)
    assert parsed["vulnerabilities"][0]["ID"] == "INJECTION-VULN-001"
    assert parsed["vulnerabilities"][0]["sink_call"] == "execute"

def test_queue_json_matches_ts_format():
    v = InjectionVulnerability(
        ID="INJECTION-VULN-001",
        vulnerability_type="SQL Injection",
        externally_exploitable=True,
        confidence="high",
        source="query param",
        path="/api/search",
        sink_call="db.execute",
        mismatch_reason="String concatenation in query",
    )
    queue = VulnerabilityQueue(vulnerabilities=[v])
    data = json.loads(queue.model_dump_json())
    entry = data["vulnerabilities"][0]
    assert "ID" in entry
    assert "vulnerability_type" in entry
    assert "externally_exploitable" in entry
    assert "confidence" in entry
    assert "source" in entry
    assert "path" in entry
    assert "sink_call" in entry
    assert "mismatch_reason" in entry


def test_parse_lenient_standard_object():
    content = VulnerabilityQueue(vulnerabilities=[
        InjectionVulnerability(
            ID="INJ-1", vulnerability_type="SQLi",
            externally_exploitable=True, confidence="high",
        ),
    ]).model_dump_json()
    result = VulnerabilityQueue.parse_lenient(content)
    assert result.original_form == "object"
    assert result.warnings == []
    assert len(result.queue.vulnerabilities) == 1
    assert result.queue.vulnerabilities[0].ID == "INJ-1"


def test_parse_lenient_wraps_bare_list():
    content = json.dumps([
        {"ID": "AUTH-1", "vulnerability_type": "Auth", "externally_exploitable": True, "confidence": "high"},
        {"ID": "AUTH-2", "vulnerability_type": "Auth", "externally_exploitable": True, "confidence": "medium"},
    ])
    result = VulnerabilityQueue.parse_lenient(content)
    assert result.original_form == "bare_list"
    assert any("bare-list" in w for w in result.warnings)
    assert len(result.queue.vulnerabilities) == 2
    assert result.queue.vulnerabilities[0].ID == "AUTH-1"


def test_parse_lenient_invalid_json():
    result = VulnerabilityQueue.parse_lenient("{not valid json")
    assert result.original_form == "invalid_json"
    assert len(result.queue.vulnerabilities) == 0
    assert any("invalid json" in w for w in result.warnings)


def test_parse_lenient_object_without_vulnerabilities_key():
    result = VulnerabilityQueue.parse_lenient(json.dumps({"meta": "no queue here"}))
    assert result.original_form == "object_no_key"
    assert len(result.queue.vulnerabilities) == 0
    assert any("vulnerabilities" in w for w in result.warnings)


def test_parse_lenient_drops_malformed_entries_keeps_good():
    content = json.dumps([
        {"ID": "GOOD-1", "vulnerability_type": "Auth", "externally_exploitable": True, "confidence": "high"},
        {"missing": "required fields"},
        {"ID": "GOOD-2", "vulnerability_type": "Auth", "externally_exploitable": True, "confidence": "low"},
    ])
    result = VulnerabilityQueue.parse_lenient(content)
    ids = [v.ID for v in result.queue.vulnerabilities]
    assert ids == ["GOOD-1", "GOOD-2"]
    assert any("dropped" in w for w in result.warnings)


def test_parse_lenient_vulnerabilities_not_a_list():
    content = json.dumps({"vulnerabilities": "not a list"})
    result = VulnerabilityQueue.parse_lenient(content)
    assert len(result.queue.vulnerabilities) == 0
    assert result.warnings  # some warning surfaced


def test_parse_lenient_returns_lenient_parse_result():
    result = VulnerabilityQueue.parse_lenient("[]")
    assert isinstance(result, LenientParseResult)
    assert hasattr(result, "queue")
    assert hasattr(result, "warnings")
    assert hasattr(result, "original_form")


def test_parse_lenient_never_raises_on_non_str_input():
    for bad in (None, 123, 42.0):
        result = VulnerabilityQueue.parse_lenient(bad)
        assert isinstance(result, LenientParseResult)
        assert result.original_form == "invalid_json"
        assert len(result.queue.vulnerabilities) == 0
        assert result.warnings  # some warning surfaced


# --- regression: crAPI-20260731 injection 渲染为"渲染错误"占位 ---
# injection vuln agent 输出的字段是 XSS 风格(sink_function/render_context/...),
# smart-union 把它误判为 XssVulnerability → render_injection_entry 访问 sink_call 崩。
# parse_lenient 必须支持按 class 强制解析成对应子类型。

def _injection_entry_with_llm_fields():
    """真实 crAPI injection queue 的 entry 形态:LLM 输出 XSS 风格字段。"""
    return {
        "ID": "INJ-VULN-01",
        "vulnerability_type": "SQLi",
        "externally_exploitable": True,
        "confidence": "needs_review",
        "source": "coupon_code @ services/workshop/crapi/shop/views.py:379",
        "source_detail": "request.data[\"coupon_code\"]",
        "sink_function": "cursor.execute",
        "render_context": None,
        "encoding_observed": None,
        "verdict": "vulnerable",
        "mismatch_reason": "string concat into SQL value slot, no parameterization",
        "witness_payload": "{\"coupon_code\": \"' UNION SELECT version()-- \"}",
    }


def test_parse_lenient_with_vuln_class_forces_injection_subtype():
    """vuln_class='injection' 时,entry 必须解析成 InjectionVulnerability
    (而非 smart-union 误判的 XssVulnerability)。"""
    content = json.dumps({"vulnerabilities": [_injection_entry_with_llm_fields()]})
    result = VulnerabilityQueue.parse_lenient(content, vuln_class="injection")
    assert len(result.queue.vulnerabilities) == 1
    assert isinstance(result.queue.vulnerabilities[0], InjectionVulnerability)


def test_parse_lenient_without_vuln_class_backward_compatible():
    """不传 vuln_class 时:entry 仍被解析(不丢、不崩),通用字段可访问——
    向后兼容契约(poc_generator/queue_merge/blackbox checker 等通用调用者只读
    BaseVulnerability 字段,不依赖具体子类型)。

    具体子类型由 smart-union 决定、不绑定(字段对齐后默认也会选 InjectionVulnerability,
    但这是 smart-union ties 行为,可能随 pydantic 版本变);稳定的类型保证只在传
    vuln_class 时提供(见 test_parse_lenient_with_vuln_class_forces_injection_subtype)。"""
    content = json.dumps({"vulnerabilities": [_injection_entry_with_llm_fields()]})
    result = VulnerabilityQueue.parse_lenient(content)  # 不传 class
    assert len(result.queue.vulnerabilities) == 1  # 不丢
    v = result.queue.vulnerabilities[0]
    assert v.ID == "INJ-VULN-01"  # 通用字段可访问,不崩
    assert v.verdict == "vulnerable"


def test_parse_lenient_vuln_class_unknown_falls_back_to_union():
    """未知 vuln_class 应回落到通用 Union 行为(不崩)。"""
    content = json.dumps({"vulnerabilities": [_injection_entry_with_llm_fields()]})
    result = VulnerabilityQueue.parse_lenient(content, vuln_class="bogus")
    assert len(result.queue.vulnerabilities) == 1  # 仍解析,不丢


# --- 漏洞 title 字段（spec 2026-08-06）：报告一句话概括标题 SSOT ---

def test_base_vulnerability_title_defaults_none():
    """title 可选，缺省 None（兼容旧数据 / 无 title 条目）。"""
    v = BaseVulnerability(
        ID="INJ-VULN-01",
        vulnerability_type="SQLi",
        externally_exploitable=True,
        confidence="high",
    )
    assert v.title is None


def test_base_vulnerability_title_settable():
    v = BaseVulnerability(
        ID="INJ-VULN-01",
        vulnerability_type="SQLi",
        externally_exploitable=True,
        confidence="high",
        title="PostgreSQL SQL Injection via Coupon Validation",
    )
    assert v.title == "PostgreSQL SQL Injection via Coupon Validation"


def test_title_json_roundtrip():
    """title 进 model_dump_json，反序列化后保留。"""
    v = InjectionVulnerability(
        ID="INJ-VULN-01", vulnerability_type="SQLi",
        externally_exploitable=True, confidence="high",
        title="SQLi via search q param",
    )
    data = json.loads(v.model_dump_json())
    assert data["title"] == "SQLi via search q param"
    back = InjectionVulnerability.model_validate(data)
    assert back.title == "SQLi via search q param"


def test_parse_lenient_legacy_queue_without_title_keeps_title_none():
    """旧 queue JSON 无 title 字段 → parse_lenient 不崩，title=None。"""
    content = json.dumps({"vulnerabilities": [
        {"ID": "INJ-1", "vulnerability_type": "SQLi",
         "externally_exploitable": True, "confidence": "high"},
    ]})
    result = VulnerabilityQueue.parse_lenient(content, vuln_class="injection")
    assert len(result.queue.vulnerabilities) == 1
    assert result.queue.vulnerabilities[0].title is None


def test_parse_lenient_preserves_title_when_present():
    """新 queue JSON 含 title → parse_lenient 保留 title。"""
    content = json.dumps({"vulnerabilities": [
        {"ID": "INJ-1", "vulnerability_type": "SQLi",
         "externally_exploitable": True, "confidence": "high",
         "title": "SQLi in /search via q"},
    ]})
    result = VulnerabilityQueue.parse_lenient(content, vuln_class="injection")
    assert result.queue.vulnerabilities[0].title == "SQLi in /search via q"


def test_title_inherited_by_all_subclasses():
    """5 个子类都继承 title（无需各自声明）。"""
    for cls in (InjectionVulnerability, XssVulnerability, SsrfVulnerability,
                AuthVulnerability, AuthzVulnerability):
        v = cls(
            ID="X-1", vulnerability_type="t", externally_exploitable=True,
            confidence="high", title="desc",
        )
        assert v.title == "desc", f"{cls.__name__} 未继承 title"


def test_injection_vulnerability_accepts_llm_output_fields():
    """InjectionVulnerability 同时接受两族字段：TS 原版 sink_call 族（prompt
    字段表所教，2026-08-20 follow-up 后是产出主族）与 XSS 风格
    (sink_function/render_context/encoding_observed/source_detail，
    _vuln_output_schema 时代的历史产出)，否则按-class parse 后信息被丢弃。"""
    v = InjectionVulnerability(
        ID="INJ-VULN-01",
        vulnerability_type="SQLi",
        externally_exploitable=True,
        confidence="high",
        source="coupon_code @ views.py:379",
        source_detail="request.data['coupon_code']",
        sink_function="cursor.execute",
        render_context="SQL_VALUE",
        encoding_observed="None",
        verdict="vulnerable",
        witness_payload="' UNION SELECT version()--",
    )
    assert v.sink_function == "cursor.execute"
    assert v.source_detail == "request.data['coupon_code']"
    assert v.render_context == "SQL_VALUE"
    assert v.encoding_observed == "None"
    # 旧字段仍保留(兼容 GitNexus 轨未来输出 / 现有测试)
    assert v.sink_call is None



def test_injection_vulnerability_keeps_prompt_taught_fields():
    """injection prompt 字段表（TS 原版 injectionFields + 移植增强）教的
    sink_call 族字段 + authentication_required / accessible_routes 必须被
    pydantic 保留——此前两增强字段不在模型上，按-class parse 时被静默丢弃
    （2026-08-20 follow-up 根因修复）。"""
    v = InjectionVulnerability(
        ID="INJ-VULN-01",
        vulnerability_type="SQLi",
        externally_exploitable=True,
        confidence="high",
        title="SQL 注入：搜索参数 q 进入原始查询",
        source="q @ controllers/search.js:88",
        authentication_required="false",
        accessible_routes="GET /search []",
        path="GET /search → handleSearch → sequelize.query",
        sink_call="models/search.js:31 sequelize.query",
        slot_type="SQL-val",
        sanitization_observed="escape @ controllers/search.js:90",
        concat_occurrences="models/search.js:31 (after sanitization)",
        verdict="vulnerable",
        witness_payload="' UNION SELECT 1--",
    )
    assert v.sink_call == "models/search.js:31 sequelize.query"
    assert v.slot_type == "SQL-val"
    assert v.sanitization_observed == "escape @ controllers/search.js:90"
    assert v.concat_occurrences.startswith("models/search.js:31")
    assert v.authentication_required == "false"
    assert v.accessible_routes == "GET /search []"


def test_xss_vulnerability_keeps_prompt_taught_fields():
    """xss prompt 增强字段 authentication_required / accessible_routes 同样
    不丢（与 injection 对齐）。"""
    v = XssVulnerability(
        ID="XSS-VULN-01",
        vulnerability_type="Reflected",
        externally_exploitable=True,
        confidence="high",
        authentication_required="true",
        accessible_routes="GET /profile [authGuard]",
        sink_function="res.send",
    )
    assert v.authentication_required == "true"
    assert v.accessible_routes == "GET /profile [authGuard]"
    assert v.sink_function == "res.send"
