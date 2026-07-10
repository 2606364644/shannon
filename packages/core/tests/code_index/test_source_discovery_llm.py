import asyncio
from unittest.mock import MagicMock

from shannon_core.code_index.models import FuncBlock, ParameterSource
from shannon_core.code_index.source_discovery_llm import (
    collect_source_candidates, discover_sources_by_rules, discover_sources_llm,
)


def _block(file_path, func_name, start_line, source, language="typescript"):
    return FuncBlock(
        id=f"{file_path}:{func_name}:{start_line}", file_path=file_path,
        function_name=func_name, start_line=start_line, end_line=start_line + 5,
        source_code=source, parameters=[], language=language,
    )


def test_collect_candidates_returns_entry_handlers_without_rule_hit():
    # 用了一个非常规取用(input.get("x"))→ 规则未命中,但仍作为候选送 LLM
    src = 'function f(req){ const x = input.get("x"); }\n'
    block = _block("f.js", "f", 1, src)
    cands = collect_source_candidates([block], {block.id},
                                      source_provider=lambda b: block.source_code.encode())
    assert len(cands) == 1
    assert cands[0].block.id == block.id


def test_discover_sources_llm_soft_source_on_llm_verdict():
    block = _block("f.js", "f", 1, 'function f(req){ const x = input.get("x"); }\n')
    cands = collect_source_candidates([block], {block.id},
                                      source_provider=lambda b: block.source_code.encode())
    async def fake_llm(prompt):
        return ('[{"field":"x","source_type":"query","is_source":true,"rationale":"r"}]')
    out = asyncio.run(discover_sources_llm(cands, fake_llm))
    soft, gaps = out
    assert len(soft) == 1
    assert soft[0].param_name == "x"
    assert soft[0].rule_id == "llm-discovered-source"
    assert soft[0].needs_review is True


def test_discover_sources_llm_degrades_to_empty_when_llm_unavailable():
    block = _block("f.js", "f", 1, 'function f(req){ const x = input.get("x"); }\n')
    cands = collect_source_candidates([block], {block.id},
                                      source_provider=lambda b: block.source_code.encode())
    out = asyncio.run(discover_sources_llm(cands, None))  # LLM 不可用
    soft, gaps = out
    assert soft == []
    assert gaps == []


def test_discover_sources_llm_reports_progress_and_hits():
    """progress_cb: 每个 handler 一次 tick(命中带 detail) + 末尾 finalize 汇总。"""
    from shannon_core.code_index.progress import ProgressSample

    # 两个 handler(不同 block), 各判一个 source。
    b1 = _block("f.js", "f", 1, 'function f(req){ const x = input.get("x"); }\n')
    b2 = _block("g.js", "g", 1, 'function g(req){ const y = input.get("y"); }\n')
    cands = (collect_source_candidates([b1, b2], {b1.id, b2.id},
             source_provider=lambda b: b.source_code.encode()))

    async def fake_llm(prompt):
        if "f.js" in prompt:
            return ('[{"field":"x","source_type":"query","is_source":true,"rationale":"r"}]')
        return ('[{"field":"y","source_type":"body","is_source":true,"rationale":"r"}]')

    samples: list[ProgressSample] = []

    async def cb(s: ProgressSample):
        samples.append(s)

    out = asyncio.run(discover_sources_llm(cands, fake_llm, progress_cb=cb))
    soft, _gaps = out
    assert len(soft) == 2  # 两个 source

    # 至少一条 tick 带 hit detail(命中行)。
    hit_ticks = [s for s in samples if not s.final and s.detail]
    assert hit_ticks, f"no hit-detail tick emitted: {samples}"
    assert "param" in hit_ticks[0].detail or "source=" in hit_ticks[0].detail

    # 最后一条是 finalize 汇总, done == 唯一 function 数。
    assert samples[-1].final is True
    assert samples[-1].done == len({c.block.id for c in cands})


def test_discover_sources_llm_skip_emits_note_via_progress_cb():
    """per-handler 超时 → emitter.note 经 progress_cb 上报(走 dispatcher, 非裸 warning)。

    on_skip 注入: 超时 handler 名经 idx 映射进 note detail。
    """
    from shannon_core.code_index.progress import ProgressSample

    b1 = _block("f.js", "f", 1, 'function f(req){ const x = input.get("x"); }\n')
    b2 = _block("g.js", "g", 1, 'function g(req){ const y = input.get("y"); }\n')
    cands = collect_source_candidates([b1, b2], {b1.id, b2.id},
                                      source_provider=lambda b: b.source_code.encode())

    async def fake_llm(prompt):
        if "function f" in prompt:  # f 的 source code → 挂死超时
            await asyncio.sleep(10)
        return '[]'

    samples: list[ProgressSample] = []

    async def cb(s):
        samples.append(s)

    asyncio.run(discover_sources_llm(cands, fake_llm, progress_cb=cb,
                                     concurrency=2, per_call_timeout=0.2))

    notes = [s for s in samples if s.note]
    assert notes, f"超时应经 note 上报: {samples}"
    assert "timed out" in notes[0].note
    assert "f" in notes[0].note  # block.function_name 经 idx 映射


