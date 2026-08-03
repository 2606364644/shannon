"""黑盒 web 化（C1 Phase B + 登录配置）：scan_manager._submit_blackbox + _resolve_blackbox_inputs。

直测 _submit_blackbox / _resolve_blackbox_inputs（隔离 start() 的 create_scan/_watch 副作用）：
mock Client.connect 捕获 BlackboxPipelineInput，断言 provider_config/event_file/task_queue；
_resolve_blackbox_inputs 测 auth→scan-config.yaml、reuse→repo_path=wb scan_dir、校验失败 ValueError。
"""
from pathlib import Path

import pytest


async def test_submit_blackbox_injects_event_file_provider_config_and_queue(tmp_path, monkeypatch):
    """_submit_blackbox 塞 event_file（worker 路径）+ provider_config + 提交到 supernova-bb-web。"""
    from supernova_web.components.scan_manager import ScanManager

    captured: dict = {}

    class FakeHandle:
        pass

    class FakeClient:
        async def start_workflow(self, fn, inp, **kw):
            captured["inp"] = inp
            captured["kw"] = kw
            return FakeHandle()

    async def fake_connect(addr):
        return FakeClient()

    monkeypatch.setattr("supernova_web.components.scan_manager.Client.connect", fake_connect)

    sm = ScanManager(workspaces_dir=tmp_path, repos_dir=tmp_path, config_store=object())
    sm._mark_submitted_at = lambda scan_dir: None  # 聚焦穿线，跳过 session.json 写

    scan_dir = tmp_path / "ws-a" / "scans" / "s1"
    scan_dir.mkdir(parents=True)
    await sm._submit_blackbox(
        repo_path="/r", ws="ws-a", scan_id="s1", scan_dir=scan_dir,
        event_file=scan_dir / "events.ndjson", web_url="https://target.example",
        config_path=None,
    )

    inp = captured["inp"]
    assert inp is not None
    assert inp.web_url == "https://target.example"
    assert inp.workspace_name == "s1"
    # event_file 非 None → workflow 走 worker 路径（setup_display 注入 StructuredEventRenderer）
    assert inp.event_file == str(scan_dir / "events.ndjson")
    assert inp.provider_config is not None            # per-ws 解析（或全局 env 兜底），web 路径非 None
    assert inp.workspaces_root == str(tmp_path)       # web 已知 workspaces_dir
    assert captured["kw"]["task_queue"] == "supernova-bb-web"


async def test_resolve_blackbox_inputs_writes_auth_yaml(tmp_path):
    """authentication dict → scan-config.yaml（parse_config 可读的 {authentication: {...}}）。"""
    import yaml
    from supernova_web.components.scan_manager import ScanManager
    from supernova_web.models import ScanRequest

    sm = ScanManager(workspaces_dir=tmp_path, repos_dir=tmp_path, config_store=object())
    wb_scan_id, wb_scan_dir = sm._store.create_scan(
        "ws-a", "https://t.example", "/r", "whitebox")
    scan_dir = tmp_path / "ws-a" / "scans" / "s1"
    scan_dir.mkdir(parents=True)
    req = ScanRequest(type="blackbox", url="https://t.example", workspace="ws-a",
                      reuse_whitebox_scan_id=wb_scan_id, authentication={
        "login_type": "form",
        "login_url": "https://t.example/login",
        "credentials": {"username": "admin", "password": "pw"},
        "success_condition": {"type": "url_contains", "value": "/dashboard"},
    })
    config_path, repo_path = await sm._resolve_blackbox_inputs(req, "ws-a", scan_dir, target=None)

    assert config_path is not None and config_path.endswith("scan-config.yaml")
    parsed = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    assert parsed["authentication"]["login_type"] == "form"
    assert parsed["authentication"]["credentials"]["username"] == "admin"
    assert parsed["authentication"]["success_condition"]["value"] == "/dashboard"
    assert repo_path == str(wb_scan_dir)  # 黑盒恒复用白盒 → repo_path = wb scan_dir


