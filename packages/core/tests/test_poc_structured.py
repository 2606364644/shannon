# packages/core/tests/test_poc_structured.py
"""T4③：core 层结构化 POC 生成（spec 2026-08-26-report-generation-agent-design §4 poc / §5.3）。

build_structured_poc 产 models/report_data.PocBlock 形态 dict：
- request（method/url/headers/body）确定性优先——endpoint/endpoints/path/
  source_endpoint/source 提取 method+path，base_url 拼接；
- preconditions 从 authentication_required/notes 派生；
- expected_response 由 llm_fn 产（verdict/mismatch_reason 基础上判断成功响应特征），
  不给/失败 → None（降级只含确定性字段，绝不抛）；
- witness_payload 直传 vuln.witness_payload/minimal_witness；
- curl/raw_http 由 request 确定性生成。
"""
import json
from types import SimpleNamespace

import pytest

from supernova_core.models.report_data import PocBlock
from supernova_core.services.poc_structured import (
    apply_structured_poc,
    build_structured_poc,
    render_curl,
    render_raw_http,
)

BASE = "http://t.example.com"


def _vuln(**kw) -> SimpleNamespace:
    """NodeGoat 实证形态的 xss vuln（POST body 参数 + witness + 需登录）。"""
    base = dict(
        ID="XSS-VULN-01",
        vulnerability_type="Stored",
        endpoint="POST /memos",
        endpoints=None,
        path=None,
        source=None,
        source_endpoint=None,
        witness_payload='<img src=x onerror=alert(1)>',
        affected_parameters=["memo (body)"],
        authentication_required="true",
        verdict="vulnerable",
        mismatch_reason="render without escaping",
        notes=None,
        evidence_chain=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


# --------------------------------------------------------------------------- #
# build_structured_poc：确定性部分
# --------------------------------------------------------------------------- #

def test_build_structured_poc_deterministic_body_form():
    poc = build_structured_poc(_vuln(), BASE)
    assert isinstance(poc, dict)
    req = poc["request"]
    assert req["method"] == "POST"
    assert req["url"] == f"{BASE}/memos"
    # body 带 witness_payload + affected_parameters 构造（(body) 注记 → form body）
    assert req["body"] == "memo=<img src=x onerror=alert(1)>"
    assert req["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    # witness_payload 直传
    assert poc["witness_payload"] == '<img src=x onerror=alert(1)>'
    # preconditions 从 authentication_required 派生
    assert poc["preconditions"] == "需登录（携带有效会话凭证）"
    # 无 llm_fn → expected_response 降级 None
    assert poc["expected_response"] is None
    # curl/raw_http 确定性生成
    assert poc["curl"].startswith("curl -i -X POST")
    assert poc["raw_http"].startswith("POST /memos HTTP/1.1")


def test_build_structured_poc_validates_pocblock_schema():
    """产物是 models/report_data.PocBlock 形态（T4 写回 report_poc 的契约）。"""
    poc = build_structured_poc(_vuln(), BASE)
    block = PocBlock.model_validate(poc)
    assert block.request is not None and block.request.method == "POST"
    assert block.witness_payload == '<img src=x onerror=alert(1)>'


def test_build_structured_poc_query_placement_goes_into_url():
    v = _vuln(endpoint=None, source="GET /search?q=1",
              witness_payload="' OR '1'='1", affected_parameters=None,
              authentication_required="false")
    poc = build_structured_poc(v, BASE)
    req = poc["request"]
    assert req["method"] == "GET"
    assert req["url"].startswith(f"{BASE}/search?q=")
    assert "OR" in req["url"]  # witness 进 query
    assert req["body"] is None
    assert req["headers"] == {}          # 无 body → 无 Content-Type
    assert poc["preconditions"] == "无需登录"


def test_build_structured_poc_endpoints_list_fallback():
    """endpoint 缺失时 endpoints 列表（'POST /memos (write)'）兜底，注记不进 path。"""
    v = _vuln(endpoint=None, endpoints=["POST /memos (write)", "GET /memos (trigger)"])
    poc = build_structured_poc(v, BASE)
    assert poc["request"]["method"] == "POST"
    assert poc["request"]["url"] == f"{BASE}/memos"


def test_build_structured_poc_endpoints_list_beats_path_hop_summary():
    """路由链优先级 endpoint → endpoints → path：接口列表权威于 source→sink 摘要。"""
    v = _vuln(endpoint=None, endpoints=["POST /memos (write)"],
              path="GET /memos -> memos.js render")
    poc = build_structured_poc(v, BASE)
    assert poc["request"]["method"] == "POST"


def test_build_structured_poc_source_endpoint_auth_vuln():
    """auth 轨：source_endpoint 提路由；witness_payload 缺 → minimal_witness 直传。"""
    v = _vuln(source_endpoint="POST /auth/callback", endpoint=None,
              witness_payload=None, minimal_witness="forge jwt with alg=none",
              affected_parameters=None, authentication_required=None)
    poc = build_structured_poc(v, BASE)
    assert poc["request"]["method"] == "POST"
    assert poc["request"]["url"] == f"{BASE}/auth/callback"
    assert poc["witness_payload"] == "forge jwt with alg=none"


def test_build_structured_poc_witness_request_line_carries_route():
    """witness 为请求行形态 'POST /x?k=v' 时自带 method/path/参数（G1 parse_witness）。"""
    v = _vuln(endpoint=None, witness_payload="POST /memos?memo=<script>alert(1)</script>",
              affected_parameters=None)
    poc = build_structured_poc(v, BASE)
    assert poc["request"]["method"] == "POST"
    assert poc["request"]["url"].startswith(f"{BASE}/memos?memo=")
    assert poc["request"]["body"] is None


def test_build_structured_poc_none_when_no_route_anchor():
    """GN 轨纯代码位置 source（无 METHOD /path、无 witness 请求行）→ None（无 HTTP 锚点）。"""
    v = _vuln(endpoint=None, source="payload (src/main/java/x/C.java:m:70)",
              witness_payload=None)
    assert build_structured_poc(v, BASE) is None
    assert build_structured_poc(None, BASE) is None


def test_build_structured_poc_base_url_normalization():
    """空 base_url → 占位符 host；无 scheme → 补 http://（raw_http Host 依赖 netloc）。"""
    poc = build_structured_poc(_vuln(), None)
    assert poc["request"]["url"].startswith("https://TARGET[:PORT]/memos")
    poc2 = build_structured_poc(_vuln(), "t.example.com")
    assert poc2["request"]["url"] == f"{BASE}/memos"


def test_build_structured_poc_preconditions_variants():
    # 非布尔契约值（如 isLoggedIn）→ 保守需登录并保留原文
    poc = build_structured_poc(_vuln(authentication_required="isLoggedIn"), BASE)
    assert poc["preconditions"] == "需登录（isLoggedIn）"
    # authentication_required 缺失但 notes 述及认证 → 需登录
    poc2 = build_structured_poc(
        _vuln(authentication_required=None, notes="login required to reach memo board"), BASE)
    assert poc2["preconditions"] == "需登录（notes 述及认证）"
    # 无任何信号 → None
    poc3 = build_structured_poc(_vuln(authentication_required=None), BASE)
    assert poc3["preconditions"] is None


# --------------------------------------------------------------------------- #
# build_structured_poc：expected_response（LLM 轨道 + 全程降级）
# --------------------------------------------------------------------------- #

def test_build_structured_poc_llm_expected_response():
    seen: list[str] = []

    def llm_fn(prompt: str):
        seen.append(prompt)
        return {"indicator": "响应含未转义 onerror",
                "success_criteria": "200 且 payload 原样回显于 memos 页"}

    poc = build_structured_poc(_vuln(), BASE, llm_fn=llm_fn)
    assert poc["expected_response"] == {
        "indicator": "响应含未转义 onerror",
        "success_criteria": "200 且 payload 原样回显于 memos 页"}
    # prompt 基于 verdict/mismatch_reason 判断成功响应特征
    assert len(seen) == 1
    assert "vulnerable" in seen[0]
    assert "render without escaping" in seen[0]


def test_build_structured_poc_llm_exception_degrades_to_deterministic():
    def boom(prompt: str):
        raise RuntimeError("llm down")

    poc = build_structured_poc(_vuln(), BASE, llm_fn=boom)
    assert poc is not None                 # 绝不抛
    assert poc["expected_response"] is None
    assert poc["request"]["url"] == f"{BASE}/memos"   # 确定性部分完好
    assert poc["witness_payload"] == '<img src=x onerror=alert(1)>'


def test_build_structured_poc_llm_string_returns_coerced():
    # JSON 字符串（含 markdown fence）→ 解析
    fenced = '```json\n{"indicator": "uid=1000 回显"}\n```'

    poc = build_structured_poc(_vuln(), BASE, llm_fn=lambda p: fenced)
    assert poc["expected_response"] == {"indicator": "uid=1000 回显",
                                        "success_criteria": None}
    # 纯文本 → 整段作 indicator
    poc2 = build_structured_poc(_vuln(), BASE, llm_fn=lambda p: "SQL 报错回显")
    assert poc2["expected_response"] == {"indicator": "SQL 报错回显",
                                         "success_criteria": None}
    # None / 空 / 无法理解的结构 → None（降级）
    assert build_structured_poc(_vuln(), BASE, llm_fn=lambda p: None)[
        "expected_response"] is None
    assert build_structured_poc(_vuln(), BASE, llm_fn=lambda p: 42)[
        "expected_response"] is None


# --------------------------------------------------------------------------- #
# render_curl / render_raw_http：从 request dict 确定性生成
# --------------------------------------------------------------------------- #

def _poc_dict(**req_kw) -> dict:
    req = {"method": "POST", "url": f"{BASE}/memos",
           "headers": {"Content-Type": "application/json"}, "body": '{"memo": "x"}'}
    req.update(req_kw)
    return {"request": req}


def test_render_curl_full_request():
    curl = render_curl(_poc_dict(
        headers={"Content-Type": "application/json", "Cookie": "session=abc"}))
    lines = curl.split(" \\\n")
    assert lines[0] == f"curl -i -X POST '{BASE}/memos'"
    assert "  -H 'Content-Type: application/json'" in lines
    assert "  -H 'Cookie: session=abc'" in lines
    assert "  --data '{\"memo\": \"x\"}'" in lines


def test_render_curl_quotes_shell_metacharacters():
    curl = render_curl(_poc_dict(body="memo=a=b'c"))
    assert """--data 'memo=a=b'\\''c'""" in curl


def test_render_curl_minimal_get_no_body():
    curl = render_curl({"request": {"method": "GET", "url": f"{BASE}/x",
                                    "headers": {}, "body": None}})
    assert curl == f"curl -i -X GET '{BASE}/x'"


def test_render_raw_http_full_request():
    raw = render_raw_http(_poc_dict(
        url=f"{BASE}:8443/memos?memo=x", method="POST"))
    lines = raw.splitlines()
    assert lines[0] == "POST /memos?memo=x HTTP/1.1"
    assert "Host: t.example.com:8443" in lines
    assert "Content-Type: application/json" in lines
    assert f"Content-Length: {len('{\"memo\": \"x\"}'.encode())}" in lines
    assert raw.endswith("\n" + '{"memo": "x"}')


def test_render_raw_http_infers_content_type_when_missing():
    raw = render_raw_http(_poc_dict(headers={}))
    assert "Content-Type: application/json" in raw      # JSON body → json
    raw2 = render_raw_http(_poc_dict(headers={}, body="memo=x"))
    assert "Content-Type: application/x-www-form-urlencoded" in raw2


def test_render_raw_http_no_body_no_length():
    raw = render_raw_http({"request": {"method": "GET", "url": f"{BASE}/x",
                                       "headers": {}, "body": None}})
    assert "Content-Length" not in raw
    assert "Content-Type" not in raw


# --------------------------------------------------------------------------- #
# apply_structured_poc：写回 queue entry
# --------------------------------------------------------------------------- #

def test_apply_structured_poc_writes_report_poc_field():
    entry = {"ID": "XSS-VULN-01", "vulnerability_type": "Stored"}
    poc = build_structured_poc(_vuln(), BASE)
    out = apply_structured_poc(entry, poc)
    assert out is entry                      # 原地写回并返回
    assert entry["report_poc"] == poc
    # 写回产物仍满足 PocBlock 契约
    assert PocBlock.model_validate(entry["report_poc"]).request.method == "POST"


def test_apply_structured_poc_none_clears_field():
    entry = {"ID": "X", "report_poc": {"request": {}}}
    out = apply_structured_poc(entry, None)
    assert out["report_poc"] is None
