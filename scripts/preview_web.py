#!/usr/bin/env python3
"""本地预览启动器:自包含实例(新后端 + 新前端 dist + 已知 admin 账号)。

仅用于设计预览/截图,不进生产。用完 Ctrl-C。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="snova-preview-"))
WS = TMP / "workspaces"
WS.mkdir()
SEED = TMP / "users.yaml"
# password: preview123, role: admin, must_change: false
SEED.write_text(
    'users:\n  - username: admin\n    password_hash: "$2b$12$BtMhXZla5/LNEQxofDcA4eE7amyD7o9rP4cpNTfOdlnGVUvhlh8lS"\n    role: admin\n    must_change_password: false\n',
    encoding="utf-8",
)

os.environ["SUPERNOVA_WORKER_ROOT"] = str(TMP)
os.environ["SUPERNOVA_WEB_COOKIE_SECURE"] = "0"
os.environ["SUPERNOVA_WEB_FRONTEND_DIR"] = str(REPO / "packages" / "web" / "frontend" / "dist")
os.environ["SUPERNOVA_WEB_USERS_SEED"] = str(SEED)
os.environ["SUPERNOVA_WEB_BRAND_NAME"] = "Supernova"
# 避免探测真实 temporal(连不上无所谓,设置页会显 error 态)
os.environ.setdefault("SUPERNOVA_TEMPORAL_HOST", "localhost")
os.environ.setdefault("SUPERNOVA_TEMPORAL_PORT", "7233")

sys.path.insert(0, str(REPO / "packages" / "web" / "src"))
sys.path.insert(0, str(REPO / "packages" / "core" / "src"))

import uvicorn
from supernova_web.app import create_app

app = create_app()

if __name__ == "__main__":
    print(f"[preview] workspace={TMP}")
    print("[preview] login: admin / preview123")
    print("[preview] http://localhost:7882/settings")
    uvicorn.run(app, host="127.0.0.1", port=7882, log_level="warning")
