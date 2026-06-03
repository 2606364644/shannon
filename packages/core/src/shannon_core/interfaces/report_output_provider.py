from abc import ABC, abstractmethod
from pathlib import Path


class ReportOutputProvider(ABC):
    @abstractmethod
    async def generate(self, report_path: Path, deliverables_path: Path) -> dict[str, str | None]:
        ...


class NoOpReportOutputProvider(ReportOutputProvider):
    async def generate(self, report_path: Path, deliverables_path: Path) -> dict[str, str | None]:
        return {"output_path": None}