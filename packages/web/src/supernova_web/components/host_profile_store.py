"""workspace 级 HOST 档案库:domain→IP 映射 + /etc/hosts 解析 + 明文 YAML 落盘。

范式镜像 ``AuthProfileStore``(独立 store,``.system`` 段 + ws-priority 去重 + 路径
穿越防护),但**剥离所有 vault/加密**:IPs / domains 不敏感,落盘明文
``host-profiles.yaml``。新增 ``parse_etc_hosts(text)`` 纯函数 + 异步拉取/刷新。

下游 ``core/utils/security.py:resolve_host`` 用 ``urlparse(url).hostname``(小写)
查 host_mappings dict,故 ``HostMapping.host`` 经字段级 validator 强制 lowercase,
任何构造路径(手动录入 / URL 导入)都会被规范化——避免大小写不一致导致 mapping MISS。
"""
from __future__ import annotations

import ipaddress
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from uuid import uuid4

import httpx
import yaml
from pydantic import BaseModel, field_validator

HOST_PROFILES_FILENAME = "host-profiles.yaml"
# 保留 workspace 段:系统级档案(configs/*.yaml seed 产物)落此,所有 ws 共享。
# 用户不可创建 . 开头的 ws(API 层 create_workspace 拒 + indexer 跳过 dot-dir),
# 故 .system 不会与用户 ws 碰撞。store 内部 _validate_ws_segment 仍放行 .system。
SYSTEM_WS = ".system"

_log = logging.getLogger("supernova_web")


# ---------------------------------------------------------------------------
# 模型
# ---------------------------------------------------------------------------

class HostMapping(BaseModel):
    """单条 IP→host 映射。host 经 validator 强制 strip+lowercase,与下游
    ``urlparse(url).hostname``(小写)一致。"""
    ip: str
    host: str

    @field_validator("host")
    @classmethod
    def _normalize_host(cls, v: str) -> str:
        if not isinstance(v, str):
            raise TypeError("host must be a string")
        return v.strip().lower()


class HostProfile(BaseModel):
    """HOST 档案:一组 IP→host 映射 + 可选 source_url(支持 refresh)。"""
    id: str = ""
    name: str
    source_url: str | None = None
    mappings: list[HostMapping] = []
    # 档案归属段:workspace(该 ws 私有)| system(.system 段,所有 ws 共享、只读,
    # 由 configs/*.yaml 启动 seed 产出)。向后兼容:旧档案默认 workspace。
    # scope 由存储位置权威决定(_read_segment 按段标记),落盘值仅供 round-trip。
    scope: Literal["workspace", "system"] = "workspace"
    created_at: str | None = None
    updated_at: str | None = None


class AlreadyForked(Exception):
    """fork_from_system 时目标 ws 段已有同 profile.id(副本已存在),拒绝覆盖。"""


# ---------------------------------------------------------------------------
# 路径防护 + 工具
# ---------------------------------------------------------------------------

def _validate_ws_segment(ws: str) -> None:
    if not ws or "/" in ws or ws in (".", ".."):
        raise ValueError("invalid workspace name")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# 纯函数:/etc/hosts 解析(无网络)
# ---------------------------------------------------------------------------

def parse_etc_hosts(text: str) -> tuple[list[HostMapping], list[str]]:
    """解析 /etc/hosts 文本格式 → (mappings, warnings)。

    - 剥离行内 ``#`` 注释 + 首尾空白。
    - 空行 / 字段不足(<2) → 跳过(字段不足入 warnings)。
    - IP 非法 → warnings + 跳过该行。
    - 每个 hostname(含别名)各生成一条 HostMapping(均指向同 IP)。
    - hostname 经 ``HostMapping.host`` validator 强制 strip+lowercase。
    """
    warnings: list[str] = []
    mappings: list[HostMapping] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            warnings.append(f"L{lineno}: {raw!r} 字段不足")
            continue
        ip = parts[0]
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            warnings.append(f"L{lineno}: {raw!r} 非合法 IP")
            continue
        for host in parts[1:]:
            mappings.append(HostMapping(ip=ip, host=host))
    return mappings, warnings


# ---------------------------------------------------------------------------
# 异步拉取(httpx)
# ---------------------------------------------------------------------------

async def _http_get_hosts(url: str, timeout: int = 15) -> str:
    """GET url → text(follow redirects,raise_on_status)。模块级 → 测试 mock 之。"""
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.text


