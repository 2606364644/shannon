"""Task 11: ScanRequest host fields + scan_manager 解析 host → mappings 注入。

测两层:
- ScanRequest model: host_profile_id / host_url 字段 + 互斥校验（与 auth 独立）。
- scan_manager._resolve_host_mappings: 选档案 / 填 GET 链接 / 都不填 → mappings dict；
  并经 _submit_blackbox 把 mappings 灌进 BlackboxPipelineInput.host_mappings（端到端）。

范式镜像 test_scan_manager_blackbox.py:mock Client.connect 捕获 BlackboxPipelineInput,
断言 host_mappings 字段;_resolve_host_mappings 单元测隔离 start() 的 create_scan/_watch 副作用。
"""
import pytest
from pydantic import ValidationError

from supernova_web.models import ScanRequest


# ---------------------------------------------------------------------------
# ScanRequest model: host fields + 互斥校验
# ---------------------------------------------------------------------------

def _bb(**kw):
    base = {"type": "blackbox", "reuse_whitebox_scan_id": "wb-1",
            "workspace": "ws1", "url": "http://x.test"}
    base.update(kw)
    return ScanRequest(**base)


def test_scan_request_accepts_host_profile_id():
    r = _bb(host_profile_id="host_abc")
    assert r.host_profile_id == "host_abc"
    assert r.host_url is None


def test_scan_request_accepts_host_url():
    r = _bb(host_url="https://h.test/get?id=1")
    assert r.host_url == "https://h.test/get?id=1"
    assert r.host_profile_id is None


def test_scan_request_host_profile_xor_url_both_set_rejected():
    """host_profile_id + host_url 同时填 → ValidationError（互斥）。"""
    with pytest.raises(ValidationError):
        _bb(host_profile_id="host_abc", host_url="https://h.test/get?id=1")


def test_scan_request_host_combines_with_auth_profile():
    """HOST 字段与 auth 字段独立（可组合：HOST 档案 + 认证档案）。"""
    r = _bb(host_profile_id="host_abc",
            auth_profile_id="prof_1", auth_credential_id="cred_a")
    assert r.host_profile_id == "host_abc"
    assert r.auth_profile_id == "prof_1"


def test_scan_request_host_combines_with_inline_auth():
    """HOST URL + inline authentication 也合法（HOST 与认证完全正交）。"""
    r = _bb(host_url="https://h.test/get?id=1",
            authentication={"login_type": "form", "login_url": "http://t/",
                            "credentials": {"username": "a"}})
    assert r.host_url == "https://h.test/get?id=1"
    assert r.authentication is not None


def test_scan_request_neither_host_field_ok():
    """都不填 → 合法（向后兼容，无 HOST 档案的既有扫描）。"""
    r = _bb()
    assert r.host_profile_id is None
    assert r.host_url is None


# ---------------------------------------------------------------------------
# scan_manager._resolve_host_mappings 单元测（隔离 start 副作用）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_host_mappings_from_profile_id(tmp_path):
    """host_profile_id → store.get → {host: ip} dict（host 已被 store 规范化为小写）。"""
    from supernova_web.components.scan_manager import ScanManager
    from supernova_web.components.host_profile_store import (
        HostMapping, HostProfile, HostProfileStore)

    store = HostProfileStore(tmp_path)
    store.upsert_profile("ws1", HostProfile(
        id="host_p1", name="P1",
        mappings=[HostMapping(ip="10.0.0.1", host="x.test")]))

    sm = ScanManager(workspaces_dir=tmp_path, repos_dir=tmp_path,
                     config_store=object(), host_profile_store=store)
    req = _bb(host_profile_id="host_p1")
    hm = await sm._resolve_host_mappings(req, "ws1")
    assert hm == {"x.test": "10.0.0.1"}


@pytest.mark.asyncio
async def test_resolve_host_mappings_from_url(tmp_path, monkeypatch):
    """host_url → fetch_and_parse_hosts → mappings dict（扫描启动时拉取）。"""
    from supernova_web.components.scan_manager import ScanManager
    from supernova_web.components.host_profile_store import HostMapping

    async def fake_fetch(url, timeout=15):
        return ([HostMapping(ip="10.0.0.2", host="y.test")], [])

    monkeypatch.setattr(
        "supernova_web.components.scan_manager.fetch_and_parse_hosts", fake_fetch)

    sm = ScanManager(workspaces_dir=tmp_path, repos_dir=tmp_path, config_store=object())
    req = _bb(host_url="https://h.test/get?id=1")
    hm = await sm._resolve_host_mappings(req, "ws1")
    assert hm == {"y.test": "10.0.0.2"}


