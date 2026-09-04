"""T7（spec 2026-08-26-report-generation-agent-design §6.1）：黑盒 report_data 组装。

数据源 = ``{vc}_exploit_verdicts.json``（exploit agent 结构化判定，schema 见
``collectors/exploit.py`` 的 5 档 verdict）→ 确定性映射 ReportData：
``evidence.verification="dynamic"``、实测证据/请求转结构化、stats 聚合。
executive_summary/qa 留 None（T5 后续接）。
"""
import json

import pytest

# ── fixtures：对齐真实 verdicts 形态（NodeGoat-20260812-024451~1 真机产物字段）──

_EXPLOITED_VERDICT = {
    "vulnerability_id": "INJ-VULN-01",
    "status": "exploited",
    "severity": "critical",
    "impact": "RCE：eval(preTax) 任意命令执行，完全接管服务器。",
    "exploitation_steps": [
        "1. Authenticate as any valid user (session cookie required).",
        "2. Full curl: curl 'http://target:4000/contributions' -X POST "
        "-H 'Cookie: connect.sid=abc' -d 'preTax=@@@&afterTax=0'",
    ],
    "proof_of_impact": "HTTP 500 响应含 '<br>SyntaxError'，证明 preTax 进入 eval()。",
    "cwe_id": "CWE-95",
    "cvss": "AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:H 9.8",
    "owasp_category": "A03:2021-Injection",
}

_BLOCKED_VERDICT = {
    "vulnerability_id": "XSS-VULN-04",
    "status": "blocked_by_security",
    "confidence": "medium",
    "current_blocker": "CSP blocks inline event handlers",
    "what_we_tried": "Tried attribute breakout via profile fields; no curl needed.",
    "evidence_of_vulnerability": "Payload reflected unencoded in HTML response.",
    "expected_impact": "Session theft if CSP is bypassed.",
}


def _write_verdicts(deliverables: "any", vc: str, payload: dict, where: str = "flat"):
    """where: flat=blackbox/ 顶层（legacy）；intermediate=blackbox/intermediate/（现行）。"""
    if where == "intermediate":
        d = deliverables / "blackbox" / "intermediate"
    else:
        d = deliverables / "blackbox"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{vc}_exploit_verdicts.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _payload(vc: str, verdicts: list) -> dict:
    return {
        "vuln_class": vc,
        "accepted_ids": [v["vulnerability_id"] for v in verdicts],
        "verdicts": verdicts,
        "rejected": [],
    }


async def test_build_blackbox_report_data_maps_exploited_verdict(tmp_path):
    from supernova_core.services.report_data_blackbox import build_blackbox_report_data
    from supernova_core.models.report_data import ScanMeta

    d = tmp_path / "deliverables"
    _write_verdicts(d, "injection", _payload("injection", [_EXPLOITED_VERDICT]))

    rd = await build_blackbox_report_data(d, ScanMeta(id="bb-1", track="blackbox"))

    assert rd.schema_version == 1
    assert rd.scan.id == "bb-1"
    assert rd.scan.track == "blackbox"
    assert rd.executive_summary is None   # T5 后续接
    assert rd.qa is None                  # T5 后续接
    assert len(rd.vulnerabilities) == 1
    v = rd.vulnerabilities[0]
    assert v.id == "INJ-VULN-01"
    assert v.type == "injection"
    assert v.severity == "critical"
    assert v.confidence == "high"          # exploited=实测复现 → 确定性映射 high
    assert v.cwe_id == "CWE-95"
    assert v.cvss and v.cvss.startswith("AV:N")
    assert v.owasp_category == "A03:2021-Injection"
    # narrative：impact 有、cause/remediation 黑盒无（None）
    assert v.narrative is not None
    assert v.narrative.impact == _EXPLOITED_VERDICT["impact"]
    assert v.narrative.cause is None
    assert v.narrative.remediation is None
    # evidence：dynamic + 实测输出
    assert v.evidence is not None
    assert v.evidence.verification == "dynamic"
    assert v.evidence.dynamic_evidence == _EXPLOITED_VERDICT["proof_of_impact"]
    assert v.evidence.verdict == "exploited"
    # poc：实际发出的请求 → request；实测观察 → expected_response
    assert v.poc is not None
    assert v.poc.request is not None
    assert v.poc.request.method == "POST"
    assert v.poc.request.url == "http://target:4000/contributions"
    assert v.poc.request.headers == {"Cookie": "connect.sid=abc"}
    assert v.poc.request.body == "preTax=@@@&afterTax=0"
    assert v.poc.preconditions == "Authenticate as any valid user (session cookie required)."
    assert v.poc.expected_response is not None
    assert v.poc.expected_response.indicator == _EXPLOITED_VERDICT["proof_of_impact"]
    # curl/raw_http 由 request 确定性生成（shlex.quote 按需加引号：URL 无元字符
    # 不引、header/body 含空格/& 引）
    assert v.poc.curl == (
        "curl -X POST http://target:4000/contributions "
        "-H 'Cookie: connect.sid=abc' --data 'preTax=@@@&afterTax=0'"
    )
    assert v.poc.raw_http is not None
    assert "POST /contributions HTTP/1.1" in v.poc.raw_http
    assert "Host: target:4000" in v.poc.raw_http
    # endpoints：从实测请求确定性派生（method+path）
    assert len(v.endpoints) == 1
    assert v.endpoints[0].method == "POST"
    assert v.endpoints[0].path == "/contributions"
    # raw 保留原始 verdict entry
    assert v.raw == _EXPLOITED_VERDICT


