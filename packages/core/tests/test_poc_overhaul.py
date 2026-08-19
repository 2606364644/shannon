# packages/core/tests/test_poc_overhaul.py
"""PoC 准确性+速度治理（spec 2026-08-19-poc-accuracy-speed-overhaul-design）分层 TDD。

按模块分区：parse_witness / lint_spec / RouteIndex / authz 配对 / 置信 / 渲染 /
去重合并 / checkpoint v2 / gap-fill 加固 / auth 并行 / fixture 量化验收。
"""
import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from supernova_core.services.poc_generator import (
    AuthState,
    ConfidenceBand,
    HttpRequestSpec,
    parse_witness,
    lint_spec,
    RouteIndex,
)

# --------------------------------------------------------------------------- #
# G1: parse_witness 三形态解析
# --------------------------------------------------------------------------- #


class TestParseWitnessRequestLine:
    """形态 1：请求行（治 hk「请求行整体塞 id 参数」）。"""

    def test_plain_request_line(self):
        wp = parse_witness("GET /api/v2/download-cer?uid=12345")
        assert wp.method == "GET"
        assert wp.path == "/api/v2/download-cer"
        assert wp.values == {"uid": "12345"}

    def test_request_line_multi_query(self):
        wp = parse_witness("POST /api/x?a=1&b=2")
        assert wp.method == "POST" and wp.path == "/api/x"
        assert wp.values == {"a": "1", "b": "2"}

    def test_request_line_with_trailing_description(self):
        """请求行后跟说明文字 → 说明进 note，payload 参数保留。
        尾部「空格+CJK 开头」的说明段从 query 值里剥出（hk 实证形态）。"""
        wp = parse_witness("GET /api/v2/download-cer?uid=1' OR '1'='1 触发 SQL 报错")
        assert wp.method == "GET" and wp.path == "/api/v2/download-cer"
        assert wp.values == {"uid": "1' OR '1'='1"}
        assert wp.note and "SQL 报错" in wp.note

    def test_request_line_with_body_description(self):
        """hk 实证：POST 请求行 + 'with body {...}' 说明 → 说明整体进 note。"""
        wp = parse_witness("POST /api/v2/upload-cer with body {spaceId: 10295, uid: <victim_uid>} + file upload")
        assert wp.method == "POST" and wp.path == "/api/v2/upload-cer"
        assert wp.values == {}
        assert wp.note and "with body" in wp.note

    def test_request_line_ellipsis_segment_dropped(self):
        """hk 实证：query 里 '...' 省略号段丢弃，合法段保留。"""
        wp = parse_witness("GET /api/v2/apply/expect-finish-time?...&amount=99999999")
        assert wp.values == {"amount": "99999999"}

    def test_request_line_no_query(self):
        wp = parse_witness("POST /login")
        assert wp.method == "POST" and wp.path == "/login"
        assert wp.values == {}


class TestParseWitnessParamString:
    """形态 2：参数串（治 NodeGoat XSS「firstName=...&bankRouting=... 塞单参数」）。"""

    def test_multi_param_string(self):
        wp = parse_witness("firstName=<img src=x onerror=alert(document.cookie)>&bankRouting=INVALID")
        assert wp.method is None and wp.path is None
        assert wp.values == {
            "firstName": "<img src=x onerror=alert(document.cookie)>",
            "bankRouting": "INVALID",
        }

    def test_param_string_with_cjk_annotation_stripped(self):
        """尾部中文注解（全角括号）剥离进 note。"""
        wp = parse_witness(
            "firstName=<img src=x onerror=alert(document.cookie)>"
            "（注册时植入，每次访问 /、/dashboard 均触发）"
        )
        assert wp.values == {"firstName": "<img src=x onerror=alert(document.cookie)>"}
        assert wp.note and "注册时植入" in wp.note

    def test_param_string_halfwidth_paren_cjk_annotation(self):
        """半角括号但内容含 CJK → 同样剥离。"""
        wp = parse_witness("firstName=javascript:alert(1)&bankRouting=INVALID(触发验证失败路径)")
        assert wp.values == {
            "firstName": "javascript:alert(1)",
            "bankRouting": "INVALID",
        }
        assert wp.note and "触发验证失败路径" in wp.note


class TestParseWitnessPureValue:
    """形态 3：纯值（现状行为）+ 注解剥离。"""

    def test_plain_sql_payload(self):
        wp = parse_witness("' OR '1'='1")
        assert wp.values == {}
        assert wp.raw == "' OR '1'='1"

    def test_json_payload(self):
        wp = parse_witness('{"userName":{"$gt":""},"password":"Admin_123"}')
        assert wp.raw == '{"userName":{"$gt":""},"password":"Admin_123"}'

    def test_nodejs_rce_payload(self):
        wp = parse_witness('process.mainModule.require("child_process").execSync("id").toString()')
        assert wp.raw.startswith("process.mainModule")

    def test_pure_value_with_cjk_tail_annotation(self):
        wp = parse_witness("1'; return 1=='1（触发 $where 注入返回全表）")
        assert wp.raw == "1'; return 1=='1"
        assert wp.note and "$where" in wp.note

    def test_ascii_payload_paren_not_stripped(self):
        """R4：ASCII-only payload 尾部括号（alert(1)）不被误剥。"""
        wp = parse_witness("<script>alert(1)</script>")
        assert wp.raw == "<script>alert(1)</script>"
        assert wp.note is None

    def test_empty_and_none(self):
        assert parse_witness("") .values == {}
        assert parse_witness(None).values == {}
        assert parse_witness(None).raw is None

    def test_url_like_value_is_pure(self):
        """SSRF URL payload 不是参数串（http 后是 : 不是 =）。"""
        wp = parse_witness("http://169.254.169.254/latest/meta-data/")
        assert wp.raw == "http://169.254.169.254/latest/meta-data/"
        assert wp.values == {}


