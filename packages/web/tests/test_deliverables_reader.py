import json

import pytest

from supernova_web.components.deliverables_reader import DeliverablesReader


def test_read_json_new_layout(tmp_path):
    ws = tmp_path / "ws"
    dl = ws / "deliverables" / "whitebox"
    dl.mkdir(parents=True)
    (dl / "xss_exploitation_queue.json").write_text(json.dumps({"vulnerabilities": [1]}))
    assert DeliverablesReader(ws).read("xss_exploitation_queue.json") == {"vulnerabilities": [1]}


def test_read_md_legacy_flat_layout(tmp_path):
    ws = tmp_path / "ws"
    dl = ws / "deliverables"
    dl.mkdir(parents=True)
    (dl / "report.md").write_text("# hi")
    assert DeliverablesReader(ws).read("report.md") == "# hi"


def test_empty_json_returns_empty_list(tmp_path):
    ws = tmp_path / "ws"
    dl = ws / "deliverables" / "whitebox"
    dl.mkdir(parents=True)
    (dl / "attack_chains.json").write_text("[]")
    assert DeliverablesReader(ws).read("attack_chains.json") == []


def test_summary(tmp_path):
    ws = tmp_path / "ws"
    dl = ws / "deliverables" / "whitebox"
    dl.mkdir(parents=True)
    (dl / "xss_exploitation_queue.json").write_text(json.dumps({"vulnerabilities": [{}]}))
    (dl / "report.md").write_text("# r")
    s = DeliverablesReader(ws).summary()
    # summary schema 已收敛为 files/aggregate（不再透传 vuln_queues/reports）
    names = [f["path"] for f in s["files"]]
    assert "whitebox/xss_exploitation_queue.json" in names
    assert "whitebox/report.md" in names


def test_list_logs_includes_logs_subdir_diagnostic(tmp_path):
    """logs/ 子目录的 diagnostic.log（LogBus DiagnosticLog 落盘的底层引擎诊断流，
    GitNexus stderr 等确定性轨问题第一现场）须进日志列表且可读回——
    含子目录前缀（'logs/diagnostic.log'），与 agents/ 前缀约定一致，
    前端 ?file= 透传即达；CLI 侧 `supernova logs --diagnostic` 同能力。"""
    ws = tmp_path / "ws"
    (ws / "logs").mkdir(parents=True)
    (ws / "workflow.log").write_text("[workflow]")
    (ws / "agents").mkdir()
    (ws / "agents" / "1_agent_attempt-1.log").write_text("[] agent_start ")
    (ws / "logs" / "diagnostic.log").write_text("[ INFO] supernova_core.code_index: Detected 30 rule-based sink call sites")

    files = DeliverablesReader(ws).list_logs()
    assert "workflow.log" in files
    assert "agents/1_agent_attempt-1.log" in files
    assert "logs/diagnostic.log" in files

    content = DeliverablesReader(ws).read_log("logs/diagnostic.log")
    assert "rule-based sink call sites" in content


def test_list_logs_includes_ndjson_and_authcheck(tmp_path):
    """顶层 *.ndjson(events.ndjson/authcheck-events.ndjson 主事件流原始文件)与
    .authcheck/*.log(t0 认证预验证子 workflow 日志,隐藏目录——precheck 失败时
    详细过程只在)也须列出且可读回:「日志页看到本次扫描所有日志」补齐。"""
    ws = tmp_path / "ws"
    (ws / "agents").mkdir(parents=True)
    (ws / ".authcheck").mkdir(parents=True)
    (ws / "workflow.log").write_text("[workflow]")
    (ws / "events.ndjson").write_text('{"ts": "t1", "type": "PhaseEvent"}\n')
    (ws / "authcheck-events.ndjson").write_text('{"ts": "t2"}\n')
    (ws / ".authcheck" / "workflow.log").write_text("authcheck workflow")
    (ws / ".authcheck" / "activity_failures.log").write_text("")

    files = DeliverablesReader(ws).list_logs()
    assert "events.ndjson" in files
    assert "authcheck-events.ndjson" in files
    assert ".authcheck/workflow.log" in files
    assert ".authcheck/activity_failures.log" in files
    assert DeliverablesReader(ws).read_log(".authcheck/workflow.log") == "authcheck workflow"
    assert "PhaseEvent" in DeliverablesReader(ws).read_log("events.ndjson")


