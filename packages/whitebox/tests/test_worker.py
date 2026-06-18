import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shannon_whitebox.pipeline.shared import PipelineInput


@pytest.mark.asyncio
async def test_run_scan_persists_session_data(tmp_path):
    """run_scan should create a session.json with repo_path via SessionManager."""
    from shannon_whitebox.pipeline.shared import PipelineState

    repo = tmp_path / "target-repo"
    repo.mkdir()

    input = PipelineInput(
        repo_path=str(repo),
        workspace_name="test-ws",
    )

    # Mock Temporal Client and Worker
    mock_result = PipelineState(status="completed")

    mock_handle = AsyncMock()
    mock_handle.result = AsyncMock(return_value=mock_result)
    mock_handle.query = AsyncMock(side_effect=Exception("no query in test"))

    mock_client = AsyncMock()
    mock_client.start_workflow = AsyncMock(return_value=mock_handle)

    mock_worker = AsyncMock()
    mock_worker.__aenter__ = AsyncMock(return_value=None)
    mock_worker.__aexit__ = AsyncMock(return_value=None)

    with patch("shannon_whitebox.worker.Client.connect", AsyncMock(return_value=mock_client)), \
         patch("shannon_whitebox.worker.Worker", return_value=mock_worker), \
         patch("shannon_whitebox.worker.ShutdownController.install"), \
         patch("shannon_whitebox.worker.ShutdownController.uninstall"):
        from shannon_whitebox.worker import run_scan
        result = await run_scan(input, "localhost:7233")

    # Verify session.json was created with repo_path
    session_file = tmp_path / "workspaces" / "test-ws" / "session.json"
    assert session_file.exists(), f"session.json not found at {session_file}"
    data = json.loads(session_file.read_text())
    assert data["repo_path"] == str(repo)

    # Verify enriched return dict
    assert result["workspace_name"] == "test-ws"
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_run_scan_uses_dynamic_task_queue(tmp_path):
    """run_scan should generate a unique task queue per scan, not use a fixed name."""
    from shannon_whitebox.pipeline.shared import PipelineState

    repo = tmp_path / "target-repo"
    repo.mkdir()

    input = PipelineInput(
        repo_path=str(repo),
        workspace_name="test-dynamic-tq",
    )

    mock_result = PipelineState(status="completed")

    mock_handle = AsyncMock()
    mock_handle.result = AsyncMock(return_value=mock_result)
    mock_handle.query = AsyncMock(side_effect=Exception("no query in test"))

    mock_client = AsyncMock()
    mock_client.start_workflow = AsyncMock(return_value=mock_handle)

    captured_task_queue = None

    def capture_worker(**kwargs):
        nonlocal captured_task_queue
        captured_task_queue = kwargs.get("task_queue")
        mock_worker = AsyncMock()
        mock_worker.__aenter__ = AsyncMock(return_value=None)
        mock_worker.__aexit__ = AsyncMock(return_value=None)
        return mock_worker

    with patch("shannon_whitebox.worker.Client.connect", AsyncMock(return_value=mock_client)), \
         patch("shannon_whitebox.worker.Worker", side_effect=capture_worker), \
         patch("shannon_whitebox.worker.ShutdownController.install"), \
         patch("shannon_whitebox.worker.ShutdownController.uninstall"):
        from shannon_whitebox.worker import run_scan
        await run_scan(input, "localhost:7233")

    # Task queue should have the shannon-py-wb prefix
    assert captured_task_queue is not None
    assert captured_task_queue.startswith("shannon-py-wb-"), f"Expected shannon-py-wb- prefix, got: {captured_task_queue}"
    suffix = captured_task_queue.removeprefix("shannon-py-wb-")
    assert len(suffix) == 8
    int(suffix, 16)  # must be valid hex


