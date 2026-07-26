# P3c 阶段 2：per-workspace 配置 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每个 workspace 独立的 provider 配置（字段级，存 `workspaces/<ws>/config.yaml`，凭据 Fernet 加密）+ `WsConfigStore` + admin/manager API（GET 脱敏 / PUT 写入）+ scan_manager 按 ws 解析配置塞 `PipelineInput.provider_config` + 前端 ws 配置页（字段表单 + 凭据脱敏）。各 ws 用各自配置 scan（此阶段并发仍 = 1，阶段 3 解锁）。

**Architecture:** `CredentialVault`（cryptography Fernet）封装凭据加解密，master key 从 `SUPERNOVA_MASTER_KEY` env 优先读、否则 `workspaces/.master_key`（首启生成，0600）。`WsConfigStore(workspaces_dir, vault)` 读写 `workspaces/<ws>/config.yaml`（`WsConfig` dataclass，字段级，凭据字段密文），`resolve_provider_config(ws)` 拼「全局默认 + ws 显式覆盖」→ provider_config dict。`api/ws_config.py`（仿 `members.py`）暴露 GET（脱敏 `api_key → "••••"`）+ PUT（`workspace_manager` 鉴权，`api_key` 空串=不改/非空=更新）。`scan_manager._submit_whitebox`（阶段 1 已塞全局 provider_config）改为 `ws_config_store.resolve_provider_config(ws)` + 提交前 `validate_ws_config` fail-fast。前端 `/p/:workspace/settings` 子路由（非 tab，header 齿轮入口）+ `WsSettingsTab` 表单（仿 AddRepoDialog）+ `apiPut` + `api/wsConfig.ts` + i18n `wsConfig` namespace。

**Tech Stack:** Python 3.11+ / cryptography(Fernet) / PyYAML / FastAPI / pydantic / pytest + monkeypatch；React 18 / react-router-dom / shadcn new-york / vitest + @testing-library/react + MSW。

## Global Constraints

- **前置依赖**：阶段 0（ProviderConfig 5 字段）+ 阶段 1（`PipelineInput.provider_config` 穿线 + scan_manager 全局构造）**必须已实现**。**执行阶段 2 前先完成阶段 1**。Task 5（scan_manager 按 ws 解析）是在阶段 1 的全局构造基础上改为 per-ws。
- **字段级配置**：未填字段 = `None` = 回落全局 env（`resolve_provider_config` 拼 `build_provider_config()` 默认 + ws 非 None 覆盖）。**不**做全 `.env` 任意覆盖（危险 + 不现实）。
- **凭据加密白名单**：`provider.api_key`（+ 阶段 4 的 `git.gitlab_token`）。落盘密文（`vault.encrypt`），内存明文。白名单常量化（`CREDENTIAL_FIELDS`）。
- **master key 优先级**：`SUPERNOVA_MASTER_KEY` env（Fernet key 字符串）> `workspaces/.master_key` 文件（首启 `Fernet.generate_key()` 生成，`0600`，gitignored）。CredentialVault 构造期处理，不放 lifespan。
- **路径穿越双防线**：`_validate_ws_segment`（`repo_manager.py:28-35`，禁 `..`/`/`/NUL）+ `resolve().is_relative_to(workspaces_dir)`（`_resolve_repo_dir` 模式）。
- **GET 脱敏**：`api_key` → `"••••"`（已配置）或 `None`（未配置）；不回传明文。**PUT `api_key` 语义**：空串/缺省 = 不改（保留原值），非空 = 更新。
- **鉴权**：PUT `Depends(workspace_manager)`（admin 直通，无需 OR 组合）；GET `Depends(workspace_member)`（成员可读脱敏后的）。
- **`validate_ws_config` 轻量**：仅校验「ws 显式选的 `ai_provider` 是合法 provider 名」（`PROVIDER_SETTINGS` 键）。required 字段深度校验留给 `ProviderConfig(**dict)` 构造（resolve 后自然报错）。未覆盖 `ai_provider`（None）→ 不校验（回落全局，全局已有启动校验）。
- **scan_manager fail-fast**：提交前 `validate_ws_config(read(ws))`，非法 → 不提交（web 层 422 或 scan_manager 抛错）。
- **新依赖**：`cryptography`（Fernet）。加到 `packages/web/pyproject.toml`（或 root，按 monorepo 惯例）。
- **不动 multi-configs**（YAGNI）；黑盒 web 路径未接（Phase C），阶段 2 只白盒。
- **测试隔离**：`monkeypatch` env / tmp_path；按 CLAUDE.md 只跑改动相关测试文件。Fernet 测试用真实加密往返（不用 mock，验证真实加解密）。

---

## File Structure

| 文件 | 职责 | 本计划改动 |
|---|---|---|
| `packages/web/src/supernova_web/components/credential_vault.py` | Fernet 加密封装 | 新建（Task 1） |
| `packages/web/src/supernova_web/components/ws_config_store.py` | `WsConfig` + `WsConfigStore` + `validate_ws_config` | 新建（Task 2） |
| `packages/web/src/supernova_web/config.py` | WebConfig | 加 `master_key_file` 属性（Task 3） |
| `packages/web/src/supernova_web/app.py` | 装配 + 路由注册 | 装配 vault/store + scan_manager 注入 + 注册 ws_config.router（Task 3） |
| `packages/web/src/supernova_web/components/scan_manager.py` | scan 提交 | `__init__` 加 `ws_config_store` + `_submit_whitebox` 按 ws 解析（Task 5） |
| `packages/web/src/supernova_web/api/ws_config.py` | ws config API | 新建 GET/PUT（Task 4） |
| `packages/web/pyproject.toml`（或 root） | 依赖 | 加 `cryptography`（Task 1） |
| `packages/web/frontend/src/api/client.ts` | api client | 加 `apiPut` 导出（Task 6） |
| `packages/web/frontend/src/api/wsConfig.ts` | ws config client | 新建（Task 6） |
| `packages/web/frontend/src/routes/WorkspaceDetail/WsSettingsTab.tsx` | ws 配置页 | 新建（Task 6） |
| `packages/web/frontend/src/router.tsx` | 路由 | 子路由加 settings（Task 6） |
| `packages/web/frontend/src/routes/WorkspaceDetail/index.tsx` | ws 详情 header | 加齿轮入口（Task 6） |
| `packages/web/frontend/src/pages/SettingsPage.tsx` | 全局设置 | 加 perWsHint Card（Task 6） |
| `packages/web/frontend/src/locales/{zh,en}.json` | i18n | 新建 `wsConfig` namespace + `settings.perWsHint`（Task 6） |

---

## Task 1: CredentialVault（Fernet 加密 + cryptography 依赖）

