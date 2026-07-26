# P3c 阶段 4：clone 凭据 per-workspace 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每个 workspace 独立的 GitLab clone 凭据（`gitlab_user` + `gitlab_token`），不同 ws 用不同 GitLab 账号/实例 clone repo。ws config git 段优先，回落全局 `WebConfig.gitlab_user/gitlab_token`（行为不变）。凭据密文落盘（复用阶段 2 的 CredentialVault）。

**Architecture:** `WsConfig` 加 `git: WsGitFields`（`gitlab_user`/`gitlab_token`），`WsConfigStore.read/write` 扩展 git 凭据加解密（`CREDENTIAL_FIELDS` 加 `gitlab_token`）。`GitFetcher` 构造收 `ws_config_store`（可选），新增 `_creds_for(ws)` 解析（ws git 段优先 / 回落全局），`available(ws)` / `_inject_auth(url, ws)` / `fetch(..., ws)` 按 ws 取凭据。`RepoManager._clone_task`（P2 已有 ws 参数）把 ws 传给 GitFetcher。`app.py` 装配 `GitFetcher(..., ws_config_store=...)`。ws_config API GET/PUT 扩展 git 段（脱敏 `gitlab_token`）。前端 ws 配置页加 git 段表单。

**Tech Stack:** Python 3.11+ / dataclasses / FastAPI / pytest；React 18 / shadcn new-york / vitest。

## Global Constraints

- **前置依赖**：阶段 2（`WsConfig`/`CredentialVault`/`WsConfigStore`）**必须已实现**。本阶段扩展 `WsConfig` 加 git 段 + 复用 vault。**执行阶段 4 前先完成阶段 2**。
- **凭据回落语义**：ws config git 段字段非 None → 覆盖全局；None → 回落 `WebConfig.gitlab_user/gitlab_token`（env，行为不变）。允许只覆盖 user 或只覆盖 token（`_creds_for` 用 `or` 合并）。
- **凭据密文**：`gitlab_token` 进 `CREDENTIAL_FIELDS` 白名单，`WsConfigStore.write` 加密 / `read` 解密（复用阶段 2 vault）。`gitlab_user` 明文（非敏感）。
- **GitFetcher 兼容**：`ws_config_store` 可选（`None` = 全局兜底，CLI/旧测试兼容）；`available(ws=None)` / `_inject_auth(url, ws=None)` / `fetch(..., ws=None)`，`ws=None` 走全局（行为不变）。
- **GET 脱敏**：`gitlab_token` → `"••••"`（已配置）或 `None`；**PUT `gitlab_token` 语义**：空串/缺省 = 不改，非空 = 更新（同阶段 2 `api_key`）。
- **不动**：`strip_credentials`（凭据剥离）/ `redact`（日志脱敏）/ `repo_name`——这些逻辑不变。
- **RepoManager 已有 ws 维度**（P2）：`_clone_task` 收 ws，只需把 ws 传给 `self._git.available(ws)` / `_inject_auth(url, ws)` / `fetch(..., ws=ws)`。
- **不动 multi-configs**；黑盒 web 路径未接（Phase C）。
- **测试隔离**：`monkeypatch` + tmp_path；真实 git clone 测试用本地 bare repo fixture（若现有 repo_manager 测试已有）；按 CLAUDE.md 只跑改动相关测试。

---

## File Structure

| 文件 | 职责 | 本计划改动 |
|---|---|---|
| `packages/web/src/supernova_web/components/ws_config_store.py` | WsConfig + WsConfigStore | 加 `WsGitFields` + `WsConfig.git` + read/write 扩展 git 加解密（Task 1） |
| `packages/web/src/supernova_web/components/credential_vault.py` | 凭据白名单 | `CREDENTIAL_FIELDS` 加 `gitlab_token`（Task 1） |
| `packages/web/src/supernova_web/components/git_fetcher.py` | GitFetcher | 加 `ws_config_store` + `_creds_for(ws)` + `available(ws)`/`_inject_auth(url,ws)`/`fetch(...,ws)`（Task 2） |
| `packages/web/src/supernova_web/components/repo_manager.py:243/271` | clone 调用链 | `_clone_task` 传 ws 给 GitFetcher（Task 3） |
| `packages/web/src/supernova_web/app.py:234` | 装配 | `GitFetcher(..., ws_config_store=...)`（Task 3） |
| `packages/web/src/supernova_web/api/ws_config.py` | ws config API | GET/PUT 扩展 git 段（Task 4） |
| `packages/web/frontend/src/api/wsConfig.ts` | 前端 client | `WsGitFields` 类型（Task 5） |
| `packages/web/frontend/src/routes/WorkspaceDetail/WsSettingsTab.tsx` | ws 配置页 | 加 git 段表单（Task 5） |
| `packages/web/frontend/src/locales/{zh,en}.json` | i18n | `wsConfig.fields.{gitlabUser,gitlabToken}` + apiKey.configured 复用（Task 5） |

