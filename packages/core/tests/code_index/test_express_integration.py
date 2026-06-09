"""Integration: Express route detection → save_adjudication → rebuild_call_chains."""

import json
from pathlib import Path

from shannon_core.code_index import (
    build_code_index,
    rebuild_call_chains,
    save_adjudication,
    write_index_files,
)
from shannon_core.code_index.models import (
    AdjudicationResult,
    AdjudicatedEntryPoint,
    EntryPointSource,
    Verdict,
)


class TestExpressIntegration:
    def test_full_pipeline_with_func_block_routes(self, tmp_path):
        """Pass 1: Routes inside a function → detect → adjudicate → rebuild chains."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "app.ts").write_text(
            "import express from 'express';\n"
            "const app = express();\n"
            "\n"
            "function setupRoutes(app) {\n"
            "  app.get('/users', (req, res) => {\n"
            "    res.json(listUsers());\n"
            "  });\n"
            "  app.post('/users', (req, res) => {\n"
            "    res.json(createUser());\n"
            "  });\n"
            "}\n"
            "\n"
            "function listUsers(): any[] {\n"
            "  return db.query('SELECT * FROM users');\n"
            "}\n"
            "\n"
            "function createUser(): any {\n"
            "  return db.insert('users');\n"
            "}\n"
        )

        # Step 1: Build code index
        index = build_code_index(str(repo))
        assert index.total_entry_points >= 2, (
            f"Expected >= 2 entry points, got {index.total_entry_points}: "
            f"{[f'{ep.route} {ep.http_method}' for ep in index.entry_points]}"
        )

        express_eps = [ep for ep in index.entry_points
                       if ep.evidence.startswith("Express")]
        assert len(express_eps) >= 2
        methods = {ep.http_method for ep in express_eps}
        assert "GET" in methods
        assert "POST" in methods

        # Step 2: Write deliverables
        deliverables = tmp_path / "deliverables"
        write_index_files(index, str(deliverables))

        # Step 3: Run save_adjudication
        save_adjudication(str(deliverables))
        assert (deliverables / "entry_points.json").exists()

        adjudication = AdjudicationResult.model_validate_json(
            (deliverables / "entry_points.json").read_text()
        )
        assert len(adjudication.adjudicated_entry_points) >= 2
        assert all(
            aep.verdict == Verdict.CONFIRMED
            for aep in adjudication.adjudicated_entry_points
        )

        # Step 4: Rebuild call chains
        updated = rebuild_call_chains(str(deliverables))
        assert updated.total_chains >= 1, (
            f"Expected >= 1 call chain, got {updated.total_chains}"
        )

        # Verify code_index.json was updated on disk
        data = json.loads((deliverables / "code_index.json").read_text())
        assert data["total_chains"] >= 1

    def test_full_pipeline_with_top_level_routes(self, tmp_path):
        """Pass 2: Top-level routes in a routes/ directory → full pipeline."""
        repo = tmp_path / "repo"
        routes_dir = repo / "routes"
        routes_dir.mkdir(parents=True)

        (routes_dir / "api.ts").write_text(
            "import { Router } from 'express';\n"
            "const router = Router();\n"
            "\n"
            "router.get('/health', (req, res) => {\n"
            "  res.json({ ok: true });\n"
            "});\n"
        )

        # Build index — the parser may extract some blocks, Pass 2 handles top-level routes
        index = build_code_index(str(repo))

        # Write + adjudicate + rebuild
        deliverables = tmp_path / "deliverables"
        write_index_files(index, str(deliverables))
        save_adjudication(str(deliverables))
        updated = rebuild_call_chains(str(deliverables))

        # The entry points.json should exist and contain the detected routes
        assert (deliverables / "entry_points.json").exists()
        adjudication = AdjudicationResult.model_validate_json(
            (deliverables / "entry_points.json").read_text()
        )
        assert len(adjudication.adjudicated_entry_points) >= 1

    def test_no_entry_points_graceful(self, tmp_path):
        """Repo with no Express routes → empty but valid pipeline."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "util.ts").write_text(
            "function helper(x: number): number {\n"
            "  return x * 2;\n"
            "}\n"
        )

        index = build_code_index(str(repo))
        express_eps = [ep for ep in index.entry_points
                       if ep.evidence.startswith("Express")]
        assert len(express_eps) == 0

        deliverables = tmp_path / "deliverables"
        write_index_files(index, str(deliverables))
        save_adjudication(str(deliverables))

        adjudication = AdjudicationResult.model_validate_json(
            (deliverables / "entry_points.json").read_text()
        )
        assert len(adjudication.adjudicated_entry_points) == 0

        # rebuild_call_chains should handle empty adjudication gracefully
        updated = rebuild_call_chains(str(deliverables))
        assert updated.total_chains == 0
