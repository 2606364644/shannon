"""上传 ZIP 添加仓库（RepoManager.upload_zip）：安全解压 + git 快照化。

上传模型（spec 2026-08-24）：zip 落到临时文件 → 后台 task 安全解压（防 zip slip /
zip bomb）→ 剥单一顶层包裹目录 → 清 .git/hooks（防 commit hooks 执行）→ 无 .git 则
git init 单 commit 快照化（扫描链路 preflight/GitNexus 全兼容）→ meta kind=upload。
"""
import asyncio
import io
import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from supernova_web.components.repo_manager import RepoManager, TooManyClones, UploadTooLarge

WS = "ws1"


def _rm(tmp_path, monkeypatch) -> RepoManager:
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_path))
    monkeypatch.setenv("SUPERNOVA_REPOS_DIR", str(tmp_path / "repos"))
    from supernova_web.config import get_config; get_config.cache_clear()
    from supernova_web.components.git_fetcher import GitFetcher
    cfg = get_config()
    ws_dir = cfg.workspaces_dir
    (ws_dir / WS).mkdir(parents=True, exist_ok=True)
    return RepoManager(ws_dir, GitFetcher(cfg.repos_dir, "u", "t"), max_concurrent=3)


def _repos_base(tmp_path) -> Path:
    return tmp_path / "workspaces" / WS / "repos"


