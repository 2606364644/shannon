import json
from contextlib import asynccontextmanager

import pytest

from supernova_whitebox.audit.session_registry import clear_audit_session, set_audit_session
from supernova_whitebox.pipeline import activities


@pytest.fixture(autouse=True)
def _gitnexus_llm_off(monkeypatch):
    """测试统一走各用例注入的 fake_llm：关掉 GitNexus LLM 开关，否则
    _make_verdict_llm_client 构建真 client 静默打真实 LLM（默认开）。"""
    monkeypatch.setattr(activities, "is_gitnexus_llm_enabled", lambda: False)


class _RecordingSession:
    def __init__(self):
        self.info_calls: list[tuple[str, str]] = []

    @asynccontextmanager
    async def track_step(self, phase: str, name: str, intent: str | None = None):
        yield

    async def log_info(self, message: str, level: str = "info"):
        self.info_calls.append((message, level))


def _input(repo):
    class FakeInput:
        agent_name = None
        web_url = None
        repo_path = str(repo)
        config_path = None
        api_key = None
        pipeline_testing_mode = False
        prompt_override = None
        deliverables_subdir = None
        workspace_name = None
        workspace_path = None
        provider_config = None

    return FakeInput()


def _write_pgraph(deliverables, flows):
    """Write a minimal parameter_graph.json with given TaintFlow-like dicts."""
    pgraph = {
        "taint_flows": flows,
        "language_coverage": ["python"],
        "skipped_languages": [],
    }
    (deliverables / "parameter_graph.json").write_text(json.dumps(pgraph))


def _flow(slot, source="q", source_type="query", sink_id="app.py:h:db.execute:5:0",
          steps=None, notes=None):
    d = {
        "flow_id": "ep#" + sink_id,
        "entry_point_id": "app.py:h:1",
        "source_param": source,
        "source_type": source_type,
        "sink_call_site_id": sink_id,
        "sink_slot": slot,
        "propagation_steps": steps or [],
        "confidence": 1.0,
        "has_sanitizer_hint": False,
    }
    if notes is not None:
        d["notes"] = notes
    return d


def _write_sink(deliverables, sink_id, category, sink_subtype):
    """Append a sink_call_site to code_index.json (created if absent)."""
    from pathlib import Path
    ci_path: Path = deliverables / "code_index.json"
    if ci_path.exists():
        ci = json.loads(ci_path.read_text())
    else:
        ci = {
            "repository": "r", "language": "python",
            "total_blocks": 0, "total_entry_points": 0, "total_chains": 0,
            "blocks": [], "edges": [], "entry_points": [], "chains": [],
        }
    ci.setdefault("sink_call_sites", []).append({
        "id": sink_id,
        "caller_id": "app.py:h",
        "callee_name": sink_subtype.split("_")[-1],
        "callee_receiver": "el",
        "category": category,
        "sink_subtype": sink_subtype,
        "file_path": "app.py",
        "line": 5,
        "column": 0,
        "dangerous_slots": [],
        "rule_id": "rule",
    })
    ci_path.write_text(json.dumps(ci))


def _write_entry_point(deliverables, func_block_id, route, http_method):
    """Append an entry_point to code_index.json (created if absent)."""
    from pathlib import Path
    ci_path: Path = deliverables / "code_index.json"
    if ci_path.exists():
        ci = json.loads(ci_path.read_text())
    else:
        ci = {
            "repository": "r", "language": "python",
            "total_blocks": 0, "total_entry_points": 0, "total_chains": 0,
            "blocks": [], "edges": [], "entry_points": [], "chains": [],
        }
    ci.setdefault("entry_points", []).append({
        "func_block_id": func_block_id,
        "entry_type": "http_route",
        "route": route,
        "http_method": http_method,
        "confidence": 1.0,
        "evidence": "annot",
        "needs_llm_review": False,
    })
    ci_path.write_text(json.dumps(ci))


