# packages/whitebox/tests/test_run_report_polish.py
"""T5（spec 2026-08-26-report-generation-agent §5.4/§5.5）+ T4 接线。

覆盖：polish 重建 report_data（吃全部富化字段）/ ④摘要 LLM 产物与确定性
兜底 / ⑤QA 必填校验（taint 卡 endpoints≥1）与回炉一次 / §4.2（spec
2026-08-26-vuln-card-seven-sections）POC 写回时序前移——generate_poc_report
不再触发写回、write_agent_poc 独立 activity 写回 report_poc、
两处 worker 注册表含新 activity。
"""
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

from supernova_whitebox.pipeline import activities


class _FakeInput:
    def __init__(self, tmp_path):
        self.agent_name = None
        self.web_url = ""
        self.repo_path = str(tmp_path)
        self.deliverables_subdir = None
        self.workspace_name = None
        self.workspace_path = None
        self.config_path = None
        self.api_key = None
        self.pipeline_testing_mode = False
        self.prompt_override = None
        self.provider_config = None
        self.vuln_classes = None


class _RecordingSession:
    @asynccontextmanager
    async def track_step(self, phase, name, intent=None):
        yield


def _wb(tmp_path):
    d = tmp_path / "deliverables" / "whitebox"
    (d / "intermediate").mkdir(parents=True, exist_ok=True)
    return d


def _write_queue(d, vulns, name="xss_exploitation_queue.json"):
    d.joinpath("intermediate", name).write_text(
        json.dumps({"vulnerabilities": vulns}))


async def test_polish_rebuilds_with_enrichments_and_llm_summary(tmp_path, monkeypatch):
    """polish 重建 rd（含 report_endpoints/report_poc）+ ④LLM 摘要写回。

    §6 后 QA 阈值抬升：fixture 需七节齐（problem_points/params/行号链/
    narrative/POC），否则触发回炉路径（未 patch 的 agent 会真调 LLM）。"""
    d = _wb(tmp_path)
    _write_queue(d, [{
        "ID": "XSS-VULN-01", "vulnerability_type": "Stored",
        "externally_exploitable": True, "confidence": "high",
        "merge_source": "llm-only", "title": "t", "severity": "high",
        "notes": "成因", "impact": "危害", "remediation": "修复",
        "report_endpoints": [{"method": "POST", "path": "/memos",
                              "params": ["memo"],
                              "route_registered_at": "app/routes/index.js:66",
                              "sink_location": "app/views/memos.html:31"}],
        "report_problem_points": [{"location": "app/views/memos.html:31",
                                   "description": "未消毒渲染",
                                   "snippet": "<%- memo %>"}],
        "report_poc": {"curl": "curl -X POST http://t/memos",
                       "steps": ["plant", "trigger"],
                       "self_check": "pass"},
    }])
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, d, tmp_path))
    summary_payload = {
        "narrative": "本次扫描发现严重 XSS。",
        "risk_level": "极高",
        "top_risks": [{"vuln_id": "XSS-VULN-01", "reason": "可窃会话",
                       "priority": "P0"}],
        "remediation_order": "先修 XSS",
    }
    with patch.object(activities, "run_claude_prompt",
                      return_value=SimpleNamespace(
                          structured_output=summary_payload, text=None)):
        result = await activities.run_report_polish(_FakeInput(tmp_path))

    assert result["summary"] == "llm"
    data = json.loads(d.joinpath("report_data.json").read_text(encoding="utf-8"))
    assert data["executive_summary"]["narrative"] == "本次扫描发现严重 XSS。"
    assert data["executive_summary"]["top_risks"][0]["vuln_id"] == "XSS-VULN-01"
    v = data["vulnerabilities"][0]
    assert v["endpoints"][0]["route_registered_at"] == "app/routes/index.js:66"
    assert v["poc"]["curl"].startswith("curl -X POST")
    assert data["qa"]["passed"] is True


