from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/fs", tags=["fs"])

MAX_ENTRIES = 5000


@router.get("/browse")
async def browse(request: Request, path: str):
    # ~ 展开 home
    if path == "~":
        path = os.path.expanduser("~")
    if not Path(path).is_absolute():
        raise HTTPException(status_code=400, detail="path must be absolute")

    resolved = Path(path).resolve()

    # allowlist（配了 SHANNON_FS_ROOTS 才约束）
    roots = request.app.state.config.fs_roots
    if roots:
        inside = any(resolved == root or root in resolved.parents for root in roots)
        if not inside:
            raise HTTPException(status_code=409, detail="path outside allowed roots")

    if not resolved.exists():
        raise HTTPException(status_code=404, detail="path not found")
    if not resolved.is_dir():
        raise HTTPException(status_code=400, detail="path is not a directory")

    try:
        scandir = list(os.scandir(resolved))
    except PermissionError:
        raise HTTPException(status_code=403, detail="permission denied")

    entries: list[dict] = []
    for entry in scandir:
        try:
            stat = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        is_dir = entry.is_dir(follow_symlinks=False)
        entries.append({
            "name": entry.name,
            "type": "dir" if is_dir else "file",
            **({} if is_dir else {"size": stat.st_size}),
            "mtime": int(stat.st_mtime),
        })

    entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))

    truncated = False
    if len(entries) > MAX_ENTRIES:
        entries = entries[:MAX_ENTRIES]
        truncated = True

    parent = str(resolved.parent) if resolved != resolved.parent else None

    return {
        "path": str(resolved),
        "parent": parent,
        "entries": entries,
        **({"truncated": True} if truncated else {}),
    }
