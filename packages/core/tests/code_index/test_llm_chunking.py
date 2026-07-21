"""文件级聚合 chunking 单测 — sink/source 补召回从 per-function 改文件级(spec
2026-07-10 §3.1)。

通用工具 chunk_items_by_file: 按 file_path 分组 + 按 token 贪心装箱 → FileChunk。
小文件 1 chunk(聚合收益); 大文件按函数拆多 chunk(防爆 LLM context)。
"""
from dataclasses import dataclass

from supernova_core.code_index.llm_concurrency import (
    FileChunk,
    _estimate_tokens,
    chunk_items_by_file,
)
from supernova_core.code_index.models import FuncBlock


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
    assert chunk_items_by_file(
        [], block_of=lambda it: it.block, token_threshold=12_000, max_calls=100) == []


def test_chunk_small_file_single_chunk():
    """小文件(总 token < 阈值): 多函数多 item → 1 chunk(全文件聚合,spec §3.1 收益)。"""
    b1 = _blk("app.py", "f", 1)
    b2 = _blk("app.py", "g", 10)
    items = [_Item(b1), _Item(b1), _Item(b2)]  # b1 两个 item, b2 一个
    chunks = chunk_items_by_file(
        items, block_of=lambda it: it.block, token_threshold=12_000, max_calls=100)
    assert len(chunks) == 1
    assert chunks[0].file_paths == ("app.py",)
    assert len(chunks[0].items) == 3
    assert {b.function_name for b in chunks[0].blocks} == {"f", "g"}


# === spec 2026-07-10: 跨文件贪心合并(文件作装箱单位 + 多小文件包合并) ============


def test_chunk_cross_file_merges_small_files():
    """两个小文件(各 < threshold、各 < max_calls)→ 跨文件贪心合并成 1 chunk(spec §3 模块1 核心)。

    file_paths 含两文件; blocks 含两文件函数; items 含全部。这是本 spec 核心收益
    (kol 259 文件 → ~7 chunk)。旧版「绝不跨文件」会把这拆成 2 chunk。
    """
    b1 = _blk("a.py", "f", 1)
    b2 = _blk("b.py", "g", 1)
    chunks = chunk_items_by_file(
        [_Item(b1), _Item(b2)], block_of=lambda it: it.block,
        token_threshold=12_000, max_calls=100)
    assert len(chunks) == 1
    assert chunks[0].file_paths == ("a.py", "b.py")
    assert {b.function_name for b in chunks[0].blocks} == {"f", "g"}
    assert len(chunks[0].items) == 2


def test_chunk_cross_file_file_paths_sorted():
    """跨文件合并: file_paths 按 file_path 字典序(spec §3 模块1 装箱顺序, 稳定可重现)。"""
    items = [_Item(_blk("z.py", "z", 1)), _Item(_blk("a.py", "a", 1)),
             _Item(_blk("m.py", "m", 1))]
    chunks = chunk_items_by_file(
        items, block_of=lambda it: it.block, token_threshold=12_000, max_calls=100)
    assert len(chunks) == 1
    assert chunks[0].file_paths == ("a.py", "m.py", "z.py")


def test_chunk_max_calls_cap_splits_across_files():
    """call 数软上限(spec §3 模块2): 多文件累加 call 数超 max_calls → 开新 chunk。

    max_calls=2: a.py(1)+b.py(1)=2 ≤2 同 chunk; +c.py(1)=3>2 → 开新 chunk。
    """
    items = [_Item(_blk(f"{c}.py", c, 1)) for c in ("a", "b", "c")]
    chunks = chunk_items_by_file(
        items, block_of=lambda it: it.block, token_threshold=12_000, max_calls=2)
    assert len(chunks) == 2
    assert chunks[0].file_paths == ("a.py", "b.py")
    assert chunks[1].file_paths == ("c.py",)
    assert sum(len(c.items) for c in chunks) == 3