@pytest.mark.asyncio
async def test_writes_injection_gitnexus_queue(tmp_path, monkeypatch):
    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    _write_pgraph(deliverables, [_flow("sql_value")])

    async def fake_llm(prompt, **kw):
        return ('{"verdict":"vulnerable","witness_payload":"\'","evidence_chain":'
                '"q->db","mismatch_reason":"concat","confidence":"high"}')

    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    monkeypatch.setattr(activities, "_gitnexus_verdict_llm_client", fake_llm, raising=False)
    session = _RecordingSession()
    set_audit_session(session)
    try:
        result = await activities.run_gitnexus_chain_verdict(_input(tmp_path))
    finally:
        clear_audit_session()

    q = deliverables / "intermediate" / "injection_gitnexus_queue.json"
    assert q.exists()
    data = json.loads(q.read_text())
    assert len(data["vulnerabilities"]) == 1
    assert data["vulnerabilities"][0]["source_track"] == "gitnexus"
    assert "injection" in result["per_class"]
    # 可观测性（spec §3.4）：有 findings → 汇总 info（per-class 明细）。
    levels = [lvl for (_msg, lvl) in session.info_calls]
    msgs = [msg for (msg, _lvl) in session.info_calls]
    assert "info" in levels
    assert any("inj=1" in m for m in msgs)


@pytest.mark.asyncio
async def test_no_parameter_graph_skips_gracefully(tmp_path, monkeypatch):
    """Plan 1 not landed -> no parameter_graph.json -> all gitnexus queues absent."""
    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)

    async def fake_llm(prompt, **kw):
        raise AssertionError("should not call LLM")

    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    monkeypatch.setattr(activities, "_gitnexus_verdict_llm_client", fake_llm, raising=False)
    session = _RecordingSession()
    set_audit_session(session)
    try:
        result = await activities.run_gitnexus_chain_verdict(_input(tmp_path))
    finally:
        clear_audit_session()

    assert result["per_class"] == {}
    assert not (deliverables / "intermediate" / "injection_gitnexus_queue.json").exists()
    # 可观测性（spec §3.2）：pgraph 缺失 early return 发 warning。
    levels = [lvl for (_msg, lvl) in session.info_calls]
    assert "warning" in levels
    assert any("缺失" in msg for (msg, _lvl) in session.info_calls)


@pytest.mark.asyncio
async def test_writes_xss_and_ssrf_queues(tmp_path, monkeypatch):
    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    xss_sid = "app.py:h:innerHTML:5:0"
    # xss routes via SinkCallSite.category == XSS (SlotContext has no render slot),
    # so a sink_call_site must exist in code_index.json with a matching id.
    _write_pgraph(deliverables, [
        _flow("generic", source="u", sink_id=xss_sid),
        _flow("url", source="u", sink_id="app.py:h:fetch:6:0"),
    ])
    _write_sink(deliverables, xss_sid, "xss", "xss_innerhtml")

    async def fake_llm(prompt, **kw):
        return ('{"verdict":"vulnerable","witness_payload":"x","evidence_chain":"s->k",'
                '"mismatch_reason":"m","confidence":"high"}')

    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    monkeypatch.setattr(activities, "_gitnexus_verdict_llm_client", fake_llm, raising=False)
    set_audit_session(_RecordingSession())
    try:
        result = await activities.run_gitnexus_chain_verdict(_input(tmp_path))
    finally:
        clear_audit_session()

    assert "xss" in result["per_class"]
    assert "ssrf" in result["per_class"]
    assert (deliverables / "intermediate" / "xss_gitnexus_queue.json").exists()
    assert (deliverables / "intermediate" / "ssrf_gitnexus_queue.json").exists()


@pytest.mark.asyncio
async def test_invalid_parameter_graph_skips_gracefully(tmp_path, monkeypatch):
    """Corrupt parameter_graph.json -> skip, don't crash."""
    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    (deliverables / "parameter_graph.json").write_text("not json")

    async def fake_llm(prompt, **kw):
        raise AssertionError("should not call LLM")

    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    monkeypatch.setattr(activities, "_gitnexus_verdict_llm_client", fake_llm, raising=False)
    session = _RecordingSession()
    set_audit_session(session)
    try:
        result = await activities.run_gitnexus_chain_verdict(_input(tmp_path))
    finally:
        clear_audit_session()

    assert result["per_class"] == {}
    # 可观测性（spec §3.3）：pgraph 无效 early return 发 warning。
    levels = [lvl for (_msg, lvl) in session.info_calls]
    assert "warning" in levels
    assert any("无效" in msg for (msg, _lvl) in session.info_calls)


