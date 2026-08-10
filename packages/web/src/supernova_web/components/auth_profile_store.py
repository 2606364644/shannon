"""workspace 级认证档案库:多角色凭据 + Fernet 加密落盘 + 脱敏/空串保留。

范式镜像 WsConfigStore(独立 store,不污染 config.yaml);加密复用 CredentialVault 的
Fernet 实例(key 同源)。CredentialVault.encrypt/decrypt 是字段级 str|None→str|None,不
支持嵌套——这里按已知 schema 路径显式遍历 credentials[].{password,totp_secret} 与
credentials[].email_login.{password,totp_secret}(非泛型递归,更稳)。
"""
from __future__ import annotations

from dataclasses import asdict  # noqa: F401  (保持与 ws_config_store 风格一致可选)
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

import yaml
from pydantic import BaseModel

from supernova_core.models.config import Authentication, Credentials, EmailLogin
from supernova_web.components.credential_vault import CredentialVault

AUTH_PROFILES_FILENAME = "auth-profiles.yaml"
# 保留 workspace 段：系统级档案（configs/*.yaml seed 产物）落此，所有 ws 共享。
# 用户不可创建 . 开头的 ws（API 层 create_workspace 拒 + indexer 跳过 dot-dir），
# 故 .system 不会与用户 ws 碰撞。store 内部 _validate_ws_segment 仍放行 .system。
SYSTEM_WS = ".system"
MASKED = "••••"
# 显式敏感路径(credential 级 + email_login 级)
_CRED_SECRET_FIELDS = ("password", "totp_secret")


class VerifyStatus(BaseModel):
    state: Literal["unverified", "success", "failed"] = "unverified"
    failure_point: str | None = None  # username_or_password | totp_secret | out_of_band
    failure_detail: str | None = None
    last_verified_at: str | None = None
    # 块3c：最近一次验证的 probe 目录 + workflow_id。verify-log 读它定位过程记录；下次"测试登录"
    # 覆盖时清旧 probe 防堆积。可选（旧档案/未验证过无此字段）。
    probe_dir: str | None = None
    workflow_id: str | None = None


class EmailLoginCred(BaseModel):
    address: str
    password: str | None = None
    totp_secret: str | None = None


class AuthProfileCredential(BaseModel):
    id: str
    role: str
    username: str
    password: str | None = None
    totp_secret: str | None = None
    email_login: EmailLoginCred | None = None
    verify_status: VerifyStatus = VerifyStatus()


class AuthProfile(BaseModel):
    id: str
    name: str
    login_url: str
    login_type: Literal["form", "sso", "api", "basic"]
    login_flow: list[str] | None = None
    credentials: list[AuthProfileCredential]
    created_at: str | None = None
    updated_at: str | None = None
    # 档案归属段：workspace（该 ws 私有）| system（.system 段，所有 ws 共享、只读，
    # 由 configs/*.yaml 启动 seed 产出）。向后兼容：旧档案默认 workspace。
    # scope 由存储位置权威决定（_read_segment 按段标记），落盘值仅供 round-trip。
    scope: Literal["workspace", "system"] = "workspace"


class AlreadyForked(Exception):
    """fork_from_system 时目标 ws 段已有同 profile.id（副本已存在），拒绝覆盖。"""


def _validate_ws_segment(ws: str) -> None:
    if not ws or "/" in ws or ws in (".", ".."):
        raise ValueError("invalid workspace name")


def _encrypt_credential(cred: dict, vault: CredentialVault) -> dict:
    for f in _CRED_SECRET_FIELDS:
        cred[f] = vault.encrypt(cred.get(f))
    el = cred.get("email_login")
    if el:
        for f in _CRED_SECRET_FIELDS:
            el[f] = vault.encrypt(el.get(f))
    return cred


def _decrypt_credential(cred: dict, vault: CredentialVault) -> dict:
    for f in _CRED_SECRET_FIELDS:
        cred[f] = vault.decrypt(cred.get(f))
    el = cred.get("email_login")
    if el:
        for f in _CRED_SECRET_FIELDS:
            el[f] = vault.decrypt(el.get(f))
    return cred


def _mask_credential(cred: dict) -> dict:
    for f in _CRED_SECRET_FIELDS:
        cred[f] = MASKED if cred.get(f) else None
    el = cred.get("email_login")
    if el:
        for f in _CRED_SECRET_FIELDS:
            el[f] = MASKED if el.get(f) else None
    return cred


