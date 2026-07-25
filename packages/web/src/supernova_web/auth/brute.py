from __future__ import annotations

import time


class BruteGuard:
    """per-username 登录失败计数 + 临时锁定（进程内存）。本地单 worker 足够。"""

    def __init__(self, threshold: int = 5, lock_seconds: int = 300) -> None:
        self.threshold = threshold
        self.lock_seconds = lock_seconds
        self._fails: dict[str, int] = {}
        self._locked_until: dict[str, float] = {}

    def record_failure(self, username: str) -> None:
        self._fails[username] = self._fails.get(username, 0) + 1
        if self._fails[username] >= self.threshold:
            self._locked_until[username] = time.monotonic() + self.lock_seconds

    def is_locked(self, username: str) -> bool:
        until = self._locked_until.get(username)
        if until is None:
            return False
        if time.monotonic() >= until:
            # 窗口过，自动解锁 + 清计数
            self._locked_until.pop(username, None)
            self._fails.pop(username, None)
            return False
        return True

    def remaining(self, username: str) -> int:
        return max(0, self.threshold - self._fails.get(username, 0))

    def reset(self, username: str) -> None:
        self._fails.pop(username, None)
        self._locked_until.pop(username, None)
