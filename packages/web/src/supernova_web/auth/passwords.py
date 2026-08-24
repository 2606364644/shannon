from __future__ import annotations

import bcrypt


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


# 新密码最小长度（create/reset 与 change-password 共用）。auth/routes._NEW_PASSWORD_MIN_LEN
# 同值；此处提为公开常量供 api/users 复用，避免跨模块依赖私有名。
NEW_PASSWORD_MIN_LEN = 8

# admin 建号时密码留空 -> 落此默认密码（6 位，绕过上面的长度校验，与 bootstrap
# admin 的 123456 同语义；must_change=True 兜底，登录后弹一次可稍后的改密提醒）。
DEFAULT_NEW_USER_PASSWORD = "123456"
