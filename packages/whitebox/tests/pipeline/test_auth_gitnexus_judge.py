"""spec-2b Task 5: auth GitNexus 轨判定段 + queue 追加（非覆盖）。

核心断言（不削弱）：
1. candidate_count>0 → 调 run_gitnexus_verdict_agent 多轮一次。
2. auth_gitnexus_queue.json 由 run_auth_config_scan 先产 2 条 config 类条目；
   本 activity 产 1 条逻辑类 verdict 后，queue 应有 3 条（2 保留 + 1 追加，非覆盖）。

mock 策略（对齐 test_authz_judge_deep.py）：
- get_audit_session → MagicMock，track_step 返 async cm，log_info 为 AsyncMock。
- build_auth_gitnexus_track / run_gitnexus_verdict_agent / _get_paths 直接 patch。
"""
import json

import pytest
from unittest.mock import AsyncMock, MagicMock


def _noop_cm_factory():
    class _CM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    return lambda *a, **k: _CM()


@pytest.mark.asyncio
async def test_auth_judge_multiturn_when_candidates_and_appends_queue(tmp_path, monkeypatch):
    """candidate_count>0 → run_gitnexus_verdict_agent 多轮；queue 追加（非覆盖）config_scan 的条目。"""
    import shannon_whitebox.pipeline.activities as act

    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    # config_scan 先产的 queue（2 条 config 类）
    (deliverables / "auth_gitnexus_queue.json").write_text(json.dumps({"vulnerabilities": [
        {"ID": "AUTH-GN-COOKIE-1", "vulnerability_type": "Session_Management_Flaw",
         "externally_exploitable": True, "confidence": "medium", "source_track": "gitnexus"},
        {"ID": "AUTH-GN-HSTS-1", "vulnerability_type": "Transport_Exposure",
         "externally_exploitable": True, "confidence": "medium", "source_track": "gitnexus"},
    ]}))

    # build_auth_gitnexus_track 产 1 个 session_regenerate_missing 候选
    from shannon_core.code_index.auth_gitnexus_track import (
        AuthTrackBuildResult, AuthCandidate, AuthCheckType, VerdictSignal,
    )
    fake_cand = AuthCandidate(
        id="a:h:session_regenerate_missing:1", handler_id="a:h",
        endpoint="POST /login", check_type=AuthCheckType.SESSION_REGENERATE_MISSING,
        verdict_signal=VerdictSignal.MISSING_POSITIVE, evidence_callee="session.regenerate",
        expected="regen", file_path="a.ts", line=1, code_snippet="...", confidence="high",
    )
    fake_result = AuthTrackBuildResult(
        markdown="## 候选", candidates=[fake_cand],
        handler_count=1, entry_point_total=1,
    )
    monkeypatch.setattr(
        "shannon_core.code_index.auth_gitnexus_track.build_auth_gitnexus_track",
        lambda d: fake_result,
    )

    verdict_called = {"n": 0}

    async def fake_verdict(*, prompt, repo_path, structured_output_schema=None, audit_session=None):
        verdict_called["n"] += 1
        r = MagicMock()
        r.structured_output = {"vulnerabilities": [{"ID": "AUTH-GN-LOGIC-1",
            "vulnerability_type": "Session_Management_Flaw", "externally_exploitable": True,
            "confidence": "high"}]}
        r.text = "{}"
        return r

    monkeypatch.setattr(act, "run_gitnexus_verdict_agent", fake_verdict)
    monkeypatch.setattr(act, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))

    # get_audit_session mock：track_step async cm + log_info async（brief 点 D）
    session = MagicMock()
    session.track_step = _noop_cm_factory()
    session.log_info = AsyncMock()
    monkeypatch.setattr(
        "shannon_whitebox.audit.session_registry.get_audit_session", lambda: session
    )

    inp = MagicMock()
    inp.workspace_name = "ws"
    inp.api_key = None

    await act.run_auth_gitnexus_judge(inp)
    assert verdict_called["n"] == 1, "应用多轮 verdict_agent"

    # queue 应有 3 条：2 config（保留）+ 1 逻辑（追加）
    q = json.loads((deliverables / "auth_gitnexus_queue.json").read_text())
    assert len(q["vulnerabilities"]) == 3, (
        f"queue 应追加到 3 条，实际 {len(q['vulnerabilities'])}"
    )
    # 2 条 config 类预置条目必须原样保留（防回归：丢/重排一条但 count 仍 == 3）。
    assert {"AUTH-GN-COOKIE-1", "AUTH-GN-HSTS-1"} <= {v["ID"] for v in q["vulnerabilities"]}
    # 追加的 verdict 条目标 source_track=gitnexus
    appended = q["vulnerabilities"][-1]
    assert appended["source_track"] == "gitnexus"


@pytest.mark.asyncio
async def test_auth_judge_explores_when_zero_candidates(tmp_path, monkeypatch):
    """spec-2b T6: candidate_count==0 → 触发自主探索（非静默空 queue）。

    确定性层 0 候选（常见于入口点未识别/auth handler 漏召回）时，多轮 agent
    自主 grep+read auth handler 补软候选（needs_review=True）。
    """
    import shannon_whitebox.pipeline.activities as act

    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)

    from shannon_core.code_index.auth_gitnexus_track import AuthTrackBuildResult
    fake_result = AuthTrackBuildResult(
        markdown="", candidates=[], handler_count=0, entry_point_total=0,
    )
    monkeypatch.setattr(
        "shannon_core.code_index.auth_gitnexus_track.build_auth_gitnexus_track",
        lambda d: fake_result,
    )

    explored = {"n": 0}

    async def fake_verdict(*, prompt, repo_path, structured_output_schema=None, audit_session=None):
        explored["n"] += 1
        # 探索 prompt 必须含 auth 探索词汇（login/session/explore 任一）
        assert (
            "login" in prompt.lower()
            or "session" in prompt.lower()
            or "explore" in prompt.lower()
        ), "0 候选应触发探索 prompt（含 login/session/explore 词汇）"
        r = MagicMock()
        r.structured_output = {"vulnerabilities": []}
        r.text = "{}"
        return r

    monkeypatch.setattr(act, "run_gitnexus_verdict_agent", fake_verdict)
    monkeypatch.setattr(act, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))

    # get_audit_session mock：track_step async cm + log_info async（复用 T5 mock 基建）
    session = MagicMock()
    session.track_step = _noop_cm_factory()
    session.log_info = AsyncMock()
    monkeypatch.setattr(
        "shannon_whitebox.audit.session_registry.get_audit_session", lambda: session
    )

    inp = MagicMock()
    inp.workspace_name = "ws"
    inp.api_key = None

    await act.run_auth_gitnexus_judge(inp)
    assert explored["n"] == 1, "0 候选应触发一次自主探索 verdict_agent"
