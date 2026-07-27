# packages/web/src/supernova_web/auth/seed.py
from __future__ import annotations

from pathlib import Path

from .store import AuthStore


def seed_users(store: AuthStore, yaml_path: str) -> int:
    """把 users.yaml 里不存在于 SQLite 的用户 upsert 进去；已存在用户不动（避免重启覆盖改过的密码）。
    yaml 缺失或 users 为空 → 0。返回新建用户数。"""
    p = Path(yaml_path)
    if not p.is_file():
        return 0
    try:
        import yaml  # PyYAML：core 已依赖，复用
    except ImportError:
        return 0
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    users = data.get("users") or []
    created = 0
    for u in users:
        username = u.get("username")
        if not username:
            continue
        if store.get_user_by_username(username) is not None:
            continue  # 不覆盖
        store.create_user(
            username,
            u.get("password_hash", ""),
            role=u.get("role", "user"),
            must_change=bool(u.get("must_change_password", False)),
        )
        created += 1
    return created
