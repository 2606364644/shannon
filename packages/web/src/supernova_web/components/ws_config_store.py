"""P3c 阶段 2：per-workspace provider 配置存储。

字段级配置存 workspaces/<ws>/config.yaml；凭据字段经 CredentialVault 加密。
resolve_provider_config(ws) = 全局默认(build_provider_config) + ws 非 None 字段覆盖。
路径穿越双防线：_validate_ws_segment + resolve().is_relative_to。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

import yaml

from supernova_core.agents.providers import build_provider_config
from supernova_core.config.provider_settings import PROVIDER_SETTINGS

from .credential_vault import CredentialVault
from .repo_manager import _validate_ws_segment

WS_CONFIG_FILENAME = "config.yaml"
# Provider 字段名（WsProviderFields）→ ProviderConfig 键名映射
_PROV_FIELD_TO_PC_KEY = {
    "ai_provider": "type",
}


@dataclass
class WsProviderFields:
    ai_provider: str | None = None
    api_key: str | None = None          # 内存明文；落盘密文
    base_url: str | None = None
    model: str | None = None
    small_model: str | None = None
    medium_model: str | None = None
    large_model: str | None = None
    max_turns: int | None = None
    adaptive_thinking: bool | None = None


@dataclass
class WsGitFields:
    gitlab_user: str | None = None
    gitlab_token: str | None = None      # 内存明文；落盘密文


@dataclass
class WsConfig:
    provider: WsProviderFields = field(default_factory=WsProviderFields)
    git: WsGitFields = field(default_factory=WsGitFields)


def validate_ws_config(cfg: WsConfig) -> None:
    """仅校验 ws 显式选的 ai_provider 是合法 provider 名（PROVIDER_SETTINGS 键）。

    未覆盖 ai_provider（None）→ 不校验（回落全局，全局已有 profile_validator）。
    required 字段深度校验留给 ProviderConfig(**dict) 构造（resolve 后自然报错）。
    """
    ap = cfg.provider.ai_provider
    if ap is None:
        return
    if ap not in PROVIDER_SETTINGS:
        raise ValueError(f"unknown ai_provider: {ap}")


class WsConfigStore:
    def __init__(self, workspaces_dir: Path, vault: CredentialVault):
        self._workspaces_dir = Path(workspaces_dir).resolve()
        self._vault = vault

    def _config_path(self, ws: str) -> Path:
        _validate_ws_segment(ws)
        p = (self._workspaces_dir / ws / WS_CONFIG_FILENAME).resolve()
        if not p.is_relative_to(self._workspaces_dir):
            raise ValueError("invalid workspace name")
        return p

    def read(self, ws: str) -> WsConfig:
        path = self._config_path(ws)
        if not path.exists():
            return WsConfig()
        data = yaml.safe_load(path.read_text("utf-8")) or {}
        prov_raw = data.get("provider") or {}
        # 凭据字段解密
        if "api_key" in prov_raw:
            prov_raw["api_key"] = self._vault.decrypt(prov_raw["api_key"])
        known_prov = {f.name for f in fields(WsProviderFields)}
        prov_kwargs = {k: prov_raw.get(k) for k in known_prov}
        # git 段（P3c 阶段 4）：gitlab_token 解密
        git_raw = data.get("git") or {}
        if "gitlab_token" in git_raw:
            git_raw["gitlab_token"] = self._vault.decrypt(git_raw["gitlab_token"])
        known_git = {f.name for f in fields(WsGitFields)}
        git_kwargs = {k: git_raw.get(k) for k in known_git}
        return WsConfig(
            provider=WsProviderFields(**prov_kwargs),
            git=WsGitFields(**git_kwargs),
        )

    def write(self, ws: str, cfg: WsConfig) -> None:
        validate_ws_config(cfg)
        path = self._config_path(ws)
        path.parent.mkdir(parents=True, exist_ok=True)
        prov = asdict(cfg.provider)
        # 凭据字段加密（仅非 None）
        if prov.get("api_key") is not None:
            prov["api_key"] = self._vault.encrypt(prov["api_key"])
        git = asdict(cfg.git)
        # git 凭据加密（仅 gitlab_token 非 None）
        if git.get("gitlab_token") is not None:
            git["gitlab_token"] = self._vault.encrypt(git["gitlab_token"])
        data = {"provider": prov, "git": git}
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), "utf-8")

    def resolve_provider_config(self, ws: str) -> dict:
        """全局默认 + ws 非 None 覆盖 → provider_config dict。"""
        defaults = asdict(build_provider_config())   # 全局（含阶段0的5字段）
        ws_prov = self.read(ws).provider
        for fld in fields(WsProviderFields):
            val = getattr(ws_prov, fld.name)
            if val is None:
                continue
            key = _PROV_FIELD_TO_PC_KEY.get(fld.name, fld.name)
            defaults[key] = val
        return defaults