**Files:**
- Create: `packages/web/src/supernova_web/components/credential_vault.py`
- Modify: `packages/web/pyproject.toml`（加 `cryptography` 依赖）
- Test: `packages/web/tests/test_credential_vault.py`

**Interfaces:**
- Consumes: `cryptography.fernet.Fernet`；`SUPERNOVA_MASTER_KEY` env
- Produces: `CredentialVault(master_key_file: Path)`——`.encrypt(plaintext|None) -> str|None` / `.decrypt(token|None) -> str|None` / `.CREDENTIAL_FIELDS` 常量。下游 Task 2 的 WsConfigStore 消费。

- [ ] **Step 1: 加依赖** — 编辑 `packages/web/pyproject.toml`，dependencies 加 `cryptography`（按现有版本固定风格，如 `cryptography>=42.0` 或不锁版本）。`cd packages/web && uv sync` 确认可装。

- [ ] **Step 2: 写失败测试** — 新建 `packages/web/tests/test_credential_vault.py`

```python
"""P3c 阶段 2：CredentialVault Fernet 加解密。"""
import os
from pathlib import Path
import pytest

from supernova_web.components.credential_vault import CredentialVault


def test_encrypt_decrypt_roundtrip(tmp_path):
    vault = CredentialVault(tmp_path / ".master_key")
    cipher = vault.encrypt("sk-secret-123")
    assert cipher != "sk-secret-123"          # 密文非明文
    assert vault.decrypt(cipher) == "sk-secret-123"


def test_encrypt_none_is_none(tmp_path):
    vault = CredentialVault(tmp_path / ".master_key")
    assert vault.encrypt(None) is None
    assert vault.decrypt(None) is None


def test_first_run_generates_master_key_file(tmp_path):
    key_file = tmp_path / ".master_key"
    assert not key_file.exists()
    CredentialVault(key_file)
    assert key_file.exists()
    # 0600 权限（非 Windows）
    if os.name == "posix":
        assert oct(key_file.stat().st_mode)[-3:] == "600"


def test_env_master_key_takes_priority(monkeypatch, tmp_path):
    """SUPERNOVA_MASTER_KEY env 优先于文件。"""
    from cryptography.fernet import Fernet
    env_key = Fernet.generate_key().decode()
    monkeypatch.setenv("SUPERNOVA_MASTER_KEY", env_key)
    file_key = Fernet.generate_key()
    (tmp_path / ".master_key").write_bytes(file_key)
    vault = CredentialVault(tmp_path / ".master_key")
    # 用 env key 加密，用另一个 vault（同 env key）能解
    cipher = vault.encrypt("x")
    other = CredentialVault(tmp_path / "other.key")  # 也读 env
    assert other.decrypt(cipher) == "x"


def test_decrypt_invalid_token_returns_none(tmp_path, caplog):
    """密文损坏/master key 不匹配 → None + warning，不崩。"""
    vault = CredentialVault(tmp_path / ".master_key")
    assert vault.decrypt("not-a-valid-fernet-token") is None


def test_existing_master_key_reused(tmp_path):
    """二次启动复用已生成的 key（不重新生成）。"""
    key_file = tmp_path / ".master_key"
    v1 = CredentialVault(key_file)
    cipher = v1.encrypt("persist")
    v2 = CredentialVault(key_file)  # 复用
    assert v2.decrypt(cipher) == "persist"


def test_credential_fields_constant():
    """凭据白名单常量（防漏加密）。"""
    assert "api_key" in CredentialVault.CREDENTIAL_FIELDS
```

- [ ] **Step 3: 跑测试确认失败** — `cd packages/web && uv run pytest tests/test_credential_vault.py -v`
  - 预期：FAIL（模块不存在）

- [ ] **Step 4: 实现 CredentialVault** — 新建 `packages/web/src/supernova_web/components/credential_vault.py`

```python
"""P3c 阶段 2：凭据对称加密封装（Fernet）。

master key 优先级：SUPERNOVA_MASTER_KEY env（Fernet key 字符串）> workspaces/.master_key 文件。
首启（env 未设 + 文件不存在）生成 key 落盘 0600。生产建议经 env/secret 注入。
凭据字段白名单 CREDENTIAL_FIELDS：WsConfigStore 据此决定哪些字段加密。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

_log = logging.getLogger(__name__)


class CredentialVault:
    CREDENTIAL_FIELDS = frozenset({"api_key", "auth_token", "gitlab_token"})

    def __init__(self, master_key_file: Path):
        self._master_key_file = Path(master_key_file)
        self._fernet = Fernet(self._load_or_create_key())

    def _load_or_create_key(self) -> bytes:
        env_key = os.environ.get("SUPERNOVA_MASTER_KEY")
        if env_key:
            return env_key.encode()
        if self._master_key_file.exists():
            return self._master_key_file.read_bytes().strip()
        # 首启生成
        key = Fernet.generate_key()
        self._master_key_file.parent.mkdir(parents=True, exist_ok=True)
        self._master_key_file.write_bytes(key)
        try:
            os.chmod(self._master_key_file, 0o600)
        except OSError:
            pass  # 非 POSIX 容忍
        _log.info("首启生成 master key: %s", self._master_key_file)
        return key

    def encrypt(self, plaintext: str | None) -> str | None:
        if plaintext is None:
            return None
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str | None) -> str | None:
        if token is None:
            return None
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError):
            _log.warning("凭据解密失败（master key 不匹配或密文损坏），降级 None")
            return None
```

- [ ] **Step 5: 跑测试确认通过** — `cd packages/web && uv run pytest tests/test_credential_vault.py -v`
  - 预期：7 PASS

- [ ] **Step 6: Commit**

```bash
git add packages/web/src/supernova_web/components/credential_vault.py \
        packages/web/tests/test_credential_vault.py packages/web/pyproject.toml
git commit -m "feat(web): P3c 阶段2 CredentialVault Fernet 凭据加密

cryptography Fernet 封装：master key env(SUPERNOVA_MASTER_KEY)优先，否则
workspaces/.master_key 首启生成(0600)。encrypt/decrypt 往返 + None 透传 +
损坏密文降级 None。CREDENTIAL_FIELDS 白名单常量。"
```

---

## Task 2: WsConfig + WsConfigStore + validate_ws_config

**Files:**
- Create: `packages/web/src/supernova_web/components/ws_config_store.py`
- Test: `packages/web/tests/test_ws_config_store.py`

**Interfaces:**
- Consumes: Task 1 的 `CredentialVault`；`supernova_core.agents.providers.build_provider_config` / `provider_settings.PROVIDER_SETTINGS`；`repo_manager._validate_ws_segment`
- Produces: `WsProviderFields` / `WsConfig` dataclass；`WsConfigStore(workspaces_dir, vault)`——`.read(ws) -> WsConfig` / `.write(ws, cfg)` / `.resolve_provider_config(ws) -> dict`；`validate_ws_config(cfg) -> None`。下游 Task 3-5 消费。