---

## Task 1: WsConfig 加 git 段 + WsConfigStore 扩展 git 凭据加解密

**Files:**
- Modify: `packages/web/src/supernova_web/components/ws_config_store.py`（阶段 2 已建）
- Modify: `packages/web/src/supernova_web/components/credential_vault.py`（`CREDENTIAL_FIELDS`）
- Test: `packages/web/tests/test_ws_config_store.py`（扩展，阶段 2 已建）

**Interfaces:**
- Consumes: 阶段 2 的 `WsConfigStore` / `CredentialVault`
- Produces: `WsGitFields` dataclass；`WsConfig.git: WsGitFields`；`WsConfigStore.read/write` 处理 git 凭据加解密。下游 Task 2-4 消费。

- [ ] **Step 1: 扩展失败测试** — 在 `packages/web/tests/test_ws_config_store.py` 追加：

```python
def test_write_then_read_git_credentials(store, tmp_path):
    """git.gitlab_token 密文落盘，读回明文。"""
    (tmp_path / "ws-a").mkdir()
    from supernova_web.components.ws_config_store import WsConfig, WsGitFields
    store.write("ws-a", WsConfig(
        provider=WsProviderFields(),
        git=WsGitFields(gitlab_user="bot-a", gitlab_token="glpat-a"),
    ))
    raw = (tmp_path / "ws-a" / "config.yaml").read_text()
    assert "glpat-a" not in raw                # 密文
    assert "bot-a" in raw                      # user 明文
    cfg = store.read("ws-a")
    assert cfg.git.gitlab_user == "bot-a"
    assert cfg.git.gitlab_token == "glpat-a"   # 读回明文


def test_read_missing_git_returns_empty(store, tmp_path):
    (tmp_path / "ws-a").mkdir()
    cfg = store.read("ws-a")
    assert cfg.git.gitlab_user is None
    assert cfg.git.gitlab_token is None


def test_credential_fields_includes_gitlab_token():
    from supernova_web.components.credential_vault import CredentialVault
    assert "gitlab_token" in CredentialVault.CREDENTIAL_FIELDS
```

- [ ] **Step 2: 跑测试确认失败** — `cd packages/web && uv run pytest tests/test_ws_config_store.py -v`
  - 预期：FAIL（`WsGitFields` 不存在）

- [ ] **Step 3: 加 WsGitFields + WsConfig.git** — 编辑 `packages/web/src/supernova_web/components/ws_config_store.py`

```python
@dataclass
class WsGitFields:
    gitlab_user: str | None = None
    gitlab_token: str | None = None      # 内存明文；落盘密文


@dataclass
class WsConfig:
    provider: WsProviderFields = field(default_factory=WsProviderFields)
    git: WsGitFields = field(default_factory=WsGitFields)
```

- [ ] **Step 4: `CREDENTIAL_FIELDS` 加 gitlab_token** — 编辑 `packages/web/src/supernova_web/components/credential_vault.py`

```python
    CREDENTIAL_FIELDS = frozenset({"api_key", "auth_token", "gitlab_token"})
```

- [ ] **Step 5: WsConfigStore read/write 扩展 git 段** — 编辑 `ws_config_store.py` 的 `read` / `write`

  `read`（解密 git.gitlab_token）：
```python
    def read(self, ws: str) -> WsConfig:
        path = self._config_path(ws)
        if not path.exists():
            return WsConfig()
        data = yaml.safe_load(path.read_text("utf-8")) or {}
        # provider 段（阶段 2 已有）
        prov_raw = data.get("provider") or {}
        if "api_key" in prov_raw:
            prov_raw["api_key"] = self._vault.decrypt(prov_raw["api_key"])
        known_prov = {f.name for f in fields(WsProviderFields)}
        # git 段（阶段 4 新增）
        git_raw = data.get("git") or {}
        if "gitlab_token" in git_raw:
            git_raw["gitlab_token"] = self._vault.decrypt(git_raw["gitlab_token"])
        known_git = {f.name for f in fields(WsGitFields)}
        return WsConfig(
            provider=WsProviderFields(**{k: prov_raw.get(k) for k in known_prov}),
            git=WsGitFields(**{k: git_raw.get(k) for k in known_git}),
        )
```

  `write`（加密 git.gitlab_token）：
