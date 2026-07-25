from __future__ import annotations

from pydantic import BaseModel


class User(BaseModel):
    id: int
    username: str
    role: str = "user"  # 'admin' | 'user'


class SessionRow(BaseModel):
    id: str
    user_id: int
    expires_at: str  # ISO8601
    last_seen_at: str  # ISO8601
