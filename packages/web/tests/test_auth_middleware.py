from fastapi import FastAPI, Request
from starlette.testclient import TestClient

from supernova_web.auth.store import AuthStore
from supernova_web.auth.session import SessionManager
from supernova_web.auth.middleware import AuthMiddleware


def _app(tmp_path):
    store = AuthStore(str(tmp_path / "auth.db")); store.init_schema()
    store.create_user("alice", "h")
    sm = SessionManager(store)
    app = FastAPI()
    app.state.session_manager = sm
    app.add_middleware(AuthMiddleware)

    @app.get("/who")
    def who(request: Request):
        u = getattr(request.state, "user", None)
        return {"user": u.username if u else None}

    return app, sm


def test_no_cookie_no_user(tmp_path):
    app, _ = _app(tmp_path)
    r = TestClient(app).get("/who")
    assert r.status_code == 200 and r.json() == {"user": None}


def test_valid_cookie_injects_user(tmp_path):
    app, sm = _app(tmp_path)
    sid = sm.create(user_id=1)
    c = TestClient(app); c.cookies.set("sn-sid", sid)
    r = c.get("/who")
    assert r.json() == {"user": "alice"}


def test_invalid_cookie_no_user(tmp_path):
    app, _ = _app(tmp_path)
    c = TestClient(app); c.cookies.set("sn-sid", "bogus")
    r = c.get("/who")
    assert r.json() == {"user": None}