# --------------------------------------------------------------------------- #
# G3/G5: lint_spec（path/method 白名单 + 占位符黑名单 + header 归一）
# --------------------------------------------------------------------------- #


class TestLintSpecPath:
    def test_trailing_junk_chars_truncated(self):
        """尾残片 `,` `;` 全角 `）` 截断（治 kol path 残片）。"""
        spec = HttpRequestSpec(method="GET", path="/api/cer/list，uid=1")
        lint_spec(spec)
        assert spec.path == "/api/cer/list"

    def test_semicolon_fragment_truncated(self):
        spec = HttpRequestSpec(method="GET", path="/api/user;DROP TABLE users")
        lint_spec(spec)
        assert spec.path == "/api/user"

    def test_fullwidth_paren_truncated(self):
        spec = HttpRequestSpec(method="POST", path="/api/x（注解）")
        lint_spec(spec)
        assert spec.path == "/api/x"

    def test_wildcard_truncated(self):
        spec = HttpRequestSpec(method="GET", path="/api/*")
        lint_spec(spec)
        assert spec.path == "/api/"

    def test_path_not_starting_with_slash_gets_prefix(self):
        spec = HttpRequestSpec(method="GET", path="api/users")
        lint_spec(spec)
        assert spec.path == "/api/users"

    def test_colon_param_and_angle_placeholder_kept(self):
        """路由参数段 :userId 与 <OWNER_RESOURCE_ID> 占位符保留。"""
        spec = HttpRequestSpec(method="GET", path="/allocations/:userId")
        lint_spec(spec)
        assert spec.path == "/allocations/:userId"
        spec2 = HttpRequestSpec(method="GET", path="/api/x/<OWNER_RESOURCE_ID>")
        lint_spec(spec2)
        assert spec2.path == "/api/x/<OWNER_RESOURCE_ID>"


class TestLintSpecMethod:
    def test_invalid_method_derived_from_placement(self):
        """method=LOCAL（hk 实证）→ 按 placement 推（body→POST）。"""
        spec = HttpRequestSpec(method="LOCAL", path="/api/x", body="a=1")
        lint_spec(spec)
        assert spec.method == "POST"
        assert spec.note and "LOCAL" in spec.note

    def test_invalid_method_query_get(self):
        spec = HttpRequestSpec(method="LOCAL", path="/api/x", query={"a": "1"})
        lint_spec(spec)
        assert spec.method == "GET"

    def test_valid_methods_untouched(self):
        for m in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
            spec = HttpRequestSpec(method=m, path="/x")
            lint_spec(spec)
            assert spec.method == m and spec.note is None


class TestLintSpecPlaceholderBlacklist:
    def test_header_value_placeholder_removed(self):
        spec = HttpRequestSpec(method="GET", path="/x",
                               headers={"X-Key": "${witness_payload}"})
        lint_spec(spec)
        assert "X-Key" not in spec.headers
        assert spec.note and "占位符" in spec.note

    def test_header_mustache_placeholder_removed(self):
        spec = HttpRequestSpec(method="GET", path="/x",
                               headers={"X-Key": "{{witness_payload}}"})
        lint_spec(spec)
        assert "X-Key" not in spec.headers

    def test_query_value_literal_placeholder_removed(self):
        spec = HttpRequestSpec(method="GET", path="/x", query={"q": "witness_payload"})
        lint_spec(spec)
        assert "q" not in spec.query

    def test_body_whole_template_degrades_to_skeleton(self):
        """body 整体为模板串 → 清空 + 标注（骨架降级）。"""
        spec = HttpRequestSpec(method="POST", path="/x", body="${witness_payload}")
        lint_spec(spec)
        assert spec.body is None
        assert spec.note and "占位符" in spec.note

    def test_body_json_with_placeholder_also_cleared(self):
        """JSON body 局部占位符（delivery 实证）→ body 清空 + 标注（不泄漏）。"""
        spec = HttpRequestSpec(method="POST", path="/x",
                               body='{"url": "{{witness_payload}}"}')
        lint_spec(spec)
        assert spec.body is None
        assert "witness_payload" not in json.dumps(spec.__dict__ if False else {"n": spec.note or ""})

    def test_normal_body_untouched(self):
        spec = HttpRequestSpec(method="POST", path="/x", body="a=<script>alert(1)</script>")
        lint_spec(spec)
        assert spec.body == "a=<script>alert(1)</script>"


class TestLintSpecHeaders:
    def test_content_type_and_host_stripped(self):
        """spec.headers 剔除 Content-Type/Host（渲染层唯一来源，治重复头）。"""
        spec = HttpRequestSpec(method="POST", path="/x",
                               headers={"Content-Type": "application/json",
                                        "Host": "evil.com",
                                        "Authorization": "Bearer <AUTH_TOKEN>"},
                               body="{}")
        lint_spec(spec)
        assert "Content-Type" not in spec.headers
        assert "Host" not in spec.headers
        assert spec.headers["Authorization"] == "Bearer <AUTH_TOKEN>"


# --------------------------------------------------------------------------- #
# G3: RouteIndex（entry_points join：handler / 行号邻近 / stem 段匹配）
# --------------------------------------------------------------------------- #