def _zip_bytes(files: dict[str, str | bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, content in files.items():
            zf.writestr(path, content)
    return buf.getvalue()


def _write_zip(tmp_path, filename: str, files: dict[str, str | bytes]) -> Path:
    z = tmp_path / filename
    z.write_bytes(_zip_bytes(files))
    return z


async def _wait_job(rm: RepoManager, ws: str, name: str) -> None:
    task = rm._jobs.get((ws, name))
    if task:
        await asyncio.wait_for(asyncio.shield(task), timeout=60)


def _read_meta(tmp_path, name: str) -> dict:
    return json.loads((_repos_base(tmp_path) / name / ".supernova-repo.json").read_text())


async def _git(repo: Path, *argv: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(repo), *argv,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    assert proc.returncode == 0, err.decode(errors="replace")
    return out.decode(errors="replace")


# ---- 正常路径 ----

@pytest.mark.asyncio
async def test_upload_no_git_creates_snapshot(tmp_path, monkeypatch):
    """无 .git 的 zip → git init + add -A + 单 commit 快照 + meta kind=upload ready。"""
    rm = _rm(tmp_path, monkeypatch)
    z = _write_zip(tmp_path, "app.zip", {"src/main.py": "print(1)\n", "README.md": "hi"})
    name = await rm.upload_zip(WS, z, "app.zip", None, None)
    assert name == "app"
    await _wait_job(rm, WS, "app")
    repo = _repos_base(tmp_path) / "app"
    assert (repo / "src" / "main.py").read_text() == "print(1)\n"
    assert (repo / ".git").is_dir()  # 快照化后是真 git 仓（preflight/GitNexus 兼容）
    meta = _read_meta(tmp_path, "app")
    assert meta["state"] == "ready"
    assert meta["source"]["kind"] == "upload"
    assert meta["source"]["commit"]
    assert meta["source"]["branch"]
    assert meta["size_bytes"] > 0
    # 快照内容真入 git 索引
    files = (await _git(repo, "ls-files")).split()
    assert "src/main.py" in files and "README.md" in files
    # 列表可见（走有 .git 分支）
    assert any(r["name"] == "app" and r["state"] == "ready" for r in rm.list_repos(WS))


@pytest.mark.asyncio
async def test_upload_strips_single_toplevel_wrapper(tmp_path, monkeypatch):
    """zip 顶层是单一目录（repo-main/ 包裹）→ 内容上移一层。"""
    rm = _rm(tmp_path, monkeypatch)
    z = _write_zip(tmp_path, "app.zip",
                   {"app-main/src/a.py": "x", "app-main/README.md": "y"})
    await rm.upload_zip(WS, z, "app.zip", None, None)
    await _wait_job(rm, WS, "app")
    repo = _repos_base(tmp_path) / "app"
    assert (repo / "src" / "a.py").exists()   # 不是 app/app-main/src/a.py
    assert not (repo / "app-main").exists()


@pytest.mark.asyncio
async def test_upload_with_git_dir_kept_and_hooks_removed(tmp_path, monkeypatch):
    """zip 自带 .git → 保留真实历史（HEAD 不变）；.git/hooks 一律删除（commit hooks
    是解压内容的任意代码执行面）。"""
    src = tmp_path / "src-repo"
    src.mkdir()
    (src / "code.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", src, "init", "-q"], check=True)
    subprocess.run(["git", "-C", src, "add", "-A"], check=True)
    subprocess.run(["git", "-C", src, "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "-m", "init"], check=True)
    head_before = subprocess.run(["git", "-C", src, "rev-parse", "HEAD"],
                                 capture_output=True, text=True, check=True).stdout.strip()
    (src / ".git" / "hooks" / "pre-commit").write_text("#!/bin/sh\necho evil\n")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for p in sorted(src.rglob("*")):
            zf.write(p, p.relative_to(src).as_posix())
    z = tmp_path / "app.zip"
    z.write_bytes(buf.getvalue())

    rm = _rm(tmp_path, monkeypatch)
    await rm.upload_zip(WS, z, "app.zip", None, None)
    await _wait_job(rm, WS, "app")
    repo = _repos_base(tmp_path) / "app"
    assert (repo / ".git").is_dir()
    assert not (repo / ".git" / "hooks").exists()  # hooks 被清
    head_after = (await _git(repo, "rev-parse", "HEAD")).strip()
    assert head_after == head_before  # 不重写历史
    meta = _read_meta(tmp_path, "app")
    assert meta["state"] == "ready"
    assert meta["source"]["kind"] == "upload"
    assert meta["source"]["commit"] == head_before


@pytest.mark.asyncio
async def test_upload_with_git_dir_infers_real_source(tmp_path, monkeypatch):
    """zip 自带 .git（remote origin + 真实分支）→ source 字段语义与 clone 呈现一致：
    url=远端地址（凭据剥净）、branch=真实分支名；kind 仍 upload（静态快照，不可
    pull/checkout——上传仓凭据未进 ws auth，可变性语义不动）。
    回归：曾因 _infer_from_git 解包反序（(url, branch) 被接成 (branch, _url)），
    branch 落成 gitlab 地址、url 落 None——来源列显「上传」、分支列显地址。"""
    src = tmp_path / "src-repo"
    src.mkdir()
    (src / "code.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", src, "init", "-q", "-b", "feature/x"], check=True)
    subprocess.run(["git", "-C", src, "add", "-A"], check=True)
    subprocess.run(["git", "-C", src, "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "-m", "init"], check=True)
    # remote URL 带用户本地凭据：落盘必须剥净（与 clone/迁移路径同一铁律）
    subprocess.run(["git", "-C", src, "remote", "add", "origin",
                    "https://user:tok@gitlab.example.com/grp/app.git"], check=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for p in sorted(src.rglob("*")):
            zf.write(p, p.relative_to(src).as_posix())
    z = tmp_path / "app.zip"
    z.write_bytes(buf.getvalue())

    rm = _rm(tmp_path, monkeypatch)
    await rm.upload_zip(WS, z, "app.zip", None, None)
    await _wait_job(rm, WS, "app")
    srcm = _read_meta(tmp_path, "app")["source"]
    assert srcm["kind"] == "upload"
    assert srcm["url"] == "https://gitlab.example.com/grp/app.git"
    assert srcm["branch"] == "feature/x"
    assert srcm["commit"]


@pytest.mark.asyncio
async def test_upload_with_group(tmp_path, monkeypatch):
    """group=... 落 repos/<group>/<name>，返回 group/repo。"""
    rm = _rm(tmp_path, monkeypatch)
    z = _write_zip(tmp_path, "app.zip", {"a.py": "x"})
    name = await rm.upload_zip(WS, z, "app.zip", None, group="g")
    assert name == "g/app"
    await _wait_job(rm, WS, "g/app")
    assert (_repos_base(tmp_path) / "g" / "app" / ".git").is_dir()


@pytest.mark.asyncio
async def test_upload_custom_name_and_events(tmp_path, monkeypatch):
    """显式 name 覆盖 zip 文件名；进度事件落 clone.ndjson（复用 events SSE 管道）。"""
    rm = _rm(tmp_path, monkeypatch)
    z = _write_zip(tmp_path, "app.zip", {"a.py": "x"})
    name = await rm.upload_zip(WS, z, "app.zip", name="custom", group=None)
    assert name == "custom"
    await _wait_job(rm, WS, "custom")
    ndjson = (_repos_base(tmp_path) / "custom" / "clone.ndjson").read_text()
    assert '"phase": "extracting"' in ndjson
    assert '"clone_end"' in ndjson and '"ready"' in ndjson


@pytest.mark.asyncio
async def test_upload_with_git_dir_lists_and_checks_out_local_branches(tmp_path, monkeypatch):
    """zip 带 .git 上传仓：本地 refs 完整（zip 打包自完整 clone）——list_branches
    枚举本地分支（不走 ls-remote，上传仓无凭据问不了远端），checkout 纯本地切换
    （不 fetch origin），meta branch/commit 随切换更新。"""
    src = tmp_path / "src-repo"
    src.mkdir()
    (src / "code.py").write_text("main\n")
    subprocess.run(["git", "-C", src, "init", "-q", "-b", "main"], check=True)
    subprocess.run(["git", "-C", src, "add", "-A"], check=True)
    subprocess.run(["git", "-C", src, "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "-m", "m1"], check=True)
    subprocess.run(["git", "-C", src, "checkout", "-q", "-b", "feature/x"], check=True)
    (src / "code.py").write_text("feature\n")
    subprocess.run(["git", "-C", src, "add", "-A"], check=True)
    subprocess.run(["git", "-C", src, "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "-m", "m2"], check=True)
    subprocess.run(["git", "-C", src, "checkout", "-q", "main"], check=True)
    feat_head = subprocess.run(["git", "-C", src, "rev-parse", "feature/x"],
                               capture_output=True, text=True, check=True).stdout.strip()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for p in sorted(src.rglob("*")):
            zf.write(p, p.relative_to(src).as_posix())
    z = tmp_path / "app.zip"
    z.write_bytes(buf.getvalue())

    rm = _rm(tmp_path, monkeypatch)
    await rm.upload_zip(WS, z, "app.zip", None, None)
    await _wait_job(rm, WS, "app")

    # 本地分支枚举：两条都在，无远端也不报错（快照仓同理，只是列表只有快照分支）
    branches = await rm.list_branches(WS, "app")
    assert set(branches) == {"main", "feature/x"}

    # 纯本地 checkout：工作树内容 / meta 同步
    await rm.checkout(WS, "app", "feature/x")
    repo = _repos_base(tmp_path) / "app"
    assert (repo / "code.py").read_text() == "feature\n"
    meta = _read_meta(tmp_path, "app")
    assert meta["source"]["branch"] == "feature/x"
    assert meta["source"]["commit"] == feat_head

    # 本地没有的分支 → ValueError（与 clone 侧「分支不存在」语义一致）
    with pytest.raises(ValueError, match="分支不存在"):
        await rm.checkout(WS, "app", "nope")


# ---- 校验 / 安全 ----

@pytest.mark.asyncio
async def test_upload_rejects_non_zip_extension(tmp_path, monkeypatch):
    rm = _rm(tmp_path, monkeypatch)
    z = _write_zip(tmp_path, "app.tar.gz", {"a.py": "x"})
    with pytest.raises(ValueError, match="zip"):
        await rm.upload_zip(WS, z, "app.tar.gz", None, None)


@pytest.mark.asyncio
async def test_upload_rejects_empty_zip(tmp_path, monkeypatch):
    rm = _rm(tmp_path, monkeypatch)
    z = _write_zip(tmp_path, "app.zip", {})
    name = await rm.upload_zip(WS, z, "app.zip", None, None)
    await _wait_job(rm, WS, name)
    assert _read_meta(tmp_path, "app")["state"] == "failed"


@pytest.mark.asyncio
async def test_upload_rejects_zip_slip(tmp_path, monkeypatch):
    """条目名含 ../ 必须拒绝：落 failed meta，绝不在 repos 根外/根内解出越界文件。"""
    rm = _rm(tmp_path, monkeypatch)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil.txt", "pwn")
    z = tmp_path / "app.zip"
    z.write_bytes(buf.getvalue())
    name = await rm.upload_zip(WS, z, "app.zip", None, None)
    await _wait_job(rm, WS, name)
    meta = _read_meta(tmp_path, "app")
    assert meta["state"] == "failed"
    assert "越界" in meta["last_error"] or "非法" in meta["last_error"]
    assert not (_repos_base(tmp_path) / "evil.txt").exists()
    assert not (_repos_base(tmp_path).parent / "evil.txt").exists()
    # 失败不留临时解压目录（repos 根下的 .upload-* 全清理）
    assert not [d for d in _repos_base(tmp_path).iterdir() if d.name.startswith(".upload")]


@pytest.mark.asyncio
async def test_upload_rejects_zip_bomb(tmp_path, monkeypatch):
    """解压总大小超上限 → failed（上限用实例属性压小触发）。"""
    rm = _rm(tmp_path, monkeypatch)
    monkeypatch.setattr(rm, "MAX_EXTRACT_BYTES", 100)
    z = _write_zip(tmp_path, "app.zip", {"big.txt": "a" * 500})
    name = await rm.upload_zip(WS, z, "app.zip", None, None)
    await _wait_job(rm, WS, name)
    assert _read_meta(tmp_path, "app")["state"] == "failed"
    repo = _repos_base(tmp_path) / "app"
    assert not (repo / "big.txt").exists() or (repo / "big.txt").stat().st_size <= 100


@pytest.mark.asyncio
async def test_upload_rejects_too_large_zip(tmp_path, monkeypatch):
    rm = _rm(tmp_path, monkeypatch)
    monkeypatch.setattr(rm, "MAX_UPLOAD_ZIP_BYTES", 10)
    z = _write_zip(tmp_path, "app.zip", {"big.txt": "a" * 500})
    with pytest.raises(UploadTooLarge):
        await rm.upload_zip(WS, z, "app.zip", None, None)


@pytest.mark.asyncio
async def test_upload_name_conflict(tmp_path, monkeypatch):
    rm = _rm(tmp_path, monkeypatch)
    (_repos_base(tmp_path) / "app").mkdir(parents=True)
    z = _write_zip(tmp_path, "app.zip", {"a.py": "x"})
    with pytest.raises(ValueError, match="已存在"):
        await rm.upload_zip(WS, z, "app.zip", None, None)


@pytest.mark.asyncio
async def test_upload_normalizes_and_rejects_bad_filename(tmp_path, monkeypatch):
    """multipart filename 可带路径分量（部分客户端发全路径）——取 basename 规范化；
    basename 后仍非法（``\\``、``.`` 段等）必须 ValueError。"""
    rm = _rm(tmp_path, monkeypatch)
    z = _write_zip(tmp_path, "app.zip", {"a.py": "x"})
    # 先测拒绝路径（校验在 task 启动前抛，zip 未被消费/删除）
    for evil in ("..\\evil.zip", "..zip"):
        with pytest.raises(ValueError):
            await rm.upload_zip(WS, z, evil, None, None)
    # 再测规范化：路径分量被剥，最终仓库名无 /（成功路径 zip 被 task 消费后删除）
    name = await rm.upload_zip(WS, z, "a/b.zip", None, None)
    assert name == "b"


@pytest.mark.asyncio
async def test_upload_concurrent_limit(tmp_path, monkeypatch):
    rm = _rm(tmp_path, monkeypatch)
    rm._max_concurrent = 1
    rm._jobs[(WS, "busy")] = asyncio.create_task(asyncio.sleep(10))
    z = _write_zip(tmp_path, "app.zip", {"a.py": "x"})
    with pytest.raises(TooManyClones):
        await rm.upload_zip(WS, z, "app.zip", None, None)


@pytest.mark.asyncio
async def test_upload_zip_payload_is_not_git_repo_still_scannable_shape(tmp_path, monkeypatch):
    """zip 内夹带 .supernova-repo.json / clone.ndjson 伪造 meta/事件 → 解压后剔除，
    最终 meta/事件只来自上传流程本身。"""
    rm = _rm(tmp_path, monkeypatch)
    z = _write_zip(tmp_path, "app.zip", {
        "a.py": "x",
        ".supernova-repo.json": json.dumps({"state": "ready", "source": {"kind": "git"}}),
        "clone.ndjson": '{"forged": true}\n',
    })
    name = await rm.upload_zip(WS, z, "app.zip", None, None)
    await _wait_job(rm, WS, name)
    meta = _read_meta(tmp_path, "app")
    assert meta["source"]["kind"] == "upload"
    ndjson = (_repos_base(tmp_path) / "app" / "clone.ndjson").read_text()
    assert "forged" not in ndjson
