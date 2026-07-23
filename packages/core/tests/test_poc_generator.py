# packages/core/tests/test_poc_generator.py
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from supernova_core.models.queue_schemas import (
    VulnerabilityQueue, InjectionVulnerability, XssVulnerability,
)
from supernova_core.services.poc_generator import (
    HttpRequestSpec, ConfidenceBand, AuthState,
    extract_method_path, extract_param_name, derive_method_path,
    classify_confidence, resolve_host, derive_auth_state, auth_header,
    parse_recon_endpoints, find_endpoint_info,
    to_curl, to_burp_raw,
    build_template_spec,
    build_llm_prompt, llm_fill_gap, LLM_REQUEST_SCHEMA,
    _spec_from_llm_guess,
    render_poc_md, empty_poc_md,
    PoCGenerator,
)


def test_extract_method_path_from_source():
    assert extract_method_path("GET /share?locale=PAYLOAD → page.js") == ("GET", "/share")
    assert extract_method_path(None) == (None, None)
    assert extract_method_path("no http here") == (None, None)


def test_extract_param_name():
    assert extract_param_name("GET /share?locale=PAYLOAD") == "locale"
    assert extract_param_name("GET /api/users?id=1") == "id"
    assert extract_param_name(None) is None


def test_resolve_host_real_and_placeholder():
    assert resolve_host("https://invite-code.moomoo.com") == "https://invite-code.moomoo.com"
    assert resolve_host("") == "https://TARGET[:PORT]"
    assert resolve_host(None) == "https://TARGET[:PORT]"


def test_classify_confidence_verdict_overrides_confidence():
    class V:
        verdict = "vulnerable"
        confidence = "needs_review"
    assert classify_confidence(V(), is_accepted=False) == ConfidenceBand.CONFIRMED

    class H:
        verdict = None
        confidence = "high"
    assert classify_confidence(H(), is_accepted=False) == ConfidenceBand.HIGH

    class S:
        verdict = None
        confidence = "needs_review"
    assert classify_confidence(S(), is_accepted=False) == ConfidenceBand.SUSPECTED

    class Acc:
        verdict = None
        confidence = "low"
    assert classify_confidence(Acc(), is_accepted=True) == ConfidenceBand.CONFIRMED


def test_derive_auth_state_and_header():
    assert derive_auth_state({"auth": "anon", "middleware": ""}) == AuthState.NONE
    assert derive_auth_state({"auth": "user", "middleware": "oa-login"}) == AuthState.REQUIRED
    assert derive_auth_state(None) == AuthState.UNKNOWN
    # jwt/token → Bearer
    assert auth_header(AuthState.REQUIRED, {"middleware": "jwt-verify"}) == {"Authorization": "Bearer <AUTH_TOKEN>"}
    # session/cookie → Cookie
    assert auth_header(AuthState.REQUIRED, {"middleware": "koa-session"}) == {"Cookie": "session=<SESSION_COOKIE>"}
    # 无需登录 → 无头
    assert auth_header(AuthState.NONE, None) == {}


# Task 2: recon 端点表解析

RECON_SAMPLE = """# Recon

Some intro.

## 2. Endpoint Map

| Method | Path | Handler | Auth (policy) | Parameters | Notes |
|--------|------|---------|---------------|------------|-------|
| GET | `/api/code/payload/share` | share | GUEST | none | public |

## 2.1 Endpoint Security Context

| Method | Path | Auth | Middleware | Framework Origin | Ownership Check | Notes |
|--------|------|------|------------|------------------|-----------------|-------|
| GET | `/api/code/payload/share` | anon | ticketFNS | manual | n/a | public |
| GET | `/api/score/:staffId` | user | oa-login, validator | manual | none | IDOR |
"""


def test_parse_recon_endpoints_finds_security_context(tmp_path):
    recon = tmp_path / "recon_deliverable.md"
    recon.write_text(RECON_SAMPLE, encoding="utf-8")
    eps = parse_recon_endpoints(recon)
    assert "/api/code/payload/share" in eps
    assert eps["/api/code/payload/share"]["auth"] == "anon"
    assert eps["/api/score/:staffid"]["middleware"] == "oa-login, validator"


def test_parse_recon_endpoints_missing_file(tmp_path):
    assert parse_recon_endpoints(tmp_path / "nope.md") == {}


def test_find_endpoint_info_exact_and_prefix():
    eps = {"/api/score/:staffid": {"auth": "user"}, "/api/code/payload/share": {"auth": "anon"}}
    assert find_endpoint_info(eps, "/api/score/:staffId") is not None
    assert find_endpoint_info(eps, "/unknown") is None


