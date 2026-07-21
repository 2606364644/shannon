"""加载共享 .env + 当前 profile 的 .env.profiles/<name>.env。

加载顺序: 先 .env(共享), 再按 SUPERNOVA_PROFILE 加载
.env.profiles/<profile>.env(override, 覆盖共享)。同一时刻只有
"共享 + 一个 profile" 进环境, 杜绝两套引擎配置并存。

设计见 docs/superpowers/specs/2026-06-18-env-config-design.md。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from supernova_core.models.errors import ErrorCode, PentestError

PROFILE_ENV = "SUPERNOVA_PROFILE"


def load_env(
    base_path: str | Path = ".env",
    profiles_dir: str | Path = ".env.profiles",
) -> str:
    """加载共享 .env 与当前 profile 文件, 返回 profile 名。

    Raises:
        PentestError: SUPERNOVA_PROFILE 未设置, 或 profile 文件不存在。
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
            f"profile 文件不存在: {profile_path}(SUPERNOVA_PROFILE={profile})。"
            f"请在该路径创建文件, 或参考 .env.profiles.example/",
            category="config",
            error_code=ErrorCode.CONFIG_VALIDATION_FAILED,
        )

    load_dotenv(profile_path, override=True)

    # 自动 wire per-profile 定价 override（spec 2026-07-09 §4.5）。
    # 约定：.env.profiles/<profile>.pricing.json，或去掉引擎后缀的 <base>.pricing.json
    # （glm-anthropic / glm-openai 共用 glm.pricing.json；deepseek → deepseek.pricing.json）。
    # 仅当用户未显式设 SUPERNOVA_PRICING_OVERRIDE 时 wire（setdefault 语义），定价回落内置表。
    if "SUPERNOVA_PRICING_OVERRIDE" not in os.environ:
        base_profile = profile.rsplit("-", 1)[0] if "-" in profile else profile
        for candidate in (
            Path(profiles_dir) / f"{profile}.pricing.json",
            Path(profiles_dir) / f"{base_profile}.pricing.json",
        ):
            if candidate.exists():
                os.environ["SUPERNOVA_PRICING_OVERRIDE"] = str(candidate)
                break

    return profile
