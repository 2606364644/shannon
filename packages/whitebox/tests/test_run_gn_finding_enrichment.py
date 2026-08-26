# packages/whitebox/tests/test_run_gn_finding_enrichment.py
"""GN-only 深度富化 activity 测试（spec 2026-08-26 §6.2 deep 档）。

覆盖：mode 门控（deep 才跑）/ GN-only 过滤（llm-only、both 不进候选）/
按 ID 回填（白名单字段，原值空或降级占位才写）/ 保护字段不覆写 /
agent 失败降级不阻塞 / 输出不可解析宽容跳过。
"""
import json
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

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
    """track_step / log_* 全部 no-op（富化 activity 只用 track_step）。"""

    @asynccontextmanager
    async def track_step(self, phase, name, intent=None):
        yield


def _wb(tmp_path):
    d = tmp_path / "deliverables" / "whitebox"
    (d / "intermediate").mkdir(parents=True, exist_ok=True)
    return d


def _write_queue(tmp_path, vulns):
    _wb(tmp_path).joinpath("intermediate", "xss_exploitation_queue.json").write_text(
        json.dumps({"vulnerabilities": vulns})
    )


_GN_VULN = {
    "ID": "XSS-GN-01",
    "vulnerability_type": "Reflected",
    "externally_exploitable": True,
    "confidence": "low",
    "title": "xss：preTax → app/routes/contributions.js:ContributionsHandler:render:21:19",
    "source": "preTax (app/routes/contributions.js:ContributionsHandler:7)",
    "sink_call": "app/routes/contributions.js:ContributionsHandler:render:21:19",
    "sink_function": "render",
    "path": "preTax -> app/routes/contributions.js:ContributionsHandler:render:21:19 (llm-pass-failed, needs_review)",
    "render_context": "HTML_BODY",
    "verdict": "vulnerable",
    "merge_source": "gitnexus-only",
    "source_track": "gitnexus",
    "evidence_chain": "preTax -> render (llm-pass-failed, needs_review)",
    "mismatch_reason": "llm chain-verdict pass failed; needs human/LLM-track review",
    "flow_id": "contributions:7->render:21:19",
    "impact": None,
    "remediation": None,
    "severity": None,
    "witness_payload": None,
}

_LLM_VULN = {
    "ID": "XSS-VULN-01",
    "vulnerability_type": "Stored",
    "externally_exploitable": True,
    "confidence": "high",
    "title": "存储型 XSS：POST /memos",
    "merge_source": "llm-only",
    "source_track": None,
    "impact": "已有危害叙事",
}


class _Result:
    def __init__(self, so=None, text=None):
        self.structured_output = so
        self.text = text


async def _run(tmp_path, mode="deep", agent_result=None, agent_exc=None):
    """跑 run_gn_finding_enrichment；agent mock 控制富化 agent 行为。

    get_audit_session/ensure_audit_session 是 activity 函数内 import（对齐
    run_authz_gitnexus_judge），patch 到 session_registry 层。"""
    import supernova_whitebox.audit.session_registry as session_registry

    async def fake_agent(**kw):
        if agent_exc is not None:
            raise agent_exc
        return agent_result or _Result()

    async def noop_ensure(input):
        return None

    with patch.object(activities, "_get_paths",
                      lambda i: (tmp_path, _wb(tmp_path), tmp_path)), \
         patch.object(activities, "gn_enrich_mode", lambda: mode), \
         patch.object(activities, "run_gitnexus_verdict_agent", fake_agent), \
         patch.object(session_registry, "get_audit_session",
                      lambda: _RecordingSession()), \
         patch.object(activities, "ensure_audit_session", noop_ensure):
        return await activities.run_gn_finding_enrichment(_FakeInput(tmp_path))


@pytest.mark.asyncio
async def test_enrichment_skipped_when_mode_not_deep(tmp_path):
    """off/light 档直接跳过——agent 不被调用，queue 原样。"""
    _write_queue(tmp_path, [dict(_GN_VULN)])
    result = await _run(tmp_path, mode="light",
                        agent_exc=AssertionError("light 档不应调 agent"))
    assert result["skipped"] == "light"
    assert result["total_enriched"] == 0


