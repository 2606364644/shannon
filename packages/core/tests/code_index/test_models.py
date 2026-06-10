import pytest
from pydantic import ValidationError

from shannon_core.code_index.models import (
    FuncBlock,
    CallEdge,
    EntryPoint,
    CallChain,
    CodeIndex,
    AdjudicatedEntryPoint,
    AdjudicationResult,
    Verdict,
    EntryPointSource,
    # New models
    ParameterSource, TypedParameter, UnifiedEntryPoint,
    FileEntry, FileManifest, DegradationLevel, CoverageGap,
)


def test_func_block_creation():
    block = FuncBlock(
        id="src/app.py:hello:10",
        file_path="src/app.py",
        function_name="hello",
        start_line=10,
        end_line=15,
        source_code="def hello(name):\n    return f'Hello {name}'",
        parameters=["name"],
        language="python",
    )
    assert block.id == "src/app.py:hello:10"
    assert block.language == "python"
    assert block.decorators == []
    assert block.class_name is None


def test_func_block_with_class_and_decorators():
    block = FuncBlock(
        id="src/app.py:UserView.get:20",
        file_path="src/app.py",
        function_name="get",
        start_line=20,
        end_line=30,
        source_code="@app.route('/users')\ndef get(self): pass",
        parameters=["self"],
        class_name="UserView",
        decorators=["@app.route('/users')"],
        language="python",
    )
    assert block.class_name == "UserView"
    assert block.decorators == ["@app.route('/users')"]


def test_call_edge_resolved():
    edge = CallEdge(
        caller_id="src/app.py:hello:10",
        callee_name="greet",
        callee_file="src/utils.py",
        resolved=True,
        line=12,
    )
    assert edge.resolved is True
    assert edge.callee_file == "src/utils.py"


def test_call_edge_unresolved():
    edge = CallEdge(
        caller_id="src/app.py:hello:10",
        callee_name="dynamic_func",
        resolved=False,
        line=13,
    )
    assert edge.resolved is False
    assert edge.callee_file is None


def test_entry_point_high_confidence():
    ep = EntryPoint(
        func_block_id="src/app.py:list_users:5",
        entry_type="http_route",
        route="/api/users",
        http_method="GET",
        confidence=0.95,
        evidence="Decorated with @app.route('/api/users')",
        needs_llm_review=False,
    )
    assert ep.needs_llm_review is False
    assert ep.confidence == 0.95


def test_entry_point_needs_review():
    ep = EntryPoint(
        func_block_id="src/app.py:process:50",
        entry_type="unknown",
        confidence=0.30,
        evidence="async def with no known decorator",
        needs_llm_review=True,
    )
    assert ep.needs_llm_review is True


def test_call_chain():
    chain = CallChain(
        entry_point_id="src/app.py:list_users:5",
        path=[
            "src/app.py:list_users:5",
            "src/services.py:get_users:20",
            "src/db.py:query:30",
        ],
        depth=2,
        has_unresolved=False,
    )
    assert chain.depth == 2
    assert len(chain.path) == 3


def test_call_chain_with_unresolved():
    chain = CallChain(
        entry_point_id="src/app.py:list_users:5",
        path=[
            "src/app.py:list_users:5",
            "src/services.py:get_users:20",
        ],
        depth=1,
        has_unresolved=True,
    )
    assert chain.has_unresolved is True


def test_code_index():
    block = FuncBlock(
        id="src/app.py:hello:1",
        file_path="src/app.py",
        function_name="hello",
        start_line=1,
        end_line=3,
        source_code="def hello(): pass",
        parameters=[],
        language="python",
    )
    edge = CallEdge(
        caller_id="src/app.py:hello:1",
        callee_name="print",
        resolved=False,
        line=2,
    )
    ep = EntryPoint(
        func_block_id="src/app.py:hello:1",
        entry_type="http_route",
        confidence=0.95,
        evidence="@app.route",
        needs_llm_review=False,
    )
    chain = CallChain(
        entry_point_id="src/app.py:hello:1",
        path=["src/app.py:hello:1"],
        depth=0,
        has_unresolved=False,
    )
    index = CodeIndex(
        repository="test-repo",
        language="python",
        total_blocks=1,
        total_entry_points=1,
        total_chains=1,
        blocks=[block],
        edges=[edge],
        entry_points=[ep],
        chains=[chain],
    )
    assert index.total_blocks == 1
    assert index.total_entry_points == 1
    assert index.total_chains == 1
    assert len(index.blocks) == 1


