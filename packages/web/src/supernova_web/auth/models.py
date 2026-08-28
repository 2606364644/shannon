from __future__ import annotations

from pydantic import BaseModel


class User(BaseModel):
    id: int
    username: str
    role: str = "user"  # 'admin' | 'user'
    # 默认账号（users.yaml 标 must_change_password: true）seed 时置 True；
    # 登录后前端据此提醒改密，改密成功（update_password）后置 False。
    must_change_password: bool = False
    created_at: str = ""  # ISO8601；list_all_users 填充，其余构造点默认空
    # per-user 置顶工作区（IA 重设计 §2.3）：用户从归属 ws 里 pin 一个，
    # 顶栏「工作区」默认跳它。None = 未置顶（跳最近归属 ws）。多对多关系不动。
    pinned_workspace: str | None = None
    # SSO（OA passport，spec 2026-08-25 §6）：头像 URL（浏览器直连加载；账密用户 None）
    avatar_url: str | None = None
    # 'password' | 'sso'——账号来源（信息性；SSO 初建户密码按 nick+@123 设置后仅存 hash）
    auth_provider: str = "password"


class SessionRow(BaseModel):
    id: str
    user_id: int
    expires_at: str  # ISO8601
    last_seen_at: str  # ISO8601
    auth_method: str = "password"  # 登录来源；登出时判定是否返回 OA 登出跳转


class SsoConfig(BaseModel):
    """SSO 运行时配置（spec 2026-08-26 §4，auth.db `sso_config` 单行表）。
    env 仅作首次种子来源；此后设置页（PUT admin config）是唯一写入方。
    public_base_url 留空 → 运行时回落 https://{auth_domain}（sso.resolve_runtime）。"""
    enabled: bool = False
    auth_domain: str = ""
    public_base_url: str = ""
    passport_base: str = "https://passport.futuoa.com"
    session_ttl_hours: int = 24
    updated_at: str = ""  # ISO8601；种子/更新时填
    updated_by: str = ""
