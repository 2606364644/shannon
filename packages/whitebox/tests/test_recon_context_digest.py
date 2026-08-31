"""Shared recon-context digest tests.

The digest is an LLM-track artifact derived only from recon_deliverable.md.
It exists so the vuln-agent fan-out no longer summarizes the same recon input
once per agent.
"""
import hashlib
import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from supernova_whitebox.pipeline import activities
from supernova_whitebox.pipeline.shared import ActivityInput


RECON_MD = """# Recon

## 4. API Endpoint Inventory
- GET /foo (role=user)
- POST /bar/:id (role=admin)

## 8. Authorization Candidates
- GET /foo missing horizontal ownership check

## 9. Ignore
"""


class _FakeAccountedClient:
    def __init__(self, result: str | None = "shared digest"):
        self.result = result
        self.calls = 0
        self.finalized = 0

    async def __call__(self, prompt: str) -> str:
        self.calls += 1
        if self.result is None:
            raise RuntimeError("summary unavailable")
        return self.result

    async def finalize(self) -> None:
        self.finalized += 1


def _session() -> MagicMock:
    session = MagicMock()

    @asynccontextmanager
    async def track_step(*args, **kwargs):
        yield

    session.track_step = track_step
    return session


def _input(tmp_path: Path) -> ActivityInput:
    return ActivityInput(repo_path=str(tmp_path), workspace_path=str(tmp_path))


def _setup(tmp_path: Path, recon: str = RECON_MD) -> tuple[Path, _FakeAccountedClient]:
    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    (deliverables / "recon_deliverable.md").write_text(recon, encoding="utf-8")
    client = _FakeAccountedClient()
    return deliverables, client


@pytest.mark.asyncio
async def test_digest_generated_once_then_cache_hit(tmp_path):
    deliverables, client = _setup(tmp_path)
    session = _session()

    with patch.object(activities, "_get_paths", return_value=(tmp_path, deliverables, tmp_path)), \
         patch.object(activities, "ensure_audit_session", AsyncMock()), \
         patch("supernova_whitebox.audit.session_registry.get_audit_session", return_value=session), \
         patch.object(activities, "_make_recon_summary_llm_client", return_value=client):
        first = await activities.run_recon_context_digest(_input(tmp_path))
        second = await activities.run_recon_context_digest(_input(tmp_path))

    assert first == {
        "source": "llm-summary", "cache_hit": False,
        "recon_context_chars": len("shared digest"),
    }
    assert second["cache_hit"] is True
    assert client.calls == 1
    assert client.finalized == 1

    artifact = deliverables / "intermediate" / "recon_context_digest.json"
    data = json.loads(artifact.read_text(encoding="utf-8"))
    assert data["text"] == "shared digest"
    assert data["source"] == "llm-summary"
    assert data["source_hash"] == hashlib.sha256(RECON_MD.encode()).hexdigest()
    assert data["summarizer_prompt_version"] == (
        activities.RECON_CONTEXT_SUMMARIZER_PROMPT_VERSION
    )


@pytest.mark.asyncio
async def test_recon_change_invalidates_digest(tmp_path):
    deliverables, client = _setup(tmp_path)
    session = _session()

    with patch.object(activities, "_get_paths", return_value=(tmp_path, deliverables, tmp_path)), \
         patch.object(activities, "ensure_audit_session", AsyncMock()), \
         patch("supernova_whitebox.audit.session_registry.get_audit_session", return_value=session), \
         patch.object(activities, "_make_recon_summary_llm_client", return_value=client):
        await activities.run_recon_context_digest(_input(tmp_path))
        (deliverables / "recon_deliverable.md").write_text(
            RECON_MD + "\n- POST /new\n", encoding="utf-8")
        result = await activities.run_recon_context_digest(_input(tmp_path))

    assert result["cache_hit"] is False
    assert client.calls == 2


@pytest.mark.asyncio
async def test_llm_failure_writes_degraded_digest_and_can_upgrade(tmp_path):
    deliverables, failing = _setup(tmp_path)
    failing.result = None
    session = _session()

    with patch.object(activities, "_get_paths", return_value=(tmp_path, deliverables, tmp_path)), \
         patch.object(activities, "ensure_audit_session", AsyncMock()), \
         patch("supernova_whitebox.audit.session_registry.get_audit_session", return_value=session), \
         patch.object(activities, "_make_recon_summary_llm_client", return_value=failing):
        degraded = await activities.run_recon_context_digest(_input(tmp_path))

    assert degraded["source"] == "deterministic-extract"
    artifact = json.loads(
        (deliverables / "intermediate" / "recon_context_digest.json").read_text(encoding="utf-8"))
    assert "GET /foo" in artifact["text"]
    assert failing.finalized == 1

    recovered = _FakeAccountedClient("upgraded digest")
    with patch.object(activities, "_get_paths", return_value=(tmp_path, deliverables, tmp_path)), \
         patch.object(activities, "ensure_audit_session", AsyncMock()), \
         patch("supernova_whitebox.audit.session_registry.get_audit_session", return_value=session), \
         patch.object(activities, "_make_recon_summary_llm_client", return_value=recovered):
        upgraded = await activities.run_recon_context_digest(_input(tmp_path))

    assert upgraded["source"] == "llm-summary"
    assert recovered.calls == 1
    upgraded_artifact = json.loads(
        (deliverables / "intermediate" / "recon_context_digest.json").read_text(encoding="utf-8"))
    assert upgraded_artifact["text"] == "upgraded digest"


@pytest.mark.asyncio
async def test_vuln_prompt_builder_reads_digest_without_llm(tmp_path):
    deliverables, _ = _setup(tmp_path)
    digest_path = deliverables / "intermediate" / "recon_context_digest.json"
    digest_path.parent.mkdir(parents=True, exist_ok=True)
    digest_path.write_text(json.dumps({
        "schema_version": 1,
        "source": "llm-summary",
        "source_hash": hashlib.sha256(RECON_MD.encode("utf-8")).hexdigest(),
        "summarizer_prompt_version": activities.RECON_CONTEXT_SUMMARIZER_PROMPT_VERSION,
        "language": "zh",
        "text": "shared context",
    }), encoding="utf-8")

    async def fail_client(*args, **kwargs):
        raise AssertionError("vuln prompt builder must not summarize per agent")

    contexts = []
    with patch.object(activities, "_make_recon_summary_llm_client", side_effect=fail_client):
        for _ in range(5):
            values = await activities._build_vuln_prompt_variables(
                _input(tmp_path), {})
            contexts.append(values["RECON_CONTEXT"])
            assert "FRAMEWORK_ANALYSIS" in values

    assert contexts == ["shared context"] * 5


@pytest.mark.asyncio
async def test_missing_digest_uses_deterministic_extract_without_llm(tmp_path):
    deliverables, _ = _setup(tmp_path)

    async def fail_client(*args, **kwargs):
        raise AssertionError("missing digest must not trigger per-agent LLM summary")

    with patch.object(activities, "_make_recon_summary_llm_client", side_effect=fail_client):
        values = await activities._build_vuln_prompt_variables(
            _input(tmp_path), {})

    assert "GET /foo" in values["RECON_CONTEXT"]
    assert "## 9. Ignore" not in values["RECON_CONTEXT"]
