from __future__ import annotations

import tempfile
import time
from pathlib import Path

from shannon_core.config.parser import parse_multi_repo_config


class MultiRepoConfigStore:
    PREFIX = "web-multi-"

    def __init__(self, configs_dir: Path) -> None:
        self._dir = Path(configs_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def list_configs(self) -> list[str]:
        return sorted(
            p.stem[len(self.PREFIX):]
            for p in self._dir.glob(f"{self.PREFIX}*.yaml")
        )

    def read(self, name: str) -> str:
        p = self._path(name)
        if not p.exists():
            raise FileNotFoundError(name)
        return p.read_text("utf-8")

    def validate(self, content: str) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write(content)
            tmp = f.name
        try:
            parse_multi_repo_config(tmp)
        finally:
            Path(tmp).unlink(missing_ok=True)

    def write(self, name: str, content: str) -> Path:
        self.validate(content)  # ValidationError 向上抛
        p = self._path(name)
        p.write_text(content, "utf-8")
        return p

    def write_temp(self, content: str) -> Path:
        self.validate(content)
        p = self._dir / f"{self.PREFIX}tmp-{int(time.time())}.yaml"
        p.write_text(content, "utf-8")
        return p

    def _path(self, name: str) -> Path:
        if "/" in name or ".." in name or name == "":
            raise ValueError("invalid config name")
        return self._dir / f"{self.PREFIX}{name}.yaml"