async def test_polish_summary_deterministic_fallback(tmp_path, monkeypatch):
    """LLM 摘要失败 → 确定性摘要（top_risks 按 severity 排序）。"""
    d = _wb(tmp_path)
    _write_queue(d, [{
        "ID": "XSS-VULN-01", "vulnerability_type": "Stored",
        "externally_exploitable": True, "confidence": "high",
        "merge_source": "llm-only", "title": "t", "severity": "critical",
        "notes": "成因", "impact": "危害", "remediation": "修复",
        "report_endpoints": [{"method": "POST", "path": "/memos",
                              "params": ["memo"],
                              "sink_location": "app/views/memos.html:31"}],
        "report_problem_points": [{"location": "app/views/memos.html:31",
                                   "description": "未消毒渲染",
                                   "snippet": "<%- memo %>"}],
        "report_poc": {"curl": "curl -X POST http://t/memos",
                       "self_check": "pass"},
    }])
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, d, tmp_path))

    async def _boom(**kw):
        raise RuntimeError("llm down")
    with patch.object(activities, "run_claude_prompt", side_effect=_boom):
        result = await activities.run_report_polish(_FakeInput(tmp_path))

    assert result["summary"] == "deterministic"
    data = json.loads(d.joinpath("report_data.json").read_text(encoding="utf-8"))
    es = data["executive_summary"]
    assert es["narrative"]          # 确定性摘要非空
    assert es["top_risks"][0]["vuln_id"] == "XSS-VULN-01"


async def test_polish_qa_flags_and_reworks_missing_endpoints(tmp_path, monkeypatch):
    """⑤QA：taint 卡缺 endpoints → 回炉一次（富化 agent）→ 仍缺则 qa.passed=false。

    §6 扩展：同一富化 agent payload 兼供 narrative 回炉（id/ID 双 key，
    endpoints+problem_points+notes/impact/remediation 一并补齐）。"""
    d = _wb(tmp_path)
    _write_queue(d, [{
        "ID": "XSS-GN-01", "vulnerability_type": "Reflected",
        "externally_exploitable": True, "confidence": "unadjudicated",
        "merge_source": "gitnexus-only", "title": "t", "severity": "high",
        "endpoint": "GET /contributions",
        "witness_payload": "<img src=x>",
    }])
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, d, tmp_path))
    # 两个 agent 各按自己的 schema 产 payload（生产中本就是不同 agent 调用）：
    # 第 1 次调用 = 接口富化（endpoints 为 dict 列表 + problem_points）；
    # 第 2 次调用 = narrative 深富化（endpoints 字段名在 queue 是 str 列表，互不混用）。
    ep_payload = {"vulnerabilities": [{
        "id": "XSS-GN-01",
        "endpoints": [{"method": "GET", "path": "/contributions",
                       "params": ["preTax"],
                       "sink_location": "app/routes/contributions.js:21"}],
        "problem_points": [{"location": "app/routes/contributions.js:21",
                            "description": "eval 直达",
                            "snippet": "eval(preTax)"}],
    }]}
    narr_payload = {"vulnerabilities": [{
        "ID": "XSS-GN-01",
        "notes": "成因", "impact": "危害", "remediation": "修复",
    }]}

    async def _agent_side_effect(**kw):
        _agent_side_effect.calls.append(kw.get("agent_name"))
        payload = _agent_side_effect.payloads.pop(0)
        return SimpleNamespace(structured_output=payload, text=None)
    poc_payload = {"pocs": [{
        "vulnerability_id": "XSS-GN-01",
        "curl": "curl -i 'http://TARGET/contributions?preTax=<img>'",
        "raw_http": "GET /contributions?preTax=<img> HTTP/1.1\nHost: TARGET",
        "preconditions": "无需登录", "expected_response": "payload 反射",
        "self_check": "pass"}]}
    # payloads 顺序 = 回炉调用顺序：接口富化 → POC 补写（poc-agent，spec
    # 2026-08-27-poc-agent-direct-design——QA 回炉缺 POC 的卡也走 poc-agent）
    # → narrative 富化
    _agent_side_effect.payloads = [ep_payload, poc_payload, narr_payload]
    _agent_side_effect.calls = []

    with patch.object(activities, "run_claude_prompt",
                      return_value=SimpleNamespace(
                          structured_output={"narrative": "s",
                                             "risk_level": "高",
                                             "top_risks": [],
                                             "remediation_order": None},
                          text=None)), \
         patch.object(activities, "run_gitnexus_verdict_agent",
                      side_effect=_agent_side_effect):
        result = await activities.run_report_polish(_FakeInput(tmp_path))

    assert result["reworked"] == ["XSS-GN-01"]  # 多路回炉去重（同一卡只记一次）
    # 回炉 agent 记账唯一名（防 metrics.agents 同名覆盖）：接口富化/POC 补写/
    # narrative 富化各带 vuln_class 后缀，与主富化（endpoint-enrich-*/gn-enrich-*）分流。
    assert _agent_side_effect.calls == [
        "endpoint-enrich-rework-xss", "poc-agent-xss", "gn-enrich-rework-xss"]
    data = json.loads(d.joinpath("report_data.json").read_text(encoding="utf-8"))
    assert data["qa"]["passed"] is True
    assert data["qa"]["reworked_ids"] == ["XSS-GN-01"]
    v = data["vulnerabilities"][0]
    assert v["endpoints"][0]["path"] == "/contributions"
    assert v["problem_points"][0]["location"] == "app/routes/contributions.js:21"
    # queue 也被回炉写回（下次 rebuild 同源）
    queue = json.loads(d.joinpath("intermediate", "xss_exploitation_queue.json")
                       .read_text(encoding="utf-8"))
    assert queue["vulnerabilities"][0]["report_endpoints"][0]["path"] == \
        "/contributions"


