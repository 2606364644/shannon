import pytest
from fastapi import HTTPException, Request

from supernova_web.auth.dependencies import current_user, require_admin
from supernova_web.auth.models import User


def _req(user):
    r = Request({"type": "http"})
    r.state.user = user
    return r


def test_current_user_ok():
    u = User(id=1, username="alice", role="user")
    assert current_user(_req(u)).username == "alice"


def test_current_user_unauthenticated():
    with pytest.raises(HTTPException) as e:
        current_user(_req(None))
    assert e.value.status_code == 401


def test_require_admin_ok():
    u = User(id=1, username="admin", role="admin")
    assert require_admin(_req(u)).role == "admin"


def test_require_admin_forbidden_for_user():
    u = User(id=1, username="alice", role="user")
    with pytest.raises(HTTPException) as e:
        require_admin(_req(u))
    assert e.value.status_code == 403
