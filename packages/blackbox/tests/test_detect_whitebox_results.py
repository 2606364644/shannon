"""T6: 黑盒读白盒 queue 走 fallback 的测试。

锁定三个读点全部经 `resolve_track_deliverable(..., WHITEBOX_SUBDIR, queue)`:
  1. `detect_whitebox_results`（单仓 + correlation 两条路径）
  2. `has_correlation_results`（关联 workspace merged queue）
  3. （cli info 见 test_cli.py 的 smoke；此处只测纯函数 / activity 级）

每条路径都必须同时覆盖:
  - 新结构: queue 落在 deliverables/whitebox/
  - 老 / correlation merged: queue 落在 deliverables 根（fallback）
"""
import json
from pathlib import Path

import pytest

from shannon_blackbox.pipeline.activities import detect_whitebox_results
from shannon_blackbox.pipeline.workflows import has_correlation_results


def _write_queue(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"vulnerabilities": [{"ID": "v-1", "vulnerability_type": "injection"}]}),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- #
# detect_whitebox_results — single-repo queue
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_detect_whitebox_results_new_structure(tmp_path: Path):
    """新结构: queue 在 deliverables/whitebox/ → has_whitebox_results=True。"""
    dlv = tmp_path / "deliverables"
    _write_queue(dlv / "whitebox" / "injection_exploitation_queue.json")

    result = await detect_whitebox_results(str(dlv), ["injection", "xss"], None)

    assert result["has_whitebox_results"] is True
    assert result["found_classes"] == ["injection"]
    assert result["corr_classes"] == []


@pytest.mark.asyncio
async def test_detect_whitebox_results_legacy_root(tmp_path: Path):
    """老结构: queue 在 deliverables 根（无 whitebox/ 子目录）→ fallback 命中 → True。"""
    dlv = tmp_path / "deliverables"
    _write_queue(dlv / "injection_exploitation_queue.json")
    assert not (dlv / "whitebox").exists()  # 确认走的是 fallback

    result = await detect_whitebox_results(str(dlv), ["injection", "xss"], None)

    assert result["has_whitebox_results"] is True
    assert result["found_classes"] == ["injection"]


@pytest.mark.asyncio
async def test_detect_whitebox_results_no_queue(tmp_path: Path):
    """无任何 queue → False、空 found_classes。"""
    dlv = tmp_path / "deliverables"
    dlv.mkdir(parents=True)

    result = await detect_whitebox_results(str(dlv), ["injection"], None)

    assert result["has_whitebox_results"] is False
    assert result["found_classes"] == []


# --------------------------------------------------------------------------- #
# detect_whitebox_results — correlation 路径（merged queue 在关联 deliverables 根）
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_detect_whitebox_results_correlation_merged_at_root(tmp_path: Path):
    """correlation merged queue 落在关联 workspace 的 deliverables 根（非 whitebox/）。

    fallback 先找 corr_dlv/whitebox/queue（不存在）→ 回退 corr_dlv/queue（命中 merged）。
    锁定此兼容路径（spec：merged queue 天然落在 correlation workspace 根）。
    """
    dlv = tmp_path / "deliverables"  # 单仓: 空
    dlv.mkdir(parents=True)
    corr_dlv = tmp_path / "corr-deliverables"
    # merged queue 故意落在根，不在 whitebox/
    _write_queue(corr_dlv / "xss_exploitation_queue.json")
    assert not (corr_dlv / "whitebox").exists()

    result = await detect_whitebox_results(str(dlv), ["injection", "xss"], str(corr_dlv))

    # 单仓无结果才查 corr；corr merged queue 命中
    assert result["has_whitebox_results"] is True
    assert result["found_classes"] == []
    assert result["corr_classes"] == ["xss"]


@pytest.mark.asyncio
async def test_detect_whitebox_results_correlation_new_structure(tmp_path: Path):
    """关联 workspace 也可以是新结构（whitebox/queue）。"""
    dlv = tmp_path / "deliverables"
    dlv.mkdir(parents=True)
    corr_dlv = tmp_path / "corr-deliverables"
    _write_queue(corr_dlv / "whitebox" / "injection_exploitation_queue.json")

    result = await detect_whitebox_results(str(dlv), ["injection"], str(corr_dlv))

    assert result["has_whitebox_results"] is True
    assert result["corr_classes"] == ["injection"]


@pytest.mark.asyncio
async def test_detect_whitebox_results_single_repo_wins_over_correlation(tmp_path: Path):
    """单仓有结果则不查 corr（ADD 语义：found_classes 非空时跳过 corr 分支）。"""
    dlv = tmp_path / "deliverables"
    _write_queue(dlv / "whitebox" / "injection_exploitation_queue.json")
    corr_dlv = tmp_path / "corr-deliverables"
    _write_queue(corr_dlv / "whitebox" / "xss_exploitation_queue.json")

    result = await detect_whitebox_results(str(dlv), ["injection", "xss"], str(corr_dlv))

    assert result["found_classes"] == ["injection"]
    assert result["corr_classes"] == []  # 单仓命中即跳过 corr


# --------------------------------------------------------------------------- #
# has_correlation_results — 纯函数
# --------------------------------------------------------------------------- #
def test_has_correlation_results_merged_at_root(tmp_path: Path):
    """关联 merged queue 在 deliverables 根（fallback）→ True。

    这是 correlation 兼容路径的核心断言：resolve_track_deliverable 先找
    whitebox/ 不存在 → 回退根命中 merged queue。
    """
    corr_dlv = tmp_path / "corr-deliverables"
    _write_queue(corr_dlv / "injection_exploitation_queue.json")
    assert not (corr_dlv / "whitebox").exists()

    assert has_correlation_results(corr_dlv, ["injection", "xss"]) is True


def test_has_correlation_results_new_structure(tmp_path: Path):
    """关联 workspace 新结构（whitebox/queue）→ True。"""
    corr_dlv = tmp_path / "corr-deliverables"
    _write_queue(corr_dlv / "whitebox" / "xss_exploitation_queue.json")

    assert has_correlation_results(corr_dlv, ["injection", "xss"]) is True


def test_has_correlation_results_no_queue(tmp_path: Path):
    """无 queue → False。"""
    corr_dlv = tmp_path / "corr-deliverables"
    corr_dlv.mkdir()

    assert has_correlation_results(corr_dlv, ["injection"]) is False


def test_has_correlation_results_empty_vuln_classes(tmp_path: Path):
    """vuln_classes 为空 → False（短路）。"""
    assert has_correlation_results(tmp_path, []) is False