# Task 3: curl / Burp raw 双格式化


def test_to_curl_url_encodes_query():
    spec = HttpRequestSpec(
        method="GET", path="/api/users",
        query={"id": "' OR '1'='1"},
        headers={"Authorization": "Bearer <AUTH_TOKEN>"},
    )
    curl = to_curl(spec, "https://invite-code.moomoo.com")
    assert curl.startswith("curl -i -X GET 'https://invite-code.moomoo.com/api/users?")
    assert "%27" in curl  # 单引号被编码
    assert "Authorization: Bearer <AUTH_TOKEN>" in curl


def test_to_curl_placeholder_host():
    spec = HttpRequestSpec(method="GET", path="/share", query={"locale": "<script>"})
    curl = to_curl(spec, "https://TARGET[:PORT]")
    assert "https://TARGET[:PORT]/share?" in curl


def test_to_burp_raw_keeps_raw_payload():
    spec = HttpRequestSpec(
        method="GET", path="/api/users",
        query={"id": "' OR '1'='1"},
        headers={"Authorization": "Bearer <AUTH_TOKEN>"},
    )
    raw = to_burp_raw(spec, "https://invite-code.moomoo.com")
    assert raw.startswith("GET /api/users?id=' OR '1'='1 HTTP/1.1")
    assert "Host: invite-code.moomoo.com" in raw
    assert "' OR '1'='1" in raw  # 原始未编码


def test_to_burp_raw_post_body():
    spec = HttpRequestSpec(method="POST", path="/api/fetch", body="url=http://127.0.0.1:8080")
    raw = to_burp_raw(spec, "https://t.example.com")
    assert "POST /api/fetch HTTP/1.1" in raw
    assert "Content-Length:" in raw
    assert "Content-Type: application/x-www-form-urlencoded" in raw
    assert "url=http://127.0.0.1:8080" in raw


def test_to_burp_raw_json_body_gets_json_content_type():
    """LLM gap-fill 可能返回 JSON body，应标 application/json 而非 form-urlencoded。"""
    spec = HttpRequestSpec(method="POST", path="/auth/callback", body='{"id_token":"forged.none.sig"}')
    raw = to_burp_raw(spec, "https://t.example.com")
    assert "Content-Type: application/json" in raw
    assert "application/x-www-form-urlencoded" not in raw
    assert '{"id_token":"forged.none.sig"}' in raw
    # Content-Length 仍按字节长度
    assert f"Content-Length: {len(spec.body.encode('utf-8'))}" in raw


# Task 4: 模板表（5 类漏洞骨架 + authz 成对 + open_redirect 分流）


def _inj(**kw):
    base = dict(ID="INJ-1", vulnerability_type="SQLi", externally_exploitable=True,
                source="GET /api/users?id=1", witness_payload="' OR '1'='1",
                verdict="vulnerable", confidence="needs_review")
    base.update(kw)
    return SimpleNamespace(**base)


def test_template_injection():
    spec = build_template_spec(_inj(), "injection", "https://t.example.com", {}, ConfidenceBand.CONFIRMED)
    assert spec.method == "GET"
    assert spec.path == "/api/users"
    assert spec.query == {"id": "' OR '1'='1"}


def test_template_ssrf_open_redirect_subform():
    v = SimpleNamespace(ID="SSRF-1", externally_exploitable=True,
                        source="GET /jump?next=https://evil.com",
                        witness_payload="https://evil.com",
                        suggested_exploit_technique="open redirect via next param")
    spec = build_template_spec(v, "ssrf", "https://t.example.com", {}, ConfidenceBand.HIGH)
    assert spec.method == "GET"
    assert spec.query == {"next": "https://evil.com"}


def test_template_ssrf_body_subform():
    v = SimpleNamespace(ID="SSRF-2", externally_exploitable=True,
                        source="POST /api/fetch", witness_payload="http://127.0.0.1:8080",
                        vulnerable_parameter="url", suggested_exploit_technique="ssrf")
    spec = build_template_spec(v, "ssrf", "https://t.example.com", {}, ConfidenceBand.HIGH)
    assert spec.method == "POST"
    assert spec.body == "url=http://127.0.0.1:8080"


