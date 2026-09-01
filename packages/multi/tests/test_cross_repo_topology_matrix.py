from __future__ import annotations

import json
from pathlib import Path

import pytest

from supernova_core.models.multi_repo_config import (
    CorrelationConfig, MultiRepoConfig, Relation, RepoSpec,
)
from supernova_multi.orchestrator import run_correlation_phase


def _config(relations: list[tuple[str, str, str]]) -> MultiRepoConfig:
    names = sorted({value for relation in relations for value in relation[:2]})
    return MultiRepoConfig(
        repos={
            "admin" if name == "admin" else name: RepoSpec(
                path=f"/r/{name}",
                role="entrypoint" if name in {"web", "admin"} else "backend",
                roles=["entrypoint", "backend"] if name == "web" else (
                    ["entrypoint"] if name == "admin" else ["backend"]),
            )
            for name in names
        },
        relations=[Relation(**{"from": f, "to": t, "protocol": p}) for f, t, p in relations],
        correlation=CorrelationConfig(out_workspace="matrix-corr"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("relations", [
    [("web", "order", "grpc"), ("web", "user", "http")],                         # fan-out
    [("web", "order", "grpc"), ("admin", "order", "graphql"),
     ("admin", "user", "http"), ("web", "user", "grpc")],                       # M:N
    [("web", "order", "grpc"), ("order", "user", "http"), ("user", "web", "grpc")],  # multi-hop/cycle
])
async def test_correlation_phase_preserves_general_directed_graph_matrix(
    tmp_path: Path, monkeypatch, relations: list[tuple[str, str, str]],
):
    config = _config(relations)
    names = set(config.repos)
    repo_workspaces = {name: tmp_path / f"{name}-scan" for name in names}
    for workspace in repo_workspaces.values():
        (workspace / "deliverables").mkdir(parents=True)
    out_workspace = tmp_path / "matrix-out"; out_workspace.mkdir()
    event_file = out_workspace / "events.ndjson"
    seen: list[dict] = []

    async def fake_execute(self, **kwargs):
        payload = json.loads(kwargs["prompt_variables"]["relations_json"])
        seen.append(payload)
        return type("Metrics", (), {"structured_output": {
            **payload, "calls": [], "status": "ok", "boundaries": [], "flows": [],
        }})()

    import supernova_core.agents.executor as executor_mod
    monkeypatch.setattr(executor_mod.AgentExecutor, "execute", fake_execute)
    result = await run_correlation_phase(
        config, repo_workspaces, out_workspace, event_file, write_scan_end=False,
    )
    assert result["edge_statuses"] == ["ok"] * len(relations)
    assert {(item["from"], item["to"], item["protocol"]) for item in seen} == set(relations)
    topology = json.loads(
        (out_workspace / "deliverables" / "cross-service-topology.json").read_text(encoding="utf-8"))
    assert {(edge["from"], edge["to"], edge["protocol"]) for edge in topology["edges"]} == set(relations)
    web = next(service for service in topology["services"] if service["name"] == "web")
    assert web["role"] == "entrypoint"
    assert web["roles"] == ["entrypoint", "backend"]
