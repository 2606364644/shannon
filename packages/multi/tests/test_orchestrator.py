import pytest
from shannon_core.models.multi_repo_config import MultiRepoConfig, RepoSpec, Relation, CorrelationConfig
from shannon_multi.orchestrator import plan_repo_scans, RepoScanPlan


def _cfg(**overrides):
    repos = {
        "gateway": RepoSpec(path="/r/gw", role="entrypoint"),
        "order-svc": RepoSpec(path="/r/order", workspace="existing-order", role="backend"),
        "payment-svc": RepoSpec(path="/r/pay", role="backend"),
    }
    return MultiRepoConfig(
        repos=repos,
        relations=[Relation(**{"from": "gateway", "to": "order-svc"})],
        correlation=CorrelationConfig(out_workspace="out"),
        **overrides,
    )


def test_reuse_when_workspace_declared():
    plans = plan_repo_scans(_cfg())
    by_svc = {p.service: p for p in plans}
    # order-svc 声明了 workspace → 复用
    assert by_svc["order-svc"].reuse is True
    assert by_svc["order-svc"].workspace == "existing-order"
    # gateway 只给 path → 现扫
    assert by_svc["gateway"].reuse is False
    assert by_svc["gateway"].repo_path == "/r/gw"


def test_all_three_repos_planned():
    plans = plan_repo_scans(_cfg())
    assert {p.service for p in plans} == {"gateway", "order-svc", "payment-svc"}


# ---------------------------------------------------------------------------
# Task A6: per-edge asyncio + 单边隔离 + merge
# ---------------------------------------------------------------------------
import asyncio  # noqa: E402
from shannon_multi.orchestrator import _run_edge, _merge_edge_results  # noqa: E402


def _edge_result(from_, to, status="ok"):
    return {"from": from_, "to": to, "protocol": "grpc",
            "calls": [], "status": status, "boundaries": []}


@pytest.mark.asyncio
async def test_single_edge_failure_does_not_break_others():
    edges = [("gateway", "order-svc"), ("gateway", "payment-svc"), ("gateway", "broken-svc")]

    async def fake_edge(f, t):
        if t == "broken-svc":
            raise RuntimeError("boom")
        return _edge_result(f, t)

    results = await asyncio.gather(*[_run_edge(f, t, runner=fake_edge) for f, t in edges],
                                   return_exceptions=False)
    statuses = {r["status"] for r in results}
    # 失败的边标 error,其余 ok,不抛
    assert "error" in statuses
    assert "ok" in statuses
    assert len(results) == 3


def test_merge_edges_collects_all():
    merged = _merge_edge_results([_edge_result("g", "a"), _edge_result("g", "b")])
    assert len(merged["edges"]) == 2


def test_write_correlation_deliverables_writes_all_files(tmp_path):
    """Task A6: report.py 落盘 helper 写齐四类产物。"""
    from shannon_core.correlation.report import write_correlation_deliverables
    from shannon_core.correlation.schemas import (
        CrossServiceTopology, ServiceNode, TopologyEdge, TrustBoundary,
    )
    topology = CrossServiceTopology(
        services=[ServiceNode(name="gateway", role="entrypoint", repo="/r/gw")],
        edges=[TopologyEdge(from_="gateway", to="order-svc", protocol="grpc")],
    )
    boundaries = [TrustBoundary(service="gateway", method="Checkout",
                                exposure="public", reachable_from=["*"],
                                reason="rbac", confidence="high")]
    merged_queues = {"injection": [
        {"title": "t", "description": "d", "severity": "high",
         "location": "f:1", "service": "gateway"}]}
    out = tmp_path / "deliverables"
    write_correlation_deliverables(out, topology, boundaries, merged_queues, "# report")
    assert (out / "cross-service-topology.json").exists()
    assert (out / "trust-boundaries.json").exists()
    assert (out / "correlation-report.md").read_text() == "# report"
    assert (out / "injection_exploitation_queue.json").exists()
