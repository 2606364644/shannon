# packages/whitebox/tests/pipeline/test_authz_judge_deep.py
"""spec-1a Task 3: candidate_count>0 切多轮深度判定（run_gitnexus_verdict_agent）。

核心断言（不削弱）：candidate_count>0 时调 run_gitnexus_verdict_agent 一次（多轮），
且不再走单次 run_claude_prompt。
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


@pytest.mark.asyncio
async def test_authz_judge_uses_multiturn_verdict_when_candidates(tmp_path, monkeypatch):
    """candidate_count>0 时调 run_gitnexus_verdict_agent（多轮），非单次 run_claude_prompt。"""
    from shannon_whitebox.pipeline import activities as act

    # 准备：patch build_authz_gitnexus_track 返固定候选（绕开 JSON 启发式构造，
    # 聚焦 T3 切换断言——candidate_count>0 即进判定段）。
    # activity 按位置解包 5 元组 (md, dom_cands, fw_cands, http_route_count,
    # entry_point_total)，故 fake 必须是可解包成 5 的序列（MagicMock 默认 __iter__
    # 返空迭代器，会触发 "got 0" 解包错）。用 AuthzTrackBuildResult NamedTuple。
    fake_result = _fake_build_result(markdown="## 候选\nPUT /api/u/:id")
    # patch 源模块（activity 内 `from ... import build_authz_gitnexus_track` 每次调用
    # 都从源模块取当前属性，patch 源模块即拦截）。
    monkeypatch.setattr(
        "shannon_core.code_index.authz_gitnexus_track.build_authz_gitnexus_track",
        lambda d: fake_result,
    )

    verdict_called = {"n": 0}

    async def fake_verdict(*, prompt, repo_path, structured_output_schema=None, audit_session=None):
        verdict_called["n"] += 1
        r = MagicMock()
        r.structured_output = {"vulnerabilities": []}
        r.text = "{}"
        return r

    monkeypatch.setattr(act, "run_gitnexus_verdict_agent", fake_verdict)

    single_called = {"n": 0}

    async def fake_single(**kw):
        single_called["n"] += 1
        return MagicMock(structured_output={}, text="{}")

    # patch 源模块 run_claude_prompt（activity 内经 `from ... import run_claude_prompt`
    # 绑到 activities 命名空间，故 patch activities 上的名字才真正拦截判定段旧调用；
    # 同时 patch 源模块以兜底 verdict_agent 内部延迟 import）。
    monkeypatch.setattr(act, "run_claude_prompt", fake_single)
    monkeypatch.setattr("shannon_core.agents.runner.run_claude_prompt", fake_single)

    # _get_paths 返 (repo, deliverables, workspaces)；deliverables 需可写 queue。
    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    monkeypatch.setattr(act, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))

    # session：track_step async cm + log_info async
    session = MagicMock()
    session.track_step = _noop_cm_factory()
    session.log_info = AsyncMock()
    monkeypatch.setattr(
        "shannon_whitebox.audit.session_registry.get_audit_session", lambda: session
    )

    inp = MagicMock()
    inp.workspace_name = "ws"
    inp.api_key = None

    await act.run_authz_gitnexus_judge(inp)

    assert verdict_called["n"] == 1, "应用 run_gitnexus_verdict_agent 多轮"
    assert single_called["n"] == 0, "不应再走单次 run_claude_prompt"


@pytest.mark.asyncio
async def test_authz_judge_verdict_passes_audit_session(tmp_path, monkeypatch):
    """verdict_agent 收到 get_audit_session()（非 None），供多轮工具调用审计。"""
    from shannon_whitebox.pipeline import activities as act

    fake_result = _fake_build_result(markdown="## 候选")
    monkeypatch.setattr(
        "shannon_core.code_index.authz_gitnexus_track.build_authz_gitnexus_track",
        lambda d: fake_result,
    )

    captured = {}

    async def fake_verdict(*, prompt, repo_path, structured_output_schema=None, audit_session=None):
        captured["audit_session"] = audit_session
        r = MagicMock()
        r.structured_output = {"vulnerabilities": []}
        r.text = "{}"
        return r

    monkeypatch.setattr(act, "run_gitnexus_verdict_agent", fake_verdict)

    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    monkeypatch.setattr(act, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))

    session = MagicMock()
    session.track_step = _noop_cm_factory()
    session.log_info = AsyncMock()
    monkeypatch.setattr(
        "shannon_whitebox.audit.session_registry.get_audit_session", lambda: session
    )

    inp = MagicMock()
    inp.workspace_name = "ws"
    inp.api_key = None

    await act.run_authz_gitnexus_judge(inp)

    assert captured["audit_session"] is session, "audit_session 应为 get_audit_session() 返回值"


@pytest.mark.asyncio
async def test_authz_judge_verdict_writes_queue_with_source_track(tmp_path, monkeypatch):
    """verdict_agent 返回的 vulnerabilities 落盘，且 source_track='gitnexus'（schema 不变）。"""
    import json as _json
    from shannon_whitebox.pipeline import activities as act

    fake_result = _fake_build_result(markdown="## 候选")
    monkeypatch.setattr(
        "shannon_core.code_index.authz_gitnexus_track.build_authz_gitnexus_track",
        lambda d: fake_result,
    )

    async def fake_verdict(*, prompt, repo_path, structured_output_schema=None, audit_session=None):
        r = MagicMock()
        r.structured_output = {"vulnerabilities": [{
            "ID": "AUTHZ-GN-01", "vulnerability_type": "Horizontal",
            "externally_exploitable": True, "endpoint": "PUT /api/u/:id",
            "vulnerable_code_location": "u.js:update:10", "role_context": "user",
            "guard_evidence": "none", "side_effect": "update", "reason": "no ownership",
            "minimal_witness": "x", "confidence": "high", "notes": "",
        }]}
        r.text = "{}"
        return r

    monkeypatch.setattr(act, "run_gitnexus_verdict_agent", fake_verdict)

    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    monkeypatch.setattr(act, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))

    session = MagicMock()
    session.track_step = _noop_cm_factory()
    session.log_info = AsyncMock()
    monkeypatch.setattr(
        "shannon_whitebox.audit.session_registry.get_audit_session", lambda: session
    )

    inp = MagicMock()
    inp.workspace_name = "ws"
    inp.api_key = None

    await act.run_authz_gitnexus_judge(inp)

    queue_path = deliverables / "authz_gitnexus_queue.json"
    assert queue_path.exists()
    data = _json.loads(queue_path.read_text())
    assert len(data["vulnerabilities"]) == 1
    v = data["vulnerabilities"][0]
    assert v["source_track"] == "gitnexus"
    assert v["evidence_chain"]  # populated


@pytest.mark.asyncio
async def test_authz_judge_explores_when_zero_candidates(tmp_path, monkeypatch):
    """spec-1a Task 4: candidate_count==0 时调 verdict_agent 自主探索（非静默写空 queue）。

    核心断言（不削弱）：0 候选时 explored==1（explore prompt 被调一次），且 prompt 含
    explore/route 字样。产软候选 needs_review=True + source_track='gitnexus'。
    """
    import shannon_whitebox.pipeline.activities as act

    # 0 候选 fixture（dom=fw=0）
    fake_result = _fake_build_result(markdown="", dom=0, fw=0, http=0, total=0)
    monkeypatch.setattr(
        "shannon_core.code_index.authz_gitnexus_track.build_authz_gitnexus_track",
        lambda d: fake_result,
    )

    explored = {"n": 0, "prompt": None}

    async def fake_verdict(*, prompt, repo_path, structured_output_schema=None, audit_session=None):
        explored["n"] += 1
        explored["prompt"] = prompt
        assert "explore" in prompt.lower() or "route" in prompt.lower(), "应用探索 prompt"
        r = MagicMock()
        r.structured_output = {"vulnerabilities": []}
        r.text = "{}"
        return r

    monkeypatch.setattr(act, "run_gitnexus_verdict_agent", fake_verdict)

    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    monkeypatch.setattr(act, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))

    session = MagicMock()
    session.track_step = _noop_cm_factory()
    session.log_info = AsyncMock()
    monkeypatch.setattr(
        "shannon_whitebox.audit.session_registry.get_audit_session", lambda: session
    )

    inp = MagicMock()
    inp.workspace_name = "ws"
    inp.api_key = None

    await act.run_authz_gitnexus_judge(inp)

    assert explored["n"] == 1, "0 候选时应触发自主探索"
    # entry_points_summary 变量被填充（非裸 placeholder）
    assert explored["prompt"] is not None
    assert "{{entry_points_summary}}" not in explored["prompt"], "变量应被填充"


def _fake_build_result(*, markdown="## 候选", dom=1, fw=0, http=1, total=1):
    """构造可按位置解包成 5 元组的 AuthzTrackBuildResult（MagicMock 默认 __iter__ 返空，
    解包触发 'got 0' 错，故必须用真 NamedTuple）。dom/fw 控候选数量。"""
    from shannon_core.code_index.authz_gitnexus_track import AuthzTrackBuildResult
    return AuthzTrackBuildResult(
        markdown=markdown,
        dominance_candidates=[MagicMock() for _ in range(dom)],
        framework_candidates=[MagicMock() for _ in range(fw)],
        http_route_count=http,
        entry_point_total=total,
    )


def _noop_cm_factory():
    class _CM:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
    return lambda *a, **k: _CM()