- [ ] **Step 1: 写失败测试** — 新建 `packages/web/tests/test_ws_config_store.py`

```python
"""P3c 阶段 2：WsConfigStore 读写 + 凭据加密 + resolve 覆盖。"""
import pytest
from pathlib import Path

from supernova_web.components.credential_vault import CredentialVault
from supernova_web.components.ws_config_store import (
    WsConfig, WsProviderFields, WsConfigStore, validate_ws_config,
)


@pytest.fixture
def store(tmp_path):
    vault = CredentialVault(tmp_path / ".master_key")
    return WsConfigStore(tmp_path, vault)


def test_read_missing_ws_returns_empty(store, tmp_path):
    """无 config.yaml → 空 WsConfig（全 None）。"""
    (tmp_path / "ws-a").mkdir()
    cfg = store.read("ws-a")
    assert cfg.provider.ai_provider is None
    assert cfg.provider.api_key is None


def test_write_then_read_roundtrip(store, tmp_path):
    """写 → 读往返；凭据密文落盘但读回明文。"""
    (tmp_path / "ws-a").mkdir()
    store.write("ws-a", WsConfig(provider=WsProviderFields(
        ai_provider="openai_compatible", api_key="sk-secret", base_url="http://x", model="m",
    )))
    # 落盘密文（cat 不见明文）
    raw = (tmp_path / "ws-a" / "config.yaml").read_text()
    assert "sk-secret" not in raw
    # 读回明文
    cfg = store.read("ws-a")
    assert cfg.provider.api_key == "sk-secret"
    assert cfg.provider.ai_provider == "openai_compatible"


def test_resolve_provider_config_global_default_when_unset(store, tmp_path):
    """未填字段 → 回落全局默认（build_provider_config）。"""
    (tmp_path / "ws-a").mkdir()
    pc = store.resolve_provider_config("ws-a")
    assert pc["type"] in ("anthropic_api", "openai_compatible", "bedrock", "vertex", "litellm_router")
    assert "api_key" in pc  # 全局默认有此键


def test_resolve_provider_config_ws_overrides(store, tmp_path):
    """ws 显式字段覆盖全局默认（ai_provider → type 映射）。"""
    (tmp_path / "ws-a").mkdir()
    store.write("ws-a", WsConfig(provider=WsProviderFields(
        ai_provider="openai_compatible", api_key="sk-ws", max_turns=999,
    )))
    pc = store.resolve_provider_config("ws-a")
    assert pc["type"] == "openai_compatible"   # ai_provider → type
    assert pc["api_key"] == "sk-ws"
    assert pc["max_turns"] == 999


def test_path_traversal_rejected(store):
    with pytest.raises(ValueError):
        store.read("../etc/passwd")
    with pytest.raises(ValueError):
        store.write("..", WsConfig())


def test_validate_ws_config_unknown_provider():
    with pytest.raises(ValueError):
        validate_ws_config(WsConfig(provider=WsProviderFields(ai_provider="bogus")))


def test_validate_ws_config_none_provider_ok():
    """未覆盖 ai_provider → 不校验（回落全局）。"""
    validate_ws_config(WsConfig(provider=WsProviderFields()))  # 不抛


def test_validate_ws_config_known_provider_ok():
    validate_ws_config(WsConfig(provider=WsProviderFields(ai_provider="openai_compatible")))
```

- [ ] **Step 2: 跑测试确认失败** — `cd packages/web && uv run pytest tests/test_ws_config_store.py -v`
  - 预期：FAIL（模块不存在）

- [ ] **Step 3: 实现 ws_config_store.py** — 新建 `packages/web/src/supernova_web/components/ws_config_store.py`

```python
"""P3c 阶段 2：per-workspace provider 配置存储。

字段级配置存 workspaces/<ws>/config.yaml；凭据字段经 CredentialVault 加密。
resolve_provider_config(ws) = 全局默认(build_provider_config) + ws 非 None 字段覆盖。
路径穿越双防线：_validate_ws_segment + resolve().is_relative_to。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, is_dataclass
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
class WsConfig:
    provider: WsProviderFields = field(default_factory=WsProviderFields)


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
        known = {f.name for f in fields(WsProviderFields)}
        prov_kwargs = {k: prov_raw.get(k) for k in known}
        return WsConfig(provider=WsProviderFields(**prov_kwargs))

    def write(self, ws: str, cfg: WsConfig) -> None:
        validate_ws_config(cfg)
        path = self._config_path(ws)
        path.parent.mkdir(parents=True, exist_ok=True)
        prov = asdict(cfg.provider)
        # 凭据字段加密（仅非 None）
        if prov.get("api_key") is not None:
            prov["api_key"] = self._vault.encrypt(prov["api_key"])
        data = {"provider": prov}
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
```

- [ ] **Step 4: 跑测试确认通过** — `cd packages/web && uv run pytest tests/test_ws_config_store.py -v`
  - 预期：8 PASS

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/supernova_web/components/ws_config_store.py \
        packages/web/tests/test_ws_config_store.py
git commit -m "feat(web): P3c 阶段2 WsConfigStore per-ws 配置存储

WsConfig/WsProviderFields dataclass + WsConfigStore(read/write/resolve_provider_config)：
config.yaml 字段级 + 凭据 Fernet 密文 + 全局默认/ws 覆盖拼 provider_config dict。
路径穿越双防线(_validate_ws_segment + is_relative_to)。validate_ws_config 校验
ai_provider 合法性。ai_provider→type 映射。"
```

---

## Task 3: WebConfig master_key_file + app.py 装配

**Files:**
- Modify: `packages/web/src/supernova_web/config.py:31-36`（加 `master_key_file` 属性）
- Modify: `packages/web/src/supernova_web/app.py:225-247`（装配 vault/store + scan_manager 注入 + 注册路由）
- Modify: `packages/web/src/supernova_web/components/scan_manager.py:46-57`（`__init__` 加 `ws_config_store` 形参）——本 task 仅加形参（默认 None 兼容），接入逻辑在 Task 5
- Test: `packages/web/tests/test_app_ws_config_wiring.py`

**Interfaces:**
- Consumes: Task 1/2 的 `CredentialVault`/`WsConfigStore`
- Produces: `app.state.credential_vault` / `app.state.ws_config_store`；`ScanManager(..., ws_config_store=)` 形参。

- [ ] **Step 1: WebConfig 加 master_key_file** — 编辑 `packages/web/src/supernova_web/config.py`，在 `workspaces_dir` property（:33-36）后加：

```python
    @property
    def master_key_file(self) -> "Path":
        """P3c 阶段 2：凭据 master key 落盘路径（env SUPERNOVA_MASTER_KEY 优先于该文件）。"""
        return self.workspaces_dir / ".master_key"
