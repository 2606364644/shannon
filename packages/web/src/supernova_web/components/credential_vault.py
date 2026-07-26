"""P3c 阶段 2：凭据对称加密封装（Fernet）。

master key 优先级：SUPERNOVA_MASTER_KEY env（Fernet key 字符串）> workspaces/.master_key 文件。
首启（env 未设 + 文件不存在）生成 key 落盘 0600。生产建议经 env/secret 注入。
凭据字段白名单 CREDENTIAL_FIELDS：WsConfigStore 据此决定哪些字段加密。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

_log = logging.getLogger(__name__)


class CredentialVault:
    CREDENTIAL_FIELDS = frozenset({"api_key", "auth_token", "gitlab_token"})

    def __init__(self, master_key_file: Path):
        self._master_key_file = Path(master_key_file)
        self._fernet = Fernet(self._load_or_create_key())

    def _load_or_create_key(self) -> bytes:
        env_key = os.environ.get("SUPERNOVA_MASTER_KEY")
        if env_key:
            return env_key.encode()
        if self._master_key_file.exists():
            return self._master_key_file.read_bytes().strip()
        # 首启生成
        key = Fernet.generate_key()
        self._master_key_file.parent.mkdir(parents=True, exist_ok=True)
        self._master_key_file.write_bytes(key)
        try:
            os.chmod(self._master_key_file, 0o600)
        except OSError:
            pass  # 非 POSIX 容忍
        _log.info("首启生成 master key: %s", self._master_key_file)
        return key

    def encrypt(self, plaintext: str | None) -> str | None:
        if plaintext is None:
            return None
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str | None) -> str | None:
        if token is None:
            return None
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError):
            _log.warning("凭据解密失败（master key 不匹配或密文损坏），降级 None")
            return None
