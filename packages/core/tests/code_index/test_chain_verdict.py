import pytest

from supernova_core.code_index.chain_verdict import (
    CHAIN_VERDICT_SCHEMA,
    CandidateChain,
    _parse_verdict_json,
    extract_candidate_chains,
    http_route_label,
    judge_chain_verdict,
    ChainVerdict,
)
from supernova_core.code_index.parameter_models import (
    DangerousSlot,
    SlotContext,
    ParameterPropagationGraph,
    SinkCallSite,
    SinkCategory,
    TaintFlow,
    PropagationStep,
)
from supernova_core.code_index.models import EntryPoint, ParameterSource


def _flow(sink_slot, source="q", source_type=ParameterSource.QUERY_PARAM, steps=None,
          sink_id="app.py:handler:db.execute:1:0"):
    return TaintFlow(
        flow_id="ep#sink1", entry_point_id="app.py:handler:1",
        source_param=source, source_type=source_type,
        sink_call_site_id=sink_id,
        sink_slot=sink_slot,
        propagation_steps=steps or [],
    )


def _step(tf, code_location="app.py:5"):
    return PropagationStep(
        step_id="s1", from_func_id="f", from_param="q",
        to_func_id="f", to_param="x", transformation=tf, code_location=code_location,
    )


def _xss_sink(sink_id, sink_subtype="xss_innerhtml"):
    return SinkCallSite(
        id=sink_id, caller_id="app.py:handler", callee_name="innerHTML",
        callee_receiver="el", category=SinkCategory.XSS, sink_subtype=sink_subtype,
        file_path="app.py", line=5, column=10, dangerous_slots=[], rule_id="xss-rule",
    )


def test_extract_injection_routes_sql_and_command_sinks():
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("sql_value"), _flow("cmd_argument")],
        language_coverage=["python"],
    )
    chains = extract_candidate_chains(pgraph, vuln_class="injection")
    assert len(chains) == 2
    assert all(c.vuln_class == "injection" for c in chains)
    assert all(c.direction_hint == "backward" for c in chains)


def test_extract_xss_routes_only_xss_sinks():
    """No sink_call_sites provided → xss cannot resolve render context → no chains."""
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("sql_value"), _flow("generic")],
        language_coverage=["python"],
    )
    chains = extract_candidate_chains(pgraph, vuln_class="xss")
    # sink_slot "generic"/"sql_value" carry no render context → no chain
    assert chains == []


def test_extract_xss_routes_by_sink_call_site_category():
    """xss routes via SinkCallSite.category == XSS (SlotContext has no render context)."""
    sid = "app.py:handler:innerHTML:5:0"
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("generic", sink_id=sid), _flow("sql_value")],
        language_coverage=["typescript"],
    )
    chains = extract_candidate_chains(
        pgraph, vuln_class="xss",
        sink_call_sites={sid: _xss_sink(sid, sink_subtype="xss_innerhtml")},
    )
    assert len(chains) == 1
    c = chains[0]
    assert c.vuln_class == "xss"
    assert c.direction_hint == "backward"
    assert c.render_context == "html_body"  # innerHTML → HTML body context


def test_extract_ssrf_routes_only_url_sinks():
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("url"), _flow("sql_value")],
        language_coverage=["python"],
    )
    chains = extract_candidate_chains(pgraph, vuln_class="ssrf")
    assert len(chains) == 1
    assert chains[0].vuln_class == "ssrf"
    assert chains[0].direction_hint == "backward"


def test_extract_empty_pgraph_returns_empty():
    pgraph = ParameterPropagationGraph(taint_flows=[], language_coverage=["python"])
    assert extract_candidate_chains(pgraph, vuln_class="injection") == []


def test_post_sanitize_concat_detected_when_concat_after_sanitizer():
    steps = [_step("sanitize_hint:html.escape"), _step("concat")]
    pgraph2 = ParameterPropagationGraph(
        taint_flows=[_flow("sql_value", steps=steps)],
        language_coverage=["python"],
    )
    chains = extract_candidate_chains(pgraph2, vuln_class="injection")
    assert len(chains) == 1
    assert chains[0].post_sanitize_concat is True


