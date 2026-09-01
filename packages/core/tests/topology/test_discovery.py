from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from supernova_core.models.multi_repo_config import MultiRepoConfig, RepoSpec
from supernova_core.models.topology import (
    NavigationManifestLimits,
    TopologyAnalysisRequest,
    TopologyFingerprint,
    TopologyUsage,
)
from supernova_core.topology.discovery import (
    build_topology_fingerprint,
    collect_navigation_manifest,
    normalize_topology_result,
)


def _repo(name: str, path: Path) -> dict:
    return {"path": str(path), "roles": ["entrypoint", "backend"]}


def test_repo_spec_roles_are_backward_compatible_and_effective():
    legacy = RepoSpec(path="/repos/a", role="entrypoint")
    dual = RepoSpec(path="/repos/a", roles=["entrypoint", "backend"])
    only_new = RepoSpec(path="/repos/a", roles=["entrypoint"])

    assert legacy.roles == ["entrypoint"]
    assert legacy.effective_roles == {"entrypoint"}
    assert dual.role == "entrypoint"
    assert dual.effective_roles == {"entrypoint", "backend"}
    assert only_new.role == "entrypoint"
    with pytest.raises(ValidationError):
        RepoSpec(path="/repos/a", roles=["database"])


def test_multi_repo_config_accepts_dual_roles_and_retains_general_graph():
    config = MultiRepoConfig.model_validate({
        "repos": {
            "gateway": {"path": "/repos/gateway", "roles": ["entrypoint", "backend"]},
            "order": {"path": "/repos/order", "roles": ["backend"]},
            "user": {"path": "/repos/user", "roles": ["backend"]},
        },
        "relations": [
            {"from": "gateway", "to": "order", "protocol": "grpc"},
            {"from": "gateway", "to": "user", "protocol": "http"},
            {"from": "order", "to": "user", "protocol": "grpc"},
        ],
        "correlation": {"out_workspace": "out"},
    })

    assert config.repos["gateway"].effective_roles == {"entrypoint", "backend"}
    assert [(r.from_, r.to, r.protocol) for r in config.relations] == [
        ("gateway", "order", "grpc"),
        ("gateway", "user", "http"),
        ("order", "user", "grpc"),
    ]


def test_analysis_request_deduplicates_and_rejects_bad_names():
    request = TopologyAnalysisRequest(repos=["b", "a", "b"])
    assert request.repos == ["a", "b"]
    with pytest.raises(ValidationError):
        TopologyAnalysisRequest(repos=["a", "../escape", "a"])


def test_manifest_collects_language_framework_and_proto_clues_and_ignores_dependencies(tmp_path):
    web = tmp_path / "web"
    order = tmp_path / "order-svc"
    web.mkdir()
    order.mkdir()
    (web / "package.json").write_text(json.dumps({
        "name": "web", "dependencies": {"next": "^15", "axios": "^1"}
    }), encoding="utf-8")
    (web / "app").mkdir()
    (web / "app" / "route.ts").write_text(
        'export async function GET() { return axios.post(process.env.ORDER_URL, {}); }\n',
        encoding="utf-8")
    (order / "order.proto").write_text(
        'syntax = "proto3";\npackage order.v1;\nservice OrderService { rpc CreateOrder(Request) returns (Response); }\n',
        encoding="utf-8")
    (order / "server.py").write_text(
        'from fastapi import FastAPI\napp = FastAPI()\n@app.post("/orders")\ndef create(): pass\n',
        encoding="utf-8")
    (order / "node_modules").mkdir()
    (order / "node_modules" / "skip.js").write_text("service ShouldNotAppear", encoding="utf-8")

    manifest = collect_navigation_manifest({"web": web, "order-svc": order})
    assert set(manifest.repositories) == {"web", "order-svc"}
    assert manifest.approximate_size > 0
    assert manifest.by_repo["web"].language == "typescript"
    assert {"next", "axios"} <= set(manifest.by_repo["web"].frameworks)
    assert any(c.kind == "http-client" and "route.ts" in c.path for c in manifest.by_repo["web"].client_clues)
    assert manifest.by_repo["order-svc"].language == "python"
    assert {"fastapi"} <= set(manifest.by_repo["order-svc"].frameworks)
    assert any(c.kind == "proto-service" and c.value == "OrderService" for c in manifest.by_repo["order-svc"].service_clues)
    assert all("node_modules" not in c.path for repo in manifest.by_repo.values() for c in repo.all_clues())


def test_manifest_limits_are_bounded(tmp_path):
    repo = tmp_path / "large"
    repo.mkdir()
    for i in range(20):
        (repo / f"file{i}.ts").write_text("service Ignored\n", encoding="utf-8")

    manifest = collect_navigation_manifest(
        {"large": repo}, limits=NavigationManifestLimits(max_files=5, max_output_chars=2000)
    )
    assert manifest.limits.max_files == 5
    assert manifest.by_repo["large"].scanned_files == 5
    assert manifest.by_repo["large"].truncated
    assert manifest.approximate_size <= 2000