async def test_resolve_blackbox_inputs_reuse_whitebox_scan(tmp_path):
    """reuse_whitebox_scan_id → 该白盒 scan_dir 作 repo_path（detect_whitebox_results 复用源）。"""
    from supernova_web.components.scan_manager import ScanManager
    from supernova_web.models import ScanRequest

    sm = ScanManager(workspaces_dir=tmp_path, repos_dir=tmp_path, config_store=object())
    wb_scan_id, wb_scan_dir = sm._store.create_scan(
        "ws-a", "https://t.example", "/r", "whitebox")
    scan_dir = tmp_path / "ws-a" / "scans" / "bb1"
    scan_dir.mkdir(parents=True)
    req = ScanRequest(type="blackbox", url="https://t.example", workspace="ws-a",
                      reuse_whitebox_scan_id=wb_scan_id)

    config_path, repo_path = await sm._resolve_blackbox_inputs(req, "ws-a", scan_dir, target=None)

    assert repo_path == str(wb_scan_dir)
    assert config_path is None  # 无 auth


async def test_resolve_blackbox_inputs_requires_reuse(tmp_path):
    """黑盒无 reuse_whitebox_scan_id → ValueError（工作层兜底）。

    ScanRequest model_validator 已在构造时拒绝无 reuse 的 blackbox；此处合法构造后清空 reuse，
    隔离测 _resolve_blackbox_inputs 的工作层防御（防 model 层被误改 / req 被直接篡改）。
    """
    from supernova_web.components.scan_manager import ScanManager
    from supernova_web.models import ScanRequest

    sm = ScanManager(workspaces_dir=tmp_path, repos_dir=tmp_path, config_store=object())
    scan_dir = tmp_path / "ws-a" / "scans" / "bb1"
    scan_dir.mkdir(parents=True)
    req = ScanRequest(type="blackbox", url="https://t.example", workspace="ws-a",
                      reuse_whitebox_scan_id="some-wb")
    req.reuse_whitebox_scan_id = None  # 绕过 model_validator，测工作层兜底
    with pytest.raises(ValueError):
        await sm._resolve_blackbox_inputs(req, "ws-a", scan_dir, target="/repos/myrepo")


async def test_resolve_blackbox_inputs_invalid_authentication_raises(tmp_path):
    """authentication 校验失败（非法 login_type）→ ValueError（API 层转 422）。"""
    from supernova_web.components.scan_manager import ScanManager
    from supernova_web.models import ScanRequest

    sm = ScanManager(workspaces_dir=tmp_path, repos_dir=tmp_path, config_store=object())
    wb_scan_id, _ = sm._store.create_scan(
        "ws-a", "https://t.example", "/r", "whitebox")
    scan_dir = tmp_path / "ws-a" / "scans" / "s1"
    scan_dir.mkdir(parents=True)
    req = ScanRequest(type="blackbox", url="https://t.example", workspace="ws-a",
                      reuse_whitebox_scan_id=wb_scan_id, authentication={
        "login_type": "INVALID_TYPE",  # 非 Literal["form","sso","api","basic"]
        "login_url": "https://t.example/login",
        "credentials": {"username": "x"},
        "success_condition": {"type": "url_contains", "value": "/d"},
    })
    with pytest.raises(ValueError):
        await sm._resolve_blackbox_inputs(req, "ws-a", scan_dir, target=None)


async def test_start_blackbox_persists_reuse_whitebox_scan_id(tmp_path, monkeypatch):
    """start blackbox 把 reuse_whitebox_scan_id 落 session.json，供 resume 重解析 wb_scan_dir。

    修 reuse resume fail-fast 的前提：create_scan 第三参 target="" → session.repo_path 读空；
    resume 需凭 reuse_whitebox_scan_id 重定位白盒产物，故 start 必须先持久化该字段。
    """
    from supernova_core.session import SessionManager
    from supernova_web.components.scan_manager import ScanManager
    from supernova_web.models import ScanRequest

    sm = ScanManager(workspaces_dir=tmp_path, repos_dir=tmp_path, config_store=object())
    wb_scan_id, _ = sm._store.create_scan(
        "ws-a", "https://t.example", "/r", "whitebox")

    # mock temporal 提交 + _check_temporal + _watch（避免 _watch task 死循环）
    async def fake_connect(addr):
        class FakeClient:
            async def start_workflow(self, fn, inp, **kw):
                return object()
        return FakeClient()
    monkeypatch.setattr("supernova_web.components.scan_manager.Client.connect", fake_connect)

    async def noop_check(self):
        return None
    monkeypatch.setattr(ScanManager, "_check_temporal", noop_check)

    async def noop_watch(self, *a, **kw):
        return None
    monkeypatch.setattr(ScanManager, "_watch", noop_watch)

    req = ScanRequest(type="blackbox", url="https://t.example", workspace="ws-a",
                      reuse_whitebox_scan_id=wb_scan_id)
    ws, scan_id = await sm.start(req)

    bb_scan_dir = sm._store.get_scan_dir(ws, scan_id)
    data = SessionManager(bb_scan_dir.parent).get_session_data(bb_scan_dir)
    assert data.get("reuse_whitebox_scan_id") == wb_scan_id


