"""方案 C 专项探针 v2 —— 变长路径的"路径数组列"怎么从 markdown 拿。

v1 发现：list-comprehension 列 `RETURN [n IN nodes(p)|n.name]` 返空 markdown（GitNexus
渲染不出 list 列）；`RETURN nodes(p)` 返完整节点对象 readline 崩（>64KB）。但变长
路径确有大量匹配（sink WithError 有 153 callers）。

v2 聚焦最有希望的形态：UNWIND + 路径 id —— 每行 (path_id, node_name)，Python 侧
group by path_id 重建路径。并 dump 完整 raw 结构看 GitNexus 变长返回到底什么样。
所有 _raw_text 调用包 try/except，防 readline 崩挂掉整个探针。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
from pathlib import Path

from shannon_core.code_index.gitnexus_mcp import GitNexusMCPClient, _parse_md_table


def _hr(t: str) -> None:
    print(f"\n{'=' * 4} {t} {'=' * max(0, 76 - len(t))}")


async def _raw_text(client: GitNexusMCPClient, tool: str, args: dict) -> str:
    args = {**args, "repo": str(client.repo_root)}
    res = await client._send_request("tools/call", {"name": tool, "arguments": args})
    content = res.get("content", []) if isinstance(res, dict) else []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            return item.get("text", "")
    return ""


def _decode(text: str) -> dict | None:
    try:
        obj, _ = json.JSONDecoder().raw_decode(text.lstrip())
        return obj if isinstance(obj, dict) else {"_raw_is": type(obj).__name__, "_val": str(obj)[:200]}
    except json.JSONDecodeError:
        return None


async def _try(client, label, query, params):
    print(f"\n  ▸ {label}")
    print(f"    Q: {query[:150]}")
    try:
        text = await _raw_text(client, "cypher", {"query": query, "params": params})
    except Exception as exc:
        print(f"    ✗ readline 崩 / 异常: {type(exc).__name__}: {str(exc)[:100]}")
        print(f"    （返回 >64KB，说明 RETURN 含完整节点对象 — C 不可用此形态）")
        return
    obj = _decode(text)
    if obj is None:
        print(f"    raw(前300): {text[:300]!r}")
        print(f"    → 非 JSON（可能 Error 文本）")
        return
    # dump obj 的真实结构
    print(f"    obj keys: {list(obj.keys())}")
    if "markdown" in obj:
        md = obj["markdown"]
        rows = _parse_md_table(md)
        print(f"    row_count={obj.get('row_count')}  parsed_rows={len(rows)}")
        print(f"    markdown(前260): {md[:260]!r}")
        for i, row in enumerate(rows[:4]):
            print(f"    row[{i}]: {json.dumps(row, ensure_ascii=False)}")
    else:
        # 不是 markdown 结构 — dump 整个 obj（前 500）
        print(f"    obj(前500): {json.dumps(obj, ensure_ascii=False)[:500]}")


async def main_async(repo: Path) -> int:
    if not shutil.which("gitnexus"):
        print("ERROR: gitnexus CLI 不在 PATH")
        return 2
    print(f"repo = {repo}")
    async with GitNexusMCPClient(repo) as client:
        # 找有真实上游的 sink
        q = ("MATCH (caller)-[r:CodeRelation {type:'CALLS'}]->(callee:Function) "
             "RETURN callee.name AS name, callee.filePath AS file, count(*) AS callers "
             "ORDER BY callers DESC LIMIT 3")
        r = await client.call_tool("cypher", {"query": q})
        rows = r.get("rows", []) if isinstance(r, dict) else []
        sink = rows[0] if rows else None
        if not sink:
            print("没找到 sink"); return 1
        name, file = sink["name"], sink.get("file", "")
        _hr(f"sink={name!r} ({file})  callers={sink.get('callers')}")

        # V-scalar：标量列，验证变长路径匹配到几条 + 端点（baseline，必 work）
        await _try(client, "V-scalar  startNode/endNode/length（验证路径数量 baseline）",
            "MATCH p=(src)-[:CodeRelation*1..4]->(f:Function {name:$name}) "
            "RETURN startNode(p).name AS entry, length(p) AS hops LIMIT 5",
            {"name": name})

        # V-list：list 列（v1 已知空，再确认 + 看 obj 结构）
        await _try(client, "V-list  [n IN nodes(p)|n.name]（v1 空，确认结构）",
            "MATCH p=(src)-[:CodeRelation*1..4]->(f:Function {name:$name}) "
            "RETURN [n IN nodes(p) | n.name] AS chain LIMIT 3",
            {"name": name})

        # V-unwind-id：UNWIND + elementId(p)（C 最有希望 — 每行 pid+name，group 重建）
        await _try(client, "V-unwind-id  UNWIND + elementId(p)（C 核心 — group by pid 重建路径）",
            "MATCH p=(src)-[:CodeRelation*1..4]->(f:Function {name:$name}) "
            "UNWIND nodes(p) AS n RETURN elementId(p) AS pid, n.name AS name, n.filePath AS file LIMIT 20",
            {"name": name})

        # V-unwind-id-idx：带节点序号（精确顺序，不依赖行序）
        await _try(client, "V-unwind-id-idx  WITH p,nodes(p) UNWIND range + ns[i].name（带序号）",
            "MATCH p=(src)-[:CodeRelation*1..4]->(f:Function {name:$name}) "
            "WITH p, nodes(p) AS ns UNWIND range(0, size(ns)-1) AS i "
            "RETURN elementId(p) AS pid, i AS idx, ns[i].name AS name LIMIT 20",
            {"name": name})

        # V-nodes-id：fallback — elementId(p) + nodes(p) name 用 reduce（若 elementId 不可用）
        await _try(client, "V-id-only  RETURN elementId(p) （看路径 id 能否拿到）",
            "MATCH p=(src)-[:CodeRelation*1..4]->(f:Function {name:$name}) "
            "RETURN elementId(p) AS pid, length(p) AS hops LIMIT 5",
            {"name": name})

        _hr("判定")
        print("若 V-unwind-id 的 rows 是 [{pid, name, file}, ...] 非 empty → C 成立：")
        print("  Python 侧 group by pid → 每组按出现序 = 一条 path 的节点序列 → 直接喂 propagate_across_chains")
        print("若 elementId 不可用 / rows 仍空 → 变长路径拿不到可解析路径，C 不可行，回退方案 B。")
    return 0


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--repo", required=True, type=Path)
    raise SystemExit(asyncio.run(main_async(p.parse_args().repo)))


if __name__ == "__main__":
    main()
