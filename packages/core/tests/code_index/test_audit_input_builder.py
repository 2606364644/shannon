import pytest
from shannon_core.code_index.audit_input_builder import (
    build_chain_audit_input, build_tier1_audit_input,
    format_taint_flow_summary,
)
from shannon_core.code_index.models import FuncBlock, CallChain, ParameterSource
from shannon_core.code_index.parameter_models import (
    TaintFlow, SinkType, PropagationStep,
)


def _block(name: str, file: str, line: int, source: str) -> FuncBlock:
    return FuncBlock(
        id=f"{file}:{name}:{line}", file_path=file,
        function_name=name, start_line=line, end_line=line + 5,
        source_code=source, parameters=[], language="python",
    )


class TestBuildChainAuditInput:
    def test_builds_input_with_source_and_taint(self):
        blocks = {
            "app.py:handler:1": _block(
                "handler", "app.py", 1,
                "def handler(request):\n    user_id = request.args.get('id')\n    process(user_id)\n",
            ),
            "svc.py:process:10": _block(
                "process", "svc.py", 10,
                "def process(order_id):\n    db.query(order_id)\n",
            ),
        }
        chain = CallChain(
            entry_point_id="app.py:handler:1",
            path=["app.py:handler:1", "svc.py:process:10"],
            depth=1, has_unresolved=False,
        )
        flows = [
            TaintFlow(
                entry_point_id="app.py:handler:1",
                source_param="user_id",
                source_type=ParameterSource.QUERY_PARAM,
                propagation_steps=[
                    PropagationStep(
                        from_func_id="app.py:handler:1", from_param="user_id",
                        to_func_id="svc.py:process:10", to_param="order_id",
                        transformation=None, code_location="app.py:3",
                    ),
                ],
                sink_func_id="svc.py:process:10",
                sink_type=SinkType.SQL_EXECUTION,
            ),
        ]

        result = build_chain_audit_input(chain, blocks, flows)
        assert "## Call Chain" in result
        assert "handler" in result
        assert "process" in result
        assert "## Taint Flow" in result
        assert "query" in result
        assert "## Sinks" in result
        assert "SQL execution" in result

    def test_empty_chain(self):
        chain = CallChain(
            entry_point_id="app.py:f:1",
            path=["app.py:f:1"], depth=0, has_unresolved=False,
        )
        result = build_chain_audit_input(chain, {}, [])
        assert "## Call Chain" in result


class TestBuildTier1AuditInput:
    def test_shorter_format(self):
        blocks = {
            "app.py:f:1": _block("f", "app.py", 1, "def f(x): g(x)"),
        }
        chain = CallChain(
            entry_point_id="app.py:f:1",
            path=["app.py:f:1"], depth=0, has_unresolved=False,
        )
        result = build_tier1_audit_input(chain, blocks, [])
        assert "Quick Security Scan" in result


class TestFormatTaintFlowSummary:
    def test_single_flow(self):
        flows = [
            TaintFlow(
                entry_point_id="app.py:handler:1",
                source_param="user_id",
                source_type=ParameterSource.QUERY_PARAM,
                propagation_steps=[
                    PropagationStep(
                        from_func_id="app.py:handler:1", from_param="user_id",
                        to_func_id="svc.py:process:10", to_param="order_id",
                        transformation=None, code_location="app.py:3",
                    ),
                ],
                sink_func_id="svc.py:process:10",
                sink_type=SinkType.SQL_EXECUTION,
            ),
        ]
        summary = format_taint_flow_summary(flows)
        assert "user_id" in summary
        assert "QUERY" in summary or "query" in summary
        assert "order_id" in summary

    def test_summary_shows_slot_and_sanitizer_tail_for_new_fields(self):
        """带 propagation_steps + sink_call_site_id 的 flow → summary tail
        应渲染为 '{slot}@arg{idx} (sanitizer_hint)'。"""
        from shannon_core.code_index.parameter_models import SlotContext
        flow = TaintFlow(
            entry_point_id="app.py:h:1",
            source_param="user_id",
            source_type=ParameterSource.QUERY_PARAM,
            propagation_steps=[
                PropagationStep(
                    from_func_id="app.py:h:1", from_param="user_id",
                    to_func_id="svc.py:q:10", to_param="sql",
                    transformation="concat", code_location="app.py:3",
                ),
            ],
            sink_call_site_id="svc.py:q:execute:11:4",
            sink_slot=SlotContext.SQL_VALUE,
            tainted_arg_index=0,
            confidence=0.8,
            has_sanitizer_hint=True,
        )
        summary = format_taint_flow_summary([flow])
        # tail 含 slot@arg index
        assert "sql_value@arg0" in summary
        # sanitizer 提示
        assert "(sanitizer_hint)" in summary
        # propagation path 也渲染了
        assert "user_id" in summary
        assert "sql" in summary

    def test_no_flows(self):
        summary = format_taint_flow_summary([])
        assert summary == "No taint flow data available for this chain."


