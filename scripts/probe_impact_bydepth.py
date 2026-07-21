"""核实 impact byDepth 的 entry 到底带不带边信息。

核心问题：B 方案里的 Python BFS 是否可省？
- 若 byDepth entry 只给"符号引用"（name/file），无边 → 必须 cypher 补边 + BFS（方案 B 原形态）。
- 若 byDepth entry 带"上一跳 caller"边信息 → impact 给的是带边可达 DAG，Python 只 DFS 提取路径，
  无需 cypher、无需全量 BFS（B 升级为"几乎全交 GitNexus"）。

用一个有真实上游的 sink（被调用最多的 Function）跑 impact upstream，dump 完整 byDepth。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from supernova_core.code_index.gitnexus_mcp import GitNexusMCPClient


async def main() -> int:
    repo = Path("/root/code/backend/statement_template_svr")
    async with GitNexusMCPClient(repo) as c:
        # 找一个有真实上游路径的 sink（被调用最多）
        r = await c.call_tool("cypher", {"query":
            "MATCH (caller)-[:CodeRelation {type:'CALLS'}]->(callee:Function) "
            "RETURN callee.name AS name, callee.filePath AS file, count(*) AS n "
            "ORDER BY n DESC LIMIT 3"})
        rows = r.get("rows", []) if isinstance(r, dict) else []
        if not rows:
            print("没找到 sink"); return 1
        # 取 callers 适中的（避免高频 sink 的 byDepth >64KB 触发 readline 崩）
        sink = rows[min(2, len(rows) - 1)]
        name, file = sink["name"], sink.get("file", "")
        print(f"=== sink: {name}  ({file})  callers={sink.get('n')} ===\n")

        # impact upstream；file_path 消歧；limit/maxDepth 收紧防爆
        try:
            res = await c.call_tool("impact", {
                "target": name, "direction": "upstream",
                "maxDepth": 2, "file_path": file, "relationTypes": ["CALLS"], "limit": 3,
            })
        except Exception as exc:
            print(f"impact 仍崩（{exc}）— 该 sink 上游仍太多，换更小的")
            sink = rows[-1]
            name, file = sink["name"], sink.get("file", "")
            print(f"=== 换 sink: {name}  ({file})  callers={sink.get('n')} ===\n")
            res = await c.call_tool("impact", {
                "target": name, "direction": "upstream",
                "maxDepth": 2, "file_path": file, "relationTypes": ["CALLS"], "limit": 3,
            })
        if not isinstance(res, dict):
            print(f"impact 返非 dict: {res!r}"); return 1

        print(f"top-level keys: {list(res.keys())}")
        print(f"impactedCount={res.get('impactedCount')}  risk={res.get('risk')}")
        print(f"byDepthCounts={res.get('byDepthCounts')}")

        bd = res.get("byDepth", {}) or {}
        print(f"\n=== byDepth（关键：看 entry 带不带边）depths={list(bd.keys())} ===")
        for depth, entries in bd.items():
            entries = entries or []
            print(f"\n  depth {depth}: {len(entries)} 个 entry")
            # dump 前几个 entry 的完整结构 + 字段名
            for i, e in enumerate(entries[:3]):
                if isinstance(e, dict):
                    print(f"    entry[{i}] keys: {list(e.keys())}")
                    print(f"    entry[{i}]: {json.dumps(e, ensure_ascii=False)[:300]}")
                else:
                    print(f"    entry[{i}] (非 dict): {e!r}")

        # 看一条完整 entry 的所有字段（第一个 depth 的第一个 entry）
        first_depth = next(iter(bd), None)
        if first_depth and bd[first_depth]:
            e0 = bd[first_depth][0]
            print(f"\n=== 第一条 entry 全字段展开（判定带边与否）===")
            print(json.dumps(e0, ensure_ascii=False, indent=2) if isinstance(e0, dict) else repr(e0))

        # affected_processes 结构（看是不是函数级路径）
        ap = res.get("affected_processes", []) or []
        if ap:
            print(f"\n=== affected_processes[0] 结构 ===")
            print(json.dumps(ap[0], ensure_ascii=False, indent=2)[:500])

        print("\n=== 判定 ===")
        if first_depth and bd[first_depth]:
            e0 = bd[first_depth][0]
            if isinstance(e0, dict):
                # 启发式：entry 里有没有 edge/caller/via/from/through/confidence/relation 这类边字段
                edge_hints = [k for k in e0.keys()
                              if any(h in k.lower() for h in ("edge", "caller", "via", "from", "through",
                                                               "relation", "confidence", "source", "parent"))]
                if edge_hints:
                    print(f"✓ byDepth entry 似乎带边字段: {edge_hints}")
                    print("  → impact 给带边 DAG，Python 只 DFS 提取路径，可省 cypher + BFS（B 升级）")
                else:
                    print(f"✗ byDepth entry 仅有符号引用字段，无边（{list(e0.keys())}）")
                    print("  → impact 给分层闭包，要精确 path 仍需 cypher 补边 + BFS（B 原形态）")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
