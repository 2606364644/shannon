import pytest
from shannon_core.code_index.models import FuncBlock, CallEdge, CallChain
from shannon_core.code_index.call_graph import build_call_chains, resolve_edges


def _block(name: str, file: str = "app.py", line: int = 1) -> FuncBlock:
    return FuncBlock(
        id=f"{file}:{name}:{line}",
        file_path=file,
        function_name=name,
        start_line=line,
        end_line=line + 5,
        source_code=f"def {name}(): pass",
        parameters=[],
        language="python",
    )


def _edge(caller: str, callee: str, line: int = 1, resolved: bool = False, callee_file: str | None = None) -> CallEdge:
    return CallEdge(
        caller_id=caller,
        callee_name=callee,
        callee_file=callee_file,
        resolved=resolved,
        line=line,
    )


class TestResolveEdges:
    def test_resolves_matching_function_name(self):
        blocks = [_block("get_users", "svc.py", 10), _block("save_users", "svc.py", 20)]
        edges = [_edge("app.py:handler:1", "get_users", resolved=False)]
        resolved = resolve_edges(edges, blocks)
        assert resolved[0].resolved is True
        assert resolved[0].callee_file == "svc.py"

    def test_unresolved_when_no_match(self):
        blocks = [_block("get_users", "svc.py", 10)]
        edges = [_edge("app.py:handler:1", "unknown_func", resolved=False)]
        resolved = resolve_edges(edges, blocks)
        assert resolved[0].resolved is False

    def test_resolves_to_first_match_when_ambiguous(self):
        blocks = [_block("helper", "a.py", 1), _block("helper", "b.py", 1)]
        edges = [_edge("app.py:main:1", "helper", resolved=False)]
        resolved = resolve_edges(edges, blocks)
        assert resolved[0].resolved is True
        assert resolved[0].callee_file in ("a.py", "b.py")


class TestBuildCallChains:
    def test_single_entry_point_with_one_call(self):
        blocks = [_block("handler", "app.py", 1), _block("get_data", "svc.py", 10)]
        edges = [
            _edge("app.py:handler:1", "get_data", resolved=True, callee_file="svc.py"),
        ]
        entry_ids = ["app.py:handler:1"]
        chains = build_call_chains(entry_ids, edges, max_depth=15, max_width=50, blocks=blocks)
        assert len(chains) == 1
        assert chains[0].path == ["app.py:handler:1", "svc.py:get_data:10"]
        assert chains[0].depth == 1

    def test_branching_call_graph(self):
        blocks = [
            _block("handler", "app.py", 1),
            _block("get_a", "svc.py", 10),
            _block("get_b", "svc.py", 20),
        ]
        edges = [
            _edge("app.py:handler:1", "get_a", resolved=True, callee_file="svc.py"),
            _edge("app.py:handler:1", "get_b", resolved=True, callee_file="svc.py"),
        ]
        entry_ids = ["app.py:handler:1"]
        chains = build_call_chains(entry_ids, edges, max_depth=15, max_width=50, blocks=blocks)
        assert len(chains) == 2

    def test_chain_with_unresolved_call(self):
        edges = [
            _edge("app.py:handler:1", "dynamic_func", resolved=False),
        ]
        entry_ids = ["app.py:handler:1"]
        chains = build_call_chains(entry_ids, edges, max_depth=15, max_width=50)
        assert len(chains) == 1
        assert chains[0].has_unresolved is True

    def test_max_depth_stops_traversal(self):
        blocks = [_block(f"func_{i}", "app.py", i * 10) for i in range(20)]
        edges = [
            _edge(f"app.py:func_{i}:{i*10}", f"func_{i+1}", resolved=True, callee_file="app.py")
            for i in range(19)
        ]
        entry_ids = ["app.py:func_0:0"]
        chains = build_call_chains(entry_ids, edges, max_depth=5, max_width=50, blocks=blocks)
        for chain in chains:
            assert chain.depth <= 5

    def test_cycle_detection(self):
        blocks = [_block("a", "app.py", 1), _block("b", "app.py", 10)]
        edges = [
            _edge("app.py:a:1", "b", resolved=True, callee_file="app.py"),
            _edge("app.py:b:10", "a", resolved=True, callee_file="app.py"),
        ]
        entry_ids = ["app.py:a:1"]
        chains = build_call_chains(entry_ids, edges, max_depth=15, max_width=50, blocks=blocks)
        assert len(chains) >= 1
        for chain in chains:
            assert len(chain.path) == len(set(chain.path))

    def test_no_edges_produces_single_node_chain(self):
        entry_ids = ["app.py:handler:1"]
        chains = build_call_chains(entry_ids, [], max_depth=15, max_width=50)
        assert len(chains) == 1
        assert chains[0].path == ["app.py:handler:1"]
        assert chains[0].depth == 0


class TestDiamondPathPreservation:
    def test_diamond_paths_preserved(self):
        """A→B→D and A→C→D should produce two separate chains."""
        blocks = [
            _block("a", "app.py", 1),
            _block("b", "svc.py", 10),
            _block("c", "svc.py", 20),
            _block("d", "svc.py", 30),
        ]
        edges = [
            _edge("app.py:a:1", "b", resolved=True, callee_file="svc.py"),
            _edge("app.py:a:1", "c", resolved=True, callee_file="svc.py"),
            _edge("svc.py:b:10", "d", resolved=True, callee_file="svc.py"),
            _edge("svc.py:c:20", "d", resolved=True, callee_file="svc.py"),
        ]
        entry_ids = ["app.py:a:1"]
        chains = build_call_chains(
            entry_ids, edges, max_depth=15, max_width=50,
            blocks=blocks, preserve_diamonds=True,
        )
        # Should have 2 chains: A→B→D and A→C→D
        assert len(chains) == 2
        paths = [c.path for c in chains]
        assert ["app.py:a:1", "svc.py:b:10", "svc.py:d:30"] in paths
        assert ["app.py:a:1", "svc.py:c:20", "svc.py:d:30"] in paths

    def test_diamond_default_off(self):
        """By default (preserve_diamonds=False), only one path is kept."""
        blocks = [
            _block("a", "app.py", 1),
            _block("b", "svc.py", 10),
            _block("c", "svc.py", 20),
            _block("d", "svc.py", 30),
        ]
        edges = [
            _edge("app.py:a:1", "b", resolved=True, callee_file="svc.py"),
            _edge("app.py:a:1", "c", resolved=True, callee_file="svc.py"),
            _edge("svc.py:b:10", "d", resolved=True, callee_file="svc.py"),
            _edge("svc.py:c:20", "d", resolved=True, callee_file="svc.py"),
        ]
        entry_ids = ["app.py:a:1"]
        chains = build_call_chains(entry_ids, edges, max_depth=15, max_width=50, blocks=blocks)
        # Default behavior: cycle check in path prevents second visit to D
        # At minimum we should have 1 chain
        assert len(chains) >= 1
