"""P4: 数据流视图组装活动——失败不阻塞，成功落 intermediate/dataflow_view.json。

run_assemble_dataflow_view 是 non-fatal 报告增强活动（spec 2026-08-20 §3 P4）：
- 组装器返 None（全产物缺）→ skipped 不落盘；
- 组装器抛异常 → logger.warning + skipped 返回值，不抛 ApplicationFailure
  （区别于同文件 fatal 活动惯例——数据流视图绝不阻塞扫描）；
- 成功 → atomic_write_json 落 intermediate/dataflow_view.json + 返回 ok/trees 数。

打桩方式：活动内 assemble_dataflow_view 走函数级 deferred import，故 patch
``supernova_core.services.dataflow_view.assemble_dataflow_view`` 即生效；input
只经 ``_get_paths`` 解耦——直接传 object() 并 patch ``activities._get_paths``。
asyncio_mode=auto：async 测试直接 await 活动函数（@activity.defn 不需要 worker
context，与 test_run_gitnexus_chain_verdict_second_order 同惯例）。
"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def deliverables_with_products(tmp_path: Path) -> Path:
    (tmp_path / "intermediate").mkdir()
    (tmp_path / "intermediate" / "injection_chain_verdicts.json").write_text(
        json.dumps({"verdicts": []}))
    return tmp_path


async def test_activity_writes_dataflow_view(deliverables_with_products: Path):
    from supernova_whitebox.pipeline import activities

    view = {
        "schema_version": 1, "summary": {},
        "trees": [{"tree_id": "t1"}, {"tree_id": "t2"}],
        "control_findings": [], "safe_vectors": [],
    }
    with patch("supernova_core.services.dataflow_view.assemble_dataflow_view",
               return_value=view):
        with patch.object(activities, "_get_paths",
                          return_value=(Path("/r"), deliverables_with_products, Path("/w"))):
            result = await activities.run_assemble_dataflow_view(input=object())
    assert result["status"] == "ok"
    assert result["trees"] == 2
    written = json.loads(
        (deliverables_with_products / "intermediate" / "dataflow_view.json").read_text(
            encoding="utf-8"))
    assert written == view


async def test_activity_non_fatal_on_exception(deliverables_with_products: Path):
    """组装器抛 → warning + 不产文件 + 不阻塞（不 raise ApplicationFailure）。"""
    from supernova_whitebox.pipeline import activities

    with patch("supernova_core.services.dataflow_view.assemble_dataflow_view",
               side_effect=RuntimeError("boom")):
        with patch.object(activities, "_get_paths",
                          return_value=(Path("/r"), deliverables_with_products, Path("/w"))):
            result = await activities.run_assemble_dataflow_view(input=object())
    assert result["status"] == "skipped"
    assert "boom" in result["reason"]
    assert not (deliverables_with_products / "intermediate" / "dataflow_view.json").exists()


async def test_activity_skipped_when_assembler_returns_none(deliverables_with_products: Path):
    """组装器返 None（全产物缺）→ skipped 不落盘。"""
    from supernova_whitebox.pipeline import activities

    with patch("supernova_core.services.dataflow_view.assemble_dataflow_view",
               return_value=None):
        with patch.object(activities, "_get_paths",
                          return_value=(Path("/r"), deliverables_with_products, Path("/w"))):
            result = await activities.run_assemble_dataflow_view(input=object())
    assert result["status"] == "skipped"
    assert result["reason"] == "no products"
    assert not (deliverables_with_products / "intermediate" / "dataflow_view.json").exists()