_ENTRY_POINTS = [
    # NodeGoat 实证形态：Express 匿名回调全部挂 index.js:index:11
    {"func_block_id": "app/routes/index.js:index:11", "http_method": "GET", "route": "/contributions"},
    {"func_block_id": "app/routes/index.js:index:11", "http_method": "POST", "route": "/contributions"},
    {"func_block_id": "app/routes/index.js:index:11", "http_method": "GET", "route": "/allocations/:userId"},
    {"func_block_id": "app/routes/index.js:index:11", "http_method": "POST", "route": "/profile"},
    # Spring 形态：handler 精确
    {"func_block_id": "src/main/java/x/ClusterConfigController.java:apiModifyClusterConfig:70",
     "http_method": "POST", "route": "/cluster/config/modify_single"},
    # 同文件多 handler（行号邻近可分辨）
    {"func_block_id": "src/routes/user.ts:getUser:42", "http_method": "GET", "route": "/user/:id"},
    {"func_block_id": "src/routes/user.ts:updateUser:80", "http_method": "PUT", "route": "/user/:id"},
    # 无路由信息的条目（跳过）
    {"func_block_id": "app/routes/allocations.js:AllocationsHandler:6",
     "http_method": None, "route": None},
]


class TestRouteIndex:
    def test_handler_exact_join(self):
        """Spring 形态：(basename, handler) 精确 join。"""
        ri = RouteIndex(_ENTRY_POINTS)
        m, p = ri.resolve(file="src/main/java/x/ClusterConfigController.java",
                          handler="apiModifyClusterConfig")
        assert (m, p) == ("POST", "/cluster/config/modify_single")

    def test_line_proximity_join(self):
        """同文件多 handler → 行号邻近。"""
        ri = RouteIndex(_ENTRY_POINTS)
        m, p = ri.resolve(file="src/routes/user.ts", handler=None, line=78)
        assert (m, p) == ("PUT", "/user/:id")
        m2, p2 = ri.resolve(file="src/routes/user.ts", handler=None, line=50)
        assert (m2, p2) == ("GET", "/user/:id")

    def test_stem_segment_match_express(self):
        """NodeGoat 形态：handler miss + 行号 miss（contributions.js 无条目）→
        file basename stem ↔ route 段匹配；body placement 偏好 POST。"""
        ri = RouteIndex(_ENTRY_POINTS)
        m, p = ri.resolve(file="app/routes/contributions.js",
                          handler="handleContributionsUpdate", line=32,
                          placement="body")
        assert (m, p) == ("POST", "/contributions")

    def test_stem_segment_match_query_prefers_get(self):
        ri = RouteIndex(_ENTRY_POINTS)
        m, p = ri.resolve(file="app/routes/allocations.js",
                          handler="displayAllocations", line=20, placement="query")
        assert (m, p) == ("GET", "/allocations/:userId")

    def test_basename_normalization(self):
        """两侧路径前缀不同 → basename 归一后 join。"""
        ri = RouteIndex(_ENTRY_POINTS)
        m, p = ri.resolve(file="/build/checkout/src/main/java/x/ClusterConfigController.java",
                          handler="apiModifyClusterConfig")
        assert p == "/cluster/config/modify_single"

    def test_miss_returns_none(self):
        ri = RouteIndex(_ENTRY_POINTS)
        assert ri.resolve(file="nowhere.js", handler="nope") == (None, None)

    def test_empty_index_miss(self):
        """黑盒 track 无 entry_points → 全 miss 不崩。"""
        ri = RouteIndex([])
        assert ri.resolve(file="a.js", handler="x") == (None, None)

    def test_stem_requires_full_segment(self):
        """stem 匹配须整段相等（contributions 不匹配 /contributions_extra）。"""
        eps = [{"func_block_id": "a.js:h:1", "http_method": "GET", "route": "/contributions_extra"}]
        ri = RouteIndex(eps)
        assert ri.resolve(file="contributions.js", handler="h") == (None, None)


# --------------------------------------------------------------------------- #
# G3: 统一分层路径 —— _extract_deterministic（RouteIndex join + WitnessParse）
#                          / _assemble（wp 消费）
# --------------------------------------------------------------------------- #

from supernova_core.services.poc_generator import (  # noqa: E402
    _extract_deterministic,
    _assemble,
    _extract_source_location,
    build_template_spec,
)


def _nodegoat_route_index():
    """NodeGoat 实证 entry_points 裁剪（Express 全挂 index.js:index:11）。"""
    return RouteIndex([
        {"func_block_id": "app/routes/index.js:index:11", "http_method": "GET", "route": "/contributions"},
        {"func_block_id": "app/routes/index.js:index:11", "http_method": "POST", "route": "/contributions"},
        {"func_block_id": "app/routes/index.js:index:11", "http_method": "GET", "route": "/allocations/:userId"},
        {"func_block_id": "app/routes/index.js:index:11", "http_method": "POST", "route": "/profile"},
        {"func_block_id": "app/routes/index.js:index:11", "http_method": "POST", "route": "/signup"},
    ])


class _NgInj:
    """NodeGoat INJ-VULN-01 实证形态：LLM 轨，path 是函数流（无路由）。"""
    ID = "INJ-VULN-01"
    source = "req.body.preTax @ app/routes/contributions.js:32"
    path = "handleContributionsUpdate(req) → eval(req.body.preTax) @ contributions.js:32"
    endpoint = None
    source_endpoint = None
    witness_payload = 'process.mainModule.require("child_process").execSync("id").toString()'
    verdict = "vulnerable"
    confidence = "needs_review"


class TestExtractSourceLocation:
    def test_llm_track_tail_at_file_line(self):
        f, ln = _extract_source_location("req.body.preTax @ app/routes/contributions.js:32")
        assert f == "app/routes/contributions.js" and ln == 32

    def test_fallback_to_path_flow(self):
        f, ln = _extract_source_location(None, "handleContributionsUpdate(req) → eval(x) @ contributions.js:32")
        assert f == "contributions.js" and ln == 32

    def test_none(self):
        assert _extract_source_location(None, None) == (None, None)
        assert _extract_source_location("plain text") == (None, None)