async def fetch_and_parse_hosts(
    url: str, timeout: int = 15
) -> tuple[list[HostMapping], list[str]]:
    """GET + 解析 /etc/hosts:返回 (mappings, warnings),纯拉取解析不落盘。"""
    text = await _http_get_hosts(url, timeout)
    return parse_etc_hosts(text)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class HostProfileStore:
    """workspace 级 HOST 档案库(明文,无加密)。

    结构镜像 ``AuthProfileStore``:
        ``_path`` / ``_read_segment`` / ``read``(.system 合并 + id 去重,ws 优先)/
        ``get`` / ``write`` / ``upsert_profile``(id 前缀 ``host_``)/
        ``delete_profile`` / ``fork_from_system`` / ``AlreadyForked``。

    差异:
        - 无 vault / 加密 —— 直接 yaml.safe_load / yaml.safe_dump。
        - 新增 ``import_from_url``(async) / ``refresh``(async,best-effort)。
    """

    def __init__(self, workspaces_dir: Path):
        self._workspaces_dir = Path(workspaces_dir).resolve()

    def _path(self, ws: str) -> Path:
        _validate_ws_segment(ws)
        p = (self._workspaces_dir / ws / HOST_PROFILES_FILENAME).resolve()
        if not p.is_relative_to(self._workspaces_dir):
            raise ValueError("invalid workspace name")
        return p

    def _read_segment(self, ws: str) -> list[HostProfile]:
        """纯段读取(不合并):该 ws 文件内的档案,scope 按存储位置标记
        (.system 段 → system,其余 → workspace)。所有写方法用本方法,避免合并版
        把系统档案错误持久化到 ws 文件。"""
        path = self._path(ws)
        if not path.exists():
            return []
        data = yaml.safe_load(path.read_text("utf-8")) or []
        profiles = [HostProfile.model_validate(p) for p in data]
        seg_scope = "system" if ws == SYSTEM_WS else "workspace"
        for p in profiles:
            p.scope = seg_scope
        return profiles

    def read(self, ws: str) -> list[HostProfile]:
        """读 → list[HostProfile]。合并系统档案:返回 ws 段(scope=workspace)
        + .system 段(scope=system,所有 ws 共享)。
        read('.system') 只返回系统段自身(不自合并,防内容翻倍/递归)。
        按 profile.id 去重:ws 段覆盖 .system 段同 id(fork 副本覆盖系统原型,不重复)。"""
        ws_profiles = self._read_segment(ws)
        if ws == SYSTEM_WS:
            return ws_profiles
        ws_ids = {p.id for p in ws_profiles}
        return ws_profiles + [
            p for p in self._read_segment(SYSTEM_WS) if p.id not in ws_ids
        ]

    def get(self, ws: str, profile_id: str) -> HostProfile | None:
        for p in self.read(ws):
            if p.id == profile_id:
                return p
        return None

    def write(self, ws: str, profiles: list[HostProfile]) -> None:
        """明文整体覆盖写。"""
        path = self._path(ws)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = [p.model_dump(mode="json") for p in profiles]
        path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False), "utf-8")

    def upsert_profile(self, ws: str, profile: HostProfile) -> HostProfile:
        if not profile.id:
            profile.id = f"host_{uuid4().hex[:10]}"
        profile.updated_at = _now()
        if not profile.created_at:
            profile.created_at = profile.updated_at
        profiles = self._read_segment(ws)
        profiles = [p for p in profiles if p.id != profile.id] + [profile]
        self.write(ws, profiles)
        return profile

    def delete_profile(self, ws: str, profile_id: str) -> bool:
        profiles = self._read_segment(ws)
        rest = [p for p in profiles if p.id != profile_id]
        if len(rest) == len(profiles):
            return False
        self.write(ws, rest)
        return True

    def fork_from_system(self, ws: str, profile_id: str) -> HostProfile | None:
        """把 .system 段系统档案 fork 成 ws 段可编辑副本。返回 fork 后 ws 副本;
        系统段无该 id → None。

        profile.id 保留系统原 id(ws-priority 覆盖系统原型);mappings 深拷贝;
        scope 重置 workspace;created_at / updated_at 清空(副本独立时间戳)。"""
        sys_profile = next(
            (p for p in self._read_segment(SYSTEM_WS) if p.id == profile_id), None)
        if sys_profile is None:
            return None
        if any(p.id == profile_id for p in self._read_segment(ws)):
            raise AlreadyForked(profile_id)
        forked = sys_profile.model_copy(deep=True)
        forked.scope = "workspace"
        forked.created_at = None
        forked.updated_at = None
        return self.upsert_profile(ws, forked)  # profile.id 非空 → 保留

    # ------------------------------------------------------------------
    # 异步:URL 导入 + 刷新(best-effort,失败不 raise)
    # ------------------------------------------------------------------

    async def import_from_url(
        self, ws: str, url: str, name: str | None = None
    ) -> HostProfile:
        """GET + 解析 /etc/hosts → 构造 HostProfile(source_url=url) → upsert → 返回。"""
        mappings, _warnings = await fetch_and_parse_hosts(url)
        profile = HostProfile(
            name=name or self._derive_name_from_url(url),
            source_url=url,
            mappings=mappings,
        )
        return self.upsert_profile(ws, profile)

    async def refresh(self, ws: str, pid: str) -> HostProfile | None:
        """重新拉取 source_url → 更新 mappings + updated_at + 落盘。

        - 找不到 profile → None。
        - profile 无 source_url → 原样返回(不发请求)。
        - 拉取 / 解析失败 → 日志 warning,**保留原 mappings 不变**,返回原 profile
          (**NEVER raise** —— best-effort,保 snapshot 优先)。
        """
        profile = self.get(ws, pid)
        if profile is None:
            return None
        if not profile.source_url:
            return profile
        try:
            mappings, _warnings = await fetch_and_parse_hosts(profile.source_url)
        except Exception as e:  # noqa: BLE001 —— best-effort:任何异常都保留快照
            _log.warning(
                "host-profile refresh %s/%s from %s failed: %s",
                ws, pid, profile.source_url, e)
            return profile
        profile.mappings = mappings
        profile.updated_at = _now()
        return self.upsert_profile(ws, profile)

    @staticmethod
    def _derive_name_from_url(url: str) -> str:
        """url → 人类可读 name(最后路径段 / netloc,失败回落 url 原值)。"""
        try:
            parsed = urlparse(url)
            path = parsed.path.rstrip("/")
            if path:
                return path.rsplit("/", 1)[-1] or parsed.netloc or url
            return parsed.netloc or url
        except Exception:
            return url
