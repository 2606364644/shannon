"""D 方案可行性最后一块：process trace ↔ detect_sinks 真 sink 的 match 覆盖率。

同时验证两件事：
① 召回：多少 sink 函数落在 process trace 上（D 比当前空壳强多少）
② 对齐：trace 的 (name, filePath) 能否对齐到 tree-sitter FuncBlock.id（D 的核心转换）
"""
from __future__ import annotations
import asyncio, re
from pathlib import Path
from collections import defaultdict
from shannon_core.code_index.parser import detect_language, discover_source_files
from shannon_core.code_index.parsers import get_parser
from shannon_core.code_index.sink_detector import detect_sinks
from shannon_core.code_index.gitnexus_mcp import GitNexusMCPClient

REPO = Path("/root/code/backend/statement_template_svr")
NAME = "statement_template_svr"


def parse_repo_and_sinks():
    lang = detect_language(REPO)
    parser = get_parser(lang)
    source_files = discover_source_files(REPO, lang)
    all_blocks, file_sources = [], {}
    for fp in source_files:
        try:
            rel = str(fp.relative_to(REPO))
            file_sources[rel] = fp.read_bytes()
            all_blocks.extend(parser.parse_file(fp, REPO))
        except Exception:
            pass
    sinks = detect_sinks(all_blocks, parser, source_provider=lambda b: file_sources.get(b.file_path))
    return all_blocks, sinks


async def _read(c, uri):
    r = await c._send_request("resources/read", {"uri": uri})
    for item in r.get("contents", []):
        if isinstance(item, dict) and "text" in item:
            return item["text"]
    return ""


def _parse_trace(text):
    """trace 步骤 → [(step, name, filePath)]。"""
    return [(int(m.group(1)), m.group(2).strip(), m.group(3).strip())
            for m in re.finditer(r"^\s*(\d+):\s*(.+?)\s*\(([^)]+)\)\s*$", text, re.MULTILINE)]


def _parse_processes(text):
    return [m.strip().strip('"') for m in re.findall(r"^\s*- name:\s*(.+)$", text, re.MULTILINE)]


async def main():
    print("=== ① parse 全仓 + detect_sinks（拿真 sink）===")
    all_blocks, sinks = parse_repo_and_sinks()
    sink_caller_ids = {s.caller_id for s in sinks}
    id_to_block = {b.id: b for b in all_blocks}
    id_to_callees = defaultdict(list)
    for s in sinks:
        id_to_callees[s.caller_id].append(s.callee_name)
    # 对齐索引：(filePath, name) 精确 + name 模糊（GitNexus filePath 可能和 tree-sitter 略有出入）
    by_full = {(b.file_path, b.function_name): b for b in all_blocks}
    by_name = defaultdict(list)
    for b in all_blocks:
        by_name[b.function_name].append(b)
    print(f"  blocks={len(all_blocks)}  sinks={len(sinks)}  含 sink 的函数={len(sink_caller_ids)}")
    print(f"  sink 函数样例: " + ", ".join(
        f"{id_to_block[i].function_name}({id_to_block[i].file_path.split('/')[-1]})→{id_to_callees[i]}"
        for i in list(sink_caller_ids)[:5]))

    def resolve(name, fpath):
        b = by_full.get((fpath, name))
        if b:
            return b
        cands = by_name.get(name, [])
        for cc in cands:
            if cc.file_path == fpath or cc.file_path.endswith(fpath) or fpath.endswith(cc.file_path):
                return cc
        return cands[0] if len(cands) == 1 else None

    print("\n=== ② 读所有 process traces + match sink ===")
    matched, uncovered = [], set(sink_caller_ids)
    total_traces = step_resolve_fail = 0
    async with GitNexusMCPClient(REPO) as c:
        # processes resource 截断只给 20；用 cypher 拿全 140 个 Process 的 label
        r = await c.call_tool("cypher", {"query": "MATCH (p:Process) RETURN p.label AS label"})
        labels = [row["label"] for row in r.get("rows", []) if isinstance(row, dict) and row.get("label")]
        print(f"  process 数（cypher 全量）: {len(labels)}")
        for label in labels:
            steps = _parse_trace(await _read(c, f"gitnexus://repo/{NAME}/process/{label}"))
            if not steps:
                continue
            total_traces += 1
            hits = []
            for step, name, fpath in steps:
                b = resolve(name, fpath)
                if b is None:
                    step_resolve_fail += 1
                elif b.id in sink_caller_ids:
                    hits.append((step, name, fpath.split('/')[-1]))
                    uncovered.discard(b.id)
            if hits:
                matched.append((label, steps, hits))

    print(f"  解析出步骤的 trace: {total_traces}/{len(labels)}")
    print(f"  step→FuncBlock 解析失败次数: {step_resolve_fail}")
    print(f"  ★ 含 sink 的 process trace: {len(matched)}/{total_traces}")
    print(f"  ★ sink 函数被 trace 覆盖: {len(sink_caller_ids) - len(uncovered)}/{len(sink_caller_ids)}")
    print(f"  ★ 未被任何 trace 覆盖的 sink 函数: {len(uncovered)}")

    print("\n=== ③ 命中样例（source→…→sink，前 10）===")
    for label, steps, hits in matched[:10]:
        chain = " → ".join(s[1] for s in steps)
        print(f"  [{label}]")
        print(f"    chain: {chain}")
        print(f"    sink@: {hits}")

    print("\n=== ④ 未覆盖 sink 函数样例（D 的盲区，前 12）===")
    for bid in list(uncovered)[:12]:
        b = id_to_block.get(bid)
        if b:
            print(f"    {b.function_name} ({b.file_path}) → {id_to_callees.get(bid, [])}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