```

- [ ] **Step 2: ScanManager.__init__ 加形参** — 编辑 `packages/web/src/supernova_web/components/scan_manager.py:46-57`，构造函数末尾加：

```python
        # P3c 阶段 2：per-ws 配置解析（None=CLI/旧测试兜底，走全局 env）
        self._ws_config_store = ws_config_store
```

（在 `__init__` 签名加 `ws_config_store=None` 形参，赋给 `self._ws_config_store`。本 task 不动 `_submit_whitebox` 的构造逻辑——Task 5 接入。）

- [ ] **Step 3: app.py 装配** — 编辑 `packages/web/src/supernova_web/app.py`

  3a. import（:225 附近 `from .api import ...`）追加 `ws_config`；并在组件 import 区加：
```python
from .components.credential_vault import CredentialVault
from .components.ws_config_store import WsConfigStore
from .api import ws_config
```

  3b. 在 `app.state.config_store = MultiRepoConfigStore(...)`（:228）**之前**插：
```python
        # P3c 阶段 2：per-ws 配置
        app.state.credential_vault = CredentialVault(cfg.master_key_file)
        app.state.ws_config_store = WsConfigStore(cfg.workspaces_dir, app.state.credential_vault)
```

  3c. `ScanManager(...)` 构造（:231-233）实参追加 `ws_config_store=app.state.ws_config_store`。

  3d. `:242` 后插路由注册：
```python
        app.include_router(ws_config.router, dependencies=_require_auth)
```

- [ ] **Step 4: 写装配测试** — 新建 `packages/web/tests/test_app_ws_config_wiring.py`

```python
"""P3c 阶段 2：app 装配 vault/store + 路由注册。"""
from supernova_web.components.credential_vault import CredentialVault
from supernova_web.components.ws_config_store import WsConfigStore


def test_app_wires_credential_vault(app_with_ws):
    assert isinstance(app_with_ws.state.credential_vault, CredentialVault)


def test_app_wires_ws_config_store(app_with_ws):
    assert isinstance(app_with_ws.state.ws_config_store, WsConfigStore)


def test_app_registers_ws_config_routes(app_with_ws):
    """GET/PUT /api/workspaces/{ws}/config 路由已注册。"""
    paths = {r.path for r in app_with_ws.routes}
    assert "/api/workspaces/{ws}/config" in paths
```

  > 注：`app_with_ws` fixture 以现有 `tests/conftest.py` 为准；若该 fixture 未装配 vault/store，需在 conftest 更新 `create_app` 调用链（装配是 `create_app` 内，应自动随 Task 3 生效）。

- [ ] **Step 5: 跑测试确认通过** — `cd packages/web && uv run pytest tests/test_app_ws_config_wiring.py -v`
  - 预期：3 PASS。若 `app_with_ws` fixture 因新装配失败，按报错补 conftest。

- [ ] **Step 6: 跑现有 app/config 回归** — `cd packages/web && uv run pytest tests/test_app_system_status.py tests/test_frontend_serving.py -v`
  - 预期：全 PASS

- [ ] **Step 7: Commit**

```bash
git add packages/web/src/supernova_web/config.py \
        packages/web/src/supernova_web/app.py \
        packages/web/src/supernova_web/components/scan_manager.py \
        packages/web/tests/test_app_ws_config_wiring.py
git commit -m "feat(web): P3c 阶段2 装配 CredentialVault/WsConfigStore + ws_config 路由

WebConfig.master_key_file 属性；app.state 装配 vault/store；ScanManager 收
ws_config_store 形参(None 兜底)；注册 ws_config.router。scan_manager 接入(Task 5)。"
```

---

## Task 4: ws_config API（GET 脱敏 / PUT 写入）

**Files:**
- Create: `packages/web/src/supernova_web/api/ws_config.py`
- Test: `packages/web/tests/test_api_ws_config.py`

**Interfaces:**
- Consumes: Task 3 的 `app.state.ws_config_store`；`auth.dependencies.workspace_member`/`workspace_manager`
- Produces: `GET /api/workspaces/{ws}/config`（脱敏）+ `PUT /api/workspaces/{ws}/config`（写入）。

- [ ] **Step 1: 写失败测试** — 新建 `packages/web/tests/test_api_ws_config.py`

```python
"""P3c 阶段 2：ws config API（GET 脱敏 / PUT 写入 + 鉴权）。"""
import pytest


def test_get_config_empty_ws(authed_client, tmp_workspaces):
    """空 ws → 全 None（脱敏 api_key=None）。"""
    (tmp_workspaces / "ws-a").mkdir()
    r = authed_client.get("/api/workspaces/ws-a/config")
    assert r.status_code == 200
    prov = r.json()["provider"]
    assert prov["api_key"] is None
    assert prov["ai_provider"] is None


def test_put_then_get_masks_api_key(authed_client, tmp_workspaces):
    """写入 api_key → GET 返 '••••'（脱敏，非明文）。"""
    (tmp_workspaces / "ws-a").mkdir()
    r = authed_client.put("/api/workspaces/ws-a/config", json={
        "provider": {"ai_provider": "openai_compatible", "api_key": "sk-secret", "base_url": "http://x"}
    })
    assert r.status_code == 200
    g = authed_client.get("/api/workspaces/ws-a/config").json()["provider"]
    assert g["api_key"] == "••••"        # 脱敏
    assert g["ai_provider"] == "openai_compatible"
    assert g["base_url"] == "http://x"


def test_put_empty_api_key_keeps_existing(authed_client, tmp_workspaces):
    """api_key 空串/缺省 = 不改（保留原值）。"""
    (tmp_workspaces / "ws-a").mkdir()
    authed_client.put("/api/workspaces/ws-a/config", json={"provider": {"api_key": "sk-orig"}})
    authed_client.put("/api/workspaces/ws-a/config", json={"provider": {"api_key": "", "model": "m"}})
    # 再读确认 api_key 仍配置（••••）
    g = authed_client.get("/api/workspaces/ws-a/config").json()["provider"]
    assert g["api_key"] == "••••"
    assert g["model"] == "m"


def test_put_unknown_provider_422(authed_client, tmp_workspaces):
    (tmp_workspaces / "ws-a").mkdir()
    r = authed_client.put("/api/workspaces/ws-a/config", json={"provider": {"ai_provider": "bogus"}})
    assert r.status_code == 422


def test_get_non_member_403(...):
    """非成员 GET → 403（按现有 members 测试的 multi-user fixture 模式）。"""
    # 用现有 test_members 多用户 fixture（alice/bob/admin）；bob 非 ws-a 成员 → 403
    ...


def test_put_non_manager_403(...):
    """member（非 manager）PUT → 403。"""
    ...
