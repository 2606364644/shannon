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


class SessionRow(BaseModel):
    id: str
    user_id: int
    expires_at: str  # ISO8601
    last_seen_at: str  # ISO8601
