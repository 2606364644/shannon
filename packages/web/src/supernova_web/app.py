from __future__ import annotations

import html
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from starlette.staticfiles import StaticFiles

from .api.system_status import resolve_brand_name
from .config import get_config
from supernova_core.config.env_loader import load_env


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时加载 profile 凭证（对齐 worker runner.main / CLI blackbox·combined main.py
    # 首行 load_env）。不加载则 scan_manager.build_provider_config() 读不到 profile 里的
    # SUPERNOVA_AI_PROVIDER（只在 .env.profiles/<profile>.env，docker env_file 不注入）→
    # 回落默认 anthropic_api → worker 跑 CLI 但无凭据 → 每轮 "Not logged in"（2026-07-30 根因）。
    load_env()
    # auth: 启动 seed 预置账号 + 周期清理过期 session
    from .auth.seed import seed_users
    import asyncio
    seed_users(app.state.auth_store, app.state.config.users_seed_file)

    async def _purge_loop():
        while True:
            try:
                app.state.session_manager.purge_expired()
            except Exception:
                pass
            await asyncio.sleep(3600)
    app.state._purge_task = asyncio.create_task(_purge_loop())

    # 启动迁移序列（顺序敏感）：
    #   1) 旧全局 repos/<name> -> workspaces/__legacy__/repos/<name>（创建 __legacy__ ws 目录）
    #   2) ws 根 legacy scan（session.json）-> scans/<legacy_id>/（T5 解耦 1:N）
    #   3) 给无成员记录的 ws（含 __legacy__）分配 admin (manager)
    #   4) per-ws 补写仓库 meta（覆盖 __legacy__ 的搬迁仓库）
    #   5) 重建孤儿 scan 状态（遍历 ScanStore scans，含迁移后的 legacy scan）
    # purge_loop 与本序列无依赖、保持原位即可。
    _migrate_legacy_repos(app)
    _migrate_legacy_scans(app)
    _migrate_legacy_workspace_members(app)
    _reconcile_repo_meta(app)
    await _reconcile_orphaned_scans(app)  # 重启后给孤儿 scan 补 scan_end，让 live 不再卡 running
    yield
    app.state._purge_task.cancel()
    # shutdown（任务 9 接入 ScanManager 取消在途扫描后填充）


async def _reconcile_orphaned_scans(app: FastAPI) -> None:
    """启动时遍历每个 ws 的所有 scan（ScanStore 双源：新 scans/<id>/ + legacy 根），
    对孤儿 scan（session running 但 worker 已不存活、且无 scan_end）补写 scan_end
    (interrupted) + 失败原因。

    T5: 改遍历 ScanStore._scan_entries（per-scan），而非 ws 根目录 -- 1:N 后 scan 在
    scans/<id>/（含迁移后的 legacy scan）。容器重启会杀掉 scan_manager._watch 协程，
    导致在途 scan 永不写 scan_end、session 卡 running、live SSE 空等。此处一次性兜底，
    使重开 live 页能正常显「已中断」+ 原因。单 scan 异常不阻塞启动。
    """
    from .components.orphan_reconciler import reconcile_orphaned
    from .components.scan_store import ScanStore
    cfg = app.state.config
    indexer = app.state.indexer
    # 启动时 scan_manager._procs 为空，active_pids()={} -> is_running 对所有 ws=False，
    # 故所有 session running 的 scan 都会被判孤儿并补 scan_end（这正是重启后的真实情况）。
    indexer.sync_active(app.state.scan_manager.active_pids())
    if not cfg.workspaces_dir.is_dir():
        return
    store = ScanStore(cfg.workspaces_dir)
    for ws_dir in cfg.workspaces_dir.iterdir():
        if not ws_dir.is_dir():
            continue
        for _scan_id, scan_dir in store._scan_entries(ws_dir.name):
            try:
                await reconcile_orphaned(scan_dir, False)
            except Exception:
                continue


def _migrate_legacy_workspace_members(app: FastAPI) -> None:
    """把无成员记录的 legacy workspace 分配给所有 admin（manager），让 admin 能见/管。
    已有成员记录的 workspace 不动（不重复分配、不覆盖）。"""
    store = app.state.auth_store
    admins = [u for u in store.list_all_users() if u.role == "admin"]
    if not admins:
        return
    ws_dir = app.state.config.workspaces_dir
    if not ws_dir.is_dir():
        return
    for d in ws_dir.iterdir():
        if not d.is_dir():
            continue
        if store.list_workspace_members(d.name):
            continue  # 已有成员，跳过
        for a in admins:
            store.add_workspace_member(d.name, a.id, "manager")