@pytest.mark.asyncio
async def test_summary_info_when_all_classes_zero(tmp_path, monkeypatch):
    """空壳 parameter_graph（taint_flows=[]）→ 3 类 0 findings → 汇总 info（spec §3.4）。"""
    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    _write_pgraph(deliverables, [])

    async def fake_llm(prompt, **kw):
        raise AssertionError("empty pgraph should not call LLM")

    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    monkeypatch.setattr(activities, "_gitnexus_verdict_llm_client", fake_llm, raising=False)
    session = _RecordingSession()
    set_audit_session(session)
    try:
        result = await activities.run_gitnexus_chain_verdict(_input(tmp_path))
    finally:
        clear_audit_session()

    assert result["per_class"] == {}
    levels = [lvl for (_msg, lvl) in session.info_calls]
    msgs = [msg for (msg, _lvl) in session.info_calls]
    assert "info" in levels
    assert any("3 类 0 findings" in m and "taint_flows=0" in m for m in msgs)


@pytest.mark.asyncio
async def test_entry_points_route_flows_into_queue(tmp_path, monkeypatch):
    """O2 前半：code_index.json 的 entry_points 经 activity join 进 builder，
    GN 轨漏洞 path 带 "METHOD /path" 前缀（下游 PoC 模板层直接可用）。"""
    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    _write_pgraph(deliverables, [_flow("sql_value")])
    _write_entry_point(deliverables, "app.py:h:1", "/search", "POST")

    async def fake_llm(prompt, **kw):
        return ('{"verdict":"vulnerable","witness_payload":"\'","evidence_chain":'
                '"q->db","mismatch_reason":"concat","confidence":"high"}')

    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    monkeypatch.setattr(activities, "_gitnexus_verdict_llm_client", fake_llm, raising=False)
    set_audit_session(_RecordingSession())
    try:
        await activities.run_gitnexus_chain_verdict(_input(tmp_path))
    finally:
        clear_audit_session()

    data = json.loads((deliverables / "intermediate" / "injection_gitnexus_queue.json").read_text())
    assert data["vulnerabilities"][0]["path"] == "POST /search → q->db"


# ===== P3 (2026-08-21 safe-branch-recall spec): presumed-safe 来源分流 =====

@pytest.mark.asyncio
async def test_presumed_safe_vulnerable_excluded_from_queue_kept_in_verdicts(
        tmp_path, monkeypatch):
    """P3：presumed-safe 来源（intra 否定过的表达式兜底候选，notes='presumed-safe'）
    判 vulnerable → 只进 chain_verdicts.json（数据流视图可见红枝）不进
    exploitation queue（报告零影响）；同场普通来源（无该 notes）判 vulnerable
    照常进 queue。"""
    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    _write_pgraph(deliverables, [
        _flow("sql_value", source="q", sink_id="app.py:h:db.execute:5:0"),   # 普通
        _flow("sql_value", source="b", sink_id="app.py:h:db.execute:6:0",
              notes="presumed-safe"),                                        # P2 来源
    ])

    async def fake_llm(prompt, **kw):
        return ('{"verdict":"vulnerable","witness_payload":"\'","evidence_chain":'
                '"q->db","mismatch_reason":"concat","confidence":"high"}')

    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    monkeypatch.setattr(activities, "_gitnexus_verdict_llm_client", fake_llm, raising=False)
    session = _RecordingSession()
    set_audit_session(session)
    try:
        result = await activities.run_gitnexus_chain_verdict(_input(tmp_path))
    finally:
        clear_audit_session()

    # queue 只剩普通来源（presumed-safe 判 vuln 不进 —— 防确定性兜底假阳进报告）
    queue = json.loads(
        (deliverables / "intermediate" / "injection_gitnexus_queue.json").read_text())
    assert len(queue["vulnerabilities"]) == 1
    assert queue["vulnerabilities"][0]["source"].startswith("q ")
    assert result["per_class"]["injection"] == 1
    # verdicts 两条都在（数据流视图可见 presumed-safe 枝的终审结论）
    verdicts = json.loads(
        (deliverables / "intermediate" / "injection_chain_verdicts.json").read_text())
    assert len(verdicts["verdicts"]) == 2
    assert all(v["verdict"] == "vulnerable" for v in verdicts["verdicts"])