```

  > 注：`authed_client` / `tmp_workspaces` / 多用户 fixture 以现有 `tests/conftest.py` + `test_api_members`（P1）模式为准。`authed_client` 默认登录身份（admin/manager/member）以现有 fixture 为准，调整以覆盖鉴权用例。

- [ ] **Step 2: 跑测试确认失败** — `cd packages/web && uv run pytest tests/test_api_ws_config.py -v`
  - 预期：FAIL（路由不存在 → 404）

- [ ] **Step 3: 实现 ws_config API** — 新建 `packages/web/src/supernova_web/api/ws_config.py`

```python
"""P3c 阶段 2：per-workspace provider 配置 API。

GET  /api/workspaces/{ws}/config — 读配置（api_key 脱敏）— workspace_member
PUT  /api/workspaces/{ws}/config — 写配置 — workspace_manager（admin 直通）

PUT api_key 语义：空串/缺省=不改（保留原值），非空=更新。
"""
from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..auth.dependencies import workspace_member, workspace_manager
from ..components.ws_config_store import WsConfig, WsProviderFields, WsConfigStore

router = APIRouter(prefix="/api/workspaces", tags=["ws-config"])

MASKED = "••••"


class WsProviderFieldsIn(BaseModel):
    ai_provider: Optional[str] = None
    api_key: Optional[str] = None       # "" = 不改, 非空 = 更新
    base_url: Optional[str] = None
    model: Optional[str] = None
    small_model: Optional[str] = None
    medium_model: Optional[str] = None
    large_model: Optional[str] = None
    max_turns: Optional[int] = None
    adaptive_thinking: Optional[bool] = None


class WsConfigIn(BaseModel):
    provider: WsProviderFieldsIn


def _store(request: Request) -> WsConfigStore:
    return request.app.state.ws_config_store


@router.get("/{ws}/config")
async def get_ws_config(ws: str, request: Request, user=Depends(workspace_member)):
    cfg = _store(request).read(ws)
    p = cfg.provider
    return {"provider": {
        "ai_provider": p.ai_provider,
        "api_key": MASKED if p.api_key else None,
        "base_url": p.base_url,
        "model": p.model,
        "small_model": p.small_model,
        "medium_model": p.medium_model,
        "large_model": p.large_model,
        "max_turns": p.max_turns,
        "adaptive_thinking": p.adaptive_thinking,
    }}


@router.put("/{ws}/config")
async def put_ws_config(ws: str, body: WsConfigIn, request: Request,
                        user=Depends(workspace_manager)):
    store = _store(request)
    existing = store.read(ws).provider
    # api_key 空串/None = 保留原值；非空 = 更新
    new_api_key = body.provider.api_key if body.provider.api_key else existing.api_key
    cfg = WsConfig(provider=WsProviderFields(
        ai_provider=body.provider.ai_provider,
        api_key=new_api_key,
        base_url=body.provider.base_url,
        model=body.provider.model,
        small_model=body.provider.small_model,
        medium_model=body.provider.medium_model,
        large_model=body.provider.large_model,
        max_turns=body.provider.max_turns,
        adaptive_thinking=body.provider.adaptive_thinking,
    ))
    try:
        store.write(ws, cfg)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True}
```

- [ ] **Step 4: 跑测试确认通过** — `cd packages/web && uv run pytest tests/test_api_ws_config.py -v`
  - 预期：全 PASS（含鉴权用例）

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/supernova_web/api/ws_config.py \
        packages/web/tests/test_api_ws_config.py
git commit -m "feat(web): P3c 阶段2 ws config API (GET 脱敏/PUT 写入)

GET /api/workspaces/{ws}/config (workspace_member, api_key→••••脱敏)；
PUT (workspace_manager, api_key 空串=不改/非空=更新, validate→422)。仿 members.py。"
```

---

## Task 5: scan_manager 按 ws 解析接入

**Files:**
- Modify: `packages/web/src/supernova_web/components/scan_manager.py:121-143`（`_submit_whitebox`）——**阶段 1 已塞全局 provider_config**，本 task 改为 per-ws 解析
- Test: `packages/web/tests/test_scan_manager_ws_config.py`

**Interfaces:**
- Consumes: Task 3 的 `self._ws_config_store`；阶段 1 的 `PipelineInput.provider_config` 字段
- Produces: `_submit_whitebox` 用 `ws_config_store.resolve_provider_config(ws)` + 提交前 `validate_ws_config` fail-fast。

- [ ] **Step 1: 写失败测试** — 新建 `packages/web/tests/test_scan_manager_ws_config.py`

```python
"""P3c 阶段 2：scan_manager 按 ws 解析配置（替代阶段 1 全局构造）+ fail-fast。"""
import pytest


async def test_submit_uses_ws_config(app_with_ws, monkeypatch):
    """ws 配置的 ai_provider → 提交的 PipelineInput.provider_config['type']。"""
    from supernova_web.components.ws_config_store import WsConfig, WsProviderFields
    app_with_ws.state.ws_config_store.write("ws-a", WsConfig(provider=WsProviderFields(
        ai_provider="openai_compatible", api_key="sk-ws")))
    captured = {}
    # mock client.start_workflow 捕获 PipelineInput（按现有 test_api_scan mock 模式）
    ...  # 参照 test_scan_manager_provider_config.py（阶段1）的 mock 模式
    # 触发 sm.start(whitebox, ws-a)
    inp = captured["inp"]
    assert inp.provider_config["type"] == "openai_compatible"
    assert inp.provider_config["api_key"] == "sk-ws"


async def test_submit_fails_fast_on_invalid_ws_config(app_with_ws, ...):
    """ws 选了非法 ai_provider → validate_ws_config 抛 → 不提交。"""
    app_with_ws.state.ws_config_store.write("ws-a", WsConfig(provider=WsProviderFields(
        ai_provider="bogus")))
    with pytest.raises(Exception):  # validate_ws_config → ValueError 上抛
        await app_with_ws.state.scan_manager.start(...)


async def test_submit_falls_back_when_ws_config_store_none(...):
    """ws_config_store=None（CLI/旧测试）→ 全局 env 构造（阶段1 兜底，行为不变）。"""
    ...
```

  > 注：mock `Client.connect` + `start_workflow` 捕获 `inp`，模式参照阶段 1 的 `test_scan_manager_provider_config.py`。`sm.start` 的 ScanRequest 构造参照现有 `test_api_scan.py`。

- [ ] **Step 2: 跑测试确认失败** — `cd packages/web && uv run pytest tests/test_scan_manager_ws_config.py -v`
  - 预期：FAIL（仍用全局构造）

- [ ] **Step 3: `_submit_whitebox` 改 per-ws 解析** — 编辑 `packages/web/src/supernova_web/components/scan_manager.py:121-143`

