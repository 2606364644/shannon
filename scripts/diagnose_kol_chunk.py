#!/usr/bin/env python3
"""诊断 kol sink-discovery chunk 构成: 为什么 threshold 提升后 chunk 数仍 259?

假设: 259 = 有 suspicious call 的文件数, 每个文件源码 < 12K token -> 早就是 1 文件 1 chunk,
threshold 12K->750K 对「已聚合到文件下限」的情况无效。

打印: suspicious 总数 / 文件数 / 各 threshold(12K|96K|750K) 下 chunk 数 / 每文件 token 分布。
"""
import sys
from pathlib import Path
from collections import defaultdict

from shannon_core.code_index import _parse_and_detect_sync
from shannon_core.code_index.file_discovery import discover_security_files
from shannon_core.code_index.parser import detect_language
from shannon_core.code_index.parsers import get_parser
from shannon_core.code_index.llm_concurrency import chunk_items_by_file, _estimate_tokens

REPO = "/root/shannon-py/repos/backend/kol_mapping_service"


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
    print(f"=== 259 == 文件数? {file_count == 259} ===")

    tokens = sorted(by_file_tokens.values())
    print(f"\n=== 每文件 token 分布 ===")
    print(f"  min={tokens[0]}  max={tokens[-1]}  avg={sum(tokens)//len(tokens)}")
    # 分位
    import statistics
    print(f"  median={statistics.median(tokens)}  p90={tokens[int(len(tokens)*0.9)]}")
    # 多少文件 < 12K token(若全部 -> threshold 提升无效)
    under_12k = sum(1 for t in tokens if t < 12_000)
    under_96k = sum(1 for t in tokens if t < 96_000)
    print(f"  <12K token 的文件: {under_12k}/{file_count}")
    print(f"  <96K token 的文件: {under_96k}/{file_count}")

    print(f"\n=== 各 threshold 下 chunk 数 ===")
    for thr in (12_000, 96_000, 750_000):
        chunks = chunk_items_by_file(
            suspicious, block_of=lambda sc: sc.block, token_threshold=thr)
        print(f"  threshold={thr:>7,}: {len(chunks)} chunks")
    print(f"\n结论: 若三档 chunk 数都=259=文件数 -> 瓶颈是文件数, 不是 threshold。")
    print(f"      真正瓶颈 = {file_count} 次跨文件 LLM 调用(每文件 1 次独立 CLI 子进程)。")


if __name__ == "__main__":
    main()
