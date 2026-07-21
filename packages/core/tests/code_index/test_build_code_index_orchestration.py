"""编排不变量: sink 探测器产出在 taint analysis 之前并入 sinks_by_func。

用 monkeypatch 桩掉重 I/O(build_call_graph_from_gitnexus / discover_*_llm /
analyze_taint_llm / detect_entry_points / _parse_and_detect_sync),断言
discover_sinks_by_entry 先于 analyze_taint_llm 被调,且其产出进入 taint 的输入。

Resolution #2 修正原 brief 测试:原版 stub `_parse_and_detect_sync → ({}, [], [], [])`
会让 sinks_by_func 空 → taint_items 空 → analyze_taint_llm 永不被调 →
order.index("analyze_taint_llm") 抛 ValueError(不是干净的断言失败)。

本测试构造一个真 FuncBlock("e1")+ 真 EntryPoint("e1")+ hunter 产
SinkCallSite(caller_id="e1"),同时触发 discover_sinks_by_entry 与
analyze_taint_llm,真正验证"hunter 先 + 喂 taint"不变量。
"""
import os
import tempfile

import pytest

from supernova_core.code_index.models import (
    CallGraphResult, EntryPoint, FuncBlock,
)
from supernova_core.code_index.parameter_models import (
    DangerousSlot, IntraResult, SinkCallSite, SinkCategory, SlotContext,
)


def _make_min_repo() -> str:
    """tmp repo with a single app.py(让 detect_language 返回 python, 不走真 parse)."""
    repo = tempfile.mkdtemp()
    with open(os.path.join(repo, "app.py"), "w") as fh:
        fh.write("def handler(req): pass\n")
    os.makedirs(os.path.join(repo, ".git"), exist_ok=True)
    return repo


@pytest.mark.asyncio
async def test_sink_hunter_runs_before_taint_and_feeds_it(monkeypatch):
    """编排不变量(Resolution #1): ⑦ entry 提前 → ④ sinks_by_func →
    ③c hunter → ⑤ taint。验证 hunter 先于 taint + hunter sink 喂进 taint 输入。

    步骤:
    1. 1 个 entry handler block(e1),无 rule sink(sinks_by_func 空,需 hunter 补)
    2. detect_entry_points stub → e1 是 entry(确定性, 不靠 source 匹配)
    3. hunter stub → 产 1 个 e1 上的 SinkCallSite(让 taint 有 item 跑)
    4. analyze_taint_llm stub → 记录 order + 收到的 sinks_in_func(证明 hunter sink 进入)
    """
    import supernova_core.code_index as ci
    from supernova_core.code_index import build_code_index_with_gitnexus

    order: list[str] = []
    taint_inputs: dict[str, list] = {}  # func_id -> sinks_in_func received by taint

    # 1 个 entry handler block(e1), 无 rule sink
    block = FuncBlock(
        id="e1", file_path="app.py", function_name="handler",
        start_line=1, end_line=10, source_code="def handler(req): pass",
        parameters=["req"], language="python",
    )

    # Stub _parse_and_detect_sync → 1 block, 空 sinks/suspicious
    monkeypatch.setattr(
        ci, "_parse_and_detect_sync",
        lambda *a, **kw: ({"app.py": b"src"}, [block], [], []),
    )

    # Stub call_graph 空
    async def fake_call_graph(*a, **kw):
        return CallGraphResult(edges=[], chains=[], entry_points=[])

    monkeypatch.setattr(ci, "build_call_graph_from_gitnexus", fake_call_graph)

    # Stub ③b discover_sinks_llm 空(候选表无命中 → 判定器产 0)
    async def fake_discover_sinks(suspicious, llm_client, **kw):
        order.append("discover_sinks_llm")
        return [], []

    monkeypatch.setattr(ci, "discover_sinks_llm", fake_discover_sinks)

    # Stub ⑦ detect_entry_points → e1 是 entry(确定性, 不靠 source 匹配)
    monkeypatch.setattr(
        ci, "detect_entry_points",
        lambda *a, **kw: [EntryPoint(
            func_block_id="e1", entry_type="http_route", route="/x",
            http_method="GET", confidence=0.9, evidence="",
            needs_llm_review=False, source="code_index",
        )])

    # Stub ③c hunter → 产 1 个 e1 上的 sink(让 taint 有 item 跑)
    hunter_sink = SinkCallSite(
        id="llm:app.py:5", caller_id="e1", callee_name="JSON.parseObject",
        callee_receiver=None, category=SinkCategory.DESERIALIZATION,
        sink_subtype="deserialization", file_path="app.py", line=5, column=0,
        dangerous_slots=[DangerousSlot(
            arg_index=0, slot=SlotContext.DESERIALIZE_OBJ,
            expression="payload", is_entry_hint=True,
        )],
        rule_id="llm-discovered-sink", needs_review=True,
    )

    async def fake_hunter(cands, llm_client, **kw):
        order.append("discover_sinks_by_entry")
        return [hunter_sink], []

    monkeypatch.setattr(ci, "discover_sinks_by_entry", fake_hunter)

    # Stub ⑤ analyze_taint_llm 记录它收到的 sinks_in_func(证明 hunter sink 进入)
    async def fake_taint(*, block, sinks_in_func, llm_client, **kw):
        order.append("analyze_taint_llm")
        taint_inputs[block.id] = list(sinks_in_func)
        return IntraResult(tainted_params=set(), hits={}, local_steps=[])

    monkeypatch.setattr(ci, "analyze_taint_llm", fake_taint)

    # Stub ⑧b source discovery 空
    async def fake_sources(cands, llm_client, **kw):
        order.append("discover_sources_llm")
        return [], []

    monkeypatch.setattr(ci, "discover_sources_llm", fake_sources)

    # 触发编排
    repo = _make_min_repo()
    await build_code_index_with_gitnexus(
        repo, mcp_client=None, llm_client=None,
    )

    # 不变量 1: hunter 先于 taint
    assert "discover_sinks_by_entry" in order, (
        f"sink 探测器未被调用, order={order}")
    assert "analyze_taint_llm" in order, (
        f"taint 未被调用(hunter 可能产 0 sink → taint_items 空), order={order}")
    assert order.index("discover_sinks_by_entry") < order.index("analyze_taint_llm"), (
        f"sink 探测器必须在 taint 之前跑, 实际顺序: {order}")

    # 不变量 2(更强): hunter 产的 sink 确实进入 taint 的输入
    assert "e1" in taint_inputs, (
        f"e1 未进入 taint, taint_inputs={taint_inputs}, order={order}")
    received_ids = {s.id for s in taint_inputs["e1"]}
    assert hunter_sink.id in received_ids, (
        f"hunter sink {hunter_sink.id} 未进入 taint 输入, 收到: {received_ids}")
