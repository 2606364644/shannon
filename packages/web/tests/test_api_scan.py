from fastapi.testclient import TestClient

from supernova_web.app import create_app
from supernova_web.components.scan_manager import TemporalUnavailable, TooManyScans


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


def test_cancel_passes_through_via_signal():
    """cancel 对 owner=host scan 返 via:signal 时,api 透传给前端(语义提示)。"""
    class HostSM:
        async def cancel(self, ws):
            return {"cancelled": ws, "via": "signal"}

        def active_pids(self):
            return {}

    client = TestClient(create_app(overrides={"scan_manager": HostSM()}))
    r = client.delete("/api/scan/WSX")
    assert r.status_code == 200
    assert r.json() == {"cancelled": "WSX", "via": "signal"}


def test_cancel_404_when_workspace_missing():
    """workspace 不存在(scan_manager.cancel 返 None)→ 唯一 404(spec §4.6)。"""
    class NoScanSM:
        async def cancel(self, ws):
            return None

        def active_pids(self):
            return {}

    client = TestClient(create_app(overrides={"scan_manager": NoScanSM()}))
    assert client.delete("/api/scan/WSX").status_code == 404
