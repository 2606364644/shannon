# packages/core/tests/display/test_structured_event_renderer.py
import asyncio
import json
from pathlib import Path

import pytest

from shannon_core.display.events import (
    AgentEvent,
    InfoEvent,
    PhaseEvent,
    StepEvent,
    SummaryEvent,
    ToolCallEvent,
)
from shannon_core.display.structured_event_renderer import StructuredEventRenderer


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


@pytest.mark.asyncio
async def test_writes_phase_event_with_common_and_extra_fields(tmp_path: Path):
    f = tmp_path / "events.ndjson"
    r = StructuredEventRenderer(str(f))
    ev = PhaseEvent(timestamp="2026-07-02T09:44:01.123Z", category="PHASE",
                    phase="recon", event="start", steps=("s1", "s2"))
    await r.render(ev)
    await r.close()

    rows = _lines(f)
    assert len(rows) == 1
    row = rows[0]
    assert row["ts"] == "2026-07-02T09:44:01.123Z"
    assert row["category"] == "PHASE"
    assert row["type"] == "PhaseEvent"
    assert row["phase"] == "recon"
    assert row["event"] == "start"
    assert row["steps"] == ["s1", "s2"]  # tuple -> list


@pytest.mark.asyncio
async def test_tool_call_parameters_any_serializable(tmp_path: Path):
    f = tmp_path / "events.ndjson"
    r = StructuredEventRenderer(str(f))
    await r.render(ToolCallEvent(timestamp="t1", category="TOOL",
                                 agent_name="recon", tool_name="Bash",
                                 parameters={"cmd": "ls", "n": 3}))
    await r.close()
    row = _lines(f)[0]
    assert row["parameters"] == {"cmd": "ls", "n": 3}


@pytest.mark.asyncio
async def test_summary_event_appends_scan_end(tmp_path: Path):
    f = tmp_path / "events.ndjson"
    r = StructuredEventRenderer(str(f))
    await r.render(SummaryEvent(timestamp="t2", category="SUMMARY",
                                status="completed", total_duration_ms=1000,
                                total_cost_usd=0.5))
    await r.close()
    rows = _lines(f)
    assert len(rows) == 2
    assert rows[0]["type"] == "SummaryEvent"
    assert rows[1] == {"ts": "t2", "category": "CONTROL", "type": "scan_end", "status": "completed"}


@pytest.mark.asyncio
async def test_non_summary_event_does_not_write_scan_end(tmp_path: Path):
    f = tmp_path / "events.ndjson"
    r = StructuredEventRenderer(str(f))
    await r.render(StepEvent(timestamp="t3", category="STEP", name="x", phase="p", event="start"))
    await r.close()
    assert [r["type"] for r in _lines(f)] == ["StepEvent"]


@pytest.mark.asyncio
async def test_lazy_open_no_event_no_file(tmp_path: Path):
    f = tmp_path / "events.ndjson"
    r = StructuredEventRenderer(str(f))
    await r.close()
    assert not f.exists()


@pytest.mark.asyncio
async def test_concurrent_renders_no_interleaving(tmp_path: Path):
    f = tmp_path / "events.ndjson"
    r = StructuredEventRenderer(str(f))
    events = [InfoEvent(timestamp=f"t{i}", category="INFO", message=f"m{i}") for i in range(50)]
    await asyncio.gather(*(r.render(e) for e in events))
    await r.close()
    rows = _lines(f)
    assert len(rows) == 50
    for row in rows:
        assert row["type"] == "InfoEvent"  # 每行完整可 parse = 无交错断行
