"""文件级聚合 chunking 单测 — sink/source 补召回从 per-function 改文件级(spec
2026-07-10 §3.1)。

通用工具 chunk_items_by_file: 按 file_path 分组 + 按 token 贪心装箱 → FileChunk。
小文件 1 chunk(聚合收益); 大文件按函数拆多 chunk(防爆 LLM context)。
"""
from dataclasses import dataclass

from shannon_core.code_index.llm_concurrency import (
    FileChunk,
    _estimate_tokens,
    chunk_items_by_file,
)
from shannon_core.code_index.models import FuncBlock


@dataclass
class _Item:
    """测试用 item: 只需带 block(chunking 通用,不关心 call/candidate 细节)。"""
    block: FuncBlock


def _blk(file, name, line, source="def f(): pass\n", language="python"):
    return FuncBlock(
        id=f"{file}:{name}:{line}", file_path=file, function_name=name,
        start_line=line, end_line=line + 5, source_code=source,
        parameters=[], language=language,
    )


def test_chunk_empty_returns_empty():
    assert chunk_items_by_file([], block_of=lambda it: it.block, token_threshold=12_000) == []


def test_chunk_small_file_single_chunk():
    """小文件(总 token < 阈值): 多函数多 item → 1 chunk(全文件聚合,spec §3.1 收益)。"""
    b1 = _blk("app.py", "f", 1)
    b2 = _blk("app.py", "g", 10)
    items = [_Item(b1), _Item(b1), _Item(b2)]  # b1 两个 item, b2 一个
    chunks = chunk_items_by_file(items, block_of=lambda it: it.block, token_threshold=12_000)
    assert len(chunks) == 1
    assert chunks[0].file_path == "app.py"
    assert len(chunks[0].items) == 3
    assert {b.function_name for b in chunks[0].blocks} == {"f", "g"}


def test_chunk_separates_different_files():
    """不同文件的 item → 不同 chunk(绝不混文件,分组键 = file_path)。"""
    items = [_Item(_blk("a.py", "f", 1)), _Item(_blk("b.py", "g", 1))]
    chunks = chunk_items_by_file(items, block_of=lambda it: it.block, token_threshold=12_000)
    assert sorted(c.file_path for c in chunks) == ["a.py", "b.py"]


def test_chunk_large_file_splits_by_function():
    """大文件(总 token > 阈值)→ 按函数拆多 chunk;每个函数源码大各自成 chunk。"""
    big = "x = 1\n" * 200  # ~1200 chars → ~300 tokens/block
    b1 = _blk("big.go", "A", 1, big)
    b2 = _blk("big.go", "B", 100, big)
    chunks = chunk_items_by_file(
        [_Item(b1), _Item(b2)], block_of=lambda it: it.block, token_threshold=100)
    assert len(chunks) == 2  # 每 block ~300 tokens > 100 → 各自一 chunk


def test_chunk_keeps_same_block_items_together():
    """同 block 的多个 item 必须落在同一 chunk(不被拆散,chunk 单位 = 函数)。"""
    b1 = _blk("app.py", "f", 1)
    chunks = chunk_items_by_file(
        [_Item(b1), _Item(b1), _Item(b1)], block_of=lambda it: it.block, token_threshold=12_000)
    assert len(chunks) == 1
    assert len(chunks[0].items) == 3


def test_chunk_single_oversized_block_is_own_chunk():
    """单个 block 自身超阈值 → 独立成 chunk(chunk 单位是函数,无法再拆,不爆不无限分)。"""
    big = "y = 2\n" * 500  # ~3000 chars → ~750 tokens
    b = _blk("huge.py", "big", 1, big)
    chunks = chunk_items_by_file(
        [_Item(b)], block_of=lambda it: it.block, token_threshold=100)
    assert len(chunks) == 1  # 不能再拆,1 chunk 容纳这 1 个超大函数
    assert chunks[0].blocks[0].function_name == "big"


def test_chunk_fills_until_threshold_then_splits():
    """贪心装箱: 小函数累加到超阈值才开新 chunk(最大化聚合,而非一函数一 chunk)。"""
    small = "z = 0\n" * 10  # ~60 chars → ~15 tokens/block
    blocks = [_blk("app.py", f"f{i}", 1 + i, small) for i in range(5)]  # 5 × 15 = 75 tokens
    items = [_Item(b) for b in blocks]
    # threshold=40: f0+f1=30 ≤40 同 chunk; +f2=45>40 → 开新 chunk
    chunks = chunk_items_by_file(
        items, block_of=lambda it: it.block, token_threshold=40)
    assert len(chunks) >= 2  # 不是 5(一函数一 chunk), 也不是 1(超阈值会拆)
    # 所有 item 都被覆盖, 无丢失
    assert sum(len(c.items) for c in chunks) == 5


def test_chunk_token_threshold_is_required():
    """token_threshold 现为必填(无默认), 防误用旧 12K 硬编码(spec §3 模块3)。"""
    import pytest
    b = _blk("app.py", "f", 1)
    with pytest.raises(TypeError):
        chunk_items_by_file([_Item(b)], block_of=lambda it: it.block)  # 缺 token_threshold


def test_file_chunk_is_frozen():
    """FileChunk frozen=True: chunk 是不可变分组结果,防误改。"""
    b = _blk("app.py", "f", 1)
    chunk = chunk_items_by_file(
        [_Item(b)], block_of=lambda it: it.block, token_threshold=12_000)[0]
    assert isinstance(chunk, FileChunk)
    try:
        chunk.file_path = "other.py"  # type: ignore[misc]
        assert False, "FileChunk 应 frozen, 不可赋值"
    except (AttributeError, Exception):
        pass


# === token 估算 CJK 加权(spec 2026-07-10) ===

def test_estimate_tokens_ascii_approx_len_div_4():
    """纯 ASCII: ~4 char/token(与旧 len//4 行为一致)。"""
    text = "def f():\n    return 1\n"  # 22 chars
    assert _estimate_tokens(text) == 6  # ceil(0*1.5 + 22/4) = ceil(5.5) = 6


def test_estimate_tokens_chinese_not_underestimated():
    """纯中文: ~1.5 token/char(不再被 len//4 低估成 0.25 token/char)。"""
    text = "中" * 100  # 100 chars 全 CJK
    assert _estimate_tokens(text) == 150  # ceil(100*1.5 + 0/4)


def test_estimate_tokens_mixed_cjk_and_ascii():
    """混合: CJK 按 1.5, 其余按 /4。"""
    cjk = "你好世界"  # 4 CJK chars
    ascii_part = "x" * 40  # 40 ascii
    text = cjk + ascii_part  # len=44, cjk=4
    expected = 4 * 1.5 + 40 / 4  # 6 + 10 = 16
    assert _estimate_tokens(text) == 16  # ceil(16.0)=16


def test_estimate_tokens_japanese_korean_counted_as_cjk():
    """日文/韩文也按 CJK 高权重(防低估)。"""
    jp = "こんにちは" * 10  # 50 CJK chars
    kr = "안녕하세요" * 10  # 50 CJK chars
    text = jp + kr  # 100 CJK
    assert _estimate_tokens(text) == 150


def test_estimate_tokens_empty():
    assert _estimate_tokens("") == 0


def test_estimate_tokens_never_underestimates_pure_cjk_vs_len_div_4():
    """核心不变量: 对纯中文, CJK 加权 > len//4(防 context 爆)。"""
    text = "代码注释中文" * 50  # 300 CJK chars
    assert _estimate_tokens(text) > len(text) // 4  # 450 > 75
