"""BrowserEngine Protocol and BrowserEngineFactory for dual browser engine support.

Provides an abstract interface so that PlaywrightEngine and AgentBrowserEngine
can be used interchangeably by the rest of shannon-py.
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable


@runtime_checkable
class BrowserEngine(Protocol):
    """Protocol defining the contract for a browser engine backend.

    Each concrete engine (PlaywrightEngine, AgentBrowserEngine) must implement
    every method / property listed here so the rest of shannon-py can treat
    them uniformly.
    """

    @property
    def name(self) -> str:
        """Engine identifier string, e.g. ``'playwright'`` or ``'agent-browser'``."""
        ...

    @property
    def cli_binary(self) -> str:
        """Name of the CLI binary to look up on PATH, e.g. ``'playwright-cli'``.

        Distinct from ``name`` (the registry identifier): playwright registers as
        ``'playwright'`` but its binary is ``'playwright-cli'``.
        """
        ...

    def session_flag(self, session_id: str) -> str:
        """Return the CLI flag string for session isolation.

        For example, Playwright returns ``--session <session_id>``
        while AgentBrowser might return a different flag format.
        """
        ...

    def commands_reference(self) -> str:
        """Return engine-specific command reference text for prompt injection.

        This text is injected into the LLM prompt so the model knows which
        CLI commands are available and how to use them.
        """
        ...

    def auth_save_command(self, session_id: str, path: str) -> str:
        """Return the CLI command string that saves auth state for *session_id* to *path*."""
        ...

    def auth_load_command(self, session_id: str, path: str) -> str:
        """Return the CLI command string that loads saved auth state for *session_id* from *path*."""
        ...

    def write_config(
        self,
        source_dir: str,
        session_id: str | None = None,
    ) -> dict:
        """Write engine config files under *source_dir*.

        Returns ``{'result': 'wrote'|'skipped-existing', 'configPath': str}``.
        """
        ...

    def cleanup_config(
        self,
        source_dir: str,
        session_id: str | None = None,
    ) -> None:
        """Remove engine config files and state for *session_id*.

        If *session_id* is ``None``, removes all engine artifacts.
        """
        ...

    def cleanup_processes(
        self,
        source_dir: str | None = None,
        session_ids: list[str] | None = None,
    ) -> dict:
        """Best-effort 回收 engine 拉起的浏览器进程。

        优先优雅关闭(engine CLI 的 close 命令),失败/残留再 pkill 兜底。
        清理失败一律 log + 吞(不反过来崩扫描)。

        - session_ids 非空:只清理这些 session(精准隔离,不误杀并发扫描)。
        - session_ids 为 None:清理 source_dir profile 下全部 session
          (_force_exit 强退路径用,粗粒度兜底)。

        返回 ``{"closed": [...], "killed": [...], "errors": [...]}`` 摘要。
        """
        ...

    def check_available(self) -> bool:
        """Check whether the engine CLI is installed and usable."""
        ...


class BrowserEngineFactory:
    """Registry and factory for ``BrowserEngine`` implementations.

    Usage::

        BrowserEngineFactory.register("playwright", PlaywrightEngine)
        engine = BrowserEngineFactory.get_engine("playwright")
    """

    _engines: dict[str, type] = {}

    @classmethod
    def register(cls, name: str, engine_class: type) -> None:
        """Register an engine class under *name*.

        Raises ``ValueError`` if *name* is already registered.
        """
        if name in cls._engines:
            raise ValueError(
                f"Browser engine '{name}' is already registered as {cls._engines[name]!r}"
            )
        cls._engines[name] = engine_class

    @classmethod
    def get_engine(cls, engine_name: str) -> BrowserEngine:
        """Instantiate and return the engine registered under *engine_name*.

        Raises ``KeyError`` if no engine has been registered with that name.
        """
        if engine_name not in cls._engines:
            raise KeyError(
                f"No browser engine registered as '{engine_name}'. "
                f"Available: {list(cls._engines.keys())}"
            )
        return cls._engines[engine_name]()

    @classmethod
    def resolve_name(cls, config_path: str | None = None) -> str:
        """Resolve the effective browser engine name.

        Priority (matches ``config/parser.py`` env-override semantics):
        1. ``SHANNON_BROWSER_ENGINE`` env var (highest)
        2. ``browser_engine`` field parsed from *config_path* (when provided)
        3. Default ``"agent-browser"``
        """
        env_engine = os.environ.get("SHANNON_BROWSER_ENGINE")
        if env_engine:
            return env_engine.strip()
        if config_path:
            try:
                from shannon_core.config.parser import parse_config

                cfg = parse_config(config_path)
                if cfg.browser_engine:
                    return cfg.browser_engine
            except Exception:
                # Config unreadable → fall through to default rather than crash
                # the preflight gate. The workflow's hard check_available()
                # will surface real config errors later.
                pass
        return "agent-browser"
