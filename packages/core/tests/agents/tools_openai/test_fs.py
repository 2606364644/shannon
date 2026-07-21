import pytest
from agents import RunContextWrapper

from supernova_core.agents.tools_openai import ToolContext
from supernova_core.agents.tools_openai.fs import _edit_file_impl, _glob_impl, _read_file_impl, _write_file_impl


def _ctx(tmp_path):
    return RunContextWrapper(ToolContext(cwd=str(tmp_path)))


@pytest.mark.asyncio
async def test_read_file_with_line_numbers(tmp_path):
    (tmp_path / "a.txt").write_text("alpha\nbeta\n")
    out = await _read_file_impl(_ctx(tmp_path), "a.txt")
    assert "1\talpha" in out and "2\tbeta" in out


@pytest.mark.asyncio
async def test_read_file_offset_limit(tmp_path):
    (tmp_path / "a.txt").write_text("l1\nl2\nl3\nl4\n")
    out = await _read_file_impl(_ctx(tmp_path), "a.txt", offset=1, limit=2)
    assert "l2" in out and "l3" in out and "l4" not in out


@pytest.mark.asyncio
async def test_write_file_creates_and_overwrites(tmp_path):
    await _write_file_impl(_ctx(tmp_path), "sub/dir/b.txt", "hello")
    assert (tmp_path / "sub" / "dir" / "b.txt").read_text() == "hello"
    await _write_file_impl(_ctx(tmp_path), "sub/dir/b.txt", "world")
    assert (tmp_path / "sub" / "dir" / "b.txt").read_text() == "world"


@pytest.mark.asyncio
async def test_edit_file_replaces_unique(tmp_path):
    (tmp_path / "c.txt").write_text("foo bar foo")
    await _edit_file_impl(_ctx(tmp_path), "c.txt", "bar", "baz")
    assert (tmp_path / "c.txt").read_text() == "foo baz foo"


@pytest.mark.asyncio
async def test_edit_file_error_when_not_unique(tmp_path):
    (tmp_path / "c.txt").write_text("dup dup")
    out = await _edit_file_impl(_ctx(tmp_path), "c.txt", "dup", "x")
    assert "not unique" in out.lower()


@pytest.mark.asyncio
async def test_edit_file_replace_all(tmp_path):
    (tmp_path / "c.txt").write_text("dup dup")
    await _edit_file_impl(_ctx(tmp_path), "c.txt", "dup", "x", replace_all=True)
    assert (tmp_path / "c.txt").read_text() == "x x"


@pytest.mark.asyncio
async def test_glob_matches_pattern(tmp_path):
    (tmp_path / "x.py").write_text("")
    (tmp_path / "y.txt").write_text("")
    (tmp_path / "z.py").write_text("")
    out = await _glob_impl(_ctx(tmp_path), "**/*.py")
    assert "x.py" in out and "z.py" in out and "y.txt" not in out