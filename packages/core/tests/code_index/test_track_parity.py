"""双轨呈现一致性编排（spec 2026-08-26 §6）：配对归并 + GN-only 卡补全。

enhance_track_parity 是 merge activity 内的编排入口（确定性 merge 之后调用）：
1. llm-only × gitnexus-only 残余卡每 class 一次 LLM 批量配对（仅 high 应用）；
2. 配对后仍 gitnexus-only 的卡逐卡轻量补全（title/notes/impact/remediation/
   cvss/owasp_category/severity 校准，写 BaseVulnerability 现成字段）。
LLM 不可用（raise/超时）优雅退化：维持确定性 merge 结果，不阻塞报告。
"""
import json

import pytest

from supernova_core.code_index.dual_track_merger import merge_dual_track_queues
from supernova_core.models.queue_schemas import InjectionVulnerability
from supernova_core.services.track_parity import (
    apply_completion,
    build_completion_prompt,
    enhance_track_parity,
    parse_completion_response,
)


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


COMPLETION_JSON = json.dumps({
    "title": "命令注入：POST /contributions 的 preTax 未校验进入 eval()（RCE）",
    "notes": "请求体 preTax 在 handleContributionsUpdate 解构后直接传入 eval 求值，全程无类型校验或沙箱。",
    "impact": "攻击者可执行任意 Node.js 代码，接管服务器。",
    "remediation": "将 eval 替换为 Number(preTax) 并校验类型。",
    "severity": "critical",
    "cvss": None,
    "owasp_category": "A03:2021-Injection",
})


# --- 纯函数：补全 prompt / 解析 / 应用 ---

def test_build_completion_prompt_contains_gn_material():
    prompt = build_completion_prompt(_gn_card(), "injection")
    assert "preTax" in prompt
    assert "app/routes/contributions.js" in prompt
    assert "injection" in prompt.lower() or "注入" in prompt


def test_parse_completion_response_valid_and_lenient():
    fields = parse_completion_response(f"```json\n{COMPLETION_JSON}\n```")
    assert fields is not None
    assert fields["title"].startswith("命令注入")
    assert fields["severity"] == "critical"
    # cvss=None → 不写入集（键剔除，不编造）


def test_parse_completion_response_garbage_returns_none():
    assert parse_completion_response("no json") is None
    assert parse_completion_response("") is None
    # title 缺失的最小集不采（补全的核心价值就是叙事标题）
    assert parse_completion_response(json.dumps({"severity": "low"})) is None


def test_apply_completion_fills_fields_without_overwrite_of_cvss():
    fields = parse_completion_response(COMPLETION_JSON)
    out = apply_completion(_gn_card(), fields)
    assert out.title.startswith("命令注入")
    assert out.impact == "攻击者可执行任意 Node.js 代码，接管服务器。"
    assert out.remediation.startswith("将 eval 替换")
    assert out.severity == "critical"
    assert out.cvss is None  # None 不写（不编造）


# --- 编排：enhance_track_parity ---

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
    """配对 high → GN 卡并入 LLM 卡（both），无剩余 gn-only → 不调补全。"""
    pairing = json.dumps({"pairs": [
        {"gn_id": "INJ-GN-01", "llm_id": "INJ-VULN-01",
         "confidence": "high", "reason": "same eval sink"}]})
    client = _StubClient([pairing])
    out = await enhance_track_parity(_merged(), "injection", client)
    assert len(out) == 1 and out[0].merge_source == "both"
    assert len(client.calls) == 1  # 配对一次，无补全调用


@pytest.mark.asyncio
async def test_completion_applies_to_remaining_gn_only():
    """配对 low（不合并）→ 剩余 gn-only 卡逐卡补全，字段写入。"""
    pairing = json.dumps({"pairs": [
        {"gn_id": "INJ-GN-01", "llm_id": "INJ-VULN-01",
         "confidence": "low"}]})
    client = _StubClient([pairing, COMPLETION_JSON])
    out = await enhance_track_parity(_merged(), "injection", client)
    assert len(out) == 2  # 两张卡保持独立
    gn = next(f for f in out if f.merge_source == "gitnexus-only")
    assert gn.title.startswith("命令注入")
    assert gn.impact is not None
    assert len(client.calls) == 2  # 配对 1 + 补全 1


@pytest.mark.asyncio
async def test_llm_failure_degrades_to_deterministic_merge():
    """LLM raise（不可用档）→ 原样返回确定性 merge 结果，不抛。"""
    client = _StubClient([RuntimeError("llm down")])
    merged = _merged()
    out = await enhance_track_parity(merged, "injection", client)
    assert [f.ID for f in out] == [f.ID for f in merged]
    assert all(f.merge_source != "both" for f in out)


@pytest.mark.asyncio
async def test_completion_garbage_keeps_card_unchanged():
    """补全输出不可解析 → 该卡保持确定性文案，流程继续。"""
    pairing = json.dumps({"pairs": []})
    client = _StubClient([pairing, "garbage not json"])
    out = await enhance_track_parity(_merged(), "injection", client)
    gn = next(f for f in out if f.merge_source == "gitnexus-only")
    assert gn.title is None  # 未被补全污染


@pytest.mark.asyncio
async def test_no_cross_track_cards_skips_llm_entirely():
    """单侧空 → 零 LLM 调用（成本守门）。"""
    merged = merge_dual_track_queues([_llm_card()], [], mode="verdict")
    client = _StubClient([])
    out = await enhance_track_parity(merged, "injection", client)
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
    out = await enhance_track_parity(_merged(), "injection", client)
    assert len(out) == 1
    card = out[0]
    assert card.ID == "INJ-VULN-01"
    assert card.merged_from == ["INJ-GN-01"]
    assert card.merge_source == "llm-only"
    assert len(client.calls) == 1   # GN 卡已挂靠移除 → 无剩余 gn-only，不补全


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
    out = await enhance_track_parity(merged, "injection", client)
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
    out = await enhance_track_parity(merged, "injection", client,
                                     complete=False)
    assert [f.ID for f in out] == [f.ID for f in merged]
    assert all(f.merged_from is None for f in out)
