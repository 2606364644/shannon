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
from ..components.ws_config_store import WsConfig, WsProviderFields, WsConfigStore

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


class WsConfigIn(BaseModel):
    provider: WsProviderFieldsIn


def _store(request: Request) -> WsConfigStore:
    return request.app.state.ws_config_store


@router.get("/{ws}/config")
async def get_ws_config(ws: str, request: Request, user=Depends(workspace_member)):
    cfg = _store(request).read(ws)
    p = cfg.provider
    return {"provider": {
        "ai_provider": p.ai_provider,
        "api_key": MASKED if p.api_key else None,
        "base_url": p.base_url,
        "model": p.model,
        "small_model": p.small_model,
        "medium_model": p.medium_model,
        "large_model": p.large_model,
        "max_turns": p.max_turns,
        "adaptive_thinking": p.adaptive_thinking,
    }}


@router.put("/{ws}/config")
async def put_ws_config(ws: str, body: WsConfigIn, request: Request,
                        user=Depends(workspace_manager)):
    store = _store(request)
    existing = store.read(ws).provider
    # api_key 空串/None = 保留原值；非空 = 更新
    new_api_key = body.provider.api_key if body.provider.api_key else existing.api_key
    cfg = WsConfig(provider=WsProviderFields(
        ai_provider=body.provider.ai_provider,
        api_key=new_api_key,
        base_url=body.provider.base_url,
        model=body.provider.model,
        small_model=body.provider.small_model,
        medium_model=body.provider.medium_model,
        large_model=body.provider.large_model,
        max_turns=body.provider.max_turns,
        adaptive_thinking=body.provider.adaptive_thinking,
    ))
    try:
        store.write(ws, cfg)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True}
