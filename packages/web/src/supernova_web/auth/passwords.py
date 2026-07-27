from __future__ import annotations

import bcrypt


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


# 新密码最小长度（create/reset 与 change-password 共用）。auth/routes._NEW_PASSWORD_MIN_LEN
# 同值；此处提为公开常量供 api/users 复用，避免跨模块依赖私有名。
NEW_PASSWORD_MIN_LEN = 8