def _migrate_legacy_repos(app: FastAPI) -> None:
    """启动迁移：把旧全局 ``repos/<name>`` 搬到 ``workspaces/__legacy__/repos/<name>``。

    背景：web repos 隔离 P2 前，所有 clone 仓库落在共享全局 ``repos/`` 下（无 ws 归属）。
    P2 起 repos 按 ws 分桶（``workspaces/<ws>/repos/``），旧全局目录废弃。本函数在启动时
    一次性把残留的旧全局仓库迁入 ``__legacy__`` ws，使其对 admin 可见、可扫。

    仅迁 top-level 且含 ``.git`` 的目录（避免误搬普通文件夹）；目标已存在或源缺失则跳过
    （幂等）。admin 分配由 ``_migrate_legacy_workspace_members`` 复用既有逻辑，**此处不
    重复实现**。单仓库失败不阻塞启动（best-effort）。

    final-review C1：搬迁后给 ``__legacy__/`` 补写 ``workspace.json``（mirror
    ``POST /api/workspaces`` 的写法），否则 indexer ``read_workspace_meta`` 不认
    ``__legacy__`` -> GET /api/workspaces 对全员（含 admin）不可见。仅在 ``__legacy__``
    已作为真实 ws 目录存在（至少迁了一个仓库）时写，幂等（已存在则不覆盖），写失败不阻塞启动。
    """
    import shutil

    cfg = app.state.config
    old_root = cfg.repos_dir
    if not old_root.is_dir():
        return
    legacy_ws = cfg.workspaces_dir / "__legacy__"
    legacy_repos = legacy_ws / "repos"
    for sub in list(old_root.iterdir()):
        if not sub.is_dir() or sub.name.startswith("."):
            continue
        if not (sub / ".git").exists():
            continue  # 仅迁真仓库（含 .git），跳过普通文件夹
        target = legacy_repos / sub.name
        if target.exists():
            continue  # 幂等：目标已存在，跳过（不覆盖）
        try:
            legacy_repos.mkdir(parents=True, exist_ok=True)
            shutil.move(str(sub), str(target))
        except Exception:
            # 单仓库失败不影响其他仓库与整体启动
            continue

    # final-review C1：__legacy__ ws 目录已存在（至少迁了一个仓库）-> 补写 workspace.json，
    # 使其在 GET /api/workspaces 可见（indexer read_workspace_meta 认 workspace.json）。T2
    # 后 ws 元数据与 scan 状态机解耦：ws 级写 workspace.json（非 session.json），空 ws 经
    # indexer 聚合 scan_count=0 可见。mirror POST /api/workspaces 的写法。幂等 + best-effort。
    if legacy_repos.is_dir():
        from .components.scan_store import write_workspace_meta
        meta_file = legacy_ws / "workspace.json"
        if not meta_file.exists():
            try:
                write_workspace_meta(legacy_ws, name="__legacy__", owner="legacy")
            except Exception:
                # 写 workspace.json 失败不阻塞启动（best-effort）
                pass


_LEGACY_WS = "__legacy__"
# workspace.json owner 为以下自动填充值时，该 ws 是 scan 固化/迁移产生的伪 ws（非 admin 手建）。
_AUTO_OWNERS = {"legacy", "host", "web"}
# scan 默认目录命名：<hostname>_YYYYMMDD-HHMMSS / <repo>_<epoch> /
# <hostname>_shannon-<epoch> / <repo>_<scan_type>-<epoch> —— 共同特征为以 <sep><6+位数字> 结尾。
_SCAN_NAME_RE = re.compile(r"^.+[-_]\d{6,}$")


def _is_scan_name(name: str) -> bool:
    """目录名是否符合 scan 默认命名（用于识别被误固化为 ws 的旧 scan）。"""
    return bool(_SCAN_NAME_RE.match(name))


def _ensure_legacy_ws(workspaces_dir: Path) -> Path:
    """确保 __legacy__ ws 存在且有 workspace.json（indexer read_workspace_meta 可见）。"""
    legacy = workspaces_dir / _LEGACY_WS
    legacy.mkdir(parents=True, exist_ok=True)
    if not (legacy / "workspace.json").exists():
        try:
            from .components.scan_store import write_workspace_meta
            write_workspace_meta(legacy, name=_LEGACY_WS, owner="legacy")
        except Exception:
            pass  # best-effort：写失败不阻塞
    return legacy


