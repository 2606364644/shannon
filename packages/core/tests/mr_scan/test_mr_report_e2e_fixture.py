"""MR 增量链路 E2E fixture（spec §6/§8：NodeGoat 缩影——三来源各出标注正确的发现）。

真实链路段：真实 ``git diff -U3``（base 带 sanitize / head 删 sanitize + 新路由
+ helper 加 sink）→ ``parse_unified_diff`` → ``build_incremental_scope`` →
``incremental_scope.json`` 落盘 → ``build_report_data`` 组装报告。

合成段（无法在单测内跑 GitNexus/LLM，用 fixture 对齐 head 索引/污点图）：
code_index（blocks/entries/sinks）+ taint flows + 删防护判定产物（直接构造
``RemovedProtection``，等价 LLM 输出——判定链路另有 5 个单测）。

断言（spec §8 E2E 冒烟口径）：
- 三来源 flow 各自命中互不串（fA→A / fB→B / fC→C / fz 不入集）；
- 报告卡 trigger_source 标注正确（C > B > A 归并后 fB 仍 new_entry）；
- incremental_summary 完整（新入口路由 join / 防护行 followed_by_chains /
  flow_counts）+ scan.base_commit/head_commit 透传。
"""

import json
import subprocess

from supernova_core.code_index.models import CodeIndex, EntryPoint, FuncBlock
from supernova_core.code_index.parameter_models import (
    ParameterPropagationGraph, PropagationStep, SinkCallSite, SinkCategory,
    SourcePoint, TaintFlow,
)
from supernova_core.mr_scan.diff_manifest import parse_unified_diff
from supernova_core.mr_scan.incremental_scope import (
    RemovedProtection, build_incremental_scope,
)

# ── mini-repo：NodeGoat 缩影（base = 带 sanitize / 无新路由）──────────────────

_BASE_ROUTES = """const express = require('express');
const router = express.Router();

router.post('/memos', function handler(req, res) {
  const memo = processMemo(req.body.memo);
  db.query('INSERT ...', memo);
  res.send('ok');
});

function legacyRender(v) {
  return v;
}

module.exports = router;
"""

_HEAD_ROUTES = """const express = require('express');
const router = express.Router();

router.post('/memos', function handler(req, res) {
  const memo = processMemo(req.body.memo);
  db.query('INSERT ...', memo);
  res.send('ok');
});

function legacyRender(v) {
  eval(v);
  return v;
}

module.exports = router;
"""

_BASE_UTILS = """function sanitizeInput(v) {
    return v.replace(/[<>]/g, '');
}

function processMemo(v) {
    const s = sanitizeInput(v);
    return s;
}

module.exports = { sanitizeInput, processMemo };
"""

_HEAD_UTILS = """function sanitizeInput(v) {
    return v.replace(/[<>]/g, '');
}

function processMemo(v) {
    return v;
}

module.exports = { sanitizeInput, processMemo };
"""

_HEAD_ROUTES2 = """const express = require('express');
const router = express.Router();

router.get('/search', function searchHandler(req, res) {
  http.get(req.query.url);
  res.send('ok');
});

module.exports = router;
"""


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _make_repo(tmp_path):
    """真实 git 仓：base commit（routes+utils）→ head commit（改 utils/routes + 新 routes2）。"""
    repo = tmp_path / "nodegoat-mini"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    base = repo / "app"
    base.mkdir()
    (base / "routes.js").write_text(_BASE_ROUTES)
    (base / "utils.js").write_text(_BASE_UTILS)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base: sanitize + no extra route")
    base_sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=repo,
        check=True, capture_output=True, text=True).stdout.strip()

    (base / "routes.js").write_text(_HEAD_ROUTES)       # legacyRender 加 eval sink（来源 A）
    (base / "utils.js").write_text(_HEAD_UTILS)          # 删 sanitizeInput 调用（来源 C）
    (base / "routes2.js").write_text(_HEAD_ROUTES2)      # 新路由文件（来源 B）
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "head: drop sanitize + new route + new sink")
    head_sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=repo,
        check=True, capture_output=True, text=True).stdout.strip()
    return repo, base_sha, head_sha


# ── head 索引 fixture（行号对齐 head 工作树）─────────────────────────────────

def _blk(fid, start, end):
    fp, fn, _ = fid.rsplit(":", 2)[0], None, None
    file_path = fid.split(":")[0]
    func = fid.split(":")[1]
    return FuncBlock(id=fid, file_path=file_path, function_name=func,
                     start_line=start, end_line=end, source_code="",
                     parameters=[], language="javascript")


def _sink(sid, file, line, callee="db.query", cat=SinkCategory.SQL):
    fp, fn, ln = sid.split(":")[:3]
    return SinkCallSite(id=sid, caller_id=f"{fp}:{fn}:{ln}", callee_name=callee,
                        callee_receiver=None, category=cat,
                        sink_subtype="value", file_path=file, line=line, column=1,
                        dangerous_slots=[], rule_id="r")


def _flow(fid, entry, sink_id, steps=()):
    return TaintFlow(flow_id=fid, entry_point_id=entry, source_param="req.q",
                     source_type="query", sink_call_site_id=sink_id,
                     propagation_steps=list(steps))