def test_chunk_cross_file_token_threshold_splits():
    """跨文件 token 累加超 threshold → 开新 chunk(双上限之 token 维度, 跨文件累加)。

    4 文件各 ~300 tokens, threshold=700: a+b=600≤700; +c=900>700 → 开新; c+d=600≤700。
    """
    big = "x = 1\n" * 200  # ~300 tokens/block
    items = [_Item(_blk(f"{c}.py", c, 1, big)) for c in ("a", "b", "c", "d")]
    chunks = chunk_items_by_file(
        items, block_of=lambda it: it.block, token_threshold=700, max_calls=100)
    assert len(chunks) == 2
    assert chunks[0].file_paths == ("a.py", "b.py")
    assert chunks[1].file_paths == ("c.py", "d.py")
    assert sum(len(c.items) for c in chunks) == 4


def test_chunk_large_file_splits_by_function():
    """单文件超 token threshold → 文件内按函数退化拆多 chunk(spec §3 模块1 退化)。"""
    big = "x = 1\n" * 200  # ~1200 chars → ~300 tokens/block
    b1 = _blk("big.go", "A", 1, big)
    b2 = _blk("big.go", "B", 100, big)
    chunks = chunk_items_by_file(
        [_Item(b1), _Item(b2)], block_of=lambda it: it.block,
        token_threshold=100, max_calls=100)
    assert len(chunks) == 2  # 每 block ~300 tokens > 100 → 各自一 chunk(退化拆分)
    assert all(c.file_paths == ("big.go",) for c in chunks)


def test_chunk_keeps_same_block_items_together():
    """同 block 的多个 item 必须落在同一 chunk(不被拆散,chunk 单位 = 函数)。"""
    b1 = _blk("app.py", "f", 1)
    chunks = chunk_items_by_file(
        [_Item(b1), _Item(b1), _Item(b1)], block_of=lambda it: it.block,
        token_threshold=12_000, max_calls=100)
    assert len(chunks) == 1
    assert len(chunks[0].items) == 3


def test_chunk_same_file_blocks_not_split_across_chunks():
    """同文件多 block 不被拆到不同 chunk(除非单文件超限退化, spec §3 模块1 保证)。"""
    blocks = [_blk("app.py", f"f{i}", 1 + i, "z = 0\n") for i in range(3)]  # tiny
    items = [_Item(b) for b in blocks]
    chunks = chunk_items_by_file(
        items, block_of=lambda it: it.block, token_threshold=12_000, max_calls=100)
    assert len(chunks) == 1
    assert chunks[0].file_paths == ("app.py",)
    assert len(chunks[0].blocks) == 3


def test_chunk_single_oversized_block_is_own_chunk():
    """单个 block 自身超阈值 → 独立成 chunk(chunk 单位是函数,无法再拆,不爆不无限分)。"""
    big = "y = 2\n" * 500  # ~3000 chars → ~750 tokens
    b = _blk("huge.py", "big", 1, big)
    chunks = chunk_items_by_file(
        [_Item(b)], block_of=lambda it: it.block, token_threshold=100, max_calls=100)
    assert len(chunks) == 1  # 不能再拆,1 chunk 容纳这 1 个超大函数
    assert chunks[0].blocks[0].function_name == "big"


def test_chunk_single_file_token_overload_degrades_to_block_split():
    """单文件包总 token > threshold → 文件内按 block 退化拆分, block 连续保序(spec §3 模块1)。

    pkg_tokens=900 > 500 → 退化; 每 block ~300: A≤500, +B=600>500 flush[A]; B+C=600>500 flush[B]; [C]。
    """
    big = "y = 2\n" * 200  # ~300 tokens/block
    items = [_Item(_blk("big.go", "A", 1, big)),
             _Item(_blk("big.go", "B", 100, big)),
             _Item(_blk("big.go", "C", 200, big))]
    chunks = chunk_items_by_file(
        items, block_of=lambda it: it.block, token_threshold=500, max_calls=100)
    assert len(chunks) == 3
    assert all(c.file_paths == ("big.go",) for c in chunks)  # 退化拆分仍是单文件
    assert [c.blocks[0].function_name for c in chunks] == ["A", "B", "C"]  # 连续保序