def _unique_scan_target(scans_dir: Path, scan_id: str) -> Path:
    """在 scans_dir 下确定目标 scan 目录，名字冲突时追加 -2/-3。"""
    target = scans_dir / scan_id
    i = 2
    while target.exists():
        target = scans_dir / f"{scan_id}-{i}"
        i += 1
    return target


def _rmdir_if_empty(d: Path) -> None:
    """目录为空才删（防误删仍有内容的目录）。"""
    try:
        next(d.iterdir())  # 非空 -> 返回首项；空 -> StopIteration
    except StopIteration:
        try:
            d.rmdir()
        except OSError:
            pass
    except OSError:
        pass


def _migrate_legacy_scans(app: FastAPI) -> None:
    """把 workspaces 根下的旧 scan 收纳进 __legacy__ ws 的 scans/ 下。

    web 多租户化后，工作区是 admin 手建的容器（workspace.json + 成员制），scan 应落在
    某个 ws 的 scans/ 下。但历史遗留两类旧 scan 平铺在 workspaces 根，被 indexer 误识别
    成工作区（read_workspace_meta 回退根 session.json），污染工作区列表。本函数在启动时
    把它们统一收纳进 __legacy__ ws：

    - 情况 A（未固化 legacy scan）：ws 根 session.json + 无 workspace.json + 无 config.yaml
      -> session.json + 产物搬入 __legacy__/scans/<legacy_id>/，原目录删除（不再提升为 ws）。
    - 情况 B（已固化伪 ws）：有 workspace.json 且 owner 为自动值 {legacy,host,web} 且目录名
      匹配 scan 命名 -> 先备份 workspace.json 到 __legacy__/.migrated/，再把 scans/* 搬入
      __legacy__/scans/，删原伪 ws 目录。
    - 其余（真 ws / __legacy__ 自身 / 无 session.json 的残留目录）-> 不动。

    - legacy_id 从 session.json created_at 派生 YYYYMMDD-HHMMSS（碰撞 -2/-3）；缺失/异常回退
      ws 目录名。
    - 幂等：A 搬走 session.json 后根无 session.json -> 跳过；B 删目录后不存在 -> 跳过。
    - best-effort：损坏 session.json / 单目录失败不阻断启动。
    - 不动 read_workspace_meta（保留 session.json 回退兼容真旧 ws）；不动 CLI/worker.py。
    """
    import json
    import shutil
    from datetime import datetime

    from .components.workspaces_indexer import _to_unix

    cfg = app.state.config
    if not cfg.workspaces_dir.is_dir():
        return
    _SCAN_ARTIFACTS = (
        "events.ndjson", "deliverables", "agents", "heartbeat",
        "cancel.requested", "prompts", "workflow.log", "activity_failures.log",
    )
    # __legacy__ ws lazy 建：仅在确有旧 scan 要搬时才 ensure，避免无 legacy 时凭空
    # 多出一个空 __legacy__ ws 污染工作区列表。
    for ws_dir in cfg.workspaces_dir.iterdir():
        if not ws_dir.is_dir() or ws_dir.name == _LEGACY_WS:
            continue
        root_session = ws_dir / "session.json"
        has_meta = (ws_dir / "workspace.json").exists()

        # 情况 A：未固化 legacy scan（根 session.json，无 ws 元数据，无 ws 级 config）
        if (root_session.exists() and not has_meta
                and not (ws_dir / "config.yaml").exists()):
            try:
                data = json.loads(root_session.read_text("utf-8"))
                if not isinstance(data, dict):
                    continue
            except (json.JSONDecodeError, OSError):
                continue  # 损坏 -> 跳过不崩
            ts = _to_unix(data.get("created_at"))
            legacy_id = (datetime.fromtimestamp(ts).strftime("%Y%m%d-%H%M%S")
                         if ts else ws_dir.name)
            try:
                legacy_ws = _ensure_legacy_ws(cfg.workspaces_dir)
                legacy_scans = legacy_ws / "scans"
                legacy_scans.mkdir(parents=True, exist_ok=True)
                target = _unique_scan_target(legacy_scans, legacy_id)
                target.mkdir(parents=True, exist_ok=True)
                for name in _SCAN_ARTIFACTS:
                    src = ws_dir / name
                    if src.exists():
                        shutil.move(str(src), str(target / name))
                # session.json 最后搬（搬完即标志已迁，幂等）
                shutil.move(str(root_session), str(target / "session.json"))
                _rmdir_if_empty(ws_dir)  # 原目录搬空 -> 删除（不提升为 ws）
            except Exception:
                continue  # best-effort：session.json 仍在根，下次重试
            continue

        # 情况 B：已固化伪 ws（自动 owner + scan 命名）-> 降级进 __legacy__
        if has_meta:
            try:
                meta = json.loads((ws_dir / "workspace.json").read_text("utf-8"))
            except (json.JSONDecodeError, OSError):
                meta = {}
            if (isinstance(meta, dict) and meta.get("owner") in _AUTO_OWNERS
                    and _is_scan_name(ws_dir.name)):
                try:
                    legacy_ws = _ensure_legacy_ws(cfg.workspaces_dir)
                    legacy_scans = legacy_ws / "scans"
                    migrated_dir = legacy_ws / ".migrated"
                    migrated_dir.mkdir(parents=True, exist_ok=True)
                    src_meta = ws_dir / "workspace.json"
                    if src_meta.exists():  # 备份 ws 元数据
                        shutil.move(str(src_meta),
                                    str(migrated_dir / f"{ws_dir.name}.json"))
                    src_scans = ws_dir / "scans"
                    if src_scans.is_dir():  # 搬 scans/* 进 __legacy__/scans/
                        legacy_scans.mkdir(parents=True, exist_ok=True)
                        for sub in list(src_scans.iterdir()):
                            target = _unique_scan_target(legacy_scans, sub.name)
                            shutil.move(str(sub), str(target))
                    shutil.rmtree(ws_dir)  # 删原伪 ws 壳
                except Exception:
                    continue  # best-effort
        # 其余（真 ws / 无 session.json 残留）-> 不动