def test_template_authz_returns_pair():
    v = SimpleNamespace(ID="AUTHZ-1", externally_exploitable=True,
                        endpoint="GET /api/score/:staffId", minimal_witness="swap staffId")
    result = build_template_spec(v, "authz", "https://t.example.com", {}, ConfidenceBand.CONFIRMED)
    assert isinstance(result, list) and len(result) == 2  # 成对
    assert all(s.path == "/api/score/:staffId" for s in result)


def test_template_auth_returns_none_needs_llm():
    v = SimpleNamespace(ID="AUTH-1", externally_exploitable=True,
                        exploitation_hypothesis="missing jwt verify")
    assert build_template_spec(v, "auth", "https://t.example.com", {}, ConfidenceBand.HIGH) is None


def test_template_no_witness_returns_none_defers_to_llm():
    """inj/xss/ssrf without witness_payload → None (defers to LLM)."""
    v = SimpleNamespace(ID="INJ-X", vulnerability_type="SQLi", externally_exploitable=True,
                        source="GET /api/users?id=1", witness_payload="",
                        verdict=None, confidence="low")
    assert build_template_spec(v, "injection", "https://t.example.com", {}, ConfidenceBand.SUSPECTED) is None


# Task 5: 富信息 LLM 补缺口

_AUTH_VULN = SimpleNamespace(
    ID="AUTH-1", vulnerability_type="missing-jwt-verify",
    externally_exploitable=True,
    exploitation_hypothesis="id_token signature not verified",
    suggested_exploit_technique="forge jwt with alg=none",
    source_endpoint="POST /auth/callback",
    confidence="needs_review",
)


def test_llm_schema_has_required_fields():
    assert LLM_REQUEST_SCHEMA["type"] == "object"
    assert "method" in LLM_REQUEST_SCHEMA["properties"]


def test_build_llm_prompt_is_rich_info():
    prompt = build_llm_prompt(_AUTH_VULN, "auth", "https://t.example.com", {"POST /auth/callback": {"auth": "anon"}})
    # 富信息：含 host、hypothesis、technique（不脱敏）
    assert "https://t.example.com" in prompt
    assert "id_token signature not verified" in prompt
    assert "forge jwt with alg=none" in prompt


async def test_llm_fill_gap_success(monkeypatch):
    async def fake_run(prompt, **kw):
        assert kw.get("structured_output_schema") is LLM_REQUEST_SCHEMA
        return SimpleNamespace(success=True, structured_output={
            "method": "POST", "path": "/auth/callback",
            "body": "id_token=forged.none.sig", "query": None, "headers": None, "steps": None,
        }, error=None)

    import supernova_core.services.poc_generator as mod
    monkeypatch.setattr(mod, "run_claude_prompt", fake_run)
    out = await llm_fill_gap(_AUTH_VULN, "auth", "https://t.example.com", {}, repo_path="/tmp/x")
    assert out["method"] == "POST"
    assert out["body"] == "id_token=forged.none.sig"


async def test_llm_fill_gap_failure_returns_none(monkeypatch):
    async def boom(prompt, **kw):
        raise RuntimeError("llm down")
    import supernova_core.services.poc_generator as mod
    monkeypatch.setattr(mod, "run_claude_prompt", boom)
    out = await llm_fill_gap(_AUTH_VULN, "auth", "https://t.example.com", {}, repo_path="/tmp/x")
    assert out is None


def test_spec_from_llm_guess_normalizes_dict_body():
    """regression：LLM structured_output 不可靠（GLM 无 strict），body 可能返回 dict 而非
    schema 声明的 str。_spec_from_llm_guess 须归一化为 JSON 字符串，否则 to_burp_raw
    的 spec.body.lstrip() 对 dict 崩 -> 整个 PoC 报告丢失（冒泡到顶层 except）。
    """
    v = SimpleNamespace(ID="AUTH-1")
    guess = {"method": "POST", "path": "/auth/callback",
             "body": {"id_token": "forged.none.sig"}, "query": None, "headers": None, "steps": None}
    spec = _spec_from_llm_guess(guess, v, "auth", ConfidenceBand.HIGH)
    assert isinstance(spec.body, str)
    assert spec.body == '{"id_token": "forged.none.sig"}'
    raw = to_burp_raw(spec, "https://t.example.com")
    assert "Content-Type: application/json" in raw
    assert '"id_token": "forged.none.sig"' in raw


