from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from supernova_core.agents.runner import ClaudeRunResult, TokenUsage
from supernova_web.components.topology_analysis import (
    AnalysisNotFound,
    TooManyTopologyAnalyses,
    TopologyAnalysisManager,
    TopologyAnalysisStore,
    TopologyValidationError,
)


def _make_repos(root: Path, ws: str = "ws1") -> dict[str, Path]:
    out: dict[str, Path] = {}
    for name in ("gateway", "order-svc", "user-svc", "slow"):
        path = root / "workspaces" / ws / "repos" / name
        (path / ".git").mkdir(parents=True, exist_ok=True)
        (path / "README.md").write_text(f"# {name}\n", encoding="utf-8")
        out[name] = path
    return out


def _result(payload: dict | None = None, *, success: bool = True, error: str | None = None) -> ClaudeRunResult:
    return ClaudeRunResult(
        success=success,
        text="" if payload is not None else "not json",
        structured_output=payload,
        turns=3,
        cost=0.125,
        cost_currency="CNY",
        model="glm-test",
        tokens=TokenUsage(input_tokens=11, output_tokens=7, cache_read_input_tokens=3),
        error=error,
    )


def _payload() -> dict:
    return {
        "nodes": [{"repo": "gateway", "roles": ["entrypoint", "backend"]}],
        "edges": [{
            "from": "gateway", "to": "order-svc", "protocol": "grpc", "confidence": "medium",
            "client_evidence": [], "handler_evidence": [],
        }],
        "uncertain": [],
        "coverage": [{"repo": name, "complete": True, "reason": "test"} for name in ("gateway", "order-svc", "user-svc")],
    }


