import json
from pathlib import Path

import pytest

from shannon_core.code_index import save_adjudication
from shannon_core.code_index.models import (
    AdjudicatedEntryPoint,
    AdjudicationResult,
    CodeIndex,
    EntryPoint,
    EntryPointSource,
    FuncBlock,
    Verdict,
)


def _make_block(name: str, file_path: str = "app.ts", start: int = 1) -> FuncBlock:
    return FuncBlock(
        id=f"{file_path}:{name}:{start}",
        file_path=file_path,
        function_name=name,
        start_line=start,
        end_line=start + 5,
        source_code=f"function {name}() {{ }}",
        parameters=[],
        language="typescript",
    )


def _write_index(tmp_path: Path, index: CodeIndex) -> Path:
    d = tmp_path / "deliverables"
    d.mkdir(exist_ok=True)
    (d / "code_index.json").write_text(index.model_dump_json(indent=2))
    return d


class TestSaveAdjudication:
    def test_auto_confirms_high_confidence(self, tmp_path):
        b1 = _make_block("getUsers")
        ep = EntryPoint(
            func_block_id=b1.id,
            entry_type="http_route",
            route="/users",
            http_method="GET",
            confidence=0.90,
            evidence="Express route: app.get('/users')",
            needs_llm_review=False,
        )
        index = CodeIndex(
            repository="test-repo",
            language="typescript",
            total_blocks=1,
            total_entry_points=1,
            total_chains=0,
            blocks=[b1],
            edges=[],
            entry_points=[ep],
            chains=[],
        )

        d = _write_index(tmp_path, index)
        save_adjudication(str(d))

        result = AdjudicationResult.model_validate_json(
            (d / "entry_points.json").read_text()
        )
        assert len(result.adjudicated_entry_points) == 1
        aep = result.adjudicated_entry_points[0]
        assert aep.verdict == Verdict.CONFIRMED
        assert aep.source == EntryPointSource.CODE_INDEX
        assert aep.func_block_id == b1.id
        assert aep.route == "/users"
        assert aep.http_method == "GET"

    def test_rejects_low_confidence(self, tmp_path):
        """Low-confidence entry points (< 0.50) are rejected."""
        b1 = _make_block("handler")
        ep = EntryPoint(
            func_block_id=b1.id,
            entry_type="unknown",
            confidence=0.40,
            evidence="async def with no decorator",
            needs_llm_review=True,
        )
        index = CodeIndex(
            repository="test-repo",
            language="typescript",
            total_blocks=1,
            total_entry_points=1,
            total_chains=0,
            blocks=[b1],
            edges=[],
            entry_points=[ep],
            chains=[],
        )

        d = _write_index(tmp_path, index)
        save_adjudication(str(d))

        result = AdjudicationResult.model_validate_json(
            (d / "entry_points.json").read_text()
        )
        assert len(result.adjudicated_entry_points) == 1
        assert result.adjudicated_entry_points[0].verdict == Verdict.REJECTED

    def test_writes_entry_points_json(self, tmp_path):
        b1 = _make_block("handler")
        ep = EntryPoint(
            func_block_id=b1.id,
            entry_type="http_route",
            confidence=0.95,
            evidence="test",
            needs_llm_review=False,
        )
        index = CodeIndex(
            repository="test-repo",
            language="typescript",
            total_blocks=1,
            total_entry_points=1,
            total_chains=0,
            blocks=[b1],
            edges=[],
            entry_points=[ep],
            chains=[],
        )

        d = _write_index(tmp_path, index)
        assert not (d / "entry_points.json").exists()

        save_adjudication(str(d))

        assert (d / "entry_points.json").exists()
        data = json.loads((d / "entry_points.json").read_text())
        assert "adjudicated_entry_points" in data

    def test_no_entry_points_still_writes(self, tmp_path):
        """Empty entry point list → empty adjudication file."""
        b1 = _make_block("helper")
        index = CodeIndex(
            repository="test-repo",
            language="typescript",
            total_blocks=1,
            total_entry_points=0,
            total_chains=0,
            blocks=[b1],
            edges=[],
            entry_points=[],
            chains=[],
        )

        d = _write_index(tmp_path, index)
        save_adjudication(str(d))

        result = AdjudicationResult.model_validate_json(
            (d / "entry_points.json").read_text()
        )
        assert len(result.adjudicated_entry_points) == 0

    def test_skips_when_no_code_index(self, tmp_path):
        """Graceful no-op when code_index.json is missing."""
        d = tmp_path / "deliverables"
        d.mkdir()
        # Should not raise
        save_adjudication(str(d))
        assert not (d / "entry_points.json").exists()

    def test_multiple_entry_points(self, tmp_path):
        b1 = _make_block("getHandler", start=1)
        b2 = _make_block("postHandler", start=10)
        ep1 = EntryPoint(
            func_block_id=b1.id,
            entry_type="http_route",
            route="/users",
            http_method="GET",
            confidence=0.90,
            evidence="Express route: app.get('/users')",
            needs_llm_review=False,
        )
        ep2 = EntryPoint(
            func_block_id=b2.id,
            entry_type="http_route",
            route="/users",
            http_method="POST",
            confidence=0.90,
            evidence="Express route: app.post('/users')",
            needs_llm_review=False,
        )
        index = CodeIndex(
            repository="test-repo",
            language="typescript",
            total_blocks=2,
            total_entry_points=2,
            total_chains=0,
            blocks=[b1, b2],
            edges=[],
            entry_points=[ep1, ep2],
            chains=[],
        )

        d = _write_index(tmp_path, index)
        save_adjudication(str(d))

        result = AdjudicationResult.model_validate_json(
            (d / "entry_points.json").read_text()
        )
        assert len(result.adjudicated_entry_points) == 2
        methods = {aep.http_method for aep in result.adjudicated_entry_points}
        assert methods == {"GET", "POST"}