async def test_polish_qa_flags_when_rework_also_fails(tmp_path, monkeypatch):
    """回炉也失败 → qa.passed=false + failed_ids 显式呈现（不静默）。"""
    d = _wb(tmp_path)
    _write_queue(d, [{
        "ID": "SSRF-GN-01", "vulnerability_type": "SSRF",
        "externally_exploitable": True, "confidence": "low",
        "merge_source": "gitnexus-only", "title": "t", "severity": "high",
    }])
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, d, tmp_path))

    async def _boom_agent(**kw):
        raise RuntimeError("llm down")
    with patch.object(activities, "run_claude_prompt", side_effect=_boom_agent), \
         patch.object(activities, "run_gitnexus_verdict_agent",
                      side_effect=_boom_agent):
        await activities.run_report_polish(_FakeInput(tmp_path))

    data = json.loads(d.joinpath("report_data.json").read_text(encoding="utf-8"))
    assert data["qa"]["passed"] is False
    check = next(c for c in data["qa"]["checks"]
                 if "endpoints" in c["check"])
    assert "SSRF-GN-01" in check["failed_ids"]


# ---------- §6（spec 2026-08-26-report-single-source-rendering）七节覆盖率 QA ----------

async def test_polish_qa_seven_section_checks(tmp_path, monkeypatch):
    """§6：七节覆盖率逐卡 checks——缺 problem_points/poc/params/narrative/
    行号链各记 failed_ids（显式呈现，不静默）。

    新世界观（spec 2026-08-27-poc-agent-direct-design）：POC 由 poc-agent 产，
    agent down → 全部卡缺 POC（诚实缺失，无确定性兜底）——两卡都进 failed_ids。"""
    d = _wb(tmp_path)
    _write_queue(d, [{
        # taint 卡：接口有（report_endpoints）但无参数/无行号链，
        # 无 problem_points/narrative；路由锚点在 → POC 可产
        "ID": "XSS-VULN-01", "vulnerability_type": "Stored",
        "externally_exploitable": True, "confidence": "high",
        "merge_source": "llm-only", "title": "t", "severity": "high",
        "report_endpoints": [{"method": "POST", "path": "/memos"}],
    }, {
        # GN 轨卡：无任何路由锚点（endpoint/endpoints/report_endpoints 全空，
        # path 是数据流摘要）→ 结构化 POC 提不出 → poc_complete 检出
        "ID": "XSS-GN-09", "vulnerability_type": "Stored",
        "externally_exploitable": True, "confidence": "low",
        "merge_source": "gitnexus-only", "title": "t2", "severity": "high",
        "path": "memo -> app/routes/memos.js:render:12:5 (needs_review)",
    }])
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, d, tmp_path))
    with patch.object(activities, "run_claude_prompt",
                      return_value=SimpleNamespace(
                          structured_output={"narrative": "s",
                                             "risk_level": "高",
                                             "top_risks": [],
                                             "remediation_order": None},
                          text=None)), \
         patch.object(activities, "run_gitnexus_verdict_agent",
                      side_effect=RuntimeError("agent down")):
        await activities.run_report_polish(_FakeInput(tmp_path))

    data = json.loads(d.joinpath("report_data.json").read_text(encoding="utf-8"))
    checks = {c["check"]: c["failed_ids"] for c in data["qa"]["checks"]}
    assert checks["problem_points_present"] == ["XSS-VULN-01", "XSS-GN-09"]
    assert checks["poc_complete"] == ["XSS-VULN-01", "XSS-GN-09"]
    assert checks["params_present"] == ["XSS-VULN-01"]
    assert checks["narrative_complete"] == ["XSS-VULN-01", "XSS-GN-09"]
    assert checks["endpoint_rows_have_locations"] == ["XSS-VULN-01"]
    assert data["qa"]["passed"] is False