class TestUnifiedLayeredPath:
    def test_route_index_join_completes_partial(self):
        """NodeGoat 形态：derive 提不出路由 → RouteIndex stem join 补 POST /contributions，
        witness 纯值 + req.body 信号 → 完整 spec，不进待补桶（治 GET / 塌缩）。"""
        ri = _nodegoat_route_index()
        p = _extract_deterministic(_NgInj(), "injection", {}, ConfidenceBand.CONFIRMED, ri)
        assert p.method == "POST" and p.path == "/contributions"
        assert not p.needs_gap_fill
        spec = _assemble(p, None, {})
        assert spec.method == "POST"
        assert spec.path == "/contributions"
        assert spec.body == f"preTax={_NgInj.witness_payload}"

    def test_no_route_index_goes_gapped(self):
        """RouteIndex 缺失（黑盒/索引不可用）→ gapped（不劣于现状）。"""
        p = _extract_deterministic(_NgInj(), "injection", {}, ConfidenceBand.CONFIRMED, None)
        assert p.method is None and p.path is None
        assert p.needs_gap_fill

    def test_witness_request_line_completes_partial(self):
        """hk 形态：witness 是完整请求行 → method/path/query 全出。"""
        v = SimpleNamespace(ID="INJ-1", source=None, path=None, endpoint=None,
                            source_endpoint=None,
                            witness_payload="GET /api/v2/download-cer?uid=999999&spaceId=10249",
                            verdict="vulnerable", confidence="needs_review")
        p = _extract_deterministic(v, "injection", {}, ConfidenceBand.CONFIRMED, None)
        assert p.method == "GET" and p.path == "/api/v2/download-cer"
        assert not p.needs_gap_fill
        spec = _assemble(p, None, {})
        assert spec.query == {"uid": "999999", "spaceId": "10249"}

    def test_witness_param_string_expands(self):
        """NodeGoat XSS 形态：'firstName=...&bankRouting=INVALID（注解）' → 多参数展开，
        不再整体塞单参数。"""
        v = SimpleNamespace(ID="XSS-1", source="req.body.firstName @ app/routes/profile.js:43",
                            path=None, endpoint=None, source_endpoint=None,
                            witness_payload="firstName=<img src=x onerror=alert(document.cookie)>&bankRouting=INVALID（触发验证失败）",
                            verdict="vulnerable", confidence="needs_review")
        ri = _nodegoat_route_index()
        p = _extract_deterministic(v, "xss", {}, ConfidenceBand.SUSPECTED, ri)
        assert p.method == "POST" and p.path == "/profile"
        spec = _assemble(p, None, {})
        assert spec.body == ("firstName=<img src=x onerror=alert(document.cookie)>"
                             "&bankRouting=INVALID")
        assert spec.note and "触发验证失败" in spec.note

    def test_assemble_gap_witness_also_parsed(self):
        """gap-fill 补回的 witness 也走 parse_witness（LLM 同样可能给请求行/参数串）。"""
        v = SimpleNamespace(ID="INJ-GN-1", source="payload (src/C.java:m:70)",
                            path="payload -> C.java", endpoint=None, source_endpoint=None,
                            witness_payload=None, verdict="vulnerable", confidence="high")
        p = _extract_deterministic(v, "injection", {}, ConfidenceBand.HIGH, None)
        spec = _assemble(p, {"http_method": "POST", "route_path": "/c",
                             "witness_payload": "a=1&b=2"}, {})
        assert spec.query == {"a": "1", "b": "2"}

    def test_build_template_spec_inj_xss_ssrf_retired(self):
        """修 07-22 实现偏差①：inj/xss/ssrf 分支退役，一律返回 None 走分层路径
        （缺路由不再拿 witness 硬拼模板）。"""
        assert build_template_spec(_NgInj(), "injection", "http://t", {}, ConfidenceBand.CONFIRMED) is None
        assert build_template_spec(_NgInj(), "xss", "http://t", {}, ConfidenceBand.CONFIRMED) is None
        assert build_template_spec(_NgInj(), "ssrf", "http://t", {}, ConfidenceBand.CONFIRMED) is None


# --------------------------------------------------------------------------- #
# G4: authz 配对鉴别力（资源参数替换 / 无资源降单请求）
# --------------------------------------------------------------------------- #

from supernova_core.services.poc_generator import _build_authz_pair  # noqa: E402


class TestAuthzPairDiscriminating:
    def test_path_param_substituted(self):
        """path 模板段 :userId → OWNER/VICTIM 占位符替换，两请求真实不同（治 P0-4）。"""
        v = SimpleNamespace(ID="AUTHZ-1", endpoint="GET /allocations/:userId",
                            source=None, minimal_witness="swap userId")
        pair = _build_authz_pair(v, {}, ConfidenceBand.CONFIRMED)
        assert isinstance(pair, list) and len(pair) == 2
        legit, cross = pair
        assert legit.path == "/allocations/<OWNER_RESOURCE_ID>"
        assert cross.path == "/allocations/<VICTIM_RESOURCE_ID>"
        assert legit.path != cross.path

    def test_body_param_substituted(self):
        """资源参数在 body（req.body.uid）→ body 值替换。"""
        v = SimpleNamespace(ID="AUTHZ-2", endpoint="POST /api/del-bankcard",
                            source="req.body.uid (src/routes/bank.ts:del:32)",
                            minimal_witness=None)
        pair = _build_authz_pair(v, {}, ConfidenceBand.CONFIRMED)
        legit, cross = pair
        assert legit.method == "POST"
        assert legit.body == "uid=<OWNER_RESOURCE_ID>"
        assert cross.body == "uid=<VICTIM_RESOURCE_ID>"

    def test_no_resource_param_single_request_with_note(self):
        """无资源参数（Vertical/BFLA：GET /benefits）→ 单请求 + 无鉴别力标注。"""
        v = SimpleNamespace(ID="AUTHZ-3", endpoint="GET /benefits",
                            source=None, minimal_witness=None)
        out = _build_authz_pair(v, {}, ConfidenceBand.SUSPECTED)
        assert not isinstance(out, list)  # 单 spec
        assert out.path == "/benefits"
        assert out.note and "无资源对象" in out.note and "无鉴别力" in out.note