def _reconcile_repo_meta(app: FastAPI) -> None:
    """对每个 workspace 跑 RepoManager.migrate_legacy(ws)——为已存在但缺 meta 的仓库
    补写 ``.supernova-repo.json``（含从旧全局迁入 ``__legacy__`` 的仓库）。

    旧名 ``_migrate_legacy_repos`` 与本函数语义不符（migrate_legacy 不搬仓库、只补 meta），
    2026-07-26 web repos isolation P2 Task 7 重命名为 ``_reconcile_repo_meta``，腾出原名
    给「搬旧全局 repos → __legacy__ ws」的新函数。单 ws 异常不阻塞启动（best-effort）。
    """
    cfg = app.state.config
    if not cfg.workspaces_dir.is_dir():
        return
    rm = app.state.repo_manager
    for d in cfg.workspaces_dir.iterdir():
        if not d.is_dir():
            continue
        try:
            rm.migrate_legacy(d.name)
        except Exception:
            # 单 ws 失败不影响其他 ws 与整体启动
            continue


_INDEX_CACHE: dict[str, tuple[float, str]] = {}
_TITLE_RE = re.compile(r"<title>.*?</title>", re.IGNORECASE | re.DOTALL)


def _render_index_html(index_html: Path, brand: str) -> str:
    """读 index.html，把首个 <title> 注入为当前生效品牌名(已 HTML 转义)。

    消除 SPA 刷新时标签页「先显 index.html 硬编码 Supernova、再被前端 JS 异步改写」的
    跳变:浏览器拿到 HTML 时 title 即为生效品牌名(运行时改名后即时反映)。文件内容按
    mtime 缓存——SPA fallback 是 catch-all,深度路由每次命中都要返 index.html,避免每请求
    读盘 + 正则;title 段每次现替换(brand 随运行时改名变)。brand 经 BrandingStore.validate
    只限长度/非空、不限字符,故必须 HTML 转义防破坏 <title> / 注入。
    """
    key = str(index_html.resolve())
    mtime = index_html.stat().st_mtime
    cached = _INDEX_CACHE.get(key)
    if cached is None or cached[0] != mtime:
        cached = (mtime, index_html.read_text("utf-8"))
        _INDEX_CACHE[key] = cached
    template = cached[1]
    if _TITLE_RE.search(template):
        return _TITLE_RE.sub(f"<title>{html.escape(brand)}</title>", template, count=1)
    return template


def _mount_frontend(app: FastAPI, cfg) -> None:
    """挂载前端 SPA 静态托管（生产/集成模式）。

    cfg.frontend_dir 为空或目录不存在时直接返回（dev 模式前端走 vite 5173）。
    必须在所有 /api/* 路由与 /health 注册**之后**调用——catch-all 靠 FastAPI
    注册顺序保证 API 优先命中。
    """
    if not cfg.frontend_dir:
        return
    dist = Path(cfg.frontend_dir)
    if not dist.is_dir():
        return
    index_html = dist / "index.html"
    dist_resolved = dist.resolve()
    assets_dir = dist / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

    # SPA 入口 index.html 每次必重新验证:注入的 title(品牌名)随运行时改名变,且杜绝浏览器
    # 启发式缓存陈旧 HTML(改名后 F5 仍显旧 title)。带 hash 的 /assets/* 仍可长缓存。
    no_cache = {"cache-control": "no-cache"}

    @app.get("/")
    async def _spa_root(request: Request):
        return HTMLResponse(
            _render_index_html(index_html, resolve_brand_name(request)), headers=no_cache
        )

    @app.get("/{full_path:path}")
    async def _spa_fallback(full_path: str, request: Request):
        candidate = (dist / full_path).resolve()
        try:
            candidate.relative_to(dist_resolved)
        except ValueError:
            raise HTTPException(status_code=404)
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return HTMLResponse(
            _render_index_html(index_html, resolve_brand_name(request)), headers=no_cache
        )