@pytest.mark.asyncio
async def test_enrichment_agent_name_per_class(tmp_path):
    """deep 档：verdict agent 记账唯一名带 vuln_class——逐 class 调用防
    metrics.agents 同名条目互相覆盖（totals 累加、agents 覆盖）。"""
    import supernova_whitebox.audit.session_registry as session_registry

    captured: list[dict] = []

    async def fake_agent(**kw):
        captured.append(kw)
        return _Result()

    async def noop_ensure(input):
        return None

    _write_queue(tmp_path, [dict(_GN_VULN)])
    with patch.object(activities, "_get_paths",
                      lambda i: (tmp_path, _wb(tmp_path), tmp_path)), \
         patch.object(activities, "gn_enrich_mode", lambda: "deep"), \
         patch.object(activities, "run_gitnexus_verdict_agent", fake_agent), \
         patch.object(session_registry, "get_audit_session",
                      lambda: _RecordingSession()), \
         patch.object(activities, "ensure_audit_session", noop_ensure):
        await activities.run_gn_finding_enrichment(_FakeInput(tmp_path))

    assert [c.get("agent_name") for c in captured] == ["gn-enrich-xss"]


@pytest.mark.asyncio
async def test_enrichment_backfills_gn_only_by_id(tmp_path):
    """deep 档：GN-only 卡按 ID 回填富化字段（叙事/评级/数据流/PoC），
    降级占位 mismatch_reason 被替换；llm-only 卡不动。"""
    result_so = {
        "vulnerabilities": [{
            "ID": "XSS-GN-01",
            "title": "反射型 XSS：POST /contributions 的 preTax 未转义进入模板渲染",
            "impact": "攻击者可在受害者浏览器执行任意脚本。",
            "remediation": "render 前对 preTax 做 HTML 实体编码。",
            "severity": "high",
            "cwe_id": "CWE-79",
            "witness_payload": "<img src=x onerror=alert(1)>",
            "mismatch_reason": "swig autoescape:false 全局关闭，无编码。",
            "dataflow_steps": [{"label": "reads preTax",
                                "file": "app/routes/contributions.js",
                                "line": 7, "protection": None}],
            "externally_exploitable": False,  # 保护字段，应被忽略
            "verdict": "safe",               # 保护字段，应被忽略
        }]
    }
    _write_queue(tmp_path, [dict(_GN_VULN), dict(_LLM_VULN)])
    result = await _run(tmp_path, agent_result=_Result(so=result_so))
    assert result["total_enriched"] == 1

    out = json.loads((_wb(tmp_path) / "intermediate" /
                      "xss_exploitation_queue.json").read_text())
    gn = next(v for v in out["vulnerabilities"] if v["ID"] == "XSS-GN-01")
    assert gn["impact"] == "攻击者可在受害者浏览器执行任意脚本。"
    assert gn["severity"] == "high"
    assert gn["cwe_id"] == "CWE-79"
    assert gn["witness_payload"] == "<img src=x onerror=alert(1)>"
    assert gn["mismatch_reason"].startswith("swig autoescape")  # 占位句被替换
    assert gn["dataflow_steps"][0]["file"] == "app/routes/contributions.js"
    assert gn["title"].startswith("反射型 XSS")  # fallback title 被富化 title 替换
    # 保护字段不覆写（externally_exploitable/verdict/flow_id/merge_source）
    assert gn["externally_exploitable"] is True
    assert gn["verdict"] == "vulnerable"
    assert gn["flow_id"] == "contributions:7->render:21:19"
    assert gn["merge_source"] == "gitnexus-only"
    # llm-only 卡不动
    llm = next(v for v in out["vulnerabilities"] if v["ID"] == "XSS-VULN-01")
    assert llm["impact"] == "已有危害叙事"


@pytest.mark.asyncio
async def test_enrichment_bare_array_root_backfills(tmp_path):
    """agent 产裸数组根（structured_output=[{...}]，NodeGoat 2026-08-26 实翻车
    形态：两次 "output not a JSON object" 整轮报废）：包装成
    {"vulnerabilities":[...]} 走既有白名单回填。"""
    result_so = [{
        "ID": "XSS-GN-01",
        "impact": "攻击者可在受害者浏览器执行任意脚本。",
        "witness_payload": "<img src=x onerror=alert(1)>",
    }]
    _write_queue(tmp_path, [dict(_GN_VULN)])
    result = await _run(tmp_path, agent_result=_Result(so=result_so))
    assert result["total_enriched"] == 1

    out = json.loads((_wb(tmp_path) / "intermediate" /
                      "xss_exploitation_queue.json").read_text())
    gn = next(v for v in out["vulnerabilities"] if v["ID"] == "XSS-GN-01")
    assert gn["impact"] == "攻击者可在受害者浏览器执行任意脚本。"
    assert gn["witness_payload"] == "<img src=x onerror=alert(1)>"


