from shannon_core.code_index.models import (
    FuncBlock, CallEdge, EntryPoint, CallChain, CodeIndex,
)
from shannon_core.code_index.summary import generate_summary


def _make_index() -> CodeIndex:
    return CodeIndex(
        repository="test-repo",
        language="python",
        total_blocks=3,
        total_entry_points=2,
        total_chains=2,
        blocks=[
            FuncBlock(
                id="app.py:list_users:5",
                file_path="app.py",
                function_name="list_users",
                start_line=5, end_line=10,
                source_code="def list_users(): ...",
                parameters=[],
                decorators=["@app.route('/users')"],
                language="python",
            ),
            FuncBlock(
                id="app.py:process_queue:20",
                file_path="app.py",
                function_name="process_queue",
                start_line=20, end_line=25,
                source_code="async def process_queue(): ...",
                parameters=[],
                language="python",
            ),
            FuncBlock(
                id="svc.py:get_users:10",
                file_path="svc.py",
                function_name="get_users",
                start_line=10, end_line=15,
                source_code="def get_users(): ...",
                parameters=[],
                language="python",
            ),
        ],
        edges=[
            CallEdge(
                caller_id="app.py:list_users:5",
                callee_name="get_users",
                callee_file="svc.py",
                resolved=True,
                line=7,
            ),
            CallEdge(
                caller_id="app.py:process_queue:20",
                callee_name="dynamic_func",
                resolved=False,
                line=22,
            ),
        ],
        entry_points=[
            EntryPoint(
                func_block_id="app.py:list_users:5",
                entry_type="http_route",
                route="/users",
                http_method="GET",
                confidence=0.95,
                evidence="@app.route('/users')",
                needs_llm_review=False,
            ),
            EntryPoint(
                func_block_id="app.py:process_queue:20",
                entry_type="unknown",
                confidence=0.30,
                evidence="async def with no known decorator",
                needs_llm_review=True,
            ),
        ],
        chains=[
            CallChain(
                entry_point_id="app.py:list_users:5",
                path=["app.py:list_users:5", "svc.py:get_users:10"],
                depth=1,
                has_unresolved=False,
            ),
            CallChain(
                entry_point_id="app.py:process_queue:20",
                path=["app.py:process_queue:20"],
                depth=0,
                has_unresolved=True,
            ),
        ],
    )


class TestGenerateSummary:
    def test_contains_entry_points_table(self):
        index = _make_index()
        summary = generate_summary(index)
        assert "/users" in summary
        assert "GET" in summary
        assert "list_users" in summary

    def test_contains_needs_review_section(self):
        index = _make_index()
        summary = generate_summary(index)
        assert "Entry Points Needing LLM Review" in summary
        assert "process_queue" in summary

    def test_contains_coverage_metrics(self):
        index = _make_index()
        summary = generate_summary(index)
        assert "Coverage" in summary or "resolved" in summary.lower()
        assert "unresolved" in summary.lower()

    def test_shows_total_counts(self):
        index = _make_index()
        summary = generate_summary(index)
        assert "3" in summary  # total blocks
        assert "2" in summary  # total entry points

    def test_empty_index_still_valid(self):
        index = CodeIndex(
            repository="empty", language="python",
            total_blocks=0, total_entry_points=0, total_chains=0,
            blocks=[], edges=[], entry_points=[], chains=[],
        )
        summary = generate_summary(index)
        assert isinstance(summary, str)
        assert len(summary) > 0