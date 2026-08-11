"""scan_manager 档案级批量认证验证:选 cred 子集 / 各建 probe / 起 BatchAuthValidationWorkflow /
写首 cred running / 起 watcher(Slice 3) / cred_id 越界守护。语义=逐个独立验证(spec §2)。"""
import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from supernova_web.components.auth_profile_store import (
    AuthProfileStore, AuthProfile, AuthProfileCredential,
)
from supernova_web.components.credential_vault import CredentialVault
from supernova_web.components.scan_manager import ScanManager


def _multi_store(tmp_path):
    """3 角色 profile(admin/user/guest)。"""
    vault = CredentialVault(tmp_path / ".master.key")
    s = AuthProfileStore(tmp_path, vault)
    s.write("ws1", [AuthProfile(
        id="prof_1", name="NG", login_url="http://t/", login_type="form",
        credentials=[
            AuthProfileCredential(id="cred_a", role="admin", username="admin", password="pw"),
            AuthProfileCredential(id="cred_b", role="user", username="user1", password="pw"),
            AuthProfileCredential(id="cred_c", role="guest", username="guest", password="pw"),
        ])])
    return s


def _mgr(tmp_path, store):
    return ScanManager(
        workspaces_dir=tmp_path, repos_dir=tmp_path / "repos", config_store=MagicMock(),
        max_concurrent=1, scan_timeout=0.0, ws_config_store=MagicMock(),
        auth_profile_store=store,
    )


def _patch_client(handle_id="authval-batch-ws1-abc12345"):
    fake_handle = MagicMock()
    fake_handle.id = handle_id
    client = MagicMock()
    client.start_workflow = AsyncMock(return_value=fake_handle)
    return client, fake_handle


@pytest.mark.asyncio
async def test_start_batch_full_selection_creates_probe_per_cred(tmp_path):
    """全选(cred_ids None)→ 为每个 cred 建 probe_dir + scan-config.yaml + 起 batch workflow + 起 watcher。"""
    store = _multi_store(tmp_path)
    mgr = _mgr(tmp_path, store)
    client, handle = _patch_client()
    with patch("supernova_web.components.scan_manager.Client") as ClientCls, \
         patch.object(mgr, "_watch_batch_progress", new=AsyncMock()) as watch:
        ClientCls.connect = AsyncMock(return_value=client)
        result = await mgr.start_batch_auth_validation("ws1", "prof_1", None)
    assert result["workflow_id"] == handle.id
    # 3 个 probe(scan-config.yaml),每 probe 含对应 username
    probes = sorted((tmp_path / "ws1" / "auth-probes").glob("*/scan-config.yaml"))
    assert len(probes) == 3
    bodies = "||".join(p.read_text("utf-8") for p in probes)
    assert "admin" in bodies and "user1" in bodies and "guest" in bodies
    # 起 BatchAuthValidationWorkflow(传 items=3)
    sent_input = client.start_workflow.call_args.args[1]
    assert len(sent_input.items) == 3
    # watcher 被起
    assert watch.called


@pytest.mark.asyncio
async def test_start_batch_subset_only_selected_creds(tmp_path):
    """cred_ids 子集 → 只为选中的建 probe(未选的不建)。"""
    store = _multi_store(tmp_path)
    mgr = _mgr(tmp_path, store)
    client, _ = _patch_client()
    with patch("supernova_web.components.scan_manager.Client") as ClientCls, \
         patch.object(mgr, "_watch_batch_progress", new=AsyncMock()):
        ClientCls.connect = AsyncMock(return_value=client)
        await mgr.start_batch_auth_validation("ws1", "prof_1", ["cred_a", "cred_c"])
    probes = list((tmp_path / "ws1" / "auth-probes").glob("*/scan-config.yaml"))
    assert len(probes) == 2
    bodies = "||".join(p.read_text("utf-8") for p in probes)
    assert "admin" in bodies and "guest" in bodies and "user1" not in bodies
    sent_input = client.start_workflow.call_args.args[1]
    assert [it.cred_id for it in sent_input.items] == ["cred_a", "cred_c"]


@pytest.mark.asyncio
async def test_start_batch_rejects_cred_id_not_in_profile(tmp_path):
    """cred_ids 含不属于该 profile 的 id → ValueError(防注入任意 id 越界)。"""
    store = _multi_store(tmp_path)
    mgr = _mgr(tmp_path, store)
    client, _ = _patch_client()
    with patch("supernova_web.components.scan_manager.Client") as ClientCls, \
         patch.object(mgr, "_watch_batch_progress", new=AsyncMock()):
        ClientCls.connect = AsyncMock(return_value=client)
        with pytest.raises(ValueError, match="不属于"):
            await mgr.start_batch_auth_validation("ws1", "prof_1", ["cred_a", "cred_evil"])
    # 不应起任何 probe / workflow
    assert not list((tmp_path / "ws1" / "auth-probes").glob("*/scan-config.yaml"))
    assert not client.start_workflow.called


