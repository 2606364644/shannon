"""chain_propagator 单元测试 — 确定性跨函数 taint 传播。"""
import pytest

from shannon_core.code_index.models import FuncBlock, CallChain
from shannon_core.code_index.parameter_models import (
    DangerousSlot,
    IntraResult,
    PropagationStep,
    SinkCallSite,
    SinkCategory,
    SlotContext,
)
from shannon_core.code_index.chain_propagator import (
    propagate_across_chains,
    _references_tainted,
)
from shannon_core.code_index.llm_taint_analyzer import _deterministic_intra_fallback
from shannon_core.code_index.parameter_models import TaintFlow


def _block(
    name: str, file: str = "app.py", line: int = 1,
    params: list[str] | None = None,
) -> FuncBlock:
    return FuncBlock(
        id=f"{file}:{name}:{line}",
        file_path=file,
        function_name=name,
        start_line=line,
        end_line=line + 10,
        source_code=f"def {name}(): pass",
        parameters=params or [],
        language="python",
    )


def _sink(func_id: str, sink_id: str = "sink_1") -> SinkCallSite:
    return SinkCallSite(
        id=sink_id,
        caller_id=func_id,
        callee_name="cursor.execute",
        callee_receiver="cursor",
        category=SinkCategory.SQL,
        sink_subtype="execute",
        file_path="app.py",
        line=5,
        column=0,
        dangerous_slots=[DangerousSlot(arg_index=0, slot=SlotContext.SQL_VALUE, expression="query", is_entry_hint=False)],
        rule_id="sql-execute",
        needs_review=False,
    )


class TestReferencesTainted:
    def test_exact_match(self):
        assert _references_tainted("user_input", {"user_input"}) is True

    def test_prefix_match(self):
        assert _references_tainted("request.user_id", {"request"}) is True

    def test_no_match(self):
        assert _references_tainted("config.limit", {"user_input"}) is False

    def test_empty_tainted(self):
        assert _references_tainted("anything", set()) is False


class TestPropagateAcrossChains:
    def test_single_function_chain_with_tainted_sink(self):
        handler = _block("handler", "app.py", 1, params=["user_input"])
        intra_results = {
            handler.id: IntraResult(
                tainted_params={"user_input"},
                hits={"sink_1": 0.9},
                local_steps=[
                    PropagationStep(
                        from_func_id=handler.id,
                        from_param="user_input",
                        to_func_id=handler.id,
                        to_param="query",
                        code_location="app.py:3",
                        confidence=0.9,
                    ),
                ],
            ),
        }
        chains = [
            CallChain(
                entry_point_id=handler.id,
                path=[handler.id],
                depth=0,
                has_unresolved=False,
            ),
        ]
        flows = propagate_across_chains(
            chains=chains,
            blocks=[handler],
            intra_results=intra_results,
        )
        assert len(flows) >= 1

    def test_two_function_chain_propagates_taint(self):
        handler = _block("handler", "app.py", 1, params=["request"])
        get_user = _block("get_user", "svc.py", 10, params=["user_id"])
        intra_results = {
            handler.id: IntraResult(
                tainted_params={"request"},
                hits={},
                local_steps=[],
            ),
            get_user.id: IntraResult(
                tainted_params={"user_id"},
                hits={"sink_db": 0.85},
                local_steps=[
                    PropagationStep(
                        from_func_id=get_user.id,
                        from_param="user_id",
                        to_func_id=get_user.id,
                        to_param="query",
                        code_location="svc.py:12",
                        confidence=0.85,
                    ),
                ],
            ),
        }
        chains = [
            CallChain(
                entry_point_id=handler.id,
                path=[handler.id, get_user.id],
                depth=1,
                has_unresolved=False,
            ),
        ]
        flows = propagate_across_chains(
            chains=chains,
            blocks=[handler, get_user],
            intra_results=intra_results,
        )
        assert len(flows) >= 1

    def test_max_depth_stops_traversal(self):
        blocks = [
            _block("a", "a.py", 1, params=["x"]),
            _block("b", "b.py", 1, params=["y"]),
            _block("c", "c.py", 1, params=["z"]),
        ]
        intra_results = {b.id: IntraResult(tainted_params=set(b.parameters)) for b in blocks}
        chains = [
            CallChain(
                entry_point_id=blocks[0].id,
                path=[b.id for b in blocks],
                depth=2,
                has_unresolved=False,
            ),
        ]
        flows = propagate_across_chains(
            chains=chains,
            blocks=blocks,
            intra_results=intra_results,
            max_depth=1,
        )
        assert all(
            all(s.to_func_id != blocks[2].id for s in f.propagation_steps)
            for f in flows
        )

    def test_empty_chains_returns_empty(self):
        flows = propagate_across_chains(
            chains=[], blocks=[], intra_results={},
        )
        assert flows == []


