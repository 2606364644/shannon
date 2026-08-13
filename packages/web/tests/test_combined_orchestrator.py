"""组合扫描接力编排（Task 4）：_combined_orchestrator + _run_blackbox_phase
+ 幂等 _ensure_scan_end + 复用 _submit_blackbox（workflow_id_suffix）。

核心不变量（spec §7.4 / bug-fix）：全场景 events 文件只有一个 scan_end。
- 成功路径：黑盒 finalize 已写 scan_end → _ensure_scan_end no-op（不写第二条）。
- 异常 / 跳过 / 提交失败：events 无 scan_end → _ensure_scan_end 补写防 _watch 永久 tail。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from supernova_web.components.scan_manager import ScanManager
from supernova_web.models import ScanRequest


# ── fixture ─────────────────────────────────────────────────────────────────
@pytest.fixture
def mgr(tmp_path):
    """最小 ScanManager（只用到 _workspaces_dir + session 读写 + _temporal_address 钩子）。"""
    m = ScanManager(workspaces_dir=tmp_path, repos_dir=tmp_path, config_store=object())
    # 跳过 Temporal 连接（这些测试不真连）。
    return m


# ── 成功路径 + 幂等 scan_end 核心守卫（spec §7.4，修原 bug）───────────────────
async def test_orchestrator_success_does_not_write_second_scan_end(mgr, tmp_path):
    """核心守卫（修原 bug）：成功路径黑盒 finalize 已写 scan_end，
    _run_blackbox_phase 必须 no-op（不写第二条 scan_end）。"""
    scan_dir = tmp_path / "repo-ts"; scan_dir.mkdir()
    (scan_dir / "deliverables" / "whitebox").mkdir(parents=True)
    (scan_dir / "deliverables" / "whitebox" / "recon_deliverable.md").write_text("x")
    (scan_dir / "deliverables" / "whitebox" / "injection_exploitation_queue.json").write_text(
        '{"vulnerabilities":[{"id":1}]}')
    (scan_dir / "events.ndjson").write_text('{"type":"scan_end","status":"completed"}\n')  # 黑盒已写
    (scan_dir / "blackbox-runs" / "run-1").mkdir(parents=True)  # run 子目录
    wb_handle = AsyncMock(); wb_handle.result = AsyncMock(return_value=None)
    bb_handle = MagicMock(); bb_handle.result = AsyncMock(return_value=None)
    with patch.object(mgr, "_submit_blackbox", new=AsyncMock(return_value=bb_handle)) as sb, \
         patch.object(mgr, "_generate_combined_report", new=AsyncMock()) as gcr, \
         patch.object(mgr, "_write_scan_end", new=AsyncMock()) as ws_end, \
         patch.object(mgr, "_mark_run", new=AsyncMock()):
        await mgr._run_blackbox_phase(scan_dir, "ws", "repo-ts", {"profile_id": None}, "run-1")
        sb.assert_awaited()                      # 提交黑盒（带 -bb-{K} suffix）
        assert sb.call_args.kwargs.get("workflow_id_suffix") == "-bb-1"
        ws_end.assert_not_awaited()              # 关键：scan_end 已在，不重复写
        gcr.assert_awaited_with(scan_dir, "run-1")  # 黑盒完成 → per-run 融合报告


# ── 跳过路径（白盒无可利用产物）──────────────────────────────────────────────
async def test_orchestrator_skips_when_no_deliverables(mgr, tmp_path):
    """无白盒产物 → orchestrator 建 run-1，_run_blackbox_phase 内部跳过（_mark_run
    skipped），不提交黑盒；编排 finally 经 _ensure_scan_end 补写 scan_end。"""
    from supernova_web.components.scan_store import ScanStore
    store = ScanStore(tmp_path); mgr._store = store
    wb_id, scan_dir = store.create_scan("ws", "http://t", "/code/x")
    # 无白盒产物（不建 recon/queue）
    (scan_dir / "events.ndjson").write_text('{"type":"PhaseEvent","phase":"whitebox"}\n')
    wb_handle = AsyncMock(); wb_handle.result = AsyncMock(return_value=None)
    scan_key = ("ws", wb_id)
    req = ScanRequest(type="whitebox", url="http://t/",
                      source={"kind": "repo", "value": "r"}, workspace="ws")
    with patch.object(mgr, "_submit_blackbox", new=AsyncMock()) as sb, \
         patch.object(mgr, "_generate_combined_report", new=AsyncMock()) as gcr, \
         patch.object(mgr, "_write_scan_end", new=AsyncMock()) as ws_end, \
         patch.object(mgr, "_mark_run", new=AsyncMock()) as mark:
        mgr._orchestrator_tasks[scan_key] = None
        await mgr._combined_orchestrator(scan_key, wb_handle, scan_dir, req)
        sb.assert_not_awaited()                  # 预检失败 → 不提交黑盒
        gcr.assert_not_awaited()                 # 不生成报告
        marked = [c.args for c in mark.call_args_list]
        assert any(a[2] == "skipped" for a in marked), \
            f"期望 _mark_run(scan_dir, run_id, 'skipped', ...)，实际: {marked}"
        ws_end.assert_awaited()                  # 编排 finally：无 scan_end → 补写
        assert scan_key not in mgr._orchestrator_tasks


# ── 版本化 run-1：组合接力白盒完成后建 run-1（spec §7.2）──────────────────────
async def test_combined_orchestrator_creates_run1_after_whitebox(mgr, tmp_path):
    """白盒完成 → orchestrator 建 run-1（blackbox-runs/run-1/session.json）→
    _run_blackbox_phase(run-1, -bb-1)（与手动 _add_blackbox_run 同路径）。"""
    from supernova_web.components.scan_store import ScanStore
    store = ScanStore(tmp_path); mgr._store = store
    wb_id, scan_dir = store.create_scan("ws", "http://t", "/code/x")
    (scan_dir / "deliverables" / "whitebox").mkdir(parents=True)
    (scan_dir / "deliverables" / "whitebox" / "recon_deliverable.md").write_text("x")
    (scan_dir / "deliverables" / "whitebox" / "injection_exploitation_queue.json").write_text(
        '{"vulnerabilities":[{"id":1}]}')
    wb_handle = AsyncMock(); wb_handle.result = AsyncMock(return_value=None)
    req = ScanRequest(type="whitebox", url="http://t/",
                      source={"kind": "repo", "value": "r"}, workspace="ws")
    with patch.object(mgr, "_run_blackbox_phase", new=AsyncMock()) as rbp:
        await mgr._combined_orchestrator(("ws", wb_id), wb_handle, scan_dir, req)
        rbp.assert_awaited()
        assert rbp.call_args.args[4] == "run-1"               # 第 5 参 run_id
        assert rbp.call_args.kwargs.get("workflow_id_suffix") == "-bb-1"
    assert (scan_dir / "blackbox-runs" / "run-1" / "session.json").exists()


# ── 编排成功路径幂等守卫（端到端：scan_end 已在 → 不写第二条）──────────────
async def test_orchestrator_success_path_idempotent_scan_end(mgr, tmp_path):
    """端到端核心守卫：scan_end 已在 → 编排 finally _ensure_scan_end no-op。"""
    from supernova_web.components.scan_store import ScanStore
    store = ScanStore(tmp_path); mgr._store = store
    wb_id, scan_dir = store.create_scan("ws", "http://t", "/code/x")
    (scan_dir / "deliverables" / "whitebox").mkdir(parents=True)
    (scan_dir / "deliverables" / "whitebox" / "recon_deliverable.md").write_text("x")
    (scan_dir / "deliverables" / "whitebox" / "injection_exploitation_queue.json").write_text(
        '{"vulnerabilities":[{"id":1}]}')
    # 黑盒 finalize 已写 scan_end（成功路径产物）
    (scan_dir / "events.ndjson").write_text('{"type":"scan_end","status":"completed"}\n')
    wb_handle = AsyncMock(); wb_handle.result = AsyncMock(return_value=None)
    scan_key = ("ws", wb_id)
    req = ScanRequest(type="whitebox", url="http://t/",
                      source={"kind": "repo", "value": "r"}, workspace="ws")
    with patch.object(mgr, "_run_blackbox_phase", new=AsyncMock()), \
         patch.object(mgr, "_write_scan_end", new=AsyncMock()) as ws_end:
        mgr._orchestrator_tasks[scan_key] = None
        await mgr._combined_orchestrator(scan_key, wb_handle, scan_dir, req)
        ws_end.assert_not_awaited()              # 核心：成功路径 scan_end 已在，不重复写
        assert scan_key not in mgr._orchestrator_tasks


# ── 异常路径（白盒失败 → _ensure_scan_end 补写）─────────────────────────────
async def test_orchestrator_exception_ensures_scan_end(mgr, tmp_path):
    """白盒 result() 抛异常 → run 未建（run_id None）→ 不标 run；finally _ensure_scan_end
    补写 scan_end（白盒 workflow 自身终态已落 session，此处只补 scan_end 防 tail）。"""
    from supernova_web.components.scan_store import ScanStore
    store = ScanStore(tmp_path); mgr._store = store
    wb_id, scan_dir = store.create_scan("ws", "http://t", "/code/x")
    (scan_dir / "events.ndjson").write_text('{"type":"PhaseEvent","phase":"whitebox"}\n')  # 无 scan_end
    wb_handle = AsyncMock()
    wb_handle.result = AsyncMock(side_effect=RuntimeError("wb boom"))
    scan_key = ("ws", wb_id)
    req = ScanRequest(type="whitebox", url="http://t/",
                      source={"kind": "repo", "value": "r"}, workspace="ws")
    with patch.object(mgr, "_run_blackbox_phase", new=AsyncMock()) as rbp, \
         patch.object(mgr, "_write_scan_end", new=AsyncMock()) as ws_end:
        mgr._orchestrator_tasks[scan_key] = None  # 预登记（模拟 start 写入）
        await mgr._combined_orchestrator(scan_key, wb_handle, scan_dir, req)
        rbp.assert_not_awaited()                  # 白盒抛 → 不建 run / 不进 _run_blackbox_phase
        ws_end.assert_awaited()                    # _ensure_scan_end 补写（events 无 scan_end）
        assert scan_key not in mgr._orchestrator_tasks  # finally pop


async def test_orchestrator_returned_whitebox_failure_stops_blackbox(mgr, tmp_path):
    """白盒 workflow 返回 status=failed（不抛异常）时不得继续提交黑盒。"""
    scan_dir = tmp_path / "ws" / "scans" / "repo-ts"
    scan_dir.mkdir(parents=True)
    (scan_dir / "session.json").write_text(
        '{"status":"running","combined":true,"bb_phase":"pending"}'
    )
    (scan_dir / "events.ndjson").write_text(
        '{"type":"PhaseEvent","phase":"whitebox"}\n'
    )
    wb_handle = MagicMock()
    wb_handle.result = AsyncMock(
        return_value={"status": "failed", "error": "whitebox failed"}
    )
    scan_key = ("ws", "repo-ts")
    req = ScanRequest(
        type="whitebox",
        url="http://t/",
        source={"kind": "repo", "value": "r"},
        workspace="ws",
    )
    with patch.object(mgr, "_run_blackbox_phase", new=AsyncMock()) as rbp, \
         patch.object(mgr, "_mark_bb", new=AsyncMock()) as mark, \
         patch.object(mgr, "_ensure_scan_end", new=AsyncMock()):
        mgr._orchestrator_tasks[scan_key] = None
        await mgr._combined_orchestrator(scan_key, wb_handle, scan_dir, req)

    rbp.assert_not_awaited()
    assert any(
        call.args[1] == "failed" and "whitebox failed" in str(call.args[2])
        for call in mark.await_args_list
    )


async def test_run_blackbox_phase_returned_failure_skips_combined_report(mgr, tmp_path):
    """黑盒 workflow 返回 status=failed 时不得生成融合报告或标记 completed。"""
    scan_dir = tmp_path / "ws" / "scans" / "repo-ts"
    scan_dir.mkdir(parents=True)
    (scan_dir / "session.json").write_text(
        '{"status":"running","combined":true,"bb_url":"http://t/"}'
    )
    wb = scan_dir / "deliverables" / "whitebox"
    wb.mkdir(parents=True)
    (wb / "recon_deliverable.md").write_text("recon")
    (wb / "injection_exploitation_queue.json").write_text(
        '{"vulnerabilities":[{"id":1}]}'
    )
    bb_handle = MagicMock()
    bb_handle.result = AsyncMock(
        return_value={"status": "failed", "error": "blackbox failed"}
    )
    with patch.object(mgr, "_submit_blackbox", new=AsyncMock(return_value=bb_handle)), \
         patch.object(mgr, "_generate_combined_report", new=AsyncMock()) as report, \
         patch.object(mgr, "_mark_bb", new=AsyncMock()) as mark:
        await mgr._run_blackbox_phase(scan_dir, "ws", "repo-ts", {"profile_id": None})

    report.assert_not_awaited()
    assert any(call.args[1] == "failed" for call in mark.await_args_list)


async def test_submit_whitebox_combined_flag_is_forwarded(mgr, tmp_path):
    """组合白盒提交必须把 combined=True 传入 PipelineInput。"""
    scan_dir = tmp_path / "ws" / "scans" / "repo-ts"
    scan_dir.mkdir(parents=True)
    (scan_dir / "session.json").write_text('{"status":"running"}')
    captured = {}

    class _FakeClient:
        async def start_workflow(self, fn, inp, **kwargs):
            captured["input"] = inp
            return object()

    async def _connect(_address):
        return _FakeClient()

    with patch(
        "supernova_web.components.scan_manager.Client.connect",
        new=_connect,
    ), patch.object(mgr, "_resolve_provider_config", return_value={}), \
         patch.object(mgr, "_mark_submitted_at"):
        await mgr._submit_whitebox(
            "/repo",
            "ws",
            "repo-ts",
            scan_dir,
            scan_dir / "events.ndjson",
            "http://t/",
            combined=True,
        )

    assert captured["input"].combined is True


async def test_run_blackbox_phase_without_auth_passes_no_config_path(mgr, tmp_path):
    """公开目标没有 scan-config.yaml 时，黑盒应收到 config_path=None。"""
    scan_dir = tmp_path / "ws" / "scans" / "repo-ts"
    scan_dir.mkdir(parents=True)
    (scan_dir / "session.json").write_text(
        '{"status":"running","combined":true,"bb_url":"http://t/"}'
    )
    wb = scan_dir / "deliverables" / "whitebox"
    wb.mkdir(parents=True)
    (wb / "recon_deliverable.md").write_text("recon")
    (wb / "xss_exploitation_queue.json").write_text(
        '{"vulnerabilities":[{"id":1}]}'
    )
    captured = {}
    bb_handle = MagicMock()
    bb_handle.result = AsyncMock(return_value={"status": "completed"})

    async def _submit(**kwargs):
        captured.update(kwargs)
        return bb_handle

    with patch.object(mgr, "_submit_blackbox", new=_submit), \
         patch.object(mgr, "_generate_combined_report", new=AsyncMock()), \
         patch.object(mgr, "_mark_bb", new=AsyncMock()):
        await mgr._run_blackbox_phase(scan_dir, "ws", "repo-ts", {"profile_id": None})

    assert captured["config_path"] is None


# ── _ensure_scan_end 幂等性直接守卫（核心 bug-fix 契约）──────────────────────
async def test_ensure_scan_end_noop_when_scan_end_present(mgr, tmp_path):
    """幂等契约：events 已有 scan_end → _ensure_scan_end no-op（不写第二条）。"""
    scan_dir = tmp_path / "repo-ts"; scan_dir.mkdir()
    (scan_dir / "events.ndjson").write_text(
        '{"type":"scan_end","status":"completed"}\n')
    with patch.object(mgr, "_write_scan_end", new=AsyncMock()) as ws_end:
        await mgr._ensure_scan_end(scan_dir)
        ws_end.assert_not_awaited()               # 核心：不重复写


async def test_ensure_scan_end_writes_when_absent(mgr, tmp_path):
    """幂等契约：events 无 scan_end → _ensure_scan_end 补写（status 透传）。"""
    scan_dir = tmp_path / "repo-ts"; scan_dir.mkdir()
    (scan_dir / "events.ndjson").write_text('{"type":"PhaseEvent"}\n')
    with patch.object(mgr, "_write_scan_end", new=AsyncMock()) as ws_end:
        await mgr._ensure_scan_end(scan_dir, status="failed")
        ws_end.assert_awaited_once()
        call = ws_end.call_args
        assert call.args[0] == scan_dir / "events.ndjson"   # event_file
        assert call.args[1] == "failed"                      # status
        assert call.args[2] == 0                             # returncode
        assert call.kwargs.get("scan_dir") == scan_dir       # scan_dir 透传（session 同步）


async def test_ensure_scan_end_noop_when_events_file_missing(mgr, tmp_path):
    """events 文件不存在 → _has_scan_end 返 False → 补写（防 _watch 永久 tail）。"""
    scan_dir = tmp_path / "repo-ts"; scan_dir.mkdir()
    with patch.object(mgr, "_write_scan_end", new=AsyncMock()) as ws_end:
        await mgr._ensure_scan_end(scan_dir)
        ws_end.assert_awaited_once()


# ── _whitebox_deliverables_ready 预检谓词 ────────────────────────────────────
def test_whitebox_deliverables_ready_true_when_recon_and_nonempty_queue(mgr, tmp_path):
    """recon_deliverable.md + 至少一个非空 queue → True。"""
    scan_dir = tmp_path / "repo"; scan_dir.mkdir()
    wb = scan_dir / "deliverables" / "whitebox"; wb.mkdir(parents=True)
    (wb / "recon_deliverable.md").write_text("recon")
    (wb / "injection_exploitation_queue.json").write_text(
        '{"vulnerabilities":[{"id":1}]}')
    assert mgr._whitebox_deliverables_ready(scan_dir) is True


def test_whitebox_deliverables_ready_false_when_only_recon(mgr, tmp_path):
    """有 recon 但无非空 queue → False。"""
    scan_dir = tmp_path / "repo"; scan_dir.mkdir()
    wb = scan_dir / "deliverables" / "whitebox"; wb.mkdir(parents=True)
    (wb / "recon_deliverable.md").write_text("recon")
    (wb / "xss_exploitation_queue.json").write_text('{"vulnerabilities":[]}')  # 空 queue
    assert mgr._whitebox_deliverables_ready(scan_dir) is False


def test_whitebox_deliverables_ready_false_when_no_recon(mgr, tmp_path):
    """缺 recon_deliverable.md → False（即便有 queue）。"""
    scan_dir = tmp_path / "repo"; scan_dir.mkdir()
    wb = scan_dir / "deliverables" / "whitebox"; wb.mkdir(parents=True)
    (wb / "injection_exploitation_queue.json").write_text(
        '{"vulnerabilities":[{"id":1}]}')
    assert mgr._whitebox_deliverables_ready(scan_dir) is False


# ── _submit_blackbox 零回归：workflow_id_suffix 默认 "" ──────────────────────
async def test_submit_blackbox_default_workflow_id_suffix_empty(mgr, tmp_path, monkeypatch):
    """零回归：_submit_blackbox 默认 workflow_id_suffix=""，既有调用 workflow_id 不变。

    断言 _resolve_workflow_id 结果直接作 workflow_id（无后缀追加）。通过 mock Client
    捕获 start_workflow 的 id 参数验证。
    """
    scan_dir = tmp_path / "ws" / "scans" / "scan-1"; scan_dir.mkdir(parents=True)
    # session.json 无 resumeAttempts → _resolve_workflow_id 返 "{ws}-{scan_id}"
    (scan_dir / "session.json").write_text('{"scan_type":"blackbox"}')
    captured_id = {}

    class _FakeHandle:
        id = "ws-scan-1"

    fake_client = MagicMock()
    fake_client.start_workflow = AsyncMock(return_value=_FakeHandle())

    with patch("supernova_web.components.scan_manager.Client") as ClientCls, \
         patch.object(mgr, "_mark_submitted_at"):
        ClientCls.connect = AsyncMock(return_value=fake_client)
        await mgr._submit_blackbox(
            repo_path="/repo", ws="ws", scan_id="scan-1", scan_dir=scan_dir,
            event_file=scan_dir / "events.ndjson", web_url="http://t/",
            config_path=None)  # 不传 workflow_id_suffix → 默认 ""
        start_call = fake_client.start_workflow.call_args
        # workflow_id = _resolve_workflow_id("ws","scan-1") + "" = "ws-scan-1"
        assert start_call.kwargs.get("id") == "ws-scan-1", (
            "默认 suffix='' 时 workflow_id 不应变（零回归）")


async def test_submit_blackbox_with_bb_suffix_appends(mgr, tmp_path, monkeypatch):
    """组合模式传 workflow_id_suffix='-bb' → workflow_id = base + '-bb'。"""
    scan_dir = tmp_path / "ws" / "scans" / "scan-1"; scan_dir.mkdir(parents=True)
    (scan_dir / "session.json").write_text('{"scan_type":"blackbox"}')

    class _FakeHandle:
        id = "ws-scan-1-bb"

    fake_client = MagicMock()
    fake_client.start_workflow = AsyncMock(return_value=_FakeHandle())

    with patch("supernova_web.components.scan_manager.Client") as ClientCls, \
         patch.object(mgr, "_mark_submitted_at"):
        ClientCls.connect = AsyncMock(return_value=fake_client)
        await mgr._submit_blackbox(
            repo_path="/repo", ws="ws", scan_id="scan-1", scan_dir=scan_dir,
            event_file=scan_dir / "events.ndjson", web_url="http://t/",
            config_path=None, workflow_id_suffix="-bb")
        start_call = fake_client.start_workflow.call_args
        assert start_call.kwargs.get("id") == "ws-scan-1-bb"


# ── per-run _run_blackbox_phase（T5，spec §4/§7.3）────────────────────────────
async def test_run_blackbox_phase_event_file_points_to_run_subdir(mgr, tmp_path):
    """_run_blackbox_phase(run_id)：event_file 指 blackbox-runs/run-K/events.ndjson，
    repo_path 仍指白盒任务根，workflow_id_suffix=-bb-{K}，phase 经 _mark_run（非 _mark_bb）。"""
    from supernova_web.components.scan_store import ScanStore
    store = ScanStore(tmp_path); mgr._store = store
    wb_id, scan_dir = store.create_scan("ws", "http://t", "/code/x")
    (scan_dir / "deliverables" / "whitebox").mkdir(parents=True)
    (scan_dir / "deliverables" / "whitebox" / "recon_deliverable.md").write_text("x")
    (scan_dir / "deliverables" / "whitebox" / "injection_exploitation_queue.json").write_text(
        '{"vulnerabilities":[{"id":1}]}')
    run_id, _ = store.create_blackbox_run("ws", wb_id)
    bb_handle = MagicMock(); bb_handle.result = AsyncMock(return_value=None)
    with patch.object(mgr, "_submit_blackbox", new=AsyncMock(return_value=bb_handle)) as sb, \
         patch.object(mgr, "_generate_combined_report", new=AsyncMock()) as gcr, \
         patch.object(mgr, "_mark_run", new=AsyncMock()) as mr:
        await mgr._run_blackbox_phase(scan_dir, "ws", wb_id, {"profile_id": None}, run_id)
        kwargs = sb.call_args.kwargs
        assert kwargs["event_file"] == scan_dir / "blackbox-runs" / "run-1" / "events.ndjson"
        assert kwargs["repo_path"] == str(scan_dir)  # 仍指白盒任务根
        assert kwargs["workflow_id_suffix"] == "-bb-1"
        phases = [c.args[2] for c in mr.call_args_list]  # (scan_dir, run_id, phase)
        assert "running" in phases and "completed" in phases
        gcr.assert_awaited_with(scan_dir, run_id)


async def test_generate_combined_report_writes_combined_run_dir(mgr, tmp_path):
    """_generate_combined_report(scan_dir, run_id) → combined/run-K/combined_report.md，
    读 deliverables/whitebox queue + blackbox-runs/run-K/deliverables/blackbox verdicts。"""
    from supernova_web.components.scan_store import ScanStore
    store = ScanStore(tmp_path); mgr._store = store
    wb_id, scan_dir = store.create_scan("ws", "http://t", "/code/x")
    run_id, _ = store.create_blackbox_run("ws", wb_id)
    (scan_dir / "deliverables" / "whitebox").mkdir(parents=True)
    (scan_dir / "deliverables" / "whitebox" / "injection_exploitation_queue.json").write_text(
        '{"vulnerabilities":[{"ID":"INJ-1"}]}')
    (scan_dir / "blackbox-runs" / "run-1" / "deliverables" / "blackbox").mkdir(parents=True)
    (scan_dir / "blackbox-runs" / "run-1" / "deliverables" / "blackbox"
     / "injection_exploit_verdicts.json").write_text(
        '{"verdicts":[{"vulnerability_id":"INJ-1","status":"exploited"}]}')
    await mgr._generate_combined_report(scan_dir, run_id)
    out = scan_dir / "combined" / "run-1" / "combined_report.md"
    assert out.exists()
    assert "| injection | 1 | 1 |" in out.read_text("utf-8")
