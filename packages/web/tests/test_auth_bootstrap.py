"""默认 admin bootstrap 单元测试（纯 store，不走 app lifespan）。

驱动 seed.bootstrap_default_admin + store.count_admins：全新库（无任何 admin）
启动时自动建一个 admin/123456（must_change=True）；已有 admin 则 no-op。
"""
from supernova_web.auth.passwords import verify_password
from supernova_web.auth.seed import bootstrap_default_admin
from supernova_web.auth.store import AuthStore


def _store(tmp_path):
    s = AuthStore(str(tmp_path / "auth.db"))
    s.init_schema()
    return s


# ---------- store.count_admins ----------

def test_count_admins_empty_is_zero(tmp_path):
    s = _store(tmp_path)
    assert s.count_admins() == 0


def test_count_admins_counts_only_admin_role(tmp_path):
    s = _store(tmp_path)
    s.create_user("alice", "h", role="user")
    s.create_user("bob", "h", role="user")
    assert s.count_admins() == 0
    s.create_user("root", "h", role="admin")
    assert s.count_admins() == 1


# ---------- bootstrap_default_admin ----------

def test_bootstrap_creates_default_admin_when_no_admin(tmp_path):
    """空库 → 建 admin/123456，role=admin，must_change=True，密码可验。"""
    s = _store(tmp_path)
    n = bootstrap_default_admin(s, username="admin", password="123456")
    assert n == 1
    admin = s.get_user_by_username("admin")
    assert admin is not None
    assert admin.role == "admin"
    assert admin.must_change_password is True
    assert verify_password("123456", s.get_password_hash("admin"))


def test_bootstrap_noop_when_admin_already_exists(tmp_path):
    """已有 admin → no-op，不新建、不覆盖。"""
    s = _store(tmp_path)
    s.create_user("existing", "h", role="admin")
    n = bootstrap_default_admin(s, username="admin", password="123456")
    assert n == 0
    assert s.get_user_by_username("admin") is None  # 没新建
    assert s.count_admins() == 1  # 仍是原来那个


def test_bootstrap_disabled_returns_zero(tmp_path):
    """enabled=False → 不建。"""
    s = _store(tmp_path)
    n = bootstrap_default_admin(s, username="admin", password="123456", enabled=False)
    assert n == 0
    assert s.get_user_by_username("admin") is None


def test_bootstrap_uses_custom_username_and_password(tmp_path):
    """自定义账密：建 root/hunter2，可验。"""
    s = _store(tmp_path)
    n = bootstrap_default_admin(s, username="root", password="hunter2")
    assert n == 1
    u = s.get_user_by_username("root")
    assert u.role == "admin"
    assert verify_password("hunter2", s.get_password_hash("root"))


def test_bootstrap_must_change_can_be_false(tmp_path):
    """must_change=False → 建出来的账号不强制改密。"""
    s = _store(tmp_path)
    bootstrap_default_admin(s, username="admin", password="123456", must_change=False)
    assert s.get_user_by_username("admin").must_change_password is False