def test_chunk_single_file_call_overload_degrades_to_block_split():
    """单文件包 call 数 > max_calls → 文件内按 block 退化拆分(spec §3 模块1, call 维度)。

    max_calls=2, 5 calls: pkg_calls=5>2 → 退化; b1(3 calls)>2 但 block 原子不可拆 →
    b1 独立一 chunk(3 calls); b2(2 calls) 退到下一 chunk。同 block 的 items 必同 chunk。
    """
    b1 = _blk("svc.py", "f", 1)
    b2 = _blk("svc.py", "g", 10)
    items = [_Item(b1), _Item(b1), _Item(b1), _Item(b2), _Item(b2)]  # 5 calls
    chunks = chunk_items_by_file(
        items, block_of=lambda it: it.block, token_threshold=12_000, max_calls=2)
    assert sum(len(c.items) for c in chunks) == 5
    assert all(c.file_paths == ("svc.py",) for c in chunks)
    b1_chunk = [c for c in chunks if c.blocks and c.blocks[0].function_name == "f"]
    assert len(b1_chunk) == 1 and len(b1_chunk[0].items) == 3  # b1 的 3 个 item 不拆


def test_chunk_fills_until_threshold_then_splits():
    """贪心装箱: 小函数累加到超阈值才开新 chunk(最大化聚合,而非一函数一 chunk)。"""
    small = "z = 0\n" * 10  # ~60 chars → ~15 tokens/block
    blocks = [_blk("app.py", f"f{i}", 1 + i, small) for i in range(5)]  # 5 × 15 = 75 tokens
    items = [_Item(b) for b in blocks]
    # 单文件 75 tokens > 40 → 退化; f0+f1=30 ≤40; +f2=45>40 flush; ...
    chunks = chunk_items_by_file(
        items, block_of=lambda it: it.block, token_threshold=40, max_calls=100)
    assert len(chunks) >= 2  # 不是 5(一函数一 chunk), 也不是 1(超阈值会拆)
    assert sum(len(c.items) for c in chunks) == 5


def test_chunk_token_threshold_is_required():
    """token_threshold 必填(无默认), 防误用旧 12K 硬编码(spec §6 底层纯函数语义)。"""
    import pytest
    b = _blk("app.py", "f", 1)
    with pytest.raises(TypeError):
        chunk_items_by_file([_Item(b)], block_of=lambda it: it.block, max_calls=100)  # 缺 token_threshold


def test_chunk_max_calls_is_required():
    """max_calls 必填(无默认, spec §6); 默认值在 discovery 层从 env 读 get_chunk_max_calls()。"""
    import pytest
    b = _blk("app.py", "f", 1)
    with pytest.raises(TypeError):
        chunk_items_by_file([_Item(b)], block_of=lambda it: it.block, token_threshold=12_000)  # 缺 max_calls


def test_chunk_file_paths_is_tuple():
    """FileChunk.file_paths 是 tuple(spec §3 模块3: str -> tuple[str,...])。"""
    b = _blk("app.py", "f", 1)
    chunk = chunk_items_by_file(
        [_Item(b)], block_of=lambda it: it.block, token_threshold=12_000, max_calls=100)[0]
    assert isinstance(chunk.file_paths, tuple)
    assert chunk.file_paths == ("app.py",)


def test_file_chunk_is_frozen():
    """FileChunk frozen=True: chunk 是不可变分组结果,防误改。"""
    b = _blk("app.py", "f", 1)
    chunk = chunk_items_by_file(
        [_Item(b)], block_of=lambda it: it.block, token_threshold=12_000, max_calls=100)[0]
    assert isinstance(chunk, FileChunk)
    try:
        chunk.file_paths = ("other.py",)  # type: ignore[misc]
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