async def test_build_blackbox_report_data_blocked_verdict(tmp_path):
    from supernova_core.services.report_data_blackbox import build_blackbox_report_data
    from supernova_core.models.report_data import ScanMeta

    d = tmp_path / "deliverables"
    _write_verdicts(d, "xss", _payload("xss", [_BLOCKED_VERDICT]))

    rd = await build_blackbox_report_data(d, ScanMeta(id="bb-1", track="blackbox"))
    v = rd.vulnerabilities[0]
    assert v.severity is None               # blocked 无 severity
    assert v.confidence == "medium"         # verdict 自带 confidence 直传
    assert v.evidence is not None
    assert v.evidence.verification == "dynamic"
    assert v.evidence.dynamic_evidence == _BLOCKED_VERDICT["evidence_of_vulnerability"]
    assert v.evidence.verdict == "blocked_by_security"
    assert v.evidence.notes and "CSP" in v.evidence.notes   # blocker/尝试记 notes
    assert v.narrative is not None
    assert v.narrative.impact == _BLOCKED_VERDICT["expected_impact"]
    # 无可解析请求 → request=None，expected_response 仍有实测观察
    assert v.poc is not None
    assert v.poc.request is None
    assert v.poc.expected_response is not None
    assert v.poc.curl is None


@pytest.mark.parametrize("where", ["flat", "intermediate"])
async def test_build_blackbox_report_data_layout_fallback(tmp_path, where):
    """verdicts 落点双布局均可读：blackbox/intermediate/（现行）与 blackbox/ 顶层（legacy）。"""
    from supernova_core.services.report_data_blackbox import build_blackbox_report_data
    from supernova_core.models.report_data import ScanMeta

    d = tmp_path / "deliverables"
    _write_verdicts(d, "injection", _payload("injection", [_EXPLOITED_VERDICT]), where=where)
    rd = await build_blackbox_report_data(d, ScanMeta(id="bb-1", track="blackbox"))
    assert len(rd.vulnerabilities) == 1
    assert rd.vulnerabilities[0].id == "INJ-VULN-01"


async def test_build_blackbox_report_data_stats_aggregation(tmp_path):
    from supernova_core.services.report_data_blackbox import build_blackbox_report_data
    from supernova_core.models.report_data import ScanMeta

    d = tmp_path / "deliverables"
    inj2 = dict(_EXPLOITED_VERDICT, vulnerability_id="INJ-VULN-02", severity="high")
    _write_verdicts(d, "injection",
                    _payload("injection", [_EXPLOITED_VERDICT, inj2]))
    _write_verdicts(d, "xss", _payload("xss", [_BLOCKED_VERDICT]))

    rd = await build_blackbox_report_data(d, ScanMeta(id="bb-1", track="blackbox"))
    assert rd.stats is not None
    assert rd.stats.by_type["injection"].count == 2
    assert rd.stats.by_type["xss"].count == 1
    assert rd.stats.by_type["injection"].severity_range == "critical-high"
    # blocked 无 severity → 不计 by_severity
    assert rd.stats.by_severity == {"critical": 1, "high": 1}


async def test_build_blackbox_report_data_no_verdicts(tmp_path):
    """无任何 verdicts 文件 → 空报告不炸（stats 空聚合）。"""
    from supernova_core.services.report_data_blackbox import build_blackbox_report_data
    from supernova_core.models.report_data import ScanMeta

    d = tmp_path / "deliverables"
    d.mkdir(parents=True)
    rd = await build_blackbox_report_data(d, ScanMeta(id="bb-1", track="blackbox"))
    assert rd.vulnerabilities == []
    assert rd.stats is not None
    assert rd.stats.by_type == {}
    assert rd.stats.by_severity == {}