@pytest.mark.asyncio
async def test_resolve_host_mappings_neither_empty(tmp_path):
    """都不填 → {} （向后兼容，无代理）。"""
    from supernova_web.components.scan_manager import ScanManager

    sm = ScanManager(workspaces_dir=tmp_path, repos_dir=tmp_path, config_store=object())
    req = _bb()
    hm = await sm._resolve_host_mappings(req, "ws1")
    assert hm == {}


@pytest.mark.asyncio
async def test_resolve_host_mappings_store_none_raises(tmp_path):
    """host_profile_id 设了但 host_profile_store 未注入 → RuntimeError（对齐 auth guards）。"""
    from supernova_web.components.scan_manager import ScanManager

    sm = ScanManager(workspaces_dir=tmp_path, repos_dir=tmp_path, config_store=object())
    req = _bb(host_profile_id="host_p1")
    with pytest.raises(RuntimeError, match="host_profile_store"):
        await sm._resolve_host_mappings(req, "ws1")


@pytest.mark.asyncio
async def test_resolve_host_mappings_profile_not_found_raises(tmp_path):
    """host_profile_id 在 store 中不存在 → ValueError（档案不存在）。"""
    from supernova_web.components.scan_manager import ScanManager
    from supernova_web.components.host_profile_store import HostProfileStore

    store = HostProfileStore(tmp_path)
    sm = ScanManager(workspaces_dir=tmp_path, repos_dir=tmp_path,
                     config_store=object(), host_profile_store=store)
    req = _bb(host_profile_id="host_missing")
    with pytest.raises(ValueError, match="HOST 档案不存在"):
        await sm._resolve_host_mappings(req, "ws1")


@pytest.mark.asyncio
async def test_resolve_host_mappings_refresh_failure_falls_back(tmp_path, monkeypatch):
    """profile.source_url 设了但 store.refresh 抛异常 → 不阻断，回落快照 mappings。

    Task 9 note: store.refresh 内部 try/except 只覆盖 fetch 不覆盖最终 write，
    故 scan_manager 再包一层 try/except 防御 write 失败（best-effort）。
    """
    from supernova_web.components.scan_manager import ScanManager
    from supernova_web.components.host_profile_store import (
        HostMapping, HostProfile, HostProfileStore)

    store = HostProfileStore(tmp_path)
    store.upsert_profile("ws1", HostProfile(
        id="host_p1", name="P1", source_url="https://h.test/get?id=1",
        mappings=[HostMapping(ip="10.0.0.1", host="x.test")]))

    # 让 refresh 抛异常（模拟 write 失败等 fetch 之外的异常）
    async def boom_refresh(ws, pid):
        raise OSError("disk full")

    # Store 不是 monkeypatch 属性的直接目标——拿实例方法替换
    monkeypatch.setattr(store, "refresh", boom_refresh)

    sm = ScanManager(workspaces_dir=tmp_path, repos_dir=tmp_path,
                     config_store=object(), host_profile_store=store)
    req = _bb(host_profile_id="host_p1")
    hm = await sm._resolve_host_mappings(req, "ws1")
    assert hm == {"x.test": "10.0.0.1"}  # 回落到存储快照


@pytest.mark.asyncio
async def test_resolve_host_mappings_refresh_success_uses_fresh(tmp_path, monkeypatch):
    """profile.source_url + refresh 成功 → 用刷新后的新 mappings（非旧快照）。"""
    from supernova_web.components.scan_manager import ScanManager
    from supernova_web.components.host_profile_store import (
        HostMapping, HostProfile, HostProfileStore)

    store = HostProfileStore(tmp_path)
    store.upsert_profile("ws1", HostProfile(
        id="host_p1", name="P1", source_url="https://h.test/get?id=1",
        mappings=[HostMapping(ip="10.0.0.1", host="x.test")]))

    # refresh 写入新 mappings（模拟拉到最新 /etc/hosts）
    async def fake_refresh(ws, pid):
        fresh = store.get(ws, pid)
        fresh.mappings = [HostMapping(ip="10.0.0.9", host="z.test")]
        return store.upsert_profile(ws, fresh)

    monkeypatch.setattr(store, "refresh", fake_refresh)

    sm = ScanManager(workspaces_dir=tmp_path, repos_dir=tmp_path,
                     config_store=object(), host_profile_store=store)
    req = _bb(host_profile_id="host_p1")
    hm = await sm._resolve_host_mappings(req, "ws1")
    assert hm == {"z.test": "10.0.0.9"}  # 用刷新后的


