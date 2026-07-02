import pytest

from shannon_web.components.git_fetcher import GitFetcher


def test_repo_name_strips_git():
    assert GitFetcher.repo_name("https://gitlab.com/g/foo.git") == "foo"


def test_redact_hides_token():
    assert "secret" not in GitFetcher.redact("https://user:secret@gitlab.com/x")


def test_inject_auth():
    f = GitFetcher("/tmp/x", "u", "t")
    assert f._inject_auth("https://gitlab.com/g.git") == "https://u:t@gitlab.com/g.git"


def test_available_flag():
    assert GitFetcher("/x", None, None).available() is False
    assert GitFetcher("/x", "u", "t").available() is True


@pytest.mark.asyncio
async def test_missing_creds_raises(tmp_path):
    f = GitFetcher(tmp_path, None, None)
    with pytest.raises(PermissionError):
        await f.fetch("https://gitlab.com/g.git")


@pytest.mark.asyncio
async def test_clone_command_with_branch(monkeypatch, tmp_path):
    f = GitFetcher(tmp_path, "u", "t")
    calls: list[list[str]] = []

    async def fake_run(args, cwd=None):
        calls.append(list(args))
        if args[:2] == ["git", "clone"]:
            (tmp_path / "foo").mkdir(exist_ok=True)  # 模拟 clone 建目录
        return 0, "", ""

    monkeypatch.setattr(f, "_run", fake_run)
    await f.fetch("https://gitlab.com/g/foo.git", branch="dev")
    clone = next(c for c in calls if c[:2] == ["git", "clone"])
    assert "--branch" in clone and "dev" in clone


@pytest.mark.asyncio
async def test_force_reclone_triggers_clone(monkeypatch, tmp_path):
    f = GitFetcher(tmp_path, "u", "t")
    target = tmp_path / "foo"
    target.mkdir()
    (target / "dirty").write_text("x")
    cloned: list[bool] = []

    async def fake_run(args, cwd=None):
        if args[:2] == ["git", "clone"]:
            cloned.append(True)
            target.mkdir(exist_ok=True)
        return 0, "", ""

    monkeypatch.setattr(f, "_run", fake_run)
    await f.fetch("https://gitlab.com/g/foo.git", force_reclone=True)
    assert cloned  # force_reclone 删后走了 clone 路径


@pytest.mark.asyncio
async def test_checkout_after_clone_when_commit(monkeypatch, tmp_path):
    f = GitFetcher(tmp_path, "u", "t")
    seq: list[list[str]] = []

    async def fake_run(args, cwd=None):
        seq.append(list(args))
        if args[:2] == ["git", "clone"]:
            (tmp_path / "foo").mkdir(exist_ok=True)
        return 0, "", ""

    monkeypatch.setattr(f, "_run", fake_run)
    await f.fetch("https://gitlab.com/g/foo.git", commit="abc123")
    assert any(s[:3] == ["git", "fetch", "--all"] for s in seq)
    assert any(s[:2] == ["git", "checkout"] and "abc123" in s for s in seq)


@pytest.mark.asyncio
async def test_clone_failure_redacts_token(monkeypatch, tmp_path):
    f = GitFetcher(tmp_path, "u", "t")
    async def fake_run(args, cwd=None):
        return 128, "", "fatal: https://u:secret@gitlab.com/x"
    monkeypatch.setattr(f, "_run", fake_run)
    with pytest.raises(RuntimeError) as ei:
        await f.fetch("https://gitlab.com/x.git")
    assert "secret" not in str(ei.value)