@pytest.mark.asyncio
async def test_store_atomic_cache_recovery_and_cleanup(tmp_path):
    store = TopologyAnalysisStore(tmp_path / "workspaces")
    state = store.create("ws1", {
        "analysis_id": "topology-aaaaaaaaaaaa", "workspace": "ws1", "status": "running",
        "repos": ["a", "b"], "fingerprint": "f1", "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z", "progress": 10,
    })
    assert (tmp_path / "workspaces" / "ws1" / "correlation-topology" / "analyses" / "topology-aaaaaaaaaaaa" / "state.json").exists()
    assert store.get("ws1", "topology-aaaaaaaaaaaa") == state
    assert not list((tmp_path / "workspaces" / "ws1" / "correlation-topology" / "analyses" / "topology-aaaaaaaaaaaa").glob("*.tmp"))

    completed = dict(state, status="completed", updated_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    store.write(completed)
    assert store.find_cached("ws1", "f1", ttl_seconds=3600)["analysis_id"] == "topology-aaaaaaaaaaaa"
    assert store.find_cached("ws1", "f1", ttl_seconds=0) is None

    interrupted = store.recover_interrupted()
    # completed stays completed; now create a running orphan and recover it
    store.write(dict(state, analysis_id="topology-bbbbbbbbbbbb", status="queued", fingerprint="f2"))
    assert store.recover_interrupted() == ["topology-bbbbbbbbbbbb"]
    assert store.get("ws1", "topology-bbbbbbbbbbbb")["status"] == "interrupted"

    for i in range(12):
        store.write(dict(state, analysis_id=f"topology-{i:012x}", status="completed", fingerprint=f"f{i}"))
    store.cleanup(max_records=10)
    assert len(store.list("ws1")) == 10


@pytest.mark.asyncio
async def test_manager_lifecycle_cache_failure_timeout_and_cancel(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERNOVA_TOPOLOGY_TIMEOUT_SECONDS", "0.02")
    repos = _make_repos(tmp_path)
    calls: list[dict] = []
    release = asyncio.Event()

    async def runner(**kwargs):
        calls.append(kwargs)
        if "slow" in kwargs["prompt"]:
            kwargs["usage_sink"].record(
                model="glm-test", input_tokens=5, output_tokens=3,
                cache_read_tokens=1, cache_creation_tokens=0,
                cost_usd=0.07, cost_currency="CNY")
            await release.wait()
        return _result(_payload())

    manager = TopologyAnalysisManager(tmp_path / "workspaces", repo_manager=None, runner=runner)
    analysis_id = await manager.start("ws1", ["gateway", "order-svc", "user-svc"])
    await manager.wait(analysis_id)
    completed = manager.get("ws1", analysis_id)
    assert completed["status"] == "completed"
    assert completed["repos"] == ["gateway", "order-svc", "user-svc"]
    assert completed["result"]["edges"][0]["from"] == "gateway"
    assert completed["usage"] == {
        "input_tokens": 11, "output_tokens": 7, "cache_read_tokens": 3,
        "cache_creation_tokens": 0, "cost_usd": 0.125,
        "cost_currency": "CNY", "model": "glm-test", "turns": 3,
    }
    assert completed["manifest"]["repositories"] == ["gateway", "order-svc", "user-svc"]
    assert calls[0]["tool_policy"] == "readonly-code"
    assert calls[0]["structured_output_schema"]["title"] == "CrossRepositoryTopologyDiscovery"
    assert calls[0]["repo_path"].endswith(analysis_id)
    assert calls[0]["repo_path"] != str(tmp_path / "workspaces" / "ws1")
    audit = (tmp_path / "workspaces" / "ws1" / "correlation-topology" / "analyses" / analysis_id / "tool-audit.ndjson")
    assert audit.exists()

    cached_id = await manager.start("ws1", ["user-svc", "gateway", "order-svc"])
    assert cached_id == analysis_id and len(calls) == 1
    refreshed_id = await manager.start("ws1", ["gateway", "order-svc", "user-svc"], refresh=True)
    await manager.wait(refreshed_id)
    assert refreshed_id != analysis_id and len(calls) == 2

    drained_id = await manager.start("ws1", ["gateway", "order-svc"], refresh=True)
    await manager.wait(drained_id)
    async def failing(**kwargs):
        calls.append(kwargs)
        return _result(None, success=False, error="provider down")
    manager._runner = failing
    failed_id = await manager.start("ws1", ["gateway", "order-svc"], refresh=True)
    await manager.wait(failed_id)
    failed = manager.get("ws1", failed_id)
    assert failed["status"] == "failed" and failed["error"]["code"] == "provider_failed"

    async def timeout(**kwargs):
        calls.append(kwargs)
        await asyncio.sleep(1)
        return _result(_payload())
    manager._runner = timeout
    timed_id = await manager.start("ws1", ["gateway", "order-svc"], refresh=True)
    await manager.wait(timed_id)
    assert manager.get("ws1", timed_id)["status"] == "failed"

    manager._runner = runner
    slow_id = await manager.start("ws1", ["gateway", "order-svc", "slow"], refresh=True)
    await asyncio.sleep(0)
    await manager.cancel("ws1", slow_id)
    release.set()
    await manager.wait(slow_id)
    cancelled = manager.get("ws1", slow_id)
    assert cancelled["status"] == "cancelled"
    assert cancelled["usage"]["input_tokens"] == 5
    assert cancelled["usage"]["cost_usd"] == 0.07
    assert cancelled["usage"]["cost_currency"] == "CNY"

    with pytest.raises(TopologyValidationError):
        await manager.start("ws1", ["gateway"])
    with pytest.raises(TopologyValidationError):
        await manager.start("ws1", ["gateway", "missing"])


@pytest.mark.asyncio
async def test_manager_restart_recovery_and_concurrency(tmp_path):
    repos = _make_repos(tmp_path)
    store = TopologyAnalysisStore(tmp_path / "workspaces")
    store.create("ws1", {
        "analysis_id": "topology-cccccccccccc", "workspace": "ws1", "status": "running",
        "repos": ["gateway", "order-svc"], "fingerprint": "f",
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
    })
    manager = TopologyAnalysisManager(tmp_path / "workspaces", repo_manager=None, runner=None)
    assert manager.get("ws1", "topology-cccccccccccc")["status"] == "interrupted"

    manager._active_count = 1
    with pytest.raises(TooManyTopologyAnalyses):
        await manager.start("ws1", ["gateway", "order-svc"])


@pytest.mark.asyncio
async def test_api_lifecycle_errors_auth_and_no_scan_side_effects(authed_client, tmp_path, monkeypatch):
    app = authed_client.app
    _make_repos(tmp_path)
    calls = 0

    class Manager(TopologyAnalysisManager):
        async def _resolve_repo_path(self, ws: str, name: str) -> Path:
            return tmp_path / "workspaces" / ws / "repos" / name

        async def _run_agent(self, analysis_id: str):
            nonlocal calls
            calls += 1
            return _result(_payload())

    app.state.topology_manager = Manager(tmp_path / "workspaces", repo_manager=app.state.repo_manager, runner=None)
    csrf = authed_client.get("/api/auth/csrf").json()["csrf_token"]
    created = authed_client.post(
        "/api/workspaces/ws1/correlation-topology/analyses",
        json={"repos": ["gateway", "order-svc", "user-svc"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 202
    analysis_id = created.json()["analysis_id"]
    state = authed_client.get(f"/api/workspaces/ws1/correlation-topology/analyses/{analysis_id}").json()
    assert state["status"] == "completed"
    assert "manifest" not in state
    assert "repo_paths" not in state
    assert "raw_output" not in state
    assert state["result"]["raw"] is None
    assert all(item["raw"] == {} for item in state["result"].get("invalid", []))
    assert not (tmp_path / "workspaces" / "ws1" / "scans").exists()

    bad = authed_client.post(
        "/api/workspaces/ws1/correlation-topology/analyses",
        json={"repos": ["gateway"]}, headers={"X-CSRF-Token": csrf},
    )
    assert bad.status_code == 422
    assert bad.json()["detail"]["code"] == "invalid_repositories"
    assert authed_client.get("/api/workspaces/ws1/correlation-topology/analyses/missing").status_code == 404
    cancelled = authed_client.delete(
        f"/api/workspaces/ws1/correlation-topology/analyses/{analysis_id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert cancelled.status_code == 200
    assert "repo_paths" not in cancelled.json()
    assert "manifest" not in cancelled.json()

    # A non-member ordinary user cannot read another workspace's analysis.
    from supernova_web.auth.passwords import hash_password
    app.state.auth_store.create_user("outsider", hash_password(" outsider-pw"), role="user")
    outsider = TestClient(app)
    token = outsider.get("/api/auth/csrf").json()["csrf_token"]
    outsider.post("/api/auth/login", json={"username": "outsider", "password": " outsider-pw"},
                  headers={"X-CSRF-Token": token})
    response = outsider.get(f"/api/workspaces/ws1/correlation-topology/analyses/{analysis_id}")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_cancel_before_coroutine_start_releases_registry_and_slot(tmp_path):
    _make_repos(tmp_path)
    async def runner(**kwargs):
        return _result(_payload())
    manager = TopologyAnalysisManager(tmp_path / "workspaces", repo_manager=None, runner=runner)
    analysis_id = await manager.start("ws1", ["gateway", "order-svc"])
    task = manager._tasks[analysis_id]
    task.cancel()
    await manager.wait(analysis_id)
    assert manager._active_count == 0
    assert analysis_id not in manager._tasks
    assert analysis_id not in manager._task_ws
    assert analysis_id not in manager._usage_sinks
    next_id = await manager.start("ws1", ["gateway", "order-svc"], refresh=True)
    await manager.wait(next_id)
    assert manager.get("ws1", next_id)["status"] == "completed"