# ---------------------------------------------------------------------------
# 端到端：_submit_blackbox 把 host_mappings 灌进 BlackboxPipelineInput
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_submit_blackbox_injects_host_mappings(tmp_path, monkeypatch):
    """_submit_blackbox 接 host_mappings → BlackboxPipelineInput.host_mappings 同值。"""
    from supernova_web.components.scan_manager import ScanManager

    captured: dict = {}

    class FakeHandle:
        pass

    class FakeClient:
        async def start_workflow(self, fn, inp, **kw):
            captured["inp"] = inp
            return FakeHandle()

    async def fake_connect(addr):
        return FakeClient()

    monkeypatch.setattr("supernova_web.components.scan_manager.Client.connect", fake_connect)

    sm = ScanManager(workspaces_dir=tmp_path, repos_dir=tmp_path, config_store=object())
    sm._mark_submitted_at = lambda scan_dir: None

    scan_dir = tmp_path / "ws-a" / "scans" / "s1"
    scan_dir.mkdir(parents=True)
    await sm._submit_blackbox(
        repo_path="/r", ws="ws-a", scan_id="s1", scan_dir=scan_dir,
        event_file=scan_dir / "events.ndjson", web_url="https://target.example",
        config_path=None,
        host_mappings={"x.test": "10.0.0.1"},
    )
    assert captured["inp"].host_mappings == {"x.test": "10.0.0.1"}


@pytest.mark.asyncio
async def test_submit_blackbox_host_mappings_defaults_empty(tmp_path, monkeypatch):
    """_submit_blackbox 不传 host_mappings → BlackboxPipelineInput.host_mappings == {}（向后兼容）。"""
    from supernova_web.components.scan_manager import ScanManager

    captured: dict = {}

    class FakeHandle:
        pass

    class FakeClient:
        async def start_workflow(self, fn, inp, **kw):
            captured["inp"] = inp
            return FakeHandle()

    async def fake_connect(addr):
        return FakeClient()

    monkeypatch.setattr("supernova_web.components.scan_manager.Client.connect", fake_connect)

    sm = ScanManager(workspaces_dir=tmp_path, repos_dir=tmp_path, config_store=object())
    sm._mark_submitted_at = lambda scan_dir: None

    scan_dir = tmp_path / "ws-a" / "scans" / "s1"
    scan_dir.mkdir(parents=True)
    await sm._submit_blackbox(
        repo_path="/r", ws="ws-a", scan_id="s1", scan_dir=scan_dir,
        event_file=scan_dir / "events.ndjson", web_url="https://target.example",
        config_path=None,
    )
    assert captured["inp"].host_mappings == {}