def test_spec_from_llm_guess_normalizes_list_body():
    """list body 同样归一化（合法 JSON 数组），走 application/json 分支。"""
    v = SimpleNamespace(ID="X-1")
    guess = {"method": "POST", "path": "/x", "body": ["a", "b"],
             "query": None, "headers": None, "steps": None}
    spec = _spec_from_llm_guess(guess, v, "auth", ConfidenceBand.HIGH)
    assert spec.body == '["a", "b"]'
    raw = to_burp_raw(spec, "https://t.example.com")
    assert "Content-Type: application/json" in raw


def test_spec_from_llm_guess_keeps_str_and_none_body():
    v = SimpleNamespace(ID="A-1")
    s1 = _spec_from_llm_guess({"method": "POST", "path": "/x", "body": "k=v",
                               "query": None, "headers": None, "steps": None}, v, "auth", ConfidenceBand.HIGH)
    assert s1.body == "k=v"
    s2 = _spec_from_llm_guess({"method": "GET", "path": "/x", "body": None,
                               "query": None, "headers": None, "steps": None}, v, "auth", ConfidenceBand.HIGH)
    assert s2.body is None


# Task 6: md 渲染（概览表 + 详细 PoC + 空表兜底）

_INJ_ENTRY = (
    "injection", _inj(),
    HttpRequestSpec(method="GET", path="/api/users", query={"id": "' OR '1'='1"},
                    headers={"Authorization": "Bearer <AUTH_TOKEN>"},
                    auth_state=AuthState.REQUIRED, confidence_band=ConfidenceBand.CONFIRMED,
                    source_id="INJ-1", vuln_class="injection"),
)


def test_render_poc_md_overview_and_detail():
    md = render_poc_md([_INJ_ENTRY], "https://t.example.com", "whitebox")
    assert "# 可利用漏洞 PoC 集合（白盒）" in md
    assert "| ID | 类型 | 路径 | 认证 | 置信度 |" in md
    assert "INJ-1" in md and "injection" in md and "✓ 已确认" in md
    assert "curl -i" in md
    assert "GET /api/users?id=" in md  # Burp raw
    assert "Host: t.example.com" in md
    # placeholder 块现在总是渲染（即便 host 真实，需登录 PoC 仍含占位符）
    assert "⚠️ 使用前替换" in md


def test_render_poc_md_placeholder_block_when_host_missing():
    md = render_poc_md([_INJ_ENTRY], "https://TARGET[:PORT]", "blackbox")
    assert "⚠️ 使用前替换" in md
    assert "TARGET[:PORT]" in md


def test_render_poc_md_authz_pair():
    """authz 成对 PoC：概览表 1 行，详细含「请求 1/2」「请求 2/2」+ 两 curl。"""
    vuln = SimpleNamespace(ID="AUTHZ-1", merge_source="gitnexus")
    common = dict(
        method="GET", path="/api/score/:staffId",
        headers={"Authorization": "Bearer <AUTH_TOKEN_ATTACKER>"},
        auth_state=AuthState.REQUIRED, confidence_band=ConfidenceBand.CONFIRMED,
        source_id="AUTHZ-1", vuln_class="authz",
    )
    spec_legit = HttpRequestSpec(**common, note="合法：访问自己资源（<OWNER_RESOURCE_ID>）")
    spec_cross = HttpRequestSpec(**common, note="越权：访问受害者资源（<VICTIM_RESOURCE_ID>）")
    entry = ("authz", vuln, [spec_legit, spec_cross])
    md = render_poc_md([entry], "https://t.example.com", "whitebox")
    # 概览表仅 AUTHZ-1 一行（不是两行）
    assert md.count("| AUTHZ-1 |") == 1
    # 详细段两条请求标题都在
    assert "（请求 1/2）" in md
    assert "（请求 2/2）" in md
    # 两条 curl 都渲染
    assert md.count("curl -i") == 2
    # 两条 note 都出现
    assert "合法：访问自己资源" in md
    assert "越权：访问受害者资源" in md


def test_empty_poc_md():
    md = empty_poc_md("whitebox")
    assert "无 externally_exploitable" in md


# Task 7: generate() 主流程


def _wb_queue(tmp_path):
    d = tmp_path / "deliverables" / "whitebox"
    d.mkdir(parents=True)
    q = VulnerabilityQueue(vulnerabilities=[
        InjectionVulnerability(
            ID="INJ-1", vulnerability_type="SQLi", externally_exploitable=True,
            confidence="needs_review", verdict="vulnerable",
            source="GET /api/users?id=1", witness_payload="' OR '1'='1",
        ),
        InjectionVulnerability(
            ID="INJ-2", vulnerability_type="SQLi", externally_exploitable=False,  # 被过滤
            confidence="low", source="GET /api/x?id=1", witness_payload="' OR 1=1",
        ),
    ])
    (d / "injection_exploitation_queue.json").write_text(q.model_dump_json(indent=2), encoding="utf-8")
    (d / "recon_deliverable.md").write_text("# R\n\n## 2.1 Endpoint Security Context\n\n| Method | Path | Auth | Middleware |\n|--|--|--|--|\n| GET | `/api/users` | anon | none |\n", encoding="utf-8")
    return d


