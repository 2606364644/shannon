"""TDD regressions for the blackbox HOST snapshot/lifecycle fix spec."""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from supernova_core.session import SessionManager
from supernova_web.components.host_profile_store import HostMapping, HostProfile, HostProfileStore
from supernova_web.components.scan_manager import ScanManager
from supernova_web.models import ScanRequest


def _bb(**overrides) -> ScanRequest:
    payload = {
        "type": "blackbox",
        "workspace": "ws-a",
        "url": "https://target.example/",
        "reuse_whitebox_scan_id": "wb-1",
    }
    payload.update(overrides)
    return ScanRequest(**payload)


def _make_scan_dir(root: Path, ws: str, scan_id: str, **data) -> Path:
    scan_dir = root / ws / "scans" / scan_id
    scan_dir.mkdir(parents=True, exist_ok=True)
    session = {
        "status": "interrupted",
        "scan_type": "blackbox",
        "created_at": time.time(),
        "web_url": "https://target.example/",
        "reuse_whitebox_scan_id": "wb-1",
    }
    session.update(data)
    (scan_dir / "session.json").write_text(json.dumps(session), encoding="utf-8")
    return scan_dir


def test_host_mapping_rejects_non_ipv4_and_ssrf_sensitive_ips():
    for ip in ("not-an-ip", "::1", "127.0.0.1", "169.254.1.1", "0.0.0.0"):
        with pytest.raises(ValidationError):
            HostMapping(ip=ip, host="api.internal.example")


def test_host_mapping_rejects_protocol_port_path_and_wildcard_hosts():
    for host in ("", "https://api.example", "api.example:443", "api.example/path", "*.example"):
        with pytest.raises(ValidationError):
            HostMapping(ip="10.0.0.2", host=host)


def test_host_profile_rejects_same_host_with_different_ips():
    with pytest.raises(ValidationError):
        HostProfile(
            name="bad",
            mappings=[
                HostMapping(ip="10.0.0.2", host="api.internal.example"),
                HostMapping(ip="10.0.0.3", host="API.INTERNAL.EXAMPLE"),
            ],
        )


def test_scan_request_rejects_explicit_empty_host_source():
    with pytest.raises(ValidationError):
        _bb(host_profile_id="")
    with pytest.raises(ValidationError):
        _bb(host_url="   ")


@pytest.mark.asyncio
async def test_resolve_host_mappings_rejects_empty_profile_snapshot(tmp_path):
    store = HostProfileStore(tmp_path)
    store.upsert_profile("ws-a", HostProfile(
        id="host-empty", name="empty", mappings=[]))
    manager = ScanManager(tmp_path, tmp_path, object(), host_profile_store=store)

    with pytest.raises(ValueError, match="mapping|映射"):
        await manager._resolve_host_mappings(_bb(host_profile_id="host-empty"), "ws-a")


@pytest.mark.asyncio
async def test_resolve_host_mappings_rejects_url_with_no_valid_mappings(tmp_path, monkeypatch):
    async def empty_fetch(url, timeout=15):
        return [], ["no valid mapping"]

    monkeypatch.setattr(
        "supernova_web.components.scan_manager.fetch_and_parse_hosts", empty_fetch)
    manager = ScanManager(tmp_path, tmp_path, object())

    with pytest.raises(ValueError, match="mapping|映射"):
        await manager._resolve_host_mappings(_bb(host_url="https://hosts.example/hosts"), "ws-a")


@pytest.mark.asyncio
async def test_profile_refresh_to_empty_mappings_fails_closed(tmp_path, monkeypatch):
    store = HostProfileStore(tmp_path)
    store.upsert_profile("ws-a", HostProfile(
        id="host-refresh", name="refresh", source_url="https://hosts.example/hosts",
        mappings=[HostMapping(ip="10.0.0.2", host="api.internal.example")],
    ))

    async def empty_refresh(ws, pid):
        return HostProfile(
            id=pid, name="refresh", source_url="https://hosts.example/hosts", mappings=[])

    monkeypatch.setattr(store, "refresh", empty_refresh)
    manager = ScanManager(tmp_path, tmp_path, object(), host_profile_store=store)

    with pytest.raises(ValueError, match="mapping|映射"):
        await manager._resolve_host_mappings(_bb(host_profile_id="host-refresh"), "ws-a")


@pytest.mark.asyncio
async def test_host_resolution_failure_does_not_create_scan(tmp_path, monkeypatch):
    manager = ScanManager(tmp_path, tmp_path, object())
    wb_id, _ = manager._store.create_scan("ws-a", "https://target.example/", "/repo", "whitebox")
    request = _bb(reuse_whitebox_scan_id=wb_id, host_url="https://hosts.example/hosts")

    async def empty_fetch(url, timeout=15):
        return [], []

    async def resolve_inputs(self, req):
        return "/repo", None

    monkeypatch.setattr(manager, "_check_temporal", AsyncMock(return_value=None))
    monkeypatch.setattr(manager, "_resolve_inputs", resolve_inputs.__get__(manager))
    monkeypatch.setattr(
        "supernova_web.components.scan_manager.fetch_and_parse_hosts", empty_fetch)

    with pytest.raises(ValueError, match="mapping|映射"):
        await manager.start(request)

    scans = manager._store.list_scans("ws-a")
    assert [scan.scan_id for scan in scans] == [wb_id]