def create_app(overrides: dict | None = None) -> FastAPI:
    app = FastAPI(title="Supernova Web", version="0.1.0", lifespan=lifespan)
    cfg = get_config()
    app.state.config = cfg

    from .auth.store import AuthStore
    from .auth.session import SessionManager
    from .auth.middleware import AuthMiddleware
    auth_store = AuthStore(str(cfg.auth_db_path))
    auth_store.init_schema()
    app.state.auth_store = auth_store
    app.state.session_manager = SessionManager(auth_store, ttl_hours=cfg.session_ttl_hours)
    app.add_middleware(AuthMiddleware)

    from .components.workspaces_indexer import WorkspacesIndexer
    from .components.git_fetcher import GitFetcher
    from .components.branding_store import BrandingStore
    from .components.multi_repo_config_store import MultiRepoConfigStore
    from .components.repo_manager import RepoManager
    from .components.scan_manager import ScanManager
    from .components.credential_vault import CredentialVault
    from .components.ws_config_store import WsConfigStore
    from .api import fs, members, multi_configs, repos, scan, scans, system_status, users, workspaces, ws_config, branding

    app.state.indexer = WorkspacesIndexer(cfg.workspaces_dir)
    # P3c 阶段 2：per-ws 配置
    app.state.credential_vault = CredentialVault(cfg.master_key_file)
    app.state.ws_config_store = WsConfigStore(cfg.workspaces_dir, app.state.credential_vault)
    app.state.config_store = MultiRepoConfigStore(cfg.configs_dir)
    # 品牌名运行时覆盖存储(设置页改名):branding.json 落盘,system_status 解析优先读。
    app.state.branding_store = BrandingStore(cfg.workspaces_dir)
    git_fetcher = GitFetcher(
        cfg.repos_dir, cfg.gitlab_user, cfg.gitlab_token,
        ws_config_store=app.state.ws_config_store,
    )
    overrides = overrides or {}
    app.state.scan_manager = overrides.get("scan_manager") or ScanManager(
        cfg.workspaces_dir, cfg.repos_dir, app.state.config_store,
        max_concurrent=cfg.max_concurrent, scan_timeout=cfg.scan_timeout,
        ws_config_store=app.state.ws_config_store)
    app.state.repo_manager = overrides.get("repo_manager") or RepoManager(
        cfg.workspaces_dir, git_fetcher, max_concurrent=cfg.repos_max_concurrent_clones)

    from .auth.dependencies import current_user
    _require_auth = [Depends(current_user)]
    app.include_router(workspaces.router, dependencies=_require_auth)
    app.include_router(scans.router, dependencies=_require_auth)
    app.include_router(scans.cross_ws_router, dependencies=_require_auth)
    app.include_router(scan.router, dependencies=_require_auth)
    app.include_router(multi_configs.router, dependencies=_require_auth)
    app.include_router(repos.router, dependencies=_require_auth)
    app.include_router(fs.router, dependencies=_require_auth)
    app.include_router(system_status.router, dependencies=_require_auth)
    app.include_router(members.router, dependencies=_require_auth)
    app.include_router(ws_config.router, dependencies=_require_auth)
    app.include_router(users.router, dependencies=_require_auth)
    # branding:GET 需登录(任意角色可看当前名),PUT 需 admin(route 内 require_admin)。
    app.include_router(branding.router, dependencies=_require_auth)

    from .auth import routes as auth_routes
    app.include_router(auth_routes.router)

    @app.get("/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "git": {
                "binary_available": cfg.git_binary_available,
                "credentials_configured": bool(cfg.gitlab_user and cfg.gitlab_token),
            },
        }

    _mount_frontend(app, cfg)

    return app


app = create_app()


def main() -> None:
    import uvicorn

    cfg = get_config()
    uvicorn.run("supernova_web.app:app", host="0.0.0.0", port=cfg.port, reload=False)
