import asyncio
from unittest.mock import MagicMock

from shannon_core.code_index.models import FuncBlock, ParameterSource
from shannon_core.code_index.source_discovery_llm import (
    collect_source_candidates, discover_sources_llm,
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
    assert len(out) == 1
    assert out[0].param_name == "x"
    assert out[0].rule_id == "llm-discovered"
    assert out[0].needs_review is True


def test_discover_sources_llm_degrades_to_empty_when_llm_unavailable():
    block = _block("f.js", "f", 1, 'function f(req){ const x = input.get("x"); }\n')
    cands = collect_source_candidates([block], {block.id},
                                      source_provider=lambda b: block.source_code.encode())
    out = asyncio.run(discover_sources_llm(cands, None))  # LLM 不可用
    assert out == []


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
    assert len(out) == 2  # 两个 source

    # 至少一条 tick 带 hit detail(命中行)。
    hit_ticks = [s for s in samples if not s.final and s.detail]
    assert hit_ticks, f"no hit-detail tick emitted: {samples}"
    assert "param" in hit_ticks[0].detail or "source=" in hit_ticks[0].detail

    # 最后一条是 finalize 汇总, done == 唯一 function 数。
    assert samples[-1].final is True
    assert samples[-1].done == len({c.block.id for c in cands})


def test_discover_sources_llm_progress_cb_none_ok():
    """progress_cb=None 全程 no-op, 返回正常。"""
    block = _block("f.js", "f", 1, 'function f(req){ const x = input.get("x"); }\n')
    cands = collect_source_candidates([block], {block.id},
                                      source_provider=lambda b: block.source_code.encode())

    async def fake_llm(prompt):
        return "[]"  # 无 source

    out = asyncio.run(discover_sources_llm(cands, fake_llm, progress_cb=None))
    assert out == []