```python
    async def _submit_whitebox(self, target: str | None, ws: str,
                               event_file: Path, req: ScanRequest) -> Any:
        """...（docstring 不变）..."""
        client = await Client.connect(self._temporal_address())
        workflow_id = self._resolve_workflow_id(ws)
        provider_config = self._resolve_provider_config(ws)
        inp = PipelineInput(
            repo_path=target or "",
            web_url=req.url or "",
            workspace_name=ws,
            event_file=str(event_file),
            provider_config=provider_config,
        )
        handle = await client.start_workflow(
            WhiteboxScanWorkflow.run, inp, id=workflow_id,
            task_queue=WEB_TASK_QUEUE_WHITEBOX,
        )
        self._mark_submitted_at(self._workspaces_dir / ws)
        return handle

    def _resolve_provider_config(self, ws: str) -> dict:
        """P3c 阶段 2：per-ws 解析（ws_config_store）；None → 全局 env 兜底（阶段1/CLI）。"""
        if self._ws_config_store is not None:
            from .ws_config_store import validate_ws_config
            validate_ws_config(self._ws_config_store.read(ws))   # 提交前 fail-fast
            return self._ws_config_store.resolve_provider_config(ws)
        from dataclasses import asdict
        from supernova_core.agents.providers import build_provider_config
        return asdict(build_provider_config())
```

  > 注：阶段 1 plan 在 `_submit_whitebox` 内联了 `asdict(build_provider_config())`；本 task 把它提取为 `_resolve_provider_config` 方法并改 per-ws。若阶段 1 实现与内联位置有差异，调整提取点，保持行为（None 兜底走全局）。

- [ ] **Step 4: 跑测试确认通过** — `cd packages/web && uv run pytest tests/test_scan_manager_ws_config.py tests/test_scan_manager_provider_config.py -v`
  - 预期：全 PASS（阶段 1 的全局构造测试在 `ws_config_store=None` 时仍走兜底，绿）

- [ ] **Step 5: 跑 scan 回归** — `cd packages/web && uv run pytest tests/test_api_scan.py tests/test_scan_resolves_repo_in_ws.py -v`
  - 预期：全 PASS

- [ ] **Step 6: Commit**

```bash
git add packages/web/src/supernova_web/components/scan_manager.py \
        packages/web/tests/test_scan_manager_ws_config.py
git commit -m "feat(web): P3c 阶段2 scan_manager 按 ws 解析 provider 配置

_resolve_provider_config(ws): ws_config_store 非None → resolve_provider_config(ws)
+ validate_ws_config fail-fast；None → 全局 env 兜底(阶段1/CLI)。
替代阶段1 的全局 asdict(build_provider_config())。"
```

---

## Task 6: 前端 ws 配置页（apiPut + client + WsSettingsTab + router + i18n）

**Files:**
- Modify: `packages/web/frontend/src/api/client.ts:60`（加 `apiPut`）
- Create: `packages/web/frontend/src/api/wsConfig.ts`
- Create: `packages/web/frontend/src/routes/WorkspaceDetail/WsSettingsTab.tsx`
- Modify: `packages/web/frontend/src/router.tsx:58`（子路由加 settings）
- Modify: `packages/web/frontend/src/routes/WorkspaceDetail/index.tsx:77-92`（header 齿轮入口）
- Modify: `packages/web/frontend/src/pages/SettingsPage.tsx:71-76`（perWsHint Card）
- Modify: `packages/web/frontend/src/locales/{zh,en}.json`（新建 `wsConfig` namespace + `settings.perWsHint`）
- Test: `packages/web/frontend/src/routes/WorkspaceDetail/__tests__/WsSettingsTab.test.tsx`

**Interfaces:**
- Consumes: Task 4 的 GET/PUT API；现有 `useAuth`（权限判定）/ `useParams`（ws）/ shadcn ui 组件
- Produces: `/p/:workspace/settings` 页面（admin/manager 可编辑，member 只读；表单 + 凭据脱敏 + 保存 toast）。

- [ ] **Step 1: 加 apiPut** — 编辑 `packages/web/frontend/src/api/client.ts:60` 后：

```ts
export const apiPut = <T>(path: string, body: unknown, opts?: ReqOptions) =>
  request<T>(path, { method: "PUT", body: JSON.stringify(body) }, opts);
```

- [ ] **Step 2: 新建 api/wsConfig.ts** — 参考 `api/members.ts`：

```ts
import { apiGet, apiPut } from "./client";

export interface WsProviderFields {
  ai_provider: string | null;
  api_key: string | null;       // GET 返 "••••" 或 null
  base_url: string | null;
  model: string | null;
  small_model: string | null;
  medium_model: string | null;
  large_model: string | null;
  max_turns: number | null;
  adaptive_thinking: boolean | null;
}
export interface WsConfig { provider: WsProviderFields; }
export type WsConfigInput = { provider: Partial<WsProviderFields> };

export const getWsConfig = (ws: string) =>
  apiGet<WsConfig>(`/workspaces/${encodeURIComponent(ws)}/config`);
export const putWsConfig = (ws: string, body: WsConfigInput) =>
  apiPut(`/workspaces/${encodeURIComponent(ws)}/config`, body);
```

- [ ] **Step 3: i18n** — 编辑 `packages/web/frontend/src/locales/zh.json`（`"workspace": {...}` 后，约 :217）加 `wsConfig` namespace；`settings` 加 `perWsHint`：

```json
    "wsConfig": {
      "title": "工作区配置",
      "subtitle": "覆盖全局 provider 配置；留空字段回落全局默认。",
      "fields": {
        "aiProvider": "AI Provider",
        "apiKey": "API Key",
        "baseUrl": "Base URL",
        "model": "模型",
        "smallModel": "小模型",
        "mediumModel": "中模型",
        "largeModel": "大模型",
        "maxTurns": "最大轮数",
        "adaptiveThinking": "自适应思考"
      },
      "apiKey": { "configured": "已配置（••••），留空不改", "notConfigured": "未配置" },
      "save": "保存",
      "saved": "配置已保存",
      "errors": { "forbidden": "无权修改配置", "saveFailed": "保存失败", "invalid": "配置不合法" },
      "fallbackHint": "留空 = 回落全局默认",
      "openSettings": "工作区配置"
    }
```

  `en.json` 同位置加英文版（`title: "Workspace Config"` 等，**值要真翻译**，勿漏成英文 key——对齐 i18n 陷阱 memory）。`settings` 加 `"perWsHint": "per-workspace 配置在各工作区的「配置」页编辑（ws 详情 → 配置齿轮）。"`（en 同）。

- [ ] **Step 4: 新建 WsSettingsTab.tsx** — 参考 `AddRepoDialog.tsx` 表单 + `MemberManagerDialog.tsx` 权限判定 + `SettingsPage.tsx` Card 布局：