async def test_build_blackbox_report_data_corrupt_file_nonfatal(tmp_path):
    """单个 class verdicts 损坏 → warning 跳过该 class，其余照常。"""
    from supernova_core.services.report_data_blackbox import build_blackbox_report_data
    from supernova_core.models.report_data import ScanMeta

    d = tmp_path / "deliverables"
    bb = d / "blackbox"
    bb.mkdir(parents=True)
    (bb / "xss_exploit_verdicts.json").write_text("{not json", encoding="utf-8")
    _write_verdicts(d, "injection", _payload("injection", [_EXPLOITED_VERDICT]))

    rd = await build_blackbox_report_data(d, ScanMeta(id="bb-1", track="blackbox"))
    assert [v.id for v in rd.vulnerabilities] == ["INJ-VULN-01"]


async def test_poc_request_prose_fallback(tmp_path):
    """无 curl、但步骤含 ``METHOD http://...`` 散文形态 → 尽最大确定性拼（缺字段 None）。"""
    from supernova_core.services.report_data_blackbox import build_blackbox_report_data
    from supernova_core.models.report_data import ScanMeta

    verdict = dict(
        _EXPLOITED_VERDICT,
        exploitation_steps=["1. CONFIRM: POST http://prose:4000/login with form body a=1"],
    )
    d = tmp_path / "deliverables"
    _write_verdicts(d, "auth", _payload("auth", [verdict]))

    rd = await build_blackbox_report_data(d, ScanMeta(id="bb-1", track="blackbox"))
    v = rd.vulnerabilities[0]
    assert v.poc.request is not None
    assert v.poc.request.method == "POST"
    assert v.poc.request.url == "http://prose:4000/login"
    assert v.poc.request.headers == {}
    assert v.poc.request.body is None       # 散文形态提不出结构化 body → None


async def test_poc_request_absent(tmp_path):
    """请求形态完全提不出 → request/curl None，poc 仍带实测观察。"""
    from supernova_core.services.report_data_blackbox import build_blackbox_report_data
    from supernova_core.models.report_data import ScanMeta

    verdict = dict(
        _EXPLOITED_VERDICT,
        exploitation_steps=["1. Register an account via the signup form."],
    )
    d = tmp_path / "deliverables"
    _write_verdicts(d, "ssrf", _payload("ssrf", [verdict]))

    rd = await build_blackbox_report_data(d, ScanMeta(id="bb-1", track="blackbox"))
    v = rd.vulnerabilities[0]
    assert v.poc.request is None
    assert v.poc.curl is None
    assert v.poc.raw_http is None
    assert v.poc.expected_response is not None
    assert v.endpoints == []


# ---------- write_report_data（复用 T1 白盒同名函数，缺则兜底自实现）----------

