from pathlib import Path

import pytest

from shannon_whitebox.audit.log_stream import LogStream


async def test_open_creates_file(tmp_path: Path):
    stream = LogStream(tmp_path / "subdir" / "test.log")
    await stream.open()
    assert stream.is_open
    assert stream.path == tmp_path / "subdir" / "test.log"
    assert (tmp_path / "subdir" / "test.log").exists()
    await stream.close()


async def test_open_creates_parent_directories(tmp_path: Path):
    stream = LogStream(tmp_path / "deep" / "nested" / "dir" / "file.log")
    await stream.open()
    assert (tmp_path / "deep" / "nested" / "dir").is_dir()
    await stream.close()


async def test_write_raw_text(tmp_path: Path):
    stream = LogStream(tmp_path / "test.log")
    await stream.open()
    await stream.write("hello world\n")
    await stream.close()
    content = (tmp_path / "test.log").read_text()
    assert content == "hello world\n"


async def test_write_multiple_times(tmp_path: Path):
    stream = LogStream(tmp_path / "test.log")
    await stream.open()
    await stream.write("line 1\n")
    await stream.write("line 2\n")
    await stream.close()
    content = (tmp_path / "test.log").read_text()
    assert content == "line 1\nline 2\n"


async def test_close_sets_is_open_false(tmp_path: Path):
    stream = LogStream(tmp_path / "test.log")
    await stream.open()
    assert stream.is_open
    await stream.close()
    assert not stream.is_open


async def test_is_open_false_before_open(tmp_path: Path):
    stream = LogStream(tmp_path / "test.log")
    assert not stream.is_open


async def test_write_without_open_raises(tmp_path: Path):
    stream = LogStream(tmp_path / "test.log")
    with pytest.raises(RuntimeError, match="Stream is not open"):
        await stream.write("data")


async def test_path_property(tmp_path: Path):
    expected = tmp_path / "output" / "my.log"
    stream = LogStream(expected)
    assert stream.path == expected


async def test_append_adds_timestamp_prefix(tmp_path: Path):
    stream = LogStream(tmp_path / "test.log")
    await stream.open()
    await stream.append("test message")
    await stream.close()
    content = (tmp_path / "test.log").read_text()
    assert content.startswith("[")
    assert "test message" in content
    assert content.endswith("test message\n")


async def test_append_lines_multiple(tmp_path: Path):
    stream = LogStream(tmp_path / "test.log")
    await stream.open()
    await stream.append_lines(["line 1", "line 2", "line 3"])
    await stream.close()
    lines = (tmp_path / "test.log").read_text().strip().split("\n")
    assert len(lines) == 3
    assert "line 1" in lines[0]
    assert "line 3" in lines[2]


async def test_append_appends_to_existing(tmp_path: Path):
    file_path = tmp_path / "test.log"
    file_path.write_text("existing\n", encoding="utf-8")
    stream = LogStream(file_path)
    await stream.open()
    await stream.write("new content\n")
    await stream.close()
    content = file_path.read_text()
    assert "existing\n" in content
    assert "new content\n" in content
