import json
import pytest
from pathlib import Path

from supernova_core.utils.paths import resolve_workspaces_dir, resolve_deliverables_path, has_valid_whitebox_results, get_default_deliverables_subdir, deliverables_dir_for_workspace


class TestResolveWorkspacesDir:
    def test_with_repo_path_ignored(self, tmp_path, monkeypatch):
        """repo_path 不再决定 workspace 根(改为 project_root/workspaces)。"""
        project_root = tmp_path / "project"
        project_root.mkdir()
        (project_root / ".git").mkdir()
        monkeypatch.chdir(project_root)
        monkeypatch.delenv("SUPERNOVA_WORKER_ROOT", raising=False)
        result = resolve_workspaces_dir("/data/repos/myrepo")
        assert result == project_root / "workspaces"

    def test_with_repo_path_nested_ignored(self, tmp_path, monkeypatch):
        """repo_path 不再决定 workspace 根(改为 project_root/workspaces)。"""
        project_root = tmp_path / "myproject"
        project_root.mkdir()
        (project_root / ".git").mkdir()
        monkeypatch.chdir(project_root)
        monkeypatch.delenv("SUPERNOVA_WORKER_ROOT", raising=False)
        result = resolve_workspaces_dir("/a/b/c")
        assert result == project_root / "workspaces"

    def test_without_repo_path(self, tmp_path, monkeypatch):
        """When no repo_path and in a git repo, resolves to project_root/workspaces."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        (project_root / ".git").mkdir()
        monkeypatch.chdir(project_root)
        result = resolve_workspaces_dir()
        assert result == project_root / "workspaces"

    def test_without_repo_path_uses_project_root(self, tmp_path, monkeypatch):
        """When no repo_path, should resolve to project_root/workspaces, not CWD."""
        # Create a fake project root with .git and a subdirectory
        project_root = tmp_path / "myproject"
        project_root.mkdir()
        (project_root / ".git").mkdir()
        subdir = project_root / "subdir"
        subdir.mkdir()

        # CWD is inside the project, should find project_root by walking up
        monkeypatch.chdir(subdir)

        result = resolve_workspaces_dir()
        assert result == project_root / "workspaces"

    def test_with_worker_root_env(self, tmp_path, monkeypatch):
        """When SUPERNOVA_WORKER_ROOT is set, returns worker_root / workspaces."""
        worker_root = tmp_path / "supernova-worker"
        worker_root.mkdir()
        monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(worker_root))
        result = resolve_workspaces_dir()
        assert result == worker_root / "workspaces"

    def test_worker_root_env_used_even_when_repo_path_given(self, monkeypatch):
        """SUPERNOVA_WORKER_ROOT 优先级高于 repo_path(repo_path 已不再参与定位 workspace 根)。"""
        monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", "/custom/worker/root")
        result = resolve_workspaces_dir("/data/repos/myrepo")
        assert result == Path("/custom/worker/root/workspaces")

    def test_worker_root_fallback_without_repo_path(self, tmp_path, monkeypatch):
        """When no repo_path and no SUPERNOVA_WORKER_ROOT, uses project_root."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        (project_root / ".git").mkdir()
        monkeypatch.chdir(project_root)
        monkeypatch.delenv("SUPERNOVA_WORKER_ROOT", raising=False)
        result = resolve_workspaces_dir()
        assert result == project_root / "workspaces"

    def test_fallback_when_cwd_root_inside_scanned_repo(self, tmp_path, monkeypatch):
        """cwd-based 根落在被扫 repo 内(用户 cd 进 repo 再跑)→ fallback 到 repo 同级,
        避免污染被扫 repo(项目核心目标)。"""
        repo = tmp_path / "myrepo"
        repo.mkdir()
        (repo / ".git").mkdir()  # find_project_root 从 repo 内 cwd 找到 repo 本身
        monkeypatch.chdir(repo)
        monkeypatch.delenv("SUPERNOVA_WORKER_ROOT", raising=False)
        result = resolve_workspaces_dir(str(repo))
        assert result == repo.resolve().parent / "workspaces"

    def test_no_fallback_when_cwd_root_outside_repo(self, tmp_path, monkeypatch):
        """cwd-based 根不在被扫 repo 内(用户在 supernova 跑,repo 在别处)→ 正常返回
        project_root/workspaces,不触发 fallback(e6d74db 的默认行为不变)。"""
        project = tmp_path / "supernova"
        project.mkdir()
        (project / ".git").mkdir()
        monkeypatch.chdir(project)
        monkeypatch.delenv("SUPERNOVA_WORKER_ROOT", raising=False)
        repo = tmp_path / "vuln-range" / "NodeGoat"
        repo.mkdir(parents=True)
        result = resolve_workspaces_dir(str(repo))
        assert result == project / "workspaces"