def credential_to_authentication(profile: AuthProfile, cred: AuthProfileCredential) -> Authentication:
    """把档案某角色展开成 core 单 credentials Authentication(scan-probe / 扫描复用)。"""
    email_login = None
    if cred.email_login:
        email_login = EmailLogin(
            address=cred.email_login.address,
            password=cred.email_login.password,
            totp_secret=cred.email_login.totp_secret,
        )
    return Authentication(
        login_type=profile.login_type,
        login_url=profile.login_url,
        credentials=Credentials(
            username=cred.username,
            password=cred.password,
            totp_secret=cred.totp_secret,
            email_login=email_login,
        ),
        login_flow=profile.login_flow,
    )


class AuthProfileStore:
    def __init__(self, workspaces_dir: Path, vault: CredentialVault):
        self._workspaces_dir = Path(workspaces_dir).resolve()
        self._vault = vault

    def _path(self, ws: str) -> Path:
        _validate_ws_segment(ws)
        p = (self._workspaces_dir / ws / AUTH_PROFILES_FILENAME).resolve()
        if not p.is_relative_to(self._workspaces_dir):
            raise ValueError("invalid workspace name")
        return p

    def _read_segment(self, ws: str) -> list[AuthProfile]:
        """纯段读取（不合并）：该 ws 文件内的档案，scope 按存储位置标记
        （.system 段 → system，其余 → workspace）。所有写方法用本方法，避免合并版
        把系统档案错误持久化到 ws 文件。"""
        path = self._path(ws)
        if not path.exists():
            return []
        data = yaml.safe_load(path.read_text("utf-8")) or []
        for prof in data:
            for cred in prof.get("credentials") or []:
                _decrypt_credential(cred, self._vault)
        profiles = [AuthProfile.model_validate(p) for p in data]
        seg_scope = "system" if ws == SYSTEM_WS else "workspace"
        for p in profiles:
            p.scope = seg_scope
        return profiles

    def read(self, ws: str) -> list[AuthProfile]:
        """读 + 解密 → list[AuthProfile]（内存明文）。合并系统档案：返回 ws 段
        （scope=workspace）+ .system 段（scope=system，所有 ws 共享）。
        read('.system') 只返回系统段自身（不自合并，防内容翻倍/递归）。
        按 profile.id 去重：ws 段覆盖 .system 段同 id（fork 副本覆盖系统原型，不重复显示）。"""
        ws_profiles = self._read_segment(ws)
        if ws == SYSTEM_WS:
            return ws_profiles
        ws_ids = {p.id for p in ws_profiles}
        return ws_profiles + [p for p in self._read_segment(SYSTEM_WS) if p.id not in ws_ids]

    def read_masked(self, ws: str) -> list[AuthProfile]:
        """读 + 解密 + 脱敏 → GET 响应态(敏感字段 MASKED if 值 else None)。"""
        profiles = self.read(ws)
        out = []
        for prof in profiles:
            d = prof.model_dump(mode="json")
            for cred in d["credentials"]:
                _mask_credential(cred)
            out.append(AuthProfile.model_validate(d))
        return out

    def get(self, ws: str, profile_id: str) -> AuthProfile | None:
        for p in self.read(ws):
            if p.id == profile_id:
                return p
        return None

    def write(self, ws: str, profiles: list[AuthProfile]) -> None:
        """加密敏感字段后落盘(整体覆盖写)。"""
        path = self._path(ws)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = []
        for prof in profiles:
            d = prof.model_dump(mode="json")
            for cred in d["credentials"]:
                _encrypt_credential(cred, self._vault)
            data.append(d)
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), "utf-8")

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def upsert_profile(self, ws: str, profile: AuthProfile) -> AuthProfile:
        profiles = self._read_segment(ws)
        if not profile.id:
            profile.id = f"prof_{uuid4().hex[:10]}"
        profile.updated_at = self._now()
        if not profile.created_at:
            profile.created_at = profile.updated_at
        for c in profile.credentials:
            if not c.id:
                c.id = f"cred_{uuid4().hex[:10]}"
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

    def apply_update(self, ws: str, profile_id: str, cred_id: str, **fields) -> None:
        """更新某 credential 的非敏感字段;空串 secret = 不改(保留原密文)。"""
        profiles = self._read_segment(ws)
        for p in profiles:
            if p.id != profile_id:
                continue
            for c in p.credentials:
                if c.id != cred_id:
                    continue
                for k, v in fields.items():
                    if k in _CRED_SECRET_FIELDS:
                        if v:  # 非空 → 更新
                            setattr(c, k, v)
                        # 空串/None → 保留原值(不改)
                    elif hasattr(c, k):
                        setattr(c, k, v)
        self.write(ws, profiles)

    def set_verify_status(self, ws: str, profile_id: str, cred_id: str, status: VerifyStatus) -> None:
        # resolve 档案归属段：ws 段优先，miss → .system。写回原 source，使系统档案的
        # verify_status 更新落 .system（不在 ws 创副本）。scan_manager 透传 ws 调用即可。
        profiles = self._read_segment(ws)
        source_ws = ws if any(p.id == profile_id for p in profiles) else None
        if source_ws is None and ws != SYSTEM_WS:
            sys_profiles = self._read_segment(SYSTEM_WS)
            if any(p.id == profile_id for p in sys_profiles):
                profiles, source_ws = sys_profiles, SYSTEM_WS
        if source_ws is None:
            return  # 两段都找不到 → 静默（对齐原行为：找不到不报错）
        for p in profiles:
            if p.id == profile_id:
                for c in p.credentials:
                    if c.id == cred_id:
                        c.verify_status = status
        self.write(source_ws, profiles)

    def fork_from_system(self, ws: str, profile_id: str) -> AuthProfile | None:
        """把 .system 段系统档案 fork 成 ws 段可编辑副本。返回 fork 后 ws 副本；
        系统段无该 id → None。

        profile.id 保留系统原 id（ws-priority 覆盖系统原型）；credential.id 重新生成
        （独立实体，避免跨段 id 冲突）；verify_status 重置 unverified（fork 意在改，
        旧验证态不可信）。凭据明文经 _read_segment 解密后 model_copy，upsert→write 重新加密落盘。"""
        sys_profile = next((p for p in self._read_segment(SYSTEM_WS) if p.id == profile_id), None)
        if sys_profile is None:
            return None
        if any(p.id == profile_id for p in self._read_segment(ws)):
            raise AlreadyForked(profile_id)   # ws 段已有同 id 副本 → 拒绝覆盖
        forked = sys_profile.model_copy(deep=True)
        forked.scope = "workspace"
        forked.created_at = None
        forked.updated_at = None
        for c in forked.credentials:
            c.id = ""                          # 重新生成 cred id（独立实体）
            c.verify_status = VerifyStatus()   # 重置 unverified
        return self.upsert_profile(ws, forked)  # profile.id 非空 → 保留

    def seed_from_config(self, configs_dir: Path) -> int:
        """扫 configs/*.yaml，把 authentication 段 seed 成系统级档案（.system 段，所有
        ws 共享、只读）。排除 web-multi-*（multi-repo 配置）与 users*（凭据文件）。按
        name 去重：.system 内已有同名则跳过（不覆盖）。无 authentication 段 / parse 失败
        → 跳过（DEBUG log，不阻断启动）。返回实际 seed 数。"""
        import logging
        log = logging.getLogger("supernova_web")
        configs_dir = Path(configs_dir)
        if not configs_dir.is_dir():
            return 0
        existing_names = {p.name for p in self._read_segment(SYSTEM_WS)}
        seeded = 0
        for cfg_path in sorted(configs_dir.glob("*.yaml")):
            stem = cfg_path.stem
            if stem.startswith("web-multi-") or stem.startswith("users"):
                continue
            if stem in existing_names:
                continue
            try:
                from supernova_core.config.parser import parse_config
                cfg = parse_config(str(cfg_path))
            except Exception as e:  # parse/validation 失败 → 跳过，不阻断启动
                log.debug("auth-profile seed: skip %s (parse failed: %s)", cfg_path.name, e)
                continue
            if not cfg.authentication:
                log.debug("auth-profile seed: skip %s (no authentication section)", cfg_path.name)
                continue
            cred = cfg.authentication.credentials
            email_login = None
            if cred.email_login:
                email_login = EmailLoginCred(
                    address=cred.email_login.address,
                    password=cred.email_login.password,
                    totp_secret=cred.email_login.totp_secret,
                )
            profile = AuthProfile(
                id="",
                name=stem,
                login_url=cfg.authentication.login_url,
                login_type=cfg.authentication.login_type,
                login_flow=cfg.authentication.login_flow,
                scope="system",
                credentials=[AuthProfileCredential(
                    id="",
                    role="primary",
                    username=cred.username,
                    password=cred.password,
                    totp_secret=cred.totp_secret,
                    email_login=email_login,
                )],
            )
            self.upsert_profile(SYSTEM_WS, profile)
            existing_names.add(stem)
            seeded += 1
        return seeded