def test_discover_sources_llm_progress_cb_none_ok():
    """progress_cb=None 全程 no-op, 返回正常。"""
    block = _block("f.js", "f", 1, 'function f(req){ const x = input.get("x"); }\n')
    cands = collect_source_candidates([block], {block.id},
                                      source_provider=lambda b: block.source_code.encode())

    async def fake_llm(prompt):
        return "[]"  # 无 source

    out = asyncio.run(discover_sources_llm(cands, fake_llm, progress_cb=None))
    soft, _gaps = out
    assert soft == []


# ===== spec 2026-07-10: source 补召回(对含 sink 函数)=====


def test_discover_sources_by_rules_matches_dot_access_in_sink_func():
    """含 sink 函数(非 entry_point)的点号取用 req.body.preTax → 规则命中产 SourcePoint。

    回归锚点(spec §2 根因):NodeGoat ContributionsHandler(含 eval sink)不在
    entry_point,source_detector 漏扫;source 补召回对含 sink 函数跑规则 → 补回
    req.body.preTax。
    """
    src = 'function handler(req){ eval(req.body.preTax); }\n'
    block = _block("contributions.js", "handler", 1, src)
    out = discover_sources_by_rules(
        [block], {block.id},  # 含 sink 函数 id(handler 不在 entry_point)
        source_provider=lambda b: block.source_code.encode())
    assert len(out) == 1
    assert out[0].param_name == "preTax"
    assert out[0].rule_id == "ts-express-body"
    assert out[0].source_type == ParameterSource.BODY_FIELD
    assert out[0].entry_point_id == block.id  # anchor 到含 sink 函数本身


def test_discover_sources_by_rules_skips_non_sink_func():
    """不含 sink 的函数不扫 —— source 补召回被 sink 驱动,只兜底含 sink 函数(spec §6 决策1)。"""
    block = _block("other.js", "helper", 1,
                   'function helper(req){ return req.body.x; }\n')
    out = discover_sources_by_rules(
        [block], set(),  # sink_func_ids 空 → 不扫
        source_provider=lambda b: block.source_code.encode())
    assert out == []


def test_collect_source_candidates_picks_destructure_in_sink_func():
    """含 sink 函数的解构取用(const {a,b} = req.body,规则不命中)→ 候选送 LLM(spec §3.1)。"""
    src = 'function handler(req){ const {a, b} = req.body; eval(a); }\n'
    block = _block("h.js", "handler", 1, src)
    cands = collect_source_candidates([block], {block.id},
                                      source_provider=lambda b: block.source_code.encode())
    assert len(cands) == 1
    assert cands[0].block.id == block.id


def test_collect_source_candidates_skips_rule_hit_dot_access():
    """点号取用(req.body.x)已被规则命中 → 不送 LLM(规则路径覆盖,不重复补)。"""
    src = 'function handler(req){ eval(req.body.x); }\n'
    block = _block("h.js", "handler", 1, src)
    cands = collect_source_candidates([block], {block.id},
                                      source_provider=lambda b: block.source_code.encode())
    assert cands == []  # 规则命中 → 不候选


def test_collect_source_candidates_skips_non_sink_func():
    """不含 sink 的函数不候选(source 补召回被 sink 驱动,只兜底含 sink 函数)。"""
    src = 'function helper(req){ const {a} = req.body; return a; }\n'
    block = _block("h.js", "helper", 1, src)
    cands = collect_source_candidates([block], set(),  # sink_func_ids 空
                                      source_provider=lambda b: block.source_code.encode())
    assert cands == []


def test_discover_sources_llm_soft_source_on_destructure():
    """规则未命中的解构(const {a,b}=req.body)→ LLM 判 → 软 SourcePoint
    (rule_id=llm-discovered-source, needs_review=True, entry_point_id=含 sink 函数)
    + SourceGap 产出(spec §3.1 LLM 补召回 + source_gap_report)。"""
    src = 'function handler(req){ const {a, b} = req.body; eval(a); }\n'
    block = _block("h.js", "handler", 1, src)
    cands = collect_source_candidates([block], {block.id},
                                      source_provider=lambda b: block.source_code.encode())
    assert cands  # 解构进候选

    async def fake_llm(prompt):
        return ('[{"field":"a","source_type":"body","expression":"req.body","line":1,'
                '"is_source":true,"rationale":"r"}]')

    soft, gaps = asyncio.run(discover_sources_llm(cands, fake_llm))
    assert len(soft) == 1
    assert soft[0].param_name == "a"
    assert soft[0].rule_id == "llm-discovered-source"
    assert soft[0].needs_review is True
    assert soft[0].entry_point_id == block.id
    assert len(gaps) >= 1  # source_gap_report 聚合产出


def test_discover_sources_llm_degrades_when_unavailable():
    """LLM 不可用 → 只规则(解构漏),返回 ([], []),不抛(spec §3.1 降级)。"""
    block = _block("h.js", "handler", 1,
                   'function handler(req){ const {a} = req.body; eval(a); }\n')
    cands = collect_source_candidates([block], {block.id},
                                      source_provider=lambda b: block.source_code.encode())
    soft, gaps = asyncio.run(discover_sources_llm(cands, None))
    assert soft == []
    assert gaps == []
