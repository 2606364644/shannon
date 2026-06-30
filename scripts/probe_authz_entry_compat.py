"""坐实 spec 两个硬模糊点（用 shannon-py 真规则，非 cypher 粗估）。

探针 1（authz 兼容性）：process trace 的 terminal(path[-1]) 是不是 side-effect sink？
  vs 扫全链找 side-effect —— 决定 authz「看终端」会不会召回归零、要不要改扫全链。
探针 2（entry 体系）：process trace 的 step1(entry) 和 detect_entry_points 重合度 +
  entry_type 分布 —— 决定 process entry 能否替代/融合现有 entry 体系。
"""
from __future__ import annotations
import asyncio, re
from pathlib import Path
from collections import defaultdict, Counter
from shannon_core.code_index.parser import detect_language, discover_source_files
from shannon_core.code_index.parsers import get_parser
from shannon_core.code_index.entry_points import detect_entry_points
from shannon_core.code_index.authz_gitnexus_track import _is_side_effect_sink
from shannon_core.code_index.gitnexus_mcp import GitNexusMCPClient

REPO = Path("/root/code/backend/statement_template_svr")
NAME = "statement_template_svr"


def parse_repo():
    lang = detect_language(REPO)
    parser = get_parser(lang)
    all_blocks, file_sources = [], {}
    for fp in discover_source_files(REPO, lang):
        try:
            file_sources[str(fp.relative_to(REPO))] = fp.read_bytes()
            all_blocks.extend(parser.parse_file(fp, REPO))
        except Exception:
            pass
    return lang, all_blocks


async def _read(c, uri):
    r = await c._send_request("resources/read", {"uri": uri})
    for item in r.get("contents", []):
        if isinstance(item, dict) and "text" in item:
            return item["text"]
    return ""


def _parse_trace(text):
    return [(int(m.group(1)), m.group(2).strip(), m.group(3).strip())
            for m in re.finditer(r"^\s*(\d+):\s*(.+?)\s*\(([^)]+)\)\s*$", text, re.MULTILINE)]


async def main():
    lang, all_blocks = parse_repo()
    by_full = {(b.file_path, b.function_name): b for b in all_blocks}
    by_name = defaultdict(list)
    for b in all_blocks:
        by_name[b.function_name].append(b)

    def resolve(name, fpath):
        b = by_full.get((fpath, name))
        if b:
            return b
        for cc in by_name.get(name, []):
            if cc.file_path == fpath or cc.file_path.endswith(fpath) or fpath.endswith(cc.file_path):
                return cc
        c = by_name.get(name, [])
        return c[0] if len(c) == 1 else None

    eps = detect_entry_points(all_blocks, lang, repo_path=str(REPO))
    ep_ids = {ep.func_block_id for ep in eps}
    ep_by_id = {ep.func_block_id: ep for ep in eps}
    print(f"=== detect_entry_points: {len(eps)} 个，entry_type 分布: {dict(Counter(e.entry_type for e in eps))}")

    async with GitNexusMCPClient(REPO) as c:
        r = await c.call_tool("cypher", {"query": "MATCH (p:Process) RETURN p.label AS label"})
        labels = [row["label"] for row in r.get("rows", []) if isinstance(row, dict) and row.get("label")]
        print(f"=== process 数: {len(labels)}\n")

        term_side = full_side = entry_in_ep = 0
        term_samples, full_only_samples, entry_outside = [], [], []
        for label in labels:
            steps = _parse_trace(await _read(c, f"gitnexus://repo/{NAME}/process/{label}"))
            if not steps:
                continue
            blocks = [resolve(n, f) for _, n, f in steps]
            term_blk = blocks[-1]
            term_is = bool(term_blk and _is_side_effect_sink(term_blk))
            full_is = any(b and _is_side_effect_sink(b) for b in blocks)
            term_side += int(term_is)
            full_side += int(full_is)
            if term_is and len(term_samples) < 5:
                term_samples.append(f"{label} → terminal:{steps[-1][1]}")
            if full_is and not term_is and len(full_only_samples) < 5:
                full_only_samples.append(f"{label} → 链中side-effect: {[s[1] for s in steps if resolve(s[1],s[2]) and _is_side_effect_sink(resolve(s[1],s[2]))]}")
            entry_blk = blocks[0]
            if entry_blk and entry_blk.id in ep_ids:
                entry_in_ep += 1
            elif entry_blk and len(entry_outside) < 10:
                entry_outside.append(f"{steps[0][1]} ({steps[0][2].split('/')[-1]})")

        print("=== 探针1：authz 看终端 vs 扫全链（side-effect sink 召回）===")
        print(f"  terminal(path[-1]) 是 side-effect: {term_side}/{len(labels)}")
        print(f"  全链任一是 side-effect:           {full_side}/{len(labels)}")
        print(f"  terminal side-effect 样例:")
        for s in term_samples: print(f"    {s}")
        print(f"  仅扫全链命中（terminal 非 side-effect 但链中有）样例:")
        for s in full_only_samples: print(f"    {s}")

        print(f"\n=== 探针2：process entry(step1) 与 detect_entry_points 重合 ===")
        print(f"  process entry 在 detect_entry_points 里: {entry_in_ep}/{len(labels)}")
        print(f"  不在 detect_entry_points 的 process entry 样例（前 10）:")
        for s in entry_outside: print(f"    {s}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
