import json
import pytest
from pathlib import Path

from shannon_core.code_index import rebuild_call_chains
from shannon_core.code_index.models import (
    AdjudicationResult,
    AdjudicatedEntryPoint,
    CallChain,
    CallEdge,
    CodeIndex,
    EntryPoint,
    EntryPointSource,
    FuncBlock,
    Verdict,
)


def _make_block(name: str, file_path: str = "app.py", start: int = 1) -> FuncBlock:
    return FuncBlock(
        id=f"{file_path}:{name}:{start}",
        file_path=file_path,
        function_name=name,
        start_line=start,
        end_line=start + 5,
        source_code=f"def {name}(): pass",
        parameters=[],
        language="python",
    )


def _make_index(blocks, edges, entry_points) -> CodeIndex:
    return CodeIndex(
        repository="test-repo",
        language="python",
        total_blocks=len(blocks),
        total_entry_points=len(entry_points),
        total_chains=0,
        blocks=blocks,
        edges=edges,
        entry_points=entry_points,
        chains=[],
    )


def _write_deliverables(tmp_path, index, adjudication=None):
    d = tmp_path / "deliverables"
    d.mkdir()
    (d / "code_index.json").write_text(index.model_dump_json(indent=2))
    if adjudication:
        (d / "entry_points.json").write_text(adjudication.model_dump_json(indent=2))
    return d


class TestRebuildCallChains:
    def test_builds_chains_from_confirmed_only(self, tmp_path):
        b1 = _make_block("handler", start=1)
        b2 = _make_block("svc", start=10)
        edge = CallEdge(
            caller_id=b1.id, callee_name="svc",
            callee_file="app.py", resolved=True, line=3,
        )
        ep1 = EntryPoint(
            func_block_id=b1.id, entry_type="http_route",
            confidence=0.95, evidence="decorated", needs_llm_review=False,
        )
        ep2 = EntryPoint(
            func_block_id=b2.id, entry_type="unknown",
            confidence=0.40, evidence="async def", needs_llm_review=True,
        )
        index = _make_index([b1, b2], [edge], [ep1, ep2])

        adjudication = AdjudicationResult(
            repository="test-repo",
            language="python",
            adjudicated_entry_points=[
                AdjudicatedEntryPoint(
                    func_block_id=b1.id, verdict=Verdict.CONFIRMED,
                    entry_type="http_route", evidence="confirmed",
                    source=EntryPointSource.CODE_INDEX,
                ),
                AdjudicatedEntryPoint(
                    func_block_id=b2.id, verdict=Verdict.REJECTED,
                    entry_type="unknown", evidence="utility function",
                    source=EntryPointSource.CODE_INDEX,
                ),
            ],
        )

        d = _write_deliverables(tmp_path, index, adjudication)
        updated = rebuild_call_chains(str(d))

        assert updated.total_chains >= 1
        assert all(c.entry_point_id == b1.id for c in updated.chains)
        assert not any(c.entry_point_id == b2.id for c in updated.chains)

    def test_no_entry_points_json_skips_rebuild(self, tmp_path):
        b1 = _make_block("handler")
        index = _make_index([b1], [], [])

        d = _write_deliverables(tmp_path, index)
        updated = rebuild_call_chains(str(d))

        assert updated.total_chains == 0

    def test_unresolved_llm_discovery_skipped(self, tmp_path):
        b1 = _make_block("handler")
        edge = CallEdge(
            caller_id=b1.id, callee_name="missing",
            resolved=False, line=3,
        )
        ep = EntryPoint(
            func_block_id=b1.id, entry_type="http_route",
            confidence=0.95, evidence="decorated", needs_llm_review=False,
        )
        index = _make_index([b1], [edge], [ep])

        adjudication = AdjudicationResult(
            repository="test-repo",
            language="python",
            adjudicated_entry_points=[
                AdjudicatedEntryPoint(
                    func_block_id=b1.id, verdict=Verdict.CONFIRMED,
                    entry_type="http_route", evidence="confirmed",
                    source=EntryPointSource.CODE_INDEX,
                ),
                AdjudicatedEntryPoint(
                    func_block_id="nonexistent.py:ghost:99",
                    verdict=Verdict.CONFIRMED,
                    entry_type="http_route",
                    evidence="discovered in config",
                    source=EntryPointSource.LLM_DISCOVERY,
                ),
            ],
        )

        d = _write_deliverables(tmp_path, index, adjudication)
        updated = rebuild_call_chains(str(d))

        confirmed_ids = [c.entry_point_id for c in updated.chains]
        assert "nonexistent.py:ghost:99" not in confirmed_ids

    def test_updates_code_index_json_on_disk(self, tmp_path):
        b1 = _make_block("handler")
        ep = EntryPoint(
            func_block_id=b1.id, entry_type="http_route",
            confidence=0.95, evidence="decorated", needs_llm_review=False,
        )
        index = _make_index([b1], [], [ep])

        adjudication = AdjudicationResult(
            repository="test-repo",
            language="python",
            adjudicated_entry_points=[
                AdjudicatedEntryPoint(
                    func_block_id=b1.id, verdict=Verdict.CONFIRMED,
                    entry_type="http_route", evidence="confirmed",
                    source=EntryPointSource.CODE_INDEX,
                ),
            ],
        )

        d = _write_deliverables(tmp_path, index, adjudication)
        rebuild_call_chains(str(d))

        data = json.loads((d / "code_index.json").read_text())
        assert data["total_chains"] >= 0

    def test_raises_when_no_code_index(self, tmp_path):
        d = tmp_path / "empty_deliverables"
        d.mkdir()

        from shannon_core.models.errors import PentestError
        with pytest.raises(PentestError, match="code_index.json not found"):
            rebuild_call_chains(str(d))


class TestRebuildIntegration:
    def test_build_then_rebuild_flow(self, tmp_path):
        """Integration: build_code_index → write → rebuild_call_chains."""
        from shannon_core.code_index import build_code_index, write_index_files

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "app.py").write_text(
            'from flask import Flask\n'
            'app = Flask(__name__)\n'
            '\n'
            '@app.route("/hello")\n'
            'def hello():\n'
            '    return greet("world")\n'
            '\n'
            'def greet(name):\n'
            '    return f"Hello {name}"\n'
        )

        index = build_code_index(str(repo))
        assert index.total_chains == 0
        assert index.chains == []

        d = tmp_path / "deliverables"
        write_index_files(index, str(d))

        ep_block_id = index.entry_points[0].func_block_id
        adjudication = AdjudicationResult(
            repository=index.repository,
            language=index.language,
            adjudicated_entry_points=[
                AdjudicatedEntryPoint(
                    func_block_id=ep_block_id,
                    verdict=Verdict.CONFIRMED,
                    entry_type="http_route",
                    evidence="confirmed",
                    source=EntryPointSource.CODE_INDEX,
                ),
            ],
        )
        (d / "entry_points.json").write_text(adjudication.model_dump_json(indent=2))

        updated = rebuild_call_chains(str(d))
        assert updated.total_chains >= 1
        assert len(updated.chains) >= 1

        data = json.loads((d / "code_index.json").read_text())
        assert data["total_chains"] >= 1
