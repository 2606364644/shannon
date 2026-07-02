from fastapi.testclient import TestClient

from shannon_web.app import create_app
from shannon_web.components.scan_manager import TemporalUnavailable, TooManyScans


class FakeSM:
    def __init__(self):
        self.started = []
        self.exc = None
        self.cancelled = []

    async def start(self, req):
        if self.exc:
            raise self.exc
        self.started.append(req)
        return "WSX"

    async def cancel(self, ws):
        self.cancelled.append(ws)
        return True

    def active_pids(self):
        return {}


_BODY = {"type": "whitebox", "source": {"kind": "path", "value": "/x"}, "url": "http://e"}


def test_post_scan_202():
    fake = FakeSM()
    client = TestClient(create_app(overrides={"scan_manager": fake}))
    r = client.post("/api/scan", json=_BODY)
    assert r.status_code == 202
    assert r.json() == {"workspace": "WSX"}
    assert len(fake.started) == 1


def test_post_scan_400_temporal():
    fake = FakeSM()
    fake.exc = TemporalUnavailable()
    client = TestClient(create_app(overrides={"scan_manager": fake}))
    assert client.post("/api/scan", json=_BODY).status_code == 400


def test_post_scan_409_concurrent():
    fake = FakeSM()
    fake.exc = TooManyScans(1)
    client = TestClient(create_app(overrides={"scan_manager": fake}))
    assert client.post("/api/scan", json=_BODY).status_code == 409


def test_delete_scan():
    fake = FakeSM()
    client = TestClient(create_app(overrides={"scan_manager": fake}))
    assert client.delete("/api/scan/WSX").status_code == 200
