"""Dedicated tests for AgentBrowserEngine."""

from __future__ import annotations

from pathlib import Path

from supernova_core.services.browser_engine import BrowserEngine
from supernova_core.services.engines.agent_browser_engine import AgentBrowserEngine


# ---------------------------------------------------------------------------
# Engine identity
# ---------------------------------------------------------------------------


class TestAgentBrowserEngineIdentity:
    def test_name_returns_agent_browser(self):
        engine = AgentBrowserEngine()
        assert engine.name == "agent-browser"

    def test_satisfies_browser_engine_protocol(self):
        engine = AgentBrowserEngine()
        assert isinstance(engine, BrowserEngine)


# ---------------------------------------------------------------------------
# Session flag
# ---------------------------------------------------------------------------


class TestAgentBrowserEngineSessionFlag:
    def test_session_flag_format(self):
        """Session flag should be space-separated and include --session."""
        engine = AgentBrowserEngine()
        flag = engine.session_flag("sess-123")
        assert "--session sess-123" in flag

    def test_session_flag_includes_profile_path(self):
        """Session flag must include --profile .agent-browser/profiles/{sid}."""
        engine = AgentBrowserEngine()
        flag = engine.session_flag("my-session")
        assert "--profile .agent-browser/profiles/my-session" in flag

    def test_session_flag_uses_session_id(self):
        engine = AgentBrowserEngine()
        flag = engine.session_flag("abc")
        assert "abc" in flag


# ---------------------------------------------------------------------------
# Commands reference
# ---------------------------------------------------------------------------


class TestAgentBrowserEngineCommandsReference:
    def test_commands_reference_not_empty(self):
        engine = AgentBrowserEngine()
        ref = engine.commands_reference()
        assert isinstance(ref, str)
        assert len(ref) > 0

    def test_commands_reference_mentions_snapshot_and_ref(self):
        """Key agent-browser concepts (snapshot, @ref) should be present."""
        engine = AgentBrowserEngine()
        ref = engine.commands_reference()
        assert "snapshot" in ref.lower()
        assert "@ref" in ref

    def test_commands_reference_no_playwright_references(self):
        """Agent-browser reference should NOT mention 'playwright'."""
        engine = AgentBrowserEngine()
        ref = engine.commands_reference()
        assert "playwright" not in ref.lower()

    def test_commands_reference_lists_state_save_load(self):
        """reference must document state save/load for cross-session auth reuse."""
        engine = AgentBrowserEngine()
        ref = engine.commands_reference()
        assert "state save" in ref
        assert "state load" in ref

    def test_commands_reference_documents_close(self):
        """2026-09-03 xss 40min 事故：reference 必须教 close（回收 session 浏览器进程），
        且显式禁 `close --all`（并行 agent 共机，--all 会连杀其他 agent 的 session）。"""
        engine = AgentBrowserEngine()
        ref = engine.commands_reference()
        assert "--session <session> close" in ref
        assert "close --all" in ref  # 文档需解释为什么不能用它

    def test_commands_reference_has_session_discipline(self):
        """2026-09-03 xss 40min 事故（274 chromium 堆积）：reference 必须带资源纪律——
        禁发明新 session id、多身份用 state save/load 复用同一 session、并发上限 2、
        命令返回空 = 资源耗尽征兆（close 后用原 id 重开，不开新 id）。"""
        engine = AgentBrowserEngine()
        ref = engine.commands_reference()
        # normalize 空白：纪律句跨行时不受换行/缩进影响
        flat = " ".join(ref.lower().split())
        # 禁发明新 session id
        assert "never invent new session" in flat or "do not invent" in flat
        # 多身份切返回 state save/load（同一 session 内）
        assert "state save" in ref and "state load" in ref
        # 并发 session 上限
        assert "at most 2" in flat
        # 返回空 = 资源耗尽的诊断指引（close 后复用原 id 重开）
        assert "empty" in flat
        assert "same session id" in flat


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


class TestAgentBrowserEngineAuth:
    def test_auth_save_command_uses_state_save(self):
        """auth_save_command must emit `state save <path>` (agent-browser native)."""
        engine = AgentBrowserEngine()
        result = engine.auth_save_command("sess-1", "/tmp/auth.json")
        assert result == "state save /tmp/auth.json"

    def test_auth_load_command_uses_state_load(self):
        """auth_load_command must emit `state load <path>`."""
        engine = AgentBrowserEngine()
        result = engine.auth_load_command("sess-1", "/tmp/auth.json")
        assert result == "state load /tmp/auth.json"


# ---------------------------------------------------------------------------
# Config management – write_config
# ---------------------------------------------------------------------------


