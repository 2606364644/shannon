from __future__ import annotations

import hmac
import secrets


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def verify_csrf(header: str | None, cookie_value: str | None) -> bool:
    if not header or not cookie_value:
        return False
    return hmac.compare_digest(header, cookie_value)
