# packages/core/tests/code_index/test_authz_track_integration.py
"""End-to-end: code_index.json + framework_analysis.json → build_authz_gitnexus_track.

Covers dominance candidates (handler→sink no ownership), framework candidates
(auto-generated CRUD), evidence rendering, and graceful degradation.
"""
import json
from pathlib import Path

from shannon_core.code_index.authz_gitnexus_track import build_authz_gitnexus_track

NODEGOAT_FIXTURE = Path(__file__).parent / "fixtures" / "nodegoat_code_index.json"


def _block(bid, source, params=None):
    fp, fn, ln = bid.rsplit(":", 2)
    return {"id": bid, "file_path": fp, "function_name": fn, "start_line": int(ln),
            "end_line": int(ln) + 5, "source_code": source, "parameters": params or [],
            "decorators": [], "language": "typescript"}


def _ep(handler, route, method="DELETE"):
    return {"func_block_id": handler, "entry_type": "http_route", "route": route,
            "http_method": method, "confidence": 0.9, "evidence": "",
            "needs_llm_review": False, "authentication": "required", "source": "code_index"}


def _sp(handler, expression, param_name):
    return {"id": f"{handler}::{param_name}::1", "entry_point_id": handler,
            "param_name": param_name, "source_type": "path",
            "expression": expression, "file_path": "u.js", "line": 10,
            "confidence": 0.9, "rule_id": "test-rule"}


def _write(tmp_path, eps, blocks, chains, fw_endpoints=None, source_points=None):
    (tmp_path / "code_index.json").write_text(json.dumps({
        "repository": "r", "language": "typescript", "total_blocks": len(blocks),
        "total_entry_points": len(eps), "total_chains": len(chains),
        "blocks": blocks, "edges": [], "entry_points": eps, "chains": chains,
        "source_points": source_points or [],
    }))
    if fw_endpoints is not None:
        (tmp_path / "framework_analysis.json").write_text(json.dumps({
            "detected_framework": {"name": "finale-rest"},
            "inferred_endpoints": fw_endpoints, "recommendations": [],
        }))


def test_e2e_dominance_candidate_plus_framework_candidate(tmp_path):
    handler = _block("u.js:update:10", "async function update(req){ await persist(req.params.id); }")
    guarded = _block("g.js:safe:1",
                     "async function safe(req){ const r = await db.user.findFirst({where:{userId:req.user.id}}); await r.save(); }")
    sink = _block("repo.js:persist:1", "function persist(id){ db.user.update(); }", params=["id"])
    safe_sink = _block("repo.js:safeupdate:1", "function safeupdate(){ r.save(); }")
    _write(
        tmp_path,
        [_ep("u.js:update:10", "/api/Feedbacks/:id", "DELETE"),
         _ep("g.js:safe:1", "/api/owned/:id", "PUT")],
        [handler, guarded, sink, safe_sink],
        [
            {"entry_point_id": "u.js:update:10", "path": ["u.js:update:10", "repo.js:persist:1"],
             "depth": 1, "has_unresolved": False},
            {"entry_point_id": "g.js:safe:1", "path": ["g.js:safe:1", "repo.js:safeupdate:1"],
             "depth": 1, "has_unresolved": False},  # guarded → not a candidate
        ],
        fw_endpoints=[
            {"method": "DELETE", "path": "/api/Feedbacks", "source": "framework-auto-generated",
             "model": "Feedback", "middleware": ("isAuthenticated",),
             "vulnerability_indicators": ("No ownership check",)},
        ],
        source_points=[_sp("u.js:update:10", "req.params.id", "id")],
    )

    md, dom, fw, http_route_count, entry_point_total = build_authz_gitnexus_track(str(tmp_path))

    # dominance: only the unguarded handler → 1 candidate (guarded handler skipped)
    assert len(dom) == 1
    assert dom[0].sink_id == "repo.js:persist:1"
    # framework: 1 auto-generated
    assert len(fw) == 1
    assert fw[0].method == "DELETE"
    # markdown surfaces both
    assert "DELETE /api/Feedbacks/:id" in md
    assert "repo.js:persist:1" in md
    assert "finale-rest" in md
    # verdict directive
    assert "verdict" in md.lower() or "判定" in md


def test_e2e_graceful_degradation_no_index(tmp_path):
    md, dom, fw, http_route_count, entry_point_total = build_authz_gitnexus_track(str(tmp_path))
    assert "无" in md or "no" in md.lower()
    assert dom == [] and fw == []


def test_e2e_guarded_handler_not_a_candidate(tmp_path):
    """Handler with ORM ownership predicate → no dominance candidate even if it reaches a sink."""
    handler = _block("u.js:update:10",
                     "async function update(req){ const r = await db.user.findFirst({where:{userId:req.user.id}}); await repo.update(r); }")
    sink = _block("repo.js:update:1", "function update(){ db.user.update(); }")
    _write(
        tmp_path,
        [_ep("u.js:update:10", "/api/u/:id", "PUT")],
        [handler, sink],
        [{"entry_point_id": "u.js:update:10", "path": ["u.js:update:10", "repo.js:update:1"],
          "depth": 1, "has_unresolved": False}],
    )
    md, dom, fw, http_route_count, entry_point_total = build_authz_gitnexus_track(str(tmp_path))
    assert dom == []  # ownership guard present


def test_e2e_nodegoat_a4_insecure_dor_candidate(tmp_path):
    """真机 NodeGoat 回归：authz GitNexus 轨候选 0→≥1，含 A4 Insecure DOR 正样本
    (AllocationsHandler→UserDAO)，且无过报爆炸（≤6）。

    真因（velvety-wibbling-candy）：Express 注册块让 entry 塌缩到 index.js:index:11
    wiring 块 + IDOR 风味源 req.params 不被注入风味的 SourcePoint 检测识别。修复
    (re-anchor 真实 handler + req.* 补召回) 后 chain[6]
    AllocationsHandler:6 → AllocationsDAO(缺失中间块) → UserDAO:4 产候选。
    fixture = 真机 workspaces/NodeGoat_20260713-231325/deliverables/whitebox/code_index.json。
    """
    (tmp_path / "code_index.json").write_text(NODEGOAT_FIXTURE.read_text())
    md, dom, fw, http_route_count, entry_point_total = build_authz_gitnexus_track(str(tmp_path))

    # 修复前 0 候选 → 修复后 ≥1（queue 非空，judge 走 candidate>0 深判分支）。
    assert len(dom) >= 1
    # 防过报爆炸（模拟 ≤3，实测 1；注册块 22 路由不该全炸成 22 候选）。
    assert len(dom) <= 6
    # A4 正样本：handler=AllocationsHandler:6, sink=UserDAO:4（Insecure DOR）。
    a4 = [c for c in dom
          if c.handler_id == "app/routes/allocations.js:AllocationsHandler:6"
          and c.sink_id == "app/data/user-dao.js:UserDAO:4"]
    assert len(a4) == 1
    # re-anchor 正确：handler 是真实 handler（非注册块 index.js:index:11）。
    assert a4[0].endpoint_id == "app/routes/allocations.js:AllocationsHandler:6"
