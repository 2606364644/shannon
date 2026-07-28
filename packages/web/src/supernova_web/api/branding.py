"""平台品牌名(项目名)管理 —— 管理员可在设置页改名,即时生效 + 重启保留。

GET  /api/branding        → 当前覆盖值(未覆盖返回 null,前端回落 system-status 的 env 默认)
PUT  /api/branding        → 设置/清除覆盖(admin-only);body {"brand_name": "..."} | {"brand_name": null}

落盘经 BrandingStore(branding.json);system_status 解析时优先读此覆盖。
非 admin → 403(require_admin);校验失败 → 400。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..auth.dependencies import require_admin
from ..components.branding_store import BrandingStore

router = APIRouter(prefix="/api", tags=["branding"])


class BrandingBody(BaseModel):
    # None = 清除覆盖(回落 env/default);str = 设置(校验 trim/长度)。
    brand_name: str | None


@router.get("/branding")
async def get_branding(request: Request) -> dict:
    store: BrandingStore = request.app.state.branding_store
    return {"brand_name": store.get_brand_name()}


@router.put("/branding", dependencies=[Depends(require_admin)])
async def put_branding(body: BrandingBody, request: Request) -> dict:
    store: BrandingStore = request.app.state.branding_store
    try:
        if body.brand_name is None:
            store.set_brand_name(None)
        else:
            normalized = BrandingStore.validate(body.brand_name)
            store.set_brand_name(normalized)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # brand_name = 当前覆盖值(null=已清除);effective = 生效名(清除后回落 env/default),
    # 供前端即时更新左上角而无需重拉 system-status。
    from .system_status import resolve_brand_name
    return {"brand_name": store.get_brand_name(), "effective": resolve_brand_name(request)}
