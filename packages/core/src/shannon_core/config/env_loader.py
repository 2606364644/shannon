"""加载共享 .env + 当前 profile 的 .env.profiles/<name>.env。

加载顺序: 先 .env(共享), 再按 SHANNON_PROFILE 加载
.env.profiles/<profile>.env(override, 覆盖共享)。同一时刻只有
"共享 + 一个 profile" 进环境, 杜绝两套引擎配置并存。

设计见 docs/superpowers/specs/2026-06-18-env-config-design.md。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from shannon_core.models.errors import ErrorCode, PentestError

PROFILE_ENV = "SHANNON_PROFILE"


def load_env(
    base_path: str | Path = ".env",
    profiles_dir: str | Path = ".env.profiles",
) -> str:
    """加载共享 .env 与当前 profile 文件, 返回 profile 名。

    Raises:
        PentestError: SHANNON_PROFILE 未设置, 或 profile 文件不存在。
    """
    load_dotenv(base_path, override=True)

    profile = os.getenv(PROFILE_ENV)
    if not profile:
        raise PentestError(
            f"环境变量 {PROFILE_ENV} 未设置: 请在 {base_path} 中指定当前 profile"
            f"(对应 {profiles_dir}/<name>.env)",
            category="config",
            error_code=ErrorCode.CONFIG_VALIDATION_FAILED,
        )

    profile_path = Path(profiles_dir) / f"{profile}.env"
    if not profile_path.exists():
        raise PentestError(
            f"profile 文件不存在: {profile_path}(SHANNON_PROFILE={profile})。"
            f"请在该路径创建文件, 或参考 .env.profiles.example/",
            category="config",
            error_code=ErrorCode.CONFIG_VALIDATION_FAILED,
        )

    load_dotenv(profile_path, override=True)
    return profile
