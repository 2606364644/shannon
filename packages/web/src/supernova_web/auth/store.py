from __future__ import annotations

import sqlite3
from typing import Iterable

from .models import SessionRow, User

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'user',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT);
"""


class AuthStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def init_schema(self) -> None:
        with self._conn() as c:
            c.executescript(_SCHEMA)

    def create_user(self, username: str, password_hash: str, role: str = "user") -> User:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO users(username, password_hash, role, created_at) VALUES(?,?,?,?)",
                (username, password_hash, role, now),
            )
            return User(id=cur.lastrowid, username=username, role=role)

    def get_user_by_username(self, username: str) -> User | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT id, username, role FROM users WHERE username=?", (username,)
            ).fetchone()
        return User(id=row[0], username=row[1], role=row[2]) if row else None

    def get_user(self, user_id: int) -> User | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT id, username, role FROM users WHERE id=?", (user_id,)
            ).fetchone()
        return User(id=row[0], username=row[1], role=row[2]) if row else None

    def insert_session(self, row: SessionRow) -> None:
        from datetime import datetime, timezone
        created = datetime.now(timezone.utc).isoformat()
        with self._conn() as c:
            c.execute(
                "INSERT INTO sessions(id, user_id, created_at, expires_at, last_seen_at) VALUES(?,?,?,?,?)",
                (row.id, row.user_id, created, row.expires_at, row.last_seen_at),
            )

    def get_session(self, sid: str) -> SessionRow | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT id, user_id, expires_at, last_seen_at FROM sessions WHERE id=?", (sid,)
            ).fetchone()
        return SessionRow(id=row[0], user_id=row[1], expires_at=row[2], last_seen_at=row[3]) if row else None

    def update_session_seen(self, sid: str, iso_ts: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE sessions SET last_seen_at=? WHERE id=?", (iso_ts, sid))

    def delete_session(self, sid: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM sessions WHERE id=?", (sid,))

    def purge_expired(self, now_iso: str) -> int:
        with self._conn() as c:
            cur = c.execute("DELETE FROM sessions WHERE expires_at < ?", (now_iso,))
            return cur.rowcount
