import json
from shannon_core.correlation.schemas import (
    CallSite, Call, TopologyEdge, ServiceNode, CrossServiceTopology,
    TrustBoundary, CorrelationResult,
)


def test_topology_serialization_roundtrip():
    topo = CrossServiceTopology(
        services=[ServiceNode(name="gateway", role="entrypoint", repo="/r/gw")],
        edges=[TopologyEdge(
            from_="gateway", to="order-svc", protocol="grpc",
            calls=[Call(method="order.v1.OrderService/CreateOrder",
                        call_site=CallSite(file="src/c.ts", line=42, snippet="client.createOrder(req)"),
                        confidence="high",
                        evidence="POST /orders handler calls CreateOrder")],
            status="ok", error=None,
        )],
    )
    data = json.loads(topo.to_json())
    assert data["services"][0]["role"] == "entrypoint"
    assert data["edges"][0]["calls"][0]["method"] == "order.v1.OrderService/CreateOrder"
    roundtrip = CrossServiceTopology.from_json(topo.to_json())
    assert roundtrip.edges[0].from_ == "gateway"


def test_boundary_serialization():
    b = TrustBoundary(service="order-svc", method="order.v1.OrderService/CreateOrder",
                      exposure="external", reachable_from=["gateway"],
                      reason="via POST /orders", confidence="high")
    data = json.loads(b.to_json())
    assert data["exposure"] == "external"


def test_edge_status_declared_missing():
    e = TopologyEdge(from_="gateway", to="ghost-svc", protocol="grpc",
                     calls=[], status="declared-missing", error=None)
    assert json.loads(e.to_json())["status"] == "declared-missing"
