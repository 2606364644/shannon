# packages/whitebox/tests/test_authz_gitnexus_judge_failfast.py
"""Task 3 fail-fast: authz GitNexus judge 返 failed(业务 fail 不 raise / 探索保留)。

契约：
- build_authz_gitnexus_track 抛（code_index/framework 缺）-> 返 failed=True，不 raise。
- 0 候选 -> 探索 agent 正常返回（即使空）-> failed=False（概念 A：探索是 GitNexus
  轨内部 LLM 补召回，非业务 fail）。
- candidate>0 -> verdict agent 抛 -> 返 failed=True，fail_reason 含 "verdict agent"。
- 真系统异常仍 raise ApplicationFailure（外层 except 保留，由别处测）。

复用现有 test_run_authz_gitnexus_judge.py 的 mock 风格（_FakeInput / _get_paths patch /
session_registry.get_audit_session / _noop_cm_factory）。
"""
import json
from unittest.mock import patch, AsyncMock

import pytest

from shannon_whitebox.pipeline import activities


class _FakeInput:
    def __init__(self, tmp_path):
        self.agent_name = None
        self.web_url = None
        self.repo_path = str(tmp_path)
        self.deliverables_subdir = None
        self.workspace_name = None
        self.config_path = None
        self.api_key = None
        self.pipeline_testing_mode = False
        self.prompt_override = None


def _noop_cm_factory():
    class _CM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    return lambda *a, **k: _CM()


def _write_empty_code_index(tmp_path):
    """落 deliverables/whitebox/code_index.json 无候选（match 0-candidate 探索分支意图）。"""
    dlv = tmp_path / "whitebox"
    dlv.mkdir(parents=True, exist_ok=True)
    (dlv / "code_index.json").write_text(json.dumps({
        "repository": "r", "language": "typescript", "total_blocks": 0,
        "total_entry_points": 0, "total_chains": 0, "blocks": [], "edges": [],
        "entry_points": [], "chains": [],
    }))


@pytest.mark.asyncio
async def test_build_track_failure_returns_failed_not_raise(tmp_path):
    """业务 fail: build_authz_gitnexus_track raises -> 返 failed=True，不 raise。

    模拟 code_index.json 缺失 / 框架产物缺（build 内部判 fail 抛 FileNotFoundError）。
    """
    # deliverables 子目录需存在供 atomic_write_json 落 queue（parent mkdir 兜底也行）。
    (tmp_path / "whitebox").mkdir(parents=True, exist_ok=True)

    def fake_build(deliverables_dir):
        raise FileNotFoundError("code_index.json missing")

    with patch.object(activities, "_get_paths", return_value=(tmp_path, tmp_path / "whitebox", tmp_path)):
        with patch("shannon_core.code_index.authz_gitnexus_track.build_authz_gitnexus_track", new=fake_build):
            with patch("shannon_whitebox.audit.session_registry.get_audit_session") as gs:
                inst = gs.return_value
                inst.track_step = _noop_cm_factory()
                inst.log_info = AsyncMock()
                result = await activities.run_authz_gitnexus_judge(_FakeInput(tmp_path))

    assert result["failed"] is True
    assert "build_authz_gitnexus_track" in result["fail_reason"]
    assert "code_index" in result["fail_reason"]
    # queue 写空（业务 fail 也写空 queue，下游 merger 不崩）
    queue_path = tmp_path / "whitebox" / "authz_gitnexus_queue.json"
    assert queue_path.exists()
    data = json.loads(queue_path.read_text())
    assert data["vulnerabilities"] == []


@pytest.mark.asyncio
async def test_explore_branch_not_failed(tmp_path):
    """0 候选 -> 探索 agent 正常返空 -> failed=False（概念 A 保留：探索非业务 fail）。"""
    _write_empty_code_index(tmp_path)

    async def fake_run(prompt, **kwargs):
        return type("R", (), {
            "success": True, "structured_output": {"vulnerabilities": []},
            "text": "{}",
        })()

    with patch.object(activities, "_get_paths", return_value=(tmp_path, tmp_path / "whitebox", tmp_path)):
        with patch("shannon_whitebox.pipeline.activities.run_gitnexus_verdict_agent", new=fake_run):
            with patch("shannon_whitebox.audit.session_registry.get_audit_session") as gs:
                inst = gs.return_value
                inst.track_step = _noop_cm_factory()
                inst.log_info = AsyncMock()
                result = await activities.run_authz_gitnexus_judge(_FakeInput(tmp_path))

    assert result["failed"] is False
    assert result["fail_reason"] is None


@pytest.mark.asyncio
async def test_verdict_agent_exception_marks_failed(tmp_path):
    """candidate>0 + verdict agent raises -> failed=True，fail_reason 含 'verdict agent'。

    强制 candidate>0：mock build_authz_gitnexus_track 返非空 dom_cands
    （绕开 fixture 不被 dominance 识别的预存 xfail 根因）。
    """
    (tmp_path / "whitebox").mkdir(parents=True, exist_ok=True)

    def fake_build(deliverables_dir):
        # (md, dom_cands, fw_cands, http_route_count, entry_point_total)
        return ("fake candidates markdown", [("fake_dom_candidate",)], [], 1, 1)

    async def fake_run_raises(prompt, **kwargs):
        raise RuntimeError("LLM API connection refused")

    with patch.object(activities, "_get_paths", return_value=(tmp_path, tmp_path / "whitebox", tmp_path)):
        with patch("shannon_core.code_index.authz_gitnexus_track.build_authz_gitnexus_track", new=fake_build):
            with patch("shannon_whitebox.pipeline.activities.run_gitnexus_verdict_agent", new=fake_run_raises):
                with patch("shannon_whitebox.audit.session_registry.get_audit_session") as gs:
                    inst = gs.return_value
                    inst.track_step = _noop_cm_factory()
                    inst.log_info = AsyncMock()
                    result = await activities.run_authz_gitnexus_judge(_FakeInput(tmp_path))

    assert result["failed"] is True
    assert "verdict agent" in result["fail_reason"]
    # queue 仍写空（agent 异常 -> vulnerabilities=[] -> 写空不崩 merger）
    queue_path = tmp_path / "whitebox" / "authz_gitnexus_queue.json"
    assert queue_path.exists()
    data = json.loads(queue_path.read_text())
    assert data["vulnerabilities"] == []
