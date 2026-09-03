from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from supernova_web.auth.dependencies import workspace_member
from supernova_web.auth.models import User
from supernova_web.components.topology_analysis import (
    AnalysisNotFound, TopologyProviderConfigError,
    TooManyTopologyAnalyses,
    TopologyValidationError,
)
from supernova_web.components.ws_config_store import ProviderConfigIncomplete

router = APIRouter(prefix="/api/workspaces", tags=["correlation-topology"])


class StartTopologyBody(BaseModel):
    repos: list[str]
    refresh: bool = False

    @field_validator("repos")
    @classmethod
    def _unique(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))


@router.post("/{ws}/correlation-topology/analyses", status_code=202)
async def start_analysis(ws: str, body: StartTopologyBody, request: Request,
                         _: User = Depends(workspace_member)):
    try:
        analysis_id = await request.app.state.topology_manager.start(
            ws, body.repos, refresh=body.refresh)
    except TopologyValidationError as exc:
        raise HTTPException(422, detail={
            "code": "invalid_repositories", "message": str(exc), "repos": exc.repos,
        })
    except TooManyTopologyAnalyses as exc:
        raise HTTPException(429, detail={"code": "too_many_analyses", "message": str(exc)})
    except ProviderConfigIncomplete as exc:
        raise HTTPException(422, detail={
            "code": "provider_incomplete", "message": str(exc), "missing": exc.missing,
        })
    except TopologyProviderConfigError as exc:
        raise HTTPException(422, detail={
            "code": "provider_invalid", "message": str(exc),
        })
    except ValueError as exc:
        # 兜底：store 层 ws 名/路径校验（__legacy__ 时代 500 回归）。注意须排在
        # TopologyValidationError/TopologyProviderConfigError（均为 ValueError 子类）
        # 之后，勿抢子类语义。转 422 JSON，勿让 500 纯文本把前端 json() 解析炸成
        # "body stream already read"。
        raise HTTPException(422, detail={
            "code": "invalid_workspace", "message": str(exc),
        })
    return {"analysis_id": analysis_id}


@router.get("/{ws}/correlation-topology/analyses/{analysis_id}")
async def get_analysis(ws: str, analysis_id: str, request: Request,
                       _: User = Depends(workspace_member)):
    try:
        return request.app.state.topology_manager.api_view(ws, analysis_id)
    except AnalysisNotFound:
        raise HTTPException(404, detail={"code": "analysis_not_found", "message": "analysis not found"})


@router.delete("/{ws}/correlation-topology/analyses/{analysis_id}")
async def cancel_analysis(ws: str, analysis_id: str, request: Request,
                          _: User = Depends(workspace_member)):
    try:
        await request.app.state.topology_manager.cancel(ws, analysis_id)
        return request.app.state.topology_manager.api_view(ws, analysis_id)
    except AnalysisNotFound:
        raise HTTPException(404, detail={"code": "analysis_not_found", "message": "analysis not found"})
