from __future__ import annotations

import shutil
from pathlib import Path

from supernova_web.auth.models import User
from supernova_web.auth.store import AuthStore

from .scan_store import read_workspace_meta, write_workspace_meta

GLOBAL_ADMIN_USERNAME = "admin"


def is_global_admin(user: User) -> bool:
    """Return whether ``user`` is the canonical global workspace administrator."""
    return user.username == GLOBAL_ADMIN_USERNAME and user.role == "admin"


def is_safe_workspace_name(name: str) -> bool:
    """Whether a username can safely be used as a workspace directory name."""
    return (
        bool(name)
        and name not in {".", ".."}
        and not name.startswith(".")
        and Path(name).name == name
        and "/" not in name
        and "\\" not in name
    )


def _global_admin(store: AuthStore) -> User | None:
    user = store.get_user_by_username(GLOBAL_ADMIN_USERNAME)
    return user if user is not None and is_global_admin(user) else None


def _workspace_is_real(ws_dir: Path) -> bool:
    return ws_dir.is_dir() and read_workspace_meta(ws_dir) is not None


def ensure_user_workspace(workspaces_dir: Path, store: AuthStore, user: User) -> Path:
    """Idempotently create ``<workspaces_dir>/<username>`` and its memberships."""
    if not is_safe_workspace_name(user.username):
        raise ValueError("unsafe workspace name")

    workspaces_dir.mkdir(parents=True, exist_ok=True)
    ws_dir = workspaces_dir / user.username
    if ws_dir.exists() and not _workspace_is_real(ws_dir):
        raise FileExistsError(f"workspace conflict: {user.username}")

    created = False
    if not ws_dir.exists():
        try:
            ws_dir.mkdir()
            created = True
            write_workspace_meta(ws_dir, name=user.username, owner=user.username)
        except Exception:
            if created:
                shutil.rmtree(ws_dir, ignore_errors=True)
            raise

    store.add_workspace_member(user.username, user.id, "manager")
    admin = _global_admin(store)
    if admin is not None:
        store.add_workspace_member(user.username, admin.id, "manager")
    return ws_dir


def ensure_global_admin_member(workspace_name: str, store: AuthStore) -> None:
    admin = _global_admin(store)
    if admin is not None:
        store.add_workspace_member(workspace_name, admin.id, "manager")


def ensure_global_admin_access(workspaces_dir: Path, store: AuthStore) -> None:
    """Idempotently add canonical ``admin`` to every non-system workspace directory."""
    admin = _global_admin(store)
    if admin is None or not workspaces_dir.is_dir():
        return

    for ws_dir in workspaces_dir.iterdir():
        if not ws_dir.is_dir() or ws_dir.name.startswith("."):
            continue
        store.add_workspace_member(ws_dir.name, admin.id, "manager")


def ensure_all_user_workspaces(workspaces_dir: Path, store: AuthStore) -> None:
    """Startup reconciliation for historical users and global admin access."""
    for user in store.list_all_users():
        try:
            ensure_user_workspace(workspaces_dir, store, user)
        except (FileExistsError, ValueError, OSError):
            # A single malformed username or conflicting directory must not block startup.
            continue
    ensure_global_admin_access(workspaces_dir, store)
