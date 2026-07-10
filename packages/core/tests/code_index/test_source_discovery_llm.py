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
    """文件级: 不同文件 chunk, 一个超时 → emitter.note 经 progress_cb 上报(走 dispatcher)。

    on_skip 注入: 超时 chunk 的 file_path 经 idx 映射进 note detail(文件级)。
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
    assert "f.js" in notes[0].note  # file_path 经 idx 映射(文件级)


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


# ===== spec 2026-07-10: source 补召回 per-function → 文件级聚合 + chunking =====


async def test_discover_sources_file_level_groups_same_file_into_one_call():
    """同文件多候选函数 → 1 次 LLM 调用(文件级聚合, spec §3.1)。"""
    b1 = _block("app.js", "f", 1, 'function f(req){ const {a}=req.body; eval(a); }\n')
    b2 = _block("app.js", "g", 10, 'function g(req){ const {b}=req.body; eval(b); }\n')
    cands = collect_source_candidates([b1, b2], {b1.id, b2.id},
                                      source_provider=lambda b: b.source_code.encode())

    n_calls = 0

    async def fake_llm(prompt):
        nonlocal n_calls
        n_calls += 1
        return '[]'

    await discover_sources_llm(cands, fake_llm)
    assert n_calls == 1, f"同文件应 1 次调用, 实际 {n_calls}"


async def test_discover_sources_file_level_prompt_lists_all_functions():
    """文件级 prompt 含该文件所有候选函数源码。"""
    b1 = _block("app.js", "get_user", 1,
                'function get_user(req){ const {a}=req.body; eval(a); }\n')
    b2 = _block("app.js", "del_user", 10,
                'function del_user(req){ const {b}=req.body; eval(b); }\n')
    cands = collect_source_candidates([b1, b2], {b1.id, b2.id},
                                      source_provider=lambda b: b.source_code.encode())
    seen: list[str] = []

    async def fake_llm(prompt):
        seen.append(prompt)
        return '[]'

    await discover_sources_llm(cands, fake_llm)
    assert len(seen) == 1
    assert "get_user" in seen[0] and "del_user" in seen[0]


async def test_discover_sources_file_level_routes_verdict_to_correct_block():
    """关键(spec §3.1): 文件级 verdict.line(文件绝对行)反查所属 block → SourcePoint
    归位到正确函数。b1=f 覆盖 [1,6]; b2=g 覆盖 [10,15]; verdict line=12 落 g →
    entry_point_id=g 的 block。
    """
    b1 = _block("svc.js", "f", 1, 'function f(req){ const {a}=req.body; eval(a); }\n')
    b2 = _block("svc.js", "g", 10, 'function g(req){ const {b}=req.body; eval(b); }\n')
    cands = collect_source_candidates([b1, b2], {b1.id, b2.id},
                                      source_provider=lambda b: b.source_code.encode())

    async def fake_llm(prompt):
        return ('[{"field":"b","source_type":"body","expression":"req.body","line":12,'
                '"is_source":true,"rationale":"r"}]')

    soft, _ = await discover_sources_llm(cands, fake_llm)
    assert len(soft) == 1
    assert soft[0].entry_point_id == b2.id  # line 12 ∈ [10,15] → g
    assert soft[0].line == 12


async def test_discover_sources_per_call_timeout_defaults_to_120(monkeypatch):
    """文件级 prompt 更重 → discover_sources_llm 不传 per_call_timeout 时默认 120s(spec §3.2)。"""
    from shannon_core.code_index import source_discovery_llm as mod

    captured: list = []

    async def fake_map(items, fn, *, concurrency, per_call_timeout, label, on_skip):
        captured.append(per_call_timeout)
        return []

    monkeypatch.setattr(mod, "map_llm_with_bounds", fake_map)
    b = _block("h.js", "f", 1, 'function f(req){ const {a}=req.body; eval(a); }\n')
    cands = collect_source_candidates([b], {b.id},
                                      source_provider=lambda x: b.source_code.encode())

    async def dummy(prompt):
        return "[]"

    await discover_sources_llm(cands, dummy)
    assert captured[-1] == 120.0, f"默认应 120s, 实际 {captured[-1]}"

    await discover_sources_llm(cands, dummy, per_call_timeout=5)
    assert captured[-1] == 5, f"显式传值应优先, 实际 {captured[-1]}"


async def test_discover_sources_large_file_chunks_into_multiple_calls():
    """大文件(源码 token 超 token_threshold)→ 按函数拆 chunk → 多次调用(防爆 context)。"""
    big = 'function big(req){ const {a}=req.body;\n' + "  a;\n" * 200 + '}\n'  # ~300 tokens
    b1 = _block("big.js", "A", 1, big)
    b2 = _block("big.js", "B", 1, big)
    cands = collect_source_candidates([b1, b2], {b1.id, b2.id},
                                      source_provider=lambda b: b.source_code.encode())

    n_calls = 0

    async def fake_llm(prompt):
        nonlocal n_calls
        n_calls += 1
        return '[]'

    await discover_sources_llm(cands, fake_llm, token_threshold=100)
    assert n_calls == 2, f"大文件应按函数拆 2 chunk(2 次调用), 实际 {n_calls}"


async def test_discover_sources_per_call_timeout_honors_env_override(monkeypatch):
    """spec §3.2: SHANNON_LLM_PER_CALL_TIMEOUT env 须能覆盖 source 的 per-call 上限。

    文件级默认 120s(下限), 但运营设 env=200 必须生效 —— 不能被硬编码 120 绕过
    (旧版 effective_timeout 恒 120 → env 失效, 违反 spec §3.2「均可经 env 覆盖」)。
    """
    from shannon_core.code_index import source_discovery_llm as mod

    monkeypatch.setenv("SHANNON_LLM_PER_CALL_TIMEOUT", "200")

    captured: list = []

    async def fake_map(items, fn, *, concurrency, per_call_timeout, label, on_skip):
        captured.append(per_call_timeout)
        return []

    monkeypatch.setattr(mod, "map_llm_with_bounds", fake_map)
    b = _block("h.js", "f", 1, 'function f(req){ const {a}=req.body; eval(a); }\n')
    cands = collect_source_candidates([b], {b.id},
                                      source_provider=lambda x: b.source_code.encode())

    async def dummy(prompt):
        return "[]"

    await discover_sources_llm(cands, dummy)
    assert captured[-1] == 200.0, f"env=200 应生效, 实际 {captured[-1]}"


async def test_discover_sources_skips_malformed_field_keeps_other_sources():
    """文件级回归锚点: 一条 source field malformed(line=null)只跳过该 source,
    不丢整文件 chunk。

    _to_soft_source 的 int(field.get("line")) 在 line=null 时 int(None) 崩; 文件级
    聚合后若无 per-item 防护, 整 chunk 被 map 标 _Skip → 该文件所有 source 丢
    (含已 valid 的)。spec §3.1 verdict 归位须容错。
    """
    b1 = _block("svc.js", "f", 1, 'function f(req){ const {a}=req.body; eval(a); }\n')
    b2 = _block("svc.js", "g", 10, 'function g(req){ const {b}=req.body; eval(b); }\n')
    cands = collect_source_candidates([b1, b2], {b1.id, b2.id},
                                      source_provider=lambda x: x.source_code.encode())

    async def fake_llm(prompt):
        # good line=1 正常; bad line=null → int(None) 旧版崩
        return ('[{"field":"good","source_type":"body","expression":"req.body","line":1,'
                '"is_source":true,"rationale":"g"},'
                '{"field":"bad","source_type":"body","expression":"req.body","line":null,'
                '"is_source":true,"rationale":"b"}]')

    soft, _ = await discover_sources_llm(cands, fake_llm)
    assert any(s.param_name == "good" for s in soft), \
        f"malformed line 不应丢整 chunk, good 应保留: {soft}"
