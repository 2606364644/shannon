from supernova_core.models.queue_schemas import InjectionVulnerability
from supernova_core.services.severity_rules import (
    SEVERITY_ZH, derive_fallback_severity, effective_severity, max_severity,
)

def _vuln(**kw):
    base = dict(ID="INJ-GN-01", vulnerability_type="injection",
                externally_exploitable=True, confidence="low",
                sink_call="app/routes/contributions.js:ContributionsHandler:eval:32:23")
    base.update(kw)
    return InjectionVulnerability(**base)

def test_fallback_eval_sink_is_critical():
    assert derive_fallback_severity(_vuln()) == "critical"

def test_fallback_injection_generic_is_high():
    v = _vuln(sink_call=None, sink_function="findOne", vulnerability_type="injection")
    assert derive_fallback_severity(v) == "high"

def test_fallback_externally_exploitable_other_class_is_high():
    v = _vuln(vulnerability_type="ssrf", sink_call=None, sink_function="needle.get")
    assert derive_fallback_severity(v) == "high"

def test_fallback_baseline_medium():
    v = _vuln(vulnerability_type="auth", sink_call=None, sink_function=None,
              externally_exploitable=False)
    # auth 无 sink 字段 → medium
    assert derive_fallback_severity(v) == "medium"

def test_effective_severity_prefers_explicit():
    assert effective_severity(_vuln(severity="medium")) == "medium"
    assert effective_severity(_vuln(severity=None)) == "critical"  # eval 兜底
    assert effective_severity(_vuln(severity="bogus")) == "critical"  # 非法值走兜底

def test_max_severity_and_zh_mapping():
    assert max_severity("medium", "critical") == "critical"
    assert SEVERITY_ZH == {"critical": "严重", "high": "高危", "medium": "中危", "low": "低危"}


# --- F9c（spec 2026-08-25 终审）：RCE 关键词词边界匹配 ---
# 旧子串匹配 "exec" 会误吞 "cursor.execute"（SQLi sink 应 high）、
# "eval" 误吞 "reevaluate"——SQLi 被误升 critical。词边界后须各归其位。

def test_fallback_cursor_execute_sqli_is_high_not_critical():
    v = _vuln(sink_call=None, sink_function="cursor.execute(sql)",
              vulnerability_type="injection")
    assert derive_fallback_severity(v) == "high"

def test_fallback_word_bounded_rce_sinks_still_critical():
    for sink in ("exec(user_input)", "eval(req.body.x)", "os.system(cmd)"):
        v = _vuln(sink_call=None, sink_function=sink)
        assert derive_fallback_severity(v) == "critical", sink

def test_fallback_substring_lookalikes_not_critical():
    # "reevaluate" 含 "eval"、"ecosystem" 含 "ecos..."——词边界外不算 RCE
    for sink in ("reevaluate(x)", "ecosystem call"):
        v = _vuln(sink_call=None, sink_function=sink,
                  vulnerability_type="injection")
        assert derive_fallback_severity(v) != "critical", sink