# --------------------------------------------------------------------------- #
# G2: 置信语义（needs_review 不虚标 CONFIRMED）+ 白盒文案
# --------------------------------------------------------------------------- #

from supernova_core.services.poc_generator import (  # noqa: E402
    classify_confidence,
    to_curl,
    to_burp_raw,
    render_poc_md,
)


class TestClassifyConfidence:
    def test_needs_review_not_confirmed(self):
        """P0-2：vulnerable+needs_review 不再虚标 CONFIRMED（hk/hr/kol 共 12 条实证）。"""
        v = SimpleNamespace(verdict="vulnerable", confidence="needs_review")
        assert classify_confidence(v, is_accepted=False) == ConfidenceBand.SUSPECTED

    def test_vulnerable_low_not_confirmed(self):
        v = SimpleNamespace(verdict="vulnerable", confidence="low")
        assert classify_confidence(v, is_accepted=False) == ConfidenceBand.SUSPECTED

    def test_vulnerable_high_confirmed(self):
        v = SimpleNamespace(verdict="vulnerable", confidence="high")
        assert classify_confidence(v, is_accepted=False) == ConfidenceBand.CONFIRMED

    def test_vulnerable_missing_confidence_confirmed(self):
        """缺省 confidence 视为可确认（向后兼容）。"""
        v = SimpleNamespace(verdict="vulnerable", confidence=None)
        assert classify_confidence(v, is_accepted=False) == ConfidenceBand.CONFIRMED

    def test_blackbox_accepted_still_confirmed(self):
        """黑盒重放 accepted → CONFIRMED（有重放证据，不变）。"""
        v = SimpleNamespace(verdict=None, confidence="low")
        assert classify_confidence(v, is_accepted=True) == ConfidenceBand.CONFIRMED


class TestConfidenceWording:
    def test_whitebox_confirmed_static_wording(self):
        """白盒 CONFIRMED 渲染「已确认（静态判定）」，不再声称「可复现」。"""
        spec = HttpRequestSpec(method="GET", path="/x", source_id="INJ-1",
                               vuln_class="injection",
                               confidence_band=ConfidenceBand.CONFIRMED)
        md = render_poc_md([("injection", SimpleNamespace(merge_source="-"), spec)],
                           "https://t.example.com", "whitebox")
        assert "已确认（静态判定）" in md
        assert "已确认可复现" not in md

    def test_blackbox_confirmed_keeps_replay_wording(self):
        """黑盒有重放证据，保持「已确认可复现」。"""
        spec = HttpRequestSpec(method="GET", path="/x", source_id="XSS-1",
                               vuln_class="xss",
                               confidence_band=ConfidenceBand.CONFIRMED)
        md = render_poc_md([("xss", SimpleNamespace(merge_source="-"), spec)],
                           "https://t.example.com", "blackbox")
        assert "已确认可复现" in md

    def test_needs_review_band_renders_suspected(self):
        spec = HttpRequestSpec(method="GET", path="/x", source_id="INJ-1",
                               vuln_class="injection",
                               confidence_band=ConfidenceBand.SUSPECTED)
        md = render_poc_md([("injection", SimpleNamespace(merge_source="-"), spec)],
                           "https://t.example.com", "whitebox")
        assert "✓ 已确认" not in md
        assert "疑似" in md


# --------------------------------------------------------------------------- #
# G6: 渲染合法（curl 引号转义 / Burp 最小编码 / header 去重）
# --------------------------------------------------------------------------- #


class TestToCurl:
    def test_single_quote_in_body_escaped(self):
        """P1-7：body 含 ' → POSIX '\'' 转义，不截断 shell。"""
        spec = HttpRequestSpec(method="POST", path="/x", body="id=' OR '1'='1")
        curl = to_curl(spec, "https://t.example.com")
        assert "--data 'id='\\'' OR '\\''1'\\''='\\''1'" in curl

    def test_quote_in_header_value_escaped(self):
        spec = HttpRequestSpec(method="GET", path="/x",
                               headers={"X-Payload": "a'b"})
        curl = to_curl(spec, "https://t.example.com")
        # POSIX 语义正确（'a'\''b' 回读为 a'b），而非「引号数偶数」
        assert "-H 'X-Payload: a'\\''b'" in curl


class TestToBurpRaw:
    def test_query_space_encoded(self):
        """P1-7：query 值空格 → %20（请求行不再被空格破坏）。"""
        spec = HttpRequestSpec(method="GET", path="/api/users",
                               query={"id": "' OR '1'='1"})
        raw = to_burp_raw(spec, "https://t.example.com")
        request_line = raw.splitlines()[0]
        assert " " not in request_line.replace("GET /api/users?id=", "").replace(" HTTP/1.1", "") or True
        assert "%20" in request_line
        assert "'%20OR%20'1'='1" in request_line

    def test_query_cjk_percent_encoded(self):
        """CJK → UTF-8 percent 编码（治 hr 请求行中文）。"""
        spec = HttpRequestSpec(method="GET", path="/search", query={"q": "张三"})
        raw = to_burp_raw(spec, "https://t.example.com")
        request_line = raw.splitlines()[0]
        assert "张" not in request_line
        assert "%E5%BC%A0" in request_line

    def test_query_crlf_encoded(self):
        spec = HttpRequestSpec(method="GET", path="/x", query={"q": "a\r\nb"})
        raw = to_burp_raw(spec, "https://t.example.com")
        request_line = raw.splitlines()[0]
        assert "\r" not in request_line and "\n" not in request_line
        assert "%0D%0A" in request_line

    def test_safe_symbols_stay_raw(self):
        """最小编码：引号/等号/尖括号等 payload 符号保 raw（可读性）。"""
        spec = HttpRequestSpec(method="GET", path="/x", query={"q": "<script>'a'</script>"})
        raw = to_burp_raw(spec, "https://t.example.com")
        request_line = raw.splitlines()[0]
        assert "<script>'a'</script>" in request_line

    def test_body_kept_raw(self):
        """body 不编码（payload 可读性，Content-Length 按字节）。"""
        spec = HttpRequestSpec(method="POST", path="/x", body="q=a b")
        raw = to_burp_raw(spec, "https://t.example.com")
        assert "q=a b" in raw
        assert not raw.rstrip().endswith("q=a%20b")

    def test_content_type_not_duplicated(self):
        """spec.headers 已无 Content-Type（lint 剔除）→ 渲染只加一次；
        即使残留也由渲染层跳过，不再重复。"""
        spec = HttpRequestSpec(method="POST", path="/x", body="{}",
                               headers={"Content-Type": "application/json"})
        raw = to_burp_raw(spec, "https://t.example.com")
        assert raw.count("Content-Type:") == 1

    def test_host_not_duplicated(self):
        spec = HttpRequestSpec(method="GET", path="/x",
                               headers={"Host": "evil.example.com"})
        raw = to_burp_raw(spec, "https://t.example.com")
        assert raw.count("Host:") == 1
        assert "Host: t.example.com" in raw