async def test_polish_qa_seven_section_checks_scoping(tmp_path, monkeypatch):
    """§6 口径：params/行号链/problem_points 只查 taint 卡；poc/narrative 查全卡；
    齐全的卡不进 failed_ids。"""
    d = _wb(tmp_path)
    _write_queue(d, [{
        # 齐 卡（taint 七节全齐）
        "ID": "XSS-VULN-02", "vulnerability_type": "Stored",
        "externally_exploitable": True, "confidence": "high",
        "merge_source": "llm-only", "title": "t2", "severity": "high",
        "notes": "c", "impact": "i", "remediation": "r",
        "report_endpoints": [{"method": "POST", "path": "/memos",
                              "params": ["memo"],
                              "sink_location": "app.js:9"}],
        "report_problem_points": [{"location": "app.js:9",
                                   "description": "d", "snippet": "s"}],
        "report_poc": {"curl": "curl http://t", "self_check": "pass"},
    }], name="xss_exploitation_queue.json")
    _write_queue(d, [{
        # auth 卡：无 endpoints/problem_points（非 taint，不查）；缺 narrative/POC
        "ID": "AUTH-VULN-01", "vulnerability_type": "Auth",
        "externally_exploitable": True, "confidence": "high",
        "merge_source": "llm-only", "title": "t", "severity": "medium",
    }], name="auth_exploitation_queue.json")
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, d, tmp_path))
    with patch.object(activities, "run_claude_prompt",
                      return_value=SimpleNamespace(
                          structured_output={"narrative": "s",
                                             "risk_level": "高",
                                             "top_risks": [],
                                             "remediation_order": None},
                          text=None)), \
         patch.object(activities, "run_gitnexus_verdict_agent",
                      side_effect=RuntimeError("agent down")):
        await activities.run_report_polish(_FakeInput(tmp_path))

    data = json.loads(d.joinpath("report_data.json").read_text(encoding="utf-8"))
    checks = {c["check"]: c["failed_ids"] for c in data["qa"]["checks"]}
    # taint 专属 checks：齐 卡不入，auth 卡（非 taint）也不入
    assert checks["problem_points_present"] == []
    assert checks["params_present"] == []
    assert checks["endpoint_rows_have_locations"] == []
    # 全卡 checks：auth 缺 narrative/POC 入列
    assert checks["poc_complete"] == ["AUTH-VULN-01"]
    assert checks["narrative_complete"] == ["AUTH-VULN-01"]