@pytest.mark.asyncio
async def test_raising_llm_client_yields_nonempty_flows_via_fallback():
    """P1: raising LLM client → analyze_taint_llm conservative fallback
    (all params tainted + every sink marked hit) → propagate_across_chains
    emits non-empty flows (taint channel non-empty, not silently dead)."""
    from shannon_core.code_index.llm_taint_analyzer import analyze_taint_llm

    block = _block("handler", "app.py", 1, params=["q"])
    sink = _sink(block.id, sink_id="sink_1")

    async def raising_client(prompt, **kwargs):
        raise RuntimeError("LLM taint client not wired in production")

    intra = await analyze_taint_llm(block, sinks_in_func=[sink], llm_client=raising_client)
    assert "q" in intra.tainted_params
    assert "sink_1" in intra.hits

    chain = CallChain(entry_point_id=block.id, path=[block.id], depth=1, has_unresolved=False)
    flows = propagate_across_chains(chains=[chain], blocks=[block], intra_results={block.id: intra})
    assert len(flows) >= 1


class TestDeterministicFallbackPropagationSmoke:
    """spec §3.4: _deterministic_intra_fallback 产出的 IntraResult 必须能被
    propagate_across_chains 消费、emit TaintFlow,契约不破坏。"""

    def test_direct_hit_propagates_with_high_confidence(self):
        # head(entry point) 有参数;sink 在同一函数,dangerous_slot 直达参数。
        handler = _block("handler", "app.py", 1, params=["user_input"])
        # 用直达 slot 覆盖默认 _sink(expression="query", is_entry_hint=False)
        direct_sink = SinkCallSite(
            id="sink_direct",
            caller_id=handler.id,
            callee_name="cursor.execute",
            callee_receiver="cursor",
            category=SinkCategory.SQL,
            sink_subtype="execute",
            file_path="app.py",
            line=5,
            column=0,
            dangerous_slots=[DangerousSlot(
                arg_index=0, slot=SlotContext.SQL_VALUE,
                expression="user_input", is_entry_hint=True,
            )],
            rule_id="sql-execute",
            needs_review=False,
        )
        intra = {
            handler.id: _deterministic_intra_fallback(handler, [direct_sink]),
        }
        chains = [CallChain(
            entry_point_id=handler.id, path=[handler.id],
            depth=0, has_unresolved=False,
        )]
        flows = propagate_across_chains(chains=chains, blocks=[handler], intra_results=intra)
        assert len(flows) == 1
        assert isinstance(flows[0], TaintFlow)
        assert flows[0].sink_call_site_id == "sink_direct"
        assert flows[0].confidence == 0.9  # 直达分层传入 TaintFlow.confidence

    def test_literal_sink_produces_no_flow(self):
        # 字面量 sink 被过滤 → hits 空 → 不 emit flow(但 chain 仍 seed,不崩)。
        handler = _block("handler", "app.py", 1, params=["user_input"])
        literal_sink = SinkCallSite(
            id="sink_literal",
            caller_id=handler.id,
            callee_name="cursor.execute",
            callee_receiver="cursor",
            category=SinkCategory.SQL,
            sink_subtype="execute",
            file_path="app.py",
            line=5,
            column=0,
            dangerous_slots=[DangerousSlot(
                arg_index=0, slot=SlotContext.SQL_VALUE,
                expression="'SELECT 1'", is_entry_hint=False,
            )],
            rule_id="sql-execute",
            needs_review=False,
        )
        intra = {
            handler.id: _deterministic_intra_fallback(handler, [literal_sink]),
        }
        chains = [CallChain(
            entry_point_id=handler.id, path=[handler.id],
            depth=0, has_unresolved=False,
        )]
        flows = propagate_across_chains(chains=chains, blocks=[handler], intra_results=intra)
        assert flows == []  # 字面量 sink 过滤 → 无 flow
