# packages/core/tests/code_index/test_gn_collapse.py
from supernova_core.code_index.gn_collapse import (
    collapse_gn_entries, extract_endpoint, extract_param, parse_sink_call_site_id,
)
from supernova_core.models.queue_schemas import InjectionVulnerability, XssVulnerability

def _gn(id_, param, sink, path="POST /contributions → chain", severity=None):
    return InjectionVulnerability(
        ID=id_, vulnerability_type="injection", externally_exploitable=True,
        confidence="low", source=f"{param} (app/routes/contributions.js:ContributionsHandler:7)",
        path=path, sink_call=sink, verdict="vulnerable", source_track="gitnexus",
        severity=severity)

SINK32 = "app/routes/contributions.js:ContributionsHandler:eval:32:23"
SINK33 = "app/routes/contributions.js:ContributionsHandler:eval:33:25"

def test_parse_sink_call_site_id():
    assert parse_sink_call_site_id(SINK32) == ("eval", "app/routes/contributions.js:32")
    assert parse_sink_call_site_id("short") == (None, None)

def test_parse_sink_call_site_id_rejects_non_gn_rich_text():
    """LLM 轨 sink_call 富文本形（≥4 段冒号但行号段非纯数字）不得被当 GN id
    解析——sink 维会变成行号碎片（'32'），跨轨 key 永不相交（20260826 真实
    形态实证）。拒收 → (None, None) → merger 走自然语言回退归一出真函数名。"""
    # 多行号枚举形（NodeGoat 20260826 INJ-VULN-01 原文）
    rich = ("eval() — app/routes/contributions.js:32 (preTax)、"
            ":33 (afterTax)、:34 (roth)")
    assert parse_sink_call_site_id(rich) == (None, None)
    # URL 多冒号形（4 段但倒数第二段非纯数字）
    assert parse_sink_call_site_id(
        "get(url) via http://evil.com — a.js:16") == (None, None)
    # 合法 GN id（Spec A 五段 file:caller:callee:line:col，line 为整数）不受影响
    assert parse_sink_call_site_id(SINK32) == ("eval", "app/routes/contributions.js:32")

def test_sink_file_rejects_non_gn_rich_text():
    """_sink_file 与 parse 同口径：非 GN id 形态不给文件段（防富文本前缀
    'eval() — app/routes/contributions.js' 被当文件名进 _unit_key 回退分支）。"""
    from supernova_core.code_index.gn_collapse import _sink_file
    rich = ("eval() — app/routes/contributions.js:32 (preTax)、"
            ":33 (afterTax)、:34 (roth)")
    assert _sink_file(rich) is None
    assert _sink_file(SINK32) == "app/routes/contributions.js"

def test_extract_endpoint_and_param():
    assert extract_endpoint("POST /contributions → preTax -> x") == "POST /contributions"
    assert extract_endpoint("a → GET /login → b") == "GET /login"
    assert extract_endpoint("no route here") is None
    assert extract_param("preTax (app/routes/contributions.js:7)") == "preTax"

def test_collapse_same_unit_nine_to_three():
    """preTax/afterTax/roth × eval:32/33/34（同接口同 sink 函数）→ 1 主记录 9 入口行。"""
    gn = [_gn(f"INJ-GN-{i:02d}", p, s)
          for i, (p, s) in enumerate(
              [(p, f"app/routes/contributions.js:ContributionsHandler:eval:{ln}:{ln}")
               for p in ("preTax", "afterTax", "roth") for ln in (32, 33, 34)], start=1)]
    out = collapse_gn_entries(gn)
    assert len(out) == 1
    assert out[0].ID == "INJ-GN-01"
    assert out[0].endpoint == "POST /contributions"
    assert set(out[0].affected_parameters) == {"preTax", "afterTax", "roth"}
    assert len(out[0].affected_entries) == 9
    assert out[0].affected_entries[0] == {
        "parameter": "preTax", "sink_location": "app/routes/contributions.js:32",
        "chain_id": "INJ-GN-01", "track": "gitnexus"}

def test_collapse_keeps_different_endpoints_separate():
    a = _gn("XSS-GN-01", "memo", "app/routes/memos.js:MemosHandler:render:27:19",
            path="GET /memos → chain")
    b = _gn("XSS-GN-02", "url", "app/routes/research.js:ResearchHandler:render:31:15",
            path="GET /research → chain")
    out = collapse_gn_entries([a, b])
    assert len(out) == 2  # 不同接口绝不合并（spec §3.1）

def test_collapse_severity_takes_max():
    gn = [_gn("INJ-GN-01", "preTax", SINK32, severity="medium"),
          _gn("INJ-GN-02", "preTax", SINK33, severity=None)]  # 兜底 critical(eval)
    out = collapse_gn_entries(gn)
    assert out[0].severity == "critical"


