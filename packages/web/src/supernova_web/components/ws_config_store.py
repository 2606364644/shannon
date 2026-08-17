"""P3c 阶段 2：per-workspace provider 配置存储。

字段级配置存 workspaces/<ws>/config.yaml；凭据字段经 CredentialVault 加密。
resolve_provider_config(ws) 只使用工作区字段，并在提交前校验必填 Provider 参数。
路径穿越双防线：_validate_ws_segment + resolve().is_relative_to。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

import yaml

from supernova_core.config.provider_settings import PROVIDER_SETTINGS

from .credential_vault import CredentialVault
from .repo_manager import _validate_ws_segment

WS_CONFIG_FILENAME = "config.yaml"


class ProviderConfigIncomplete(ValueError):
    """工作区 Provider 配置缺必填字段（如 LLM API Key）。

    携带 ``missing``（缺失的 env 字段名列表，如 ``["SUPERNOVA_OPENAI_API_KEY"]``）。
    继承 ValueError 以兼容既有 ``except ValueError``；scan API 单独 catch 它，返回结构化
    错误（code=provider_incomplete + missing），让前端显示「请前往工作区设置补全 LLM 凭据」
    而非误导性的「yaml 校验失败」。
    """

    def __init__(self, missing: list[str]):
        self.missing = list(missing)
        super().__init__(
            "workspace provider config incomplete; missing: " + ", ".join(self.missing)
        )


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
    # 扫描期 env 覆盖（KEY→value，原始 str；非凭据，不加密）。
    env: dict[str, str] = field(default_factory=dict)
    # 编辑框原样文本（凭据值已 mask_credentials 打码），yaml 键 env_text。
    # GET 直接回显它（注释/顺序/占位行全保留）；None = 旧配置（该字段引入前保存），
    # GET 回落 render_env_text 从存储字段反向渲染。
    display_text: str | None = None


DEFAULT_WS_PROVIDER = "openai_compatible"
DEFAULT_WS_BASE_URL = "https://llm-proxy.futuoa.com/v1"
DEFAULT_WS_MODEL = "glm-5.2-coder"


def default_ws_config() -> WsConfig:
    """返回新工作区的 Provider 默认模板（不包含 API key）。"""
    return WsConfig(provider=WsProviderFields(
        ai_provider=DEFAULT_WS_PROVIDER,
        base_url=DEFAULT_WS_BASE_URL,
        small_model=DEFAULT_WS_MODEL,
        medium_model=DEFAULT_WS_MODEL,
        large_model=DEFAULT_WS_MODEL,
    ))


def validate_ws_config(cfg: WsConfig) -> None:
    """仅校验 ws 显式选的 ai_provider 是合法 provider 名（PROVIDER_SETTINGS 键）。

    未覆盖 ai_provider（None）允许保存，便于配置页编辑不完整表单；
    Provider 必填字段由 resolve_provider_config() 在扫描提交前严格校验。
    """
    ap = cfg.provider.ai_provider
    if ap is None:
        return
    if ap not in PROVIDER_SETTINGS:
        raise ValueError(f"unknown ai_provider: {ap}")


def _missing_provider_fields(provider: WsProviderFields) -> list[str]:
    """返回工作区 Provider 配置缺失的 env 字段名。"""
    provider_type = provider.ai_provider
    if not provider_type:
        return ["SUPERNOVA_AI_PROVIDER"]
    settings = PROVIDER_SETTINGS.get(provider_type)
    if settings is None:
        raise ValueError(f"unknown ai_provider: {provider_type}")

    missing: list[str] = []
    for required in settings.required:
        if required == "credential":
            if not provider.api_key:
                missing.append(settings.api_key or settings.auth_token or "API credential")
            continue

        value = getattr(provider, required, None)
        if value is None or (isinstance(value, str) and not value.strip()):
            env_name = getattr(settings, required, None)
            if env_name:
                missing.append(env_name)
    return missing


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

    def config_exists(self, ws: str) -> bool:
        """工作区是否已有保存的 config.yaml（前端据此判断是否预填默认模板）。"""
        return self._config_path(ws).exists()

    def read(self, ws: str) -> WsConfig:
        path = self._config_path(ws)
        if not path.exists():
            return default_ws_config()
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
        env_raw = data.get("env") or {}
        env = {str(k): str(v) for k, v in env_raw.items()} if isinstance(env_raw, dict) else {}
        display_raw = data.get("env_text")
        return WsConfig(
            provider=WsProviderFields(**prov_kwargs),
            git=WsGitFields(**git_kwargs),
            env=env,
            display_text=display_raw if isinstance(display_raw, str) else None,
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
        if cfg.env:
            data["env"] = cfg.env
        if cfg.display_text is not None:
            data["env_text"] = cfg.display_text
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), "utf-8")

    def resolve_provider_config(self, ws: str) -> dict:
        """只用工作区字段构造 provider_config，并在提交前严格校验。"""
        ws_prov = self.read(ws).provider
        missing = _missing_provider_fields(ws_prov)
        if missing:
            raise ProviderConfigIncomplete(missing)

        resolved = asdict(ws_prov)
        resolved["type"] = resolved.pop("ai_provider")
        return resolved

    def resolve_env_overrides(self, ws: str) -> dict[str, str]:
        """返回工作区的扫描期 env 覆盖（供 scan_env 覆盖层用）；空配置返空 dict。"""
        return dict(self.read(ws).env)
