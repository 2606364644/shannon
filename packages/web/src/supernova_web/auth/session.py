from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from .models import User
from .store import AuthStore, SessionRow


class SessionManager:
    def __init__(self, store: AuthStore, ttl_hours: int = 12) -> None:
        self.store = store
        self.ttl_hours = ttl_hours  # 默认 TTL（小时）；create 可按会话覆盖（SSO 24h）
        self.ttl = timedelta(hours=ttl_hours)

    def create(self, user_id: int, ttl_hours: int | None = None, auth_method: str = "password") -> str:
        sid = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        ttl = timedelta(hours=ttl_hours) if ttl_hours is not None else self.ttl
        self.store.insert_session(
            SessionRow(id=sid, user_id=user_id, expires_at=(now + ttl).isoformat(),
                       last_seen_at=now.isoformat(), auth_method=auth_method)
        )
        return sid

    def verify(self, sid: str) -> User | None:
        row = self.store.get_session(sid)
        if row is None:
            return None
        now = datetime.now(timezone.utc)
        try:
            exp = datetime.fromisoformat(row.expires_at)
        except ValueError:
            return None
        if exp < now:
            return None  # 过期视为未登录（惰性清理，不报错）
        self.store.update_session_seen(sid, now.isoformat())
        return self.store.get_user(row.user_id)

    def revoke(self, sid: str) -> None:
        self.store.delete_session(sid)

    def purge_expired(self) -> int:
        return self.store.purge_expired(datetime.now(timezone.utc).isoformat())