# ---------------------------------------------------------------------------
# 端到端：start() 整链路 - host_profile_id 经 resolve → submit 注入
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_blackbox_with_host_profile_id_threads_to_pipeline_input(
        tmp_path, monkeypatch):
    """start(blackbox, host_profile_id=...) → store.get → host_mappings 灌入 BlackboxPipelineInput。

  端到端验证 Task 11 主链路（resolve 在 start 内、_submit_blackbox 灌入），用真
  HostProfileStore.upsert_profile 建档，mock temporal 提交捕获 input。
  """
    from unittest.mock import AsyncMock, MagicMock
    from supernova_web.components.scan_manager import ScanManager
    from supernova_web.components.host_profile_store import (
        HostMapping, HostProfile, HostProfileStore)

    sm = ScanManager(workspaces_dir=tmp_path, repos_dir=tmp_path, config_store=object(),
                     host_profile_store=HostProfileStore(tmp_path))
    sm.host_profile_store.upsert_profile("WS1", HostProfile(
        id="host_p1", name="P1",
        mappings=[HostMapping(ip="10.0.0.1", host="x.test")]))

    monkeypatch.setattr(sm, "_check_temporal", AsyncMock(return_value=None))
    captured: dict = {}
    mock_handle = MagicMock()
    mock_client = AsyncMock()

    async def start_workflow(fn, inp, **kw):
        captured["inp"] = inp
        return mock_handle

    mock_client.start_workflow = start_workflow
    monkeypatch.setattr("supernova_web.components.scan_manager.Client.connect",
                        AsyncMock(return_value=mock_client))
    monkeypatch.setattr(ScanManager, "_watch", AsyncMock(return_value=None))

    wb_scan_id, _ = sm._store.create_scan(
        "WS1", "http://e", "/code/NodeGoat", "whitebox")

    ws, scan_id = await sm.start(ScanRequest(
        type="blackbox", url="http://e", workspace="WS1",
        reuse_whitebox_scan_id=wb_scan_id, host_profile_id="host_p1"))

    assert captured["inp"].host_mappings == {"x.test": "10.0.0.1"}


@pytest.mark.asyncio
async def test_start_blackbox_with_host_url_threads_to_pipeline_input(
        tmp_path, monkeypatch):
    """start(blackbox, host_url=...) → fetch_and_parse_hosts → mappings 灌入。"""
    from unittest.mock import AsyncMock, MagicMock
    from supernova_web.components.scan_manager import ScanManager
    from supernova_web.components.host_profile_store import HostMapping

    async def fake_fetch(url, timeout=15):
        return ([HostMapping(ip="10.0.0.2", host="y.test")], [])

    monkeypatch.setattr(
        "supernova_web.components.scan_manager.fetch_and_parse_hosts", fake_fetch)

    sm = ScanManager(workspaces_dir=tmp_path, repos_dir=tmp_path, config_store=object())
    monkeypatch.setattr(sm, "_check_temporal", AsyncMock(return_value=None))
    captured: dict = {}
    mock_handle = MagicMock()
    mock_client = AsyncMock()

    async def start_workflow(fn, inp, **kw):
        captured["inp"] = inp
        return mock_handle

    mock_client.start_workflow = start_workflow
    monkeypatch.setattr("supernova_web.components.scan_manager.Client.connect",
                        AsyncMock(return_value=mock_client))
    monkeypatch.setattr(ScanManager, "_watch", AsyncMock(return_value=None))

    wb_scan_id, _ = sm._store.create_scan(
        "WS1", "http://e", "/code/NodeGoat", "whitebox")

    ws, scan_id = await sm.start(ScanRequest(
        type="blackbox", url="http://e", workspace="WS1",
        reuse_whitebox_scan_id=wb_scan_id, host_url="https://h.test/get?id=1"))

    assert captured["inp"].host_mappings == {"y.test": "10.0.0.2"}


@pytest.mark.asyncio
async def test_start_blackbox_without_host_fields_empty_mappings(
        tmp_path, monkeypatch):
    """start(blackbox) 无 host_profile_id/url → host_mappings == {}（向后兼容，既有扫描字节不变）。"""
    from unittest.mock import AsyncMock, MagicMock
    from supernova_web.components.scan_manager import ScanManager

    sm = ScanManager(workspaces_dir=tmp_path, repos_dir=tmp_path, config_store=object())
    monkeypatch.setattr(sm, "_check_temporal", AsyncMock(return_value=None))
    captured: dict = {}
    mock_handle = MagicMock()
    mock_client = AsyncMock()

    async def start_workflow(fn, inp, **kw):
        captured["inp"] = inp
        return mock_handle

    mock_client.start_workflow = start_workflow
    monkeypatch.setattr("supernova_web.components.scan_manager.Client.connect",
                        AsyncMock(return_value=mock_client))
    monkeypatch.setattr(ScanManager, "_watch", AsyncMock(return_value=None))

    wb_scan_id, _ = sm._store.create_scan(
        "WS1", "http://e", "/code/NodeGoat", "whitebox")

    ws, scan_id = await sm.start(ScanRequest(
        type="blackbox", url="http://e", workspace="WS1",
        reuse_whitebox_scan_id=wb_scan_id))

    assert captured["inp"].host_mappings == {}