def _gn_xss(id_, param, sink_call, path):
    """真实形态 XssVulnerability GN 条目（F1 后 builder 回填 sink_call）。"""
    return XssVulnerability(
        ID=id_, vulnerability_type="Reflected", externally_exploitable=True,
        confidence="low", source=f"{param} (app/routes/contributions.js:ContributionsHandler:7)",
        path=path, sink_function="render", sink_call=sink_call,
        verdict="vulnerable", source_track="gitnexus", render_context="HTML_BODY")


def test_collapse_xss_findings_by_sink_call():
    """F1：XssVulnerability 凭 sink_call 折叠——NodeGoat 真实形态（preTax/afterTax/
    roth × render:21/50/58/70 同接口同 sink 函数，12 条笛卡尔积）折成 1 主记录
    12 入口行。修复前 XssVulnerability 无 sink_call 字段 → _unit_key 落
    ("__strict__", id(f)) 一条不折（spec 2026-08-26 §7 的根因修复）。"""
    gn = [_gn_xss(
        f"XSS-GN-{i:02d}", p,
        f"app/routes/contributions.js:ContributionsHandler:render:{ln}:{ln}",
        "preTax -> app/routes/contributions.js:ContributionsHandler:render (llm-pass-failed)")
        for i, (p, ln) in enumerate(
            [(p, ln) for p in ("preTax", "afterTax", "roth") for ln in (21, 50, 58, 70)],
            start=1)]
    out = collapse_gn_entries(gn)
    assert len(out) == 1
    assert out[0].ID == "XSS-GN-01"
    assert len(out[0].affected_entries) == 12
    # sink_location 出 file:line（渲染层 _FILE_LINE_RE 可提，问题点节/入口表修复）
    assert out[0].affected_entries[0]["sink_location"] == "app/routes/contributions.js:21"
    assert set(out[0].affected_parameters) == {"preTax", "afterTax", "roth"}


def test_collapse_xss_without_sink_call_keeps_strict_no_overmerge():
    """无 sink_call 且 path 无路由的 XssVulnerability（旧 queue 数据）仍走
    strict key 各自成条——不因同文件/同 sink_function 文本被过度合并。"""
    gn = [
        _gn_xss("XSS-GN-01", "preTax", None, "preTax -> render chain"),
        _gn_xss("XSS-GN-02", "afterTax", None, "afterTax -> render chain"),
    ]
    out = collapse_gn_entries(gn)
    assert len(out) == 2

def test_extract_endpoint_strips_trailing_punct():
    # 尾标点：',' 与全角 ')' 都会污染 key / 报告展示
    assert extract_endpoint("POST /login, -> handler") == "POST /login"
    assert extract_endpoint("POST /memos) -> x") == "POST /memos"
    assert extract_endpoint("GET /allocations/:userId?threshold=1 -> x") == "GET /allocations/:userId"

def test_normalize_placeholders():
    from supernova_core.code_index.gn_collapse import _normalize_placeholders
    assert _normalize_placeholders("/allocations/:userId") == "/allocations/{userId}"
    assert _normalize_placeholders("/benefits") == "/benefits"
    # 保留参数名（:id ≠ :userId，不得归一成同一形）
    assert _normalize_placeholders("/a/:id") == "/a/{id}"
    # 不误伤协议串
    assert ":https" not in _normalize_placeholders("/x?u=https://a.b")


def test_collapse_preserves_placement_annotation():
    """builder 透传的 affected_parameters 注记（'preTax (body)'——placement
    显式信号）折叠时保留；affected_entries[].parameter 保持裸名（行内定位
    用，与注记无关）。"""
    def _gn_aps(id_, param, sink, aps):
        return InjectionVulnerability(
            ID=id_, vulnerability_type="injection", externally_exploitable=True,
            confidence="low",
            source=f"{param} (app/routes/contributions.js:ContributionsHandler:7)",
            path="POST /contributions → chain", sink_call=sink,
            verdict="vulnerable", source_track="gitnexus",
            affected_parameters=aps)

    out = collapse_gn_entries([
        _gn_aps("INJ-GN-01", "preTax", SINK32, ["preTax (body)"]),
        _gn_aps("INJ-GN-02", "preTax", SINK33, ["preTax (body)"]),
        _gn_aps("INJ-GN-03", "afterTax", SINK32, None),  # 无注记 → 裸名兜底
    ])
    assert out[0].affected_parameters == ["preTax (body)", "afterTax"]
    assert out[0].affected_entries[0]["parameter"] == "preTax"  # 裸名
