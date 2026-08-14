"""ws config API：env 文本契约。

GET  /api/workspaces/{ws}/config — 读配置渲染成 env 文本（凭据掩码 ••••）— workspace_member
PUT  /api/workspaces/{ws}/config — 写 env 文本（parse + 全量覆盖 + 掩码保留 + warnings）— workspace_manager（admin 直通）

PUT 语义：文本区 = ws 配置的完整定义。出现的字段=设值；未出现=清空（回落全局）。
凭据（api_key / gitlab_token）值 == •••• → 保留原值（智能保留）；否则更新或清空。
进程级 / 未知 key 不阻塞，以 warnings 返回。

spec: docs/superpowers/specs/2026-08-10-ws-config-env-textarea-design.md
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..auth.dependencies import workspace_member, workspace_manager
from ..components.ws_config_store import (
    DEFAULT_WS_PROVIDER, WsConfig, WsProviderFields, WsGitFields, WsConfigStore,
)
from ..components.ws_env_codec import parse_env_text, render_env_text, MASKED

router = APIRouter(prefix="/api/workspaces", tags=["ws-config"])


class EnvTextIn(BaseModel):
    env_text: str = ""


def _store(request: Request) -> WsConfigStore:
    return request.app.state.ws_config_store


def _render_ai_provider(cfg: WsConfig) -> str:
    """渲染 env 文本时只选工作区 provider；缺失时使用工作区默认模板。"""
    return cfg.provider.ai_provider or DEFAULT_WS_PROVIDER


@router.get("/{ws}/config")
async def get_ws_config(ws: str, request: Request, user=Depends(workspace_member)):
    store = _store(request)
    cfg = store.read(ws)
    # is_default：工作区尚无 config.yaml（未保存过）→ 前端据此预填完整推荐模板，
    # 让用户打开即见一套可用的默认配置（凭据行留空等填），而非空白或残缺默认。
    return {
        "env_text": render_env_text(cfg, ai_provider=_render_ai_provider(cfg)),
        "is_default": not store.config_exists(ws),
    }


@router.put("/{ws}/config")
async def put_ws_config(ws: str, body: EnvTextIn, request: Request,
                        user=Depends(workspace_manager)):
    store = _store(request)
    try:
        parsed = parse_env_text(body.env_text)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    existing = store.read(ws)
    f = parsed.fields
    # 凭据智能保留：值为掩码 → 保留原值；否则用新值（None=清空）
    api_key = existing.provider.api_key if f.get("api_key") == MASKED else f.get("api_key")
    gitlab_token = existing.git.gitlab_token if f.get("gitlab_token") == MASKED else f.get("gitlab_token")
    # 全量覆盖：所有字段显式设（未出现 → None，回落全局）
    cfg = WsConfig(
        provider=WsProviderFields(
            ai_provider=f.get("ai_provider"),
            api_key=api_key,
            base_url=f.get("base_url"),
            model=f.get("model"),
            small_model=f.get("small_model"),
            medium_model=f.get("medium_model"),
            large_model=f.get("large_model"),
            max_turns=f.get("max_turns"),
            adaptive_thinking=f.get("adaptive_thinking"),
        ),
        git=WsGitFields(
            gitlab_user=f.get("gitlab_user"),
            gitlab_token=gitlab_token,
        ),
        env=parsed.env,
    )
    try:
        store.write(ws, cfg)  # write 内 validate_ws_config（非法 ai_provider → ValueError → 422）
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {
        "ok": True,
        "warnings": {"ineffective": parsed.ineffective, "unknown": parsed.unknown},
    }