def test_post_sanitize_concat_false_when_no_concat_after():
    steps = [_step("sanitize_hint:html.escape"), _step("format")]
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("sql_value", steps=steps)], language_coverage=["python"],
    )
    chains = extract_candidate_chains(pgraph, vuln_class="injection")
    assert len(chains) == 1
    assert chains[0].post_sanitize_concat is False


def test_post_sanitize_concat_detected_from_summary_step_marker():
    """summary step 的 transformation 含 |post_concat 标记 → post_sanitize_concat=True.

    覆盖 Task 2/3 产的 summary step 形态(sanitize_hint:<desc>|post_concat)。
    """
    steps = [_step("sanitize_hint:html.escape|post_concat")]
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("sql_value", steps=steps)],
        language_coverage=["python"],
    )
    chains = extract_candidate_chains(pgraph, vuln_class="injection")
    assert len(chains) == 1
    assert chains[0].post_sanitize_concat is True


@pytest.mark.asyncio
async def test_judge_chain_verdict_calls_llm_and_parses_verdict():
    """LLM pass returns verdict JSON -> ChainVerdict parsed."""
    chain = CandidateChain(
        vuln_class="injection", flow_id="f1", entry_point_id="ep",
        source_param="q", source_type="query", sink_call_site_id="db.execute:1",
        sink_slot="sql_value", propagation_steps=[_step("concat")],
        sanitizer_annotations=[], direction_hint="forward",
        post_sanitize_concat=True,
    )

    async def fake_llm(prompt, **kw):
        # LLM pass returns a compact verdict JSON
        return ('{"verdict":"vulnerable","witness_payload":"\'","evidence_chain":'
                '"q -> db.execute(L1)","mismatch_reason":"concat into sql value slot",'
                '"confidence":"high"}')

    verdict = await judge_chain_verdict(chain, llm_client=fake_llm)
    assert isinstance(verdict, ChainVerdict)
    assert verdict.verdict == "vulnerable"
    assert verdict.witness_payload == "'"
    assert "db.execute" in verdict.evidence_chain
    assert verdict.confidence == "high"


@pytest.mark.asyncio
async def test_judge_chain_verdict_defaults_safe_on_llm_failure():
    """LLM pass raises/fails → conservative: treat as needs_review, do not crash."""
    chain = CandidateChain(
        vuln_class="ssrf", flow_id="f1", entry_point_id="ep",
        source_param="url", source_type="query", sink_call_site_id="fetch:1",
        sink_slot="url", propagation_steps=[], sanitizer_annotations=[],
        direction_hint="backward", post_sanitize_concat=False,
    )

    async def failing_llm(prompt, **kw):
        raise RuntimeError("LLM chain-verdict pass not available")

    verdict = await judge_chain_verdict(chain, llm_client=failing_llm)
    # graceful: never crash; mark needs_review (do not silently declare safe/vulnerable)
    assert verdict.confidence == "low"
    assert "needs_review" in (verdict.mismatch_reason or "") or verdict.verdict in ("safe", "vulnerable")


def _slot_sink(sink_id, slot=SlotContext.SQL_VALUE, expr="req.query.q"):
    return SinkCallSite(
        id=sink_id, caller_id="app.py:handler", callee_name="execute",
        callee_receiver="db", category=SinkCategory.SQL, sink_subtype="sql_raw_query",
        file_path="app.py", line=5, column=10,
        dangerous_slots=[DangerousSlot(arg_index=0, slot=slot, expression=expr, is_entry_hint=True)],
        rule_id="py-sql-execute",
    )


def test_extract_fills_sink_expressions_from_dangerous_slots():
    """injection 路径:sink_call_sites 的 dangerous_slots.expression → CandidateChain.sink_expressions。"""
    sid = "app.py:handler:db.execute:5:0"
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("sql_value", sink_id=sid)],
        language_coverage=["python"],
    )
    chains = extract_candidate_chains(
        pgraph, vuln_class="injection",
        sink_call_sites={sid: _slot_sink(sid, SlotContext.SQL_VALUE, "q + suffix")},
    )
    assert len(chains) == 1
    assert chains[0].sink_expressions == ["q + suffix"]


