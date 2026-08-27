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
