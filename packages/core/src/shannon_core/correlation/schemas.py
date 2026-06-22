from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict


def _s(o) -> dict:
    return asdict(o)


@dataclass
class CallSite:
    file: str
    line: int
    snippet: str


@dataclass
class Call:
    method: str
    call_site: CallSite
    confidence: str
    evidence: str


@dataclass
class TopologyEdge:
    from_: str
    to: str
    protocol: str
    calls: list[Call] = field(default_factory=list)
    status: str = "ok"            # ok | low | unverified | error | declared-missing
    error: str | None = None

    def to_json(self) -> str:
        d = _s(self)
        # spec §7.1: JSON 字段名是 `from`(不带下划线), 不泄露 Python 关键字转义
        d["from"] = d.pop("from_")
        return json.dumps(d, ensure_ascii=False)

    @staticmethod
    def from_json(s: str) -> "TopologyEdge":
        d = json.loads(s)
        d["from_"] = d.pop("from")
        d["calls"] = [Call(method=c["method"],
                           call_site=CallSite(**c["call_site"]),
                           confidence=c["confidence"], evidence=c["evidence"]) for c in d["calls"]]
        return TopologyEdge(**d)


@dataclass
class ServiceNode:
    name: str
    role: str
    repo: str


@dataclass
class CrossServiceTopology:
    services: list[ServiceNode]
    edges: list[TopologyEdge]

    def to_json(self) -> str:
        return json.dumps({"services": [_s(s) for s in self.services],
                           "edges": [json.loads(e.to_json()) for e in self.edges]},
                          ensure_ascii=False)

    @staticmethod
    def from_json(s: str) -> "CrossServiceTopology":
        d = json.loads(s)
        return CrossServiceTopology(
            services=[ServiceNode(**s) for s in d["services"]],
            edges=[TopologyEdge.from_json(json.dumps(e)) for e in d["edges"]],
        )


@dataclass
class TrustBoundary:
    service: str
    method: str
    exposure: str
    reachable_from: list[str]
    reason: str
    confidence: str

    def to_json(self) -> str:
        return json.dumps(_s(self), ensure_ascii=False)


@dataclass
class CorrelationResult:
    topology: CrossServiceTopology
    boundaries: list[TrustBoundary]