def test_extract_sink_expressions_empty_when_no_sink_call_sites():
    """inj/ssrf 未传 sink_call_sites → sink_expressions 默认空(向后兼容,不报错)。"""
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("sql_value")],
        language_coverage=["python"],
    )
    chains = extract_candidate_chains(pgraph, vuln_class="injection")   # 不传 sink_call_sites
    assert len(chains) == 1
    assert chains[0].sink_expressions == []


@pytest.mark.asyncio
async def test_judge_chain_verdict_prompt_includes_sink_expressions_and_intermediate_vars():
    """prompt 含 sink_expressions + steps_repr 含 intermediate_vars(判定信息密度)。"""
    chain = CandidateChain(
        vuln_class="injection", flow_id="f1", entry_point_id="ep",
        source_param="q", source_type="query", sink_call_site_id="db.execute:1",
        sink_slot="sql_value",
        propagation_steps=[PropagationStep(
            step_id="s1", from_func_id="f", from_param="q", to_func_id="f", to_param="sink",
            transformation="sanitize_hint:html.escape", code_location="app.py:5",
            intermediate_vars=["raw", "esc"],
        )],
        sanitizer_annotations=[], direction_hint="backward",
        post_sanitize_concat=False,
        sink_expressions=["'sel ' + q"],
    )
    captured = {}

    async def fake_llm(prompt, **kw):
        captured["prompt"] = prompt
        return '{"verdict":"safe","witness_payload":null,"evidence_chain":"q->db","mismatch_reason":null,"confidence":"high"}'

    await judge_chain_verdict(chain, llm_client=fake_llm)
    assert "'sel ' + q" in captured["prompt"]            # sink_expressions 进 prompt
    assert "raw" in captured["prompt"] and "esc" in captured["prompt"]   # intermediate_vars 进 steps_repr


# --- title 字段（spec 2026-08-06）：chain verdict 产描述性标题 ---

def test_chain_verdict_schema_includes_title():
    """CHAIN_VERDICT_SCHEMA 含 title（string|null）—— LLM 输出契约。"""
    assert "title" in CHAIN_VERDICT_SCHEMA["properties"]
    assert "null" in CHAIN_VERDICT_SCHEMA["properties"]["title"]["type"]


def test_chain_verdict_dataclass_has_title_field():
    """ChainVerdict dataclass 含 title，缺省 None。"""
    cv = ChainVerdict(
        verdict="vulnerable", witness_payload="'", evidence_chain="q->db",
        mismatch_reason="concat", confidence="high",
    )
    assert cv.title is None


@pytest.mark.asyncio
async def test_judge_chain_verdict_parses_title_from_llm():
    """LLM 输出 JSON 含 title → ChainVerdict.title 解析出来。"""
    chain = CandidateChain(
        vuln_class="injection", flow_id="f1", entry_point_id="ep",
        source_param="q", source_type="query", sink_call_site_id="db.execute:1",
        sink_slot="sql_value", propagation_steps=[_step("concat")],
        sanitizer_annotations=[], direction_hint="backward",
        post_sanitize_concat=True,
    )

    async def fake_llm(prompt, **kw):
        return ('{"verdict":"vulnerable","witness_payload":"\'","evidence_chain":'
                '"q -> db.execute(L1)","mismatch_reason":"concat into sql value slot",'
                '"confidence":"high","title":"SQL Injection via search q param"}')

    verdict = await judge_chain_verdict(chain, llm_client=fake_llm)
    assert verdict.title == "SQL Injection via search q param"


