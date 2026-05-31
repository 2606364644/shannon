from abc import ABC, abstractmethod
from pathlib import Path

from shannon_core.code_index.models import CallEdge, FuncBlock


class BaseParser(ABC):
    @abstractmethod
    def parse_file(self, file_path: Path, repo_root: Path) -> list[FuncBlock]:
        """Parse a source file and return all function blocks found."""
        ...

    @abstractmethod
    def extract_calls(self, block: FuncBlock, source: bytes) -> list[CallEdge]:
        """Extract call edges from a function block's source."""
        ...