def test_read_log_rejects_path_traversal(tmp_path):
    """read_log 的 name 来自前端 ?file= query param——路径穿越(../ 越界、绝对路径
    注入 Path 拼接替换)须拒绝(FileNotFoundError),resolve 后必须仍在 scan 目录内。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")
    with pytest.raises(FileNotFoundError):
        DeliverablesReader(ws).read_log("../secret.txt")
    with pytest.raises(FileNotFoundError):
        DeliverablesReader(ws).read_log("../../etc/passwd")
    with pytest.raises(FileNotFoundError):
        DeliverablesReader(ws).read_log("/etc/passwd")


def test_missing_raises(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    with pytest.raises(FileNotFoundError):
        DeliverablesReader(ws).read("nope.md")


def test_read_poc_track_scoped(tmp_path):
    """PoC md 在 deliverables/{track}/ 下,read_poc 返回文本(PoC 自带「# 可利用漏洞 PoC 集合」标题)。"""
    ws = tmp_path / "ws"
    dl = ws / "deliverables" / "whitebox"
    dl.mkdir(parents=True)
    poc = "# 可利用漏洞 PoC 集合（白盒）\n\n```bash\ncurl -i 'https://t/x'\n```"
    (dl / "exploitable_poc_collection.md").write_text(poc)
    assert DeliverablesReader(ws).read_poc() == poc


def test_read_poc_blackbox_track(tmp_path):
    """黑盒扫描 PoC 在 deliverables/blackbox/,_infer_track 正确指向。"""
    ws = tmp_path / "ws"
    dl = ws / "deliverables" / "blackbox"
    dl.mkdir(parents=True)
    (dl / "exploitable_poc_collection.md").write_text("# 可利用漏洞 PoC 集合（黑盒）")
    assert DeliverablesReader(ws).read_poc() == "# 可利用漏洞 PoC 集合（黑盒）"


def test_read_poc_legacy_flat_layout(tmp_path):
    """老 workspace 平铺 deliverables/exploitable_poc_collection.md 也能读(resolve_track_deliverable fallback)。"""
    ws = tmp_path / "ws"
    dl = ws / "deliverables"
    dl.mkdir(parents=True)
    (dl / "exploitable_poc_collection.md").write_text("# PoC")
    assert DeliverablesReader(ws).read_poc() == "# PoC"


def test_read_poc_missing_returns_none(tmp_path):
    """无 PoC md(扫描中断/PoC activity 未跑)-> None,不抛,调用方按无 PoC 处理。"""
    ws = tmp_path / "ws"
    (ws / "deliverables" / "whitebox").mkdir(parents=True)
    assert DeliverablesReader(ws).read_poc() is None


def test_summary_mixed_layout_no_duplicate(tmp_path):
    """A queue present in BOTH the new track-scoped dir (deliverables/whitebox/)
    AND the legacy top-level dir (deliverables/) must be counted exactly once.

    Core's summarize_deliverables_dir is now recursive (rglob) and dedups by
    vuln_class, so a mixed layout does not double-count. This guards against
    drift between the two layouts during the migration window.
    """
    ws = tmp_path / "ws"
    top = ws / "deliverables"
    track_dir = top / "whitebox"
    track_dir.mkdir(parents=True)

    queue_payload = json.dumps({"vulnerabilities": [{}]})
    # Same vuln class in both locations
    (top / "xss_exploitation_queue.json").write_text(queue_payload)
    (track_dir / "xss_exploitation_queue.json").write_text(queue_payload)
    # report in both locations too
    (top / "report.md").write_text("# top")
    (track_dir / "report.md").write_text("# track")

    s = DeliverablesReader(ws).summary()
    queue_paths = [f["path"] for f in s["files"] if f["path"].endswith("xss_exploitation_queue.json")]
    assert len(queue_paths) == 2  # 两处物理文件都列出（读方按优先级取 track-scoped）
    assert s["aggregated_vulnerabilities"] == [{}]  # 聚合去重：1 条而非 2 条


class TestTieringFilter:
    """spec 2026-08-18：隐藏条目排除 + tier 字段。"""

    def _mk(self, tmp_path):
        wb = tmp_path / "deliverables" / "whitebox"
        (wb / "intermediate").mkdir(parents=True)
        (wb / "comprehensive_security_assessment_report.md").write_text("# R")
        (wb / "injection_findings.md").write_text("# F")
        (wb / "intermediate" / "code_index.json").write_text("{}")
        (wb / "intermediate" / "injection_exploitation_queue.json").write_text(
            '{"vulnerabilities": []}')
        return tmp_path

    def test_summary_excludes_hidden_entries(self, tmp_path):
        from supernova_web.components.deliverables_reader import DeliverablesReader
        ws = self._mk(tmp_path)
        wb = ws / "deliverables" / "whitebox"
        (wb / ".whitebox-archive" / "20260818").mkdir(parents=True)
        (wb / ".whitebox-archive" / "20260818" / "old.md").write_text("x")
        (wb / ".blackbox-archive").mkdir()
        (wb / ".blackbox-archive" / "old.json").write_text("{}")
        (wb / ".poc_checkpoint.json").write_text("{}")
        paths = [f["path"] for f in DeliverablesReader(ws).summary()["files"]]
        assert not any(".whitebox-archive" in p or ".blackbox-archive" in p for p in paths)
        assert not any(".poc_checkpoint" in p for p in paths)

    def test_summary_tier_by_dir_and_pattern(self, tmp_path):
        from supernova_web.components.deliverables_reader import DeliverablesReader
        ws = self._mk(tmp_path)
        # 旧结构平铺的 queue（无 intermediate/ 目录）→ pattern 兜底 intermediate
        (ws / "deliverables" / "whitebox" / "xss_llm_queue.json").write_text(
            '{"vulnerabilities": []}')
        tiers = {f["path"]: f["tier"] for f in DeliverablesReader(ws).summary()["files"]}
        assert tiers["whitebox/comprehensive_security_assessment_report.md"] == "deliverable"
        assert tiers["whitebox/injection_findings.md"] == "deliverable"
        assert tiers["whitebox/intermediate/code_index.json"] == "intermediate"
        assert tiers["whitebox/intermediate/injection_exploitation_queue.json"] == "intermediate"
        assert tiers["whitebox/xss_llm_queue.json"] == "intermediate"


class TestPreviewTruncation:
    """spec 2026-08-18：大文件预览截断（deliverables_file_for）。"""

    def test_truncates_oversized_file(self, tmp_path, monkeypatch):
        from supernova_web.api.scans import deliverables_file_for
        monkeypatch.setenv("SUPERNOVA_DELIVERABLES_PREVIEW_MAX_BYTES", "100")
        wb = tmp_path / "deliverables" / "whitebox"
        wb.mkdir(parents=True)
        (wb / "big.md").write_text("x" * 500)
        out = deliverables_file_for(tmp_path, "big.md", track="whitebox")
        assert isinstance(out, str)
        assert len(out) < 200
        assert "truncated" in out

    def test_small_file_untruncated(self, tmp_path):
        from supernova_web.api.scans import deliverables_file_for
        wb = tmp_path / "deliverables" / "whitebox"
        wb.mkdir(parents=True)
        (wb / "s.md").write_text("small")
        out = deliverables_file_for(tmp_path, "s.md", track="whitebox")
        assert out == "small"


class TestRunStripPrefix:
    """spec 2026-08-18 降级方案：run 级 summary 剥 blackbox/ 前缀（展示层归一）。"""

    def test_run_summary_strips_blackbox_prefix(self, tmp_path):
        from supernova_web.components.deliverables_reader import DeliverablesReader
        bb = tmp_path / "deliverables" / "blackbox"
        (bb / "intermediate").mkdir(parents=True)
        (bb / "comprehensive_security_assessment_report.md").write_text("# R")
        (bb / "injection_exploitation_evidence.md").write_text("# E")
        (bb / "intermediate" / "injection_exploit_verdicts.json").write_text("{}")
        reader = DeliverablesReader(tmp_path, strip_track_prefix="blackbox")
        paths = [f["path"] for f in reader.summary()["files"]]
        assert paths == [
            "comprehensive_security_assessment_report.md",
            "injection_exploitation_evidence.md",
            "intermediate/injection_exploit_verdicts.json",
        ]

    def test_run_read_without_prefix(self, tmp_path):
        from supernova_web.components.deliverables_reader import DeliverablesReader
        bb = tmp_path / "deliverables" / "blackbox"
        bb.mkdir(parents=True)
        (bb / "injection_exploitation_evidence.md").write_text("# E")
        reader = DeliverablesReader(tmp_path, strip_track_prefix="blackbox")
        assert reader.read("injection_exploitation_evidence.md") == "# E"