@pytest.mark.asyncio
async def test_enrichment_agent_failure_keeps_deterministic_fields(tmp_path):
    """agent 失败：降级不阻塞——queue 保持确定性字段原样，返回 failed 标记。"""
    _write_queue(tmp_path, [dict(_GN_VULN)])
    result = await _run(tmp_path, agent_exc=RuntimeError("LLM unavailable"))
    assert result["enriched_classes"]["xss"]["enriched"] == 0
    assert "failed" in result["enriched_classes"]["xss"]

    out = json.loads((_wb(tmp_path) / "intermediate" /
                      "xss_exploitation_queue.json").read_text())
    assert out["vulnerabilities"][0]["impact"] is None  # 原样


@pytest.mark.asyncio
async def test_enrichment_unparseable_output_no_crash(tmp_path):
    """输出非 JSON 对象：宽容跳过（0 回填），不抛异常。"""
    _write_queue(tmp_path, [dict(_GN_VULN)])
    result = await _run(tmp_path, agent_result=_Result(text="garbage not json"))
    assert result["total_enriched"] == 0


@pytest.mark.asyncio
async def test_enrichment_no_gn_only_skips_agent(tmp_path):
    """无 GN-only 条目：agent 不被调用（零候选零成本）。"""
    _write_queue(tmp_path, [dict(_LLM_VULN)])
    result = await _run(tmp_path, agent_exc=AssertionError("无候选不应调 agent"))
    assert result["total_enriched"] == 0
    assert "xss" not in result["enriched_classes"]


def test_apply_gn_enrichment_failure_warnings_include_raw_shape():
    """翻车 warning 带 raw 实际形态（type + 前 200 字符）——NodeGoat 2026-08-26
    调查只能靠推断（agents log 不记最终输出文本），下次翻车应一眼定位产出形态。"""
    # 非 str/dict/list 根（如 int）
    enriched, warnings = activities._apply_gn_enrichment([], 42)
    assert enriched == 0
    assert len(warnings) == 1
    assert "int" in warnings[0] and "42" in warnings[0]

    # str 但不可解析
    _, warnings = activities._apply_gn_enrichment([], "前置叙述无 JSON")
    assert len(warnings) == 1
    assert "str" in warnings[0] and "前置叙述无 JSON" in warnings[0]

    # dict 但无 vulnerabilities 数组
    _, warnings = activities._apply_gn_enrichment([], {"result": "done"})
    assert len(warnings) == 1
    assert "dict" in warnings[0] and "result" in warnings[0]

    # 长产出截断到 200 字符（防 warning 刷屏）
    _, warnings = activities._apply_gn_enrichment([], "x" * 500)
    assert len(warnings[0]) < 300


def test_worker_registers_gn_finding_enrichment():
    """worker activities 列表含 run_gn_finding_enrichment（4 处注册同步之一）。"""
    from supernova_whitebox import worker

    assert worker.run_gn_finding_enrichment is activities.run_gn_finding_enrichment


def test_workflow_orders_merge_before_enrichment_before_render():
    """源码顺序：merge < gn-finding-enrichment < dataflow view / render_findings
    （富化必须吃 merge 后 SSOT、先于渲染落盘）。"""
    src = activities.__dict__["run_gn_finding_enrichment"].__module__
    wf_path = None
    from pathlib import Path
    import supernova_whitebox.pipeline.workflows as wf_mod
    wf_path = Path(wf_mod.__file__).read_text()
    assert wf_path.find("run_merge_dual_track_queues") < \
           wf_path.find("run_gn_finding_enrichment"), "富化必须在 merge 之后"
    assert wf_path.find("run_gn_finding_enrichment") < \
           wf_path.find("render_findings"), "富化必须在渲染之前"
    assert src  # noqa: F841 — 仅为可读性保留模块名引用