```python
    def write(self, ws: str, cfg: WsConfig) -> None:
        validate_ws_config(cfg)
        path = self._config_path(ws)
        path.parent.mkdir(parents=True, exist_ok=True)
        prov = asdict(cfg.provider)
        if prov.get("api_key") is not None:
            prov["api_key"] = self._vault.encrypt(prov["api_key"])
        git = asdict(cfg.git)
        if git.get("gitlab_token") is not None:
            git["gitlab_token"] = self._vault.encrypt(git["gitlab_token"])
        data = {"provider": prov, "git": git}
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), "utf-8")
```

- [ ] **Step 6: 跑测试确认通过** — `cd packages/web && uv run pytest tests/test_ws_config_store.py -v`
  - 预期：全 PASS（含阶段 2 原有用例 + 阶段 4 新增 3 个）

- [ ] **Step 7: Commit**

```bash
git add packages/web/src/supernova_web/components/ws_config_store.py \
        packages/web/src/supernova_web/components/credential_vault.py \
        packages/web/tests/test_ws_config_store.py
git commit -m "feat(web): P3c 阶段4 WsConfig 加 git 段 + 凭据加解密

WsGitFields(gitlab_user/gitlab_token) + WsConfig.git；WsConfigStore read/write
扩展 git 段（gitlab_token 密文，复用 CredentialVault）；CREDENTIAL_FIELDS 加 gitlab_token。"
```

---

## Task 2: GitFetcher per-ws 凭据解析

**Files:**
- Modify: `packages/web/src/supernova_web/components/git_fetcher.py:34-93`
- Test: `packages/web/tests/test_git_fetcher_per_ws.py`

**Interfaces:**
- Consumes: Task 1 的 `WsConfigStore.read(ws).git`；`WebConfig.gitlab_user/gitlab_token`（全局）
- Produces: `GitFetcher(repos_dir, gitlab_user, gitlab_token, ws_config_store=None)` + `_creds_for(ws)` + `available(ws=None)` / `_inject_auth(url, ws=None)` / `fetch(..., ws=None)`。

- [ ] **Step 1: 写失败测试** — 新建 `packages/web/tests/test_git_fetcher_per_ws.py`

```python
"""P3c 阶段 4：GitFetcher 按 ws 解析凭据（ws git 段优先，回落全局）。"""
from unittest.mock import MagicMock
from supernova_web.components.git_fetcher import GitFetcher


def test_creds_global_when_no_ws_store():
    f = GitFetcher("/tmp/r", "global-user", "global-token")
    assert f.available() is True
    assert f._inject_auth("https://host/x") == "https://global-user:global-token@host/x"


def test_creds_from_ws_when_configured():
    store = MagicMock()
    store.read.return_value.git.__dict__ = {"gitlab_user": "ws-user", "gitlab_token": "ws-token"}
    # 用真实 WsConfig 构造 store.read 返回值更稳：
    from supernova_web.components.ws_config_store import WsConfig, WsGitFields
    store.read.return_value = WsConfig(git=WsGitFields(gitlab_user="ws-user", gitlab_token="ws-token"))
    f = GitFetcher("/tmp/r", "global-user", "global-token", ws_config_store=store)
    assert f.available("ws-a") is True
    assert f._inject_auth("https://host/x", "ws-a") == "https://ws-user:ws-token@host/x"


def test_creds_fall_back_to_global_when_ws_git_unset():
    store = MagicMock()
    from supernova_web.components.ws_config_store import WsConfig, WsGitFields
    store.read.return_value = WsConfig(git=WsGitFields())   # 空 git 段
    f = GitFetcher("/tmp/r", "global-user", "global-token", ws_config_store=store)
    assert f.available("ws-a") is True
    assert f._inject_auth("https://host/x", "ws-a") == "https://global-user:global-token@host/x"


def test_partial_override_user_only():
    """只覆盖 user，token 回落全局。"""
    store = MagicMock()
    from supernova_web.components.ws_config_store import WsConfig, WsGitFields
    store.read.return_value = WsConfig(git=WsGitFields(gitlab_user="ws-user"))  # token=None
    f = GitFetcher("/tmp/r", "global-user", "global-token", ws_config_store=store)
    assert f._inject_auth("https://host/x", "ws-a") == "https://ws-user:global-token@host/x"


def test_available_false_when_no_creds():
    f = GitFetcher("/tmp/r", None, None)
    assert f.available() is False
    assert f.available("ws-a") is False
```