def _step(frm, to, loc):
    return PropagationStep(step_id="", from_func_id=frm, from_param="p",
                           to_func_id=to, to_param="p", code_location=loc)


# app/routes.js head：handler 4-8（未触碰）；legacyRender 10-13（行 11 eval added）
_B_ROUTES = "app/routes.js"
_B_ROUTES2 = "app/routes2.js"
_B_UTILS = "app/utils.js"

_BLOCKS = [
    _blk(f"{_B_ROUTES}:handler:4", 4, 8),
    _blk(f"{_B_ROUTES}:legacyRender:10", 10, 13),
    _blk(f"{_B_ROUTES2}:searchHandler:4", 4, 7),
    _blk(f"{_B_UTILS}:processMemo:5", 5, 7),
    _blk(f"{_B_UTILS}:sanitizeInput:1", 1, 3),
]
_ENTRIES = [
    EntryPoint(func_block_id=f"{_B_ROUTES}:handler:4", entry_type="http_route",
               route="/memos", http_method="POST", confidence=1.0, evidence="e",
               needs_llm_review=False),
    EntryPoint(func_block_id=f"{_B_ROUTES2}:searchHandler:4", entry_type="http_route",
               route="/search", http_method="GET", confidence=1.0, evidence="e",
               needs_llm_review=False),
]
_SINKS = [
    # A：eval 落 routes.js added 行 11（legacyRender 非 entry——不触发 B）
    _sink(f"{_B_ROUTES}:legacyRender:11:sink", _B_ROUTES, 11,
          callee="eval", cat=SinkCategory.COMMAND),
    # B：新文件 routes2.js 的 http.get（行 5 ∈ 新文件 = added）
    _sink(f"{_B_ROUTES2}:searchHandler:5:sink", _B_ROUTES2, 5,
          callee="http.get", cat=SinkCategory.SSRF),
    # 既有 sink（routes.js 行 6，非 added）
    _sink(f"{_B_ROUTES}:handler:6:sink", _B_ROUTES, 6),
]
_SOURCES = [
    SourcePoint(id="sp1", entry_point_id=f"{_B_ROUTES}:handler:4", param_name="memo",
                source_type="body", expression="req.body.memo", file_path=_B_ROUTES,
                line=5, column=1, validation="", confidence=1.0, rule_id="r"),
    SourcePoint(id="sp2", entry_point_id=f"{_B_ROUTES2}:searchHandler:4", param_name="url",
                source_type="query", expression="req.query.url", file_path=_B_ROUTES2,
                line=5, column=1, validation="", confidence=1.0, rule_id="r"),
]

_PROTECTION = RemovedProtection(
    file_path=_B_UTILS, base_line_no=6,          # base utils.js 行 6：sanitizeInput 调用
    removed_text="const s = sanitizeInput(v);",
    function_name="processMemo", protection_kind="sanitize",
    rationale="escapes angle brackets", confidence=0.9,
)


def _flows():
    return [
        # fA：新 sink（eval@11 added）→ 仅来源 A（entry handler 未触碰）
        _flow("fA", f"{_B_ROUTES}:handler:4", f"{_B_ROUTES}:legacyRender:11:sink"),
        # fB：新入口 searchHandler 的链路（新文件）→ B（sink 亦 added → A 并存，
        #     归并 B > A 后仍 new_entry）
        _flow("fB", f"{_B_ROUTES2}:searchHandler:4", f"{_B_ROUTES2}:searchHandler:5:sink"),
        # fC：传播步经过 processMemo（被删防护函数）→ 仅来源 C（entry 未动、
        #     step 落点在 routes.js 未改行）
        _flow("fC", f"{_B_ROUTES}:handler:4", f"{_B_ROUTES}:handler:6:sink", steps=[
            _step(f"{_B_ROUTES}:handler:4", f"{_B_UTILS}:processMemo:5",
                  f"{_B_ROUTES}:5"),
            _step(f"{_B_UTILS}:processMemo:5", f"{_B_ROUTES}:handler:4",
                  f"{_B_ROUTES}:6"),
        ]),
        # fz：与增量无关（entry/sink/steps 均未触碰）→ 不入集
        _flow("fz", f"{_B_ROUTES}:handler:4", f"{_B_ROUTES}:handler:6:sink"),
    ]


def _index():
    flows = _flows()
    return CodeIndex(
        repository="nodegoat-mini", language="javascript",
        total_blocks=len(_BLOCKS), total_entry_points=len(_ENTRIES), total_chains=0,
        blocks=_BLOCKS, edges=[], entry_points=_ENTRIES, chains=[],
        sink_call_sites=_SINKS, source_points=_SOURCES,
        parameter_graph=ParameterPropagationGraph(taint_flows=flows),
    )


