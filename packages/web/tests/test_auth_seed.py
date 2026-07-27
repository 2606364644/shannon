# packages/web/tests/test_auth_seed.py
from supernova_web.auth.store import AuthStore
from supernova_web.auth.seed import seed_users


def _yaml(tmp_path, body: str) -> str:
    p = tmp_path / "users.yaml"
    p.write_text(body, encoding="utf-8")
    return str(p)


def test_seed_creates_missing_users(tmp_path):
    s = AuthStore(str(tmp_path / "auth.db")); s.init_schema()
    path = _yaml(tmp_path, """
users:
  - username: admin
    password_hash: "$2b$12$aaa"
    role: admin
  - username: alice
    password_hash: "$2b$12$bbb"
""")
    n = seed_users(s, path)
    assert n == 2
    assert s.get_user_by_username("admin").role == "admin"
    assert s.get_user_by_username("alice").role == "user"  # 默认 user


def test_seed_reads_must_change_flag(tmp_path):
    """seed 读 yaml 的 must_change_password 字段写入用户记录。"""
    s = AuthStore(str(tmp_path / "auth.db")); s.init_schema()
    path = _yaml(tmp_path, """
users:
  - username: admin
    password_hash: "$2b$12$aaa"
    role: admin
    must_change_password: true
  - username: alice
    password_hash: "$2b$12$bbb"
""")
    n = seed_users(s, path)
    assert n == 2
    assert s.get_user_by_username("admin").must_change_password is True
    assert s.get_user_by_username("alice").must_change_password is False  # 缺省 False


def test_seed_does_not_overwrite_existing_must_change(tmp_path):
    """已存在用户不被 seed 覆盖：改密后 must_change=0，重启 seed 不会重新置 true。"""
    s = AuthStore(str(tmp_path / "auth.db")); s.init_schema()
    s.create_user("admin", "$2b$12$old", role="admin", must_change=True)
    s.update_password(s.get_user_by_username("admin").id, "$2b$12$changed")  # 清 must_change
    path = _yaml(tmp_path, """
users:
  - username: admin
    password_hash: "$2b$12$aaa"
    must_change_password: true
""")
    n = seed_users(s, path)
    assert n == 0  # 已存在，不覆盖
    assert s.get_user_by_username("admin").must_change_password is False  # 仍为改密后的 False


def test_seed_does_not_overwrite_existing(tmp_path):
    s = AuthStore(str(tmp_path / "auth.db")); s.init_schema()
    s.create_user("admin", "$2b$12$OLD")  # 已存在，密码 OLD
    path = _yaml(tmp_path, """
users:
  - username: admin
    password_hash: "$2b$12$NEW"
""")
    n = seed_users(s, path)
    assert n == 0  # 没新建
    # 已存在用户不被改：重新读 password_hash 确认未被覆盖（store 不暴露 hash，用 get_user_by_username 仅确认仍在）
    assert s.get_user_by_username("admin") is not None


def test_seed_missing_file_is_noop(tmp_path):
    s = AuthStore(str(tmp_path / "auth.db")); s.init_schema()
    assert seed_users(s, str(tmp_path / "nope.yaml")) == 0


def test_seed_empty_users_key_is_noop(tmp_path):
    s = AuthStore(str(tmp_path / "auth.db")); s.init_schema()
    path = _yaml(tmp_path, "users: []\n")
    assert seed_users(s, path) == 0
