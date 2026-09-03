"""真实 git 输出的契约测试——单元 fixture 是手写的，此处核对 git 实际输出格式。"""

import subprocess

from supernova_core.mr_scan.diff_manifest import parse_unified_diff


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_parse_real_git_diff_output(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")

    target = repo / "app.py"
    target.write_text("def h(req):\n    a = 1\n    b = 2\n    c = 3\n    d = 4\n    e = 5\n    f = 6\n    return req\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")

    # 改中间一行 + 加两行（-U3 上下文覆盖）
    target.write_text("def h(req):\n    a = 1\n    b = 2\n    c = 33\n    d = 40\n    e = 50\n    f = 6\n    return req\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "head")

    out = subprocess.run(
        ["git", "diff", "-U3", "HEAD~1..HEAD", "--no-color"],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout

    manifest = parse_unified_diff(out, base_commit="HEAD~1", head_commit="HEAD")
    assert manifest.hunks, f"未解析出 hunk，原始输出:\n{out}"
    hunk = manifest.hunks[0]
    assert hunk.file_path == "app.py"
    assert hunk.removed and hunk.removed[0].base_line_no == 4   # c = 3（def 行算第 1 行）
    assert hunk.added and [l.head_line_no for l in hunk.added] == [4, 5, 6]
    assert manifest.stats.insertions == 3 and manifest.stats.deletions == 3
    assert manifest.added_line_set("app.py") == {4, 5, 6}
