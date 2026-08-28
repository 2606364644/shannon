"""chain_verdict 逐链 checkpoint（2026-08-28 事故修）。

run_gitnexus_chain_verdict activity 超时重试 / resume 从头重跑全部链
（2026-08-27 NodeGoat：27+31 条链每条被判定 ~5 遍）。checkpoint 逐链落盘，
重跑只补未判链。护栏（用户 2026-08-28）：缓存命中零 LLM 调用（严格只减
消耗）；损坏/畸形 checkpoint 一律当 miss（绝不异常循环）。
"""

import json

import pytest

from supernova_core.code_index.chain_verdict import (
    CandidateChain,
    ChainVerdict,
    gather_verdicts_concurrently,
)
from supernova_core.code_index.verdict_checkpoint import (
    VerdictCheckpoint,
    chain_fingerprint,
)


def _cand(i: int, vc: str = "xss", sink: str | None = None) -> CandidateChain:
    return CandidateChain(
        vuln_class=vc, flow_id=f"flow#{i}", entry_point_id="app.py:handler:1",
        source_param=f"p{i}", source_type="query_param",
        sink_call_site_id=sink or f"sink:{i}", sink_slot="generic",
        propagation_steps=[], sanitizer_annotations=[],
        direction_hint="backward", post_sanitize_concat=False,
    )


def _verdict(tag: str, verdict: str = "vulnerable") -> ChainVerdict:
    return ChainVerdict(
        verdict=verdict, witness_payload="w",
        evidence_chain=f"p -> sink ({tag})", mismatch_reason=None,
        confidence="high", title=f"t-{tag}",
    )


def _agent(results: dict, calls: list):
    """fake verdict_agent：按链序号返回注入的 verdict，记录调用。"""
    async def agent(prompt, *, output_format=None, agent_name=None):
        i = int(prompt.split("source: p")[1].split()[0])
        calls.append(i)
        v = results[i]
        from types import SimpleNamespace
        return SimpleNamespace(structured_output={
            "verdict": v.verdict, "confidence": v.confidence,
            "evidence_chain": v.evidence_chain, "title": v.title,
        }, text="")
    return agent


# ---------- 指纹 ----------

def test_fingerprint_stable_and_discriminating():
    """同链同指纹（含字段顺序无关）；source/sink/flow 变化 → 指纹变化。"""
    assert chain_fingerprint(_cand(1)) == chain_fingerprint(_cand(1))
    assert chain_fingerprint(_cand(1)) != chain_fingerprint(_cand(2))
    assert chain_fingerprint(_cand(1)) != chain_fingerprint(
        _cand(1, sink="sink:other"))
    assert chain_fingerprint(_cand(1)) != chain_fingerprint(_cand(1, vc="injection"))


# ---------- 存取往返 / 容错 ----------

def test_checkpoint_put_get_roundtrip(tmp_path):
    path = tmp_path / "chain_verdict_checkpoint_xss.json"
    store = VerdictCheckpoint.load(path)
    store.put(_cand(1), _verdict("one"))
    store.put(_cand(2), _verdict("two", verdict="not_vulnerable"))

    reloaded = VerdictCheckpoint.load(path)
    assert reloaded.get(_cand(1)) == _verdict("one")
    assert reloaded.get(_cand(2)) == _verdict("two", verdict="not_vulnerable")
    assert reloaded.get(_cand(3)) is None


def test_checkpoint_load_corrupt_file_returns_empty(tmp_path):
    """损坏 JSON → 空 store（当 miss 重判），不抛异常。"""
    path = tmp_path / "chain_verdict_checkpoint_xss.json"
    path.write_text("{not json", encoding="utf-8")
    store = VerdictCheckpoint.load(path)
    assert store.get(_cand(1)) is None


def test_checkpoint_load_malformed_entry_treated_as_miss(tmp_path):
    """条目 value 不是合法 verdict dict（缺字段/类型错）→ get 返 None 不抛。"""
    path = tmp_path / "chain_verdict_checkpoint_xss.json"
    key = chain_fingerprint(_cand(1))
    path.write_text(json.dumps({key: {"verdict": "vulnerable"}}),
                    encoding="utf-8")   # 缺 evidence_chain 等必填
    store = VerdictCheckpoint.load(path)
    assert store.get(_cand(1)) is None


def test_checkpoint_put_write_failure_does_not_raise(tmp_path):
    """落盘失败（只读目录等）→ put 不抛（判定流程不受阻，退化为无 checkpoint）。"""
    store = VerdictCheckpoint.load(tmp_path / "ro" / "ckpt.json")
    store.put(_cand(1), _verdict("one"))   # 不应抛
    assert store.get(_cand(1)) == _verdict("one")   # 内存仍在（本进程内有效）


