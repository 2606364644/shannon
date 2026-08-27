# 真机锚点验证（spec 2026-08-27-poc-agent-direct-design §8.5）：
# 用 app-20260827-062331 的 xss queue 直跑 _write_agent_pocs（真 LLM），
# 验收 XSS-VULN-01 的 PoC 形态——不得再产出「POST 到 SPA 路由 + auditTaskId body」。
import asyncio
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path("/root/ft-codescan")
SCAN = REPO / "workspaces/__legacy__/scans/app-20260827-062331/deliverables/whitebox"
SRC_REPO = "/root/ft-codescan/workspaces/__legacy__/repos/app"

sys.path.insert(0, str(REPO / "packages/core/src"))
sys.path.insert(0, str(REPO / "packages/whitebox/src"))

from supernova_core.config.env_loader import load_env
load_env(REPO / ".env", REPO / ".env.profiles")

from supernova_whitebox.pipeline import activities


async def main() -> None:
    tmp = Path("/tmp/poc-anchor")
    if tmp.exists():
        shutil.rmtree(tmp)
    inter = tmp / "deliverables" / "whitebox" / "intermediate"
    inter.mkdir(parents=True)
    q = json.loads((SCAN / "intermediate/xss_exploitation_queue.json").read_text())
    for v in q["vulnerabilities"]:          # 清旧确定性 report_poc，强制重跑
        v.pop("report_poc", None)
    (inter / "xss_exploitation_queue.json").write_text(json.dumps(q, ensure_ascii=False))
    print(f"cards: {[v['ID'] for v in q['vulnerabilities']]}")

    inp = SimpleNamespace(
        web_url="", repo_path=SRC_REPO, vuln_classes=["xss"],
        provider_config=None, api_key=None,
    )
    written = await activities._write_agent_pocs(inp, tmp / "deliverables" / "whitebox")
    print(f"written: {written}")
    out = json.loads((inter / "xss_exploitation_queue.json").read_text())
    for v in out["vulnerabilities"]:
        poc = v.get("report_poc")
        print(f"\n===== {v['ID']} =====")
        if not poc:
            print("  <no report_poc (honest absence)>")
            continue
        print(f"  self_check: {poc.get('self_check')}")
        print(f"  preconditions: {poc.get('preconditions')}")
        print(f"  steps: {json.dumps(poc.get('steps'), ensure_ascii=False)}")
        print(f"  curl: {poc.get('curl')}")
        print(f"  raw_http: {(poc.get('raw_http') or '')[:200]}")
        print(f"  expected_response: {poc.get('expected_response')}")
        print(f"  notes: {poc.get('notes')}")

    # 验收断言：XSS-VULN-01 不得再产出 SPA-POST 形态
    bad = None
    for v in out["vulnerabilities"]:
        poc = v.get("report_poc") or {}
        curl = poc.get("curl") or ""
        if "modification-application/application-review" in curl and "-X POST" in curl:
            bad = v["ID"]
    if bad:
        print(f"\n✗ FAIL: {bad} 仍产出 POST 到 SPA 路由的形态")
        sys.exit(1)
    print("\n✓ PASS: 无 POST-to-SPA 形态")


asyncio.run(main())