@pytest.mark.asyncio
async def test_submit_failure_marks_scan_failed_and_writes_scan_end(tmp_path, monkeypatch):
    manager = ScanManager(tmp_path, tmp_path, object())
    wb_id, _ = manager._store.create_scan("ws-a", "https://target.example/", "/repo", "whitebox")
    request = _bb(reuse_whitebox_scan_id=wb_id)

    async def resolve_inputs(self, req):
        return "/repo", None

    monkeypatch.setattr(manager, "_check_temporal", AsyncMock(return_value=None))
    monkeypatch.setattr(manager, "_resolve_inputs", resolve_inputs.__get__(manager))
    monkeypatch.setattr(manager, "_submit_blackbox", AsyncMock(side_effect=RuntimeError("temporal down")))

    with pytest.raises(RuntimeError, match="temporal down"):
        await manager.start(request)

    # 黑盒 scan_id = {wb_id}~1（start 黑盒分支建平级 ~N；list_scans 隐藏 legacy ~N，
    # 但 get_scan_dir 仍可定位——验证 submit 失败标记 + scan_end + 清理）。
    bb_scan_id = f"{wb_id}~1"
    scan_dir = manager._store.get_scan_dir("ws-a", bb_scan_id)
    assert scan_dir is not None, "黑盒 ~N scan 应已创建"
    data = SessionManager(scan_dir.parent).get_session_data(scan_dir)
    assert data["status"] == "failed"
    assert data.get("completed_at") is not None
    assert any(json.loads(line).get("type") == "scan_end" for line in
               (scan_dir / "events.ndjson").read_text(encoding="utf-8").splitlines())
    key = ("ws-a", bb_scan_id)
    assert key not in manager._active_reqs
    assert key not in manager._handles
    assert key not in manager._tasks


@pytest.mark.asyncio
async def test_blackbox_resume_reuses_persisted_host_snapshot(tmp_path, monkeypatch):
    manager = ScanManager(tmp_path, tmp_path, object())
    wb_id, _ = manager._store.create_scan("ws-a", "https://target.example/", "/repo", "whitebox")
    scan_dir = _make_scan_dir(
        tmp_path,
        "ws-a",
        "bb-1",
        reuse_whitebox_scan_id=wb_id,
        host_config={
            "enabled": True,
            "source": "profile",
            "profile_id": "host-old",
            "mappings": {"target.example": "10.0.0.2"},
            "warnings": [],
            "resolved_at": 1.0,
        },
    )
    captured = {}

    async def fake_submit(*args, **kwargs):
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(manager, "_check_temporal", AsyncMock(return_value=None))
    monkeypatch.setattr(manager, "_submit_blackbox", fake_submit)
    monkeypatch.setattr(manager, "_watch", AsyncMock(return_value=None))

    await manager.resume("ws-a", "bb-1")

    assert captured["host_mappings"] == {"target.example": "10.0.0.2"}
    assert scan_dir.exists()


@pytest.mark.asyncio
async def test_combined_rerun_uses_host_snapshot_without_refresh(tmp_path, monkeypatch):
    manager = ScanManager(tmp_path, tmp_path, object())
    scan_dir = _make_scan_dir(
        tmp_path,
        "ws-a",
        "combined-1",
        status="failed",
        scan_type="whitebox",
        combined=True,
        bb_phase="failed",
        bb_url="https://target.example/",
        host_config={
            "enabled": True,
            "source": "profile",
            "profile_id": "host-old",
            "mappings": {"target.example": "10.0.0.2"},
            "warnings": [],
            "resolved_at": 1.0,
        },
    )
    wb = scan_dir / "deliverables" / "whitebox"
    wb.mkdir(parents=True)
    (wb / "recon_deliverable.md").write_text("recon", encoding="utf-8")
    (wb / "injection_exploitation_queue.json").write_text(
        '{"vulnerabilities":[{"id":"x"}]}', encoding="utf-8")
    captured = {}
    handle = MagicMock()
    handle.result = AsyncMock(return_value=None)

    async def fake_submit(*args, **kwargs):
        captured.update(kwargs)
        return handle

    monkeypatch.setattr(manager, "_submit_blackbox", fake_submit)
    monkeypatch.setattr(manager, "_generate_combined_report", AsyncMock())
    monkeypatch.setattr(manager, "_run_precheck", AsyncMock(return_value=True))
    monkeypatch.setattr(manager, "_ensure_scan_end", AsyncMock())

    await manager.rerun_blackbox("ws-a", "combined-1")
    for task in list(manager._orchestrator_tasks.values()):
        await task

    assert captured["host_mappings"] == {"target.example": "10.0.0.2"}


