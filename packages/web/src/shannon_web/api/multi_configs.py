from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ValidationError

router = APIRouter(prefix="/api/multi-configs", tags=["multi-configs"])


class CreateBody(BaseModel):
    name: str
    content: str


@router.get("")
async def list_configs(request: Request):
    return request.app.state.config_store.list_configs()


@router.post("", status_code=201)
async def create_config(body: CreateBody, request: Request):
    try:
        request.app.state.config_store.write(body.name, body.content)
    except ValidationError as e:
        raise HTTPException(422, detail=e.errors())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"name": body.name}


@router.get("/{name}")
async def get_config(name: str, request: Request):
    try:
        return {"name": name, "content": request.app.state.config_store.read(name)}
    except FileNotFoundError:
        raise HTTPException(404, "config not found")
