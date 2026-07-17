"""Task 6: 报告标红 - GitNexus 轨判定失败类注记注入(inject_attack_chains 后 report-executive)。

注入点决策:原 brief 建议 assemble_report 注入,但 report-executive agent 会重写
comprehensive_security_assessment_report.md 并清理"meta-commentary sections without
vulnerability IDs"(prompts/report-executive.txt:106) -> `## GitNexus 轨判定状态` 会被擦。
因此注入到 inject_attack_chains(report-executive 之后跑,与攻击链章节同款 survival pattern)。

铁律:banner 必须在无 attack_chains 时也持久化(原 early-return `if not chains_md: return`
会丢 banner)。
"""
import json

import pytest

from shannon_whitebox.pipeline import activities
from shannon_whitebox.pipeline.shared import ActivityInput

REPORT_FILENAME = "comprehensive_security_assessment_report.md"
BANNER_HEADER = "## GitNexus 轨判定状态"


def _write_report(deliverables, body: str = "# 安全评估报告\n\n## 执行摘要\n\n正文...\n"):
    report = deliverables / REPORT_FILENAME
    report.write_text(body, encoding="utf-8")
    return report


def _write_track_status(deliverables, statuses: dict):
    (deliverables / "gitnexus_track_status.json").write_text(
        json.dumps(statuses, ensure_ascii=False), encoding="utf-8")


def _monkey_paths(monkeypatch, tmp_path, deliverables):
    monkeypatch.setattr(
        activities, "_get_paths", lambda inp: (tmp_path, deliverables, tmp_path))


@pytest.mark.asyncio
async def test_report_includes_gitnexus_failed_note(tmp_path, monkeypatch):
    """失败类 -> 报告含 banner + 类名 + reason。"""
    deliverables = tmp_path / "whitebox"
    deliverables.mkdir()
    _write_report(deliverables)
    _write_track_status(deliverables, {
        "xss": {"status": "failed", "reason": "builder raised: KeyError"},
    })
    _monkey_paths(monkeypatch, tmp_path, deliverables)

    await activities.inject_attack_chains(ActivityInput(repo_path=str(tmp_path)))

    content = (deliverables / REPORT_FILENAME).read_text(encoding="utf-8")
    assert BANNER_HEADER in content
    assert "GitNexus 轨判定失败" in content
    assert "xss" in content
    assert "builder raised: KeyError" in content
    assert "LLM 轨提供" in content


@pytest.mark.asyncio
async def test_report_includes_gitnexus_failed_note_idempotent(tmp_path, monkeypatch):
    """重跑不重复注入 banner(resume/重跑幂等)。"""
    deliverables = tmp_path / "whitebox"
    deliverables.mkdir()
    _write_report(deliverables)
    _write_track_status(deliverables, {
        "xss": {"status": "failed", "reason": "builder raised: KeyError"},
    })
    _monkey_paths(monkeypatch, tmp_path, deliverables)

    await activities.inject_attack_chains(ActivityInput(repo_path=str(tmp_path)))
    await activities.inject_attack_chains(ActivityInput(repo_path=str(tmp_path)))

    content = (deliverables / REPORT_FILENAME).read_text(encoding="utf-8")
    assert content.count(BANNER_HEADER) == 1


@pytest.mark.asyncio
async def test_no_banner_when_no_failures(tmp_path, monkeypatch):
    """全部 ok 或无 status 文件 -> 不注入 banner。"""
    deliverables = tmp_path / "whitebox"
    deliverables.mkdir()
    original_body = "# 安全评估报告\n\n## 执行摘要\n\n正文...\n"
    _write_report(deliverables, body=original_body)
    _write_track_status(deliverables, {
        "xss": {"status": "ok"},
        "injection": {"status": "ok"},
    })
    _monkey_paths(monkeypatch, tmp_path, deliverables)

    await activities.inject_attack_chains(ActivityInput(repo_path=str(tmp_path)))

    content = (deliverables / REPORT_FILENAME).read_text(encoding="utf-8")
    assert BANNER_HEADER not in content
    assert "GitNexus 轨判定失败" not in content
    # 内容未变(无攻击链也无 banner)
    assert content == original_body