@pytest.mark.asyncio
async def test_start_batch_rejects_missing_profile(tmp_path):
    store = _multi_store(tmp_path)
    mgr = _mgr(tmp_path, store)
    with pytest.raises(ValueError, match="认证档案不存在"):
        await mgr.start_batch_auth_validation("ws1", "prof_missing", None)


@pytest.mark.asyncio
async def test_start_batch_role_not_in_yaml(tmp_path):
    """role 不入 scan-config.yaml(保持 Branch A 纯单次登录,spec §2)——credential_to_authentication 丢 role。"""
    store = _multi_store(tmp_path)
    mgr = _mgr(tmp_path, store)
    client, _ = _patch_client()
    with patch("supernova_web.components.scan_manager.Client") as ClientCls, \
         patch.object(mgr, "_watch_batch_progress", new=AsyncMock()):
        ClientCls.connect = AsyncMock(return_value=client)
        await mgr.start_batch_auth_validation("ws1", "prof_1", ["cred_a"])
    body = next((tmp_path / "ws1" / "auth-probes").glob("*/scan-config.yaml")).read_text("utf-8")
    assert "admin" in body  # username
    assert "role" not in body  # role 字段不入 YAML


@pytest.mark.asyncio
async def test_start_batch_workflow_id_naming(tmp_path):
    """workflow_id = authval-batch-{ws}-{uuid8}(越界守护前缀,供 verify-events 守护放宽识别)。"""
    store = _multi_store(tmp_path)
    mgr = _mgr(tmp_path, store)
    client, _ = _patch_client()
    with patch("supernova_web.components.scan_manager.Client") as ClientCls, \
         patch.object(mgr, "_watch_batch_progress", new=AsyncMock()):
        ClientCls.connect = AsyncMock(return_value=client)
        result = await mgr.start_batch_auth_validation("ws1", "prof_1", None)
    assert result["workflow_id"].startswith("authval-batch-ws1-")


@pytest.mark.asyncio
async def test_start_batch_writes_first_cred_running(tmp_path):
    """起 workflow 后写首 cred running verify_status(带 probe_dir/workflow_id)——前端轮询 profile
    发现 running → 订阅其 verify-events。其余 cred 保持 unverified(watcher 终态时回填)。"""
    store = _multi_store(tmp_path)
    mgr = _mgr(tmp_path, store)
    client, _ = _patch_client()
    with patch("supernova_web.components.scan_manager.Client") as ClientCls, \
         patch.object(mgr, "_watch_batch_progress", new=AsyncMock()):
        ClientCls.connect = AsyncMock(return_value=client)
        result = await mgr.start_batch_auth_validation("ws1", "prof_1", None)
    profile = store.read("ws1")[0]
    by_id = {c.id: c for c in profile.credentials}
    # 首 cred running(带 probe_dir/workflow_id)
    assert by_id["cred_a"].verify_status.state == "running"
    assert by_id["cred_a"].verify_status.workflow_id == result["workflow_id"]
    assert by_id["cred_a"].verify_status.probe_dir is not None
    # 其余 cred 仍 unverified(watcher 回填)
    assert by_id["cred_b"].verify_status.state == "unverified"
    assert by_id["cred_c"].verify_status.state == "unverified"


@pytest.mark.asyncio
async def test_start_batch_cleans_previous_probes_per_cred(tmp_path):
    """各 cred 覆盖清理旧 probe(复用单 cred 覆盖逻辑)——防 auth-probes/ 无限堆积。"""
    store = _multi_store(tmp_path)
    # 预置 cred_a 旧 probe(VerifyStatus 指向它)
    old_probe = tmp_path / "ws1" / "auth-probes" / "probe-old-a"
    old_probe.mkdir(parents=True)
    (old_probe / "events.ndjson").write_text('{"old":1}', "utf-8")
    from supernova_web.components.auth_profile_store import VerifyStatus
    store.set_verify_status("ws1", "prof_1", "cred_a", VerifyStatus(
        state="failed", probe_dir=str(old_probe), workflow_id="authval-ws1-probe-old-a"))
    mgr = _mgr(tmp_path, store)
    client, _ = _patch_client()
    with patch("supernova_web.components.scan_manager.Client") as ClientCls, \
         patch.object(mgr, "_watch_batch_progress", new=AsyncMock()):
        ClientCls.connect = AsyncMock(return_value=client)
        await mgr.start_batch_auth_validation("ws1", "prof_1", ["cred_a", "cred_b"])
    # 旧 probe 被清,只剩 2 个新 probe
    assert not old_probe.exists()
    new_probes = [p for p in (tmp_path / "ws1" / "auth-probes").iterdir() if p.is_dir()]
    assert len(new_probes) == 2


