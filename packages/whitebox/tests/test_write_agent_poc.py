# packages/whitebox/tests/test_write_agent_poc_agent.py
"""write_agent_poc 重写为 poc-agent 接线（spec 2026-08-27-poc-agent-direct-design §3）。

对齐 endpoint_enrichment 模式（run_gitnexus_verdict_agent 多轮 + structured
output）。新契约：report_poc = {curl, raw_http, steps, preconditions,
expected_response, self_check, notes}（agent 直产文本，透传不改写）；
失败诚实缺失（不写回、不抛）；默认跳过已有 report_poc 的卡（写回即
checkpoint）；only_ids 回炉过滤。
"""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from supernova_whitebox.pipeline import activities


class _FakeInput:
    def __init__(self, tmp_path, web_url=""):
        self.agent_name = None
        self.web_url = web_url
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


def _wb(tmp_path):
    d = tmp_path / "deliverables" / "whitebox"
    (d / "intermediate").mkdir(parents=True, exist_ok=True)
    return d


def _write_queue(d, vulns, name="xss_exploitation_queue.json"):
    d.joinpath("intermediate", name).write_text(
        json.dumps({"vulnerabilities": vulns}))


def _read_queue(d, name="xss_exploitation_queue.json"):
    return json.loads(d.joinpath("intermediate", name).read_text(encoding="utf-8"))


def _agent_result(payload):
    return SimpleNamespace(structured_output=payload, text=None, success=True)


_VULN = {"ID": "XSS-VULN-01", "vulnerability_type": "Stored",
         "externally_exploitable": True, "confidence": "high",
         "merge_source": "llm-only", "title": "t", "severity": "high"}


async def test_writes_agent_text_poc_verbatim(tmp_path, monkeypatch):
    """happy path：agent 产 curl/raw_http/steps/self_check 文本 → 写回新 schema。"""
    d = _wb(tmp_path)
    _write_queue(d, [dict(_VULN)])
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, d, tmp_path))
    payload = {"pocs": [{
        "vulnerability_id": "XSS-VULN-01",
        "curl": "curl -i 'http://TARGET/memos'",
        "raw_http": "GET /memos HTTP/1.1\nHost: TARGET",
        "steps": ["plant via POST /memos", "reviewer opens /memos"],
        "preconditions": "需登录", "expected_response": "alert 触发",
        "self_check": "pass", "notes": "n"}]}
    with patch.object(activities, "run_gitnexus_verdict_agent",
                      return_value=_agent_result(payload)) as mock_agent:
        written = await activities._write_agent_pocs(_FakeInput(tmp_path), d)
    assert written == ["XSS-VULN-01"]
    poc = _read_queue(d)["vulnerabilities"][0]["report_poc"]
    assert poc["curl"].startswith("curl -i")          # 原文透传
    assert poc["raw_http"].startswith("GET /memos")
    assert poc["steps"] == ["plant via POST /memos", "reviewer opens /memos"]
    assert poc["self_check"] == "pass"
    assert "request" not in poc                        # 旧确定性 schema 不再产出
    # agent 调用契约：poc-agent-{vc} 命名 + prompt 渲染
    kwargs = mock_agent.call_args.kwargs
    assert kwargs["agent_name"] == "poc-agent-xss"
    assert "XSS-VULN-01" in kwargs["prompt"]           # queue 内容进 prompt
    assert "{{VULN_QUEUE}}" not in kwargs["prompt"]    # 占位符已替换
    assert "{{WEB_URL}}" not in kwargs["prompt"]


async def test_prompt_uses_web_url_variable(tmp_path, monkeypatch):
    """web_url 有值时 prompt 目标 host 用真值。"""
    d = _wb(tmp_path)
    _write_queue(d, [dict(_VULN)])
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, d, tmp_path))
    with patch.object(activities, "run_gitnexus_verdict_agent",
                      return_value=_agent_result({"pocs": []})) as mock_agent:
        await activities._write_agent_pocs(
            _FakeInput(tmp_path, web_url="https://prod.example.com"), d)
    assert "https://prod.example.com" in mock_agent.call_args.kwargs["prompt"]


async def test_agent_failure_leaves_queue_untouched(tmp_path, monkeypatch):
    """诚实缺失：agent 抛异常/返回空 → 不写回（queue 原样）、不抛。"""
    d = _wb(tmp_path)
    _write_queue(d, [dict(_VULN)])
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, d, tmp_path))
    for failure in (RuntimeError("agent down"),
                    _agent_result(None),                # structured_output None
                    _agent_result({})):                 # 无 pocs 键
        with patch.object(activities, "run_gitnexus_verdict_agent",
                          side_effect=failure
                          if isinstance(failure, Exception) else None,
                          return_value=None
                          if isinstance(failure, Exception) else failure):
            written = await activities._write_agent_pocs(_FakeInput(tmp_path), d)
        assert written == []
    assert not _read_queue(d)["vulnerabilities"][0].get("report_poc")