@pytest.mark.asyncio
async def test_presumed_safe_safe_verdict_archived_not_in_queue(tmp_path, monkeypatch):
    """P3 边界 × 2026-08-27 §4 新口径：presumed-safe 来源判 safe → 判非漏洞
    分流优先——不进 queue、进 dismissed_findings.json 留档（原「safe 全量落
    queue」口径已被「非漏洞不进报告」取代）；chain_verdicts 数据流视图仍全量。"""
    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    _write_pgraph(deliverables, [
        _flow("sql_value", source="b", sink_id="app.py:h:db.execute:6:0",
              notes="presumed-safe"),
    ])

    async def fake_llm(prompt, **kw):
        return ('{"verdict":"safe","witness_payload":null,"evidence_chain":'
                '"b->db","mismatch_reason":null,"confidence":"high"}')

    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    monkeypatch.setattr(activities, "_gitnexus_verdict_llm_client", fake_llm, raising=False)
    set_audit_session(_RecordingSession())
    try:
        result = await activities.run_gitnexus_chain_verdict(_input(tmp_path))
    finally:
        clear_audit_session()

    # queue 不落（0 张 safe 卡不写文件，与「零 finding 不产空文件」一致）
    assert not (deliverables / "intermediate" / "injection_gitnexus_queue.json").exists()
    assert result["per_class"].get("injection") is None
    dismissed = json.loads(
        (deliverables / "intermediate" / "dismissed_findings.json").read_text())
    assert [d["ID"] for d in dismissed["dismissed"]] == ["INJ-GN-01"]


# ===== spec 2026-08-27 §4：GN 判非漏洞分流——queue 不含 safe 卡 + dismissed 留档 =====

@pytest.mark.asyncio
async def test_safe_verdict_excluded_from_queue_and_archived(tmp_path, monkeypatch):
    """chain_verdict 判 safe（非漏洞）→ 不进 gitnexus_queue、进
    dismissed_findings.json 留档（人工分析，不进报告）；chain_verdicts 数据流
    视图仍全量落（safe 链可见）。"""
    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    _write_pgraph(deliverables, [
        _flow("sql_value", source="a", sink_id="app.py:h:db.execute:5:0"),
        _flow("sql_value", source="b", sink_id="app.py:h:db.execute:6:0"),
    ])

    async def fake_llm(prompt, **kw):
        if "db.execute:5" in prompt:
            return ('{"verdict":"vulnerable","witness_payload":"\'",'
                    '"evidence_chain":"a->db","mismatch_reason":"concat",'
                    '"confidence":"high"}')
        return ('{"verdict":"safe","witness_payload":null,'
                '"evidence_chain":"b->db","mismatch_reason":"parameterized query",'
                '"confidence":"high"}')

    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    monkeypatch.setattr(activities, "_gitnexus_verdict_llm_client", fake_llm, raising=False)
    set_audit_session(_RecordingSession())
    try:
        result = await activities.run_gitnexus_chain_verdict(_input(tmp_path))
    finally:
        clear_audit_session()

    q = json.loads(
        (deliverables / "intermediate" / "injection_gitnexus_queue.json").read_text())
    assert [v["ID"] for v in q["vulnerabilities"]] == ["INJ-GN-01"]
    assert q["vulnerabilities"][0]["verdict"] == "vulnerable"
    assert result["per_class"]["injection"] == 1

    dismissed = json.loads(
        (deliverables / "intermediate" / "dismissed_findings.json").read_text())
    assert [d["ID"] for d in dismissed["dismissed"]] == ["INJ-GN-02"]
    d = dismissed["dismissed"][0]
    assert d["source_track"] == "gitnexus"
    assert d["vuln_class"] == "injection"
    assert d["dismissed_at_stage"] == "chain-verdict"
    assert d["dismiss_reason"] == "parameterized query"
    assert d["evidence"] == "b->db"


