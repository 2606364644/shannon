"""模型定价管理 API：全局表（本 router）+ 工作区覆盖（ws_router）。

spec: docs/superpowers/specs/2026-08-28-global-pricing-console-design.md §4.2

全局（/api/pricing）：
- GET  全员——生效表全局视角（不含工作区层），来源徽章 + builtin 默认表（供「恢复默认」）
- PUT  admin——**完整生效表快照**，保存即接管 profile env 层（「界面为准」）
- DELETE admin——删全局表，回落 profile env / 内置；幂等

工作区（/api/workspaces/{ws}/pricing）：
- GET  workspace_member——生效表含 workspace 层 + override_exists
- PUT / DELETE workspace_manager——写/删 <ws>/pricing.override.json（清除走 DELETE）

落盘经 PricingStore；校验 ValueError → 400（branding 先例）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from supernova_core.agents.pricing import BUILTIN_PRICING_CNY

from ..auth.dependencies import require_admin, workspace_manager, workspace_member
from ..components.pricing_store import PricingStore

router = APIRouter(prefix="/api", tags=["pricing"])
ws_router = APIRouter(prefix="/api/workspaces", tags=["ws-pricing"])


class PriceTiers(BaseModel):
    # 四档可选（缺档统一由 PricingStore.validate 报 400 中文 detail，而非 pydantic 422）
    input: float | None = None
    output: float | None = None
    cache_read: float | None = None
    cache_creation: float | None = None


class PricingBody(BaseModel):
    currency: str
    models: dict[str, PriceTiers]


def _store(request: Request) -> PricingStore:
    return request.app.state.pricing_store


def _body_models(body: PricingBody) -> dict:
    return {k: v.model_dump() for k, v in body.models.items()}


def _pricing_view(store: PricingStore, ws: str | None) -> dict:
    """GET 响应公共体：生效表 + builtin 默认 + 损坏标志。"""
    eff = store.resolve_effective(ws)
    view = dict(eff)
    view["builtin_defaults"] = BUILTIN_PRICING_CNY
    corrupt = False
    _payload, g_corrupt = store.read_global_ex()
    corrupt = corrupt or g_corrupt
    if ws is not None:
        _payload, ws_corrupt = store.read_ws_override_ex(ws)
        corrupt = corrupt or ws_corrupt
    view["table_corrupt"] = corrupt
    return view


# ---- 全局 ----


@router.get("/pricing")
async def get_pricing(request: Request) -> dict:
    store = _store(request)
    view = _pricing_view(store, ws=None)   # 全局视角：不含工作区层
    _payload, corrupt = store.read_global_ex()
    view["has_global_table"] = store.read_global() is not None or corrupt
    return view


@router.put("/pricing", dependencies=[Depends(require_admin)])
async def put_pricing(body: PricingBody, request: Request) -> dict:
    store = _store(request)
    try:
        store.write_global(body.currency, _body_models(body))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.delete("/pricing", dependencies=[Depends(require_admin)])
async def delete_pricing(request: Request) -> dict:
    _store(request).clear_global()   # 幂等
    return {"ok": True}


# ---- 工作区覆盖 ----


@ws_router.get("/{ws}/pricing")
async def get_ws_pricing(ws: str, request: Request, user=Depends(workspace_member)) -> dict:
    store = _store(request)
    view = _pricing_view(store, ws=ws)
    _payload, corrupt = store.read_ws_override_ex(ws)
    view["override_exists"] = store.read_ws_override(ws) is not None or corrupt
    return view


@ws_router.put("/{ws}/pricing")
async def put_ws_pricing(ws: str, body: PricingBody, request: Request,
                         user=Depends(workspace_manager)) -> dict:
    store = _store(request)
    try:
        store.write_ws_override(ws, body.currency, _body_models(body))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@ws_router.delete("/{ws}/pricing")
async def delete_ws_pricing(ws: str, request: Request,
                            user=Depends(workspace_manager)) -> dict:
    _store(request).clear_ws_override(ws)   # 幂等；恢复继承全局
    return {"ok": True}
