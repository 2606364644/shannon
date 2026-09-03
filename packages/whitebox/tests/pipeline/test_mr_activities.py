"""MR 前置 activities（spec 2026-09-03 §3.1 步骤 2/3）：repo prepare + git diff。

不经 Temporal 直接调 async activity 函数（对齐 test_activities_* 惯例）；
产物落 deliverables/whitebox/intermediate/mr/。
"""

import json
import subprocess
from pathlib import Path

import pytest

from supernova_core.models.errors import PentestError
from supernova_whitebox.pipeline.shared import ActivityInput
from supernova_whitebox.pipeline.mr_activities import (
    MR_DIR_NAME, run_git_diff, run_mr_repo_prepare,
)


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture()
def mr_repo(tmp_path):
    """两 commit 的 git 仓：base（sanitize 版）→ head（删 sanitize + 加路由）。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    f = repo / "app.py"
    f.write_text("def h(req):\n    q = req['q']\n    q = sanitize(q)\n    return db(q)\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    f.write_text("def h(req):\n    q = req['q']\n    return db(q)\n"
                 "\n\ndef new_route(req):\n    return db(req['id'])\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "head")
    return repo


def _act(repo: Path, tmp_path: Path, **kw) -> ActivityInput:
    scan_dir = tmp_path / "scan"
    scan_dir.mkdir(exist_ok=True)
    return ActivityInput(
        repo_path=str(repo),
        workspace_path=str(scan_dir),
        mr_base_ref="main~1",
        mr_head_ref="main",
        **kw,
    )


async def test_repo_prepare_resolves_merge_base_and_checks_out_head(mr_repo, tmp_path):
    result = await run_mr_repo_prepare(_act(mr_repo, tmp_path))

    # merge-base 解析出 base commit sha；head 已 checkout（HEAD == head_ref 解析值）
    assert result["base_commit"]
    assert result["head_commit"]
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=mr_repo,
                          capture_output=True, text=True, check=True).stdout.strip()
    assert head == result["head_commit"]


async def test_repo_prepare_fails_fast_on_unresolvable_ref(mr_repo, tmp_path):
    act = _act(mr_repo, tmp_path)
    act.mr_base_ref = "no-such-ref"

    with pytest.raises(PentestError):
        await run_mr_repo_prepare(act)


async def test_git_diff_writes_manifest_and_patch(mr_repo, tmp_path):
    result = await run_git_diff(_act(mr_repo, tmp_path))

    mr_dir = tmp_path / "scan" / "deliverables" / "whitebox" / "intermediate" / MR_DIR_NAME
    manifest = json.loads((mr_dir / "diff_manifest.json").read_text())
    patch = (mr_dir / "diff.patch").read_text()

    assert manifest["stats"]["insertions"] >= 1
    assert manifest["stats"]["deletions"] >= 1
    assert "diff --git" in patch
    # 解析出的新增行覆盖 new_route（来源 B 的对齐基础）
    added_files = {h["file_path"] for h in manifest["hunks"]}
    assert "app.py" in added_files
    assert result["stats"]["insertions"] == manifest["stats"]["insertions"]
    # vuln 类启发式一并返回（child workflow 类选择用）
    assert "injection" in result["selected_vuln_classes"]


_VALID_PROTECTION_JSON = json.dumps({"removed_protections": [{
    "file_path": "app.py", "base_line_no": 3, "removed_text": "    q = sanitize(q)",
    "function_name": "h", "protection_kind": "sanitize",
    "rationale": "输入清洗被删", "confidence": 0.9,
}]})


async def test_protection_removal_analysis_writes_protections(mr_repo, tmp_path, monkeypatch):
    await run_git_diff(_act(mr_repo, tmp_path))   # 先产 diff.patch

    from supernova_whitebox.pipeline import mr_activities

    async def stub_client(prompt, **kwargs):
        return _VALID_PROTECTION_JSON

    monkeypatch.setattr(mr_activities, "_make_protection_llm_client",
                        lambda *a, **k: stub_client)

    result = await mr_activities.run_protection_removal_analysis(_act(mr_repo, tmp_path))

    mr_dir = tmp_path / "scan" / "deliverables" / "whitebox" / "intermediate" / MR_DIR_NAME
    data = json.loads((mr_dir / "removed_protections.json").read_text())
    assert result["degraded"] is False
    assert data["degraded"] is False
    assert data["protections"][0]["function_name"] == "h"


async def test_protection_removal_analysis_degrades_without_llm(mr_repo, tmp_path, monkeypatch):
    await run_git_diff(_act(mr_repo, tmp_path))

    from supernova_whitebox.pipeline import mr_activities
    monkeypatch.setattr(mr_activities, "_make_protection_llm_client", lambda *a, **k: None)

    result = await mr_activities.run_protection_removal_analysis(_act(mr_repo, tmp_path))

    assert result["degraded"] is True
    assert result["protections"] == []


async def test_incremental_scope_activity_writes_scope(mr_repo, tmp_path):
    act = _act(mr_repo, tmp_path)
    await run_git_diff(act)
    mr_dir = tmp_path / "scan" / "deliverables" / "whitebox" / "intermediate" / MR_DIR_NAME
    # 布置 head 索引产物（code_index.json 含 parameter_graph）
    from supernova_whitebox.pipeline import mr_activities

    index = _index_fixture()
    (mr_dir.parent / "code_index.json").write_text(index.model_dump_json())

    result = await mr_activities.run_incremental_scope(act)

    data = json.loads((mr_dir / "incremental_scope.json").read_text())
    # fixture 的新入口链（app.py:new_route added）被来源 B 收进 verdict 候选
    assert data["verdict_flow_ids"]
    assert result["verdict_flow_count"] == len(data["verdict_flow_ids"])


def _index_fixture():
    """与 mr_repo head 版本对齐的最小 CodeIndex：new_route 为新入口（added 行 5-6）。"""
    from supernova_core.code_index.models import CodeIndex, EntryPoint, FuncBlock
    from supernova_core.code_index.parameter_models import (
        ParameterPropagationGraph, SinkCallSite, SinkCategory, TaintFlow,
    )

    def blk(fid, start, end):
        fp, fn, _ = fid.split(":")
        return FuncBlock(id=fid, file_path=fp, function_name=fn, start_line=start,
                         end_line=end, source_code="", parameters=[], language="python")

    blocks = [blk("app.py:h:1", 1, 4), blk("app.py:new_route:6", 6, 7)]
    entries = [EntryPoint(func_block_id="app.py:new_route:6", entry_type="http_route",
                          route="/new", http_method="GET", confidence=1.0, evidence="e",
                          needs_llm_review=False)]
    sink = SinkCallSite(id="app.py:new_route:7:db:7", caller_id="app.py:new_route:6",
                        callee_name="db", callee_receiver=None,
                        category=SinkCategory.SQL, sink_subtype="sql_value",
                        file_path="app.py", line=7, column=1, dangerous_slots=[],
                        rule_id="r")
    flows = [TaintFlow(flow_id="app.py:new_route:6->app.py:new_route:7:db:7",
                       entry_point_id="app.py:new_route:6", source_param="req",
                       source_type="query",
                       sink_call_site_id="app.py:new_route:7:db:7")]
    return CodeIndex(repository="r", language="python", total_blocks=2,
                     total_entry_points=1, total_chains=0, blocks=blocks, edges=[],
                     entry_points=entries, chains=[], sink_call_sites=[sink],
                     source_points=[],
                     parameter_graph=ParameterPropagationGraph(taint_flows=flows))
