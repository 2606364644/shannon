# packages/whitebox/tests/test_run_authz_gitnexus_judge.py
import json
from unittest.mock import AsyncMock, patch

import pytest

from shannon_whitebox.pipeline import activities


class _FakeInput:
    def __init__(self, tmp_path):
        self.agent_name = None
        self.web_url = None
        self.repo_path = str(tmp_path)
        self.deliverables_subdir = None
        self.workspace_name = None
        self.config_path = None
        self.api_key = None
        self.pipeline_testing_mode = False
        self.prompt_override = None


def _write_index_with_candidate(tmp_path):
    handler = {"id": "u.js:update:10", "file_path": "u.js", "function_name": "update",
               "start_line": 10, "end_line": 13,
               "source_code": "async function update(req){ await repo.update(req.params.id); }",
               "parameters": [], "decorators": [], "language": "typescript"}
    sink = {"id": "repo.js:update:1", "file_path": "repo.js", "function_name": "update",
            "start_line": 1, "end_line": 3,
            "source_code": "function update(){ db.user.update(); }",
            "parameters": [], "decorators": [], "language": "typescript"}
    (tmp_path / "code_index.json").write_text(json.dumps({
        "repository": "r", "language": "typescript", "total_blocks": 2,
        "total_entry_points": 1, "total_chains": 1, "blocks": [handler, sink],
        "edges": [],
        "entry_points": [{"func_block_id": "u.js:update:10", "entry_type": "http_route",
                          "route": "/api/u/:id", "http_method": "PUT", "confidence": 0.9,
                          "evidence": "", "needs_llm_review": False,
                          "authentication": "required", "source": "code_index"}],
        "chains": [{"entry_point_id": "u.js:update:10",
                    "path": ["u.js:update:10", "repo.js:update:1"],
                    "depth": 1, "has_unresolved": False}],
    }))


@pytest.mark.asyncio
async def test_judge_writes_gitnexus_queue_from_candidates(tmp_path):
    _write_index_with_candidate(tmp_path)
    captured = {}

    async def fake_run(prompt, **kwargs):
        captured["prompt"] = prompt
        return type("R", (), {
            "success": True, "error": None, "retryable": False, "turns": 1,
            "cost": 0.0, "text": "", "model": "m", "stop_reason": "end",
            "tokens": None,
            "structured_output": {"vulnerabilities": [{
                "ID": "AUTHZ-GN-01", "vulnerability_type": "Horizontal",
                "externally_exploitable": True, "endpoint": "PUT /api/u/:id",
                "vulnerable_code_location": "u.js:update:10",
                "role_context": "user", "guard_evidence": "no ownership check",
                "side_effect": "update any user record", "reason": "no ownership",
                "minimal_witness": "change :id", "confidence": "high",
                "notes": "dominance",
            }]},
        })()

    with patch.object(activities, "_get_paths", return_value=(tmp_path, tmp_path, tmp_path)):
        with patch("shannon_whitebox.pipeline.activities.run_claude_prompt", new=fake_run):
            with patch("shannon_whitebox.audit.session_registry.get_audit_session") as gs:
                inst = gs.return_value
                inst.track_step = _noop_cm_factory()
                result = await activities.run_authz_gitnexus_judge(_FakeInput(tmp_path))

    queue_path = tmp_path / "authz_gitnexus_queue.json"
    assert queue_path.exists()
    data = json.loads(queue_path.read_text())
    assert len(data["vulnerabilities"]) == 1
    v = data["vulnerabilities"][0]
    assert v["externally_exploitable"] is True
    assert v["source_track"] == "gitnexus"
    assert v["evidence_chain"]  # populated from candidate path
    assert result["candidate_count"] >= 1
    # prompt carried candidates
    assert "PUT /api/u/:id" in captured["prompt"]


@pytest.mark.asyncio
async def test_judge_skips_llm_when_no_candidates(tmp_path):
    """No candidates → write empty queue, do NOT call LLM (save cost)."""
    (tmp_path / "code_index.json").write_text(json.dumps({
        "repository": "r", "language": "typescript", "total_blocks": 0,
        "total_entry_points": 0, "total_chains": 0, "blocks": [], "edges": [],
        "entry_points": [], "chains": [],
    }))

    called = {"n": 0}

    async def fake_run(prompt, **kwargs):
        called["n"] += 1
        return type("R", (), {"success": True, "structured_output": {"vulnerabilities": []}})()

    with patch.object(activities, "_get_paths", return_value=(tmp_path, tmp_path, tmp_path)):
        with patch("shannon_whitebox.pipeline.activities.run_claude_prompt", new=fake_run):
            with patch("shannon_whitebox.audit.session_registry.get_audit_session") as gs:
                inst = gs.return_value
                inst.track_step = _noop_cm_factory()
                result = await activities.run_authz_gitnexus_judge(_FakeInput(tmp_path))

    assert called["n"] == 0  # LLM not called
    assert (tmp_path / "authz_gitnexus_queue.json").exists()
    data = json.loads((tmp_path / "authz_gitnexus_queue.json").read_text())
    assert data["vulnerabilities"] == []
    assert result["candidate_count"] == 0


@pytest.mark.asyncio
async def test_judge_lenient_on_invalid_llm_output(tmp_path):
    """LLM returns non-JSON → parse_lenient absorbs, writes empty queue, no crash."""
    _write_index_with_candidate(tmp_path)

    async def fake_run(prompt, **kwargs):
        return type("R", (), {
            "success": True, "structured_output": None,
            "text": "not json", "error": None, "retryable": False, "turns": 1,
            "cost": 0.0, "model": "m", "stop_reason": "end", "tokens": None,
        })()

    with patch.object(activities, "_get_paths", return_value=(tmp_path, tmp_path, tmp_path)):
        with patch("shannon_whitebox.pipeline.activities.run_claude_prompt", new=fake_run):
            with patch("shannon_whitebox.audit.session_registry.get_audit_session") as gs:
                inst = gs.return_value
                inst.track_step = _noop_cm_factory()
                await activities.run_authz_gitnexus_judge(_FakeInput(tmp_path))

    data = json.loads((tmp_path / "authz_gitnexus_queue.json").read_text())
    assert data["vulnerabilities"] == []  # lenient


def _noop_cm_factory():
    class _CM:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
    return lambda *a, **k: _CM()
