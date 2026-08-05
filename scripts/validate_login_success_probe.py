#!/usr/bin/env python3
"""Probe: GLM 在 validate-authentication 结构化输出下是否会误判 login_success。

验证根因 B（memory: blackbox-auth-validation-two-root-causes）的核心假设：
GLM 在协议级结构化输出（anthropic 引擎 --json_schema / openai 引擎 response_format）
下，对"客观登录成功"是否会误填 login_success=false（假阴性），以及对"客观登录
失败"是否误填 true（假阳性）。

同时区分两种误判来源：
  (a) 模型判断错 —— 自然语言文本也说失败（模型真觉得没登录成功）
  (b) 字段提取错 —— 自然语言说成功，但 login_success 字段被填成 false（协议层问题，
      这正是根因 B 描述的 GLM --json_schema 行为）

设计说明（诚实局限）：
  真实 validate-authentication 是多轮浏览器 agent。本探针把"判定"独立出来——给模型
  一段客观登录后观察，让它判定 + 按 schema 输出。因此本探针测的是"结构化输出层在给定
  相同客观信息时是否可靠"，不等于 agent 端到端判定。但根因 B 的机制恰恰是"字段被误填"，
  所以这个隔离测试正中要害。

用法：
  python scripts/validate_login_success_probe.py [glm-anthropic] [glm-openai] ...
  LOGINPROBE_N=8 python scripts/validate_login_success_probe.py   # 每格样本数（默认 12）
"""
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILES = {
    "glm-anthropic": ROOT / ".env.profiles" / "glm-anthropic.env",
    "glm-openai": ROOT / ".env.profiles" / "glm-openai.env",
}

# 与 validate_authentication.py:21-32 完全一致的 schema
AUTH_SCHEMA = {
    "type": "object",
    "properties": {
        "login_success": {"type": "boolean"},
        "failure_point": {
            "type": "string",
            "enum": ["username_or_password", "totp_secret", "out_of_band"],
        },
        "failure_detail": {"type": "string", "maxLength": 250},
    },
    "required": ["login_success"],
}

SCENARIOS = {
    # ground_truth=True：客观成功，且有强文本标志（welcome/logout）
    "SUCCESS": {
        "ground_truth": True,
        "observation": (
            "You just attempted to log in to http://target.local as user 'admin'. "
            "Here are the objective browser observations after the login attempt:\n\n"
            "- Current URL: http://target.local/benefits  (navigated away from /login)\n"
            "- Saved auth-state.json: cookies=[connect.sid, csrf_token], "
            "origins=[{localStorage:{auth_token}}]\n"
            "- Page snapshot (DOM):\n"
            "    * Top bar reads: \"Welcome back, admin\"\n"
            "    * Top-right shows a \"Logout\" button\n"
            "    * Navigation bar: \"Dashboard | Benefits | Profile\"\n"
            "    * The login form is gone; no error messages visible\n"
            "- Network: POST /login -> 302 redirect -> GET /benefits -> 200 OK\n\n"
            "Based solely on these observations, did authentication succeed?"
        ),
    },
    # ground_truth=True：客观成功（URL 跳转 + session cookie），但缺少 welcome/logout
    # 这类强文本标志——考验模型能否从 URL+cookie 推断成功，而非依赖文本。
    "PARTIAL": {
        "ground_truth": True,
        "observation": (
            "You just attempted to log in to http://target.local as user 'admin'. "
            "Here are the objective browser observations after the login attempt:\n\n"
            "- Current URL: http://target.local/dashboard  (navigated away from /login)\n"
            "- Saved auth-state.json: cookies=[session_id], origins=[]\n"
            "- Page snapshot (DOM):\n"
            "    * Page title: \"Dashboard\"\n"
            "    * Body shows a data table labeled \"Your recent transactions\" with 12 rows\n"
            "    * No login form present\n"
            "    * No visible username, no \"Welcome\" text, no \"Logout\" button (minimal UI)\n"
            "    * No error messages\n"
            "- Network: POST /login -> 302 redirect -> GET /dashboard -> 200 OK\n\n"
            "Based solely on these observations, did authentication succeed?"
        ),
    },
    # ground_truth=False：客观失败
    "FAILURE": {
        "ground_truth": False,
        "observation": (
            "You just attempted to log in to http://target.local as user 'admin'. "
            "Here are the objective browser observations after the login attempt:\n\n"
            "- Current URL: http://target.local/login  (still on the login page)\n"
            "- Saved auth-state.json: cookies=[], origins=[]  (empty)\n"
            "- Page snapshot (DOM):\n"
            "    * The login form is still present (username/password inputs visible)\n"
            "    * A red error banner reads: \"Invalid username or password\"\n"
            "    * No \"Logout\" button, no username, no dashboard navigation\n"
            "- Network: POST /login -> 200 OK (re-rendered login page with error)\n\n"
            "Based solely on these observations, did authentication succeed?"
        ),
    },
}

