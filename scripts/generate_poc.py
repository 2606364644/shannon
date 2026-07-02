#!/usr/bin/env python3
"""对历史 session 重跑 PoC 生成（报告后处理，独立于 workflow）。

用法:
  python scripts/generate_poc.py --session workspaces/<sess> --track blackbox --target https://t.example.com
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# 让 src/ 可 import（对齐 scripts/validate_*.py 的 sys.path 处理）
_REPO_ROOT = Path(__file__).resolve().parent.parent
for pkg in (_REPO_ROOT / "packages" / "core" / "src",):
    sys.path.insert(0, str(pkg))

from shannon_core.services.poc_generator import PoCGenerator  # noqa: E402
from shannon_core.models.config import ALL_VULN_CLASSES  # noqa: E402


def _track_dir(session_dir: Path, track: str) -> Path:
    """定位 track deliverables 目录：优先 session/deliverables/{track}，回退 session/deliverables（老平铺）。"""
    deliverables = session_dir / "deliverables"
    sub = deliverables / track
    if sub.exists():
        return sub
    if deliverables.exists():
        return deliverables  # 老平铺 session
    raise SystemExit(f"deliverables not found under {session_dir}")


async def main() -> int:
    ap = argparse.ArgumentParser(description="重跑 PoC 生成（curl/Burp md）")
    ap.add_argument("--session", required=True, type=Path, help="session 目录 (workspaces/<sess>)")
    ap.add_argument("--track", required=True, choices=["whitebox", "blackbox"])
    ap.add_argument("--target", default=None, help="目标 host URL（覆盖 web_url，省略则用占位符）")
    ap.add_argument("--vuln-classes", nargs="*", default=list(ALL_VULN_CLASSES))
    args = ap.parse_args()

    if not args.session.exists():
        raise SystemExit(f"session not found: {args.session}")

    track_dir = _track_dir(args.session, args.track)
    out = await PoCGenerator.generate(
        deliverables_dir=track_dir,
        vuln_classes=args.vuln_classes,
        target_url=args.target,
        track=args.track,
    )
    print(f"PoC md written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
