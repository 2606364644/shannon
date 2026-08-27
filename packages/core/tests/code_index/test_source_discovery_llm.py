import asyncio
from unittest.mock import MagicMock

from supernova_core.code_index.models import FuncBlock, ParameterSource
from supernova_core.code_index.source_discovery_llm import (
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
    async def fake_llm(prompt, **kwargs):
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
    from supernova_core.code_index.progress import ProgressSample

    # 两个 handler(不同 block), 各判一个 source。
    b1 = _block("f.js", "f", 1, 'function f(req){ const x = input.get("x"); }\n')
    b2 = _block("g.js", "g", 1, 'function g(req){ const y = input.get("y"); }\n')
    cands = (collect_source_candidates([b1, b2], {b1.id, b2.id},
             source_provider=lambda b: b.source_code.encode()))

    async def fake_llm(prompt, **kwargs):
        if "f.js" in prompt:
            return ('[{"field":"x","source_type":"query","is_source":true,"rationale":"r"}]')
        return ('[{"field":"y","source_type":"body","is_source":true,"rationale":"r"}]')

    samples: list[ProgressSample] = []

    async def cb(s: ProgressSample):
        samples.append(s)

    out = asyncio.run(discover_sources_llm(cands, fake_llm, progress_cb=cb, max_calls=1))
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
    from supernova_core.code_index.progress import ProgressSample

    b1 = _block("f.js", "f", 1, 'function f(req){ const x = input.get("x"); }\n')
    b2 = _block("g.js", "g", 1, 'function g(req){ const y = input.get("y"); }\n')
    cands = collect_source_candidates([b1, b2], {b1.id, b2.id},
                                      source_provider=lambda b: b.source_code.encode())

    async def fake_llm(prompt, **kwargs):
        if "function f" in prompt:  # f 的 source code → 挂死超时
            await asyncio.sleep(10)
        return '[]'

    samples: list[ProgressSample] = []

    async def cb(s):
        samples.append(s)

    asyncio.run(discover_sources_llm(cands, fake_llm, progress_cb=cb,
                                     concurrency=2, per_call_timeout=0.2, max_calls=1))

    notes = [s for s in samples if s.note]
    assert notes, f"超时应经 note 上报: {samples}"
    assert "timed out" in notes[0].note
    assert "f.js" in notes[0].note  # file_path 经 idx 映射(文件级)


def test_discover_sources_llm_progress_cb_none_ok():
    """progress_cb=None 全程 no-op, 返回正常。"""
    block = _block("f.js", "f", 1, 'function f(req){ const x = input.get("x"); }\n')
    cands = collect_source_candidates([block], {block.id},
                                      source_provider=lambda b: block.source_code.encode())

    async def fake_llm(prompt, **kwargs):
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

    async def fake_llm(prompt, **kwargs):
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

    async def fake_llm(prompt, **kwargs):
        nonlocal n_calls
        n_calls += 1
        return '[]'

    await discover_sources_llm(cands, fake_llm)
    assert n_calls == 1, f"同文件应 1 次调用, 实际 {n_calls}"


async def test_discover_sources_cross_file_merges_into_one_call():
    """跨文件贪心合并(spec 2026-07-10 §3 模块1): 两个小文件, 默认 max_calls → 1 次调用,
    prompt 的 {file_paths} 含两文件 join(如 "a.js, b.js")。
    """
    b1 = _block("a.js", "f", 1, 'function f(req){ const {a}=req.body; eval(a); }\n')
    b2 = _block("b.js", "g", 1, 'function g(req){ const {b}=req.body; eval(b); }\n')
    cands = collect_source_candidates([b1, b2], {b1.id, b2.id},
                                      source_provider=lambda b: b.source_code.encode())
    seen: list[str] = []

    async def fake_llm(prompt, **kwargs):
        seen.append(prompt)
        return '[]'

    await discover_sources_llm(cands, fake_llm)
    assert len(seen) == 1, f"跨文件应合并 1 次, 实际 {len(seen)}"
    assert "a.js, b.js" in seen[0]  # {file_paths} join 多文件


async def test_discover_sources_max_calls_cap_separates_files():
    """max_calls 上限(spec §3 模块2): max_calls=1 → 两文件(各 1 call)拆成 2 次调用。"""
    b1 = _block("a.js", "f", 1, 'function f(req){ const {a}=req.body; eval(a); }\n')
    b2 = _block("b.js", "g", 1, 'function g(req){ const {b}=req.body; eval(b); }\n')
    cands = collect_source_candidates([b1, b2], {b1.id, b2.id},
                                      source_provider=lambda b: b.source_code.encode())
    n_calls = 0

    async def fake_llm(prompt, **kwargs):
        nonlocal n_calls
        n_calls += 1
        return '[]'

    await discover_sources_llm(cands, fake_llm, max_calls=1)
    assert n_calls == 2, f"max_calls=1 应拆 2 次, 实际 {n_calls}"


async def test_discover_sources_file_level_prompt_lists_all_functions():
    """文件级 prompt 含该文件所有候选函数源码。"""
    b1 = _block("app.js", "get_user", 1,
                'function get_user(req){ const {a}=req.body; eval(a); }\n')
    b2 = _block("app.js", "del_user", 10,
                'function del_user(req){ const {b}=req.body; eval(b); }\n')
    cands = collect_source_candidates([b1, b2], {b1.id, b2.id},
                                      source_provider=lambda b: b.source_code.encode())
    seen: list[str] = []

    async def fake_llm(prompt, **kwargs):
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

    async def fake_llm(prompt, **kwargs):
        return ('[{"field":"b","source_type":"body","expression":"req.body","line":12,'
                '"is_source":true,"rationale":"r"}]')

    soft, _ = await discover_sources_llm(cands, fake_llm)
    assert len(soft) == 1
    assert soft[0].entry_point_id == b2.id  # line 12 ∈ [10,15] → g
    assert soft[0].line == 12


async def test_discover_sources_per_call_timeout_defaults_to_120(monkeypatch):
    """文件级 prompt 更重 → discover_sources_llm 不传 per_call_timeout 时默认 120s(spec §3.2)。"""
    from supernova_core.code_index import source_discovery_llm as mod

    captured: list = []

    async def fake_map(items, fn, *, concurrency, per_call_timeout, label, on_skip):
        captured.append(per_call_timeout)
        return []

    monkeypatch.setattr(mod, "map_llm_with_bounds", fake_map)
    b = _block("h.js", "f", 1, 'function f(req){ const {a}=req.body; eval(a); }\n')
    cands = collect_source_candidates([b], {b.id},
                                      source_provider=lambda x: b.source_code.encode())

    async def dummy(prompt, **kwargs):
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

    async def fake_llm(prompt, **kwargs):
        nonlocal n_calls
        n_calls += 1
        return '[]'

    await discover_sources_llm(cands, fake_llm, token_threshold=100)
    assert n_calls == 2, f"大文件应按函数拆 2 chunk(2 次调用), 实际 {n_calls}"


async def test_discover_sources_per_call_timeout_honors_env_override(monkeypatch):
    """spec §3.2: SUPERNOVA_LLM_PER_CALL_TIMEOUT env 须能覆盖 source 的 per-call 上限。

    文件级默认 120s(下限), 但运营设 env=200 必须生效 —— 不能被硬编码 120 绕过
    (旧版 effective_timeout 恒 120 → env 失效, 违反 spec §3.2「均可经 env 覆盖」)。
    """
    from supernova_core.code_index import source_discovery_llm as mod

    monkeypatch.setenv("SUPERNOVA_LLM_PER_CALL_TIMEOUT", "200")

    captured: list = []

    async def fake_map(items, fn, *, concurrency, per_call_timeout, label, on_skip):
        captured.append(per_call_timeout)
        return []

    monkeypatch.setattr(mod, "map_llm_with_bounds", fake_map)
    b = _block("h.js", "f", 1, 'function f(req){ const {a}=req.body; eval(a); }\n')
    cands = collect_source_candidates([b], {b.id},
                                      source_provider=lambda x: b.source_code.encode())

    async def dummy(prompt, **kwargs):
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

    async def fake_llm(prompt, **kwargs):
        # good line=1 正常; bad line=null → int(None) 旧版崩
        return ('[{"field":"good","source_type":"body","expression":"req.body","line":1,'
                '"is_source":true,"rationale":"g"},'
                '{"field":"bad","source_type":"body","expression":"req.body","line":null,'
                '"is_source":true,"rationale":"b"}]')

    soft, _ = await discover_sources_llm(cands, fake_llm)
    assert any(s.param_name == "good" for s in soft), \
        f"malformed line 不应丢整 chunk, good 应保留: {soft}"


def test_discover_sources_threshold_derives_from_model():
    """model='glm-5.2' -> token_threshold 派生 750K, 大函数进 1 chunk(spec §3 模块3)。

    glm-5.2 context 1M × 0.75 = 750K; big_src ~125K tokens < 750K -> 1 chunk(1 次 LLM 调用)。
    """
    import asyncio
    from supernova_core.code_index.source_discovery_llm import SourceCandidate

    big_src = "x = 1\n" * 100_000  # ~500K chars -> ~125K tokens(ascii)
    block = _block("app.js", "f", 1, big_src)
    cands = [SourceCandidate(block=block)]
    calls = []

    async def fake_llm(prompt, **kwargs):
        calls.append(prompt)
        return "[]"  # 空 verdict

    soft, gaps = asyncio.run(discover_sources_llm(cands, fake_llm, model="glm-5.2"))
    assert len(calls) == 1  # 整个大函数进 1 chunk(750K 容得下 125K)
    assert soft == [] and gaps == []


def test_discover_sources_threshold_default_model():
    """model=None -> 走默认 128K context -> threshold 96K。

    big_src ~125K tokens > 96K, 但单 block 超 threshold 独立成 1 chunk(无法再拆)。
    """
    import asyncio
    from supernova_core.code_index.source_discovery_llm import SourceCandidate

    big_src = "x = 1\n" * 100_000  # ~125K tokens
    block = _block("app.js", "f", 1, big_src)
    cands = [SourceCandidate(block=block)]
    calls = []

    async def fake_llm(prompt, **kwargs):
        calls.append(prompt)
        return "[]"

    soft, gaps = asyncio.run(discover_sources_llm(cands, fake_llm, model=None))
    assert len(calls) == 1  # 单 block 超阈值独立成 chunk


# ===== Koa(ctx.*)候选 hint + entry handler 范围(trip 等Koa+Sequelize项目治本)=====


def test_collect_source_candidates_picks_koa_query_destructure():
    """Koa 解构 const {x} = ctx.query → 候选送 LLM(点号规则不命中解构)。

    改动3(a)锚点:hint 补 ctx\\.(?:request\\.)?(?:body|query|params|headers) 前,ctx.query
    (不经 ctx.request)不被旧 ctx\\.Request 命中 → 解构 source 丢失(根因:trip 104/141
    controller 用 ctx.* 但解构写法规则漏扫)。
    """
    src = 'function handler(ctx){ const {x} = ctx.query; eval(x); }\n'
    block = _block("trip.ts", "handler", 1, src)
    cands = collect_source_candidates([block], {block.id},
                                      source_provider=lambda b: block.source_code.encode())
    assert len(cands) == 1
    assert cands[0].block.id == block.id


def test_collect_source_candidates_picks_koa_params_direct_pass():
    """Koa 对象直传 svc.findOne(ctx.params) → 候选(点号规则不命中直传)。"""
    src = 'function handler(ctx){ return svc.findOne(ctx.params); }\n'
    block = _block("trip.ts", "handler", 1, src)
    cands = collect_source_candidates([block], {block.id},
                                      source_provider=lambda b: block.source_code.encode())
    assert len(cands) == 1


def test_collect_source_candidates_picks_entry_handler_via_entry_point_ids():
    """分层架构:controller(entry handler,有 source 无 sink)不在 sink_func_ids,经
    entry_point_ids 纳入候选范围 → controller 的解构 source 能 LLM 补召回。

    改动3(b)锚点:候选范围只扫 sink_func_ids 时,controller(source 无 sink)进不来;
    扩到 entry_point_ids 后 controller 解构进候选(trip controller→service 分层)。
    """
    # controller:解构 ctx.query,无 sink(调 service.findOne,非 sink)→ 不在 sink_func_ids
    ctrl = _block(
        "trip.ts", "getUser", 1,
        'function getUser(ctx){ const {id} = ctx.query; return svc.findOne(id); }\n')
    cands = collect_source_candidates(
        [ctrl], sink_func_ids=set(),  # controller 不含 sink
        source_provider=lambda b: ctrl.source_code.encode(),
        entry_point_ids={ctrl.id},
    )
    assert len(cands) == 1
    assert cands[0].block.id == ctrl.id


def test_discover_sources_by_rules_scans_entry_handler_dot_access():
    """entry handler 的点号取用 ctx.query.userId → 经 entry_point_ids 纳入规则路径产 source。

    改动3(b):discover_sources_by_rules 范围扩到 entry handler(controller 点号 source 兜底;
    与主路径 detect_sources 重叠部分由 _dedup_source_points 吸收)。
    """
    src = 'function getUser(ctx){ const x = ctx.query.userId; return x; }\n'
    block = _block("trip.ts", "getUser", 1, src)
    out = discover_sources_by_rules(
        [block], sink_func_ids=set(),  # 不含 sink
        source_provider=lambda b: block.source_code.encode(),
        entry_point_ids={block.id},
    )
    assert len(out) == 1
    assert out[0].param_name == "userId"
    assert out[0].rule_id == "ts-koa-query-direct"
    assert out[0].entry_point_id == block.id


def test_collect_source_candidates_entry_point_ids_none_backward_compatible():
    """不传 entry_point_ids(默认 None)→ 只扫 sink_func_ids,行为不变(向后兼容)。"""
    ctrl = _block("trip.ts", "getUser", 1,
                  'function getUser(ctx){ const {id} = ctx.query; return id; }\n')
    cands = collect_source_candidates(
        [ctrl], sink_func_ids=set(),  # 空,且不传 entry_point_ids
        source_provider=lambda b: ctrl.source_code.encode(),
    )
    assert cands == []  # 不在任何范围 → 不候选


# ===== spec 子项②: source 候选 hint 加 IDOR 风味(对象级实体 id)=====


def test_collect_source_candidates_catches_idor_flavor_java_get_parameter():
    """IDOR 风味: Java servlet `request.getParameter("userId")` 是对象级实体 id 取用。

    行为演进(deepsec §2.4 吸收后):`request.getParameter(...)` 现被确定性规则
    `j-httpservlet-getparameter` 识别为 query source → block 整体不再进候选收集
    (`_has_rule_hit` 拦截)。这是更精确的覆盖 —— 实体 id 取用已是确定性 source,
    无需送 LLM 候选。本测试改为断言确定性命中 + 候选不再重复送 LLM。

    IDOR flavor「不漏」由 `test_collect_source_candidates_catches_idor_flavor_generic_get_param`
    (getParam("resourceId") 无规则命中)继续锚定候选路径。
    """
    src = (
        'public User getUser(HttpServletRequest req) {\n'
        '  String userId = request.getParameter("userId");\n'
        '  return userService.findById(userId);\n'
        '}\n'
    )
    block = _block("Ctl.java", "getUser", 1, src, language="java")
    # 确定性规则现在直接命中 → block 不进候选(已被规则接管)
    cands = collect_source_candidates(
        [block], sink_func_ids=set(),
        entry_point_ids={block.id},
        source_provider=lambda b: block.source_code.encode(),
    )
    assert len(cands) == 0  # request.getParameter("userId") 已被 j-httpservlet-getparameter 规则覆盖
    # 验证确实被确定性规则识别(而非静默丢失)
    from supernova_core.code_index.source_detector import detect_sources
    sps = detect_sources([block], parser=None, entry_point_ids={block.id},
                        source_provider=lambda b: block.source_code.encode())
    assert any(s.param_name == "userId" and s.rule_id == "j-httpservlet-getparameter"
               for s in sps)


def test_collect_source_candidates_catches_idor_flavor_generic_get_param():
    """IDOR 风味: 通用 `getParam("resourceId")` 写法(部分框架/工具类)—— 非注入 SourcePoint
    规则模式,且不含 `request.` / `req.` 前缀 → 现有 hint 不命中。子项② 新增 regex 项
    `getParam\\(\\s*['\"]\\w*[Ii]d['\"]\\)` 的直接 RED→GREEN 锚点。
    """
    src = (
        'function handler(ctx) {\n'
        '  const resourceId = getParam("resourceId");\n'
        '  return db.resource.findById(resourceId);\n'
        '}\n'
    )
    block = _block("ctl.js", "handler", 1, src, language="javascript")
    cands = collect_source_candidates(
        [block], sink_func_ids=set(),
        entry_point_ids={block.id},
        source_provider=lambda b: block.source_code.encode(),
    )
    assert len(cands) == 1  # getParam("resourceId") 进候选
    assert cands[0].block.id == block.id


# ===== spec 子项② prompt 侧: source 探测器 prompt 标 IDOR 风味(Task 5)=====


async def test_discover_sources_llm_prompt_includes_idor_instruction():
    """IDOR 风味 source 候选 → LLM prompt 含 IDOR 指示段 → 软 source 产 path-type entity-id。

    回归锚点 + prompt 内容守卫: Task 5 在 _PROMPT_TMPL 补「Also identify IDOR vectors」
    段, 要求 LLM 显式识别用作实体 id 的输入(path var / params.id / getParam("userId"))。

    候选来源(Resolution #2): getParam("userId") 非注入 SourcePoint 规则模式 →
    `_has_rule_hit`=False → 进候选; hint 子项② regex `getParam\\(\\s*['\"]\\w*[Ii]d['\"]\\)`
    命中 → 送 LLM。**不用 Java @PathVariable**(被 java-path-variable 规则命中 → 跳过)。
    """
    src = (
        'function handler(ctx) {\n'
        '  const userId = getParam("userId");\n'
        '  return db.user.findById(userId);\n'
        '}\n'
    )
    block = _block("ctl.js", "handler", 1, src, language="javascript")
    cands = collect_source_candidates(
        [block], sink_func_ids=set(),
        entry_point_ids={block.id},
        source_provider=lambda b: block.source_code.encode(),
    )
    assert len(cands) == 1  # getParam("userId") 进候选(非规则命中)

    captured_prompts: list[str] = []

    async def fake_llm(prompt, **kwargs):
        captured_prompts.append(prompt)
        # IDOR 风味: userId 经 getParam 取出, 流入 findById(IDOR vector)
        return ('[{"field":"userId","source_type":"path",'
                '"expression":"getParam(\\"userId\\")","line":2,"is_source":true,'
                '"rationale":"entity-id input used as lookup key (IDOR vector)"}]')

    soft, _gaps = await discover_sources_llm(cands, fake_llm)

    # 软 source 产出 + 标记
    assert len(soft) == 1, f"应产 1 软 source, 实际 {soft}"
    assert soft[0].param_name == "userId"
    assert soft[0].rule_id == "llm-discovered-source"
    assert soft[0].source_type == ParameterSource.PATH_PARAM

    # Resolution #3: prompt 内容守卫(绑定本次 prompt 改动, 非仅回归锚点)
    assert captured_prompts, "prompt 应被发送到 LLM"
    assert "IDOR" in captured_prompts[0], (
        f"prompt 须含 IDOR 指示(Task 5); 实际 prompt 片段: "
        f"{captured_prompts[0][:300]!r}")


# ===== Task 6 (spec 子项① 解耦): source 探测器主基于 entry_point_ids =====
# source 候选收集主基于 entry_point_ids; sink_func_ids 降为可选边际扩展(含 sink
# 函数多看几眼)。sink 失明时 entry handler 仍能进候选 —— 锁定该语义, 防止后续回到
# “source 被 sink 驱动”的耦合(target_ids = sink_func_ids | … 即 sink 失明 → 空)。


def test_collect_source_candidates_independent_of_sink_funcs():
    """sink_func_ids 空时, entry handler 仍经 entry_point_ids 进候选(子项① 解耦锁)。

    Task 3 已把 entry_point_ids 编排提前;本测试锁定函数语义:target_ids 须由
    entry_point_ids 驱动,空 sink_func_ids 不收窄候选范围。
    """
    src = 'function h(req){ const {userId} = req.params; db.find(userId); }\n'
    block = _block("r.js", "h", 1, src, language="javascript")
    # sink_func_ids 空(模拟 sink 失明); entry_point_ids 驱动 → 仍进候选
    out = collect_source_candidates(
        [block], sink_func_ids=set(),
        entry_point_ids={block.id},
        source_provider=lambda b: block.source_code.encode(),
    )
    assert len(out) == 1, (
        f"entry handler 应经 entry_point_ids 进候选(sink 失明不收窄), 实际 {out}")
    assert out[0].block.id == block.id


def test_discover_sources_by_rules_independent_of_sink_funcs():
    """sink_func_ids 空时, entry handler 的点号取用仍经 entry_point_ids 产 SourcePoint。

    对称锁:discover_sources_by_rules 也须由 entry_point_ids 驱动(子项① 解耦)。
    """
    src = 'function h(req){ const x = req.body.preTax; return x; }\n'
    block = _block("r.js", "h", 1, src, language="javascript")
    out = discover_sources_by_rules(
        [block], sink_func_ids=set(),
        entry_point_ids={block.id},
        source_provider=lambda b: block.source_code.encode(),
    )
    assert len(out) == 1
    assert out[0].param_name == "preTax"
    assert out[0].entry_point_id == block.id


def test_collect_source_candidates_sink_func_ids_none_tolerant():
    """sink_func_ids=None 不应 TypeError(None 容错契约)。

    子项①: sink_func_ids 是 optional 边际扩展, 须像 entry_point_ids 一样支持 None。
    旧实现 `sink_func_ids | (entry_point_ids or set())` 在 None 时 TypeError —— 本测试
    驱动 `or set()` 容错落地。
    """
    src = 'function h(req){ const {x} = req.body; return x; }\n'
    block = _block("r.js", "h", 1, src, language="javascript")
    out = collect_source_candidates(
        [block], sink_func_ids=None,
        entry_point_ids={block.id},
        source_provider=lambda b: block.source_code.encode(),
    )
    assert len(out) == 1
    assert out[0].block.id == block.id


def test_discover_sources_by_rules_sink_func_ids_none_tolerant():
    """sink_func_ids=None 不应 TypeError(对称 None 容错)。"""
    src = 'function h(req){ const x = req.body.preTax; return x; }\n'
    block = _block("r.js", "h", 1, src, language="javascript")
    out = discover_sources_by_rules(
        [block], sink_func_ids=None,
        entry_point_ids={block.id},
        source_provider=lambda b: block.source_code.encode(),
    )
    assert len(out) == 1
    assert out[0].param_name == "preTax"



# ===== spec 2026-08-27 §5：discovery 多轮 agent 路径 =====

class _AgentResult:
    def __init__(self, *, success=True, structured_output=None, text="", error=None):
        self.success = success
        self.structured_output = structured_output
        self.text = text
        self.error = error


def test_discover_sources_llm_agent_path():
    """多轮 agent 路径：瘦身 prompt（无源码快照）+ agent_name=gn-discovery-source-NNN
    + structured_output 解析软 source（产物形态与单次路径一致）。"""
    calls_rec = []
    block = _block("f.js", "f", 1, 'function f(req){ const x = input.get("x"); }\n')
    cands = collect_source_candidates([block], {block.id},
                                      source_provider=lambda b: block.source_code.encode())

    async def fake_agent(prompt, *, output_format=None, agent_name=None):
        calls_rec.append({"prompt": prompt, "name": agent_name})
        return _AgentResult(structured_output=[
            {"field": "x", "source_type": "query", "is_source": True,
             "rationale": "r"}])

    out, _gaps = asyncio.run(discover_sources_llm(cands, None,
                                                  discovery_agent=fake_agent))
    assert len(out) == 1
    assert out[0].rule_id == "llm-discovered-source"
    assert calls_rec[0]["name"] == "gn-discovery-source-001"
    assert "def handler" not in calls_rec[0]["prompt"]
    assert "input.get" not in calls_rec[0]["prompt"]  # 无源码快照（agent 自己 read）