# ---- watcher 回填 verify_status(Slice 3)----


def _prep_probe(tmp_path, cred_id, ws="ws1"):
    """预置一个 probe 目录(模拟 start_batch 已建):scan-config.yaml + events.ndjson。"""
    probe = tmp_path / ws / "auth-probes" / f"probe-{cred_id}"
    probe.mkdir(parents=True)
    (probe / "scan-config.yaml").write_text(f"authentication: {{username: {cred_id}}}", "utf-8")
    (probe / "events.ndjson").write_text('{"i":1}\n', "utf-8")
    return probe


def _mock_query_client(progress_seq):
    """handle.query 按 progress_seq 顺序返回(side_effect);末轮须 all_done=True 致 watcher 退出。"""
    handle = MagicMock()
    handle.query = AsyncMock(side_effect=list(progress_seq))
    client = MagicMock()
    client.get_workflow_handle = MagicMock(return_value=handle)
    return client, handle


@pytest.mark.asyncio
async def test_watcher_backfills_terminal_creds_and_deletes_config(tmp_path):
    """终态 cred → 回填 verify_status(success/failed + failure_point/detail + last_verified_at)+
    删 scan-config(密码卫生),保留 events.ndjson 供回看。"""
    store = _multi_store(tmp_path)
    probe_a = _prep_probe(tmp_path, "cred_a")
    probe_b = _prep_probe(tmp_path, "cred_b")
    mgr = _mgr(tmp_path, store)
    cred_probe_map = {"cred_a": {"probe_dir": str(probe_a)},
                      "cred_b": {"probe_dir": str(probe_b)}}
    progress = {"items": [
        {"cred_id": "cred_a", "state": "success"},
        {"cred_id": "cred_b", "state": "failed", "failure_point": "totp_secret", "failure_detail": "bad"},
    ], "all_done": True}
    client, _ = _mock_query_client([progress])
    with patch("supernova_web.components.scan_manager.Client") as ClientCls:
        ClientCls.connect = AsyncMock(return_value=client)
        await mgr._watch_batch_progress("ws1", "prof_1", "authval-batch-ws1-x", cred_probe_map)
    by_id = {c.id: c for c in store.read("ws1")[0].credentials}
    assert by_id["cred_a"].verify_status.state == "success"
    assert by_id["cred_b"].verify_status.state == "failed"
    assert by_id["cred_b"].verify_status.failure_point == "totp_secret"
    assert by_id["cred_a"].verify_status.last_verified_at is not None
    assert by_id["cred_a"].verify_status.probe_dir == str(probe_a)
    # scan-config 删(密码卫生),events 保留
    assert not (probe_a / "scan-config.yaml").exists()
    assert (probe_a / "events.ndjson").exists()
    assert not (probe_b / "scan-config.yaml").exists()
    assert (probe_b / "events.ndjson").exists()


@pytest.mark.asyncio
async def test_watcher_writes_running_for_in_flight_cred(tmp_path):
    """running 的 cred → 写 running verify_status(前端轮询 profile 定位 running 订阅其 verify-events)。
    两轮:第一轮 running + not done → 写 running + sleep;第二轮 success + done → 回填 + 退出。"""
    store = _multi_store(tmp_path)
    probe_b = _prep_probe(tmp_path, "cred_b")
    mgr = _mgr(tmp_path, store)
    client, _ = _mock_query_client([
        {"items": [{"cred_id": "cred_b", "state": "running"}], "all_done": False},
        {"items": [{"cred_id": "cred_b", "state": "success"}], "all_done": True},
    ])
    with patch("supernova_web.components.scan_manager.Client") as ClientCls, \
         patch.object(asyncio, "sleep", new=AsyncMock()):
        ClientCls.connect = AsyncMock(return_value=client)
        await mgr._watch_batch_progress("ws1", "prof_1", "authval-batch-ws1-x",
                                        {"cred_b": {"probe_dir": str(probe_b)}})
    by_id = {c.id: c for c in store.read("ws1")[0].credentials}
    assert by_id["cred_b"].verify_status.state == "success"  # 最终终态


