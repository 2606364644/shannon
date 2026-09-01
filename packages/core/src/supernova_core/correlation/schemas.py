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
        # final-review MINOR 7: 过滤掉 LLM 可能多吐的未知键,避免 TopologyEdge(**d) 触发 TypeError。
        # calls 单独重建并显式赋值,不参与 **d 过滤集。
        calls_raw = d.get("calls", [])
        d = {k: d[k] for k in ("from_", "to", "protocol", "status", "error") if k in d}
        d["calls"] = [Call(method=c["method"],
                           call_site=CallSite(**c["call_site"]),
                           confidence=c["confidence"], evidence=c["evidence"]) for c in calls_raw]
        return TopologyEdge(**d)


@dataclass
class ServiceNode:
    name: str
    role: str
    repo: str
    # New capability set; None/empty falls back to legacy single role for old readers.
    roles: list[str] | None = None

    @property
    def effective_roles(self) -> list[str]:
        if self.roles:
            return self.roles
        return [self.role]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "role": self.role,
            "roles": self.effective_roles,
            "repo": self.repo,
        }


@dataclass
class CrossServiceTopology:
    services: list[ServiceNode]
    edges: list[TopologyEdge]

    def to_json(self) -> str:
        return json.dumps({"services": [s.to_dict() for s in self.services],
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


@dataclass
class CrossServiceFlow:
    """候选跨服务攻击链（spec 2026-08-24 §5.4）：前端仓入口 → RPC method → 后端仓漏洞。

    概率性 Agent 推断产物，供人工复核；vuln_refs 宽松 dict（title/severity/location/service）。
    """
    edge_from: str
    edge_to: str
    entry: str
    method: str
    call_site: CallSite
    vuln_refs: list[dict] = field(default_factory=list)
    confidence: str = "low"
    evidence: str = ""

    def to_json(self) -> str:
        return json.dumps(_s(self), ensure_ascii=False)

    @staticmethod
    def from_json(s: str) -> "CrossServiceFlow":
        d = json.loads(s)
        d["call_site"] = CallSite(**d["call_site"])
        return CrossServiceFlow(**d)