class TestSinkCallSiteFieldsInPrompt:
    def test_chain_audit_input_shows_slot_and_arg_index(self):
        from shannon_core.code_index.audit_input_builder import build_chain_audit_input
        from shannon_core.code_index.parameter_models import SlotContext
        block = FuncBlock(
            id="app.py:h:1", file_path="app.py",
            function_name="h", start_line=1, end_line=5,
            source_code="def h(x): cursor.execute(x)",
            parameters=["x"], language="python",
        )
        chain = CallChain(
            entry_point_id="app.py:h:1",
            path=["app.py:h:1"],
            depth=0, has_unresolved=False,
        )
        flow = TaintFlow(
            flow_id="app.py:h:1->app.py:h:execute:2:4",
            entry_point_id="app.py:h:1",
            source_param="x",
            source_type=ParameterSource.QUERY_PARAM,
            sink_call_site_id="app.py:h:execute:2:4",
            sink_slot=SlotContext.SQL_VALUE,
            tainted_arg_index=0,
            confidence=0.7,
            has_sanitizer_hint=False,
        )
        text = build_chain_audit_input(chain, {block.id: block}, [flow])
        assert "sql_value" in text
        assert "arg 0" in text

    def test_sanitizer_hint_marked(self):
        from shannon_core.code_index.audit_input_builder import build_chain_audit_input
        from shannon_core.code_index.parameter_models import SlotContext
        block = FuncBlock(
            id="app.py:h:1", file_path="app.py",
            function_name="h", start_line=1, end_line=5,
            source_code="def h(x): cursor.execute(escape(x))",
            parameters=["x"], language="python",
        )
        chain = CallChain(entry_point_id="app.py:h:1", path=["app.py:h:1"],
                          depth=0, has_unresolved=False)
        flow = TaintFlow(
            flow_id="f1",
            entry_point_id="app.py:h:1",
            source_param="x",
            source_type=ParameterSource.QUERY_PARAM,
            sink_call_site_id="app.py:h:execute:2:4",
            sink_slot=SlotContext.SQL_VALUE,
            tainted_arg_index=0,
            confidence=0.5,
            has_sanitizer_hint=True,
        )
        text = build_chain_audit_input(chain, {block.id: block}, [flow])
        assert "[sanitizer_hint]" in text

    def test_legacy_flow_without_new_fields_renders_without_crash(self):
        """旧 TaintFlow（只填了 sink_func_id / sink_type）仍能渲染。"""
        from shannon_core.code_index.audit_input_builder import build_chain_audit_input
        block = FuncBlock(
            id="app.py:h:1", file_path="app.py",
            function_name="h", start_line=1, end_line=5,
            source_code="def h(x): pass",
            parameters=["x"], language="python",
        )
        chain = CallChain(entry_point_id="app.py:h:1", path=["app.py:h:1"],
                          depth=0, has_unresolved=False)
        flow = TaintFlow(
            entry_point_id="app.py:h:1",
            source_param="x",
            source_type=ParameterSource.QUERY_PARAM,
            sink_func_id="app.py:h:1",
            sink_type=SinkType.SQL_EXECUTION,
        )
        text = build_chain_audit_input(chain, {block.id: block}, [flow])
        # 老 sink 文案还在
        assert "SQL" in text
