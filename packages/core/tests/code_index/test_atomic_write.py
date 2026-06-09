"""Spec C: atomic_write_text —— markdown deliverable 的原子写入。"""
from pathlib import Path

from shannon_core.utils.atomic_write import atomic_write_text


def test_writes_text_content(tmp_path: Path):
    target = tmp_path / "out.md"
    atomic_write_text(target, "# Hello\n- a\n- b\n")
    assert target.read_text(encoding="utf-8") == "# Hello\n- a\n- b\n"


def test_creates_parent_dirs(tmp_path: Path):
    target = tmp_path / "nested" / "deep" / "out.md"
    atomic_write_text(target, "x")
    assert target.read_text(encoding="utf-8") == "x"


def test_no_tmp_file_left_behind(tmp_path: Path):
    target = tmp_path / "out.md"
    atomic_write_text(target, "payload")
    # 成功写入后不应残留 .tmp 文件
    assert not (tmp_path / "out.md.tmp").exists()
    assert not (tmp_path / "out.tmp").exists()


def test_overwrite_replaces_existing(tmp_path: Path):
    target = tmp_path / "out.md"
    target.write_text("OLD", encoding="utf-8")
    atomic_write_text(target, "NEW")
    assert target.read_text(encoding="utf-8") == "NEW"