async def test_phantom_and_duplicate_rejected(tmp_path, monkeypatch):
    """validate 集成：幻觉 id / 重复 id 不写回，合法卡照写。"""
    d = _wb(tmp_path)
    _write_queue(d, [dict(_VULN), {**_VULN, "ID": "XSS-VULN-02"}])
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, d, tmp_path))
    payload = {"pocs": [
        {"vulnerability_id": "XSS-VULN-01", "curl": "curl -i 'http://TARGET/a'",
         "self_check": "pass"},
        {"vulnerability_id": "PHANTOM", "curl": "curl -i 'http://TARGET/b'",
         "self_check": "pass"},
        {"vulnerability_id": "XSS-VULN-01", "curl": "curl -i 'http://TARGET/c'",
         "self_check": "pass"},
    ]}
    with patch.object(activities, "run_gitnexus_verdict_agent",
                      return_value=_agent_result(payload)):
        written = await activities._write_agent_pocs(_FakeInput(tmp_path), d)
    assert written == ["XSS-VULN-01"]
    vulns = _read_queue(d)["vulnerabilities"]
    assert vulns[0]["report_poc"]["curl"].endswith("/a'")   # 首份生效
    assert not vulns[1].get("report_poc")                    # 未覆盖的卡缺失（None 键=缺失，语义等价）


async def test_skips_cards_with_existing_report_poc(tmp_path, monkeypatch):
    """写回即 checkpoint：已有 report_poc 的卡不重打（prompt queue 不含它）。"""
    d = _wb(tmp_path)
    done = {**_VULN, "report_poc": {"curl": "curl -i 'http://TARGET/old'",
                                    "self_check": "pass"}}
    _write_queue(d, [done, {**_VULN, "ID": "XSS-VULN-02"}])
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, d, tmp_path))
    with patch.object(activities, "run_gitnexus_verdict_agent",
                      return_value=_agent_result({"pocs": [
                          {"vulnerability_id": "XSS-VULN-02",
                           "curl": "curl -i 'http://TARGET/new'",
                           "self_check": "pass"}]})) as mock_agent:
        written = await activities._write_agent_pocs(_FakeInput(tmp_path), d)
    assert written == ["XSS-VULN-02"]
    assert "XSS-VULN-01" not in mock_agent.call_args.kwargs["prompt"]
    vulns = _read_queue(d)["vulnerabilities"]
    assert vulns[0]["report_poc"]["curl"].endswith("/old'")  # 不覆写
    assert vulns[1]["report_poc"]["curl"].endswith("/new'")


async def test_only_ids_reflow_filters(tmp_path, monkeypatch):
    """only_ids 回炉：只处理指定卡（且仍要求无 report_poc）。"""
    d = _wb(tmp_path)
    _write_queue(d, [dict(_VULN), {**_VULN, "ID": "XSS-VULN-02"}])
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, d, tmp_path))
    with patch.object(activities, "run_gitnexus_verdict_agent",
                      return_value=_agent_result({"pocs": [
                          {"vulnerability_id": "XSS-VULN-02",
                           "curl": "curl -i 'http://TARGET/x'",
                           "self_check": "pass"}]})) as mock_agent:
        written = await activities._write_agent_pocs(
            _FakeInput(tmp_path), d, only_ids={"XSS-VULN-02"})
    assert written == ["XSS-VULN-02"]
    assert "XSS-VULN-01" not in mock_agent.call_args.kwargs["prompt"]
    assert not _read_queue(d)["vulnerabilities"][0].get("report_poc")


async def test_text_fallback_parses_json(tmp_path, monkeypatch):
    """structured_output 缺失时 text 兜底（json.loads，对齐 enrichment 模式）。"""
    d = _wb(tmp_path)
    _write_queue(d, [dict(_VULN)])
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, d, tmp_path))
    text = json.dumps({"pocs": [{"vulnerability_id": "XSS-VULN-01",
                                 "curl": "curl -i 'http://TARGET/t'",
                                 "self_check": "pass"}]})
    with patch.object(activities, "run_gitnexus_verdict_agent",
                      return_value=SimpleNamespace(structured_output=None,
                                                   text=text, success=True)):
        written = await activities._write_agent_pocs(_FakeInput(tmp_path), d)
    assert written == ["XSS-VULN-01"]
    assert _read_queue(d)["vulnerabilities"][0]["report_poc"]["curl"].endswith("/t'")