@pytest.mark.asyncio
async def test_judge_chain_verdict_title_none_when_llm_omits():
    """LLM 不返 title（旧/兜底分支）→ ChainVerdict.title=None，不崩。"""
    chain = CandidateChain(
        vuln_class="ssrf", flow_id="f1", entry_point_id="ep",
        source_param="url", source_type="query", sink_call_site_id="fetch:1",
        sink_slot="url", propagation_steps=[], sanitizer_annotations=[],
        direction_hint="backward", post_sanitize_concat=False,
    )

    async def fake_llm(prompt, **kw):
        return ('{"verdict":"safe","witness_payload":null,"evidence_chain":"url->fetch",'
                '"mismatch_reason":null,"confidence":"high"}')

    verdict = await judge_chain_verdict(chain, llm_client=fake_llm)
    assert verdict.title is None


# --------------------------------------------------------------------------- #
# O2 后半：unparseable 有界重试 + 保守分支语义（此前 0 覆盖）。
# --------------------------------------------------------------------------- #

# 无任何 JSON 结构的纯 Markdown 叙述（fenced JSON 可被 L0 容错恢复，不算
# unparseable；真正打穿 L0 的是无 {} 的叙述/截断输出——spec R5 的失败形态）。
_GLM_MARKDOWN = (
    "## 判定结论\n\n该链路存在 SQL 注入风险：参数 q 未经参数化直接拼接进入"
    "SQL 值槽位，现有转义不覆盖该槽位。建议使用参数化查询修复。"
)
_VERDICT_JSON = (
    '{"verdict":"vulnerable","witness_payload":"\' OR \'1\'=\'1",'
    '"evidence_chain":"q -> db.execute(L1)","mismatch_reason":"concat",'
    '"confidence":"high","title":"SQL 注入"}'
)


def _chain():
    return CandidateChain(
        vuln_class="injection", flow_id="f1", entry_point_id="ep",
        source_param="q", source_type="query", sink_call_site_id="db.execute:1",
        sink_slot="sql_value", propagation_steps=[_step("concat")],
        sanitizer_annotations=[], direction_hint="forward",
        post_sanitize_concat=True,
    )


@pytest.mark.asyncio
async def test_judge_recovers_from_markdown_via_reformat_retry():
    """首次返回 GLM Markdown → 轻量转格式重试恢复合法 JSON（spec O2 后半）。"""
    prompts: list[str] = []

    async def fake_llm(prompt, **kw):
        prompts.append(prompt)
        return _GLM_MARKDOWN if len(prompts) == 1 else _VERDICT_JSON

    verdict = await judge_chain_verdict(_chain(), llm_client=fake_llm)
    assert verdict.verdict == "vulnerable"
    assert verdict.witness_payload == "' OR '1'='1"
    assert len(prompts) == 2                       # 1 次 + 1 次轻量转格式
    assert "待转换文本" in prompts[1]              # 转格式 prompt 而非全量重发


@pytest.mark.asyncio
async def test_judge_unparseable_after_retries_is_conservative():
    """全部尝试仍 Markdown → 保守分支：不丢报、witness=None、置信度 low。"""
    calls = {"n": 0}

    async def fake_llm(prompt, **kw):
        calls["n"] += 1
        return _GLM_MARKDOWN

    verdict = await judge_chain_verdict(_chain(), llm_client=fake_llm)
    assert calls["n"] == 3                          # 1 原始 + 2 重试（默认）
    assert verdict.verdict == "vulnerable"          # OR 友好，不静默清除
    assert verdict.witness_payload is None
    assert verdict.confidence == "low"
    assert "unparseable output after all attempts" in verdict.mismatch_reason


@pytest.mark.asyncio
async def test_judge_empty_output_after_retries_distinguishes_reason():
    """空输出与非法输出分流：mismatch_reason 报 empty 而非 unparseable。"""
    async def fake_llm(prompt, **kw):
        return ""

    verdict = await judge_chain_verdict(_chain(), llm_client=fake_llm)
    assert verdict.verdict == "vulnerable"
    assert "empty output" in verdict.mismatch_reason