def test_code_index_serialization():
    block = FuncBlock(
        id="a:f:1",
        file_path="a",
        function_name="f",
        start_line=1,
        end_line=1,
        source_code="def f(): pass",
        parameters=[],
        language="python",
    )
    index = CodeIndex(
        repository="repo",
        language="python",
        total_blocks=1,
        total_entry_points=0,
        total_chains=0,
        blocks=[block],
        edges=[],
        entry_points=[],
        chains=[],
    )
    data = index.model_dump()
    assert data["repository"] == "repo"
    json_str = index.model_dump_json()
    assert '"repository":"repo"' in json_str.replace(" ", "")


def test_func_block_missing_required_field():
    with pytest.raises(ValidationError):
        FuncBlock(
            id="a:f:1",
            file_path="a",
            function_name="f",
        )


def test_adjudicated_entry_point_confirmed():
    ep = AdjudicatedEntryPoint(
        func_block_id="app.py:list_users:5",
        verdict=Verdict.CONFIRMED,
        entry_type="http_route",
        route="/api/users",
        http_method="GET",
        evidence="Flask @app.route decorator",
        source=EntryPointSource.CODE_INDEX,
    )
    assert ep.verdict == Verdict.CONFIRMED
    assert ep.source == EntryPointSource.CODE_INDEX


def test_adjudicated_entry_point_rejected():
    ep = AdjudicatedEntryPoint(
        func_block_id="app.py:helper:10",
        verdict=Verdict.REJECTED,
        entry_type="unknown",
        evidence="Internal utility function",
        source=EntryPointSource.CODE_INDEX,
    )
    assert ep.verdict == Verdict.REJECTED
    assert ep.route is None


def test_adjudicated_entry_point_llm_discovery():
    ep = AdjudicatedEntryPoint(
        func_block_id="routes.yaml:create_user",
        verdict=Verdict.CONFIRMED,
        entry_type="http_route",
        route="/users",
        http_method="POST",
        evidence="Found in routes.yaml configuration",
        source=EntryPointSource.LLM_DISCOVERY,
    )
    assert ep.source == EntryPointSource.LLM_DISCOVERY


def test_adjudication_result():
    result = AdjudicationResult(
        repository="test-repo",
        language="python",
        adjudicated_entry_points=[
            AdjudicatedEntryPoint(
                func_block_id="app.py:hello:1",
                verdict=Verdict.CONFIRMED,
                entry_type="http_route",
                evidence="decorated",
                source=EntryPointSource.CODE_INDEX,
            ),
        ],
    )
    assert len(result.adjudicated_entry_points) == 1
    assert result.repository == "test-repo"


def test_adjudication_result_serialization():
    result = AdjudicationResult(
        repository="repo",
        language="python",
        adjudicated_entry_points=[],
    )
    data = result.model_dump()
    assert data["repository"] == "repo"
    assert data["adjudicated_entry_points"] == []
    json_str = result.model_dump_json()
    assert '"repository":"repo"' in json_str.replace(" ", "")


# === New model tests ===

def test_parameter_source_enum():
    assert ParameterSource.QUERY_PARAM == "query"
    assert ParameterSource.BODY_FIELD == "body"
    assert ParameterSource.PATH_PARAM == "path"


def test_typed_parameter_full():
    tp = TypedParameter(
        name="user_id",
        type_annotation="int",
        default_value=None,
        is_variadic=False,
        is_keyword_variadic=False,
        is_optional=False,
    )
    assert tp.name == "user_id"
    assert tp.type_annotation == "int"
    assert tp.is_variadic is False


def test_typed_parameter_kwargs():
    tp = TypedParameter(
        name="kwargs",
        type_annotation=None,
        default_value=None,
        is_variadic=False,
        is_keyword_variadic=True,
    )
    assert tp.is_keyword_variadic is True


def test_unified_entry_point():
    ep = UnifiedEntryPoint(
        uid="app.py:handler:10",
        name="handler",
        file_path="app.py",
        confidence=0.95,
        source="gitnexus",
        entry_type="http_route",
        route="/api/users",
        http_method="GET",
    )
    assert ep.source == "gitnexus"
    assert ep.confidence == 0.95