async def test_polish_reworks_missing_pocs(tmp_path, monkeypatch):
    """§6 回炉：缺 POC 的卡走结构化 POC 写回路径（复用 write_agent_poc
    逻辑，仅补缺失卡）→ queue 写回 report_poc → 重建后 poc_complete 过。"""
    d = _wb(tmp_path)
    _write_queue(d, [{
        "ID": "XSS-VULN-01", "vulnerability_type": "Stored",
        "externally_exploitable": True, "confidence": "high",
        "merge_source": "llm-only", "title": "t", "severity": "high",
        "notes": "c", "impact": "i", "remediation": "r",
        "endpoint": "POST /memos",
        "witness_payload": "<img src=x>",
        "report_endpoints": [{"method": "POST", "path": "/memos",
                              "params": ["memo"],
                              "sink_location": "app.js:9"}],
        "report_problem_points": [{"location": "app.js:9",
                                   "description": "d", "snippet": "s"}],
    }])
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, d, tmp_path))
    poc_payload = {"pocs": [{
        "vulnerability_id": "XSS-VULN-01",
        "curl": "curl -i -X POST 'http://TARGET/memos' --data 'memo=<img src=x>'",
        "raw_http": "POST /memos HTTP/1.1\nHost: TARGET",
        "steps": ["plant via POST /memos", "victim opens /memos"],
        "preconditions": "需登录", "expected_response": "alert 触发",
        "self_check": "pass"}]}
    async def _poc_agent(**kw):
        return SimpleNamespace(structured_output=poc_payload, text=None)
    with patch.object(activities, "run_claude_prompt",
                      return_value=SimpleNamespace(
                          structured_output={"narrative": "s",
                                             "risk_level": "高",
                                             "top_risks": [],
                                             "remediation_order": None},
                          text=None)), \
         patch.object(activities, "run_gitnexus_verdict_agent",
                      side_effect=_poc_agent):
        result = await activities.run_report_polish(_FakeInput(tmp_path))

    assert "XSS-VULN-01" in result["reworked"]
    queue = json.loads(d.joinpath("intermediate", "xss_exploitation_queue.json")
                       .read_text(encoding="utf-8"))
    poc = queue["vulnerabilities"][0]["report_poc"]
    assert poc["curl"].startswith("curl -i -X POST")
    assert poc["steps"] == ["plant via POST /memos", "victim opens /memos"]
    data = json.loads(d.joinpath("report_data.json").read_text(encoding="utf-8"))
    checks = {c["check"]: c["failed_ids"] for c in data["qa"]["checks"]}
    assert checks["poc_complete"] == []