class TestResolveDeliverablesPath:
    def test_workspace_name_takes_priority(self, tmp_path):
        """workspace_name 优先 → workspaces/<name>/deliverables，忽略 repo_path。"""
        result = resolve_deliverables_path(
            repo_path="/data/repos/myrepo",
            deliverables_subdir="deliverables",
            workspace_name="scan-1",
            workspaces_root=tmp_path / "workspaces",
        )
        assert result == tmp_path / "workspaces" / "scan-1" / "deliverables"

    def test_workspace_name_without_workspaces_root(self, tmp_path, monkeypatch):
        """workspace_name 但未传 workspaces_root → 用 resolve_workspaces_dir()。"""
        monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_path / "worker"))
        result = resolve_deliverables_path(
            repo_path=None,
            deliverables_subdir="deliverables",
            workspace_name="scan-1",
        )
        assert result == tmp_path / "worker" / "workspaces" / "scan-1" / "deliverables"

    def test_repo_path_fallback_when_no_workspace(self):
        """无 workspace_name 时过渡回退 repo_path/<subdir>。"""
        result = resolve_deliverables_path(
            repo_path="/data/repos/myrepo",
            deliverables_subdir="deliverables",
        )
        assert result == Path("/data/repos/myrepo/deliverables")

    def test_raises_when_no_repo_or_workspace(self):
        with pytest.raises(ValueError, match="必须提供"):
            resolve_deliverables_path(
                repo_path=None,
                deliverables_subdir="deliverables",
            )

    def test_deliverables_parent_is_workspace(self, tmp_path):
        """deliverables.parent ≡ workspace_path（auth save/load 路径一致性根基）。

        AgentExecutor 基层用 deliverables.parent 推导 AUTH_STATE_FILE，须与 auth save
        用的 input.workspace_path 同目录（spec §3.3）。锁定此隐含约定，防 deliverables
        结构变更悄悄破坏 save/load 一致性。
        """
        ws_root = tmp_path / "workspaces"
        ws_root.mkdir()
        deliverables = resolve_deliverables_path(
            repo_path=None,
            deliverables_subdir="deliverables",
            workspace_name="session",
            workspaces_root=ws_root,
        )
        assert deliverables.parent == ws_root / "session"
        assert deliverables.name == "deliverables"


class TestHasValidWhiteboxResults:
    """对齐原始 TS validateQueueStructure:文件存在 + ``vulnerabilities`` 非空数组即 True。

    不校验条目内部字段——``title``/``description``/``severity``/``location`` 是 exploit
    阶段字段,非 vuln queue 字段(回归锚点见 ``test_accepts_real_vuln_queue_fields``)。
    """

    def test_file_not_found(self, tmp_path):
        assert has_valid_whitebox_results(tmp_path / "nonexistent.json") is False

    def test_accepts_real_vuln_queue_fields(self, tmp_path):
        """回归锚点:真实 vuln queue 字段(ID/vulnerability_type/source/path/sink_call/
        verdict/externally_exploitable/confidence,**无** title/description/severity/
        location)→ True。锁定'不再误要求 exploit 阶段那 4 字段'。"""
        queue_file = tmp_path / "injection_exploitation_queue.json"
        queue_file.write_text(json.dumps({
            "vulnerabilities": [{
                "ID": "INJ-VULN-01",
                "vulnerability_type": "SQLi",
                "externally_exploitable": True,
                "confidence": "high",
                "source": "req.body.q at routes/search.js:12",
                "path": "search → sink",
                "sink_call": "db.query:30",
                "verdict": "vulnerable",
                "witness_payload": "' OR 1=1--",
            }]
        }))
        assert has_valid_whitebox_results(queue_file) is True

    def test_accepts_minimal_entry(self, tmp_path):
        """对齐 TS:条目字段极少也 True(只校验数组非空,不查条目字段)。"""
        queue_file = tmp_path / "injection_exploitation_queue.json"
        queue_file.write_text(json.dumps({
            "vulnerabilities": [{"ID": "V-001"}]
        }))
        assert has_valid_whitebox_results(queue_file) is True

    def test_accepts_non_dict_entries(self, tmp_path):
        """对齐 TS validateQueueStructure:不查条目内部,非空数组即 True(条目非 dict 亦然)。"""
        queue_file = tmp_path / "injection_exploitation_queue.json"
        queue_file.write_text(json.dumps({
            "vulnerabilities": ["not a dict", 42]
        }))
        assert has_valid_whitebox_results(queue_file) is True

    def test_empty_vulnerabilities(self, tmp_path):
        queue_file = tmp_path / "injection_exploitation_queue.json"
        queue_file.write_text(json.dumps({"vulnerabilities": []}))
        assert has_valid_whitebox_results(queue_file) is False

    def test_missing_vulnerabilities_key(self, tmp_path):
        queue_file = tmp_path / "injection_exploitation_queue.json"
        queue_file.write_text(json.dumps({"data": "something"}))
        assert has_valid_whitebox_results(queue_file) is False

    def test_invalid_json(self, tmp_path):
        queue_file = tmp_path / "injection_exploitation_queue.json"
        queue_file.write_text("not json")
        assert has_valid_whitebox_results(queue_file) is False

    def test_vulnerabilities_not_a_list(self, tmp_path):
        queue_file = tmp_path / "injection_exploitation_queue.json"
        queue_file.write_text(json.dumps({"vulnerabilities": "not a list"}))
        assert has_valid_whitebox_results(queue_file) is False