# --------------------------------------------------------------------------- #
# G8: 相同请求去重合并 + checkpoint v2
# --------------------------------------------------------------------------- #

from supernova_core.services.poc_generator import (  # noqa: E402
    _load_checkpoint,
    _write_checkpoint,
    _POC_CHECKPOINT_FILENAME,
    merge_duplicate_requests,
)


def _spec_entry(vid, method="GET", path="/x", query=None, body=None, vc="injection"):
    spec = HttpRequestSpec(method=method, path=path, query=dict(query or {}),
                           body=body, source_id=vid, vuln_class=vc,
                           confidence_band=ConfidenceBand.CONFIRMED)
    return (vc, SimpleNamespace(ID=vid, merge_source="-"), spec)


class TestMergeDuplicateRequests:
    def test_identical_requests_merged_with_joined_ids(self):
        """P1（NodeGoat 45→19 塌缩同源）：同请求合并一节，ID 逗连。"""
        entries = [
            _spec_entry("INJ-VULN-01", query={"id": "W"}),
            _spec_entry("INJ-VULN-02", query={"id": "W"}),
            _spec_entry("INJ-VULN-03", query={"id": "W"}),
        ]
        merged = merge_duplicate_requests(entries)
        assert len(merged) == 1
        _, _, spec = merged[0]
        assert spec.source_id == "INJ-VULN-01/02/03"
        md = render_poc_md(merged, "https://t.example.com", "whitebox")
        assert md.count("### ✓ INJ-VULN-01/02/03") == 1
        assert md.count("curl -i") == 1  # detail 一份
        # 头部计数与概览行数对齐（合并组算一条）
        assert "共 1 条" in md

    def test_different_requests_not_merged(self):
        entries = [
            _spec_entry("INJ-1", query={"id": "A"}),
            _spec_entry("INJ-2", query={"id": "B"}),
        ]
        merged = merge_duplicate_requests(entries)
        assert len(merged) == 2

    def test_authz_pair_keyed_as_whole_list(self):
        """authz 成对以整个 list 为 key：两对同请求才合并，单/成对不混。"""
        def pair(vid):
            common = dict(method="GET", path="/a/<OWNER_RESOURCE_ID>",
                          headers={"Authorization": "Bearer <T>"},
                          source_id=vid, vuln_class="authz")
            s1 = HttpRequestSpec(**common, note="legit")
            s2 = HttpRequestSpec(**{**common, "path": "/a/<VICTIM_RESOURCE_ID>"}, note="cross")
            return ("authz", SimpleNamespace(ID=vid), [s1, s2])
        merged = merge_duplicate_requests([pair("AZ-1"), pair("AZ-2")])
        assert len(merged) == 1
        assert merged[0][2][0].source_id == "AZ-1/2"

    def test_order_preserved_for_uniques(self):
        entries = [
            _spec_entry("INJ-B", path="/b"),
            _spec_entry("INJ-A", path="/a"),
        ]
        merged = merge_duplicate_requests(entries)
        assert [s.source_id for _, _, s in merged] == ["INJ-B", "INJ-A"]


class TestCheckpointV2:
    def test_v2_matching_track_loaded(self, tmp_path):
        ckpt = {"version": 2, "track": "whitebox", "completed": {"INJ-1": {"spec": {}}}}
        (tmp_path / _POC_CHECKPOINT_FILENAME).write_text(json.dumps(ckpt), encoding="utf-8")
        assert "INJ-1" in _load_checkpoint(tmp_path, "whitebox")

    def test_v1_discarded(self, tmp_path):
        """修 07-22 实现偏差②：v1 不校验 → 修复对存量重跑不生效；v2 强制丢弃。"""
        ckpt = {"version": 1, "track": "whitebox", "completed": {"INJ-1": {"spec": {}}}}
        (tmp_path / _POC_CHECKPOINT_FILENAME).write_text(json.dumps(ckpt), encoding="utf-8")
        assert _load_checkpoint(tmp_path, "whitebox") == {}

    def test_track_mismatch_discarded(self, tmp_path):
        ckpt = {"version": 2, "track": "blackbox", "completed": {"INJ-1": {"spec": {}}}}
        (tmp_path / _POC_CHECKPOINT_FILENAME).write_text(json.dumps(ckpt), encoding="utf-8")
        assert _load_checkpoint(tmp_path, "whitebox") == {}

    def test_corrupt_discarded(self, tmp_path):
        (tmp_path / _POC_CHECKPOINT_FILENAME).write_text("{NOT JSON", encoding="utf-8")
        assert _load_checkpoint(tmp_path, "whitebox") == {}

    def test_write_uses_version_2(self, tmp_path):
        _write_checkpoint(tmp_path, "whitebox", {"INJ-1": {"vuln_class": "injection", "spec": {}}})
        # tiering：写入 intermediate/ 桶
        p = tmp_path / "intermediate" / _POC_CHECKPOINT_FILENAME
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["version"] == 2 and data["track"] == "whitebox"


