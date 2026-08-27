"""artifacts-guide 生成（spec 2026-08-27 §5.1）——per-edge 目录导读，确定性纯函数。

投喂形态 = 目录导读（列文件路径 + 作用说明 + 缺失标注），Agent 自己按需读；
不塞内容。这是修 P1 的核心：deliverables_from_scans 不再指向空的关联 out_dlv，
而是指向各子仓真实扫描产物。
"""
from pathlib import Path

from supernova_core.correlation.artifacts_guide import (
    ServiceArtifacts, build_artifacts_guide,
)


def _svc(service, role="backend", repo_path="/r/repo", queue=None,
         entry_points=None, dismissed=None, proto_roots=None):
    return ServiceArtifacts(
        service=service, role=role, repo_path=repo_path,
        deliverables=Path("/ws") / service / "deliverables",
        queue_files=[Path(q) for q in (queue or [])],
        entry_points=Path(entry_points) if entry_points else None,
        dismissed=Path(dismissed) if dismissed else None,
        proto_roots=list(proto_roots or []),
    )


def test_guide_lists_both_services_with_role_and_repo_path():
    guide = build_artifacts_guide(
        _svc("gateway", role="entrypoint", repo_path="/r/node-gw"),
        _svc("order-svc", role="backend", repo_path="/r/go-order"))
    assert "gateway" in guide and "entrypoint" in guide and "/r/node-gw" in guide
    assert "order-svc" in guide and "backend" in guide and "/r/go-order" in guide
    # from 仓在前，to 仓在后
    assert guide.index("gateway") < guide.index("order-svc")


def test_guide_lists_existing_artifacts_with_real_paths():
    ep = "/ws/gateway/deliverables/whitebox/intermediate/entry_points.json"
    q = "/ws/order-svc/deliverables/injection_exploitation_queue.json"
    dm = "/ws/order-svc/deliverables/whitebox/intermediate/dismissed_findings.json"
    guide = build_artifacts_guide(
        _svc("gateway", entry_points=ep),
        _svc("order-svc", queue=[q], dismissed=dm))
    assert ep in guide
    assert q in guide
    assert dm in guide


def test_guide_marks_missing_artifacts_explicitly():
    """缺失文件如实标注（缺失），不静默省略——Agent 需知道产物不存在。"""
    guide = build_artifacts_guide(_svc("gateway"), _svc("order-svc"))
    assert "entry_points.json" in guide and "缺失" in guide
    assert "dismissed_findings.json" in guide


def test_guide_describes_purpose_keywords():
    """作用说明是给 Agent 的语义锚：路由表/漏洞(ID)/非病毒(判非漏洞)各就各位。"""
    ep = "/ws/g/deliverables/whitebox/intermediate/entry_points.json"
    q = "/ws/b/deliverables/injection_exploitation_queue.json"
    dm = "/ws/b/deliverables/whitebox/intermediate/dismissed_findings.json"
    guide = build_artifacts_guide(
        _svc("gateway", entry_points=ep),
        _svc("order-svc", queue=[q], dismissed=dm))
    ep_line = next(l for l in guide.splitlines() if ep in l)
    assert "路由表" in ep_line or "route" in ep_line.lower()
    q_line = next(l for l in guide.splitlines() if q in l)
    assert "ID" in q_line          # 引用漏洞必须优先用 ID（方法论锚）
    dm_line = next(l for l in guide.splitlines() if dm in l)
    assert "非漏洞" in dm_line


def test_guide_includes_proto_roots_only_when_declared():
    base = build_artifacts_guide(
        _svc("gateway", proto_roots=["proto/"]),
        _svc("order-svc"))
    assert "proto/" in base
    none_declared = build_artifacts_guide(_svc("gateway"), _svc("order-svc"))
    assert "proto_roots" not in none_declared


def test_full_guide_covers_all_services():
    """裁决批用全仓 guide：每个 service 一节，仓数不限。"""
    from supernova_core.correlation.artifacts_guide import build_full_artifacts_guide
    guide = build_full_artifacts_guide({
        "gateway": _svc("gateway", role="entrypoint"),
        "order-svc": _svc("order-svc"),
        "payment-svc": _svc("payment-svc"),
    })
    for s in ("gateway", "order-svc", "payment-svc"):
        assert s in guide
    assert guide.index("gateway") < guide.index("order-svc") < guide.index("payment-svc")
    assert guide.startswith("<artifacts-guide>") and guide.endswith("</artifacts-guide>")