def test_normalizer_retains_mn_edges_and_validates_evidence(tmp_path):
    web = tmp_path / "web"; order = tmp_path / "order"; user = tmp_path / "user"
    for p in (web, order, user): p.mkdir()
    client = web / "client.ts"; client.write_text("new OrderClient()\n", encoding="utf-8")
    handler = order / "server.py"; handler.write_text("def create_order(): pass\n", encoding="utf-8")
    missing = web / "missing.ts"
    common = {
        "nodes": [
            {"repo": "web", "roles": ["entrypoint"], "capabilities": [
                {"role": "entrypoint", "confidence": "high", "evidence": [
                    {"repo": "web", "file": "client.ts", "line": 1, "snippet": "OrderClient"}
                ]}
            ]},
            {"repo": "order", "roles": ["entrypoint", "backend"]},
            {"repo": "user", "roles": ["backend"]},
            {"repo": "ghost", "roles": ["backend"]},
        ],
        "edges": [
            {
                "from": "web", "to": "order", "protocol": "grpc", "confidence": "high",
                "service": "OrderService", "method": "CreateOrder",
                "client_evidence": [{"repo": "web", "file": "client.ts", "line": 1, "snippet": "OrderClient"}],
                "handler_evidence": [{"repo": "order", "file": "server.py", "line": 1, "snippet": "create_order"}],
            },
            {
                "from": "web", "to": "order", "protocol": "grpc", "confidence": "low",
                "service": "OrderService", "method": "CreateOrder",
                "client_evidence": [], "handler_evidence": [],
            },
            {
                "from": "admin", "to": "user", "protocol": "http", "confidence": "high",
                "client_evidence": [], "handler_evidence": [],
            },
            {"from": "web", "to": "web", "protocol": "http", "confidence": "high"},
            {
                "from": "order", "to": "user", "protocol": "thrift", "confidence": "medium",
                "client_evidence": [{"repo": "order", "file": "../escape", "line": 1, "snippet": "x"}],
            },
            {
                "from": "web", "to": "user", "protocol": "graphql", "confidence": "high",
                "client_evidence": [{"repo": "web", "file": str(missing), "line": 99, "snippet": "x"}],
            },
        ],
        "uncertain": [{"repo": "order", "message": "possible Dubbo", "protocol_hint": "dubbo"}],
        "coverage": [{"repo": "web", "complete": True, "reason": "routes and clients checked"}],
    }

    result = normalize_topology_result(common, {"web": web, "order": order, "user": user})

    assert [(e.from_, e.to, e.protocol) for e in result.edges] == [("web", "order", "grpc"), ("web", "user", "graphql")]
    assert result.edges[0].confidence == "high"
    assert result.edges[1].confidence == "low"
    assert all(e.client_evidence[0].valid for e in result.edges[:1])
    assert result.edges[1].client_evidence[0].valid is False
    web_node = next(node for node in result.nodes if node.repo == "web")
    capability = web_node.capabilities[0]
    assert capability.role == "entrypoint"
    assert capability.confidence == "high"
    assert capability.evidence[0].valid is True
    assert {x.reason for x in result.invalid} >= {
        "unknown_node", "self_loop", "invalid_protocol", "invalid_evidence"
    }
    assert any(u.protocol_hint == "thrift" for u in result.uncertain)
    assert any(u.protocol_hint == "dubbo" for u in result.uncertain)
    assert {c.repo for c in result.coverage} == {"web", "order", "user"}


def test_usage_model_defaults_and_serialization():
    usage = TopologyUsage(input_tokens=10, output_tokens=5, cost_usd=0.25, cost_currency="CNY", turns=2)
    assert usage.total_tokens == 15
    assert TopologyUsage().cost_currency == "USD"


def test_fingerprint_uses_git_head_dirty_and_bounded_non_git_fallback(tmp_path):
    git_repo = tmp_path / "git"; git_repo.mkdir()
    (git_repo / "tracked.txt").write_text("stable\n", encoding="utf-8")
    subprocess = pytest.importorskip("subprocess")
    assert subprocess.run(["git", "init"], cwd=git_repo, check=True,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=git_repo, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.name", "T"], cwd=git_repo, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "init"], cwd=git_repo, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    plain = tmp_path / "plain"; plain.mkdir()
    (plain / "a.txt").write_text("a", encoding="utf-8")

    clean = build_topology_fingerprint({"git": git_repo, "plain": plain})
    assert clean.value == build_topology_fingerprint({"git": git_repo, "plain": plain}).value
    (git_repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    dirty = build_topology_fingerprint({"git": git_repo, "plain": plain})

    assert isinstance(clean, TopologyFingerprint)
    assert clean.value != dirty.value
    assert clean.repos["git"].git_head
    assert dirty.repos["git"].dirty is True
    assert clean.repos["plain"].git_head is None
