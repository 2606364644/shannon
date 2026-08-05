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
MASKED = "••••"
# 显式敏感路径(credential 级 + email_login 级)
_CRED_SECRET_FIELDS = ("password", "totp_secret")


class VerifyStatus(BaseModel):
    state: Literal["unverified", "success", "failed"] = "unverified"
    failure_point: str | None = None  # username_or_password | totp_secret | out_of_band
    failure_detail: str | None = None
    last_verified_at: str | None = None


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

    def read(self, ws: str) -> list[AuthProfile]:
        """读 + 解密 → list[AuthProfile](内存明文)。"""
        path = self._path(ws)
        if not path.exists():
            return []
        data = yaml.safe_load(path.read_text("utf-8")) or []
        for prof in data:
            for cred in prof.get("credentials") or []:
                _decrypt_credential(cred, self._vault)
        return [AuthProfile.model_validate(p) for p in data]

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
        profiles = self.read(ws)
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
        profiles = self.read(ws)
        rest = [p for p in profiles if p.id != profile_id]
        if len(rest) == len(profiles):
            return False
        self.write(ws, rest)
        return True

    def apply_update(self, ws: str, profile_id: str, cred_id: str, **fields) -> None:
        """更新某 credential 的非敏感字段;空串 secret = 不改(保留原密文)。"""
        profiles = self.read(ws)
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
        profiles = self.read(ws)
        for p in profiles:
            if p.id == profile_id:
                for c in p.credentials:
                    if c.id == cred_id:
                        c.verify_status = status
        self.write(ws, profiles)
