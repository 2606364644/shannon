from __future__ import annotations

import sqlite3

from .models import SessionRow, User

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'user',
  created_at TEXT NOT NULL,
  must_change_password INTEGER NOT NULL DEFAULT 0,
  pinned_workspace TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS workspace_members (
  workspace_name TEXT NOT NULL,
  user_id INTEGER NOT NULL REFERENCES users(id),
  role TEXT NOT NULL DEFAULT 'member',
  created_at TEXT NOT NULL,
  PRIMARY KEY (workspace_name, user_id)
);
CREATE INDEX IF NOT EXISTS idx_wm_user ON workspace_members(user_id);
CREATE INDEX IF NOT EXISTS idx_wm_ws ON workspace_members(workspace_name);
"""

# must_change_password 列后加（2026-07-27 默认密码改密提醒）。SQLite 的
# ALTER TABLE ADD COLUMN 无 IF NOT EXISTS，对已建库需幂等补列：列已存则
# OperationalError，吞掉即可。新建库（_SCHEMA 已含该列）init_schema 后此句
# 必抛 -> 同样吞掉。保证旧 auth.db 升级、新 auth.db 重复 init 均不崩。
_ADD_MUST_CHANGE_COL = "ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0"

# pinned_workspace 列后加（2026-07-27 IA 重设计 §2.3 per-user 置顶工作区）。同上，
# ALTER TABLE ADD COLUMN 无 IF NOT EXISTS，对已建库幂等补列：列已存 -> OperationalError 吞掉。
_ADD_PINNED_WS_COL = "ALTER TABLE users ADD COLUMN pinned_workspace TEXT"


class AuthStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def init_schema(self) -> None:
        with self._conn() as c:
            c.executescript(_SCHEMA)
            try:
                c.execute(_ADD_MUST_CHANGE_COL)  # 旧库补列；新库已含 -> OperationalError 吞掉
            except sqlite3.OperationalError:
                pass
            try:
                c.execute(_ADD_PINNED_WS_COL)  # 旧库补列；新库已含 -> OperationalError 吞掉
            except sqlite3.OperationalError:
                pass

    def create_user(self, username: str, password_hash: str, role: str = "user",
                    must_change: bool = False) -> User:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO users(username, password_hash, role, created_at, must_change_password, pinned_workspace) "
                "VALUES(?,?,?,?,?,?)",
                (username, password_hash, role, now, 1 if must_change else 0, None),
            )
            return User(id=cur.lastrowid, username=username, role=role,
                        must_change_password=must_change)

    def get_user_by_username(self, username: str) -> User | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT id, username, role, must_change_password, pinned_workspace FROM users WHERE username=?", (username,)
            ).fetchone()
        return User(id=row[0], username=row[1], role=row[2],
                    must_change_password=bool(row[3]),
                    pinned_workspace=row[4]) if row else None

    def get_password_hash(self, username: str) -> str | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT password_hash FROM users WHERE username=?", (username,)
            ).fetchone()
        return row[0] if row else None

    def get_user(self, user_id: int) -> User | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT id, username, role, must_change_password, pinned_workspace FROM users WHERE id=?", (user_id,)
            ).fetchone()
        return User(id=row[0], username=row[1], role=row[2],
                    must_change_password=bool(row[3]),
                    pinned_workspace=row[4]) if row else None

    def update_password(self, user_id: int, new_hash: str) -> None:
        """改密码：写新 hash 并把 must_change_password 置 0（改密即脱默认密码提醒）。"""
        with self._conn() as c:
            c.execute(
                "UPDATE users SET password_hash=?, must_change_password=0 WHERE id=?",
                (new_hash, user_id),
            )

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

    def add_workspace_member(self, ws_name: str, user_id: int, role: str = "member") -> None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO workspace_members(workspace_name, user_id, role, created_at) VALUES(?,?,?,?)",
                (ws_name, user_id, role, now),
            )

    def remove_workspace_member(self, ws_name: str, user_id: int) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM workspace_members WHERE workspace_name=? AND user_id=?", (ws_name, user_id))

    def list_workspace_members(self, ws_name: str) -> list[tuple[int, str, str]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT u.id, u.username, m.role FROM workspace_members m JOIN users u ON u.id=m.user_id WHERE m.workspace_name=?",
                (ws_name,),
            ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    def list_user_workspaces(self, user_id: int) -> list[str]:
        with self._conn() as c:
            rows = c.execute("SELECT workspace_name FROM workspace_members WHERE user_id=?", (user_id,)).fetchall()
        return [r[0] for r in rows]

    def get_workspace_member_role(self, ws_name: str, user_id: int) -> str | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT role FROM workspace_members WHERE workspace_name=? AND user_id=?", (ws_name, user_id)
            ).fetchone()
        return row[0] if row else None

    def delete_workspace_members(self, ws_name: str) -> int:
        with self._conn() as c:
            cur = c.execute("DELETE FROM workspace_members WHERE workspace_name=?", (ws_name,))
            return cur.rowcount

    def count_admins(self) -> int:
        with self._conn() as c:
            row = c.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()
        return int(row[0]) if row else 0

    def list_all_users(self) -> list["User"]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, username, role, must_change_password, created_at, pinned_workspace FROM users ORDER BY id"
            ).fetchall()
        return [User(id=r[0], username=r[1], role=r[2],
                     must_change_password=bool(r[3]), created_at=r[4],
                     pinned_workspace=r[5]) for r in rows]

    def delete_user(self, user_id: int) -> None:
        """删用户：单事务清 workspace_members + sessions + users。
        SQLite 未开 PRAGMA foreign_keys，REFERENCES 不强制，必须手动清，否则孤儿成员 +
        被删用户 session 残留有效。护栏（自删/最后 admin）在 route 层先做。"""
        with self._conn() as c:
            c.execute("DELETE FROM workspace_members WHERE user_id=?", (user_id,))
            c.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            c.execute("DELETE FROM users WHERE id=?", (user_id,))

    def update_role(self, user_id: int, role: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))

    def reset_password(self, user_id: int, new_hash: str) -> None:
        """admin 重置他人密码：写新 hash 并置 must_change=1（强制对方首登改密）。
        区别于 update_password（自己改自己，置 must_change=0）。"""
        with self._conn() as c:
            c.execute(
                "UPDATE users SET password_hash=?, must_change_password=1 WHERE id=?",
                (new_hash, user_id),
            )

    def list_user_workspaces_with_role(self, user_id: int) -> list[tuple[str, str]]:
        """该用户的全部 ws 归属 [(ws_name, role)]（形态 A 展开面板用）。"""
        with self._conn() as c:
            rows = c.execute(
                "SELECT workspace_name, role FROM workspace_members WHERE user_id=?",
                (user_id,),
            ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def update_workspace_member_role(self, ws_name: str, user_id: int, role: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE workspace_members SET role=? WHERE workspace_name=? AND user_id=?",
                (role, ws_name, user_id),
            )

    def update_pinned_workspace(self, user_id: int, ws_name: str | None) -> None:
        """per-user 置顶工作区。ws_name=None 清除置顶。多对多关系不动--pin 不改成员关系。"""
        with self._conn() as c:
            c.execute("UPDATE users SET pinned_workspace=? WHERE id=?", (ws_name, user_id))
