# packages/web/src/supernova_web/auth/seed.py
from __future__ import annotations

from pathlib import Path

from .passwords import hash_password
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


def bootstrap_default_admin(
    store: AuthStore,
    *,
    username: str,
    password: str,
    enabled: bool = True,
    must_change: bool = True,
) -> int:
    """库内无任何 admin 时，建一个默认 admin（密码经 bcrypt hash）。

    全新部署（configs/users.yaml 缺失或空）启动时由 app lifespan 调用，使新环境开箱即有
    一个可登录的 admin。已有 admin（含 users.yaml seed 出来的）→ no-op，绝不覆盖真实部署。
    返回新建数（0 或 1）。

    注意：密码长度不受 NEW_PASSWORD_MIN_LEN 约束——该约束只作用于 API create/reset/change
    路由；bootstrap 与 seed 一样直插 DB（store.create_user），默认密码 123456（6 位）可建。
    """
    if not enabled or not password or not username:
        return 0
    if store.count_admins() > 0:
        return 0
    store.create_user(username, hash_password(password), role="admin", must_change=must_change)
    return 1
