#!/usr/bin/env python3
"""Replay recon-context digest generation against a real recon_deliverable.md.

spec 2026-09-01 §7.3 真实规模验收：不经 Temporal，直接调
``summarize_recon_context`` + 解析落盘逻辑（与 activities.run_recon_context_digest
的 llm-summary 路径同构），人工核对六节内容——每行可在原文找到出处（硬约束
抽检）、endpoints 节 ≥ 31 行、ssrf 节含 /research。

用法：
    .venv/bin/python scripts/replay_recon_context_digest.py \
        [--profile glm-anthropic] [--out /tmp/digest.json] [recon_md_path]

默认 md = NodeGoat 20260901 真实交付物。真实 LLM 调用（medium 档，~¥0.05）。
"""
import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "packages" / "core" / "src"))
sys.path.insert(0, str(_REPO_ROOT / "packages" / "whitebox" / "src"))

_DEFAULT_MD = (
    _REPO_ROOT / "workspaces" / "__legacy__" / "scans" / "NodeGoat-20260901-015018"
    / "deliverables" / "whitebox" / "recon_deliverable.md"
)
_DEFAULT_REPO = _REPO_ROOT / "workspaces" / "__legacy__" / "repos" / "NodeGoat"


def load_profile(name: str) -> None:
    profile = _REPO_ROOT / ".env.profiles" / f"{name}.env"
    if not profile.exists():
        sys.exit(f"profile not found: {profile}")
    for line in profile.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("recon_md", nargs="?", default=str(_DEFAULT_MD))
    parser.add_argument("--profile", default="glm-anthropic")
    parser.add_argument("--out", default="/tmp/recon_context_digest_replay.json")
    args = parser.parse_args()

    load_profile(args.profile)

    from supernova_core.agents.recon_context_summarizer import (
        DIGEST_SECTION_ORDER,
        RECON_CONTEXT_SUMMARIZER_PROMPT_VERSION,
        build_summarizer_input,
        parse_sections,
        summarize_recon_context,
    )
    from supernova_core.agents.runner import run_claude_prompt

    md_path = Path(args.recon_md)
    recon_md = md_path.read_text(encoding="utf-8")
    print(f"[replay] md={md_path} ({len(recon_md)} chars)")

    input_text, input_meta = build_summarizer_input(recon_md)
    print(f"[replay] six-section input: {len(input_text)} chars, meta={input_meta}")

    async def llm_client(prompt: str) -> str:
        # 取法镜像 AccountedLlmClient.__call__（activities 侧包装）；失败 raise
        # 以便本脚本直接暴露错误（活动里是静默 None → 判空走降级链）。
        result = await run_claude_prompt(
            prompt=prompt, repo_path=str(_DEFAULT_REPO), model_tier="medium")
        if result is None or getattr(result, "success", True) is False:
            raise RuntimeError(f"run_claude_prompt failed: {result!r}")
        so = getattr(result, "structured_output", None)
        if so is not None:
            return json.dumps(so, ensure_ascii=False)
        return getattr(result, "text", "") or ""

    t0 = time.monotonic()
    raw = await summarize_recon_context(recon_md, llm_client, fallback_on_error=False)
    elapsed = time.monotonic() - t0
    print(f"[replay] LLM summary: {len(raw)} chars in {elapsed:.1f}s")

    sections = parse_sections(raw)
    degraded = not sections
    degraded_reason = "unsectioned" if degraded else None
    digest_rows = sum(
        1 for ln in sections.get("endpoints", "").splitlines() if ln.strip())
    ratio = digest_rows / max(input_meta["source_endpoint_rows"], 1)
    if not degraded and ratio < 0.8:
        degraded, degraded_reason = True, "coverage_low"

    digest = {
        "schema_version": 2,
        "source": "llm-summary",
        "degraded": degraded,
        "degraded_reason": degraded_reason,
        "source_hash": f"replay:{md_path.name}",
        "summarizer_prompt_version": RECON_CONTEXT_SUMMARIZER_PROMPT_VERSION,
        "language": "en",
        "input_meta": input_meta,
        "coverage": {"digest_endpoint_rows": digest_rows, "coverage_ratio": ratio},
        "missing_sections": [n for n in DIGEST_SECTION_ORDER if n not in sections],
        "text": raw,
        "sections": sections,
    }
    out = Path(args.out)
    out.write_text(json.dumps(digest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[replay] coverage={digest['coverage']} degraded={degraded}({degraded_reason})")
    print(f"[replay] missing_sections={digest['missing_sections']}")
    print(f"[replay] artifact → {out}")
    print("\n=== 人工核对要点（spec §7.3）===")
    for name in DIGEST_SECTION_ORDER:
        body = sections.get(name)
        if body is None:
            print(f"## {name}: (MISSING)")
        else:
            preview = body if len(body) <= 400 else body[:400] + " …"
            print(f"## {name} ({len(body.splitlines())} lines):\n{preview}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