@pytest.mark.asyncio
async def test_resume_corrupt_host_snapshot_fails_without_running_state(tmp_path, monkeypatch):
    """损坏的已启用 snapshot 不能静默回退 DNS，也不能留下 running scan。"""
    manager = ScanManager(tmp_path, tmp_path, object())
    wb_id, _ = manager._store.create_scan("ws-a", "https://target.example/", "/repo", "whitebox")
    scan_dir = _make_scan_dir(
        tmp_path,
        "ws-a",
        "bb-corrupt",
        reuse_whitebox_scan_id=wb_id,
        host_config={"enabled": True, "source": "profile", "mappings": {}},
    )
    monkeypatch.setattr(manager, "_check_temporal", AsyncMock(return_value=None))

    with pytest.raises(ValueError, match="snapshot|HOST|mapping|映射"):
        await manager.resume("ws-a", "bb-corrupt")

    data = SessionManager(scan_dir.parent).get_session_data(scan_dir)
    assert data["status"] == "failed"
    assert data.get("completed_at") is not None
    assert any(json.loads(line).get("type") == "scan_end" for line in
               (scan_dir / "events.ndjson").read_text(encoding="utf-8").splitlines())


@pytest.mark.asyncio
async def test_combined_submit_failure_marks_scan_failed_not_running(tmp_path):
    """组合黑盒 workflow submit 失败也必须写 failed 终态，而非只写 bb_phase。"""
    manager = ScanManager(tmp_path, tmp_path, object())
    scan_dir = _make_scan_dir(
        tmp_path,
        "ws-a",
        "combined-submit-fail",
        status="running",
        scan_type="whitebox",
        combined=True,
        bb_phase="pending",
        bb_url="https://target.example/",
        host_config={
            "enabled": True,
            "source": "profile",
            "mappings": {"target.example": "10.0.0.2"},
        },
    )
    wb = scan_dir / "deliverables" / "whitebox"
    wb.mkdir(parents=True)
    (wb / "recon_deliverable.md").write_text("recon", encoding="utf-8")
    (wb / "injection_exploitation_queue.json").write_text(
        '{"vulnerabilities":[{"id":"x"}]}', encoding="utf-8")
    wb_handle = MagicMock()
    wb_handle.result = AsyncMock(return_value=None)
    manager._submit_blackbox = AsyncMock(side_effect=RuntimeError("temporal down"))

    await manager._combined_orchestrator(
        ("ws-a", "combined-submit-fail"), wb_handle, scan_dir,
        ScanRequest(type="whitebox", url="https://target.example/",
                    source={"kind": "repo", "value": "repo"}, workspace="ws-a"),
    )

    data = SessionManager(scan_dir.parent).get_session_data(scan_dir)
    assert data["status"] == "failed"
    assert data.get("completed_at") is not None
    events = (scan_dir / "events.ndjson").read_text(encoding="utf-8").splitlines()
    assert any(json.loads(line).get("status") == "failed" for line in events)


@pytest.mark.asyncio
async def test_host_url_warnings_are_persisted_in_scan_snapshot(tmp_path, monkeypatch):
    manager = ScanManager(tmp_path, tmp_path, object())
    wb_id, _ = manager._store.create_scan("ws-a", "https://target.example/", "/repo", "whitebox")

    async def fetch_with_warning(url, timeout=15):
        return [HostMapping(ip="10.0.0.2", host="target.example")], ["L4: malformed"]

    async def resolve_inputs(self, req):
        return "/repo", None

    monkeypatch.setattr(
        "supernova_web.components.scan_manager.fetch_and_parse_hosts", fetch_with_warning)
    monkeypatch.setattr(manager, "_check_temporal", AsyncMock(return_value=None))
    monkeypatch.setattr(manager, "_resolve_inputs", resolve_inputs.__get__(manager))
    monkeypatch.setattr(manager, "_submit_blackbox", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(manager, "_watch", AsyncMock(return_value=None))

    ws, scan_id = await manager.start(_bb(
        reuse_whitebox_scan_id=wb_id,
        host_url="https://hosts.example/hosts",
    ))
    scan_dir = manager._store.get_scan_dir(ws, scan_id)
    data = SessionManager(scan_dir.parent).get_session_data(scan_dir)
    assert data["host_config"]["mappings"] == {"target.example": "10.0.0.2"}
    assert data["host_config"]["warnings"] == ["L4: malformed"]


@pytest.mark.asyncio
async def test_profile_refresh_failure_keeps_old_snapshot_and_records_warning(tmp_path, monkeypatch):
    store = HostProfileStore(tmp_path)
    store.upsert_profile("ws-a", HostProfile(
        id="host-refresh-warning",
        name="refresh",
        source_url="https://hosts.example/hosts",
        mappings=[HostMapping(ip="10.0.0.2", host="target.example")],
    ))

    async def refresh_failed(ws, pid):
        raise OSError("provider unavailable")

    monkeypatch.setattr(store, "refresh", refresh_failed)
    manager = ScanManager(tmp_path, tmp_path, object(), host_profile_store=store)
    config = await manager._resolve_host_config(
        _bb(host_profile_id="host-refresh-warning"), "ws-a")
    assert config["mappings"] == {"target.example": "10.0.0.2"}
    assert "provider unavailable" in " ".join(config["warnings"])
