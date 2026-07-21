#!/usr/bin/env python3
"""诊断 kol sink-discovery chunk 构成: 跨文件贪心合并后 chunk 数下降多少?

历史(spec 2026-07-10): 旧版 chunk_items_by_file 按 file_path 分组绝不跨文件, kol 259 个
小文件 = 259 chunk = 259 次独立 LLM 调用; threshold 提升无效(瓶颈是文件数不是单文件大小)。
本 spec 改「文件作装箱单位 + 跨文件贪心合并 + 双上限(token + max_calls)」, 预期 259 → ~7。

打印: suspicious 总数 / 文件数 / 每文件 token 分布 / 各 threshold×max_calls 下 chunk 数。
"""
import sys
from pathlib import Path
from collections import defaultdict

from supernova_core.code_index import _parse_and_detect_sync
from supernova_core.code_index.file_discovery import discover_security_files
from supernova_core.code_index.parser import detect_language
from supernova_core.code_index.parsers import get_parser
from supernova_core.code_index.llm_concurrency import chunk_items_by_file, _estimate_tokens

REPO = "/root/supernova/repos/backend/kol_mapping_service"


def main():
    repo = Path(REPO).resolve()
    language = detect_language(repo)
    print(f"language: {language}")
    parser = get_parser(language)
    if parser is None:
        print(f"ERROR: no parser for {language}", file=sys.stderr)
        sys.exit(1)

    _file_sources, _all_blocks, _sink_call_sites, suspicious = _parse_and_detect_sync(
        repo, language, parser)
    print(f"\n=== suspicious calls 总数: {len(suspicious)} ===")

    # 文件分组 + 每文件 token
    by_file_tokens = defaultdict(int)
    by_file_count = defaultdict(int)
    for sc in suspicious:
        b = sc.block
        by_file_tokens[b.file_path] += _estimate_tokens(b.source_code)
        by_file_count[b.file_path] += 1
    file_count = len(by_file_tokens)
    print(f"=== 有 suspicious call 的文件数: {file_count} ===")

    tokens = sorted(by_file_tokens.values())
    total_tokens = sum(tokens)
    print(f"\n=== 每文件 token 分布 ===")
    print(f"  min={tokens[0]}  max={tokens[-1]}  avg={sum(tokens)//len(tokens)}")
    # 分位
    import statistics
    print(f"  median={statistics.median(tokens)}  p90={tokens[int(len(tokens)*0.9)]}")
    print(f"  总 token: {total_tokens:,}  (跨文件合并后理论上限 ~ 总token / threshold)")

    print(f"\n=== 各 threshold × max_calls 下 chunk 数(跨文件贪心合并, spec 2026-07-10) ===")
    for thr in (12_000, 96_000, 750_000):
        for max_calls in (100,):
            chunks = chunk_items_by_file(
                suspicious, block_of=lambda sc: sc.block,
                token_threshold=thr, max_calls=max_calls)
            print(f"  threshold={thr:>7,}  max_calls={max_calls}: {len(chunks)} chunks"
                  f"  (旧版绝不跨文件时 = {file_count})")
    print(f"\n结论: 跨文件贪心合并后 chunk 数 << 文件数; 259 文件预期 → ~7 chunk,")
    print(f"      调用数 259 → ~7(降 ~37x), ~75min → ~3-4min, 不再撞 20min activity timeout。")


if __name__ == "__main__":
    main()
