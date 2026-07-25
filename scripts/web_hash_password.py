#!/usr/bin/env python3
"""交互式生成 bcrypt 密码 hash，粘进 configs/users.yaml 的 password_hash 字段。
用法: uv run python scripts/web_hash_password.py"""
from __future__ import annotations

import getpass
import sys

import bcrypt


def main() -> int:
    pw = getpass.getpass("密码: ")
    if not pw:
        print("密码不能为空", file=sys.stderr)
        return 1
    print("\n把下面这行粘进 users.yaml 的 password_hash：\n")
    print(bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
