"""HostProfileStore: plaintext workspace-level host profile store + /etc/hosts parse.

Mirror of AuthProfileStore structure but **without encryption** (IPs / domains
are not sensitive). Adds pure `parse_etc_hosts(text)` + async fetch/refresh.
"""
from pathlib import Path

import pytest

from supernova_web.components.host_profile_store import (
    AlreadyForked,
    HostMapping,
    HostProfile,
    HostProfileStore,
    parse_etc_hosts,
)

ETC_HOSTS_SAMPLE = """# comment line
10.0.0.1 api.example.com alias.example.com

# blank above
not-a-valid-line
192.168.1.5 svc.test
"""


# ---------------------------------------------------------------------------
# parse_etc_hosts —— 纯函数（无网络），/etc/hosts 格式 → list[HostMapping]
# ---------------------------------------------------------------------------

def test_parse_etc_hosts_basic():
    """跳过注释/空行/非法行；别名同指 IP 各生成一条。"""
    mappings, warnings = parse_etc_hosts(ETC_HOSTS_SAMPLE)
    ips = {m.host: m.ip for m in mappings}
    assert ips["api.example.com"] == "10.0.0.1"
    assert ips["alias.example.com"] == "10.0.0.1"
    assert ips["svc.test"] == "192.168.1.5"
    assert len(warnings) >= 1  # "not-a-valid-line"


def test_parse_etc_hosts_strips_inline_comments():
    text = "1.2.3.4 foo.test  # inline comment\n"
    mappings, warnings = parse_etc_hosts(text)
    assert len(mappings) == 1
    assert mappings[0].ip == "1.2.3.4"
    assert mappings[0].host == "foo.test"
    assert warnings == []


def test_parse_etc_hosts_short_line_warns():
    mappings, warnings = parse_etc_hosts("lonelyword\n")
    assert mappings == []
    assert len(warnings) == 1


def test_parse_etc_hosts_normalizes_host_to_lowercase():
    """混合大小写 hostname → 规范化为小写 key。

    下游 core/utils/security.py:resolve_host 用 urlparse(url).hostname（小写）
    查 host_mappings dict；若 mapping key 大写则 MISS。任何路径（手动录入、
    导入）都须经字段级 validator 规范化。
    """
    mappings, _ = parse_etc_hosts("10.0.0.1 Api.Example.COM\n")
    assert mappings[0].host == "api.example.com"


def test_host_mapping_field_validator_lowercases():
    """HostMapping.host 字段级 validator：任何构造路径都 lowercase + strip。"""
    m = HostMapping(ip="10.0.0.1", host="  Foo.Bar.TEST  ")
    assert m.host == "foo.bar.test"


# ---------------------------------------------------------------------------
# HostProfileStore CRUD（去加密，落盘明文 host-profiles.yaml）
# ---------------------------------------------------------------------------

def test_store_crud(tmp_path):
    store = HostProfileStore(tmp_path)
    p = HostProfile(id="", name="华南", source_url=None,
                    mappings=[HostMapping(ip="10.0.0.1", host="x.test")],
                    created_at="", updated_at="")
    saved = store.upsert_profile("ws1", p)
    assert saved.id.startswith("host_")
    assert len(store.read("ws1")) == 1
    assert store.get("ws1", saved.id).name == "华南"
    assert store.delete_profile("ws1", saved.id) is True
    assert store.read("ws1") == []


def test_store_upsert_rewrites_existing_by_id(tmp_path):
    store = HostProfileStore(tmp_path)
    p = HostProfile(id="host_a", name="a", mappings=[])
    store.upsert_profile("ws1", p)
    # 二次 upsert 同 id 应覆盖，而非追加
    p2 = HostProfile(id="host_a", name="renamed",
                     mappings=[HostMapping(ip="1.1.1.1", host="x.test")])
    store.upsert_profile("ws1", p2)
    profiles = store.read("ws1")
    assert len(profiles) == 1
    assert profiles[0].name == "renamed"
    assert len(profiles[0].mappings) == 1


def test_store_get_returns_none_when_missing(tmp_path):
    store = HostProfileStore(tmp_path)
    assert store.get("ws1", "host_nope") is None


