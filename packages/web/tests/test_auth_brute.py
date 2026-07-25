# packages/web/tests/test_auth_brute.py
import time
from supernova_web.auth.brute import BruteGuard


def test_allows_before_threshold():
    g = BruteGuard(threshold=3, lock_seconds=60)
    for _ in range(2):
        g.record_failure("alice")
    assert not g.is_locked("alice")
    assert g.remaining("alice") == 1


def test_locks_at_threshold():
    g = BruteGuard(threshold=3, lock_seconds=60)
    for _ in range(3):
        g.record_failure("alice")
    assert g.is_locked("alice")
    assert g.remaining("alice") == 0


def test_reset_clears():
    g = BruteGuard(threshold=2, lock_seconds=60)
    g.record_failure("alice"); g.record_failure("alice")
    assert g.is_locked("alice")
    g.reset("alice")
    assert not g.is_locked("alice")
    assert g.remaining("alice") == 2


def test_unlocks_after_window(monkeypatch):
    t = {"v": 0.0}
    monkeypatch.setattr(time, "monotonic", lambda: t["v"])
    g = BruteGuard(threshold=1, lock_seconds=60)
    g.record_failure("alice")
    assert g.is_locked("alice")
    t["v"] += 61  # 过窗口
    assert not g.is_locked("alice")


def test_independent_per_user():
    g = BruteGuard(threshold=2, lock_seconds=60)
    g.record_failure("alice"); g.record_failure("alice")
    assert g.is_locked("alice")
    assert not g.is_locked("bob")