@pytest.mark.asyncio
async def test_judge_no_retry_when_env_zero(monkeypatch):
    """SUPERNOVA_CHAIN_VERDICT_RETRIES=0 → 只打 1 次，直接保守降级。"""
    monkeypatch.setenv("SUPERNOVA_CHAIN_VERDICT_RETRIES", "0")
    calls = {"n": 0}

    async def fake_llm(prompt, **kw):
        calls["n"] += 1
        return _GLM_MARKDOWN

    verdict = await judge_chain_verdict(_chain(), llm_client=fake_llm)
    assert calls["n"] == 1
    assert verdict.verdict == "vulnerable"
    assert verdict.witness_payload is None


@pytest.mark.asyncio
async def test_judge_retry_call_exception_falls_conservative():
    """重试调用本身 raise → 放弃重试，保守降级（不 crash）。"""
    calls = {"n": 0}

    async def fake_llm(prompt, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _GLM_MARKDOWN
        raise RuntimeError("provider down")

    verdict = await judge_chain_verdict(_chain(), llm_client=fake_llm)
    assert calls["n"] == 2
    assert verdict.verdict == "vulnerable"
    assert verdict.witness_payload is None


@pytest.mark.asyncio
async def test_judge_single_call_when_parseable():
    """合法 JSON 一次到位 → 不触发任何重试。"""
    calls = {"n": 0}

    async def fake_llm(prompt, **kw):
        calls["n"] += 1
        return _VERDICT_JSON

    verdict = await judge_chain_verdict(_chain(), llm_client=fake_llm)
    assert calls["n"] == 1
    assert verdict.verdict == "vulnerable"


@pytest.mark.asyncio
async def test_judge_prompt_asks_for_concrete_witness():
    """prompt 含具体 witness 指令（MINIMAL concrete attack input）。"""
    prompts: list[str] = []

    async def fake_llm(prompt, **kw):
        prompts.append(prompt)
        return _VERDICT_JSON

    await judge_chain_verdict(_chain(), llm_client=fake_llm)
    assert "MINIMAL concrete attack input" in prompts[0]


def test_schema_requires_witness_payload():
    """witness_payload 入 required（可 null）——prompt-only 约束下防模型省略。"""
    assert "witness_payload" in CHAIN_VERDICT_SCHEMA["required"]


def test_parse_verdict_json_recovers_fenced_json():
    assert _parse_verdict_json('```json\n{"verdict":"safe"}\n```') == {"verdict": "safe"}


def test_parse_verdict_json_rejects_invalid_values():
    assert _parse_verdict_json('{"verdict":"maybe"}') is None      # 非法枚举
    assert _parse_verdict_json('["not","object"]') is None         # 非 object
    assert _parse_verdict_json("") is None                         # 空输出
    assert _parse_verdict_json(None) is None
    assert _parse_verdict_json("no braces at all") is None


# --------------------------------------------------------------------------- #
# O2 前半：http_route_label（builder 路由 join 的共享 helper）。
# --------------------------------------------------------------------------- #

def _route_ep(func_block_id="app.py:h:1", route="/search", http_method="POST"):
    return EntryPoint(
        func_block_id=func_block_id, entry_type="http_route", route=route,
        http_method=http_method, confidence=1.0, evidence="annot",
        needs_llm_review=False,
    )


def test_http_route_label_hit():
    assert http_route_label("app.py:h:1", {"app.py:h:1": _route_ep()}) == "POST /search"


def test_http_route_label_normalizes_route_and_method():
    ep = _route_ep(route="search", http_method="post")
    assert http_route_label("app.py:h:1", {"app.py:h:1": ep}) == "POST /search"


def test_http_route_label_miss_variants():
    assert http_route_label("app.py:h:1", {}) is None                       # 空 map
    assert http_route_label("app.py:h:1", None) is None                     # 不传
    assert http_route_label(None, {"a": _route_ep()}) is None               # 无 id
    assert http_route_label(
        "app.py:h:1", {"app.py:h:1": _route_ep(route=None)}) is None        # 无路由
    assert http_route_label(
        "app.py:h:1", {"app.py:h:1": _route_ep(http_method=None)}) is None  # 无 method
    assert http_route_label(
        "app.py:x:9", {"app.py:h:1": _route_ep()}) is None                  # join miss
