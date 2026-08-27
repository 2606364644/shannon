import json

import pytest

# 函数已 activity 化搬到 activities(workflows.py 经 activities.load_correlation_context 引用)
from supernova_blackbox.pipeline.activities import load_correlation_context


@pytest.mark.asyncio
async def test_load_correlation_context_when_files_exist(tmp_path):
    dlv = tmp_path / "deliverables"
    dlv.mkdir()
    (dlv / "cross-service-topology.json").write_text(
        json.dumps({"services": [], "edges": []}), encoding="utf-8"
    )
    (dlv / "trust-boundaries.json").write_text("[]", encoding="utf-8")
    ctx = await load_correlation_context(str(tmp_path))
    assert ctx is not None
    assert ctx["topology"]["edges"] == []
    assert ctx["boundaries"] == []


@pytest.mark.asyncio
async def test_load_correlation_context_none_when_absent(tmp_path):
    assert await load_correlation_context(str(tmp_path)) is None