```tsx
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
import { getWsConfig, putWsConfig, type WsProviderFields } from "@/api/wsConfig";
import { ApiError } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { useToast } from "@/components/ui/toaster";

const EMPTY: WsProviderFields = {
  ai_provider: null, api_key: null, base_url: null, model: null,
  small_model: null, medium_model: null, large_model: null,
  max_turns: null, adaptive_thinking: null,
};

export function WsSettingsTab() {
  const { workspace: ws = "" } = useParams<{ workspace: string }>();
  const { t } = useTranslation();
  const { user } = useAuth();
  const { toast } = useToast();
  const canEdit = user?.role === "admin" || user?.role === "manager";

  const [cfg, setCfg] = useState<WsProviderFields>(EMPTY);
  const [apiKeyInput, setApiKeyInput] = useState("");   // password 框，空=不改
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    getWsConfig(ws).then((r) => { setCfg(r.provider); setLoaded(true); })
      .catch(() => setLoaded(true));
  }, [ws]);

  async function onSave() {
    setBusy(true);
    try {
      await putWsConfig(ws, {
        provider: {
          ...cfg,
          api_key: apiKeyInput || undefined,   // 空=不改（undefined 不发，后端保原值）
        },
      });
      setApiKeyInput("");
      const fresh = await getWsConfig(ws);
      setCfg(fresh.provider);
      toast({ title: t("wsConfig.saved") });
    } catch (e) {
      const status = e instanceof ApiError ? e.status : 0;
      toast({ title: t(`wsConfig.errors.${status === 403 ? "forbidden" : status === 422 ? "invalid" : "saveFailed"}`), variant: "destructive" });
    } finally {
      setBusy(false);
    }
  }

  if (!loaded) return null;
  return (
    <Card>
      <CardHeader><CardTitle>{t("wsConfig.title")}</CardTitle></CardHeader>
      <CardContent className="space-y-4 max-w-xl">
        <p className="text-sm text-muted-foreground">{t("wsConfig.subtitle")}</p>
        <div className="space-y-2">
          <Label>{t("wsConfig.fields.aiProvider")}</Label>
          <Select value={cfg.ai_provider ?? "__unset__"} disabled={!canEdit}
                  onValueChange={(v) => setCfg({ ...cfg, ai_provider: v === "__unset__" ? null : v })}>
            <SelectTrigger><SelectValue placeholder={t("wsConfig.fallbackHint")} /></SelectTrigger>
            <SelectContent>
              <SelectItem value="__unset__">{t("wsConfig.fallbackHint")}</SelectItem>
              <SelectItem value="anthropic_api">anthropic_api</SelectItem>
              <SelectItem value="openai_compatible">openai_compatible</SelectItem>
              <SelectItem value="bedrock">bedrock</SelectItem>
              <SelectItem value="vertex">vertex</SelectItem>
              <SelectItem value="litellm_router">litellm_router</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-2">
          <Label>{t("wsConfig.fields.apiKey")}</Label>
          <Input type="password" value={apiKeyInput} disabled={!canEdit}
                 placeholder={cfg.api_key ? t("wsConfig.apiKey.configured") : t("wsConfig.apiKey.notConfigured")}
                 onChange={(e) => setApiKeyInput(e.target.value)} />
        </div>
        {(["base_url", "model", "small_model", "medium_model", "large_model"] as const).map((f) => (
          <div className="space-y-2" key={f}>
            <Label>{t(`wsConfig.fields.${f === "base_url" ? "baseUrl" : f === "small_model" ? "smallModel" : f === "medium_model" ? "mediumModel" : f === "large_model" ? "largeModel" : "model"}`)}</Label>
            <Input value={cfg[f] ?? ""} disabled={!canEdit}
                   onChange={(e) => setCfg({ ...cfg, [f]: e.target.value || null })} />
          </div>
        ))}
        <div className="space-y-2">
          <Label>{t("wsConfig.fields.maxTurns")}</Label>
          <Input type="number" value={cfg.max_turns ?? ""} disabled={!canEdit}
                 onChange={(e) => setCfg({ ...cfg, max_turns: e.target.value ? Number(e.target.value) : null })} />
        </div>
        <div className="flex items-center gap-2">
          <Switch checked={cfg.adaptive_thinking ?? false} disabled={!canEdit}
                  onCheckedChange={(v) => setCfg({ ...cfg, adaptive_thinking: v })} />
          <Label>{t("wsConfig.fields.adaptiveThinking")}</Label>
        </div>
        {canEdit && (
          <Button onClick={onSave} disabled={busy}>{t("wsConfig.save")}</Button>
        )}
      </CardContent>
    </Card>
  );
}
```

  > 注：`useToast` / `Card` / `Select` / `Switch` 等以现有 `components/ui/` + 项目 toast 模式为准；`authed_client`/`useAuth().user.role` 字段名以 P0 auth 实现为准。

- [ ] **Step 5: router + header 入口** — 编辑 `packages/web/frontend/src/router.tsx:58` 子路由数组加：
```tsx
  { path: "settings", element: <WsSettingsTab /> },
```
  （import `WsSettingsTab`）。编辑 `routes/WorkspaceDetail/index.tsx:77-92` header 操作区加齿轮按钮（Link 到 `./settings`，仅 admin/manager 可见，参考 `MemberManagerDialog` 的条件渲染）。

- [ ] **Step 6: SettingsPage perWsHint** — 编辑 `pages/SettingsPage.tsx:71-76` 后加一个 Card 显示 `t("settings.perWsHint")`。

- [ ] **Step 7: 写组件测试** — 新建 `packages/web/frontend/src/routes/WorkspaceDetail/__tests__/WsSettingsTab.test.tsx`，参考现有组件测试（MSW mock GET/PUT + render + 交互）。核心断言：
  - GET 返 `api_key: "••••"` → 输入框 placeholder 显示「已配置」
  - 填表单 + 点保存 → PUT 调用 body 含字段
  - member（非 manager）→ 保存按钮不显示（只读）

- [ ] **Step 8: 跑前端测试 + tsc + build** — `cd packages/web/frontend && npx vitest run src/routes/WorkspaceDetail && npx tsc -p . --noEmit && npm run build`（按现有 frontend 测试命令，**必须 cd frontend**——对齐 memory 陷阱）
  - 预期：测试绿 + tsc 0 error + build 成功

- [ ] **Step 9: Commit**

