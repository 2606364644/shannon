"""探 GitNexus MCP resources + query process_symbols —— 拿完整 execution-flow 路径。

之前探针只查 tools（cypher/impact），漏了 MCP resources。README 明示：
  gitnexus://repo/{name}/processes           — 所有 execution flows
  gitnexus://repo/{name}/process/{name}      — Full process trace with steps  ← 完整路径！
且 query 的 process_symbols 带 process_id + step_index（按 process 分组+step 排序=路径）。

本探针用 _send_request 直接发 resources/list + resources/read（GitNexusMCPClient
只封装了 tools/call，没封装 resources），验证 process 能否吐出 entry→…→terminal
的完整函数步骤序列。
"""
from __future__ import annotations
import asyncio, json
from pathlib import Path
from supernova_core.code_index.gitnexus_mcp import GitNexusMCPClient


def _hr(t): print(f"\n{'='*4} {t} {'='*max(0,76-len(t))}")


async def _res(c, method, params):
    """直接发 JSON-RPC（resources/list、resources/read）。"""
    try:
        return await c._send_request(method, params)
    except Exception as exc:
        print(f"  ✗ {method} 异常: {type(exc).__name__}: {str(exc)[:120]}")
        return None


async def main():
    repo = Path("/root/code/backend/statement_template_svr")
    repo_name = "statement_template_svr"
    async with GitNexusMCPClient(repo) as c:
        _hr("① resources/list — GitNexus 暴露哪些 resource URI")
        rl = await _res(c, "resources/list", {})
        uris = []
        if isinstance(rl, dict):
            for r in rl.get("resources", []):
                uri = r.get("uri", "")
                desc = r.get("description", "")[:80]
                uris.append(uri)
                print(f"    {uri}  — {desc}")
        if not uris:
            print("    (空或失败)")

        _hr("② resources/read processes — 所有 execution flow 列表")
        pr = await _res(c, "resources/read", {"uri": f"gitnexus://repo/{repo_name}/processes"})
        text = ""
        if isinstance(pr, dict):
            for item in pr.get("contents", []):
                if item.get("type") == "text":
                    text = item.get("text", "")
                    break
        if text:
            try:
                obj, _ = json.JSONDecoder().raw_decode(text.lstrip())
            except json.JSONDecodeError:
                obj = None
            if isinstance(obj, list):
                print(f"    {len(obj)} 个 process，前 3 个:")
                for p in obj[:3]:
                    print(f"      {json.dumps(p, ensure_ascii=False)[:200]}")
                procs = obj
            elif isinstance(obj, dict):
                print(f"    dict keys: {list(obj.keys())}")
                procs = (obj.get("processes") or obj.get("items") or [])
            else:
                procs = []
        else:
            print(f"    raw(前300): {text[:300]!r}")
            procs = []

        _hr("③ resources/read process/{name} — ★ 完整 process trace with steps（核心）")
        # 拿一个 process name（从 ② 或 fallback 已知）
        pname = None
        for p in procs:
            if isinstance(p, dict):
                pname = p.get("name") or p.get("id") or p.get("label")
                if pname: break
        if not pname:
            # fallback：用 cypher 拿一个 process id
            try:
                r = await c.call_tool("cypher", {"query": "MATCH (p:Process) RETURN p.id AS id, p.label AS label LIMIT 1"})
                row = (r.get("rows") or [{}])[0]
                pname = row.get("id") or "proc_0_init"
                print(f"    (从 cypher 取 pname={pname})")
            except Exception:
                pname = "proc_0_init"
        print(f"    读 process: {pname}")
        pt = await _res(c, "resources/read", {"uri": f"gitnexus://repo/{repo_name}/process/{pname}"})
        ptext = ""
        if isinstance(pt, dict):
            for item in pt.get("contents", []):
                if item.get("type") == "text":
                    ptext = item.get("text", "")
                    break
        if ptext:
            print(f"    raw text 长度 {len(ptext)}，前 1200 字符:")
            print("    " + ptext[:1200].replace("\n", "\n    "))
        else:
            print(f"    (空)")

        _hr("④ query 自然语言 — process_symbols 带 process_id + step_index")
        for q in ("template version", "upload template", "create template"):
            print(f"\n    query({q!r})")
            try:
                r = await c.call_tool("query", {"query": q, "limit": 3})
            except Exception as exc:
                print(f"      ✗ {exc}"); continue
            if not isinstance(r, dict):
                print(f"      → {r!r}"); continue
            ps = r.get("process_symbols", []) or []
            procs2 = r.get("processes", []) or []
            print(f"      processes={len(procs2)} process_symbols={len(ps)}")
            for s in ps[:5]:
                print(f"        {json.dumps(s, ensure_ascii=False)[:200]}")
            if ps:
                break

        _hr("判定")
        print("③ 若 process trace 含 entry→…→terminal 的有序步骤（函数序列）→")
        print("   GitNexus 直接吐调用链路径，supernova 只消费、零 BFS（调用链层面）。")
        print("④ 若 process_symbols 带 step_index → query 也能重组路径。")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
