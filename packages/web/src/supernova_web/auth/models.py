from __future__ import annotations

from pydantic import BaseModel


class User(BaseModel):
    id: int
    username: str
    role: str = "user"  # 'admin' | 'user'
    # 默认账号（users.yaml 标 must_change_password: true）seed 时置 True；
    # 登录后前端据此提醒改密，改密成功（update_password）后置 False。
    must_change_password: bool = False


class SessionRow(BaseModel):
    id: str
    user_id: int
    expires_at: str  # ISO8601
    last_seen_at: str  # ISO8601
