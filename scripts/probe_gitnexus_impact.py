"""GitNexus `impact` 工具双向探针 —— 为"定向追踪"设计探路（v2）。

一次性诊断脚本（**非生产流程**）。v1 发现：query 单关键词返空（改用 cypher
精确查函数名）；全量 cypher LIMIT 5000 撞 asyncio readline 64KB 上限崩（强化
"全量用法坏、定向用法好"）；impact 真实 schema 远比死代码 trace_from_sink 假设
的丰富（target_uid 消歧 / crossDepth 跨仓 / byDepth 分层可达 / 内置 timeoutMs）。

本轮目标：拿到 impact 在真机上的**真实返回结构**（byDepth? paths?
affected_processes?），定方向 1 的数据契约。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import time
from pathlib import Path

from supernova_core.code_index.gitnexus_mcp import GitNexusMCPClient

SINK_KEYWORDS = ["exec", "query", "eval", "execute", "command", "fetch", "render", "template", "unmarshal", "sql", "db"]
ENTRY_KEYWORDS = ["handler", "route", "main", "entry", "serve", "api", "rpc"]


def _hr(title: str) -> None:
    print(f"\n{'=' * 4} {title} {'=' * max(0, 76 - len(title))}")


async def _list_tools(client: GitNexusMCPClient) -> None:
    _hr("Q1  tools/list — impact schema 摘要")
    res = await client._send_request("tools/list", {})
    tools = res.get("tools", []) if isinstance(res, dict) else []
    for t in tools:
        name = t.get("name")
        schema = t.get("inputSchema", {})
        required = schema.get("required", [])
        print(f"  • {name}  required={required}")
    impact = next((t for t in tools if t.get("name") == "impact"), None)
    if impact:
        print("  impact 参数全集:")
        for pname, pdef in (impact.get("inputSchema", {}).get("properties", {})).items():
            desc = pdef.get("description", "")[:110]
            print(f"      {pname} ({pdef.get('type', '?')}): {desc}")


async def _discover(client: GitNexusMCPClient, keywords: list[str], *, kind: str, limit: int = 10) -> list[dict]:
    """用 cypher 精确查函数名（query 语义搜索单关键词返空，不可靠），按关键词过滤。"""
    cypher = "MATCH (f:Function) RETURN f.name AS name, f.filePath AS file LIMIT 400"
    try:
        r = await client.call_tool("cypher", {"query": cypher})
    except Exception as exc:
        print(f"  (cypher discover failed: {exc})")
        return []
    rows = r.get("rows", []) if isinstance(r, dict) else []
    matched: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = row.get("name", "")
        if any(kw.lower() in name.lower() for kw in keywords):
            matched.append({"name": name, "filePath": row.get("file", "")})
        if len(matched) >= limit:
            break
    print(f"  discovered {len(matched)} {kind} (cypler 扫 {len(rows)} 个 Function, keywords={keywords})")
    if not matched and rows:
        matched = [{"name": row.get("name", ""), "filePath": row.get("file", "")}
                   for row in rows[:limit] if isinstance(row, dict)]
        print(f"  (无关键词命中 — 取前 {len(matched)} 个 Function 当 fallback target，保证 impact 能跑)")
    return matched


async def _probe_impact(client: GitNexusMCPClient, target, direction: str, max_depth: int) -> dict:
    """单次 impact。不假设返回结构，保留 raw + top-level 形态。"""
    args = {"target": target, "direction": direction, "maxDepth": max_depth}
    t0 = time.perf_counter()
    try:
        r = await client.call_tool("impact", args)
        elapsed = time.perf_counter() - t0
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "elapsed": time.perf_counter() - t0, "args": args}

    if r is None:
        return {"ok": True, "elapsed": elapsed, "args": args, "raw_is_none": True,
                "note": "call_tool 返 None = ambiguous 或 _parse_text 判非 JSON"}
    summary: dict = {"ok": True, "elapsed": elapsed, "top_type": type(r).__name__}
    if isinstance(r, dict):
        summary["top_keys"] = list(r.keys())
        for k, v in r.items():
            if isinstance(v, (list, dict, str)):
                summary[f"len:{k}"] = len(v)
            else:
                summary[f"val:{k}"] = v
    summary["raw"] = r  # 完整保留，外层按需 dump
    return summary


async def _probe_direction(client, targets, direction, label, max_depth, dump_raw_n=2) -> None:
    _hr(f"impact {direction}（{label}）maxDepth={max_depth}")
    if not targets:
        print("  (no targets — skip)")
        return
    print(f"  对 {len(targets)} 个 target 跑：① target=str(name)  ② target=str(name)+file_path hint（消歧）")
    print(f"  前 {dump_raw_n} 个 target 会 dump 完整 raw 结构（看 byDepth / affected_processes）\n")
    for idx, t in enumerate(targets):
        name = t["name"]
        r1 = await _probe_impact(client, name, direction, max_depth)
        # 第二次：加 file_path 消歧
        args2_target = name
        # 用直接 call 测 file_path 消歧
        t0 = time.perf_counter()
        try:
            raw2 = await client.call_tool("impact",
                {"target": name, "direction": direction, "maxDepth": max_depth,
                 "file_path": t.get("filePath", "")})
            e2 = time.perf_counter() - t0
        except Exception as exc:
            raw2, e2 = None, time.perf_counter() - t0
            print(f"  • {name}  ②file_path raise: {exc}")
        print(f"  • {name}  ({t.get('filePath','')})")
        print(f"      ① str        : ok={r1.get('ok')} {r1.get('elapsed',0):.2f}s "
              f"keys={r1.get('top_keys')} raw_none={r1.get('raw_is_none', False)}")
        if r1.get("error"):
            print(f"      ① error: {r1['error']}")
        print(f"      ② +file_path : {type(raw2).__name__} {e2:.2f}s "
              f"keys={(list(raw2.keys()) if isinstance(raw2, dict) else raw2)}")
        # dump 完整 raw（前 N 个）
        if idx < dump_raw_n and r1.get("raw") is not None:
            print(f"      --- ① raw dump ---")
            dump = json.dumps(r1["raw"], ensure_ascii=False, indent=2)[:1400]
            print("      " + dump.replace("\n", "\n      "))
            print(f"      --- end raw ---")
        # 逐 key 长度摘要
        for k, v in r1.items():
            if k.startswith("len:") and v:
                print(f"      {k} = {v}")


async def _probe_cypher_path(client: GitNexusMCPClient, sink_name: str) -> None:
    _hr(f"Q5  cypher 变长路径（备选）：sink={sink_name!r}")
    queries = [
        ("single-hop baseline LIMIT 3",
         "MATCH (caller)-[r:CodeRelation {type:'CALLS'}]->(callee) "
         "RETURN caller.name AS a, callee.name AS b LIMIT 3"),
        ("var-length *1..5 (不带 type 过滤)",
         f"MATCH p=(caller)-[:CodeRelation*1..5]->(f:Function {{name:'{sink_name}'}}) "
         "RETURN length(p) AS hops, [n in nodes(p) | n.name] AS chain LIMIT 3"),
        ("var-length *1..5 + RETURN nodes name",
         f"MATCH p=(caller)-[:CodeRelation*1..5]->(f:Function {{name:'{sink_name}'}}) "
         "UNWIND nodes(p) AS n RETURN n.name AS name, n.filePath AS file LIMIT 8"),
    ]
    for label, q in queries:
        t0 = time.perf_counter()
        try:
            r = await client.call_tool("cypher", {"query": q})
            dt = time.perf_counter() - t0
            if isinstance(r, dict) and r.get("rows") is not None:
                rows = r.get("rows", [])
                print(f"  • {label}: OK {dt:.2f}s rows={len(rows)}")
                for row in rows[:2]:
                    print(f"      {json.dumps(row, ensure_ascii=False)[:200]}")
            elif r is None:
                print(f"  • {label}: {dt:.2f}s → None")
            else:
                print(f"  • {label}: {dt:.2f}s → {type(r).__name__}")
        except Exception as exc:
            print(f"  • {label}: ERROR {time.perf_counter()-t0:.2f}s — {type(exc).__name__}: {str(exc)[:120]}")


async def _probe_perf(client: GitNexusMCPClient) -> None:
    _hr("Q6  全量 cypher LIMIT（当前用法的成本 + readline 风险）")
    for lim in (500, 5000):
        q = (f"MATCH (caller)-[r:CodeRelation {{type:'CALLS'}}]->(callee) "
             f"RETURN caller.name AS a, callee.name AS b LIMIT {lim}")
        t0 = time.perf_counter()
        try:
            r = await client.call_tool("cypher", {"query": q})
            dt = time.perf_counter() - t0
            rows = r.get("rows", []) if isinstance(r, dict) else []
            print(f"  LIMIT {lim}: {dt:.2f}s rows={len(rows)} ✓")
        except Exception as exc:
            print(f"  LIMIT {lim}: {time.perf_counter()-t0:.2f}s ✗ {type(exc).__name__}: {str(exc)[:100]}")
    print("  注：LIMIT 5000 若 ✗ 'Separator ... chunk longer than limit' = asyncio readline 64KB 上限")


async def main_async(repo: Path, sinks: list[str], entries: list[str], max_depth: int) -> int:
    if not shutil.which("gitnexus"):
        print("ERROR: gitnexus CLI 不在 PATH。")
        return 2
    print(f"repo = {repo}")
    async with GitNexusMCPClient(repo) as client:
        await _list_tools(client)
        sink_targets = [{"name": s} for s in sinks] or await _discover(client, SINK_KEYWORDS, kind="sink")
        entry_targets = [{"name": e} for e in entries] or await _discover(client, ENTRY_KEYWORDS, kind="entry")
        await _probe_direction(client, sink_targets, "upstream", "sink→source", max_depth)
        await _probe_direction(client, entry_targets, "downstream", "source→sink", max_depth)
        if sink_targets:
            await _probe_cypher_path(client, sink_targets[0]["name"])
        await _probe_perf(client)
    _hr("完成")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="GitNexus impact 双向探针 v2")
    p.add_argument("--repo", required=True, type=Path)
    p.add_argument("--sink", action="append", default=[])
    p.add_argument("--entry", action="append", default=[])
    p.add_argument("--max-depth", type=int, default=5)
    args = p.parse_args()
    raise SystemExit(asyncio.run(main_async(args.repo, args.sink, args.entry, args.max_depth)))


if __name__ == "__main__":
    main()