async def test_generate_writes_poc_md_and_filters(tmp_path):
    d = _wb_queue(tmp_path)
    out = await PoCGenerator.generate(
        deliverables_dir=d, vuln_classes=["injection"],
        target_url="https://t.example.com", track="whitebox",
    )
    assert out.name == "exploitable_poc_collection.md"
    md = out.read_text(encoding="utf-8")
    assert "INJ-1" in md
    assert "INJ-2" not in md  # externally_exploitable=False 被过滤
    assert "curl -i" in md


async def test_generate_emits_progress_via_audit_session(tmp_path, monkeypatch, capsys):
    """spec 组件 3：PoC 进度经 get_audit_session().log_info 发 InfoEvent，不再裸 print。

    注入 mock session，断言：开始行 + 逐条 (i/N) 进度行 + 完成行 都经 log_info；
    且改走 session 后无裸 print 残留到 stdout（capsys 应无进度行）。
    NullAuditSession（无 session 时）no-op，未注入 session 的现有测试不受影响。
    """
    d = _wb_queue(tmp_path)
    info_calls: list[str] = []

    class _RecordingSession:
        async def log_info(self, message, level="info"):
            info_calls.append(message)

    monkeypatch.setattr(
        "supernova_core.services.poc_generator.get_audit_session",
        lambda: _RecordingSession(),
    )

    out = await PoCGenerator.generate(
        deliverables_dir=d, vuln_classes=["injection"],
        target_url="https://t.example.com", track="whitebox",
    )
    messages = info_calls
    assert any("1 个 externally_exploitable" in m for m in messages), messages  # 开始行
    assert any("(1/1)" in m and "INJ-1" in m for m in messages), messages       # 逐条进度
    assert any("PoC 完成" in m for m in messages), messages                    # 完成行
    assert out is not None
    # 改走 session 后，进度行不应再裸 print 到 stdout
    captured = capsys.readouterr().out
    assert "externally_exploitable" not in captured
    assert "(1/1)" not in captured


async def test_generate_empty_when_all_filtered(tmp_path):
    d = _wb_queue(tmp_path)
    # 全部改成不可达
    q = VulnerabilityQueue(vulnerabilities=[
        InjectionVulnerability(ID="INJ-9", vulnerability_type="SQLi", externally_exploitable=False,
                               confidence="low", source="GET /a?id=1", witness_payload="x"),
    ])
    (d / "injection_exploitation_queue.json").write_text(q.model_dump_json(), encoding="utf-8")
    out = await PoCGenerator.generate(d, ["injection"], "https://t.example.com", "whitebox")
    assert "无 externally_exploitable" in out.read_text(encoding="utf-8")


async def test_generate_placeholder_host_when_target_empty(tmp_path):
    d = _wb_queue(tmp_path)
    out = await PoCGenerator.generate(d, ["injection"], None, "whitebox")
    md = out.read_text(encoding="utf-8")
    assert "TARGET[:PORT]" in md and "⚠️ 使用前替换" in md


async def test_generate_blackbox_uses_accepted_ids(tmp_path):
    d = tmp_path / "deliverables" / "blackbox"
    d.mkdir(parents=True)
    q = VulnerabilityQueue(vulnerabilities=[
        XssVulnerability(ID="XSS-1", vulnerability_type="Reflected", externally_exploitable=True,
                         confidence="low", source="GET /share?locale=x",
                         witness_payload="</script><script>1</script>", verdict=None),
    ])
    (d / "xss_exploitation_queue.json").write_text(q.model_dump_json(), encoding="utf-8")
    (d / "xss_exploit_verdicts.json").write_text(
        json.dumps({"vuln_class": "xss", "accepted_ids": ["XSS-1"], "rejected": []}), encoding="utf-8")
    out = await PoCGenerator.generate(d, ["xss"], "https://t.example.com", "blackbox")
    md = out.read_text(encoding="utf-8")
    assert "✓ 已确认" in md  # accepted_ids → CONFIRMED


