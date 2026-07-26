"""P3c 阶段 2：app 装配 vault/store + 路由注册。"""
from supernova_web.components.credential_vault import CredentialVault
from supernova_web.components.ws_config_store import WsConfigStore


def test_app_wires_credential_vault(app_with_ws):
    assert isinstance(app_with_ws.state.credential_vault, CredentialVault)


def test_app_wires_ws_config_store(app_with_ws):
    assert isinstance(app_with_ws.state.ws_config_store, WsConfigStore)


def test_app_registers_ws_config_routes(app_with_ws):
    """GET /api/workspaces/{ws}/config 路由已注册（非 404；未登录返 401 即说明路由存在）。"""
    from starlette.testclient import TestClient
    c = TestClient(app_with_ws)
    r = c.get("/api/workspaces/ws-x/config")
    assert r.status_code != 404