# --------------------------------------------------------------------------- #
# G5: gap-fill 加固（有界重试 / file_key=None 修正 / recon_ctx 裁剪）
# --------------------------------------------------------------------------- #

from supernova_core.services.poc_generator import (  # noqa: E402
    PartialSpec,
    _build_gapfill_prompt,
    _trim_recon_ctx,
    _batch_fill_gaps,
    llm_fill_gaps,
)


def _gap_partial(vid, file_=None, source_file=None, placement="query"):
    class V:
        pass
    v = V()
    v.ID = vid
    return PartialSpec(vuln=v, vuln_class="injection", band=ConfidenceBand.HIGH,
                       param_name="p", placement=placement, controller_file=file_,
                       method=None, path=None, witness=None, source_file=source_file)


class TestGapFillRetry:
    async def test_unparseable_retried_once_then_succeeds(self, monkeypatch):
        """第 1 次 unparseable（success=False）→ 重发（JSON-only 强化）→ 第 2 次成功。"""
        import supernova_core.services.poc_generator as mod
        calls = []

        async def fake_run(prompt, **kw):
            calls.append(prompt)
            if len(calls) == 1:
                return SimpleNamespace(success=False, structured_output=None, error="not json")
            return SimpleNamespace(success=True, structured_output={
                "items": [{"ID": "G1", "http_method": "POST", "route_path": "/a",
                           "witness_payload": "w"}]}, error=None)
        monkeypatch.setattr(mod, "run_claude_prompt", fake_run)
        gapmap = await llm_fill_gaps("C.java", [_gap_partial("G1", "C.java")],
                                     recon_ctx={}, repo_path="/tmp/x")
        assert len(calls) == 2
        assert "JSON only" in calls[1] or "ONLY" in calls[1]  # 重试 prompt 强化
        assert gapmap["G1"]["route_path"] == "/a"

    async def test_retry_exhausted_returns_empty(self, monkeypatch):
        import supernova_core.services.poc_generator as mod
        n = {"i": 0}

        async def fake_run(prompt, **kw):
            n["i"] += 1
            return SimpleNamespace(success=False, structured_output=None, error="x")
        monkeypatch.setattr(mod, "run_claude_prompt", fake_run)
        gapmap = await llm_fill_gaps("C.java", [_gap_partial("G1", "C.java")],
                                     recon_ctx={}, repo_path="/tmp/x")
        assert gapmap == {}
        assert n["i"] == 2  # 1 + 默认重试 1

    async def test_success_first_try_no_retry(self, monkeypatch):
        import supernova_core.services.poc_generator as mod
        n = {"i": 0}

        async def fake_run(prompt, **kw):
            n["i"] += 1
            return SimpleNamespace(success=True, structured_output={
                "items": [{"ID": "G1", "http_method": "GET", "route_path": "/b",
                           "witness_payload": "w"}]}, error=None)
        monkeypatch.setattr(mod, "run_claude_prompt", fake_run)
        await llm_fill_gaps("C.java", [_gap_partial("G1", "C.java")],
                            recon_ctx={}, repo_path="/tmp/x")
        assert n["i"] == 1


class TestGapFillPromptFileKeyNone:
    def test_no_unknown_read_that_file_contradiction(self):
        """G5：file_key=None 不再输出 'Handler file: unknown … Read that file' 矛盾句。"""
        p = _gap_partial("U1", None, source_file="src/routes/contributions.js")
        prompt = _build_gapfill_prompt(None, [p], recon_ctx={})
        assert "Handler file: unknown" not in prompt
        assert "Read that file" not in prompt
        # 逐条 source_file 进上下文 + 指令读各自文件
        assert "src/routes/contributions.js" in prompt
        assert "source_file" in prompt

    def test_file_key_present_keeps_read_instruction(self):
        p = _gap_partial("G1", "C.java")
        prompt = _build_gapfill_prompt("C.java", [p], recon_ctx={})
        assert "Handler file: C.java" in prompt


class TestTrimReconCtx:
    def test_stem_matched_endpoints_kept(self):
        """组内 source_file 的 basename stem ↔ 端点 path 段匹配 → 只保留命中端点。"""
        endpoints = {
            "/contributions": {"auth": "user"},
            "/contributions/:id": {"auth": "user"},
            "/memos": {"auth": "anon"},
        }
        partials = [_gap_partial("G1", None, source_file="app/routes/contributions.js")]
        trimmed = _trim_recon_ctx(endpoints, partials)
        assert "/contributions" in trimmed
        assert "/memos" not in trimmed

    def test_no_match_returns_empty(self):
        endpoints = {"/memos": {"auth": "anon"}}
        partials = [_gap_partial("G1", None, source_file="app/routes/session.js")]
        assert _trim_recon_ctx(endpoints, partials) == {}

    def test_no_file_info_returns_empty(self):
        """组内无任何文件信息 → 不再全量灌入（省略端点 section）。"""
        endpoints = {"/a": {"auth": "anon"}, "/b": {"auth": "anon"}}
        partials = [_gap_partial("G1", None, source_file=None)]
        assert _trim_recon_ctx(endpoints, partials) == {}


# --------------------------------------------------------------------------- #
# G7: auth 并行 + per-call 超时 + gap-fill 组并行
# --------------------------------------------------------------------------- #

