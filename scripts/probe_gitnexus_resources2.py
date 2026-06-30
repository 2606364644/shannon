"""深挖：1.6.7 的 process / schema resource 到底有没有（不假设 resources/list 全列）。

上轮 resources/list 只返回 repos + setup，可能是：①解析错；②per-repo resource
list 不列但直接 read 能读；③schema resource 能指导正确的 cypher 查 process 步骤。
本探针 dump 完整 raw + 直接 read 各种 URI + query 带 task_context。
"""
from __future__ import annotations
import asyncio, json
from pathlib import Path
from shannon_core.code_index.gitnexus_mcp import GitNexusMCPClient

NAME = "statement_template_svr"


def _hr(t): print(f"\n{'='*4} {t} {'='*max(0,76-len(t))}")


async def _raw(c, method, params):
    try:
        return await c._send_request(method, params)
    except Exception as exc:
        return {"_exc": f"{type(exc).__name__}: {str(exc)[:120]}"}


async def _read(c, uri):
    """resources/read 一个 URI，返回 raw text（前 600）+ 解析提示。"""
    r = await _raw(c, "resources/read", {"uri": uri})
    if "_exc" in r:
        print(f"    ✗ {r['_exc']}")
        return None
    text = ""
    contents = r.get("contents", []) if isinstance(r, dict) else []
    for item in contents:
        if isinstance(item, dict) and item.get("type") == "text":
            text = item.get("text", "")
            break
    if not text:
        # 可能是别的结构，dump r 的 keys
        print(f"    (空 text) r keys={list(r.keys()) if isinstance(r,dict) else type(r).__name__} r(前200)={str(r)[:200]}")
        return None
    print(f"    ✓ text 长度 {len(text)}，前 600:")
    print("    " + text[:600].replace("\n", "\n    "))
    return text


async def main():
    repo = Path("/root/code/backend/statement_template_svr")
    async with GitNexusMCPClient(repo) as c:
        _hr("① resources/list 完整 raw dump（确认是不是只有 repos+setup）")
        rl = await _raw(c, "resources/list", {})
        print(f"    raw(前 800): {json.dumps(rl, ensure_ascii=False)[:800]}")

        _hr("② resources/read gitnexus://repos（repo 列表，看有无 per-repo hint）")
        await _read(c, "gitnexus://repos")

        _hr("③ 直接 read per-repo URI（不等 list 列出，逐个试）")
        for uri in [
            f"gitnexus://repo/{NAME}/context",
            f"gitnexus://repo/{NAME}/schema",
            f"gitnexus://repo/{NAME}/processes",
            f"gitnexus://repo/{NAME}/clusters",
            f"gitnexus://repo/{NAME}/process/proc_0_init",
        ]:
            print(f"\n    ▸ {uri}")
            await _read(c, uri)

        _hr("④ resources/read setup（上轮 list 列了它，看它有无提示别的 resource）")
        await _read(c, "gitnexus://setup")

        _hr("⑤ query 带 task_context/goal + 多种词（含已知函数名）")
        for q, tc in [
            ("template", "analyzing template version flows"),
            ("process flow", "tracing execution flows"),
            ("WithBroker", "understanding call chain"),
            ("CreateTable", "understanding call chain"),
        ]:
            print(f"\n    ▸ query({q!r}, task_context={tc!r})")
            try:
                r = await c.call_tool("query", {"query": q, "task_context": tc, "goal": "find execution flows", "limit": 5})
            except Exception as exc:
                print(f"      ✗ {exc}"); continue
            if not isinstance(r, dict):
                print(f"      → {r!r}"); continue
            ps = r.get("process_symbols", []) or []
            procs = r.get("processes", []) or []
            defs = r.get("definitions", []) or []
            print(f"      processes={len(procs)} process_symbols={len(ps)} definitions={len(defs)} warning={r.get('warning','')[:80]}")
            for s in ps[:3]:
                print(f"        ps: {json.dumps(s, ensure_ascii=False)[:200]}")
            for p in procs[:2]:
                print(f"        proc: {json.dumps(p, ensure_ascii=False)[:200]}")

        _hr("⑥ schema 拿到后用对的关系类型查 Process 步骤（若 ③ schema 成功）")
        # 直接试几个可能的关系类型（不等 schema，盲试 r.keys 在 cypher 里）
        for rel in ["HAS_STEP", "STEP", "NEXT", "CONTAINS", "MEMBER_OF"]:
            try:
                r = await c.call_tool("cypher", {"query":
                    f"MATCH (p:Process {{id:'proc_0_init'}})-[:{rel}]->(n) RETURN n.name AS name, labels(n) AS lbl LIMIT 5"})
                rows = r.get("rows", []) if isinstance(r, dict) else []
                err = r.get("error") if isinstance(r, dict) else None
                if rows:
                    print(f"    ✓ 关系 {rel}: {len(rows)} 行 — {rows[:2]}")
                elif err:
                    print(f"    ✗ {rel}: {err[:80]}")
                else:
                    print(f"    · {rel}: 0 行（关系不存在或无匹配）")
            except Exception as exc:
                print(f"    ✗ {rel}: {exc}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
