"""storage_discovery_llm TDD(spec 子项⑤ Task 4).

LLM hunter for storage read/write points the hard rules missed. Mirrors
source_discovery_llm (reads → SourcePoint(STORAGE)) and sink_discovery_llm
(writes → StorageWritePoint). Soft anchors rule_id="llm-discovered-storage"
+ needs_review=True; chain_verdict re-checks on the read side.
"""
import asyncio

import pytest

from supernova_core.code_index.models import FuncBlock, ParameterSource
from supernova_core.code_index.storage_discovery_llm import (
    StorageReadCandidate,
    StorageWriteCandidate,
    discover_storage_reads_llm,
    discover_storage_writes_llm,
)
from supernova_core.code_index.storage_models import StorageMedium


def _block(file_path, func_name, start_line, source, language="java"):
    return FuncBlock(
        id=f"{file_path}:{func_name}:{start_line}", file_path=file_path,
        function_name=func_name, start_line=start_line,
        end_line=start_line + 5, source_code=source, parameters=[],
        language=language,
    )


# ===== read hunter: hard-rule miss → soft SourcePoint(STORAGE) =====


def test_discover_storage_reads_soft_source_on_llm_verdict():
    """repo.findByName(name) — not in hard rules → LLM catches it as soft
    SourcePoint(source_type=STORAGE, rule_id=llm-discovered-storage,
    needs_review=True)."""
    block = _block(
        "H.java", "f", 1,
        'void f(String name){ var x = repo.findByName(name); echo(x); }\n')
    cands = [StorageReadCandidate(block=block)]

    async def fake_llm(prompt, **kwargs):
        return ('[{"read":"repo.findByName(name)","medium":"db","token":"name",'
                '"read_var":"x","line":1,"is_storage_read":true,'
                '"rationale":"orm find"}]')

    reads, gaps = asyncio.run(discover_storage_reads_llm(cands, fake_llm))
    assert len(reads) == 1
    r = reads[0]
    assert r.source_type is ParameterSource.STORAGE
    assert r.rule_id == "llm-discovered-storage"
    assert r.needs_review is True
    assert r.param_name == "x"
    assert r.entry_point_id == block.id
    assert r.line == 1


def test_discover_storage_reads_degrades_when_llm_unavailable():
    """LLM=None → ([], []) (deterministic-fallback posture; hard rules stand)."""
    block = _block("H.java", "f", 1, 'void f(String n){ repo.findByName(n); }\n')
    cands = [StorageReadCandidate(block=block)]
    reads, gaps = asyncio.run(discover_storage_reads_llm(cands, None))
    assert reads == []
    assert gaps == []


def test_discover_storage_reads_empty_candidates_short_circuits():
    """No candidates → ([], []) without calling LLM."""
    calls: list = []

    async def fake_llm(prompt, **kwargs):
        calls.append(prompt)
        return "[]"

    reads, gaps = asyncio.run(discover_storage_reads_llm([], fake_llm))
    assert reads == [] and gaps == []
    assert calls == []


def test_discover_storage_reads_skips_non_storage_verdict():
    """is_storage_read != true → skip (mirror discover_sources_llm is_source filter)."""
    block = _block("H.java", "f", 1, 'void f(String n){ repo.findByName(n); }\n')
    cands = [StorageReadCandidate(block=block)]

    async def fake_llm(prompt, **kwargs):
        return ('[{"read":"repo.findByName(name)","medium":"db","token":"name",'
                '"read_var":"x","line":1,"is_storage_read":false,'
                '"rationale":"not storage"}]')

    reads, gaps = asyncio.run(discover_storage_reads_llm(cands, fake_llm))
    assert reads == []


def test_discover_storage_reads_routes_line_to_correct_block():
    """File-level chunk 多函数: verdict.line(文件绝对行)反查所属 block。"""
    b1 = _block("Svc.java", "f", 1, 'void f(String n){ repo.findByName(n); }\n')
    b2 = _block("Svc.java", "g", 10, 'void g(String n){ repo.findByName(n); }\n')
    cands = [StorageReadCandidate(block=b1), StorageReadCandidate(block=b2)]

    async def fake_llm(prompt, **kwargs):
        # line=12 ∈ [10,15] → b2 (g)
        return ('[{"read":"repo.findByName(name)","medium":"db","token":"name",'
                '"read_var":"y","line":12,"is_storage_read":true,"rationale":"r"}]')

    reads, _ = asyncio.run(discover_storage_reads_llm(cands, fake_llm))
    assert len(reads) == 1
    assert reads[0].entry_point_id == b2.id
    assert reads[0].line == 12