def test_file_entry():
    fe = FileEntry(
        file_path="templates/index.html",
        file_type="template",
        size_bytes=1024,
    )
    assert fe.file_type == "template"


def test_file_manifest():
    fm = FileManifest(
        entries=[
            FileEntry(file_path="a.html", file_type="template", size_bytes=100),
            FileEntry(file_path="b.yaml", file_type="config", size_bytes=200),
        ]
    )
    assert fm.total_count == 2
    assert fm.by_type["template"] == 1
    assert fm.by_type["config"] == 1


def test_coverage_gap():
    gap = CoverageGap(
        capability="cross_file_call_resolution",
        reason="BFS uses name matching",
        affected_phases=["Phase 0"],
        estimated_coverage_loss="30-50%",
    )
    assert gap.capability == "cross_file_call_resolution"


def test_degradation_level_enum():
    assert DegradationLevel.FULL == "full"
    assert DegradationLevel.DEGRADED == "degraded"
    assert DegradationLevel.MINIMAL == "minimal"


class TestCodeIndexSinkCallSites:
    def test_default_empty_sink_call_sites(self):
        from shannon_core.code_index.models import CodeIndex
        index = CodeIndex(
            repository="repo",
            language="python",
            total_blocks=0,
            total_entry_points=0,
            total_chains=0,
            blocks=[],
            edges=[],
            entry_points=[],
            chains=[],
        )
        assert index.sink_call_sites == []

    def test_sink_call_sites_serialized(self):
        from shannon_core.code_index.models import CodeIndex
        from shannon_core.code_index.parameter_models import (
            SinkCallSite, SinkCategory,
        )
        site = SinkCallSite(
            id="a.py:f:execute:1:0",
            caller_id="a.py:f:1",
            callee_name="execute",
            callee_receiver="cursor",
            category=SinkCategory.SQL,
            sink_subtype="sql_raw",
            file_path="a.py",
            line=1,
            column=0,
            dangerous_slots=[],
            rule_id="py-db-cursor-execute",
        )
        index = CodeIndex(
            repository="repo",
            language="python",
            total_blocks=1,
            total_entry_points=0,
            total_chains=0,
            blocks=[],
            edges=[],
            entry_points=[],
            chains=[],
            sink_call_sites=[site],
        )
        json_str = index.model_dump_json()
        assert '"sink_call_sites"' in json_str
        assert '"py-db-cursor-execute"' in json_str


class TestCallGraphResult:
    def test_call_graph_result_holds_edges_chains_entry_points(self):
        from shannon_core.code_index.models import (
            CallGraphResult, CallEdge, CallChain, FuncBlock,
        )
        edge = CallEdge(
            caller_id="app.py:handler:1",
            callee_name="get_users",
            callee_file="svc.py",
            resolved=True,
            line=5,
        )
        chain = CallChain(
            entry_point_id="app.py:handler:1",
            path=["app.py:handler:1", "svc.py:get_users:10"],
            depth=1,
            has_unresolved=False,
        )
        block = FuncBlock(
            id="app.py:handler:1",
            file_path="app.py",
            function_name="handler",
            start_line=1,
            end_line=20,
            source_code="def handler(): pass",
            parameters=[],
            language="python",
        )
        result = CallGraphResult(
            edges=[edge],
            chains=[chain],
            entry_points=[block],
        )
        assert len(result.edges) == 1
        assert len(result.chains) == 1
        assert len(result.entry_points) == 1
        assert result.degradation_report is None


class TestIntraResult:
    def test_intra_result_holds_taint_data(self):
        from shannon_core.code_index.parameter_models import IntraResult
        from shannon_core.code_index.parameter_models import PropagationStep
        step = PropagationStep(
            from_func_id="app.py:handler:1",
            from_param="user_input",
            to_func_id="app.py:handler:1",
            to_param="query",
        )
        result = IntraResult(
            tainted_params={"user_input", "query"},
            hits={"sink_abc": 0.9},
            local_steps=[step],
        )
        assert "user_input" in result.tainted_params
        assert result.hits["sink_abc"] == 0.9
        assert len(result.local_steps) == 1

    def test_intra_result_empty(self):
        from shannon_core.code_index.parameter_models import IntraResult
        result = IntraResult()
        assert len(result.tainted_params) == 0
        assert len(result.hits) == 0
        assert len(result.local_steps) == 0
