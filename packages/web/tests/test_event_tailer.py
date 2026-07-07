import asyncio
import json

import pytest

from shannon_web.components.event_tailer import EventTailer


def _line(d):
    return json.dumps(d, ensure_ascii=False)


@pytest.mark.asyncio
async def test_tails_until_scan_end(tmp_path):
    f = tmp_path / "e.ndjson"
    f.write_text(
        _line({"type": "InfoEvent", "ts": "t1", "category": "INFO", "message": "a"}) + "\n"
        + _line({"type": "scan_end", "ts": "t2", "category": "CONTROL", "status": "completed"}) + "\n"
    )
    t = EventTailer(f)
    seen: list[dict] = []

    async def cb(d, eid):
        seen.append(d)

    await t.tail(cb)
    assert seen[-1]["type"] == "scan_end"
    assert seen[0]["message"] == "a"


@pytest.mark.asyncio
async def test_corrupt_line_skipped(tmp_path):
    f = tmp_path / "e.ndjson"
    f.write_text(
        "not-json\n"
        + _line({"type": "scan_end", "ts": "t", "category": "CONTROL", "status": "completed"}) + "\n"
    )
    t = EventTailer(f)
    await t.tail(lambda d, eid: asyncio.sleep(0))
    assert t.corrupt_count == 1


@pytest.mark.asyncio
async def test_continues_from_last_event_id(tmp_path):
    # NOTE: brief 用两次 f.write_text(...) 构造文件，但 write_text 是 truncate 模式——
    # 第二次会把 first 抹掉，只剩 scan_end，offset=72 越过 EOF 后读到半行 scan_end，
    # 解析失败→corrupt→空读→死循环（任何实现都会挂）。改为追加模式构造，使
    # 「offset 跳过第一条」的意图真正可验证。
    f = tmp_path / "e.ndjson"
    first = _line({"type": "InfoEvent", "ts": "t1", "category": "INFO", "message": "old"}) + "\n"
    with open(f, "a") as fh:
        fh.write(first)
    offset_after_first = len(first.encode())
    with open(f, "a") as fh:
        fh.write(_line({"type": "scan_end", "ts": "t2", "category": "CONTROL", "status": "completed"}) + "\n")
    t = EventTailer(f)
    seen: list[dict] = []

    async def cb(d, eid):
        seen.append(d)

    await t.tail(cb, last_event_id=offset_after_first)  # 跳过第一条
    assert all(d.get("message") != "old" for d in seen)
    # 仅 scan_end 被交付，offset 落在文件末尾
    assert [d["type"] for d in seen] == ["scan_end"]


@pytest.mark.asyncio
async def test_append_during_tail(tmp_path):
    f = tmp_path / "e.ndjson"
    f.write_text(_line({"type": "InfoEvent", "ts": "t1", "category": "INFO", "message": "x"}) + "\n")
    t = EventTailer(f)
    seen: list[dict] = []

    async def cb(d, eid):
        seen.append(d)

    async def append_later():
        await asyncio.sleep(0.3)
        with open(f, "a") as fh:
            fh.write(_line({"type": "scan_end", "ts": "t2", "category": "CONTROL", "status": "completed"}) + "\n")

    asyncio.create_task(append_later())
    await asyncio.wait_for(t.tail(cb), timeout=5)
    assert any(d["type"] == "scan_end" for d in seen)


def test_encode_sse_format():
    out = EventTailer.encode_sse({"a": 1}, event_id=42)
    assert out.startswith("id: 42\n")
    assert "data: " in out
    assert out.endswith("\n\n")


@pytest.mark.asyncio
async def test_stops_on_custom_stop_type(tmp_path):
    f = tmp_path / "c.ndjson"
    f.write_text(
        _line({"type": "progress", "ts": "t1", "category": "INFO", "progress": 40}) + "\n"
        + _line({"type": "clone_end", "ts": "t2", "category": "CONTROL", "status": "ready"}) + "\n"
    )
    t = EventTailer(f)
    seen: list[dict] = []

    async def cb(d, eid):
        seen.append(d)

    await t.tail(cb, stop_type="clone_end")
    assert seen[-1]["type"] == "clone_end"
    assert seen[0]["progress"] == 40
