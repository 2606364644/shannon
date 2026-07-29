from fastapi.testclient import TestClient

from supernova_web.app import create_app


def test_system_status_shape(authed_client):
    # 现有测试：/api/system-status 已被 T11 加 Depends(current_user)，用 authed_client 装配
    r = authed_client.get("/api/system-status")
    assert r.status_code == 200
    body = r.json()
    # 顶层字段
    assert body["ai_provider"] in {"claude", "openai"}
    assert body["browser_engine"] in {"agent-browser", "playwright"}
    # 版本字标前缀跟随品牌名(改名后不再残留 "supernova"):形如 "<brand_name> <ver>"
    assert body["version"].startswith(body["brand_name"])
    # 品牌名(左上角字标数据源,默认 Supernova,经 SUPERNOVA_WEB_BRAND_NAME 可覆盖)
    assert body["brand_name"]
    # git 子对象(拆分:二进制存在 / GitLab 凭据已配置,两个独立信号)
    git = body["git"]
    assert isinstance(git["binary_available"], bool)
    assert isinstance(git["credentials_configured"], bool)
    # temporal 子对象
    t = body["temporal"]
    assert t["enabled"] is True
    assert "host" in t and isinstance(t["host"], str)
    assert t["last_status"] in {"connected", "error"}
    assert "last_error" in t