async def test_polish_reworks_missing_narratives(tmp_path, monkeypatch):
    """§6 回炉：narrative 缺段 → GN 深富化路径（gn_finding_enrichment prompt
    + 白名单仅补空缺）→ queue 写回 → 重建后 narrative_complete 过。"""
    d = _wb(tmp_path)
    _write_queue(d, [{
        "ID": "XSS-VULN-01", "vulnerability_type": "Stored",
        "externally_exploitable": True, "confidence": "high",
        "merge_source": "llm-only", "title": "t", "severity": "high",
        "impact": "只有危害段",
        "report_endpoints": [{"method": "POST", "path": "/memos",
                              "params": ["memo"],
                              "sink_location": "app.js:9"}],
        "report_problem_points": [{"location": "app.js:9",
                                   "description": "d", "snippet": "s"}],
        "report_poc": {"curl": "curl http://t", "self_check": "pass"},
    }])
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, d, tmp_path))
    enrich_payload = {"vulnerabilities": [{
        "ID": "XSS-VULN-01", "notes": "成因补全", "remediation": "修复补全",
    }]}
    with patch.object(activities, "run_claude_prompt",
                      return_value=SimpleNamespace(
                          structured_output={"narrative": "s",
                                             "risk_level": "高",
                                             "top_risks": [],
                                             "remediation_order": None},
                          text=None)), \
         patch.object(activities, "run_gitnexus_verdict_agent",
                      return_value=SimpleNamespace(
                          structured_output=enrich_payload, text=None)) as m_agent:
        result = await activities.run_report_polish(_FakeInput(tmp_path))

    # 深富化 agent 被调（gn_finding_enrichment prompt）+ 记账唯一名
    assert m_agent.called
    assert m_agent.call_args.kwargs["agent_name"] == "gn-enrich-rework-xss"
    assert "XSS-VULN-01" in result["reworked"]
    queue = json.loads(d.joinpath("intermediate", "xss_exploitation_queue.json")
                       .read_text(encoding="utf-8"))
    qv = queue["vulnerabilities"][0]
    assert qv["notes"] == "成因补全"
    assert qv["remediation"] == "修复补全"
    assert qv["impact"] == "只有危害段"  # 已有段不覆写（白名单仅补空缺）
    data = json.loads(d.joinpath("report_data.json").read_text(encoding="utf-8"))
    checks = {c["check"]: c["failed_ids"] for c in data["qa"]["checks"]}
    assert checks["narrative_complete"] == []


# ---------- §4.2（spec 2026-08-26-vuln-card-seven-sections）POC 写回时序前移 ----------

_POC_QUEUE_VULN = {
    "ID": "XSS-VULN-01", "vulnerability_type": "Stored",
    "externally_exploitable": True, "confidence": "high",
    "merge_source": "llm-only", "title": "t", "severity": "high",
    "endpoint": "POST /memos",
    "witness_payload": "<img src=x onerror=alert(1)>",
    "authentication_required": "true",
}



async def test_write_agent_poc_activity_writes_report_poc(tmp_path, monkeypatch):
    """§4.2 + spec 2026-08-27-poc-agent-direct-design：write_agent_poc 独立
    activity 可跑，经 poc-agent 产出写回 report_poc（新文本 schema，透传）。"""
    d = _wb(tmp_path)
    _write_queue(d, [_POC_QUEUE_VULN])
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, d, tmp_path))
    payload = {"pocs": [{
        "vulnerability_id": "XSS-VULN-01",
        "curl": "curl -i -X POST 'http://TARGET/memos' --data 'memo=<img src=x>'",
        "raw_http": "POST /memos HTTP/1.1\nHost: TARGET",
        "steps": ["plant via POST /memos", "victim opens /memos"],
        "preconditions": "需登录",
        "expected_response": "alert 触发",
        "self_check": "pass"}]}
    with patch.object(activities, "run_gitnexus_verdict_agent",
                      return_value=SimpleNamespace(
                          structured_output=payload, text=None, success=True)):
        await activities.write_agent_poc(_FakeInput(tmp_path))

    queue = json.loads(d.joinpath("intermediate", "xss_exploitation_queue.json")
                       .read_text(encoding="utf-8"))
    poc = queue["vulnerabilities"][0]["report_poc"]
    assert poc["curl"].startswith("curl -i -X POST")   # agent 文本透传
    assert poc["steps"] == ["plant via POST /memos", "victim opens /memos"]
    assert poc["self_check"] == "pass"
    assert "request" not in poc                         # 旧确定性 schema 已退役


def test_write_agent_poc_registered_on_workers():
    """b51eb9a4 教训：新 activity 两处 worker 注册表（CLI worker.py + web
    runner.py）都必须 import + 列入 activities，漏注册会 fail-fast/静默不跑。"""
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    for rel in ("packages/whitebox/src/supernova_whitebox/worker.py",
                "packages/worker/src/supernova_worker/runner.py"):
        src = (root / rel).read_text(encoding="utf-8")
        count = src.count("write_agent_poc")
        assert count >= 2, (
            f"write_agent_poc 在 {rel} 仅出现 {count} 次，"
            f"预期 >= 2（import + activities 列表）"
        )