def test_store_delete_returns_false_when_missing(tmp_path):
    store = HostProfileStore(tmp_path)
    assert store.delete_profile("ws1", "host_nope") is False


def test_store_path_traversal_rejected(tmp_path):
    store = HostProfileStore(tmp_path)
    for bad in ("..", "a/b", "."):
        with pytest.raises(ValueError):
            store._path(bad)


# ---------------------------------------------------------------------------
# 系统档案（.system 段）合并 + ws-priority 去重
# ---------------------------------------------------------------------------

def test_store_system_merge_dedup(tmp_path):
    """.system 段 + ws 段按 id 去重（ws 优先）。"""
    store = HostProfileStore(tmp_path)
    sys_p = HostProfile(id="host_sys", name="sys", source_url=None, mappings=[],
                        created_at="", updated_at="", scope="system")
    store.upsert_profile(".system", sys_p)
    # ws 自己的 host_sys 应覆盖 system 的
    ws_p = HostProfile(id="host_sys", name="ws-override", source_url=None,
                       mappings=[], created_at="", updated_at="")
    store.upsert_profile("ws1", ws_p)
    profiles = store.read("ws1")
    assert len([p for p in profiles if p.id == "host_sys"]) == 1
    assert next(p for p in profiles if p.id == "host_sys").name == "ws-override"


def test_read_merges_system_profiles(tmp_path):
    store = HostProfileStore(tmp_path)
    store.upsert_profile("ws1", HostProfile(id="host_a", name="ws-side", mappings=[]))
    store.upsert_profile(".system", HostProfile(id="host_b", name="sys-side",
                                                mappings=[], scope="system"))
    profiles = store.read("ws1")
    by_name = {p.name: p for p in profiles}
    assert by_name["ws-side"].scope == "workspace"
    assert by_name["sys-side"].scope == "system"


def test_read_system_segment_not_self_merged(tmp_path):
    store = HostProfileStore(tmp_path)
    store.upsert_profile(".system", HostProfile(id="host_s", name="s", mappings=[]))
    profiles = store.read(".system")
    assert len(profiles) == 1


# ---------------------------------------------------------------------------
# fork_from_system —— 系统档案 fork 成 ws 可编辑副本
# ---------------------------------------------------------------------------

def test_fork_from_system(tmp_path):
    store = HostProfileStore(tmp_path)
    sys_p = HostProfile(id="host_s1", name="sys", source_url=None, mappings=[
        HostMapping(ip="10.0.0.1", host="api.sys.test"),
    ], created_at="", updated_at="", scope="system")
    store.upsert_profile(".system", sys_p)
    forked = store.fork_from_system("ws1", "host_s1")
    assert forked is not None
    assert forked.scope == "workspace"
    assert forked.id == "host_s1"  # 保留系统原 id（ws-priority 覆盖）
    # 镜像 auth:fork 时 created_at/updated_at 重置为 None 后 upsert 重新打戳
    # （upsert_profile 内 if not created_at → _now()），二者最终为 fresh 时间戳。
    assert forked.created_at is not None
    assert forked.updated_at is not None
    assert len(forked.mappings) == 1  # mappings 深拷贝
    # ws 段已有同 id → 第二次 fork 拒绝
    with pytest.raises(AlreadyForked):
        store.fork_from_system("ws1", "host_s1")


def test_fork_from_system_unknown_returns_none(tmp_path):
    store = HostProfileStore(tmp_path)
    assert store.fork_from_system("ws1", "host_nope") is None


# ---------------------------------------------------------------------------
# import_from_url / refresh —— 异步（await + httpx mock）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_import_from_url_fetches_and_saves(tmp_path, monkeypatch):
    """import_from_url: GET + 解析 + 落盘 + 存 source_url。"""
    async def fake_get(url, timeout=15):
        return ETC_HOSTS_SAMPLE
    monkeypatch.setattr(
        "supernova_web.components.host_profile_store._http_get_hosts", fake_get)
    store = HostProfileStore(tmp_path)
    p = await store.import_from_url(
        "ws1", "https://hosts.test/get?id=1", name="导入")
    assert p.source_url == "https://hosts.test/get?id=1"
    assert p.name == "导入"
    assert any(m.host == "api.example.com" for m in p.mappings)
    assert p.id.startswith("host_")
    # 落盘可读回
    assert store.get("ws1", p.id).source_url == "https://hosts.test/get?id=1"