class TestAgentBrowserEngineWriteConfig:
    def test_write_config_creates_profile_dir(self, tmp_path):
        """Default session creates .agent-browser/profiles/default/."""
        engine = AgentBrowserEngine()
        result = engine.write_config(str(tmp_path))
        assert result["result"] == "wrote"
        profile_dir = Path(result["configPath"])
        assert profile_dir.exists()
        assert ".agent-browser/profiles/default" in str(profile_dir)

    def test_write_config_creates_named_session_dir(self, tmp_path):
        """Named session creates .agent-browser/profiles/{session_id}/."""
        engine = AgentBrowserEngine()
        result = engine.write_config(str(tmp_path), session_id="my-session")
        assert result["result"] == "wrote"
        profile_dir = Path(result["configPath"])
        assert profile_dir.exists()
        assert ".agent-browser/profiles/my-session" in str(profile_dir)

    def test_write_config_skips_existing(self, tmp_path):
        """Writing config twice should be idempotent (skips-existing)."""
        engine = AgentBrowserEngine()
        first = engine.write_config(str(tmp_path), session_id="dup")
        assert first["result"] == "wrote"
        second = engine.write_config(str(tmp_path), session_id="dup")
        assert second["result"] == "skipped-existing"
        assert first["configPath"] == second["configPath"]


# ---------------------------------------------------------------------------
# Config management – cleanup_config
# ---------------------------------------------------------------------------


class TestAgentBrowserEngineCleanupConfig:
    def test_cleanup_config_removes_session_dir(self, tmp_path):
        """Session-specific cleanup removes only that session's profile dir."""
        engine = AgentBrowserEngine()
        engine.write_config(str(tmp_path), session_id="cleanup-me")
        profile_dir = tmp_path / ".agent-browser" / "profiles" / "cleanup-me"
        assert profile_dir.exists()

        engine.cleanup_config(str(tmp_path), session_id="cleanup-me")
        assert not profile_dir.exists()
        # The .agent-browser parent dir should still exist
        assert (tmp_path / ".agent-browser").exists()

    def test_cleanup_config_removes_all_when_no_session(self, tmp_path):
        """No session_id removes the entire .agent-browser/ directory."""
        engine = AgentBrowserEngine()
        engine.write_config(str(tmp_path), session_id="sess-a")
        engine.write_config(str(tmp_path), session_id="sess-b")
        assert (tmp_path / ".agent-browser").exists()

        engine.cleanup_config(str(tmp_path), session_id=None)
        assert not (tmp_path / ".agent-browser").exists()


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


class TestAgentBrowserEngineAvailability:
    def test_check_available_returns_bool(self):
        engine = AgentBrowserEngine()
        result = engine.check_available()
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# Process lifecycle – cleanup_processes
# ---------------------------------------------------------------------------


