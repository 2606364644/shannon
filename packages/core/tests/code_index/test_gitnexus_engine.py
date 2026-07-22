import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
from supernova_core.code_index.gitnexus_engine import GitNexusEngine, GitNexusError


@pytest.fixture(autouse=True)
def _isolate_gitnexus_home(monkeypatch, tmp_path):
    """隔离 ~/.gitnexus：flock lock + registry 读写不触碰真实 home（每测试独立 tmp）。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)


def _subcommands(mock_run):
    """提取所有 gitnexus 子调用中的子命令名 (cmd[1], 如 'analyze'/'index')。"""
    return [c[0][0][1] for c in mock_run.call_args_list]


def _fake_proc(*, communicate_return=(b"{}", b""), returncode=0, communicate_side_effect=None):
    """构造 _run_cli_async 期望的 fake subprocess proc（asyncio.create_subprocess_exec 返回值）。"""
    proc = MagicMock()
    proc.returncode = returncode
    if communicate_side_effect is not None:
        proc.communicate = AsyncMock(side_effect=communicate_side_effect)
    else:
        proc.communicate = AsyncMock(return_value=communicate_return)
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


class TestGitNexusEngineCLI:
    def test_ensure_indexed_runs_analyze(self, tmp_path):
        engine = GitNexusEngine(tmp_path)
        with patch("supernova_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            engine.ensure_indexed()
            assert "analyze" in _subcommands(mock_run)

    def test_ensure_indexed_skips_analyze_but_registers_registry_when_indexed(self, tmp_path):
        """Regression: .gitnexus 已存在时跳过 analyze,但仍须 `gitnexus index` 注册进
        全局 registry —— 否则 `gitnexus mcp` 启动后发现 0 个仓, 查询死锁
        (2026-07-08 kol_mapping_service 卡 35min 的真根因)。
        """
        (tmp_path / ".gitnexus").mkdir()
        engine = GitNexusEngine(tmp_path)
        with patch("supernova_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            engine.ensure_indexed()
            cmds = _subcommands(mock_run)
            assert "analyze" not in cmds
            assert "index" in cmds

    def test_ensure_indexed_registers_registry_after_fresh_analyze(self, tmp_path):
        """fresh analyze 后必须 `gitnexus index` 注册进全局 registry (幂等)。"""
        engine = GitNexusEngine(tmp_path)
        with patch("supernova_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            engine.ensure_indexed()
            cmds = _subcommands(mock_run)
            assert "analyze" in cmds
            assert "index" in cmds

    def test_ensure_indexed_returns_failure_when_registry_register_fails(self, tmp_path):
        """`gitnexus index` 失败 → success=False (避免 registry 未注册进 MCP 死锁)。"""
        (tmp_path / ".gitnexus").mkdir()
        engine = GitNexusEngine(tmp_path)

        def fake_run(cmd, *a, **kw):
            if cmd[1] == "index":
                return MagicMock(returncode=1, stdout="", stderr="registry write failed")
            return MagicMock(returncode=0, stdout="{}", stderr="")

        with patch("supernova_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
            mock_run.side_effect = fake_run
            result = engine.ensure_indexed()
            assert result.success is False
            assert result.error_message is not None

    def test_get_context_returns_dict(self, tmp_path):
        engine = GitNexusEngine(tmp_path)
        context_data = {"outgoing": {"calls": []}, "incoming": {}, "processes": []}
        with patch("supernova_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=json.dumps(context_data), stderr=""
            )
            result = engine.get_context("my_function")
            assert result == context_data
            cmd = mock_run.call_args[0][0]
            assert "context" in cmd
            assert "--name" in cmd

    def test_cli_error_raises_gitnexus_error(self, tmp_path):
        engine = GitNexusEngine(tmp_path)
        with patch("supernova_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error msg")
            result = engine.ensure_indexed()
            assert result.success is False
            assert "error msg" in result.error_message

    def test_timeout_returns_failed_result(self, tmp_path):
        import subprocess
        engine = GitNexusEngine(tmp_path, timeout=1)
        with patch("supernova_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("gitnexus", 1)
            result = engine.ensure_indexed()
            assert result.success is False
            assert "timed out" in result.error_message

    def test_is_available_checks_command(self, tmp_path):
        engine = GitNexusEngine(tmp_path)
        with patch("supernova_core.code_index.gitnexus_engine.shutil.which") as mock_which:
            mock_which.return_value = "/usr/bin/gitnexus"
            assert engine.is_available() is True

    def test_is_available_returns_false_when_missing(self, tmp_path):
        engine = GitNexusEngine(tmp_path)
        with patch("supernova_core.code_index.gitnexus_engine.shutil.which") as mock_which:
            mock_which.return_value = None
            assert engine.is_available() is False

    def test_ensure_indexed_force_rebuilds(self, tmp_path):
        """ensure_indexed(force=True) runs analyze --force even when .gitnexus/ exists,
        and still registers the repo into the global registry afterwards."""
        (tmp_path / ".gitnexus").mkdir()  # existing index
        engine = GitNexusEngine(tmp_path)
        with patch("supernova_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            engine.ensure_indexed(force=True)
            analyze_calls = [c[0][0] for c in mock_run.call_args_list if c[0][0][1] == "analyze"]
            assert analyze_calls, "force 应触发 analyze"
            assert "--force" in analyze_calls[0]
            assert "index" in _subcommands(mock_run)

    def test_ensure_indexed_returns_index_result(self, tmp_path):
        """ensure_indexed returns an IndexResult dataclass."""
        engine = GitNexusEngine(tmp_path)
        with patch("supernova_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            result = engine.ensure_indexed()
            assert result.success is True
            assert result.is_stale is False

    def test_ensure_indexed_failure_returns_failed_result(self, tmp_path):
        """ensure_indexed returns failed IndexResult on error."""
        engine = GitNexusEngine(tmp_path)
        with patch("supernova_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error msg")
            result = engine.ensure_indexed()
            assert result.success is False
            assert result.error_message is not None

    def test_check_stale_no_git_repo(self, tmp_path):
        """check_stale returns False when no .git exists."""
        engine = GitNexusEngine(tmp_path)
        with patch("supernova_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
            result = engine.check_stale()
            # No git repo → can't determine staleness → assume not stale
            assert result is False

    def test_check_stale_fresh_index(self, tmp_path):
        """check_stale returns False when index is newer than latest commit."""
        import time
        (tmp_path / ".git").mkdir()
        (tmp_path / ".gitnexus").mkdir()
        engine = GitNexusEngine(tmp_path)
        with patch("supernova_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
            # git log returns a recent timestamp, .gitnexus mtime is newer
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=str(int(time.time())),  # current timestamp
                stderr="",
            )
            result = engine.check_stale()
            assert result is False

    def test_check_stale_stale_index(self, tmp_path):
        """check_stale returns True when index is older than latest commit."""
        import time
        (tmp_path / ".git").mkdir()
        (tmp_path / ".gitnexus").mkdir()
        engine = GitNexusEngine(tmp_path)
        with patch("supernova_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
            # git log returns a very recent timestamp (future)
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=str(int(time.time()) + 10000),  # future timestamp
                stderr="",
            )
            result = engine.check_stale()
            assert result is True


class TestRegistryConcurrencyProtection:
    """gitnexus index 并发写 registry.json 的 ENOENT race 缓解（2026-07-22
    NodeGoat_1784743576 + 1784741574 并发 scan → ActivityError → scan failed）。

    根因：GitNexus 1.6.8 ``writeRegistry`` 用固定 ``registry.json.tmp`` + 无 flock，
    多 scan/activity 并发 ``gitnexus index`` 写同一全局 registry → rename 互相踩 → ENOENT。
    缓解：ensure_indexed 用 flock 跨进程串行 index + index 失败幂等检查（repo 已注册则成功）。
    """

    @staticmethod
    def _seed_registry(home: Path, repo_path: Path) -> None:
        reg = home / ".gitnexus"
        reg.mkdir(parents=True, exist_ok=True)
        (reg / "registry.json").write_text(
            json.dumps([{"name": "x", "path": str(repo_path)}]), encoding="utf-8")

    def test_is_repo_registered_true_when_in_registry(self, monkeypatch, tmp_path):
        self._seed_registry(tmp_path, tmp_path)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert GitNexusEngine(tmp_path)._is_repo_registered() is True

    def test_is_repo_registered_false_when_other_repo(self, monkeypatch, tmp_path):
        self._seed_registry(tmp_path, tmp_path / "other-repo")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert GitNexusEngine(tmp_path)._is_repo_registered() is False

    def test_is_repo_registered_false_when_registry_absent(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert GitNexusEngine(tmp_path)._is_repo_registered() is False

    def test_is_repo_registered_false_when_registry_corrupt(self, monkeypatch, tmp_path):
        reg = tmp_path / ".gitnexus"
        reg.mkdir(parents=True)
        (reg / "registry.json").write_text("not json", encoding="utf-8")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert GitNexusEngine(tmp_path)._is_repo_registered() is False

    def test_ensure_indexed_idempotent_when_index_fails_but_registered(self, monkeypatch, tmp_path):
        """index 失败 + repo 已注册 → success=True（race 下另一进程已注册本 repo）。"""
        (tmp_path / ".gitnexus").mkdir()
        self._seed_registry(tmp_path, tmp_path)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        def fake_run(cmd, *a, **kw):
            if cmd[1] == "index":
                return MagicMock(returncode=1, stdout="", stderr="ENOENT rename race")
            return MagicMock(returncode=0, stdout="{}", stderr="")

        engine = GitNexusEngine(tmp_path)
        with patch("supernova_core.code_index.gitnexus_engine.subprocess.run", side_effect=fake_run):
            result = engine.ensure_indexed()
        assert result.success is True  # 幂等：已注册视为成功

    def test_registry_lock_is_mutually_exclusive(self, monkeypatch, tmp_path):
        """flock 锁互斥：一线程持锁时，另一非阻塞 flock 拿不到（串行化 gitnexus index）。"""
        import fcntl
        import os
        import threading
        import time

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        engine = GitNexusEngine(tmp_path)
        inside = threading.Event()

        def holder():
            with engine._registry_lock():
                inside.set()
                time.sleep(0.15)
            inside.clear()

        t = threading.Thread(target=holder)
        t.start()
        try:
            deadline = time.time() + 1.0
            while not inside.is_set() and time.time() < deadline:
                time.sleep(0.005)
            assert inside.is_set(), "holder 未持锁"
            # 主线程非阻塞尝试应被拒（BlockingIOError）
            fd = os.open(str(engine._registry_lock_path()), os.O_CREAT | os.O_RDWR)
            try:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(fd)
        finally:
            t.join()

    @pytest.mark.asyncio
    async def test_ensure_indexed_async_idempotent_when_index_fails_but_registered(self, monkeypatch, tmp_path):
        """async 版幂等：index 失败 + repo 已注册 → success=True。"""
        (tmp_path / ".gitnexus").mkdir()
        self._seed_registry(tmp_path, tmp_path)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        proc = _fake_proc(communicate_return=(b"", b"ENOENT rename race"), returncode=1)
        engine = GitNexusEngine(tmp_path)
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            result = await engine.ensure_indexed_async()
        assert result.success is True


class TestGitNexusEngineAsyncCLI:
    """async 版 (_run_cli_async / ensure_indexed_async): cancel 时能 kill 子进程，
    消除 run_code_index 阻塞 event loop 导致 Ctrl+C 不可取消的根因。"""

    @pytest.mark.asyncio
    async def test_ensure_indexed_async_runs_analyze(self, tmp_path):
        engine = GitNexusEngine(tmp_path)
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=_fake_proc())) as mock_exec:
            result = await engine.ensure_indexed_async()
        assert result.success is True
        cmds = [c.args[1] for c in mock_exec.call_args_list]
        assert "analyze" in cmds and "index" in cmds

    @pytest.mark.asyncio
    async def test_ensure_indexed_async_skips_analyze_but_registers(self, tmp_path):
        (tmp_path / ".gitnexus").mkdir()
        engine = GitNexusEngine(tmp_path)
        with patch(
            "asyncio.create_subprocess_exec",
            AsyncMock(return_value=_fake_proc(communicate_return=(b"", b""))),
        ) as mock_exec:
            await engine.ensure_indexed_async()
        cmds = [c.args[1] for c in mock_exec.call_args_list]
        assert "analyze" not in cmds
        assert "index" in cmds

    @pytest.mark.asyncio
    async def test_run_cli_async_nonzero_returncode_raises(self, tmp_path):
        engine = GitNexusEngine(tmp_path)
        proc = _fake_proc(communicate_return=(b"", b"error msg"), returncode=1)
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            with pytest.raises(GitNexusError) as ei:
                await engine._run_cli_async("analyze", str(tmp_path))
        assert "error msg" in str(ei.value)

    @pytest.mark.asyncio
    async def test_run_cli_async_timeout_kills_proc(self, tmp_path):
        async def hang():
            await asyncio.sleep(10)
            return (b"", b"")

        engine = GitNexusEngine(tmp_path, timeout=0.01)
        proc = _fake_proc(communicate_side_effect=hang)
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            with pytest.raises(GitNexusError) as ei:
                await engine._run_cli_async("analyze", str(tmp_path))
        assert "timed out" in str(ei.value)
        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_cli_async_cancel_kills_proc(self, tmp_path):
        """核心：cancel 必杀子进程（消除 300s event loop 阻塞，让 Ctrl+C 可取消）。"""
        engine = GitNexusEngine(tmp_path)
        proc = _fake_proc(communicate_side_effect=asyncio.CancelledError)
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            with pytest.raises(asyncio.CancelledError):
                await engine._run_cli_async("analyze", str(tmp_path))
        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_cli_async_file_not_found_raises(self, tmp_path):
        engine = GitNexusEngine(tmp_path)
        with patch("asyncio.create_subprocess_exec", AsyncMock(side_effect=FileNotFoundError)):
            with pytest.raises(GitNexusError) as ei:
                await engine._run_cli_async("analyze", str(tmp_path))
        msg = str(ei.value).lower()
        assert "not found" in msg or "install" in msg
