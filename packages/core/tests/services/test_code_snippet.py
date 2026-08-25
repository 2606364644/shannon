# packages/core/tests/services/test_code_snippet.py
import pytest
from supernova_core.services.code_snippet import annotate_direct, extract_snippet

@pytest.mark.asyncio
async def test_extract_snippet_reads_range(tmp_path):
    f = tmp_path / "contributions.js"
    f.write_text("\n".join(f"line{i}" for i in range(1, 41)))
    snippet = await extract_snippet(tmp_path, "contributions.js:32")
    assert snippet is not None
    assert "line29" in snippet and "line35" in snippet and "line28" not in snippet

@pytest.mark.asyncio
async def test_extract_snippet_none_cases(tmp_path):
    assert await extract_snippet(None, "x.js:1") is None
    assert await extract_snippet(tmp_path, "missing.js:1") is None
    assert await extract_snippet(tmp_path, None) is None

def test_annotate_direct():
    entries = [{"parameter": "preTax", "sink_location": "a.js:32"},
               {"parameter": "afterTax", "sink_location": "a.js:33"}]
    snippet = "preTax = eval(req.body.preTax);"
    annotate_direct(entries, snippet)
    assert entries[0]["direct"] is True
    assert entries[1]["direct"] is False