# ===== spec 2026-08-27 §3：多轮 verdict agent 接线（env 开走 agent 路径）=====

class _AgentRunResult:
    def __init__(self, text="", structured_output=None, success=True, error=None):
        self.text = text
        self.structured_output = structured_output
        self.success = success
        self.error = error


@pytest.mark.asyncio
async def test_agent_runner_factory_passes_through(monkeypatch, tmp_path):
    """_make_verdict_agent_runner 闭包 → run_gitnexus_verdict_agent 参数透传：
    prompt / structured_output_schema（output_format 归一）/ agent_name /
    audit_session / provider_config / max_turns（env 默认 30）。"""
    captured = {}

    async def fake_run_agent(*, prompt, repo_path, structured_output_schema=None,
                             audit_session=None, provider_config=None,
                             max_turns=None, agent_name="gitnexus-verdict"):
        captured.update(prompt=prompt, repo_path=repo_path,
                        schema=structured_output_schema,
                        audit_session=audit_session,
                        provider_config=provider_config,
                        max_turns=max_turns, agent_name=agent_name)
        return _AgentRunResult(
            structured_output={"verdict": "safe", "witness_payload": None,
                               "evidence_chain": "x->y", "title": "t"})

    monkeypatch.setattr(activities, "run_gitnexus_verdict_agent", fake_run_agent)
    runner = activities._make_verdict_agent_runner(
        str(tmp_path), provider_config={"provider": "x"}, audit_session=object())
    result = await runner("p", output_format={"type": "object"},
                          agent_name="chain-verdict-xss-01")
    assert captured["repo_path"] == str(tmp_path)
    assert captured["schema"] == {"type": "object"}
    assert captured["agent_name"] == "chain-verdict-xss-01"
    assert captured["provider_config"] == {"provider": "x"}
    assert captured["audit_session"] is not None
    assert captured["max_turns"] == 30  # SUPERNOVA_CHAIN_VERDICT_MAX_TURNS 默认


@pytest.mark.asyncio
async def test_activity_uses_agent_path_when_enabled(tmp_path, monkeypatch):
    """env 开（is_gitnexus_llm_enabled=True）→ activity 构造 agent runner 走
    多轮路径（run_gitnexus_verdict_agent 被调、agent_name 唯一化）。"""
    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    _write_pgraph(deliverables, [_flow("sql_value")])
    monkeypatch.setattr(activities, "is_gitnexus_llm_enabled", lambda: True)

    agent_calls = []

    async def fake_run_agent(*, prompt, repo_path, structured_output_schema=None,
                             audit_session=None, provider_config=None,
                             max_turns=None, agent_name="gitnexus-verdict"):
        agent_calls.append(agent_name)
        return _AgentRunResult(structured_output={
            "verdict": "vulnerable", "witness_payload": "'",
            "evidence_chain": "q->db", "mismatch_reason": "concat",
            "confidence": "high", "title": "SQLi"})

    monkeypatch.setattr(activities, "run_gitnexus_verdict_agent", fake_run_agent)
    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    set_audit_session(_RecordingSession())
    try:
        await activities.run_gitnexus_chain_verdict(_input(tmp_path))
    finally:
        clear_audit_session()

    assert agent_calls == ["chain-verdict-injection-01"]
    q = json.loads(
        (deliverables / "intermediate" / "injection_gitnexus_queue.json").read_text())
    assert q["vulnerabilities"][0]["verdict"] == "vulnerable"


