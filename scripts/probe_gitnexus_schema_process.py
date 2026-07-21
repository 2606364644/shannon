"""1.6.7 确实有 schema / processes / process trace resource（resources/list 不列但 read 能读）。

上轮已确认：schema/processes/clusters 直接 read 都返内容；process/{id} not found 是因为
URI 要用 process 的 label（"Init → GetOffset"）不是 id；query 返空是 FTS 缺失（--repair-fts）。

本探针：① 完整读 schema（看 Process 怎么连步骤、所有边类型）② 完整读 processes（拿 label）
③ 用 label 读 process trace（"Full process trace with steps"）→ 验证能否拿 entry→…→terminal。
"""
from __future__ import annotations
import asyncio
from pathlib import Path
from supernova_core.code_index.gitnexus_mcp import GitNexusMCPClient

NAME = "statement_template_svr"


async def _read(c, uri):
    try:
        r = await c._send_request("resources/read", {"uri": uri})
    except Exception as exc:
        return f"✗ {type(exc).__name__}: {str(exc)[:100]}"
    # MCP resources 的 content 是 {uri, mimeType, text}（无 type 字段，和 tools/call 的 {type:"text"} 不同）
    for item in r.get("contents", []):
        if isinstance(item, dict) and "text" in item:
            return item["text"]
    return ""


async def main():
    repo = Path("/root/code/backend/statement_template_svr")
    async with GitNexusMCPClient(repo) as c:
        print("=" * 80)
        print("① schema 完整内容（图 schema — Process 怎么连步骤、边类型）")
        print("=" * 80)
        schema = await _read(c, f"gitnexus://repo/{NAME}/schema")
        print(schema[:2500])
        print(f"\n[schema 总长 {len(schema)}]")

        print("\n" + "=" * 80)
        print("② processes 完整内容（拿 process 列表 + 每个 process 的字段/label）")
        print("=" * 80)
        procs = await _read(c, f"gitnexus://repo/{NAME}/processes")
        print(procs[:1500])
        print(f"\n[processes 总长 {len(procs)}]")

        print("\n" + "=" * 80)
        print("③ 用 label 读 process trace（URI encode 空格/箭头）")
        print("=" * 80)
        # 从 ② 的 processes 里挑 label。已知第一个是 "Init → GetOffset"。
        # 试多种标识：label 原样 / URL-encoded / proc id
        import urllib.parse
        candidates = [
            ("label 原样", "Init → GetOffset"),
            ("label URL-encoded", urllib.parse.quote("Init → GetOffset")),
            ("proc id", "proc_0_init"),
            ("name 小写去箭头", "init_getoffset"),
        ]
        for label, ident in candidates:
            uri = f"gitnexus://repo/{NAME}/process/{ident}"
            print(f"\n  ▸ [{label}] uri={uri}")
            t = await _read(c, uri)
            if isinstance(t, str) and t and "not found" not in t.lower():
                print(f"    ✓ 命中！长度 {len(t)}，前 1000:")
                print("    " + t[:1000].replace("\n", "\n    "))
            else:
                print(f"    {t[:120] if t else '(空)'}")

        print("\n" + "=" * 80)
        print("④ 根据 schema 用对的边类型 cypher 查 Process 步骤")
        print("=" * 80)
        # 先从 schema 找关系类型（CodeRelation 子类型），盲试常见 + 上面 schema 提到的
        for rel, q in [
            ("CodeRelation all", "MATCH (p:Process)-[r:CodeRelation]->(n) RETURN n.name AS name, r.type AS rtype, r.step AS step LIMIT 8"),
            ("Process-Function any", "MATCH (p:Process)-[r]->(n:Function) RETURN n.name AS name, labels(r) AS rl, r.step AS step LIMIT 8"),
            ("Process 属于 Community", "MATCH (p:Process)-[r]->(n) RETURN labels(n) AS nl, count(*) AS c"),
        ]:
            print(f"\n  ▸ {rel}\n    {q[:120]}")
            try:
                r = await c.call_tool("cypher", {"query": q})
                rows = r.get("rows", []) if isinstance(r, dict) else []
                err = r.get("error") if isinstance(r, dict) else None
                if err:
                    print(f"    ✗ {err[:100]}")
                else:
                    print(f"    rows={len(rows)}")
                    import json
                    for row in rows[:5]:
                        print(f"      {json.dumps(row, ensure_ascii=False)[:160]}")
            except Exception as exc:
                print(f"    ✗ {exc}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
