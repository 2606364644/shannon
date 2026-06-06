"""BrowserEngine Protocol and BrowserEngineFactory for dual browser engine support.

Provides an abstract interface so that PlaywrightEngine and AgentBrowserEngine
can be used interchangeably by the rest of shannon-py.
"""

from __future__ import annotations

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