@pytest.mark.asyncio
async def test_three_builders_run_concurrently(tmp_path, monkeypatch):
    """inj/xss/ssrf 三类 builder 并发跑：各 1 条候选链时 3 个判定同时在飞
    （三类串行则 max_seen 恒为 1）。"""
    import asyncio

    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    xss_sid = "app.py:h:innerHTML:5:0"
    _write_pgraph(deliverables, [
        _flow("sql_value", source="q", sink_id="app.py:h:db.execute:5:0"),   # injection
        _flow("generic", source="u", sink_id=xss_sid),                       # xss
        _flow("url", source="v", sink_id="app.py:h:fetch:6:0"),              # ssrf
    ])
    _write_sink(deliverables, xss_sid, "xss", "xss_innerhtml")

    state = {"in_flight": 0, "max_seen": 0}

    async def fake_llm(prompt, **kw):
        state["in_flight"] += 1
        state["max_seen"] = max(state["max_seen"], state["in_flight"])
        await asyncio.sleep(0.03)
        state["in_flight"] -= 1
        return ('{"verdict":"safe","witness_payload":"","evidence_chain":"s->k",'
                '"mismatch_reason":"","confidence":"high"}')

    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    monkeypatch.setattr(activities, "_gitnexus_verdict_llm_client", fake_llm, raising=False)
    set_audit_session(_RecordingSession())
    try:
        result = await activities.run_gitnexus_chain_verdict(_input(tmp_path))
    finally:
        clear_audit_session()

    assert state["max_seen"] > 1
    assert result["failed_classes"] == []


@pytest.mark.asyncio
async def test_builders_run_concurrently_shared_budget(tmp_path, monkeypatch):
    """三类 builder 并发跑（共享一个并发预算）：跨类的 verdict agent 同时
    in-flight——串行分段时峰值=单类链数，并发共享预算时跨类同飞。"""
    import asyncio
    from types import SimpleNamespace

    monkeypatch.setattr(activities, "is_gitnexus_llm_enabled", lambda: True)
    state = {"in_flight": 0, "max_seen": 0, "names": []}

    async def fake_agent(prompt, *, output_format=None, agent_name=None):
        state["in_flight"] += 1
        state["max_seen"] = max(state["max_seen"], state["in_flight"])
        await asyncio.sleep(0.08)
        state["in_flight"] -= 1
        state["names"].append(agent_name)
        return SimpleNamespace(structured_output={
            "verdict": "safe", "witness_payload": None,
            "evidence_chain": "q -> sink", "title": "t"}, text="")

    monkeypatch.setattr(
        activities, "_make_verdict_agent_runner",
        lambda *a, **kw: fake_agent)

    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    # inj 2 链（sql_value）+ ssrf 2 链（url）：类内并行下串行分段峰值=2，
    # 三类并发共享预算（默认 4）时跨类同飞 → 峰值 ≥3。
    _write_pgraph(deliverables, [
        _flow("sql_value", source="q1"), _flow("sql_value", source="q2"),
        _flow("url", source="u1"), _flow("url", source="u2"),
    ])
    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    session = _RecordingSession()
    set_audit_session(session)
    try:
        await activities.run_gitnexus_chain_verdict(_input(tmp_path))
    finally:
        clear_audit_session()

    assert state["max_seen"] >= 3
    assert sorted(state["names"]) == [
        "chain-verdict-injection-01", "chain-verdict-injection-02",
        "chain-verdict-ssrf-01", "chain-verdict-ssrf-02"]


# ── step cache 接线（spec 2026-08-27-web-resume-breakpoint §4.3）───────────────
#
# marker + 输入指纹（parameter_graph.json / code_index.json）+ salt
# （gn-llm env 开关）——命中则整段判定机制不跑、直接还原缓存返回值；
# 干净完成（failed_classes 空）末尾打点，类级失败不打（resume=再试一次）。

_STEP = "gitnexus-chain-verdict"


def _cache_inputs(deliverables):
    from supernova_core.utils.paths import intermediate_path
    return [intermediate_path(deliverables, "parameter_graph.json"),
            intermediate_path(deliverables, "code_index.json")]


def _salt():
    return f"gn-llm={activities.is_gitnexus_llm_enabled()}"