async def test_write_report_data_writes_json(tmp_path):
    from supernova_core.services.report_data_blackbox import (
        build_blackbox_report_data, write_report_data,
    )
    from supernova_core.models.report_data import ScanMeta

    d = tmp_path / "deliverables"
    _write_verdicts(d, "injection", _payload("injection", [_EXPLOITED_VERDICT]))
    rd = await build_blackbox_report_data(d, ScanMeta(id="bb-1", track="blackbox"))

    out = tmp_path / "blackbox" / "report_data.json"
    await write_report_data(rd, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["scan"]["track"] == "blackbox"
    assert data["vulnerabilities"][0]["id"] == "INJ-VULN-01"
    # ensure_ascii=False：中文 impact 原样落盘
    assert "任意命令执行" in out.read_text(encoding="utf-8")


# ---------- scan meta（session.json → ScanMeta）----------

def test_build_scan_meta_from_session_data():
    from supernova_core.services.report_data_blackbox import build_scan_meta_from_session

    session_data = {
        "session": {"id": "BB-1", "createdAt": "2026-08-12T04:52:07.106Z"},
        "metrics": {
            "total_duration_ms": 4053996,
            "total_cost_usd": 41.33,
            "cost_currency": "CNY",
            "agents": {
                "xss-exploit": {"model": "glm-4.6"},
                "report": {"model": "glm-4.6"},
                "auth-exploit": {"model": "glm-4.5"},
            },
        },
    }
    meta = build_scan_meta_from_session(session_data, fallback_id="fallback")
    assert meta.id == "BB-1"
    assert meta.track == "blackbox"
    assert meta.date == "2026-08-12T04:52:07.106Z"
    assert meta.duration_ms == 4053996
    assert meta.cost == 41.33
    assert meta.currency == "CNY"
    assert meta.model == "glm-4.5, glm-4.6"   # 去重排序


def test_build_scan_meta_from_none_session():
    from supernova_core.services.report_data_blackbox import build_scan_meta_from_session

    meta = build_scan_meta_from_session(None, fallback_id="scan-9")
    assert meta.id == "scan-9"
    assert meta.track == "blackbox"
    assert meta.duration_ms is None
    assert meta.model is None


# ── 结构化验证步骤贯通（验证证据展示优化，2026-08-27）──────────────────────────
# verdict 落盘 json 的 steps（新结构化 / 旧字符串形态）→ evidence.steps 逐字保序，
# 报告（黑盒 + 融合）天然分步骤；命令有独立字段，poc.request 优先直取。

_STRUCTURED_STEPS_VERDICT = {
    "vulnerability_id": "INJ-VULN-07",
    "status": "exploited",
    "severity": "high",
    "impact": "SQLi 数据抽取。",
    "exploitation_steps": [
        {"action": "Confirm injection point",
         "command": "curl -s 'http://target:4000/api/search?q=test%27'",
         "result": "500 SyntaxError — injectable"},
        {"action": "Enumerate columns",
         "command": "curl -s 'http://target:4000/api/search?q=x%27+ORDER+BY+4--'",
         "result": "200 — 4 columns"},
    ],
    "proof_of_impact": "users 表 5 行凭证抽取成功。",
}


async def test_structured_steps_flow_to_evidence_steps(tmp_path):
    from supernova_core.services.report_data_blackbox import build_blackbox_report_data
    from supernova_core.models.report_data import ScanMeta

    d = tmp_path / "deliverables"
    _write_verdicts(d, "injection", _payload("injection", [_STRUCTURED_STEPS_VERDICT]))
    rd = await build_blackbox_report_data(d, ScanMeta(id="bb-2", track="blackbox"))
    v = rd.vulnerabilities[0]
    assert v.evidence is not None and len(v.evidence.steps) == 2
    s1 = v.evidence.steps[0]
    assert s1.action == "Confirm injection point"
    assert s1.command == "curl -s 'http://target:4000/api/search?q=test%27'"
    assert s1.result == "500 SyntaxError — injectable"
    # 步数/顺序保真（分步骤报告的数据底座）
    assert v.evidence.steps[1].action == "Enumerate columns"


async def test_structured_command_feeds_poc_request_first(tmp_path):
    """poc.request 优先直取结构化 command（生成层有字段就不再靠散文正则反解）。"""
    from supernova_core.services.report_data_blackbox import build_blackbox_report_data
    from supernova_core.models.report_data import ScanMeta

    d = tmp_path / "deliverables"
    _write_verdicts(d, "injection", _payload("injection", [_STRUCTURED_STEPS_VERDICT]))
    rd = await build_blackbox_report_data(d, ScanMeta(id="bb-2", track="blackbox"))
    v = rd.vulnerabilities[0]
    assert v.poc is not None and v.poc.request is not None
    assert v.poc.request.method == "GET"
    assert v.poc.request.url == "http://target:4000/api/search?q=test%27"


async def test_legacy_string_steps_normalized_into_steps(tmp_path):
    """旧落盘 json（纯字符串步骤）→ 组装时同款归一化（剥编号+拆尾随命令）进 steps。"""
    from supernova_core.services.report_data_blackbox import build_blackbox_report_data
    from supernova_core.models.report_data import ScanMeta

    legacy = dict(_EXPLOITED_VERDICT)
    d = tmp_path / "deliverables"
    _write_verdicts(d, "injection", _payload("injection", [legacy]))
    rd = await build_blackbox_report_data(d, ScanMeta(id="bb-3", track="blackbox"))
    v = rd.vulnerabilities[0]
    assert v.evidence is not None and len(v.evidence.steps) == 2
    s1, s2 = v.evidence.steps
    assert s1.action == "Authenticate as any valid user (session cookie required)."
    assert s1.command is None
    # 「2. Full curl: curl '...'」→ 剥编号、散文留 action、命令拆进 command 字段
    assert s2.action == "Full curl:"
    assert s2.command is not None and s2.command.startswith("curl 'http://target:4000/contributions'")
    # 无 steps 字段的 verdict（如 blocked 档）→ steps 空列表（不炸、不编造）
    _write_verdicts(d, "xss", _payload("xss", [_BLOCKED_VERDICT]))
    rd2 = await build_blackbox_report_data(d, ScanMeta(id="bb-4", track="blackbox"))
    assert all(v.evidence is not None and v.evidence.steps == []
               for v in rd2.vulnerabilities if v.type == "xss")


# ── 验证缺口留痕（spec 2026-09-03-blackbox-verification-gap-traceability §5）──

@pytest.mark.asyncio
async def test_gaps_become_interrupted_cards(tmp_path):
    """verdicts payload 的 gaps 逐条成「未验证卡」：verdict=interrupted、
    notes=detail 原因、endpoints=queue 端点（融合 (type, path) 匹配键）。"""
    from supernova_core.services.report_data_blackbox import build_blackbox_report_data
    from supernova_core.models.report_data import ScanMeta

    payload = _payload("xss", [_EXPLOITED_VERDICT.__class__(_EXPLOITED_VERDICT)])
    payload["accepted_ids"] = ["XSS-VULN-01"]
    payload["verdicts"] = []  # 0 accepted：全缺口形态（xss agent 中断真机复现）
    payload["gaps"] = [
        {"id": "XSS-VULN-01", "reason_type": "unregistered", "attempted": True,
         "endpoints": ["POST /login"],
         "detail": "agent 未完成验证闭环（登记 0/1）；工具轨迹显示已对该端点发起过请求，未产出结论"},
        {"id": "XSS-VULN-02", "reason_type": "rejected", "attempted": None,
         "endpoints": ["POST /signup"],
         "detail": "agent 已登记验证结论但被校验拒收：L1 schema: exploited.severity Field required"},
    ]
    _write_verdicts(tmp_path, "xss", payload, where="intermediate")
    rd = await build_blackbox_report_data(tmp_path, ScanMeta(id="s", track="blackbox"))
    by_id = {v.id: v for v in rd.vulnerabilities}
    assert set(by_id) == {"XSS-VULN-01", "XSS-VULN-02"}
    g1 = by_id["XSS-VULN-01"]
    assert g1.evidence.verdict == "interrupted"
    assert "登记 0/1" in (g1.evidence.notes or "")
    assert [(e.method, e.path) for e in g1.endpoints] == [("POST", "/login")]
    g2 = by_id["XSS-VULN-02"]
    assert g2.evidence.verdict == "interrupted"
    assert "L1 schema" in (g2.evidence.notes or "")
    # interrupted 卡不计入 exploited 统计口径（stats 按类计数但非 exploited）
    assert rd.stats.by_type["xss"].count == 2


@pytest.mark.asyncio
async def test_no_gaps_no_interrupted_cards(tmp_path):
    """无 gaps 键 / gaps 空 → 无 interrupted 卡（向后兼容旧 payload）。"""
    from supernova_core.services.report_data_blackbox import build_blackbox_report_data
    from supernova_core.models.report_data import ScanMeta

    payload = _payload("injection", [_EXPLOITED_VERDICT])
    _write_verdicts(tmp_path, "injection", payload, where="intermediate")
    rd = await build_blackbox_report_data(tmp_path, ScanMeta(id="s", track="blackbox"))
    assert len(rd.vulnerabilities) == 1
    assert rd.vulnerabilities[0].evidence.verdict == "exploited"


@pytest.mark.asyncio
async def test_build_class_meta_from_verdicts(tmp_path):
    """verdicts 文件 → {vc: {exists, ids}}（融合层 not-covered 成因判据，
    spec 2026-09-03 §6：ids = accepted∪gaps∪rejected 全集）。"""
    from supernova_core.services.report_data_blackbox import build_class_meta

    payload = _payload("injection", [_EXPLOITED_VERDICT])
    payload["gaps"] = [{"id": "INJ-VULN-02", "reason_type": "rejected",
                        "attempted": None, "endpoints": [], "detail": "d"}]
    payload["rejected"] = [{"id": "INJ-VULN-02", "reason": "L1 schema"}]
    _write_verdicts(tmp_path, "injection", payload, where="intermediate")
    meta = await build_class_meta(tmp_path)
    assert meta["injection"]["exists"] is True
    assert meta["injection"]["ids"] == {"INJ-VULN-01", "INJ-VULN-02"}
    assert "xss" not in meta  # 文件不存在 → 键缺席（类未跑）