async def test_generate_llm_failure_degrades_gracefully(tmp_path, monkeypatch):
    """auth 漏洞走 LLM；LLM 失败 → 退骨架+标注，不阻塞。"""
    d = tmp_path / "deliverables" / "whitebox"
    d.mkdir(parents=True)
    from supernova_core.models.queue_schemas import AuthVulnerability
    q = VulnerabilityQueue(vulnerabilities=[
        AuthVulnerability(ID="AUTH-1", vulnerability_type="missing-jwt", externally_exploitable=True,
                          confidence="needs_review", exploitation_hypothesis="no verify",
                          suggested_exploit_technique="forge jwt"),
    ])
    (d / "auth_exploitation_queue.json").write_text(q.model_dump_json(), encoding="utf-8")
    import supernova_core.services.poc_generator as mod
    async def boom(prompt, **kw):
        raise RuntimeError("llm down")
    monkeypatch.setattr(mod, "run_claude_prompt", boom)
    out = await PoCGenerator.generate(d, ["auth"], "https://t.example.com", "whitebox", repo_path="/tmp/x")
    md = out.read_text(encoding="utf-8")
    assert "AUTH-1" in md  # 仍产出条目
    assert "请求形态未推断" in md


async def test_generate_llm_dict_body_does_not_crash(tmp_path, monkeypatch):
    """regression（sentinel_dashboard 2026-07-21）：LLM gap-fill 返回 dict body（GLM
    structured_output 不可靠，schema 声明 string|null 但实际返回 object）曾致
    to_burp_raw spec.body.lstrip() 对 dict 崩 -> 整个 PoC 报告丢失（冒泡到顶层 except）。
    归一化后应正常写入 md，含 JSON body + application/json Content-Type。
    """
    d = tmp_path / "deliverables" / "whitebox"
    d.mkdir(parents=True)
    from supernova_core.models.queue_schemas import AuthVulnerability
    q = VulnerabilityQueue(vulnerabilities=[
        AuthVulnerability(ID="AUTH-1", vulnerability_type="missing-jwt", externally_exploitable=True,
                          confidence="needs_review", exploitation_hypothesis="no verify",
                          suggested_exploit_technique="forge jwt"),
    ])
    (d / "auth_exploitation_queue.json").write_text(q.model_dump_json(), encoding="utf-8")
    import supernova_core.services.poc_generator as mod

    async def fake_run(prompt, **kw):
        return SimpleNamespace(success=True, structured_output={
            "method": "POST", "path": "/auth/callback",
            "body": {"id_token": "forged.none.sig"},  # dict，非 schema 声明的 str
            "query": None, "headers": None, "steps": None,
        }, error=None)

    monkeypatch.setattr(mod, "run_claude_prompt", fake_run)
    out = await PoCGenerator.generate(d, ["auth"], "https://t.example.com", "whitebox", repo_path="/tmp/x")
    md = out.read_text(encoding="utf-8")
    assert "AUTH-1" in md
    assert "Content-Type: application/json" in md
    assert '"id_token": "forged.none.sig"' in md


def test_coerce_str_dict_handles_str_query_headers():
    """regression（sentinel_dashboard 2026-07-22 INJ-GN-08）:LLM 返回 str 类型
    query/headers（GLM 无 strict）曾致 'str' object has no attribute 'items'。"""
    from supernova_core.services.poc_generator import _coerce_str_dict
    # dict 直通
    assert _coerce_str_dict({"a": "1"}) == {"a": "1"}
    # None / 空
    assert _coerce_str_dict(None) == {}
    assert _coerce_str_dict("") == {}
    # query string
    assert _coerce_str_dict("a=1&b=2") == {"a": "1", "b": "2"}
    # JSON 对象字符串
    assert _coerce_str_dict('{"a": "1"}') == {"a": "1"}
    # 乱串不崩
    assert _coerce_str_dict("garbage") == {}


def test_spec_from_llm_guess_str_query_does_not_crash():
    """_spec_from_llm_guess 收到 str query/headers 不再崩。"""
    from supernova_core.services.poc_generator import _spec_from_llm_guess
    class V:
        ID = "X-1"
    spec = _spec_from_llm_guess(
        {"method": "GET", "path": "/x", "query": "a=1&b=2",
         "headers": "X-Test: y", "body": None}, V(), "injection", ConfidenceBand.SUSPECTED)
    assert spec.query == {"a": "1", "b": "2"}
    assert spec.headers == {}  # "X-Test: y" 非 k=v& 形态 → 空 dict（不崩即可）