def test_discover_storage_reads_malformed_field_does_not_kill_chunk():
    """per-item 防护: 一条 verdict malformed(line=null)只跳过该条, 不丢整 chunk。"""
    b1 = _block("Svc.java", "f", 1, 'void f(String n){ repo.findByName(n); }\n')
    b2 = _block("Svc.java", "g", 10, 'void g(String n){ repo.findByName(n); }\n')
    cands = [StorageReadCandidate(block=b1), StorageReadCandidate(block=b2)]

    async def fake_llm(prompt, **kwargs):
        return ('[{"read":"repo.findByName(name)","medium":"db","token":"name",'
                '"read_var":"good","line":1,"is_storage_read":true,"rationale":"g"},'
                '{"read":"x","medium":"db","token":"y","read_var":"bad","line":null,'
                '"is_storage_read":true,"rationale":"b"}]')

    reads, _ = asyncio.run(discover_storage_reads_llm(cands, fake_llm))
    assert any(r.param_name == "good" for r in reads), \
        f"malformed line 不应丢整 chunk, good 应保留: {reads}"


# ===== write hunter: hard-rule miss → soft StorageWritePoint =====


def test_discover_storage_writes_soft_write_on_llm_verdict():
    """repo.save(name, entity) — not in hard rules → LLM catches it as soft
    StorageWritePoint(rule_id=llm-discovered-storage, needs_review=True)."""
    block = _block(
        "H.java", "f", 1,
        'void f(String name, User u){ repo.save(name, u); }\n')
    cands = [StorageWriteCandidate(block=block)]

    async def fake_llm(prompt, **kwargs):
        return ('[{"write":"repo.save(name, u)","medium":"db","token":"name",'
                '"written_arg":"u","line":1,"is_storage_write":true,'
                '"rationale":"orm save"}]')

    writes, gaps = asyncio.run(discover_storage_writes_llm(cands, fake_llm))
    assert len(writes) == 1
    w = writes[0]
    assert w.rule_id == "llm-discovered-storage"
    assert w.needs_review is True
    assert w.medium is StorageMedium.DB
    assert w.storage_token == "name"
    assert w.written_expr == "u"
    assert w.caller_id == block.id


def test_discover_storage_writes_degrades_when_llm_unavailable():
    """LLM=None → ([], []) (deterministic-fallback)."""
    block = _block("H.java", "f", 1, 'void f(){ repo.save("k", x); }\n')
    cands = [StorageWriteCandidate(block=block)]
    writes, gaps = asyncio.run(discover_storage_writes_llm(cands, None))
    assert writes == [] and gaps == []


def test_discover_storage_writes_dynamic_token_marked_unresolvable():
    """token=null (dynamic) → storage_token="unresolvable" (mirror hard-rule behavior)."""
    block = _block("H.java", "f", 1, 'void f(String k){ cache.set(k, v); }\n')
    cands = [StorageWriteCandidate(block=block)]

    async def fake_llm(prompt, **kwargs):
        return ('[{"write":"cache.set(k, v)","medium":"cache","token":null,'
                '"written_arg":"v","line":1,"is_storage_write":true,'
                '"rationale":"dynamic key"}]')

    writes, _ = asyncio.run(discover_storage_writes_llm(cands, fake_llm))
    assert len(writes) == 1
    assert writes[0].storage_token == "unresolvable"


# ===== prompt content guard =====


def test_discover_storage_reads_prompt_includes_file_and_function():
    """Prompt 必含 file_paths join + 函数源码(文件级聚合, spec §3.1)."""
    block = _block("H.java", "f", 1, 'void f(String n){ repo.findByName(n); }\n')
    cands = [StorageReadCandidate(block=block)]
    seen: list[str] = []

    async def fake_llm(prompt, **kwargs):
        seen.append(prompt)
        return "[]"

    asyncio.run(discover_storage_reads_llm(cands, fake_llm))
    assert seen
    assert "H.java" in seen[0]
    assert "repo.findByName" in seen[0]