- [ ] **Step 2: 跑测试确认失败** — `cd packages/web && uv run pytest tests/test_git_fetcher_per_ws.py -v`
  - 预期：FAIL（`GitFetcher` 不收 `ws_config_store` / `available` 不收 ws）

- [ ] **Step 3: 改 GitFetcher** — 编辑 `packages/web/src/supernova_web/components/git_fetcher.py:34-93`

```python
class GitFetcher:
    def __init__(self, repos_dir: Path, gitlab_user: str | None, gitlab_token: str | None,
                 ws_config_store=None) -> None:
        self._dir = Path(repos_dir)
        self._user = gitlab_user          # 全局默认
        self._token = gitlab_token        # 全局默认
        self._ws_config_store = ws_config_store   # P3c 阶段 4：per-ws 凭据

    def _creds_for(self, ws: str | None = None) -> tuple[str | None, str | None]:
        """P3c 阶段 4：按 ws 解析凭据——ws git 段优先（字段级 or 合并），回落全局。"""
        if ws and self._ws_config_store is not None:
            git = self._ws_config_store.read(ws).git
            user = git.gitlab_user or self._user
            token = git.gitlab_token or self._token
            return user, token
        return self._user, self._token

    def available(self, ws: str | None = None) -> bool:
        u, t = self._creds_for(ws)
        return bool(u and t)

    @staticmethod
    def repo_name(url: str) -> str:
        last = url.rstrip("/").split("/")[-1]
        return last[:-4] if last.endswith(".git") else last

    @staticmethod
    def redact(text: str) -> str:
        return _TOKEN_RE.sub("https://***:***@", text)

    def _inject_auth(self, url: str, ws: str | None = None) -> str:
        u, t = self._creds_for(ws)
        return url.replace("https://", f"https://{u}:{t}@", 1)

    async def _run(self, args, cwd=None):   # 不变
        ...

    async def fetch(self, url: str, branch: str | None = None,
                    commit: str | None = None, force_reclone: bool = False,
                    ws: str | None = None) -> Path:
        if not self.available(ws):
            raise PermissionError("GitLab credentials missing")
        name = self.repo_name(url)
        target = self._dir / name
        authed = self._inject_auth(url, ws)

        if target.exists() and not force_reclone:
            rc, _, _ = await self._run(["git", "pull", "--ff-only"], cwd=target)
            if rc != 0:
                shutil.rmtree(target, ignore_errors=True)
        if force_reclone and target.exists():
            shutil.rmtree(target, ignore_errors=True)

        if not target.exists():
            cmd = ["git", "clone"]
            if branch and not commit:
                cmd += ["--branch", branch]
            cmd += [authed, str(target)]
            rc, _, err = await self._run(cmd)
            if rc != 0:
                raise RuntimeError(f"clone failed: {self.redact(err)}")

        if commit:
            await self._run(["git", "fetch", "--all"], cwd=target)
            rc, _, err = await self._run(["git", "checkout", commit], cwd=target)
            if rc != 0:
                raise RuntimeError(f"checkout failed: {self.redact(err)}")
        return target
```

  > 注：`_run` 方法体不变（:55-62）。`fetch` 的 `_dir / name` 在 P2 后 GitFetcher 的 `repos_dir` 实际是 ws 的 repos 目录（RepoManager 按 ws 构造/调用），ws 凭据解析与目录解耦——确认 RepoManager 传 ws 给 fetch（Task 3）。

- [ ] **Step 4: 跑测试确认通过** — `cd packages/web && uv run pytest tests/test_git_fetcher_per_ws.py -v`
  - 预期：5 PASS

- [ ] **Step 5: 跑现有 git_fetcher 回归** — `cd packages/web && uv run pytest tests/test_git_fetcher.py -v`（若存在；否则跑 repo_manager clone 相关）
  - 预期：全 PASS（`ws=None` 走全局，行为不变）

