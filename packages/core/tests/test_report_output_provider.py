import pytest
from pathlib import Path

from shannon_core.interfaces.report_output_provider import (
    ReportOutputProvider,
    NoOpReportOutputProvider,
)


@pytest.mark.asyncio
async def test_noop_provider_returns_none(tmp_path: Path):
    provider = NoOpReportOutputProvider()
    result = await provider.generate(tmp_path / "report.md", tmp_path / "deliverables")
    assert result == {"output_path": None}


def test_noop_is_subclass():
    assert issubclass(NoOpReportOutputProvider, ReportOutputProvider)


def test_cannot_instantiate_abstract():
    with pytest.raises(TypeError):
        ReportOutputProvider()


@pytest.mark.asyncio
async def test_custom_provider_can_be_implemented(tmp_path: Path):
    class InMemoryProvider(ReportOutputProvider):
        async def generate(self, report_path: Path, deliverables_path: Path) -> dict[str, str | None]:
            return {"output_path": str(report_path)}

    provider = InMemoryProvider()
    result = await provider.generate(tmp_path / "report.md", tmp_path / "deliverables")
    assert result["output_path"] == str(tmp_path / "report.md")