def _marker(deliverables):
    return deliverables / "intermediate" / ".step-cache" / f"{_STEP}.json"


@pytest.mark.asyncio
async def test_step_cache_hit_skips_verdict_machinery(tmp_path, monkeypatch):
    """marker 有效（指纹+salt 匹配）→ 判定通道不进，返回缓存快照。"""
    from supernova_whitebox.pipeline import step_cache
    deliverables = tmp_path / "deliverables" / "whitebox"
    inter = deliverables / "intermediate"
    inter.mkdir(parents=True)
    (inter / "parameter_graph.json").write_text("{}")
    (inter / "code_index.json").write_text("{}")
    cached_ret = {"per_class": {"injection": 2}, "failed_classes": [],
                  "fail_reasons": {}}
    step_cache.mark_done(_STEP, deliverables, inputs=_cache_inputs(deliverables),
                         outputs=[], ret=cached_ret, salt=_salt())

    async def fake_llm(prompt, **kw):
        raise AssertionError("缓存命中时不得走判定通道")

    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    monkeypatch.setattr(activities, "_gitnexus_verdict_llm_client", fake_llm, raising=False)
    set_audit_session(_RecordingSession())
    try:
        result = await activities.run_gitnexus_chain_verdict(_input(tmp_path))
    finally:
        clear_audit_session()

    assert result == cached_ret


@pytest.mark.asyncio
async def test_clean_run_writes_marker(tmp_path, monkeypatch):
    """干净完成末尾打点，键料（inputs/salt/ret）与跳过侧一致——下一轮 resume 即命中。"""
    from supernova_whitebox.pipeline import step_cache
    deliverables = tmp_path / "deliverables" / "whitebox"
    inter = deliverables / "intermediate"
    inter.mkdir(parents=True)
    (inter / "parameter_graph.json").write_text(json.dumps({
        "taint_flows": [_flow("sql_value")],
        "language_coverage": ["python"], "skipped_languages": []}))

    async def fake_llm(prompt, **kw):
        return ('{"verdict":"vulnerable","witness_payload":"\'","evidence_chain":'
                '"q->db","mismatch_reason":"concat","confidence":"high"}')

    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    monkeypatch.setattr(activities, "_gitnexus_verdict_llm_client", fake_llm, raising=False)
    set_audit_session(_RecordingSession())
    try:
        result = await activities.run_gitnexus_chain_verdict(_input(tmp_path))
    finally:
        clear_audit_session()

    assert result["failed_classes"] == []
    skip, cached = step_cache.should_skip(
        _STEP, deliverables, inputs=_cache_inputs(deliverables), salt=_salt())
    assert skip is True
    assert cached == result


@pytest.mark.asyncio
async def test_failed_classes_run_does_not_write_marker(tmp_path, monkeypatch):
    """类级判定失败（failed_classes 非空）不打点——resume 语义=再试一次；
    打点会让关轨 fail-fast 的续跑用缓存返回值原地下场（spec §4.3）。"""
    deliverables = tmp_path / "deliverables" / "whitebox"
    inter = deliverables / "intermediate"
    inter.mkdir(parents=True)
    (inter / "parameter_graph.json").write_text(json.dumps({
        "taint_flows": [_flow("sql_value")],
        "language_coverage": ["python"], "skipped_languages": []}))

    import supernova_core.code_index.vuln_chain_builders.injection_builder as ib
    monkeypatch.setattr(
        ib, "build_injection_findings",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("builder boom")))

    async def fake_llm(prompt, **kw):
        return ('{"verdict":"vulnerable","witness_payload":"x","evidence_chain":"q->db",'
                '"mismatch_reason":"concat","confidence":"high"}')

    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    monkeypatch.setattr(activities, "_gitnexus_verdict_llm_client", fake_llm, raising=False)
    set_audit_session(_RecordingSession())
    try:
        result = await activities.run_gitnexus_chain_verdict(_input(tmp_path))
    finally:
        clear_audit_session()

    assert "injection" in result["failed_classes"]
    assert not _marker(deliverables).exists()