- [ ] **Step 6: Commit**

```bash
git add packages/web/src/supernova_web/components/git_fetcher.py \
        packages/web/tests/test_git_fetcher_per_ws.py
git commit -m "feat(web): P3c 阶段4 GitFetcher per-ws 凭据解析

_creds_for(ws): ws git 段优先(字段级 or 合并)回落全局；available(ws)/
_inject_auth(url,ws)/fetch(...,ws)。ws_config_store 可选(None=全局兜底)。
ws=None 走全局(行为不变)。"
```

---

## Task 3: RepoManager._clone_task 传 ws + app.py 装配

**Files:**
- Modify: `packages/web/src/supernova_web/components/repo_manager.py:243/271`（+ fetch 调用点若有）
- Modify: `packages/web/src/supernova_web/app.py:234`（GitFetcher 装配）
- Test: `packages/web/tests/test_repo_clone_uses_ws_creds.py`

**Interfaces:**
- Consumes: Task 2 的 `GitFetcher.available(ws)`/`_inject_auth(url, ws)`/`fetch(..., ws)`；P2 的 `RepoManager._clone_task` 已有 ws
- Produces: clone 用 ws 凭据；`app.state.repo_manager` 的 GitFetcher 持 ws_config_store。

- [ ] **Step 1: 写失败测试** — 新建 `packages/web/tests/test_repo_clone_uses_ws_creds.py`

```python
"""P3c 阶段 4：RepoManager.clone 用 ws 凭据（传 ws 给 GitFetcher）。"""
from unittest.mock import AsyncMock, MagicMock


async def test_clone_passes_ws_to_git_fetcher(tmp_path):
    """clone(ws=...) → GitFetcher._inject_auth(url, ws) / available(ws) 被调。"""
    from supernova_web.components.repo_manager import RepoManager
    git = MagicMock()
    git.available = MagicMock(return_value=True)
    git._inject_auth = MagicMock(side_effect=lambda url, ws=None: f"https://ws-{ws}:t@host/x")
    git.repo_name = MagicMock(return_value="x")
    git.redact = MagicMock(return_value="redacted")
    # _clone_task 跑真实 git clone 较重，mock _build_clone_argv + _run（按现有 repo_manager 测试模式）
    rm = RepoManager(tmp_path, git, max_concurrent=1)
    # 触发 clone(ws="ws-a")，断言 git._inject_auth 收到 ws="ws-a"
    ...   # 参照现有 test_repos_ws_isolation.py 的 clone 测试 mock 模式
    git._inject_auth.assert_called_with(..., "ws-a")   # 或 assert ws="ws-a" in 调用
```

  > 注：clone 测试 mock 模式参照现有 `tests/test_repos_ws_isolation.py` / `test_repo_lifecycle_in_ws.py`（P2）。核心断言：`_inject_auth` / `available` 收到 ws 参数。

- [ ] **Step 2: 跑测试确认失败** — `cd packages/web && uv run pytest tests/test_repo_clone_uses_ws_creds.py -v`

- [ ] **Step 3: 改 RepoManager._clone_task** — 编辑 `packages/web/src/supernova_web/components/repo_manager.py:243/271`

```python
        if not self._git.available(ws):          # P3c 阶段 4：传 ws（原 :243 available()）
            ...
        name = name or self._git.repo_name(url)  # :245 不变
        ...
                    argv=self._build_clone_argv(self._git._inject_auth(url, ws), target, branch))
                    # P3c 阶段 4：_inject_auth(url, ws)（原 :271 _inject_auth(url)）
```

  > 注：`_clone_task` 的 ws 参数来自 P2（`clone(ws=...)` → `_clone_task(ws, ...)`）。若 `_clone_task` 内 ws 变量名不同，按实际名传。fetch 调用点（若有 `await self._git.fetch(...)`）同样加 `ws=ws`。

- [ ] **Step 4: app.py 装配** — 编辑 `packages/web/src/supernova_web/app.py:234`

```python
        git_fetcher = GitFetcher(
            cfg.repos_dir, cfg.gitlab_user, cfg.gitlab_token,
            ws_config_store=app.state.ws_config_store,   # P3c 阶段 4：per-ws 凭据
        )
```

  > 注：`GitFetcher` 装配在 `app.state.ws_config_store`（阶段 2 Task 3 装配）之后，顺序正确（:234 在 ws_config_store 装配 :228 后）。确认行号顺序，必要时调整装配顺序。