async def test_mr_e2e_fixture_three_sources_annotated(tmp_path):
    repo, base_sha, head_sha = _make_repo(tmp_path)

    # ① 真实 git diff -U3 → DiffManifest
    out = subprocess.run(
        ["git", "diff", "-U3", "HEAD~1..HEAD", "--no-color"],
        cwd=repo, check=True, capture_output=True, text=True).stdout
    manifest = parse_unified_diff(out, base_commit=base_sha, head_commit=head_sha)
    # diff 契约：三文件（routes 改 / utils 改 / routes2 新）
    assert {h.file_path for h in manifest.hunks} == {
        "app/routes.js", "app/utils.js", "app/routes2.js"}
    new_files = {h.file_path for h in manifest.hunks if h.is_new_file}
    assert new_files == {"app/routes2.js"}

    # ② scope 合成（真实 diff 输入 + head 索引 fixture + 模拟删防护判定产物）
    index = _index()
    scope = build_incremental_scope(
        diff=manifest, index=index,
        pgraph=index.parameter_graph, removed_protections=[_PROTECTION])

    # 三来源互不串 + 负例不入集。fB 的 sink 在新文件（added 行）→ A 并存——
    # 归并（B > A）后标注仍是 new_entry，A 明细如实含 fB（诚实记账）。
    assert set(scope.source_a_flow_ids) == {"fA", "fB"}
    assert set(scope.source_b_flow_ids) == {"fB"}
    assert set(scope.source_c_flow_ids) == {"fC"}
    assert set(scope.verdict_flow_ids) == {"fA", "fB", "fC"}
    assert "fz" not in scope.verdict_flow_ids

    # ③ 产物落盘 → 报告组装（复用 whitebox mr_activities 的目录布局）
    from supernova_core.services.report_data_builder import build_report_data
    from supernova_core.models.report_data import ScanMeta

    deliverables = tmp_path / "deliverables" / "whitebox"
    mr_dir = deliverables / "intermediate" / "mr"
    mr_dir.mkdir(parents=True)
    (mr_dir / "diff_manifest.json").write_text(manifest.model_dump_json())
    (mr_dir / "incremental_scope.json").write_text(scope.model_dump_json())
    (mr_dir / "removed_protections.json").write_text(json.dumps(
        {"degraded": False, "protections": [_PROTECTION.model_dump()]}))
    (deliverables / "intermediate").mkdir(exist_ok=True)
    (deliverables / "intermediate" / "code_index.json").write_text(
        index.model_dump_json())

    # merged queue：fA/fC GN 卡、fB 双轨卡、fz LLM-only 卡
    (deliverables / "intermediate").mkdir(exist_ok=True)
    (deliverables / "intermediate" / "xss_exploitation_queue.json").write_text(
        json.dumps({"vulnerabilities": [
            {"ID": "XSS-VULN-01", "vulnerability_type": "Reflected",
             "externally_exploitable": True, "confidence": "high", "severity": "high",
             "verdict": "vulnerable", "merge_source": "gitnexus-only", "flow_id": "fA"},
            {"ID": "SSRF-VULN-01", "vulnerability_type": "SSRF",
             "externally_exploitable": True, "confidence": "high", "severity": "high",
             "verdict": "vulnerable", "merge_source": "both", "flow_id": "fB"},
            {"ID": "INJ-VULN-01", "vulnerability_type": "NoSQL Injection",
             "externally_exploitable": True, "confidence": "high", "severity": "critical",
             "verdict": "vulnerable", "merge_source": "gitnexus-only", "flow_id": "fC"},
            {"ID": "XSS-VULN-02", "vulnerability_type": "Stored",
             "externally_exploitable": True, "confidence": "high", "severity": "high",
             "verdict": "vulnerable", "merge_source": "llm-only", "flow_id": "fz"},
        ]}, ensure_ascii=False))

    rd = await build_report_data(deliverables, ScanMeta(id="nodegoat-mr-1",
                                                       track="whitebox"))

    # ④ scan MR 元信息（真实 commit sha 透传）
    assert rd.scan.base_commit == base_sha
    assert rd.scan.head_commit == head_sha
    assert rd.scan.diff_stat is not None and rd.scan.diff_stat["files"] == 3

    # ⑤ 增量摘要段：新入口路由 join + 防护行（followed_by_chains）+ 三来源分布
    inc = rd.incremental_summary
    assert inc is not None
    assert [e.route for e in inc.new_entry_points] == ["/search"]
    assert inc.new_entry_points[0].method == "GET"
    assert [p.function for p in inc.removed_protections] == ["processMemo"]
    assert inc.removed_protections[0].followed_by_chains is True
    assert inc.flow_counts == {"new_code": 2, "new_entry": 1,
                               "removed_protection": 1, "affected_flows": 3}

    # ⑥ 漏洞卡 trigger_source：A/B/C 各标正确；fz（LLM-only）不标
    by_id = {v.id: v.trigger_source for v in rd.vulnerabilities}
    assert by_id == {
        "XSS-VULN-01": "new_code",        # fA → eval 新 sink
        "SSRF-VULN-01": "new_entry",      # fB → 新路由（A 并存，B > A 归并）
        "INJ-VULN-01": "removed_protection",  # fC → 删 sanitize 反向链
        "XSS-VULN-02": None,              # fz → 全量链，LLM-only 不标
    }
