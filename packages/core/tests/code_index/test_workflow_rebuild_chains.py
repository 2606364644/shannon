"""Integration test: code_index → adjudication → rebuild_call_chains pipeline.

Tests the full data flow: build code_index with empty chains,
write entry_points.json with adjudication results, then rebuild
chains from confirmed entry points only.
"""

import json
from pathlib import Path

import pytest

from shannon_core.code_index import rebuild_call_chains
from shannon_core.code_index.models import (
    AdjudicatedEntryPoint,
    AdjudicationResult,
    CodeIndex,
    EntryPoint,
    EntryPointSource,
    FuncBlock,
    Verdict,
)


def _block(**overrides) -> FuncBlock:
    defaults = dict(
        id="src/app.py:f:1",
        file_path="src/app.py",
        function_name="f",
        start_line=1,
        end_line=5,
        source_code="def f(): pass",
        parameters=[],
        language="python",
    )
    defaults.update(overrides)
    return FuncBlock(**defaults)


class TestEndToEndPipeline:
    def test_full_pipeline_deferred_then_rebuilt(self, tmp_path):
        """Simulate the full pipeline: code_index → adjudicate → rebuild."""
        deliverables = tmp_path / "deliverables"
        deliverables.mkdir()

        # Step 1: Code index produces empty chains
        handler = _block(
            id="src/app.py:handler:1",
            function_name="handler",
            source_code="def handler(): process()",
        )
        process = _block(
            id="src/app.py:process:10",
            function_name="process",
            source_code="def process(): db_call()",
        )
        helper = _block(
            id="src/app.py:helper:20",
            function_name="helper",
            source_code="async def helper(): pass",
        )
        index = CodeIndex(
            repository=str(tmp_path),
            language="python",
            total_blocks=3,
            total_entry_points=2,
            total_chains=0,  # Deferred!
            blocks=[handler, process, helper],
            edges=[],
            entry_points=[
                EntryPoint(
                    func_block_id="src/app.py:handler:1",
                    entry_type="http_route",
                    confidence=0.95,
                    evidence="@app.route",
                    needs_llm_review=False,
                ),
                EntryPoint(
                    func_block_id="src/app.py:helper:20",
                    entry_type="unknown",
                    confidence=0.40,
                    evidence="async def catch-all",
                    needs_llm_review=True,
                ),
            ],
            chains=[],  # Deferred!
        )
        (deliverables / "code_index.json").write_text(index.model_dump_json(indent=2))
        assert index.total_chains == 0

        # Step 2: PRE_RECON adjudicates and writes entry_points.json
        adjudication = AdjudicationResult(
            repository=str(tmp_path),
            language="python",
            adjudicated_entry_points=[
                AdjudicatedEntryPoint(
                    func_block_id="src/app.py:handler:1",
                    verdict=Verdict.CONFIRMED,
                    entry_type="http_route",
                    evidence="Confirmed Flask route",
                    source=EntryPointSource.CODE_INDEX,
                ),
                AdjudicatedEntryPoint(
                    func_block_id="src/app.py:helper:20",
                    verdict=Verdict.REJECTED,
                    entry_type="unknown",
                    evidence="Internal async helper, not exposed",
                    source=EntryPointSource.CODE_INDEX,
                ),
            ],
        )
        (deliverables / "entry_points.json").write_text(adjudication.model_dump_json(indent=2))

        # Step 3: Rebuild call chains from confirmed only
        updated = rebuild_call_chains(str(deliverables))
        assert updated.total_chains >= 1

        # Only handler chains, no helper chains
        for chain in updated.chains:
            assert chain.entry_point_id == "src/app.py:handler:1"

        # Verify disk state
        disk = json.loads((deliverables / "code_index.json").read_text())
        assert disk["total_chains"] >= 1

    def test_pipeline_with_llm_supplement(self, tmp_path):
        """PRE_RECON discovers an entry point code_index missed."""
        deliverables = tmp_path / "deliverables"
        deliverables.mkdir()

        handler = _block(
            id="src/routes.py:dynamic:1",
            function_name="dynamic",
        )
        index = CodeIndex(
            repository=str(tmp_path),
            language="python",
            total_blocks=1,
            total_entry_points=0,
            total_chains=0,
            blocks=[handler],
            edges=[],
            entry_points=[],
            chains=[],
        )
        (deliverables / "code_index.json").write_text(index.model_dump_json(indent=2))

        # LLM discovered a route in routes.yaml that maps to dynamic()
        adjudication = AdjudicationResult(
            repository=str(tmp_path),
            language="python",
            adjudicated_entry_points=[
                AdjudicatedEntryPoint(
                    func_block_id="src/routes.py:dynamic:1",
                    verdict=Verdict.CONFIRMED,
                    entry_type="http_route",
                    route="/dynamic-endpoint",
                    evidence="Found in routes.yaml config",
                    source=EntryPointSource.LLM_DISCOVERY,
                ),
            ],
        )
        (deliverables / "entry_points.json").write_text(adjudication.model_dump_json(indent=2))

        updated = rebuild_call_chains(str(deliverables))
        assert updated.total_chains >= 1

    @pytest.mark.skipif(
        True,  # shannon_whitebox not installed in test environment
        reason="shannon_whitebox module not available in core test environment"
    )
    def test_workflow_code_contains_rebuild_step(self):
        """Verify the workflow source code has rebuild_call_chains after PRE_RECON."""
        import inspect
        from shannon_whitebox.pipeline.workflows import WhiteboxScanWorkflow

        source = inspect.getsource(WhiteboxScanWorkflow.run)
        assert "run_rebuild_call_chains" in source
        # rebuild must execute BEFORE marking pre-recon complete
        rebuild_idx = source.index("run_rebuild_call_chains")
        append_idx = source.index("completed_agents.append")
        assert rebuild_idx < append_idx, (
            "rebuild_call_chains should execute before marking pre-recon complete"
        )