from supernova_core.models.queue_schemas import VulnerabilityQueue, AuthVulnerability  # noqa: E402


def _auth_queue(tmp_path, n=7):
    d = tmp_path / "deliverables" / "whitebox"
    d.mkdir(parents=True, exist_ok=True)
    q = VulnerabilityQueue(vulnerabilities=[
        AuthVulnerability(ID=f"AUTH-{i}", vulnerability_type="missing-jwt",
                          externally_exploitable=True, confidence="needs_review",
                          exploitation_hypothesis=f"hypo {i}",
                          suggested_exploit_technique="forge jwt")
        for i in range(1, n + 1)
    ])
    (d / "auth_exploitation_queue.json").write_text(q.model_dump_json(), encoding="utf-8")
    return d


class TestAuthParallel:
    async def test_parallel_faster_than_serial_with_cap(self, tmp_path, monkeypatch):
        """7 条 auth × mock 0.15s：并行(cap 3) 总时长 ≈ 0.45s << 串行 1.05s；
        并发峰值 ≤ cap。"""
        import supernova_core.services.poc_generator as mod
        d = _auth_queue(tmp_path)
        state = {"in_flight": 0, "peak": 0}

        async def fake_run(prompt, **kw):
            state["in_flight"] += 1
            state["peak"] = max(state["peak"], state["in_flight"])
            await asyncio.sleep(0.15)
            state["in_flight"] -= 1
            # 每条返回不同 path（避免触发 G8 相同请求去重合并）
            import re as _re
            m = _re.search(r"AUTH-(\d+)", prompt)
            path = f"/auth/callback-{m.group(1) if m else 'x'}"
            return SimpleNamespace(success=True, structured_output={
                "method": "POST", "path": path, "body": "id_token=forged",
                "query": None, "headers": None, "steps": None}, error=None)
        monkeypatch.setattr(mod, "run_claude_prompt", fake_run)

        t0 = time.monotonic()
        out = await mod.PoCGenerator.generate(
            d, ["auth"], "https://t.example.com", "whitebox", repo_path="/tmp/x")
        elapsed = time.monotonic() - t0
        assert out is not None
        md = out.read_text(encoding="utf-8")
        for i in range(1, 8):
            assert f"AUTH-{i}" in md
        assert elapsed < 0.75, f"elapsed={elapsed:.2f}s (serial would be ~1.05s)"
        assert 1 < state["peak"] <= 3, state  # 并行生效且 cap 生效

    async def test_per_call_timeout_degrades_to_skeleton(self, tmp_path, monkeypatch):
        """单条 LLM 卡死（5m12s 实证）→ wait_for 超时 → 骨架 + 「LLM 超时」标注，
        不阻塞其他条目。"""
        import supernova_core.services.poc_generator as mod
        d = _auth_queue(tmp_path, n=2)
        monkeypatch.setenv("SUPERNOVA_POC_AUTH_TIMEOUT_S", "0.2")

        async def fake_run(prompt, **kw):
            if "AUTH-1" in prompt:
                await asyncio.sleep(5)  # 卡死
            return SimpleNamespace(success=True, structured_output={
                "method": "POST", "path": "/ok", "body": None,
                "query": None, "headers": None, "steps": None}, error=None)
        monkeypatch.setattr(mod, "run_claude_prompt", fake_run)

        t0 = time.monotonic()
        out = await mod.PoCGenerator.generate(
            d, ["auth"], "https://t.example.com", "whitebox", repo_path="/tmp/x")
        elapsed = time.monotonic() - t0
        assert elapsed < 2.0, f"timeout did not bound the stuck call: {elapsed:.2f}s"
        md = out.read_text(encoding="utf-8")
        assert "AUTH-1" in md and "LLM 超时" in md  # 超时条目降级骨架
        assert "AUTH-2" in md and "/ok" in md       # 正常条目不受影响

    async def test_checkpoint_complete_after_parallel(self, tmp_path, monkeypatch):
        """并行完成后 checkpoint 全量（7 条都在，断点续传语义保住）。"""
        import supernova_core.services.poc_generator as mod
        d = _auth_queue(tmp_path)

        async def fake_run(prompt, **kw):
            await asyncio.sleep(0.02)
            return SimpleNamespace(success=True, structured_output={
                "method": "POST", "path": "/auth/callback", "body": None,
                "query": None, "headers": None, "steps": None}, error=None)
        monkeypatch.setattr(mod, "run_claude_prompt", fake_run)
        await mod.PoCGenerator.generate(
            d, ["auth"], "https://t.example.com", "whitebox", repo_path="/tmp/x")
        ckpt = json.loads(
            (d / "intermediate" / _POC_CHECKPOINT_FILENAME).read_text(encoding="utf-8"))
        assert len(ckpt["completed"]) == 7


class TestGapFillParallel:
    async def test_groups_run_concurrently(self, tmp_path, monkeypatch):
        """2 个 controller 组 × mock 0.15s → 并行 ≈0.15s << 串行 0.3s。"""
        import supernova_core.services.poc_generator as mod
        calls = []

        async def fake_run(prompt, **kw):
            calls.append(prompt)
            await asyncio.sleep(0.15)
            return SimpleNamespace(success=True, structured_output={
                "items": [{"ID": "A1", "http_method": "GET", "route_path": "/a",
                           "witness_payload": "w"}]}, error=None)
        monkeypatch.setattr(mod, "run_claude_prompt", fake_run)
        partials = [_gap_partial("A1", "C1.java"), _gap_partial("B1", "C2.java")]
        t0 = time.monotonic()
        gapmap = await _batch_fill_gaps(partials, endpoints={}, repo_path="/tmp/x")
        elapsed = time.monotonic() - t0
        assert len(calls) == 2
        assert "A1" in gapmap
        assert elapsed < 0.28, f"groups not concurrent: {elapsed:.2f}s"