@pytest.mark.asyncio
async def test_run_scan_returns_cancelled_on_scan_cancelled(tmp_path):
    """run_scan should return {"status": "cancelled"} when the workflow is interrupted."""
    from shannon_core.runtime.scan_runner import ScanCancelled
    from shannon_whitebox.worker import run_scan

    repo = tmp_path / "target-repo"
    repo.mkdir()
    input = PipelineInput(repo_path=str(repo), workspace_name="ws-cancel")

    mock_client = AsyncMock()
    mock_client.start_workflow = AsyncMock(return_value=AsyncMock())
    mock_worker = AsyncMock()
    mock_worker.__aenter__ = AsyncMock(return_value=None)
    mock_worker.__aexit__ = AsyncMock(return_value=None)

    with patch("shannon_whitebox.worker.Client.connect", AsyncMock(return_value=mock_client)), \
         patch("shannon_whitebox.worker.Worker", return_value=mock_worker), \
         patch("shannon_whitebox.worker.ShutdownController.install"), \
         patch("shannon_whitebox.worker.ShutdownController.uninstall"), \
         patch("shannon_whitebox.worker.await_workflow_with_shutdown",
               AsyncMock(side_effect=ScanCancelled())), \
         patch("shannon_whitebox.audit.session_registry.clear_audit_session") as mock_clear:
        result = await run_scan(input, "localhost:7233")

    assert result == {"status": "cancelled"}
    mock_clear.assert_called()  # 清理在 cancel 路径仍执行


from shannon_whitebox.worker import resolve_workflow_id


def test_resolve_workflow_id_uses_workspace_name_when_given():
    assert resolve_workflow_id("my-ws", epoch=1000.0) == "my-ws"


def test_resolve_workflow_id_synthesizes_when_none():
    wid = resolve_workflow_id(None, epoch=1234567890.7)
    assert wid == "whitebox-1234567890"


def test_resolve_workflow_id_resume_count():
    # fresh: 不带 resume 计数
    assert resolve_workflow_id("ws", epoch=1.0, resume_attempt=0) == "ws"
    # resume: 带 -resume-{n}
    assert resolve_workflow_id("ws", epoch=1.0, resume_attempt=2) == "ws-resume-2"
    # 无 workspace + resume_attempt>0: 仍用 epoch（无 workspace 无意义加后缀）
    assert resolve_workflow_id(None, epoch=1000.0, resume_attempt=3) == "whitebox-1000"
    # 默认参数：向后兼容（等价 resume_attempt=0）
    assert resolve_workflow_id("ws", epoch=1.0) == "ws"