# ---------- gather 集成 ----------

@pytest.mark.asyncio
async def test_gather_second_run_zero_llm_calls(tmp_path):
    """二跑同 checkpoint：全部命中缓存 → agent 零调用，verdict 逐位相等且保序。"""
    path = tmp_path / "chain_verdict_checkpoint_xss.json"
    results = {i: _verdict(f"{i:02d}") for i in range(1, 4)}
    calls1: list = []
    v1 = await gather_verdicts_concurrently(
        [_cand(i) for i in range(1, 4)], vc="xss",
        verdict_agent=_agent(results, calls1),
        max_agents=100, concurrency=2,
        checkpoint=VerdictCheckpoint.load(path))
    assert sorted(calls1) == [1, 2, 3]

    calls2: list = []
    v2 = await gather_verdicts_concurrently(
        [_cand(i) for i in range(1, 4)], vc="xss",
        verdict_agent=_agent(results, calls2),
        max_agents=100, concurrency=2,
        checkpoint=VerdictCheckpoint.load(path))
    assert calls2 == []                    # 缓存命中零 LLM 调用
    assert v2 == v1
    assert [v.title for v in v2] == ["t-01", "t-02", "t-03"]


@pytest.mark.asyncio
async def test_gather_partial_checkpoint_only_judges_missing(tmp_path):
    """预置 2/3 条 → 只补判缺失链（agent 恰调 1 次），缓存链原值返回。"""
    path = tmp_path / "chain_verdict_checkpoint_xss.json"
    seed = VerdictCheckpoint.load(path)
    seed.put(_cand(1), _verdict("cached-1", verdict="not_vulnerable"))
    seed.put(_cand(2), _verdict("cached-2"))

    results = {i: _verdict(f"fresh-{i}") for i in range(1, 4)}
    calls: list = []
    verdicts = await gather_verdicts_concurrently(
        [_cand(i) for i in range(1, 4)], vc="xss",
        verdict_agent=_agent(results, calls),
        max_agents=100, concurrency=3,
        checkpoint=VerdictCheckpoint.load(path))
    assert calls == [3]                    # 只判了链 3
    assert verdicts[0].title == "t-cached-1"
    assert verdicts[1].title == "t-cached-2"
    assert verdicts[2].title == "t-fresh-3"


@pytest.mark.asyncio
async def test_gather_unadjudicated_not_checkpointed(tmp_path):
    """超预算链的 unadjudicated 不落盘 → 重跑（预算放开后）会真判它。"""
    path = tmp_path / "chain_verdict_checkpoint_xss.json"
    results = {i: _verdict(f"{i:02d}") for i in (1, 3)}
    calls: list = []
    await gather_verdicts_concurrently(
        [_cand(1), _cand(2), _cand(3)], vc="xss",
        verdict_agent=_agent(results, calls),
        max_agents=2, concurrency=3,       # 链 3 超预算 → unadjudicated
        checkpoint=VerdictCheckpoint.load(path))
    assert sorted(calls) == [1, 2]

    store = VerdictCheckpoint.load(path)
    assert store.get(_cand(1)) is not None
    assert store.get(_cand(3)) is None     # unadjudicated 不入账

    calls2: list = []
    await gather_verdicts_concurrently(
        [_cand(1), _cand(2), _cand(3)], vc="xss",
        verdict_agent=_agent(results, calls2),
        max_agents=10, concurrency=3,      # 预算放开
        checkpoint=VerdictCheckpoint.load(path))
    assert calls2 == [3]                   # 链 1/2 缓存命中，链 3 真判


@pytest.mark.asyncio
async def test_gather_cached_chains_still_tick_progress(tmp_path):
    """缓存命中也走 tick（done 到 N/N，进度不缺格）。"""
    from supernova_core.code_index.progress import ProgressEmitter

    path = tmp_path / "chain_verdict_checkpoint_xss.json"
    seed = VerdictCheckpoint.load(path)
    seed.put(_cand(1), _verdict("cached-1"))

    ticks: list = []

    async def cb(sample):
        ticks.append(sample)

    results = {i: _verdict(f"fresh-{i}") for i in (2, 3)}
    verdicts = await gather_verdicts_concurrently(
        [_cand(i) for i in range(1, 4)], vc="xss",
        verdict_agent=_agent(results, []),
        max_agents=100, concurrency=3,
        emitter=ProgressEmitter("chain-verdict", 3, cb),
        checkpoint=VerdictCheckpoint.load(path))
    assert len(verdicts) == 3
    # tick 全量计数（含缓存命中链 1）：3 次 tick、末次 done=3
    assert len(ticks) == 3
    assert ticks[-1].done == 3
