from shannon_core.code_index.risk_scorer import ChainRiskScore, AuditBudget
from shannon_core.code_index.models import FuncBlock, CallChain, ParameterSource
from shannon_core.code_index.parameter_models import TaintFlow, SinkType, PropagationStep


def _block(name: str, file: str = "app.py", line: int = 1,
           source: str = "") -> FuncBlock:
    return FuncBlock(
        id=f"{file}:{name}:{line}", file_path=file,
        function_name=name, start_line=line, end_line=line + 5,
        source_code=source or f"def {name}(): pass",
        parameters=[], language="python",
    )


def _make_flow(sink_type: SinkType = SinkType.SQL_EXECUTION) -> TaintFlow:
    return TaintFlow(
        entry_point_id="app.py:handler:1",
        source_param="user_id",
        source_type=ParameterSource.QUERY_PARAM,
        propagation_steps=[
            PropagationStep(
                from_func_id="app.py:handler:1", from_param="user_id",
                to_func_id="svc.py:query:10", to_param="sql",
                transformation=None, code_location="app.py:3",
            ),
        ],
        sink_func_id="svc.py:query:10",
        sink_type=sink_type,
    )


class TestChainRiskScore:
    def test_tier3_high_risk(self):
        """SQL sink + taint complete + no auth + depth 5 = Tier 3."""
        score = ChainRiskScore(
            chain_id="app.py:handler:1→svc.py:query:10",
            sink_danger=10, taint_completeness=8,
            auth_gap=8, depth=5,
        )
        assert score.total == 31
        assert score.tier == 3

    def test_tier2_medium_risk(self):
        """Template sink + some taint + no auth + depth 3 = Tier 2."""
        score = ChainRiskScore(
            chain_id="a→b",
            sink_danger=7, taint_completeness=4,
            auth_gap=8, depth=3,
        )
        assert score.total == 22
        assert score.tier == 2

    def test_tier1_low_risk(self):
        """Unknown sink + no taint + has auth + depth 1 = Tier 1."""
        score = ChainRiskScore(
            chain_id="a→c",
            sink_danger=0, taint_completeness=0,
            auth_gap=0, depth=1,
        )
        assert score.total == 1
        assert score.tier == 1

    def test_boundary_tier2_at_15(self):
        """Score exactly 15 is Tier 2."""
        score = ChainRiskScore(
            chain_id="x",
            sink_danger=5, taint_completeness=5,
            auth_gap=0, depth=5,
        )
        assert score.total == 15
        assert score.tier == 2

    def test_boundary_tier3_at_30(self):
        """Score exactly 30 is Tier 3."""
        score = ChainRiskScore(
            chain_id="y",
            sink_danger=10, taint_completeness=10,
            auth_gap=5, depth=5,
        )
        assert score.total == 30
        assert score.tier == 3


class TestChainRiskScoreClassMethod:
    def test_score_sql_chain_no_auth(self):
        blocks = {
            "app.py:handler:1": _block("handler", "app.py", 1),
            "svc.py:query:10": _block("query", "svc.py", 10,
                                       source="def query(sql): cursor.execute(sql)"),
        }
        chain = CallChain(
            entry_point_id="app.py:handler:1",
            path=["app.py:handler:1", "svc.py:query:10"],
            depth=1, has_unresolved=False,
        )
        flows = [_make_flow(SinkType.SQL_EXECUTION)]
        auth_ids: set[str] = set()

        score = ChainRiskScore.score(chain, blocks, flows, auth_ids)
        assert score.sink_danger == 10  # SQL execution
        assert score.auth_gap == 8      # No auth
        assert score.tier == 3          # High total

    def test_score_with_auth(self):
        blocks = {
            "app.py:handler:1": _block("handler", "app.py", 1),
            "svc.py:query:10": _block("query", "svc.py", 10,
                                       source="def query(sql): cursor.execute(sql)"),
        }
        chain = CallChain(
            entry_point_id="app.py:handler:1",
            path=["app.py:handler:1", "svc.py:query:10"],
            depth=1, has_unresolved=False,
        )
        flows = [_make_flow(SinkType.SQL_EXECUTION)]
        auth_ids = {"app.py:handler:1"}  # handler has auth

        score = ChainRiskScore.score(chain, blocks, flows, auth_ids)
        assert score.auth_gap == 0  # Has auth middleware
        assert score.tier >= 2      # Still elevated due to SQL sink + taint

    def test_score_no_flows_no_sink(self):
        blocks = {
            "app.py:handler:1": _block("handler", "app.py", 1),
        }
        chain = CallChain(
            entry_point_id="app.py:handler:1",
            path=["app.py:handler:1"],
            depth=0, has_unresolved=False,
        )
        score = ChainRiskScore.score(chain, blocks, [], set())
        assert score.sink_danger == 0
        assert score.taint_completeness == 0
        assert score.tier == 1

    def test_score_command_exec_sink(self):
        blocks = {
            "app.py:handler:1": _block("handler", "app.py", 1),
            "svc.py:run:10": _block("run", "svc.py", 10,
                                     source="def run(cmd): os.system(cmd)"),
        }
        chain = CallChain(
            entry_point_id="app.py:handler:1",
            path=["app.py:handler:1", "svc.py:run:10"],
            depth=1, has_unresolved=False,
        )
        flows = [_make_flow(SinkType.COMMAND_EXEC)]
        score = ChainRiskScore.score(chain, blocks, flows, set())
        assert score.sink_danger == 10


class TestAuditBudget:
    def test_default_budget(self):
        budget = AuditBudget()
        assert budget.max_total_llm_calls == 200
        assert budget.tier3_max_chains == 5
        assert budget.tier2_max_chains == 20

    def test_custom_budget(self):
        budget = AuditBudget(
            max_total_llm_calls=100,
            tier3_max_chains=3,
            tier2_max_chains=10,
        )
        assert budget.max_total_llm_calls == 100

    def test_estimate_calls(self):
        budget = AuditBudget()
        # 3 tier3 × 5 + 10 tier2 × 2 + 50 tier1 × 1 = 15 + 20 + 50 = 85
        est = budget.estimate_calls(tier3_count=3, tier2_count=10, tier1_count=50)
        assert est == 85