@pytest.mark.asyncio
async def test_watcher_rejects_out_of_containment_probe_dir(tmp_path):
    """cred_probe_map 含越界 probe_dir → 不删该路径不回填(守护,防 map 污染致任意路径删除)。"""
    store = _multi_store(tmp_path)
    evil = tmp_path / "evil-target"
    evil.mkdir()
    (evil / "scan-config.yaml").write_text("secret", "utf-8")
    mgr = _mgr(tmp_path, store)
    cred_probe_map = {"cred_a": {"probe_dir": str(evil)}}  # 越界(不在 auth-probes/ 下)
    client, _ = _mock_query_client([{"items": [
        {"cred_id": "cred_a", "state": "success"}], "all_done": True}])
    with patch("supernova_web.components.scan_manager.Client") as ClientCls:
        ClientCls.connect = AsyncMock(return_value=client)
        await mgr._watch_batch_progress("ws1", "prof_1", "authval-batch-ws1-x", cred_probe_map)
    # 越界目录不删
    assert (evil / "scan-config.yaml").read_text("utf-8") == "secret"
    # 不回填(保持 unverified)
    by_id = {c.id: c for c in store.read("ws1")[0].credentials}
    assert by_id["cred_a"].verify_status.state == "unverified"


@pytest.mark.asyncio
async def test_watcher_exits_when_all_done(tmp_path):
    """all_done=True → watcher 退出(只 query 一次,不无限循环)。"""
    store = _multi_store(tmp_path)
    probe_a = _prep_probe(tmp_path, "cred_a")
    mgr = _mgr(tmp_path, store)
    client, handle = _mock_query_client([{"items": [
        {"cred_id": "cred_a", "state": "success"}], "all_done": True}])
    with patch("supernova_web.components.scan_manager.Client") as ClientCls:
        ClientCls.connect = AsyncMock(return_value=client)
        await mgr._watch_batch_progress("ws1", "prof_1", "authval-batch-ws1-x",
                                        {"cred_a": {"probe_dir": str(probe_a)}})
    assert handle.query.call_count == 1


@pytest.mark.asyncio
async def test_watcher_skips_unknown_cred_id(tmp_path):
    """query 返回的 cred_id 不在 cred_probe_map → 跳过(防 watcher 回填未追踪的 cred)。"""
    store = _multi_store(tmp_path)
    probe_a = _prep_probe(tmp_path, "cred_a")
    mgr = _mgr(tmp_path, store)
    client, _ = _mock_query_client([{"items": [
        {"cred_id": "cred_a", "state": "success"},
        {"cred_id": "cred_unknown", "state": "success"},  # 不在 map
    ], "all_done": True}])
    with patch("supernova_web.components.scan_manager.Client") as ClientCls:
        ClientCls.connect = AsyncMock(return_value=client)
        await mgr._watch_batch_progress("ws1", "prof_1", "authval-batch-ws1-x",
                                        {"cred_a": {"probe_dir": str(probe_a)}})
    by_id = {c.id: c for c in store.read("ws1")[0].credentials}
    assert by_id["cred_a"].verify_status.state == "success"  # 已知 cred 正常回填
    # cred_unknown 不在 profile → 不影响(KeyError 不抛,跳过)


# ---- workflow_id 前缀守护放宽(Slice 4:接受 authval-batch-{ws}-)----


@pytest.mark.asyncio
async def test_events_path_accepts_batch_workflow_id_prefix(tmp_path):
    """verify-events 守护放宽:接受 authval-batch-{ws}- 前缀(批量产物订阅实时日志)。"""
    store = _multi_store(tmp_path)
    mgr = _mgr(tmp_path, store)
    probe = _prep_probe(tmp_path, "cred_a")
    ndjson = await mgr.auth_validation_events_path(
        "ws1", workflow_id="authval-batch-ws1-deadbeef", probe_dir=str(probe))
    assert ndjson == probe.resolve() / "events.ndjson"


@pytest.mark.asyncio
async def test_verify_log_accepts_batch_workflow_id_prefix(tmp_path):
    """verify-log 守护放宽:接受 authval-batch-{ws}- 前缀(批量产物事后回看)。"""
    store = _multi_store(tmp_path)
    mgr = _mgr(tmp_path, store)
    probe = _prep_probe(tmp_path, "cred_a")
    events = await mgr.get_auth_validation_log(
        "ws1", workflow_id="authval-batch-ws1-deadbeef", probe_dir=str(probe))
    assert len(events) == 1  # _prep_probe 写了 1 行 events


@pytest.mark.asyncio
async def test_events_path_rejects_batch_prefix_other_ws(tmp_path):
    """守护②:authval-batch-{其他ws}- → 拒(防跨 ws 读 events)。"""
    store = _multi_store(tmp_path)
    mgr = _mgr(tmp_path, store)
    probe = _prep_probe(tmp_path, "cred_a")
    with pytest.raises(ValueError, match="workflow_id 越界"):
        await mgr.auth_validation_events_path(
            "ws1", workflow_id="authval-batch-ws2-deadbeef", probe_dir=str(probe))