@pytest.mark.asyncio
async def test_no_banner_when_status_file_missing(tmp_path, monkeypatch):
    """无 status 文件 -> 不注入 banner(report 不变)。"""
    deliverables = tmp_path / "whitebox"
    deliverables.mkdir()
    original_body = "body\n"
    _write_report(deliverables, body=original_body)
    _monkey_paths(monkeypatch, tmp_path, deliverables)

    await activities.inject_attack_chains(ActivityInput(repo_path=str(tmp_path)))

    content = (deliverables / REPORT_FILENAME).read_text(encoding="utf-8")
    assert BANNER_HEADER not in content
    assert content == original_body


@pytest.mark.asyncio
async def test_banner_persists_without_attack_chains(tmp_path, monkeypatch):
    """关键:无 attack_chains.json 时 banner 仍写盘(原 early-return 会丢 banner)。"""
    deliverables = tmp_path / "whitebox"
    deliverables.mkdir()
    _write_report(deliverables)
    _write_track_status(deliverables, {
        "xss": {"status": "failed", "reason": "builder raised: KeyError"},
    })
    _monkey_paths(monkeypatch, tmp_path, deliverables)
    # 不写 attack_chains.json

    await activities.inject_attack_chains(ActivityInput(repo_path=str(tmp_path)))

    content = (deliverables / REPORT_FILENAME).read_text(encoding="utf-8")
    assert BANNER_HEADER in content
    assert "GitNexus 轨判定失败" in content


@pytest.mark.asyncio
async def test_banner_and_attack_chains_both_present(tmp_path, monkeypatch):
    """失败类 + 攻击链 -> 两个章节都在。"""
    deliverables = tmp_path / "whitebox"
    deliverables.mkdir()
    _write_report(deliverables)
    _write_track_status(deliverables, {
        "xss": {"status": "failed", "reason": "builder raised: KeyError"},
    })
    (deliverables / "attack_chains.json").write_text(
        json.dumps({"chains": [
            {"id": "llm-chain-1", "name": "enum->idor", "vuln_type": "authz",
             "severity": "critical", "confidence": "high",
             "steps": [{"order": 1, "endpoint": "/api/x", "method": "GET", "description": "d"}]},
        ]}),
        encoding="utf-8",
    )
    _monkey_paths(monkeypatch, tmp_path, deliverables)

    await activities.inject_attack_chains(ActivityInput(repo_path=str(tmp_path)))

    content = (deliverables / REPORT_FILENAME).read_text(encoding="utf-8")
    assert BANNER_HEADER in content
    assert "## 攻击链（多步利用路径）" in content
    assert "### llm-chain-1: enum->idor" in content


@pytest.mark.asyncio
async def test_multiple_failed_classes_all_listed(tmp_path, monkeypatch):
    """多失败类 -> banner 列全部。"""
    deliverables = tmp_path / "whitebox"
    deliverables.mkdir()
    _write_report(deliverables)
    _write_track_status(deliverables, {
        "xss": {"status": "failed", "reason": "builder raised: KeyError"},
        "injection": {"status": "failed", "reason": "timeout 30s"},
        "ssrf": {"status": "ok"},
    })
    _monkey_paths(monkeypatch, tmp_path, deliverables)

    await activities.inject_attack_chains(ActivityInput(repo_path=str(tmp_path)))

    content = (deliverables / REPORT_FILENAME).read_text(encoding="utf-8")
    assert content.count("GitNexus 轨判定失败") == 2
    assert "xss" in content
    assert "injection" in content
    # ssrf 是 ok,不出现在 banner
    banner_section = content.split(BANNER_HEADER)[1] if BANNER_HEADER in content else ""
    assert "ssrf" not in banner_section