class TestGetDefaultDeliverablesSubdir:
    def test_returns_constant_when_no_env(self, monkeypatch):
        """When SUPERNOVA_DELIVERABLES_SUBDIR is not set, returns the default constant."""
        from supernova_core.constants import DEFAULT_DELIVERABLES_SUBDIR
        monkeypatch.delenv("SUPERNOVA_DELIVERABLES_SUBDIR", raising=False)
        assert get_default_deliverables_subdir() == DEFAULT_DELIVERABLES_SUBDIR

    def test_returns_env_value_when_set(self, monkeypatch):
        """When SUPERNOVA_DELIVERABLES_SUBDIR is set, returns its value."""
        monkeypatch.setenv("SUPERNOVA_DELIVERABLES_SUBDIR", "custom/output")
        assert get_default_deliverables_subdir() == "custom/output"

    def test_returns_empty_string_when_env_empty(self, monkeypatch):
        """When SUPERNOVA_DELIVERABLES_SUBDIR is set to empty string, returns empty string."""
        monkeypatch.setenv("SUPERNOVA_DELIVERABLES_SUBDIR", "")
        assert get_default_deliverables_subdir() == ""


class TestDeliverablesDirForWorkspace:
    """deliverables_dir_for_workspace 直接返回 workspace 下的 deliverables 目录。"""

    def test_returns_workspace_deliverables(self, tmp_path):
        from supernova_core.session import SessionManager

        repo = tmp_path / "repo"
        repo.mkdir()
        mgr = SessionManager(tmp_path / "workspaces")
        ws = mgr.create_workspace("https://x.com", str(repo), name="wb-1")

        assert deliverables_dir_for_workspace(ws) == ws / "deliverables"

    def test_works_without_session_json(self, tmp_path):
        ws = tmp_path / "workspaces" / "orphan"
        ws.mkdir(parents=True)
        assert deliverables_dir_for_workspace(ws) == ws / "deliverables"


from supernova_core.utils.paths import (
    WHITEBOX_SUBDIR, BLACKBOX_SUBDIR,
    whitebox_dir, blackbox_dir, resolve_track_deliverable,
)


class TestTrackSubdirHelpers:
    def test_whitebox_dir_appends_subdir(self, tmp_path):
        dlv = tmp_path / "deliverables"
        assert whitebox_dir(dlv) == dlv / "whitebox"

    def test_blackbox_dir_appends_subdir(self, tmp_path):
        dlv = tmp_path / "deliverables"
        assert blackbox_dir(dlv) == dlv / "blackbox"

    def test_subdir_constants(self):
        assert WHITEBOX_SUBDIR == "whitebox"
        assert BLACKBOX_SUBDIR == "blackbox"

    def test_blackbox_runs_helpers(self, tmp_path):
        from supernova_core.utils.paths import (
            BLACKBOX_RUNS_SUBDIR, blackbox_runs_dir, blackbox_run_dir, combined_run_dir)
        scan_dir = tmp_path / "repo-ts"
        assert BLACKBOX_RUNS_SUBDIR == "blackbox-runs"
        assert blackbox_runs_dir(scan_dir) == scan_dir / "blackbox-runs"
        assert blackbox_run_dir(scan_dir, "run-1") == scan_dir / "blackbox-runs" / "run-1"
        assert combined_run_dir(scan_dir, "run-1") == scan_dir / "combined" / "run-1"


class TestResolveTrackDeliverable:
    def test_prefers_new_track_subdir(self, tmp_path):
        dlv = tmp_path / "deliverables"
        (dlv / "whitebox").mkdir(parents=True)
        (dlv / "whitebox" / "injection_exploitation_queue.json").write_text("{}")
        # 老结构也存在，但新结构优先
        (dlv / "injection_exploitation_queue.json").write_text("{}")
        result = resolve_track_deliverable(dlv, "whitebox", "injection_exploitation_queue.json")
        assert result == dlv / "whitebox" / "injection_exploitation_queue.json"

    def test_falls_back_to_legacy_root(self, tmp_path):
        dlv = tmp_path / "deliverables"
        dlv.mkdir()
        # 仅老结构存在（老 workspace）
        (dlv / "injection_exploitation_queue.json").write_text("{}")
        result = resolve_track_deliverable(dlv, "whitebox", "injection_exploitation_queue.json")
        assert result == dlv / "injection_exploitation_queue.json"

    def test_returns_new_path_when_neither_exists(self, tmp_path):
        dlv = tmp_path / "deliverables"
        dlv.mkdir()
        result = resolve_track_deliverable(dlv, "blackbox", "x_evidence.md")
        # 都不存在 → 返回新结构路径（让调用方自然 not-found）
        assert result == dlv / "blackbox" / "x_evidence.md"
