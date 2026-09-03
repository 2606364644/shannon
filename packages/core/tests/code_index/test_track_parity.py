"""双轨呈现一致性编排（spec 2026-08-26 §6）：配对归并。

enhance_track_parity 是 merge activity 内的编排入口（确定性 merge 之后调用）：
llm-only × gitnexus-only 残余卡每 class 一次 LLM 批量配对（仅 high 应用）。
merge 内逐卡轻量补全已随 SUPERNOVA_GN_ENRICH_MODE 档位开关（light 档）于
2026-08-31 整键移除而删——GN-only 卡叙事/评级由独立深度富化 step（whitebox
run_gn_finding_enrichment，多轮读码）承担。
LLM 不可用（raise/超时）优雅退化：维持确定性 merge 结果，不阻塞报告。
"""
import json

import pytest

from supernova_core.code_index.dual_track_merger import merge_dual_track_queues
from supernova_core.models.queue_schemas import InjectionVulnerability
from supernova_core.services.track_parity import enhance_track_parity


def _llm_card(ID="INJ-VULN-01", **kw):
    return InjectionVulnerability(
        ID=ID, vulnerability_type="CommandInjection",
        externally_exploitable=True, confidence="medium",
        verdict="vulnerable", title="命令注入：preTax 直接进入 eval()",
        affected_parameters=["preTax"], **kw)


def _gn_card(ID="INJ-GN-01", **kw):
    return InjectionVulnerability(
        ID=ID, vulnerability_type="CommandInjection",
        externally_exploitable=True, confidence="low",
        verdict="vulnerable",
        source="preTax (app/routes/contributions.js:ContributionsHandler:7)",
        sink_call="app/routes/contributions.js:ContributionsHandler:eval:32:23",
        evidence_chain="preTax -> app/routes/contributions.js:eval:32",
        **kw)


def _merged():
    llm = merge_dual_track_queues(
        [_llm_card(endpoint="GET /other", sink_function="eval elsewhere")],
        [], mode="verdict")
    gn = merge_dual_track_queues(
        [], [_gn_card()], mode="verdict")
    return llm + gn


# --- 编排：enhance_track_parity（配对-only）---