```bash
git add packages/web/frontend/src/api/client.ts \
        packages/web/frontend/src/api/wsConfig.ts \
        packages/web/frontend/src/routes/WorkspaceDetail/WsSettingsTab.tsx \
        packages/web/frontend/src/routes/WorkspaceDetail/__tests__/WsSettingsTab.test.tsx \
        packages/web/frontend/src/router.tsx \
        packages/web/frontend/src/routes/WorkspaceDetail/index.tsx \
        packages/web/frontend/src/pages/SettingsPage.tsx \
        packages/web/frontend/src/locales/zh.json \
        packages/web/frontend/src/locales/en.json
git commit -m "feat(web/frontend): P3c 阶段2 ws 配置页 UI

apiPut + api/wsConfig.ts；WsSettingsTab（/p/:workspace/settings 子路由，非 tab，
header 齿轮入口）：字段表单 + api_key 脱敏占位 + manager/admin 可编辑/member 只读 +
保存 toast。SettingsPage 加 perWsHint Card。i18n wsConfig namespace (zh/en)。"
```

---

## Task 7: 回归 + 端到端不变量

**Files:**
- Test: `packages/web/tests/test_ws_config_e2e.py`（新建，集成 ws 配置 → scan 解析）

**Interfaces:**
- Consumes: Task 1-6 全部
- Produces: 端到端不变量——ws 配置经 `PUT → config.yaml(密文) → scan_manager.resolve_provider_config → PipelineInput.provider_config → ProviderConfig` 全链生效；未配 ws 回落全局；凭据密文落盘。

- [ ] **Step 1: 写端到端测试** — 新建 `packages/web/tests/test_ws_config_e2e.py`

```python
"""P3c 阶段 2 端到端：ws 配置 → scan 解析 → PipelineInput.provider_config。"""


def test_ws_config_flows_to_pipeline_input(app_with_ws, tmp_workspaces, monkeypatch):
    """PUT 写 ws 配置 → scan_manager 提交的 PipelineInput.provider_config 用 ws 配置。"""
    (tmp_workspaces / "ws-a").mkdir()
    # PUT 写配置
    app_with_ws.test_client().put("/api/workspaces/ws-a/config", json={
        "provider": {"ai_provider": "openai_compatible", "api_key": "sk-e2e", "max_turns": 555}
    }, headers=auth_headers)
    # mock start_workflow 捕获 inp
    captured = {}
    monkeypatch.setattr(..., "start_workflow", lambda fn, inp, **k: captured.__setitem__("inp", inp))
    # 触发 scan
    app_with_ws.state.scan_manager.start(whitebox_req_ws_a)
    inp = captured["inp"]
    assert inp.provider_config["type"] == "openai_compatible"
    assert inp.provider_config["api_key"] == "sk-e2e"
    assert inp.provider_config["max_turns"] == 555


def test_unconfigured_ws_falls_back_to_global(app_with_ws, tmp_workspaces, monkeypatch):
    """未配 ws → PipelineInput.provider_config = 全局默认（build_provider_config）。"""
    (tmp_workspaces / "ws-b").mkdir()
    ...  # 同上，断言 provider_config["type"] 在合法 provider 集合
```

  > 注：完整 e2e 需 auth_headers + whitebox req 构造，参照现有 `test_api_scan.py` 集成测试模式；mock `start_workflow` 捕获 inp。

- [ ] **Step 2: 跑 e2e + 全包回归** —
  - `cd packages/web && uv run pytest tests/test_ws_config_e2e.py tests/test_credential_vault.py tests/test_ws_config_store.py tests/test_api_ws_config.py tests/test_scan_manager_ws_config.py -v`
  - 相关回归：`uv run pytest tests/test_api_scan.py tests/test_api_members.py tests/test_scan_resolves_repo_in_ws.py -v`
  - 预期：全 PASS

- [ ] **Step 3: 人工核验凭据密文落盘** — 测试后 `cat workspaces/<ws>/config.yaml` 不见明文 api_key（仅 `api_key: gAAAA...`）。

- [ ] **Step 4: Commit**

```bash
git add packages/web/tests/test_ws_config_e2e.py
git commit -m "test(web): P3c 阶段2 ws config 端到端 + 回归

PUT→config.yaml(密文)→resolve_provider_config→PipelineInput.provider_config 全链；
未配 ws 回落全局。阶段 2 完成：per-ws 配置隔离（并发仍=1，阶段3 解锁）。"
```

---

## Self-Review（plan 作者自检）

**1. Spec 覆盖**：spec §7.2（config.yaml + master key）→ Task 1-3；§7.3（CredentialVault/WsConfigStore/resolve）→ Task 1-2；§7.4（scan_manager 接入）→ Task 5；§7.5（admin API GET 脱敏/PUT 鉴权）→ Task 4；§7.6（per-ws 校验 fail-fast）→ Task 2（validate）+ Task 5（提交前）；§7.7（前端 ws 配置页 + SettingsPage hint）→ Task 6；§7.8（验收）→ Task 7。

**2. 占位符扫描**：少数测试用例（e2e 的 auth_headers/req 构造、鉴权用例的多用户 fixture）标注"参照现有 X 模式"——这是对现有 fixture 的复用指引（P1 test_api_members 已有 alice/bob/admin 多用户 fixture），非占位。核心代码（CredentialVault/WsConfigStore/ws_config API/WsSettingsTab）完整。

**3. 类型/键一致性**：`WsProviderFields.ai_provider` ↔ `ProviderConfig.type`（resolve 经 `_PROV_FIELD_TO_PC_KEY` 映射）——Task 2 定义、Task 5/7 验证；凭据白名单 `api_key`——Task 1（CREDENTIAL_FIELDS）+ Task 2（write 加密 read 解密）一致；脱敏 `"••••"`——Task 4（GET 返）+ Task 6（前端 placeholder 判定）一致。

**4. 阶段 1 依赖一致**：Global Constraints + Task 5 说明「阶段 1 已塞全局 provider_config，Task 5 改 per-ws」+ `_resolve_provider_config` 的 None 兜底分支（保 CLI/旧测试绿）——三处一致。

**5. 鉴权一致**：PUT `workspace_manager`（admin 直通）+ GET `workspace_member`——Global Constraints + Task 4 + Task 6（前端 canEdit 判定）一致。

**6. i18n 陷阱**：Task 6 Step 3 明确「en.json 值要真翻译，勿漏成英文 key」——对齐 [[frontend-i18n-zh-value-not-translated]] memory。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-27-web-config-isolation-stage2.md`. Two execution options:

1. **Subagent-Driven（推荐）** — 每 task 派 fresh subagent + 两阶段 review。阶段 2 跨后端+前端+加密，task 间有依赖（1→2→3→4/5→6），适合按序 subagent。
2. **Inline Execution** — 本 session 批量 + 检查点。

Which approach?

---

**后续阶段**（本 plan 不含）：
- 阶段 3：并发解锁（AuditSession/LogBus/heartbeat contextvar + worker 放宽）——叠加阶段 2 后 = 各 ws 各自配置并发跑
- 阶段 4：clone 凭据 per-ws（git 段进 config.yaml + GitFetcher per-ws）
- Phase C（黑盒 web C1 化）：黑盒 ws_config 接入