@pytest.mark.asyncio
async def test_import_from_url_derives_name_when_absent(tmp_path, monkeypatch):
    async def fake_get(url, timeout=15):
        return "10.0.0.1 only.test\n"
    monkeypatch.setattr(
        "supernova_web.components.host_profile_store._http_get_hosts", fake_get)
    store = HostProfileStore(tmp_path)
    p = await store.import_from_url("ws1", "https://hosts.test/get?id=2")
    # name 未提供 → 从 url 派生（非空字符串）
    assert p.name and isinstance(p.name, str)


@pytest.mark.asyncio
async def test_refresh_updates_mappings_on_success(tmp_path, monkeypatch):
    async def fake_get(url, timeout=15):
        return "10.0.0.1 updated.test\n"
    monkeypatch.setattr(
        "supernova_web.components.host_profile_store._http_get_hosts", fake_get)
    store = HostProfileStore(tmp_path)
    p = store.upsert_profile("ws1", HostProfile(
        id="host_x", name="x", source_url="https://hosts.test/get?id=1",
        mappings=[HostMapping(ip="10.0.0.1", host="old.test")],
        created_at="", updated_at=""))
    refreshed = await store.refresh("ws1", "host_x")
    assert any(m.host == "updated.test" for m in refreshed.mappings)
    assert not any(m.host == "old.test" for m in refreshed.mappings)
    assert refreshed.updated_at is not None


@pytest.mark.asyncio
async def test_refresh_fallback_on_failure(tmp_path, monkeypatch):
    """refresh 失败 → 保留落盘快照，不 raise。"""
    async def boom(url, timeout=15):
        raise OSError("net")
    monkeypatch.setattr(
        "supernova_web.components.host_profile_store._http_get_hosts", boom)
    store = HostProfileStore(tmp_path)
    p = store.upsert_profile("ws1", HostProfile(
        id="host_x", name="x", source_url="https://hosts.test/get?id=1",
        mappings=[HostMapping(ip="10.0.0.1", host="x.test")],
        created_at="", updated_at=""))
    refreshed = await store.refresh("ws1", "host_x")
    assert refreshed is not None
    assert refreshed.mappings == p.mappings  # 保留快照


@pytest.mark.asyncio
async def test_refresh_returns_as_is_when_no_source_url(tmp_path, monkeypatch):
    """profile 无 source_url → 不发请求，原样返回。"""
    invoked = {"count": 0}

    async def fake_get(url, timeout=15):
        invoked["count"] += 1
        return "10.0.0.1 should-not-reach.test\n"
    monkeypatch.setattr(
        "supernova_web.components.host_profile_store._http_get_hosts", fake_get)
    store = HostProfileStore(tmp_path)
    p = store.upsert_profile("ws1", HostProfile(
        id="host_x", name="x", source_url=None,
        mappings=[HostMapping(ip="10.0.0.1", host="x.test")],
        created_at="", updated_at=""))
    refreshed = await store.refresh("ws1", "host_x")
    assert refreshed.mappings == p.mappings
    assert invoked["count"] == 0


@pytest.mark.asyncio
async def test_refresh_unknown_profile_returns_none(tmp_path, monkeypatch):
    store = HostProfileStore(tmp_path)
    refreshed = await store.refresh("ws1", "host_nope")
    assert refreshed is None


# ---------------------------------------------------------------------------
# 落盘格式：明文（无密文），文件名 host-profiles.yaml
# ---------------------------------------------------------------------------

def test_persisted_filename_and_plaintext(tmp_path):
    store = HostProfileStore(tmp_path)
    store.upsert_profile("ws1", HostProfile(
        id="host_a", name="plain",
        mappings=[HostMapping(ip="10.0.0.1", host="api.test")]))
    path = tmp_path / "ws1" / "host-profiles.yaml"
    assert path.exists()
    raw = path.read_text("utf-8")
    # IP 与 domain 明文落盘（非加密）
    assert "10.0.0.1" in raw
    assert "api.test" in raw
