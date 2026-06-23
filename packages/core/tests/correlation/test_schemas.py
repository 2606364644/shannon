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
    # spec §7.1: JSON 字段名是 `from`(不带下划线)
    assert "from" in data["edges"][0]
    assert "from_" not in data["edges"][0]
    assert data["edges"][0]["from"] == "gateway"
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


def test_edge_from_json_tolerates_extra_llm_fields():
    """final-review MINOR 7 回归锚点:LLM 多吐的未知键不应触发 TypeError。
    from_json 应过滤到已知键(calls 单独重建)。"""
    raw = json.dumps({
        "from": "gateway", "to": "order-svc", "protocol": "grpc",
        "status": "ok", "error": None, "calls": [],
        "extra_llm_field": "noise", "confidence_overall": "high",
    })
    e = TopologyEdge.from_json(raw)
    assert e.from_ == "gateway"
    assert e.to == "order-svc"
    assert e.calls == []
