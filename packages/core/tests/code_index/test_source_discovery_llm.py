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
    """T4: progress_cb 接进来后,per-function tick + 末尾 finalize 均上报(对称 sink)。

    构造 1 个候选函数,LLM 判 1 个字段为 source;断言:
      - 至少一条 sample 带非空 detail(命中 tick)
      - 末尾 sample.final=True 且汇总文案含 source 计数
      - done == 去重函数数(1)
      - 软 source 真产出
    """
    from shannon_core.code_index.progress import ProgressSample

    block = _block("f.js", "f", 1, 'function f(req){ const x = input.get("x"); }\n')
    cands = collect_source_candidates([block], {block.id},
                                      source_provider=lambda b: block.source_code.encode())
    samples: list[ProgressSample] = []

    async def fake_llm(prompt):
        return ('[{"field":"x","source_type":"query","is_source":true,"rationale":"r"}]')

    async def cb(s: ProgressSample):
        samples.append(s)

    out = asyncio.run(discover_sources_llm(cands, fake_llm, progress_cb=cb))
    # 至少 1 个命中 tick(detail 非 None)
    assert any(s.detail for s in samples)
    # 末尾是 finalize 汇总
    assert samples[-1].final is True
    assert "sources" in (samples[-1].detail or "")
    # done = 去重函数数
    assert samples[-1].done == len({c.block.id for c in cands})
    # 软 source 真产出
    assert len(out) == 1
    assert out[0].param_name == "x"


def test_discover_sources_llm_progress_cb_none_ok():
    """T4: progress_cb=None 时全程 no-op,功能不回归(返回空 source)。"""
    block = _block("f.js", "f", 1, 'function f(req){ const x = input.get("x"); }\n')
    cands = collect_source_candidates([block], {block.id},
                                      source_provider=lambda b: block.source_code.encode())

    async def fake_llm(prompt):
        return "[]"  # LLM 判无 source

    out = asyncio.run(discover_sources_llm(cands, fake_llm, progress_cb=None))
    assert out == []