PROMPT_TEMPLATE = (
    "{observation}\n\n"
    "Decide whether the login succeeded, then produce your verdict as a JSON object "
    "matching the schema (required field `login_success`: boolean). "
    "You may briefly state your reasoning first."
)

SUCCESS_KEYS = ("success", "succeed", "logged in", "logged-in", "authenticated",
                "成功", "登录成功", "已登录", "登陆成功")
FAILURE_KEYS = ("fail", "invalid", "did not", "didn't", "denied", "rejected",
                "unauthorized", "失败", "无效", "未登录", "不成功", "登陆失败", "登录失败")


def classify_text(text: str) -> str:
    """从自然语言文本推断模型主观判断：success / failure / unclear。"""
    t = text.lower()
    s = sum(1 for k in SUCCESS_KEYS if k in t)
    f = sum(1 for k in FAILURE_KEYS if k in t)
    if s > f:
        return "success"
    if f > s:
        return "failure"
    return "unclear"


def load_profile(name: str) -> None:
    path = PROFILES[name]
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()  # 覆盖：确保切引擎时凭证对得上


async def run_one(engine: str, scenario: str, idx: int) -> dict:
    from supernova_core.agents.runner import run_claude_prompt
    from supernova_core.agents.tool_audit_logger import NullToolAuditLogger

    sc = SCENARIOS[scenario]
    prompt = PROMPT_TEMPLATE.format(observation=sc["observation"])
    target = Path(tempfile.mkdtemp(prefix=f"loginprobe_{engine}_{scenario}_{idx}_"))
    t0 = time.time()
    try:
        result = await asyncio.wait_for(
            run_claude_prompt(
                prompt=prompt,
                repo_path=str(target),
                model_tier="medium",
                output_format=AUTH_SCHEMA,
                max_turns=5,
                tool_audit_logger=NullToolAuditLogger(),
            ),
            timeout=150,
        )
    except asyncio.TimeoutError:
        return {"engine": engine, "scenario": scenario, "idx": idx, "timeout": True}
    dt = time.time() - t0

    so = result.structured_output
    text = result.text or ""
    field = None
    if isinstance(so, dict) and "login_success" in so:
        field = bool(so["login_success"])
    return {
        "engine": engine, "scenario": scenario, "idx": idx,
        "duration": round(dt, 1), "run_success": result.success,
        "field": field,                      # True/False/None
        "so_none": so is None,               # 结构化提取失败
        "text": classify_text(text),         # 自然语言主观判断
        "snippet": text[:160].replace("\n", " "),
        "error": result.error,
    }


def summarize(rows: list[dict]) -> None:
    valid = [r for r in rows if not r.get("timeout")]
    print("\n" + "=" * 80)
    print(f"汇总  (有效样本 {len(valid)}/{len(rows)})")
    print("=" * 80)
    for engine in PROFILES:
        for scenario, sc in SCENARIOS.items():
            gt = sc["ground_truth"]
            sub = [r for r in valid if r["engine"] == engine and r["scenario"] == scenario]
            n = len(sub)
            if n == 0:
                continue
            t = sum(1 for r in sub if r["field"] is True)
            fls = sum(1 for r in sub if r["field"] is False)
            none = sum(1 for r in sub if r["field"] is None)
            misfield = sum(1 for r in sub if r["field"] is not None and r["field"] != gt)
            # text vs field 矛盾 → 字段提取错嫌疑 (b)
            conflict = sum(
                1 for r in sub
                if r["field"] is not None and r["text"] != "unclear"
                and (r["text"] == "success") != r["field"]
            )
            # 模型自身判断错 (a)：text 也与真值矛盾
            text_wrong = sum(
                1 for r in sub
                if r["text"] != "unclear" and (r["text"] == "success") != gt
            )
            tag_t = "✅" if t == n else "⚠️"
            print(f"[{engine}/{scenario}] N={n}  真值={gt}  {tag_t}")
            print(f"    字段 login_success: True={t}  False={fls}  None(提取失败)={none}")
            print(f"    误判(字段≠真值)={misfield}/{n}  text≠field矛盾(字段提取错嫌疑 b)={conflict}  "
                  f"text本身错(模型判断错嫌疑 a)={text_wrong}")


async def main() -> None:
    engines = sys.argv[1:] or ["glm-anthropic", "glm-openai"]
    n_per = int(os.getenv("LOGINPROBE_N", "12"))
    rows: list[dict] = []
    for engine in engines:
        load_profile(engine)
        for scenario in SCENARIOS:
            for idx in range(n_per):
                r = await run_one(engine, scenario, idx)
                rows.append(r)
                if r.get("timeout"):
                    print(f"[{engine}/{scenario} #{idx}] TIMEOUT (>150s)")
                else:
                    print(
                        f"[{engine}/{scenario} #{idx}] field={r['field']} "
                        f"text={r['text']} so_none={r['so_none']} "
                        f"({r['duration']}s){' ERR=' + r['error'] if r['error'] else ''}"
                    )
                    print(f"    …{r['snippet']}")
    summarize(rows)


if __name__ == "__main__":
    asyncio.run(main())
