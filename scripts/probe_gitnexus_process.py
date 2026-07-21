"""探 GitNexus "process"（执行流）—— 能否吐出 entry→…→sink 的完整步骤序列。

线索：impact byDepth entry 带 processes:[{id, label:"A → B", step:N}]；query/context
工具都返 processes。若 process = GitNexus 预计算的带步骤路径，且能拿全步骤序列，
则"全部交给 GitNexus、Python 只消费、零 BFS" 成立。

三问：① Process 是图节点吗、属性？② 步骤用什么关系串？③ 能否一次拿全步骤序列？
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from supernova_core.code_index.gitnexus_mcp import GitNexusMCPClient, _parse_md_table


def _hr(t): print(f"\n{'='*4} {t} {'='*max(0,76-len(t))}")


async def _cypher(c, label, q, params=None):
    print(f"\n  ▸ {label}")
    print(f"    Q: {q[:140]}")
    try:
        r = await c.call_tool("cypher", {"query": q, "params": params or {}})
    except Exception as exc:
        print(f"    ✗ {type(exc).__name__}: {str(exc)[:100]}")
        return None
    if isinstance(r, dict) and "rows" in r:
        rows = r.get("rows", [])
        print(f"    row_count={r.get('row_count')} parsed={len(rows)}")
        for row in rows[:5]:
            print(f"      {json.dumps(row, ensure_ascii=False)[:200]}")
        return rows
    if isinstance(r, dict) and "error" in r:
        print(f"    error: {r['error'][:120]}")
        return None
    print(f"    → {type(r).__name__}: {str(r)[:120]}")
    return None


async def _dump(c, label, tool, args):
    print(f"\n  ▸ {label}  ({tool} {list(args.keys())})")
    try:
        r = await c.call_tool(tool, args)
    except Exception as exc:
        print(f"    ✗ {type(exc).__name__}: {str(exc)[:100]}")
        return
    if not isinstance(r, dict):
        print(f"    → {r!r}")
        return
    print(f"    top keys: {list(r.keys())}")
    # 重点 dump processes / steps / chain 相关字段
    for k, v in r.items():
        if any(h in k.lower() for h in ("process", "step", "chain", "path", "flow", "call")):
            preview = json.dumps(v, ensure_ascii=False)[:400]
            print(f"    {k}: {preview}")


async def main():
    repo = Path("/root/code/backend/statement_template_svr")
    async with GitNexusMCPClient(repo) as c:
        _hr("① Process 是图节点吗？属性？")
        await _cypher(c, "Process 节点 + 属性",
            "MATCH (p:Process) RETURN p LIMIT 3")
        await _cypher(c, "Process 节点属性键",
            "MATCH (p:Process) RETURN keys(p) AS k, p.id AS id, p.label AS label LIMIT 3")

        _hr("② Process 怎么串步骤（出边类型 + 目标）")
        await _cypher(c, "Process 出边类型分布",
            "MATCH (p:Process)-[r]->(n) RETURN type(r) AS rel, labels(n)[0] AS ntype, count(*) AS cnt")
        await _cypher(c, "Process 入边类型分布",
            "MATCH (n)-[r]->(p:Process) RETURN type(r) AS rel, labels(n)[0] AS ntype, count(*) AS cnt")

        _hr("③ 拿一个 process 的完整步骤序列")
        # 先拿一个 process id
        prows = await _cypher(c, "取一个 process id",
            "MATCH (p:Process) RETURN p.id AS pid LIMIT 1")
        pid = prows[0]["pid"] if prows else None
        if pid:
            print(f"    用 pid={pid!r}")
            # 试几种"步骤序列"形态
            await _cypher(c, "a) Process→Function 直接 + 关系 step",
                "MATCH (p:Process {id:$pid})-[r]->(f:Function) RETURN type(r) AS rel, f.name AS fn, r.step AS step ORDER BY step",
                {"pid": pid})
            await _cypher(c, "b) Process 所有出边 + 关系属性",
                "MATCH (p:Process {id:$pid})-[r]->(n) RETURN type(r) AS rel, labels(n)[0] AS ntype, n.name AS name, r.step AS step ORDER BY step",
                {"pid": pid})
            await _cypher(c, "c) Process 节点自身的 steps/sequence 属性",
                "MATCH (p:Process {id:$pid}) RETURN p",
                {"pid": pid})

        _hr("④ context 工具看 process 返回（用 WithBroker，已知有 process）")
        await _dump(c, "context(WithBroker)", "context",
            {"name": "WithBroker", "file_path": "internal/app/serviceimpl/check.go"})

        _hr("⑤ query 工具看 process 返回")
        await _dump(c, "query(WithBroker)", "query",
            {"query": "WithBroker", "limit": 3})

        _hr("判定")
        print("看 ②/③：若 Process 通过带 step 属性的关系串起 Function 序列 → 拿全步骤 = 预计算路径")
        print("→ 方案升级为「GitNexus 吐路径、Python 只消费、零 BFS」成立。")
        print("否则 process 不可用作路径源，回退方案 B。")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
