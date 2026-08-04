# packages/core/tests/display/test_structured_event_renderer.py
import asyncio
import json
import os
from pathlib import Path

import pytest

from supernova_core.display.events import (
    AgentEvent,
    InfoEvent,
    PhaseEvent,
    StepEvent,
    SummaryEvent,
    ToolCallEvent,
)
from supernova_core.display.structured_event_renderer import StructuredEventRenderer, wire_web_event_file


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


# ── P2（2026-08-04）：ts 时区归一化 ──
# 历史 workflow_logger 用 format_log_time() 给 event.timestamp 赋值 = worker 容器 UTC 墙钟，
# 格式 "2026-08-04 02:49:13"（无时区后缀）。前端 Date.parse 按浏览器本地时区解释 -> 8h 漂移。
# _serialize 须把无时区 ts 归一化为 UTC ISO 带 Z（自描述时区，不依赖容器/浏览器时区）。
@pytest.mark.asyncio
async def test_serialize_normalizes_no_timezone_ts_to_utc_z(tmp_path: Path):
    """无时区串（format_log_time 产物，worker 容器 UTC）-> 补 Z 当 UTC。"""
    f = tmp_path / "events.ndjson"
    r = StructuredEventRenderer(str(f))
    ev = PhaseEvent(timestamp="2026-08-04 02:49:13", category="PHASE",
                    phase="recon", event="start", steps=())
    await r.render(ev)
    await r.close()
    row = _lines(f)[0]
    assert row["ts"] == "2026-08-04T02:49:13Z"  # 空格->T + 补 Z


@pytest.mark.asyncio
async def test_serialize_keeps_z_timestamp_as_is(tmp_path: Path):
    """带 Z 的 ISO ts 原样保留（已是 UTC，标准）。"""
    f = tmp_path / "events.ndjson"
    r = StructuredEventRenderer(str(f))
    ev = PhaseEvent(timestamp="2026-08-04T02:49:13.789Z", category="PHASE",
                    phase="recon", event="start", steps=())
    await r.render(ev)
    await r.close()
    assert _lines(f)[0]["ts"] == "2026-08-04T02:49:13.789Z"


@pytest.mark.asyncio
async def test_serialize_normalizes_offset_timestamp_to_z(tmp_path: Path):
    """带 +00:00 偏移的 ts -> 归一化为 Z（与 web 回退 _now_iso 产物对齐）。"""
    f = tmp_path / "events.ndjson"
    r = StructuredEventRenderer(str(f))
    ev = PhaseEvent(timestamp="2026-08-04T02:49:13+00:00", category="PHASE",
                    phase="recon", event="start", steps=())
    await r.render(ev)
    await r.close()
    assert _lines(f)[0]["ts"] == "2026-08-04T02:49:13Z"


@pytest.mark.asyncio
async def test_scan_end_ts_also_normalized(tmp_path: Path):
    """SummaryEvent 触发的 scan_end 行 ts 也归一化（无时区 -> Z）。"""
    f = tmp_path / "events.ndjson"
    r = StructuredEventRenderer(str(f))
    await r.render(SummaryEvent(timestamp="2026-08-04 02:49:13", category="SUMMARY",
                                status="completed", total_duration_ms=1000,
                                total_cost_usd=0.5))
    await r.close()
    rows = _lines(f)
    assert rows[1]["ts"] == "2026-08-04T02:49:13Z"
    assert rows[1]["type"] == "scan_end"


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


# --- wire_web_event_file: CLI 启动的扫描默认注入 events.ndjson 路径 ----------
# 目的：让 `uv run supernova-whitebox start`（无 SUPERNOVA_WEB_EVENT_FILE）跑出的扫描，
# 在 supernova-web 实时页（LiveTab，SSE tail events.ndjson）也可见。setdefault 语义：
# WEB 启动时 scan_manager 已注入该 env → 不覆盖；CLI 启动 env 未设 → 这里补上。

def test_wire_sets_default_when_unset(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("SUPERNOVA_WEB_EVENT_FILE", raising=False)
    wire_web_event_file(tmp_path, "NodeGoat_20260708-153045")
    assert os.environ["SUPERNOVA_WEB_EVENT_FILE"] == str(
        tmp_path / "NodeGoat_20260708-153045" / "events.ndjson"
    )


def test_wire_does_not_override_existing(monkeypatch):
    monkeypatch.setenv("SUPERNOVA_WEB_EVENT_FILE", "/preset/by/web/events.ndjson")
    wire_web_event_file(Path("/anywhere"), "ws")
    # WEB 启动注入的值原样保留，不被 CLI 的默认路径覆盖
    assert os.environ["SUPERNOVA_WEB_EVENT_FILE"] == "/preset/by/web/events.ndjson"


def test_wire_noop_without_workspace_name(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("SUPERNOVA_WEB_EVENT_FILE", raising=False)
    wire_web_event_file(tmp_path, None)
    assert "SUPERNOVA_WEB_EVENT_FILE" not in os.environ
