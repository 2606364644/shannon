"""最后一查：Process→Function 关系上有没有 step 属性（步骤序列）。

已知：Process 节点有 entryPointId/terminalId/stepCount=6 —— 预计算路径节点。
但 LadybugDB cypher 没 type() 函数。绕开它，直接 RETURN r.step 看关系是否带步骤序号。
若 r.step 有值 → 一次查询拿到 entry→…→terminal 的有序函数序列 = 零 BFS 成立。
"""
from __future__ import annotations
import asyncio, json
from pathlib import Path
from shannon_core.code_index.gitnexus_mcp import GitNexusMCPClient


async def _q(c, label, query, params=None):
    print(f"\n  ▸ {label}\n    Q: {query[:150]}")
    try:
        r = await c.call_tool("cypher", {"query": query, "params": params or {}})
    except Exception as exc:
        print(f"    ✗ {type(exc).__name__}: {str(exc)[:100]}"); return None
    rows = r.get("rows", []) if isinstance(r, dict) else []
    err = r.get("error") if isinstance(r, dict) else None
    if err:
        print(f"    error: {err[:140]}"); return None
    print(f"    rows={len(rows)}")
    for row in rows[:8]:
        print(f"      {json.dumps(row, ensure_ascii=False)[:200]}")
    return rows


async def main():
    repo = Path("/root/code/backend/statement_template_svr")
    async with GitNexusMCPClient(repo) as c:
        # 拿一个 stepCount 较小的 process（步骤少，不崩）
        prows = await _q(c, "取 stepCount 小的 process",
            "MATCH (p:Process) RETURN p.id AS pid, p.label AS label, p.stepCount AS sc, p.entryPointId AS ep, p.terminalId AS tl ORDER BY p.stepCount ASC LIMIT 3")
        if not prows:
            print("没拿到 process"); return
        p = prows[0]
        pid = p["pid"]
        print(f"\n  选 process: {pid}  label={p.get('label')!r}  stepCount={p.get('sc')}  entry={p.get('ep')}  terminal={p.get('tl')}")

        # 关键查询：Process→Function 关系是否带 step 属性（不 type()）
        await _q(c, "★ Process→Function 的 step 序列（核心！）",
            "MATCH (p:Process {id:$pid})-[r]->(f:Function) "
            "RETURN r.step AS step, f.name AS fn, f.filePath AS file ORDER BY step",
            {"pid": pid})

        # 若 step 不在关系上：看关系有哪些属性键
        await _q(c, "关系属性键 keys(r)",
            "MATCH (p:Process {id:$pid})-[r]->(f) RETURN keys(r) AS rk, labels(f) AS flabels, f.name AS fn LIMIT 3",
            {"pid": pid})

        # 关系的端点 + 全属性（不 type，直接 RETURN r 看 raw）
        await _q(c, "Process 任意出边目标 labels（不 type）",
            "MATCH (p:Process {id:$pid})-[r]->(n) RETURN labels(n) AS nl, n.name AS name, n.id AS id LIMIT 8",
            {"pid": pid})

        # 备选：节点间步骤是否用 SEQ/NEXT 之类串联（Function 之间的 step 关系）
        await _q(c, "Function 之间带 step 的关系",
            "MATCH (p:Process {id:$pid}) MATCH (a)-[r]->(b) WHERE a<>b RETURN r.step AS step, a.name AS a, b.name AS b ORDER BY step LIMIT 6",
            {"pid": pid})

        print("\n=== 判定 ===")
        print("若 ★ 查询返回 step=1..N 的有序 (fn,file) 行 → process 吐完整路径 → 零 BFS 成立")
        print("否则 process 中间步骤拿不到 → 回退方案 B")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
