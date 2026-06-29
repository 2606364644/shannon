#!/usr/bin/env python3
"""Minimal probe: GLM (glm-anthropic) 下 structured_output 兜底是否生效。

验证修复 f79dbd24 的核心假设：GLM 在 output_format 请求下，final assistant text
(collected_text) 里是否含完整 JSON、AnthropicProvider 兜底能否提取成
result.structured_output。

PASS: result.structured_output 非 None（SDK 原生返回 或 兜底从混合文本提取成功）。
FAIL: result.structured_output 为 None —— 看 result.text 诊断：
  - text 里有 JSON 但 structured_output None → 兜底没生效（bug 未修好）。
  - text 里没 JSON（只说"已写入"之类）→ 核心假设错，需别的方案（如从 md 提取）。
"""
import asyncio
import os
import tempfile
import time
from pathlib import Path


def load_env() -> None:
    # 本机 env 已有 ANTHROPIC_AUTH_TOKEN / ANTHROPIC_BASE_URL（GLM anthropic 端点）
    os.environ["SHANNON_AI_PROVIDER"] = "anthropic_api"
    os.environ.setdefault("CLAUDE_MAX_TURNS", "15")


# 复用 _vuln_output_schema 的宽松基线 schema
VULN_SCHEMA = {
    "type": "object",
    "properties": {
        "vulnerabilities": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "ID",
                    "vulnerability_type",
                    "externally_exploitable",
                    "confidence",
                ],
                "additionalProperties": True,
            },
        },
    },
    "required": ["vulnerabilities"],
}


async def main() -> None:
    load_env()
    from shannon_core.agents.tool_audit_logger import NullToolAuditLogger
    from shannon_core.agents.runner import run_claude_prompt

    target = Path(tempfile.mkdtemp(prefix="struct_probe_"))
    prompt = (
        "你是注入漏洞分析专家。分析下面这段代码的 SQL 注入漏洞：\n\n"
        "```python\n"
        "def get_user(name):\n"
        "    cur.execute(\"SELECT * FROM users WHERE name='\" + name + \"'\")\n"
        "```\n\n"
        "先用中文简要说明漏洞（源、sink、净化缺失），然后输出 exploitation queue：\n"
        "一个 JSON 对象 {\"vulnerabilities\": [{\"ID\": \"INJ-1\", "
        "\"vulnerability_type\": \"sqli\", \"externally_exploitable\": true, "
        "\"confidence\": \"high\"}]}。\n"
        "必须先有中文说明，再输出 JSON。"
    )
    print(f"[probe] target={target}  provider=anthropic_api (glm-anthropic)")
    t0 = time.time()
    try:
        result = await asyncio.wait_for(
            run_claude_prompt(
                prompt=prompt,
                repo_path=str(target),
                model_tier="medium",
                output_format=VULN_SCHEMA,
                tool_audit_logger=NullToolAuditLogger(),
            ),
            timeout=180,
        )
    except asyncio.TimeoutError:
        print("RESULT: TIMEOUT (>180s)")
        return

    dt = time.time() - t0
    print("=" * 64)
    print(
        f"duration={dt:.1f}s  turns={getattr(result, 'turns', None)}  "
        f"cost={getattr(result, 'cost', None)}  success={result.success}"
    )
    if result.error:
        print(f"ERROR: {result.error}")

    so = result.structured_output
    text = result.text or ""
    has_brace = "{" in text and "}" in text
    print(f"\n>>> structured_output: {so!r}")
    print(f">>> structured_output is None?  {so is None}")
    print(f">>> final text 含 JSON 大括号?   {has_brace}")
    print(
        f">>> structured_output NON-None (SDK 或兜底成功): "
        f"{'YES ✅ 落盘问题已解决' if so is not None else 'NO ❌ 见下方 text 诊断'}"
    )
    print("\n--- FINAL TEXT (前 2000 字符) ---")
    print(text[:2000])


if __name__ == "__main__":
    asyncio.run(main())
