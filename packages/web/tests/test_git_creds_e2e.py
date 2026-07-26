"""P3c 阶段 4 端到端：ws git 凭据 → GitFetcher 解析 → clone argv 用对应账号。

PUT API（gitlab_token 密文落盘）→ 装配后的 RepoManager.git_fetcher（持 ws_config_store）
→ clone(ws) 的 _creds_for(ws) 读回 ws 凭据 → clone argv 含 ws 凭据。验证 app.py 装配
（GitFetcher 收 ws_config_store）在小对象单元测试之外的集成正确性。
"""
from unittest.mock import AsyncMock

from supernova_web.components.repo_manager import RepoManager


async def test_ws_git_creds_flow_to_clone(app_with_ws, authed_client, tmp_workspaces, monkeypatch):
    """PUT 写 ws git 凭据 → 装配后的 RepoManager.clone(ws) 的 clone argv 用 ws 凭据。"""
    (tmp_workspaces / "ws-a").mkdir()
    tok = authed_client.get("/api/auth/csrf").json()["csrf_token"]
    r = authed_client.put("/api/workspaces/ws-a/config", json={
        "provider": {},
        "git": {"gitlab_user": "bot-a", "gitlab_token": "glpat-a"},
    }, headers={"X-CSRF-Token": tok})
    assert r.status_code == 200

    # 凭据密文落盘（gitlab_token 不可见，gitlab_user 明文）
    raw = (tmp_workspaces / "ws-a" / "config.yaml").read_text()
    assert "glpat-a" not in raw
    assert "bot-a" in raw

    # 装配后的 repo_manager.git_fetcher 持 ws_config_store → clone(ws-a) 读回 ws 凭据
    rm = app_with_ws.state.repo_manager
    captured: list[list[str]] = []

    async def capture_run(self, ws, name, phase, argv):
        captured.append(list(argv))
        return True

    monkeypatch.setattr(RepoManager, "_run_git_with_progress", capture_run)
    monkeypatch.setattr(RepoManager, "_head_commit", AsyncMock(return_value="deadbeef"))

    name = await rm.clone("ws-a", "https://gitlab.example.com/x.git", None, None, "x", None)
    task = rm._jobs.get(("ws-a", name))
    if task is not None:
        await task

    assert captured, "_run_git_with_progress 未被调用"
    # clone argv 含 ws 凭据注入的 URL（bot-a:glpat-a）
    assert any("bot-a" in str(a) and "glpat-a" in str(a) for a in captured[0]), captured[0]