async def test_resume_blackbox_reuse_resolves_repo_path(tmp_path, monkeypatch):
    """resume reuse 黑盒：凭 session.reuse_whitebox_scan_id 重解析 wb_scan_dir 作 repo_path。

    修 fail-fast：原 resume 读 session.repo_path（create 时 target=""）= 空串 → workflow
    detect_whitebox_results=False → PentestError fail-fast。现 resume 用 reuse_id 重定位白盒
    产物目录（随 workspaces_dir 配置，不存绝对路径）。
    """
    from supernova_core.session import SessionManager
    from supernova_web.components.scan_manager import ScanManager

    sm = ScanManager(workspaces_dir=tmp_path, repos_dir=tmp_path, config_store=object())
    wb_scan_id, wb_scan_dir = sm._store.create_scan(
        "ws-a", "https://t.example", "/r", "whitebox")
    bb_scan_id, bb_scan_dir = sm._store.create_scan(
        "ws-a", "https://t.example", "", "blackbox", lineage=wb_scan_id)
    # 模拟 start 后落地的 reuse_whitebox_scan_id（resume 的输入）
    SessionManager(bb_scan_dir.parent).update_session(bb_scan_dir, {
        "reuse_whitebox_scan_id": wb_scan_id,
    })

    captured: dict = {}

    async def fake_connect(addr):
        class FakeClient:
            async def start_workflow(self, fn, inp, **kw):
                captured["inp"] = inp
                return object()
        return FakeClient()
    monkeypatch.setattr("supernova_web.components.scan_manager.Client.connect", fake_connect)

    async def noop_check(self):
        return None
    monkeypatch.setattr(ScanManager, "_check_temporal", noop_check)
    monkeypatch.setattr("supernova_web.components.scan_manager._compute_status",
                        lambda scan_dir, s: "interrupted")

    async def noop_watch(self, *a, **kw):
        return None
    monkeypatch.setattr(ScanManager, "_watch", noop_watch)

    await sm.resume("ws-a", bb_scan_id)

    # repo_path 重解析为白盒 scan_dir（非空/非 None），workflow detect_whitebox_results 命中
    assert captured["inp"].repo_path == str(wb_scan_dir)


@pytest.mark.asyncio
async def test_start_blackbox_scan_id_encodes_whitebox_lineage(tmp_path, monkeypatch):
    """start 黑盒:scan_id = <wb_scan_id>~1,血缘前缀来自 reuse_whitebox_scan_id。"""
    from unittest.mock import AsyncMock, MagicMock
    from supernova_web.components.scan_manager import ScanManager
    from supernova_web.models import ScanRequest

    sm = ScanManager(workspaces_dir=tmp_path, repos_dir=tmp_path, config_store=object())
    monkeypatch.setattr(sm, "_check_temporal", AsyncMock(return_value=None))
    mock_handle = MagicMock()
    mock_client = AsyncMock()
    mock_client.start_workflow = AsyncMock(return_value=mock_handle)
    monkeypatch.setattr("supernova_web.components.scan_manager.Client.connect",
                        AsyncMock(return_value=mock_client))
    monkeypatch.setattr(ScanManager, "_watch", AsyncMock(return_value=None))  # 避免 _watch 死循环

    # 先建白盒 scan 作 reuse 源（真实 wb_scan_id = NodeGoat-<ts>）
    wb_scan_id, _ = sm._store.create_scan(
        "WS1", "http://e", "/code/NodeGoat", "whitebox")

    ws, scan_id = await sm.start(ScanRequest(
        type="blackbox", url="http://e", workspace="WS1",
        reuse_whitebox_scan_id=wb_scan_id))

    assert scan_id == f"{wb_scan_id}~1"
    # session 仍持久化 reuse_whitebox_scan_id（resume 靠它重解析 wb_scan_dir）
    import json
    sess = json.loads((tmp_path / "WS1" / "scans" / scan_id / "session.json").read_text())
    assert sess.get("reuse_whitebox_scan_id") == wb_scan_id