- [ ] **Step 5: 跑测试 + 回归** — `cd packages/web && uv run pytest tests/test_repo_clone_uses_ws_creds.py tests/test_repos_ws_isolation.py tests/test_repo_lifecycle_in_ws.py -v`
  - 预期：全 PASS

- [ ] **Step 6: Commit**

```bash
git add packages/web/src/supernova_web/components/repo_manager.py \
        packages/web/src/supernova_web/app.py \
        packages/web/tests/test_repo_clone_uses_ws_creds.py
git commit -m "feat(web): P3c 阶段4 RepoManager clone 用 ws 凭据 + 装配

_clone_task 传 ws 给 GitFetcher.available(ws)/_inject_auth(url,ws)/fetch(ws)。
app.py GitFetcher 装配收 ws_config_store。未配 ws 凭据回落全局(行为不变)。"
```

---

## Task 4: ws_config API 扩展 git 段（GET 脱敏 / PUT）

**Files:**
- Modify: `packages/web/src/supernova_web/api/ws_config.py`（阶段 2 Task 4 已建）
- Test: `packages/web/tests/test_api_ws_config.py`（扩展）

**Interfaces:**
- Consumes: Task 1 的 `WsConfig.git` / `WsGitFields`
- Produces: GET 返 git 段（`gitlab_token` 脱敏）；PUT 收 git 段（`gitlab_token` 空串=不改）。

- [ ] **Step 1: 扩展失败测试** — 在 `packages/web/tests/test_api_ws_config.py` 追加：

```python
def test_put_then_get_masks_gitlab_token(authed_client, tmp_workspaces):
    (tmp_workspaces / "ws-a").mkdir()
    authed_client.put("/api/workspaces/ws-a/config", json={
        "provider": {},
        "git": {"gitlab_user": "bot-a", "gitlab_token": "glpat-secret"},
    })
    g = authed_client.get("/api/workspaces/ws-a/config").json()["git"]
    assert g["gitlab_user"] == "bot-a"
    assert g["gitlab_token"] == "••••"     # 脱敏


def test_put_empty_gitlab_token_keeps_existing(authed_client, tmp_workspaces):
    (tmp_workspaces / "ws-a").mkdir()
    authed_client.put("/api/workspaces/ws-a/config", json={"provider": {}, "git": {"gitlab_token": "glpat-orig"}})
    authed_client.put("/api/workspaces/ws-a/config", json={"provider": {}, "git": {"gitlab_user": "bot-a"}})
    g = authed_client.get("/api/workspaces/ws-a/config").json()["git"]
    assert g["gitlab_token"] == "••••"     # 保留原值
    assert g["gitlab_user"] == "bot-a"
```

- [ ] **Step 2: 跑测试确认失败** — `cd packages/web && uv run pytest tests/test_api_ws_config.py -v`

- [ ] **Step 3: 改 ws_config API** — 编辑 `packages/web/src/supernova_web/api/ws_config.py`

  3a. 加 pydantic 入参 model + GET 返 git 段：
```python
class WsGitFieldsIn(BaseModel):
    gitlab_user: Optional[str] = None
    gitlab_token: Optional[str] = None    # "" = 不改, 非空 = 更新

class WsConfigIn(BaseModel):
    provider: WsProviderFieldsIn
    git: Optional[WsGitFieldsIn] = None   # P3c 阶段 4
```

  3b. GET（`get_ws_config`）返 git 段脱敏：
```python
    g = cfg.git
    return {
        "provider": { ... },   # 阶段 2 已有
        "git": {
            "gitlab_user": g.gitlab_user,
            "gitlab_token": MASKED if g.gitlab_token else None,
        },
    }
```

  3c. PUT（`put_ws_config`）merge git（`gitlab_token` 空串=不改）+ write：
```python
    existing_git = existing.git
    new_git_token = body.git.gitlab_token if (body.git and body.git.gitlab_token) else existing_git.gitlab_token
    new_git_user = body.git.gitlab_user if body.git else existing_git.gitlab_user
    from ..components.ws_config_store import WsGitFields
    cfg = WsConfig(
        provider=WsProviderFields(...),   # 阶段 2 已有
        git=WsGitFields(gitlab_user=new_git_user, gitlab_token=new_git_token),
    )
    store.write(ws, cfg)
```