class _StubClient:
    """按调用序返回预设响应的 stub llm_client。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[str] = []

    async def __call__(self, prompt, **kw):
        self.calls.append(prompt)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.mark.asyncio
async def test_pairing_high_confidence_consumes_gn_card():
    """配对 high → GN 卡并入 LLM 卡（both）。merge 内只此一次 LLM 调用。"""
    pairing = json.dumps({"pairs": [
        {"gn_id": "INJ-GN-01", "llm_id": "INJ-VULN-01",
         "confidence": "high", "reason": "same eval sink"}]})
    client = _StubClient([pairing])
    out = await enhance_track_parity(_merged(), client)
    assert len(out) == 1 and out[0].merge_source == "both"
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_pairing_low_confidence_keeps_cards_independent():
    """配对 low（不合并）→ 两卡保持独立且字段原样（merge 内无逐卡补全——
    叙事留给独立深度富化 step，2026-08-31 light 档移除后不再原地补全）。"""
    pairing = json.dumps({"pairs": [
        {"gn_id": "INJ-GN-01", "llm_id": "INJ-VULN-01",
         "confidence": "low"}]})
    client = _StubClient([pairing])
    out = await enhance_track_parity(_merged(), client)
    assert len(out) == 2  # 两张卡保持独立
    gn = next(f for f in out if f.merge_source == "gitnexus-only")
    assert gn.title is None  # 未被补全改写
    assert len(client.calls) == 1  # 仅配对一次，无后续调用


@pytest.mark.asyncio
async def test_llm_failure_degrades_to_deterministic_merge():
    """LLM raise（不可用档）→ 原样返回确定性 merge 结果，不抛。"""
    client = _StubClient([RuntimeError("llm down")])
    merged = _merged()
    out = await enhance_track_parity(merged, client)
    assert [f.ID for f in out] == [f.ID for f in merged]
    assert all(f.merge_source != "both" for f in out)


@pytest.mark.asyncio
async def test_no_cross_track_cards_skips_llm_entirely():
    """单侧空 → 零 LLM 调用（成本守门）。"""
    merged = merge_dual_track_queues([_llm_card()], [], mode="verdict")
    client = _StubClient([])
    out = await enhance_track_parity(merged, client)
    assert client.calls == [] and len(out) == 1


# --- spec 2026-08-26 §5.1 ①归并终审：attach 挂靠形态（merged_from）---

@pytest.mark.asyncio
async def test_attach_mode_attaches_gn_id_via_merged_from():
    """新形态 mode=attach：LLM 卡为主体挂靠 GN ID（merged_from），GN 卡移除，
    主体卡 merge_source/confidence 不变（呈现层归并，不冒充 both）。"""
    pairing = json.dumps({"merge": [
        {"llm_id": "INJ-VULN-01", "gn_id": "INJ-GN-01",
         "mode": "attach", "confidence": "high",
         "reason": "same eval hole, sink named at different granularity"}]})
    client = _StubClient([pairing])
    out = await enhance_track_parity(_merged(), client)
    assert len(out) == 1
    card = out[0]
    assert card.ID == "INJ-VULN-01"
    assert card.merged_from == ["INJ-GN-01"]
    assert card.merge_source == "llm-only"
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_mixed_merge_and_attach_modes_via_enhance():
    """一批配对同时含 merge 与 attach：merge 成 both、attach 挂靠 merged_from。"""
    llm2 = _llm_card(ID="INJ-VULN-02", endpoint="GET /research",
                     sink_function="eval elsewhere2")
    gn2 = _gn_card(ID="INJ-GN-02")
    merged = _merged() + merge_dual_track_queues([llm2], [], mode="verdict") \
        + merge_dual_track_queues([], [gn2], mode="verdict")
    pairing = json.dumps({"merge": [
        {"llm_id": "INJ-VULN-01", "gn_id": "INJ-GN-01",
         "mode": "merge", "confidence": "high"},
        {"llm_id": "INJ-VULN-02", "gn_id": "INJ-GN-02",
         "mode": "attach", "confidence": "high"}]})
    client = _StubClient([pairing])
    out = await enhance_track_parity(merged, client)
    by_id = {f.ID: f for f in out}
    assert by_id["INJ-VULN-01"].merge_source == "both"
    assert by_id["INJ-VULN-02"].merge_source == "llm-only"
    assert by_id["INJ-VULN-02"].merged_from == ["INJ-GN-02"]
    assert set(by_id) == {"INJ-VULN-01", "INJ-VULN-02"}


@pytest.mark.asyncio
async def test_attach_mode_garbage_response_falls_back_deterministic():
    """attach 时代 LLM 输出不可解析 → 回退确定性 key 配对结果（现行为不变）。"""
    client = _StubClient(["garbage not json"])
    merged = _merged()
    out = await enhance_track_parity(merged, client)
    assert [f.ID for f in out] == [f.ID for f in merged]
    assert all(f.merged_from is None for f in out)


# --- spec 2026-09-03 §3 F5：0 产出三态观测（无对 / 全中低置信 / 解析失败可区分）---

@pytest.mark.asyncio
async def test_track_parity_zero_pairs_logged(caplog):
    """LLM 返回 0 对（空 merge 列表）→ 显式 WARNING（区分「无对」与「全中低置信」）。"""
    import logging
    client = _StubClient(['{"merge": []}'])
    with caplog.at_level(logging.WARNING, logger="supernova_core.services.track_parity"):
        out = await enhance_track_parity(_merged(), client)
    assert len(out) == 2  # 无配对应用，两卡原样
    assert any("track-parity" in r.getMessage() and "0" in r.getMessage()
               for r in caplog.records)


@pytest.mark.asyncio
async def test_track_parity_all_below_high_logged(caplog):
    """有对但全 <high 置信（无一对应用）→ 显式 WARNING。"""
    import logging
    pairing = json.dumps({"merge": [
        {"gn_id": "INJ-GN-01", "llm_id": "INJ-VULN-01",
         "mode": "merge", "confidence": "medium"}]})
    client = _StubClient([pairing])
    with caplog.at_level(logging.WARNING, logger="supernova_core.services.track_parity"):
        out = await enhance_track_parity(_merged(), client)
    assert len(out) == 2  # medium 不应用
    assert any("track-parity" in r.getMessage() and "<high>" in r.getMessage()
               for r in caplog.records)
