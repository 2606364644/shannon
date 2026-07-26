"""P3c 阶段 2：per-workspace provider 配置 API。

GET  /api/workspaces/{ws}/config — 读配置（api_key 脱敏）— workspace_member
PUT  /api/workspaces/{ws}/config — 写配置 — workspace_manager（admin 直通）

PUT api_key 语义：空串/缺省=不改（保留原值），非空=更新。
"""
from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..auth.dependencies import workspace_member, workspace_manager
from ..components.ws_config_store import (
    WsConfig, WsProviderFields, WsGitFields, WsConfigStore,
)

router = APIRouter(prefix="/api/workspaces", tags=["ws-config"])

MASKED = "••••"


class WsProviderFieldsIn(BaseModel):
    ai_provider: Optional[str] = None
    api_key: Optional[str] = None       # "" = 不改, 非空 = 更新
    base_url: Optional[str] = None
    model: Optional[str] = None
    small_model: Optional[str] = None
    medium_model: Optional[str] = None
    large_model: Optional[str] = None
    max_turns: Optional[int] = None
    adaptive_thinking: Optional[bool] = None


class WsGitFieldsIn(BaseModel):
    gitlab_user: Optional[str] = None
    gitlab_token: Optional[str] = None    # "" = 不改, 非空 = 更新


class WsConfigIn(BaseModel):
    provider: WsProviderFieldsIn
    git: Optional[WsGitFieldsIn] = None   # P3c 阶段 4


def _store(request: Request) -> WsConfigStore:
    return request.app.state.ws_config_store


@router.get("/{ws}/config")
async def get_ws_config(ws: str, request: Request, user=Depends(workspace_member)):
    cfg = _store(request).read(ws)
    p = cfg.provider
    g = cfg.git
    return {
        "provider": {
            "ai_provider": p.ai_provider,
            "api_key": MASKED if p.api_key else None,
            "base_url": p.base_url,
            "model": p.model,
            "small_model": p.small_model,
            "medium_model": p.medium_model,
            "large_model": p.large_model,
            "max_turns": p.max_turns,
            "adaptive_thinking": p.adaptive_thinking,
        },
        "git": {
            "gitlab_user": g.gitlab_user,
            "gitlab_token": MASKED if g.gitlab_token else None,
        },
    }


@router.put("/{ws}/config")
async def put_ws_config(ws: str, body: WsConfigIn, request: Request,
                        user=Depends(workspace_manager)):
    store = _store(request)
    existing = store.read(ws)
    existing_prov = existing.provider
    # api_key 空串/None = 保留原值；非空 = 更新
    new_api_key = body.provider.api_key if body.provider.api_key else existing_prov.api_key
    # git 段（P3c 阶段 4）：gitlab_user 显式覆盖，gitlab_token 空串/缺省 = 不改
    if body.git is not None:
        new_git_user = body.git.gitlab_user
        new_git_token = body.git.gitlab_token if body.git.gitlab_token else existing.git.gitlab_token
    else:
        new_git_user = existing.git.gitlab_user
        new_git_token = existing.git.gitlab_token
    cfg = WsConfig(
        provider=WsProviderFields(
            ai_provider=body.provider.ai_provider,
            api_key=new_api_key,
            base_url=body.provider.base_url,
            model=body.provider.model,
            small_model=body.provider.small_model,
            medium_model=body.provider.medium_model,
            large_model=body.provider.large_model,
            max_turns=body.provider.max_turns,
            adaptive_thinking=body.provider.adaptive_thinking,
        ),
        git=WsGitFields(gitlab_user=new_git_user, gitlab_token=new_git_token),
    )
    try:
        store.write(ws, cfg)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True}