@pytest.mark.asyncio
async def test_run_scan_resume_rebuilds_completed_agents(tmp_path, monkeypatch):
    """worker resume 路径：build() 返回非空 completed_agents 时，
    - input.resume_completed_agents 被设置
    - workflow_id 含 -resume-{n}
    - meta.id 固定 = workspace_name（不是 workflow_id）
    - session.json 的 resumeAttempts 被追加一条
    """
    import json as _json
    from shannon_whitebox.pipeline.shared import PipelineState
    from shannon_whitebox.pipeline.whitebox_resume import WhiteboxResumeState

    repo = tmp_path / "target-repo"
    repo.mkdir()

    # 构造 workspaces_dir / workspace_name 让 builder 读到 session.json
    workspaces_dir = tmp_path / "workspaces"
    ws_name = "resume-ws"
    ws_dir = workspaces_dir / ws_name
    ws_dir.mkdir(parents=True)
    # builder._session_success 读 metrics.agents.<name>.success；这里不需要 success，
    # 因为 git_completed 由 mock 控制。但 session.json 需存在以便 resumeAttempts 读写。
    session_file = ws_dir / "session.json"
    session_file.write_text(_json.dumps({
        "web_url": "", "repo_path": str(repo), "scan_type": "whitebox",
        "completed_agents": [], "metrics": {"agents": {}},
        "resumeAttempts": [
            {"workflowId": "resume-ws-resume-1", "terminatedAgents": [], "checkpoint": None},
        ],
    }))

    # resolve_workspaces_dir 依赖 find_project_root / SHANNON_WORKER_ROOT；
    # 强制指向 tmp_path/workspaces。
    monkeypatch.setenv("SHANNON_WORKER_ROOT", str(tmp_path))

    input = PipelineInput(repo_path=str(repo), workspace_name=ws_name)

    # mock builder.build 返回有 completed_agents 的 state
    fake_state = WhiteboxResumeState(
        mode="auto", completed_agents=["pre_recon"], interrupted_agent="recon",
    )
    builder_build = AsyncMock(return_value=fake_state)
    builder_cleanup = AsyncMock(return_value=None)

    # patch builder 类
    fake_builder = MagicMock()
    fake_builder.build = builder_build
    fake_builder.cleanup = builder_cleanup
    with patch(
        "shannon_whitebox.pipeline.whitebox_resume.WhiteboxResumeStateBuilder",
        return_value=fake_builder,
    ):
        # mock git_completed（build 内部调 GitManager.get_completed_agents）
        with patch(
            "shannon_whitebox.pipeline.whitebox_resume.GitManager.get_completed_agents",
            AsyncMock(return_value={"pre_recon"}),
        ):
            # mock Temporal
            mock_result = PipelineState(status="completed")
            mock_handle = AsyncMock()
            mock_handle.result = AsyncMock(return_value=mock_result)
            mock_handle.query = AsyncMock(side_effect=Exception("no query"))
            mock_client = AsyncMock()
            mock_client.start_workflow = AsyncMock(return_value=mock_handle)
            mock_worker = AsyncMock()
            mock_worker.__aenter__ = AsyncMock(return_value=None)
            mock_worker.__aexit__ = AsyncMock(return_value=None)

            captured = {}

            def capture_start(*args, **kwargs):
                captured["workflow_id"] = kwargs.get("id")
                return mock_handle

            mock_client.start_workflow.side_effect = capture_start

            with patch("shannon_whitebox.worker.Client.connect",
                       AsyncMock(return_value=mock_client)), \
                 patch("shannon_whitebox.worker.Worker", return_value=mock_worker), \
                 patch("shannon_whitebox.worker.ShutdownController.install"), \
                 patch("shannon_whitebox.worker.ShutdownController.uninstall"):
                # capture meta via run_with_display（meta 在 run_scan 内局部构造）
                metas = []

                def capture_display(meta, *args, **kwargs):
                    metas.append(meta)
                    sess = MagicMock()
                    sess.log_workflow_complete = AsyncMock()

                    class _Ctx:
                        async def __aenter__(self_inner):
                            return sess

                        async def __aexit__(self_inner, *a):
                            return False
                    return _Ctx()

                with patch("shannon_whitebox.audit.display_lifecycle.run_with_display",
                           side_effect=capture_display):
                    from shannon_whitebox.worker import run_scan
                    result = await run_scan(input, "localhost:7233")

    # 断言
    assert input.resume_completed_agents == ["pre_recon"]
    # resumeAttempts 已有 1 条 → n = 2 → workflow_id 含 -resume-2
    assert captured["workflow_id"] == f"{ws_name}-resume-2", captured
    # meta.id 固定 = workspace_name（不是 resume-ws-resume-2）
    assert metas, "SessionMetadata 未被构造"
    assert metas[0].id == ws_name, metas[0].id
    # session.json resumeAttempts 被追加（2 → 3）
    data = _json.loads(session_file.read_text())
    assert len(data["resumeAttempts"]) == 2
    assert data["resumeAttempts"][-1]["workflowId"] == f"{ws_name}-resume-2"
    # builder.cleanup 被调用（auto 模式，不带 run_ts）
    builder_cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_scan_fresh_mode_skips_resume(tmp_path, monkeypatch):
    """fresh 模式（input._fresh=True）：跳过 resume 探测，workflow_id 不含 -resume-。"""
    from shannon_whitebox.pipeline.shared import PipelineState

    repo = tmp_path / "target-repo"
    repo.mkdir()
    monkeypatch.setenv("SHANNON_WORKER_ROOT", str(tmp_path))

    input = PipelineInput(repo_path=str(repo), workspace_name="fresh-ws")
    setattr(input, "_fresh", True)

    mock_result = PipelineState(status="completed")
    mock_handle = AsyncMock()
    mock_handle.result = AsyncMock(return_value=mock_result)
    mock_handle.query = AsyncMock(side_effect=Exception("no query"))
    mock_client = AsyncMock()
    captured = {}

    def capture_start(*args, **kwargs):
        captured["workflow_id"] = kwargs.get("id")
        return mock_handle

    mock_client.start_workflow = AsyncMock(side_effect=capture_start)
    mock_worker = AsyncMock()
    mock_worker.__aenter__ = AsyncMock(return_value=None)
    mock_worker.__aexit__ = AsyncMock(return_value=None)

    # builder.build 不应被调用 —— 用会爆炸的 mock 验证
    explode_build = AsyncMock(side_effect=AssertionError("fresh 模式不应调 builder.build"))
    with patch(
        "shannon_whitebox.pipeline.whitebox_resume.WhiteboxResumeStateBuilder"
    ) as MockBuilder:
        MockBuilder.return_value.build = explode_build
        with patch("shannon_whitebox.worker.Client.connect",
                   AsyncMock(return_value=mock_client)), \
             patch("shannon_whitebox.worker.Worker", return_value=mock_worker), \
             patch("shannon_whitebox.worker.ShutdownController.install"), \
             patch("shannon_whitebox.worker.ShutdownController.uninstall"):
            sess = MagicMock()
            sess.log_workflow_complete = AsyncMock()

            def fake_display(meta, *args, **kwargs):
                class _Ctx:
                    async def __aenter__(self_inner):
                        return sess

                    async def __aexit__(self_inner, *a):
                        return False
                return _Ctx()

            with patch("shannon_whitebox.audit.display_lifecycle.run_with_display",
                       side_effect=fake_display):
                from shannon_whitebox.worker import run_scan
                await run_scan(input, "localhost:7233")

    assert captured["workflow_id"] == "fresh-ws"
    explode_build.assert_not_called()


