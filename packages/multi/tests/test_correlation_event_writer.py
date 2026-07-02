import asyncio
import json

import pytest

from shannon_multi.correlation_event_writer import CorrelationEventWriter


def _rows(p):
    return [json.loads(l) for l in p.read_text("utf-8").splitlines() if l.strip()]


@pytest.mark.asyncio
async def test_repo_event_format(tmp_path):
    w = CorrelationEventWriter(tmp_path / "e.ndjson")
    await w.repo("svc-a", "started")
    r = _rows(tmp_path / "e.ndjson")[-1]
    assert r["category"] == "CONTROL"
    assert r["type"] == "correlation_progress"
    assert r["node"] == "repo" and r["name"] == "svc-a" and r["status"] == "started"
    assert "ts" in r


@pytest.mark.asyncio
async def test_phase_and_edge(tmp_path):
    w = CorrelationEventWriter(tmp_path / "e.ndjson")
    await w.phase("correlation", "started")
    await w.edge("svc-a->svc-b", "completed", detail="grpc")
    rows = _rows(tmp_path / "e.ndjson")
    assert rows[0]["node"] == "phase" and rows[0]["name"] == "correlation"
    assert rows[1]["node"] == "edge" and rows[1]["detail"] == "grpc"


@pytest.mark.asyncio
async def test_scan_end(tmp_path):
    w = CorrelationEventWriter(tmp_path / "e.ndjson")
    await w.scan_end("completed")
    r = _rows(tmp_path / "e.ndjson")[-1]
    assert r["type"] == "scan_end" and r["status"] == "completed"


@pytest.mark.asyncio
async def test_concurrent_edges_no_interleave(tmp_path):
    w = CorrelationEventWriter(tmp_path / "e.ndjson")
    await asyncio.gather(*(w.edge(f"a->b:{i}", "completed") for i in range(30)))
    rows = _rows(tmp_path / "e.ndjson")
    assert len(rows) == 30  # 每行完整可 parse = Lock 串行无交错


@pytest.mark.asyncio
async def test_creates_parent_dir(tmp_path):
    w = CorrelationEventWriter(tmp_path / "nested" / "dir" / "e.ndjson")
    await w.phase("correlation", "started")
    assert (tmp_path / "nested" / "dir" / "e.ndjson").exists()


@pytest.mark.asyncio
async def test_edge_maps_ok_to_completed(tmp_path):
    w = CorrelationEventWriter(tmp_path / "e.ndjson")
    await w.edge("a->b", "ok")
    r = _rows(tmp_path / "e.ndjson")[-1]
    assert r["status"] == "completed"
    assert "raw=ok" in r["detail"]


@pytest.mark.asyncio
async def test_edge_maps_error_to_failed(tmp_path):
    w = CorrelationEventWriter(tmp_path / "e.ndjson")
    await w.edge("a->b", "error", "network down")
    r = _rows(tmp_path / "e.ndjson")[-1]
    assert r["status"] == "failed"
    assert "network down" in r["detail"]
    assert "raw=error" in r["detail"]


@pytest.mark.asyncio
async def test_edge_maps_low_to_failed(tmp_path):
    """low 信任 → 视作未通过 → failed（TopologyEdge.status 允许 low，final-review Finding 2）。"""
    w = CorrelationEventWriter(tmp_path / "e.ndjson")
    await w.edge("a->b", "low")
    r = _rows(tmp_path / "e.ndjson")[-1]
    assert r["status"] == "failed"
    assert "raw=low" in r["detail"]


@pytest.mark.asyncio
async def test_edge_maps_declared_missing_to_failed(tmp_path):
    """declared-missing → failed（TopologyEdge.status 允许 declared-missing，final-review Finding 2）。"""
    w = CorrelationEventWriter(tmp_path / "e.ndjson")
    await w.edge("a->b", "declared-missing")
    r = _rows(tmp_path / "e.ndjson")[-1]
    assert r["status"] == "failed"
    assert "raw=declared-missing" in r["detail"]


@pytest.mark.asyncio
async def test_edge_passes_through_spec_status(tmp_path):
    w = CorrelationEventWriter(tmp_path / "e.ndjson")
    await w.edge("a->b", "completed")
    r = _rows(tmp_path / "e.ndjson")[-1]
    assert r["status"] == "completed"
    assert r["detail"] is None  # spec 值透传，无 raw 追加