- [ ] **Step 4: 跑测试确认通过** — `cd packages/web && uv run pytest tests/test_api_ws_config.py -v`
  - 预期：全 PASS

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/supernova_web/api/ws_config.py \
        packages/web/tests/test_api_ws_config.py
git commit -m "feat(web): P3c 阶段4 ws_config API git 段 (GET 脱敏/PUT)

GET 返 git.gitlab_token 脱敏；PUT 收 git 段(gitlab_token 空串=不改)。对齐阶段2 provider 段模式。"
```

---

## Task 5: 前端 ws 配置页 git 段

**Files:**
- Modify: `packages/web/frontend/src/api/wsConfig.ts`（阶段 2 Task 6 已建）
- Modify: `packages/web/frontend/src/routes/WorkspaceDetail/WsSettingsTab.tsx`（阶段 2 Task 6 已建）
- Modify: `packages/web/frontend/src/locales/{zh,en}.json`
- Test: 扩展 `WsSettingsTab.test.tsx`

**Interfaces:**
- Consumes: Task 4 的 GET/PUT git 段
- Produces: ws 配置页加 git 段表单（`gitlab_user` 文本 + `gitlab_token` password 脱敏）。

- [ ] **Step 1: api/wsConfig.ts 加 git 类型** — 编辑 `packages/web/frontend/src/api/wsConfig.ts`

```ts
export interface WsGitFields {
  gitlab_user: string | null;
  gitlab_token: string | null;   // GET 返 "••••" 或 null
}
export interface WsConfig {
  provider: WsProviderFields;
  git?: WsGitFields;             // P3c 阶段 4
}
```

- [ ] **Step 2: WsSettingsTab 加 git 段表单** — 编辑 `packages/web/frontend/src/routes/WorkspaceDetail/WsSettingsTab.tsx`

  在 provider 段表单后加 git 段（`gitlab_user` Input + `gitlab_token` password Input，脱敏占位同 `api_key`）。state 加 `git` 字段 + `gitlabTokenInput`（password 框，空=不改）。`onSave` 的 PUT body 加 `git: { gitlab_user, gitlab_token: gitlabTokenInput || undefined }`。

  参考 provider `api_key` 的脱敏 + 空串不改模式（阶段 2 Task 6 已实现），git 段照搬。

- [ ] **Step 3: i18n** — 编辑 `packages/web/frontend/src/locales/zh.json` 的 `wsConfig.fields` 加：
```json
        "gitlabUser": "GitLab 用户",
        "gitlabToken": "GitLab Token"
```
  + `wsConfig.gitSection`（如 "Git 凭据"）。`en.json` 同位置加英文（值真翻译）。

- [ ] **Step 4: 扩展组件测试** — 在 `WsSettingsTab.test.tsx` 加：
  - GET 返 `git.gitlab_token: "••••"` → gitlab_token 输入框占位显示「已配置」
  - 填 gitlab_user + 点保存 → PUT body 含 `git.gitlab_user`

- [ ] **Step 5: 跑前端测试 + tsc + build** — `cd packages/web/frontend && npx vitest run src/routes/WorkspaceDetail && npx tsc -p . --noEmit && npm run build`
  - 预期：绿

- [ ] **Step 6: Commit**

```bash
git add packages/web/frontend/src/api/wsConfig.ts \
        packages/web/frontend/src/routes/WorkspaceDetail/WsSettingsTab.tsx \
        packages/web/frontend/src/routes/WorkspaceDetail/__tests__/WsSettingsTab.test.tsx \
        packages/web/frontend/src/locales/zh.json \
        packages/web/frontend/src/locales/en.json
git commit -m "feat(web/frontend): P3c 阶段4 ws 配置页 git 段 UI

WsSettingsTab 加 git 段(gitlab_user + gitlab_token password 脱敏，对齐 api_key 模式)；
api/wsConfig.ts 加 WsGitFields；i18n gitlabUser/gitlabToken (zh/en)。"
```

---

## Task 6: 回归 + 端到端

**Files:**
- Test: `packages/web/tests/test_git_creds_e2e.py`（新建，集成 ws 凭据 → clone）

**Interfaces:**
- Consumes: Task 1-5 全部
- Produces: 端到端——ws-A 配 GitLab 账号 A / ws-B 配账号 B，各自 clone 各自可访问的 repo；未配 git 凭据的 ws 回落全局。

- [ ] **Step 1: 写端到端测试** — 新建 `packages/web/tests/test_git_creds_e2e.py`

```python
"""P3c 阶段 4 端到端：ws 凭据 → GitFetcher 解析 → clone 用对应账号。"""