class TestAgentBrowserEngineCleanupProcesses:
    """cleanup_processes: 优雅 close 先于 pkill 兜底,失败吞掉,session 精准隔离。

    用 monkeypatch 替换模块级 subprocess(raising=False),让 RED 阶段(方法尚
    不存在)干净失败在 AttributeError,而非 subprocess 属性缺失。
    """

    def test_returns_summary_dict_shape(self, monkeypatch):
        """返回 dict 含 closed/killed/errors 三键。"""
        engine = AgentBrowserEngine()
        _record_subprocess(monkeypatch, returncodes=[0])
        result = engine.cleanup_processes(session_ids=["s1"])
        assert set(result.keys()) >= {"closed", "killed", "errors"}

    def test_graceful_close_called_before_pkill(self, monkeypatch):
        """对每个 session 先跑 agent-browser close;close 成功(rc=0)时不 pkill。"""
        engine = AgentBrowserEngine()
        cmds = _record_subprocess(monkeypatch, returncodes=[0])
        engine.cleanup_processes(session_ids=["agent1"])
        joined = " ".join(cmds)
        assert "agent-browser" in joined and "close" in joined  # close 被调
        assert "pkill" not in joined  # close 成功 -> 不 pkill

    def test_pkill_fallback_when_close_fails(self, monkeypatch):
        """close 返回非零 -> 触发 pkill 兜底,匹配 profile 路径(精准隔离)。"""
        engine = AgentBrowserEngine()
        cmds = _record_subprocess(monkeypatch, returncodes=[1])
        engine.cleanup_processes(session_ids=["agent1"])
        joined = " ".join(cmds)
        assert "pkill" in joined
        assert "profiles/agent1" in joined  # 带 profile 路径,不误杀并发扫描

    def test_none_session_ids_uses_close_all(self, monkeypatch):
        """session_ids=None(强退路径)走 close --all。"""
        engine = AgentBrowserEngine()
        cmds = _record_subprocess(monkeypatch, returncodes=[0])
        engine.cleanup_processes(session_ids=None)
        joined = " ".join(cmds)
        assert "close --all" in joined

    def test_errors_swallowed_never_raises(self, monkeypatch):
        """subprocess 抛异常时 cleanup_processes 必须吞掉、填 errors、不 raise。"""
        engine = AgentBrowserEngine()
        _raising_subprocess(monkeypatch)
        result = engine.cleanup_processes(session_ids=["agent1"])
        assert result["errors"]  # 非空错误列表

    def test_only_targeted_sessions_not_others(self, monkeypatch):
        """session_ids=['agent1'] 时 pkill 匹配串不含 agent2。"""
        engine = AgentBrowserEngine()
        cmds = _record_subprocess(monkeypatch, returncodes=[1])
        engine.cleanup_processes(session_ids=["agent1"])
        joined = " ".join(cmds)
        assert "profiles/agent1" in joined
        assert "profiles/agent2" not in joined

    # -- Bug 修复(真机验证发现) -------------------------------------------

    def test_pkill_pattern_has_trailing_space_to_isolate_prefix(self, monkeypatch):
        """Bug1: Chrome pkill pattern 必须以分隔符结尾,隔离 agent-auth vs agent-authz 前缀,
        同时覆盖 identity session 变体(agent-authz-<account_id>,get_identity_session_id)。

        真实 session ID 见 AGENT_SESSION_MAPPING:agent-auth 是 agent-authz 的前缀。
        无分隔时 pkill 'profiles/agent-auth' 会连杀并发 'agent-authz' 的 Chrome。
        Chrome cmdline 里 profiles/{sid} 后跟空格或连字符(identity 变体),故 pattern
        'headless.*profiles/{sid}[- ]' 精准隔离前缀且连带回收 identity 变体。
        2026-09-03 从纯尾随空格升级:旧 pattern 匹配不到 'profiles/agent-authz-alice'
        (authz 后是 '-',不是空格)——authz 多身份扫描的 identity session 连扫描级
        finally 都清不掉(cleanup_engine_configs 只传 AGENT_SESSION_MAPPING 的 base id)。
        """
        engine = AgentBrowserEngine()
        cmds = _record_subprocess(monkeypatch, returncodes=[1])
        engine.cleanup_processes(session_ids=["agent-auth"])
        chrome_pkill = [c for c in cmds if c.startswith("pkill") and "headless" in c]
        assert chrome_pkill, "close 失败应触发 Chrome pkill 兜底"
        assert any(
            c.endswith("profiles/agent-auth[- ]") for c in chrome_pkill
        ), f"Chrome pkill pattern 缺 [- ] 分隔后缀(会误杀 agent-authz/漏杀 identity): {chrome_pkill!r}"

    def test_pkill_pattern_matches_identity_variant_not_longer_prefix(self, monkeypatch):
        """pattern 语义正反例(ERE):base sid 的 pattern 应匹配 identity 变体、
        不匹配更长 base 的其他 agent。"""
        import re

        engine = AgentBrowserEngine()
        cmds = _record_subprocess(monkeypatch, returncodes=[1])
        engine.cleanup_processes(session_ids=["agent-authz"])
        chrome_pkill = [c for c in cmds if c.startswith("pkill") and "headless" in c]
        pattern = chrome_pkill[0].split("pkill -f ")[-1]
        cmdline_suffixes = [
            "--headless --user-data-dir=/repo/.agent-browser/profiles/agent-authz --window-size=1920,1080",
            "--headless --user-data-dir=/repo/.agent-browser/profiles/agent-authz-alice --window-size=1920,1080",
        ]
        for cmdline in cmdline_suffixes:
            assert re.search(pattern, cmdline), (
                f"pattern {pattern!r} 应匹配 {cmdline!r} (含 identity 变体)"
            )
        # 反例:agent-auth(更短前缀)的 Chrome 不被 agent-authz pattern 误杀
        assert not re.search(
            pattern,
            "--headless --user-data-dir=/repo/.agent-browser/profiles/agent-auth --window-size=1920,1080",
        ), "pattern 不应误杀更短前缀的其他 agent"
        # 反例(关键方向):agent-auth 的 pattern 不误杀 agent-authz 的 Chrome
        engine2 = AgentBrowserEngine()
        cmds2 = _record_subprocess(monkeypatch, returncodes=[1])
        engine2.cleanup_processes(session_ids=["agent-auth"])
        pattern2 = [
            c for c in cmds2 if c.startswith("pkill") and "headless" in c
        ][0].split("pkill -f ")[-1]
        assert not re.search(
            pattern2,
            "--headless --user-data-dir=/repo/.agent-browser/profiles/agent-authz --window-size=1920,1080",
        ), "agent-auth pattern 不应误杀并发 agent-authz 的 Chrome"

    def test_no_dead_agent_browser_profile_pattern(self, monkeypatch):
        """Bug2: 删除死代码 pattern1 'agent-browser.*profiles/{sid}'。

        daemon(agent-browser-linux-x64)daemon 化后 cmdline 裸(零参数),
        pattern1 永远匹配 0 个进程——是死代码。删之。
        """
        engine = AgentBrowserEngine()
        cmds = _record_subprocess(monkeypatch, returncodes=[1])
        engine.cleanup_processes(session_ids=["agent-auth"])
        joined = " ".join(cmds)
        assert "agent-browser.*" not in joined, (
            f"死代码 pattern1 'agent-browser.*profiles/...' 仍存在: {joined!r}"
        )

    def test_close_failure_kills_daemon_via_ppid(self, monkeypatch):
        """Bug2: close 失败时沿残留 Chrome PPID 链杀 per-session daemon。

        daemon cmdline 裸,pkill -f 匹配不到;改用 pgrep 拿残留 Chrome PID →
        ps -o ppid= 找父 → 父 comm 以 agent-browser 开头则 kill
        (per-session 不误并发,每个 session 有独立 daemon + 独立 profile 路径的 Chrome)。
        """
        from supernova_core.services.engines import agent_browser_engine as mod

        engine = AgentBrowserEngine()
        cmds = []

        class _R:
            def __init__(self, rc, stdout=""):
                self.returncode = rc
                self.stdout = stdout

        def _fake_run(cmd, *a, **kw):
            joined = " ".join(str(c) for c in cmd)
            cmds.append(joined)
            # 优雅 close 失败
            if joined.startswith("agent-browser") and "close" in joined:
                return _R(1)
            # pgrep 残留 Chrome → 返回 PID 12345
            if cmd[0] == "pgrep":
                return _R(0, "12345\n")
            # ps -o ppid= → 父 999
            if cmd[0] == "ps" and "ppid=" in joined:
                return _R(0, "999\n")
            # ps -o comm= → daemon 二进制名
            if cmd[0] == "ps" and "comm=" in joined:
                return _R(0, "agent-browser-linux-x64")
            # kill 父(daemon)
            if cmd[0] == "kill":
                return _R(0)
            return _R(0)

        class _FakeSub:
            DEVNULL = -3
            PIPE = -4

            run = staticmethod(_fake_run)

        monkeypatch.setattr(mod, "subprocess", _FakeSub, raising=False)
        result = engine.cleanup_processes(session_ids=["agent-auth"])
        joined = " ".join(cmds)
        assert "pgrep" in joined and "headless.*profiles/agent-auth[- ]" in joined
        assert "ppid=" in joined  # PPID 查找
        assert "comm=" in joined  # 父进程名确认(避免误杀非 daemon 父)
        assert "kill" in joined  # 杀 daemon
        assert result.get("killed_daemons"), f"未记录被杀 daemon: {result}"


def _record_subprocess(monkeypatch, returncodes):
    """记录 subprocess.run 收到的命令,可控 returncode。raising=False 让 RED 干净。"""
    from supernova_core.services.engines import agent_browser_engine as mod

    cmds = []

    class _R:
        def __init__(self, rc):
            self.returncode = rc

    it = iter(returncodes)

    class _FakeSub:
        DEVNULL = -3  # 占位,匹配 subprocess.DEVNULL 用法

        @staticmethod
        def run(cmd, *a, **kw):
            cmds.append(" ".join(str(c) for c in cmd))
            try:
                rc = next(it)
            except StopIteration:
                rc = 0
            return _R(rc)

    monkeypatch.setattr(mod, "subprocess", _FakeSub, raising=False)
    return cmds


def _raising_subprocess(monkeypatch):
    """subprocess.run 抛异常的 fake(测 errors 吞掉路径)。"""
    from supernova_core.services.engines import agent_browser_engine as mod

    class _FakeSub:
        DEVNULL = -3  # 占位,匹配 subprocess.DEVNULL 用法

        @staticmethod
        def run(cmd, *a, **kw):
            raise FileNotFoundError("agent-browser vanished")

    monkeypatch.setattr(mod, "subprocess", _FakeSub, raising=False)