@pytest.mark.asyncio
async def test_resume_attempts_survive_metrics_tracker_initialize(tmp_path):
    """Important 回归保护：worker 写入的 **top-level** `resumeAttempts` 必须跨
    MetricsTracker.initialize() 存活。

    worker resume 路径把 resume 计数记录在 session.json 的 top-level
    `resumeAttempts`（与 repo_path 同层）。MetricsTracker.initialize() 只 owns
    `session`/`metrics` 两个子树，通过 ``dict(existing)`` 浅拷贝保留其它 top-level
    key —— 这是 top-level resumeAttempts 跨 init 存活的依赖点。本测试钉死该不变量：
    若未来有人改成整体覆盖写，此测试会立刻红。
    """
    from shannon_core.models.metrics import SessionMetadata
    from shannon_core.audit.metrics_tracker import MetricsTracker

    # 构造一个已有 top-level resumeAttempts（非空）的 workspace session.json
    workspaces_dir = tmp_path / "workspaces"
    ws_name = "survive-ws"
    ws_dir = workspaces_dir / ws_name
    ws_dir.mkdir(parents=True)
    session_file = ws_dir / "session.json"

    pre_attempts = [
        {"workflowId": f"{ws_name}-resume-1", "terminatedAgents": [], "checkpoint": None},
        {"workflowId": f"{ws_name}-resume-2", "terminatedAgents": ["recon"], "checkpoint": None},
    ]
    seed = {
        "web_url": "",
        "repo_path": str(tmp_path),
        "scan_type": "whitebox",
        # top-level resumeAttempts（worker 写入位置）
        "resumeAttempts": list(pre_attempts),
    }
    session_file.write_text(json.dumps(seed), encoding="utf-8")

    # MetricsTracker 用 meta.output_path + meta.id 解析 session.json 路径
    meta = SessionMetadata(
        id=ws_name,
        web_url="",
        repo_path=str(tmp_path),
        output_path=str(workspaces_dir),
    )

    tracker = MetricsTracker(meta)
    await tracker.initialize(workflow_id=f"{ws_name}-resume-3")

    # 断言：initialize 后 top-level resumeAttempts 仍在，且长度不变（未被重置/删除）
    after = json.loads(session_file.read_text(encoding="utf-8"))
    assert "resumeAttempts" in after, "top-level resumeAttempts 被 initialize 删掉了"
    assert isinstance(after["resumeAttempts"], list)
    assert len(after["resumeAttempts"]) == len(pre_attempts), (
        f"top-level resumeAttempts 长度变化："
        f"init 前={len(pre_attempts)} init 后={len(after['resumeAttempts'])}"
    )
    # 内容也未被动过
    assert after["resumeAttempts"] == pre_attempts

    # 同时确认 MetricsTracker 自己的 session.resumeAttempts（嵌套、audit 用）被初始化为空 ——
    # 这正是两个所有者刻意分离的证据：top-level 保留，嵌套重置。
    assert after["session"]["resumeAttempts"] == []


