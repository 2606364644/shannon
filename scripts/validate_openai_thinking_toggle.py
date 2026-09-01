#!/usr/bin/env python3
"""真机探针：openai 引擎 thinking 开关接线的端到端验证。

验证链（2026-09-01 NodeGoat-20260901-060640 hotfix 后）：
  工作区/ProviderConfig.adaptive_thinking=False
    → OpenAIProvider._get_client() 包装层注入 extra_body={"thinking":{"type":"disabled"}}
    → 真实网关（llm-proxy）接受参数且 reasoning 归零、completion 显著下降。

对照两跑（同一推理型 prompt、同一模型）：
  CASE A  adaptive_thinking=False  期望 reasoning_len==0（thinking 被关）
  CASE B  adaptive_thinking=None   期望 reasoning_len>0（模型默认开 thinking）

PASS 条件：A 请求成功且 reasoning_len==0，且 B reasoning_len>0（证明开关有双向差），
外加 A.completion 显著小于 B.completion（thinking token 不再计费）。

用法（宿主源码树，无需容器）：
  python3 scripts/validate_openai_thinking_toggle.py [--ws __legacy__]

注意：api_key 经 workspaces/.master_key 内存解密，不打印、不落盘。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packages" / "core" / "src"))

_REASONING_PROMPT = """审查这条 XSS 链是否真实可达。Express 路由:
app.post('/profile', (req,res)=>{ db.profiles.update(req.session.userId, {bio: req.body.bio}); res.redirect('/profile'); });
app.get('/profile', (req,res)=>{ db.profiles.get(req.session.userId, (e,u)=>{ res.render('profile', {bio: u.bio}); }); });
模板 profile.html 用 Swig {{ bio }} 插值，autoescape 默认开启。
判断 taint 从 req.body.bio 到 {{bio}} 是否构成可利用 XSS，给出三态判定与关键证据。"""


def _load_provider_inputs(ws_name: str) -> dict:
    """从工作区 config.yaml 解 provider 配置；api_key 内存解密（不物化）。"""
    import yaml
    from cryptography.fernet import Fernet

    ws_dir = ROOT / "workspaces" / ws_name
    cfg = yaml.safe_load((ws_dir / "config.yaml").read_text())
    prov = cfg["provider"]
    fernet = Fernet((ROOT / "workspaces" / ".master_key").read_bytes())
    api_key = fernet.decrypt(prov["api_key"].encode()).decode()
    return {
        "base_url": prov["base_url"],
        "model": prov["model"],
        "api_key": api_key,
    }


async def _run_case(label: str, adaptive_thinking, inputs: dict) -> dict:
    from supernova_core.agents.providers_openai import OpenAIProvider
    from supernova_core.agents.runner import ProviderConfig

    provider = OpenAIProvider(ProviderConfig(
        type="openai_compatible",
        api_key=inputs["api_key"],
        base_url=inputs["base_url"],
        adaptive_thinking=adaptive_thinking,
    ))
    client = provider._get_client()  # 包装层在此生效（extra_body 注入点）
    t0 = time.time()
    resp = await client.chat.completions.create(
        model=inputs["model"],
        messages=[{"role": "user", "content": _REASONING_PROMPT}],
        max_tokens=3000,
    )
    dur = time.time() - t0
    usage = resp.usage
    msg = resp.choices[0].message
    out = {
        "label": label,
        "ok": True,
        "dur_s": round(dur, 1),
        "completion": getattr(usage, "completion_tokens", None),
        "prompt": getattr(usage, "prompt_tokens", None),
        "reasoning_len": len(getattr(msg, "reasoning_content", None) or ""),
        "content_len": len(msg.content or ""),
    }
    print(f"[{label}] {out['dur_s']}s completion={out['completion']} "
          f"prompt={out['prompt']} reasoning_len={out['reasoning_len']} "
          f"content_len={out['content_len']}")
    return out


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws", default="__legacy__", help="工作区名（读其 provider 配置）")
    args = ap.parse_args()

    inputs = _load_provider_inputs(args.ws)
    print(f"模型: {inputs['model']}  网关: {inputs['base_url']}  "
          f"key: <内存解密，不显示>")

    case_a = await _run_case("A adaptive_thinking=False（应关 thinking）", False, inputs)
    case_b = await _run_case("B adaptive_thinking=None（默认，应开 thinking）", None, inputs)

    # 判定
    fails = []
    if not case_a["ok"]:
        fails.append("A 请求失败（网关拒绝 thinking 参数?）")
    if case_a["reasoning_len"] != 0:
        fails.append(f"A reasoning_len={case_a['reasoning_len']} != 0（未关掉 thinking）")
    if case_b["reasoning_len"] <= 0:
        fails.append(f"B reasoning_len={case_b['reasoning_len']} <= 0（默认对照未见 thinking，"
                     f"模型/网关行为已变，探针对照失效）")
    if (case_a["completion"] or 0) >= (case_b["completion"] or 0) * 0.6:
        fails.append(f"A completion={case_a['completion']} 未显著低于 B={case_b['completion']} "
                     f"（thinking token 仍在计费?）")

    print()
    if fails:
        print("FAIL — thinking 开关未按预期生效：")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("PASS — thinking 开关双向生效：")
    print(f"  - False → 注入 extra_body thinking disabled：reasoning 归零，"
          f"completion {case_b['completion']}→{case_a['completion']}，"
          f"耗时 {case_b['dur_s']}s→{case_a['dur_s']}s")
    print(f"  - None  → 不注入（模型默认）：reasoning_len={case_b['reasoning_len']}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
