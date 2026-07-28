"""Web 控制台品牌名(项目名)运行时可改存储。

brand_name 的优先级: branding.json 覆盖 > SUPERNOVA_WEB_BRAND_NAME env > "Supernova"。
env 仅启动时读、改了要重启;本 store 让管理员能在设置页改名并即时生效、重启后保留。

落盘: <workspaces_dir>/branding.json = {"brand_name": "<trimmed>"}
- 空文件 / 缺键 / None → 视作未覆盖(get_brand_name 返回 None,回落 env)。
- 写入前 trim; trim 后为空 → 当作清除(写回 None)。
原子写(tmp → replace)防半截文件;目录不存在时建。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

BRANDING_FILENAME = "branding.json"

# 品牌名长度上限:左上角字标 + 浏览器标签 title,过长难看且易撑爆布局。
MAX_BRAND_NAME = 32


class BrandingStore:
    def __init__(self, workspaces_dir: Path) -> None:
        self._dir = Path(workspaces_dir)
        self._path = self._dir / BRANDING_FILENAME

    def _read_raw(self) -> dict:
        try:
            with self._path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except (json.JSONDecodeError, OSError):
            # 损坏文件不当机:回落默认(env/default),管理员可重新设。
            return {}

    def get_brand_name(self) -> str | None:
        """返回已覆盖的品牌名(已 trim);未覆盖 / 损坏 / 空串 → None(回落 env/default)。"""
        val = self._read_raw().get("brand_name")
        if not isinstance(val, str):
            return None
        trimmed = val.strip()
        return trimmed or None

    def set_brand_name(self, name: str | None) -> str | None:
        """设置品牌名。None / 空白 → 清除覆盖(回落 env);否则 trim 后原子落盘。返回生效值。"""
        self._dir.mkdir(parents=True, exist_ok=True)
        if name is None:
            payload: dict = {}
        else:
            trimmed = name.strip()
            payload = {"brand_name": trimmed} if trimmed else {}
        # 原子写:tmp 文件同目录 → os.replace,防进程中断留半截 JSON。
        fd, tmp = tempfile.mkstemp(dir=str(self._dir), prefix=".branding.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return self.get_brand_name()

    @staticmethod
    def validate(name: str) -> str:
        """校验并归一化:trim;非空且 ≤ MAX;非法抛 ValueError(由 route 转 400/422)。"""
        if not isinstance(name, str):
            raise ValueError("brand_name must be a string")
        trimmed = name.strip()
        if not trimmed:
            raise ValueError("brand_name must not be empty")
        if len(trimmed) > MAX_BRAND_NAME:
            raise ValueError(f"brand_name must be at most {MAX_BRAND_NAME} characters")
        return trimmed