def test_ws_git_creds_flow_to_clone(app_with_ws, tmp_workspaces, monkeypatch):
    """PUT 写 ws git 凭据 → RepoManager.clone(ws) 的 GitFetcher._inject_auth 用 ws 凭据。"""
    (tmp_workspaces / "ws-a").mkdir()
    app_with_ws.test_client().put("/api/workspaces/ws-a/config", json={
        "provider": {}, "git": {"gitlab_user": "bot-a", "gitlab_token": "glpat-a"}
    }, headers=auth_headers)
    # mock GitFetcher._run（不跑真实 clone），断言 clone argv 含 "https://bot-a:glpat-a@..."
    ...
```

  > 注：e2e mock `_run` 捕获 clone argv，断言凭据。参照现有 `test_repos_ws_isolation.py` clone 测试模式。

- [ ] **Step 2: 跑 e2e + 全包回归** —
  - `cd packages/web && uv run pytest tests/test_git_creds_e2e.py tests/test_git_fetcher_per_ws.py tests/test_ws_config_store.py tests/test_api_ws_config.py tests/test_repo_clone_uses_ws_creds.py -v`
  - 相关回归：`uv run pytest tests/test_repos_ws_isolation.py tests/test_api_repos.py -v`
  - 预期：全 PASS

- [ ] **Step 3: 人工核验** — 测试后 `cat workspaces/<ws>/config.yaml` 不见明文 gitlab_token（密文）；未配 git 段的 ws clone 用全局凭据。

- [ ] **Step 4: Commit**

```bash
git add packages/web/tests/test_git_creds_e2e.py
git commit -m "test(web): P3c 阶段4 ws git 凭据端到端 + 回归

PUT→config.yaml(密文)→GitFetcher._creds_for(ws)→clone argv 用 ws 凭据。
未配 git 段回落全局。阶段 4 完成：clone 凭据 per-ws。P3c 全阶段 plan 齐全。"
```

---

## Self-Review（plan 作者自检）

**1. Spec 覆盖**：spec §9.2.1（git 段进 config.yaml）→ Task 1；§9.2.2（GitFetcher per-ws）→ Task 2；§9.2.3（RepoManager 调用链）→ Task 3；§9.2.4（前端凭据 UI）→ Task 5；§9.3（验收）→ Task 6。§9.4（multi-configs 不做）→ Global Constraints。

**2. 占位符扫描**：少数测试（clone argv mock、e2e auth_headers）标注"参照现有 X 模式"——复用指引（P2 已有 clone 测试 fixture），非占位。核心代码（WsGitFields/GitFetcher _creds_for/repo_manager 传 ws/ws_config API git 段）完整。

**3. 凭据语义一致**：`gitlab_token` 进 `CREDENTIAL_FIELDS`（Task 1）+ WsConfigStore write 加密/read 解密（Task 1）+ GET 脱敏 `••••`（Task 4）+ 前端 password 占位（Task 5）——四处一致，与阶段 2 `api_key` 模式对齐。

**4. 回落语义一致**：`_creds_for(ws)` 的「ws git 段字段级 or 合并，回落全局」（Task 2）+ Global Constraints + Task 6 e2e（未配回落全局）——三处一致。

**5. 阶段 2 依赖一致**：Global Constraints + Task 1（扩展 WsConfig/WsConfigStore）+ Task 3（app.py 装配复用 ws_config_store）——明确阶段 2 前置。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-27-web-config-isolation-stage4.md`. Two execution options:

1. **Subagent-Driven（推荐）** — 每 task 派 fresh subagent + 两阶段 review。阶段 4 相对小，依赖阶段 2，适合阶段 2 实现后接续。
2. **Inline Execution** — 本 session 批量 + 检查点。

Which approach?

---

**P3c 全阶段 plan 齐全**（阶段 0-4）。建议执行顺序：阶段 0（✅ done）→ 1 → 2 → 3 → 4（每阶段独立 merge + 冒烟后再进下一阶段；阶段 3 风险最高需充分回归）。Phase C（黑盒 web C1 化）独立于 P3c，可任何时候做。
