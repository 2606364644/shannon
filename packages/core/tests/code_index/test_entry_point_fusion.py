import pytest
from shannon_core.code_index.entry_point_fusion import merge_entry_points
from shannon_core.code_index.models import EntryPoint, UnifiedEntryPoint, Verdict


def test_verdict_has_needs_review():
    assert Verdict.NEEDS_REVIEW.value == "needs_review"


def test_entry_point_has_authentication_and_source():
    ep = EntryPoint(
        func_block_id="app.py:handler:10",
        entry_type="http_route",
        route="/users",
        http_method="GET",
        confidence=0.95,
        evidence="Flask route decorator",
        needs_llm_review=False,
        authentication="public",
        source="code_index",
    )
    assert ep.authentication == "public"
    assert ep.source == "code_index"


def test_entry_point_defaults():
    ep = EntryPoint(
        func_block_id="app.py:handler:10",
        entry_type="http_route",
        confidence=0.60,
        evidence="LLM discovery",
        needs_llm_review=False,
    )
    assert ep.authentication is None
    assert ep.source == "code_index"


def _gitnexus_ep(name: str, file: str, score: float = 0.9) -> dict:
    return {
        "name": name, "filePath": file, "score": score,
        "kind": "http_route", "route": f"/{name}", "httpMethod": "GET",
    }


def _schema_ep(name: str, file: str) -> UnifiedEntryPoint:
    return UnifiedEntryPoint(
        uid=f"{file}:{name}", name=name, file_path=file,
        confidence=0.80, source="schema_file", entry_type="http_route",
    )


def _convention_ep(name: str, file: str) -> UnifiedEntryPoint:
    return UnifiedEntryPoint(
        uid=f"{file}:{name}", name=name, file_path=file,
        confidence=0.75, source="framework_convention", entry_type="http_route",
    )


class TestMergeEntryPoints:
    def test_gitnexus_only(self):
        result = merge_entry_points(
            gitnexus_eps=[_gitnexus_ep("handler", "app.py")],
            schema_eps=[],
            convention_eps=[],
        )
        assert len(result) == 1
        assert result[0].source == "gitnexus"
        assert result[0].confidence == 0.9

    def test_dedup_same_uid(self):
        gn = _gitnexus_ep("handler", "app.py", score=0.9)
        schema = _schema_ep("handler", "app.py")
        result = merge_entry_points(
            gitnexus_eps=[gn],
            schema_eps=[schema],
            convention_eps=[],
        )
        # GitNexus wins (higher confidence, primary source)
        assert len(result) == 1
        assert result[0].source == "gitnexus"

    def test_schema_fills_gap(self):
        result = merge_entry_points(
            gitnexus_eps=[_gitnexus_ep("a", "app.py")],
            schema_eps=[_schema_ep("b", "api.py")],
            convention_eps=[],
        )
        assert len(result) == 2
        sources = {ep.source for ep in result}
        assert sources == {"gitnexus", "schema_file"}

    def test_convention_fills_gap(self):
        result = merge_entry_points(
            gitnexus_eps=[],
            schema_eps=[],
            convention_eps=[_convention_ep("pages_api", "pages/api/users.ts")],
        )
        assert len(result) == 1
        assert result[0].source == "framework_convention"

    def test_all_sources_merged(self):
        result = merge_entry_points(
            gitnexus_eps=[_gitnexus_ep("a", "app.py")],
            schema_eps=[_schema_ep("b", "api.py")],
            convention_eps=[_convention_ep("c", "pages/api/x.ts")],
        )
        assert len(result) == 3

    def test_low_confidence_flagged(self):
        gn = _gitnexus_ep("maybe_handler", "app.py", score=0.3)
        result = merge_entry_points(
            gitnexus_eps=[gn],
            schema_eps=[],
            convention_eps=[],
        )
        assert len(result) == 1
        assert result[0].confidence < 0.5

    def test_empty_inputs(self):
        result = merge_entry_points(
            gitnexus_eps=[], schema_eps=[], convention_eps=[],
        )
        assert result == []

    def test_preserves_route_and_method(self):
        gn = {
            "name": "create_user", "filePath": "routes.py",
            "score": 0.95, "kind": "http_route",
            "route": "/users", "httpMethod": "POST",
        }
        result = merge_entry_points(
            gitnexus_eps=[gn], schema_eps=[], convention_eps=[],
        )
        assert result[0].route == "/users"
        assert result[0].http_method == "POST"
