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

    def test_no_flows(self):
        summary = format_taint_flow_summary([])
        assert summary == "No taint flow data available for this chain."
