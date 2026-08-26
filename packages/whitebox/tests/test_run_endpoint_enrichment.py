# packages/whitebox/tests/test_run_endpoint_enrichment.py
"""T3（spec 2026-08-26-report-generation-agent §5.2）：全卡接口表富化。

覆盖：agent 产物写回 report_endpoints（含行号链）/ 幻觉 ID 丢弃 / 畸形
endpoint 条目丢弃 / agent 失败 non-fatal（queue 不变）/ 素材包含
entry_points 路由表 / 开关门控（SUPERNOVA_ENDPOINT_ENRICH_ENABLED）。
"""
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

from supernova_whitebox.pipeline import activities


class _FakeInput:
    def __init__(self, tmp_path):
        self.agent_name = None
        self.web_url = None
        self.repo_path = str(tmp_path)
        self.deliverables_subdir = None
        self.workspace_name = None
        self.workspace_path = None
        self.config_path = None
        self.api_key = None
        self.pipeline_testing_mode = False
        self.prompt_override = None
        self.provider_config = None


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


def _write_entry_points(d, entries):
    d.joinpath("intermediate", "entry_points.json").write_text(json.dumps({
        "repository": "/repo", "language": "javascript",
        "adjudicated_entry_points": entries,
    }))


_EP = {"route": "/memos", "http_method": "POST",
       "func_block_id": "app/routes/index.js:index:66",
       "evidence": "Express route: app.post('/memos')",
       "verdict": "confirmed", "entry_type": "http_route", "source": "code_index"}

_XSS_VULN = {
    "ID": "XSS-VULN-01", "vulnerability_type": "Stored",
    "externally_exploitable": True, "confidence": "high",
    "merge_source": "llm-only", "title": "存储型 XSS：POST /memos",
    "severity": "high",
    "endpoints": ["POST /memos (write, isLoggedIn)", "GET /memos (trigger)"],
}


def _agent_result(payload):
    return SimpleNamespace(structured_output=payload, text=None)


async def test_endpoint_enrichment_writes_report_endpoints(tmp_path, monkeypatch):
    """agent 产物（接口一体表含行号链）按 ID 写回 report_endpoints。"""
    d = _wb(tmp_path)
    _write_queue(d, [_XSS_VULN])
    _write_entry_points(d, [_EP])
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, d, tmp_path))
    payload = {"vulnerabilities": [{
        "id": "XSS-VULN-01",
        "endpoints": [{
            "method": "POST", "path": "/memos", "role": "write",
            "auth": "isLoggedIn", "params": ["memo"],
            "route_registered_at": "app/routes/index.js:66",
            "source_location": "app/routes/memos.js:13",
            "sink_location": "app/views/memos.html:31",
        }],
    }]}
    with patch.object(activities, "run_gitnexus_verdict_agent",
                      return_value=_agent_result(payload)) as mock_agent, \
         patch("supernova_core.config.concurrency.ws_getenv",
               lambda k, d=None: {"SUPERNOVA_ENDPOINT_ENRICH_ENABLED": "1"}.get(k, d)):
        result = await activities.run_endpoint_enrichment(_FakeInput(tmp_path))

    assert result["total_enriched"] == 1
    queue = json.loads(d.joinpath("intermediate", "xss_exploitation_queue.json")
                       .read_text(encoding="utf-8"))
    entry = queue["vulnerabilities"][0]
    assert entry["report_endpoints"][0]["route_registered_at"] == \
        "app/routes/index.js:66"
    assert entry["report_endpoints"][0]["sink_location"] == \
        "app/views/memos.html:31"
    # prompt 素材含路由表行
    prompt = mock_agent.call_args.kwargs["prompt"]
    assert "POST /memos" in prompt
    assert "app/routes/index.js" in prompt


async def test_endpoint_enrichment_drops_hallucinated_and_malformed(tmp_path, monkeypatch):
    """幻觉 ID 整条丢弃；path 不以 / 开头的条目丢弃，合法条目照常写。"""
    d = _wb(tmp_path)
    _write_queue(d, [_XSS_VULN])
    _write_entry_points(d, [_EP])
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, d, tmp_path))
    payload = {"vulnerabilities": [
        {"id": "XSS-GHOST-99", "endpoints": [{"method": "GET", "path": "/x"}]},
        {"id": "XSS-VULN-01", "endpoints": [
            {"method": "GET", "path": "not-a-path"},       # 畸形 path
            {"method": "GET", "path": "/memos"},           # 合法
        ]},
    ]}
    with patch.object(activities, "run_gitnexus_verdict_agent",
                      return_value=_agent_result(payload)), \
         patch("supernova_core.config.concurrency.ws_getenv",
               lambda k, d=None: {"SUPERNOVA_ENDPOINT_ENRICH_ENABLED": "1"}.get(k, d)):
        result = await activities.run_endpoint_enrichment(_FakeInput(tmp_path))

    queue = json.loads(d.joinpath("intermediate", "xss_exploitation_queue.json")
                       .read_text(encoding="utf-8"))
    eps = queue["vulnerabilities"][0]["report_endpoints"]
    assert [e["path"] for e in eps] == ["/memos"]
    assert result["total_enriched"] == 1


async def test_endpoint_enrichment_agent_failure_nonfatal(tmp_path, monkeypatch):
    """agent 失败 → queue 原样保留（确定性字段兜底），返回 failed 状态。"""
    d = _wb(tmp_path)
    _write_queue(d, [_XSS_VULN])
    _write_entry_points(d, [_EP])
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, d, tmp_path))

    async def _boom(**kw):
        raise RuntimeError("llm unavailable")
    with patch.object(activities, "run_gitnexus_verdict_agent", side_effect=_boom), \
         patch("supernova_core.config.concurrency.ws_getenv",
               lambda k, d=None: {"SUPERNOVA_ENDPOINT_ENRICH_ENABLED": "1"}.get(k, d)):
        result = await activities.run_endpoint_enrichment(_FakeInput(tmp_path))

    queue = json.loads(d.joinpath("intermediate", "xss_exploitation_queue.json")
                       .read_text(encoding="utf-8"))
    assert "report_endpoints" not in queue["vulnerabilities"][0]
    assert result["enriched_classes"]["xss"]["failed"]


async def test_endpoint_enrichment_disabled_by_env(tmp_path, monkeypatch):
    """SUPERNOVA_ENDPOINT_ENRICH_ENABLED=0 → 跳过（agent 不调用）。"""
    d = _wb(tmp_path)
    _write_queue(d, [_XSS_VULN])
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, d, tmp_path))
    with patch.object(activities, "run_gitnexus_verdict_agent") as mock_agent, \
         patch("supernova_core.config.concurrency.ws_getenv",
               lambda k, d=None: {"SUPERNOVA_ENDPOINT_ENRICH_ENABLED": "0"}.get(k, d)):
        result = await activities.run_endpoint_enrichment(_FakeInput(tmp_path))
    assert result["skipped"] == "disabled"
    mock_agent.assert_not_called()
